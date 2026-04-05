from flask import Flask, render_template, request, redirect, session, jsonify
import socket
import threading
import time
import base64
from cryptography.fernet import Fernet
from urllib.parse import quote

from env_loader import get_flask_secret, get_fernet_key_bytes

cipher = Fernet(get_fernet_key_bytes())


def decrypt(data: bytes) -> bytes:
    return cipher.decrypt(data)


app = Flask(__name__)
app.secret_key = get_flask_secret()

HOST = "127.0.0.1"
PORT = 6767
MAX_FILE_SIZE = 5 * 1024 * 1024
MAX_RETURN_MESSAGES = 100

# List of preset users always reachable
PRESET_USERS = ["Test1", "Test2"]

clients = {}  # username -> socket
inboxes = {}  # username -> list[message]
typing_state = {}  # target_username -> set[sender_username]
next_message_id = 0
last_activity = {}  # username -> monotonic time of last HTTP activity
IDLE_DISCONNECT_SECONDS = 300  # 5 minutes without requests → disconnect socket

API_PATHS = (
    "/messages",
    "/users",
    "/send",
    "/send_file",
    "/typing",
    "/typing_status",
    "/disconnect",
)


@app.before_request
def touch_activity_and_stale_session():
    if request.endpoint == "static" or request.endpoint == "login":
        return
    u = session.get("username")
    if not u:
        return
    if u not in clients:
        session.pop("username", None)
        if any(request.path.startswith(p) for p in API_PATHS):
            return jsonify({"error": "disconnected"}), 401
        if request.path == "/chat":
            return redirect("/?error=" + quote("Session ended (disconnect or inactivity)."))
        return
    last_activity[u] = time.monotonic()


def receive_line(sock):
    data = b""
    while not data.endswith(b"\n"):
        chunk = sock.recv(1)
        if not chunk:
            break
        data += chunk
    return data

def receive_exact(sock, size):
    data = b""
    while len(data) < size:
        packet = sock.recv(min(4096, size - len(data)))
        if not packet:
            break
        data += packet
    return data

def ensure_inbox(username):
    if username not in inboxes:
        inboxes[username] = []

def push_to_user(username, payload):
    global next_message_id
    ensure_inbox(username)
    entry = {"id": next_message_id}
    entry.update(payload)
    inboxes[username].append(entry)
    next_message_id += 1

def disconnect_username(username):
    last_activity.pop(username, None)
    sock = clients.pop(username, None)
    if sock is not None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            sock.close()
        except Exception:
            pass

    for target in list(typing_state.keys()):
        typers = typing_state[target]
        typers.discard(username)
        if not typers:
            typing_state.pop(target, None)


def _idle_watcher():
    while True:
        time.sleep(30)
        now = time.monotonic()
        for username in list(clients.keys()):
            la = last_activity.get(username)
            if la is None:
                continue
            if now - la > IDLE_DISCONNECT_SECONDS:
                disconnect_username(username)


threading.Thread(target=_idle_watcher, daemon=True).start()


def connect_user(username):
    # Always drop any stale Flask-side socket before a new login (disconnect or crashed tab).
    if username in clients:
        disconnect_username(username)

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.connect((HOST, PORT))

    s.recv(1024)  # USERNAME prompt
    s.send((username + "\n").encode())

    s.settimeout(1.0)
    first = b""
    try:
        first = receive_line(s)
    except socket.timeout:
        first = b""
    finally:
        s.settimeout(None)

    if first:
        raw = first.strip()
        if raw.startswith(b"SERVER:"):
            try:
                _, token = raw.split(b":", 1)
                error_text = decrypt(token).decode(errors="ignore")
            except Exception:
                error_text = raw.decode(errors="ignore")
            try:
                s.close()
            except Exception:
                pass
            raise ValueError(error_text)

    clients[username] = s
    ensure_inbox(username)
    last_activity[username] = time.monotonic()

    def listen():
        while True:
            try:
                line = receive_line(s)
                if not line:
                    break

                msg = line.strip()
                if not msg:
                    continue

                # Consume private file payloads to keep the socket stream healthy.
                # We store files from /send_file endpoint, so we don't duplicate here.
                if msg.startswith(b"FILEFROM|"):
                    parts = msg.decode(errors="ignore").split("|")
                    if len(parts) == 4:
                        try:
                            size = int(parts[3])
                            _ = receive_exact(s, size)
                        except Exception:
                            pass
                    continue

            except Exception:
                break

        if clients.get(username) is s:
            disconnect_username(username)

    threading.Thread(target=listen, daemon=True).start()

@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form["username"].strip()
        if not username:
            return redirect("/?error=" + quote("Username cannot be empty."))

        try:
            connect_user(username)
        except ValueError as e:
            return redirect("/?error=" + quote(str(e)))

        session["username"] = username
        return redirect("/chat")

    return render_template("login.html", error=request.args.get("error"))

@app.route("/chat")
def chat():
    if "username" not in session:
        return redirect("/")
    return render_template("chat.html", username=session["username"])

@app.route("/send", methods=["POST"])
def send():
    if "username" not in session:
        return "Unauthorized", 401

    username = session["username"]
    msg = request.form["message"].strip()
    if not msg:
        return "Empty message", 400

    sock = clients.get(username)
    if sock is None:
        return "Not connected", 400

    if msg.startswith("/msg "):
        parts = msg.split(" ", 2)
        if len(parts) < 3:
            return "Invalid private command", 400

        target = parts[1].strip()
        private_text = parts[2]
        both_preset = username in PRESET_USERS and target in PRESET_USERS

        if not both_preset and target not in clients:
            return "Target user is offline", 400

        encrypted = cipher.encrypt(msg.encode()).decode("utf-8")
        sock.send((f"MSG|{encrypted}\n").encode())

        # Conversation bucket must differ per side so each user sees the thread under the "other person".
        sender_payload = {
            "type": "private",
            "conversation": target,
            "text": f"{username}: {private_text}",
        }
        receiver_payload = {
            "type": "private",
            "conversation": username,
            "text": f"{username}: {private_text}",
        }
        push_to_user(username, sender_payload)
        push_to_user(target, receiver_payload)
        return "OK"

    encrypted = cipher.encrypt(msg.encode()).decode("utf-8")
    sock.send((f"MSG|{encrypted}\n").encode())

    for user in list(clients.keys()):
        push_to_user(user, {
            "type": "public",
            "conversation": "Public Chat",
            "text": f"{username}: {msg}",
        })

    return "OK"

@app.route("/send_file", methods=["POST"])
def send_file():
    if "username" not in session:
        return "Unauthorized", 401

    username = session["username"]
    target = request.form.get("target", "").strip()
    f = request.files.get("file")

    if not target or f is None:
        return "Missing file or target", 400
    if target not in clients:
        return "Target user is offline", 400

    raw = f.read()
    if len(raw) > MAX_FILE_SIZE:
        return "File too large (max 5MB)", 400

    sock = clients.get(username)
    if sock is None:
        return "Not connected", 400

    encrypted_bytes = cipher.encrypt(raw)
    header = f"FILEMSG|{target}|{f.filename}|{len(encrypted_bytes)}\n"
    sock.send(header.encode())
    sock.send(encrypted_bytes)

    # Bucket under the "other person" on each side.
    sender_file_payload = {
        "type": "file",
        "conversation": target,
        "file_name": f.filename,
        "file_data": base64.b64encode(raw).decode("ascii"),
        "text": f"{username} sent file: {f.filename}",
    }
    receiver_file_payload = {
        "type": "file",
        "conversation": username,
        "file_name": f.filename,
        "file_data": base64.b64encode(raw).decode("ascii"),
        "text": f"{username} sent file: {f.filename}",
    }
    push_to_user(username, sender_file_payload)
    push_to_user(target, receiver_file_payload)
    return "OK"

@app.route("/typing", methods=["POST"])
def typing():
    if "username" not in session:
        return "Unauthorized", 401

    sender = session["username"]
    target = request.form.get("target", "").strip()
    is_typing = request.form.get("typing", "0") == "1"

    if not target:
        return "Missing target", 400

    typing_state.setdefault(target, set())
    if is_typing:
        typing_state[target].add(sender)
    else:
        typing_state[target].discard(sender)
        if not typing_state[target]:
            typing_state.pop(target, None)

    return "OK"

@app.route("/typing_status")
def typing_status():
    if "username" not in session:
        return jsonify([]), 401

    username = session["username"]
    target = request.args.get("target", "").strip()
    if not target:
        return jsonify({"typing": False})

    is_typing = target in typing_state.get(username, set())
    return jsonify({"typing": is_typing})

@app.route("/messages")
def get_messages():
    if "username" not in session:
        return jsonify([]), 401

    username = session["username"]
    since_raw = request.args.get("since", "-1")
    try:
        since = int(since_raw)
    except ValueError:
        since = -1

    ensure_inbox(username)
    new_messages = [m for m in inboxes[username] if m["id"] > since]
    return jsonify(new_messages[-MAX_RETURN_MESSAGES:])

@app.route("/users")
def users():
    if "username" not in session:
        return jsonify([]), 401

    current = session.get("username")
    all_usernames = set(PRESET_USERS) | set(clients.keys())
    if current in all_usernames:
        all_usernames.remove(current)

    result = []
    for name in sorted(all_usernames):
        result.append({
            "username": name,
            "online": name in clients,
            "preset": name in PRESET_USERS,
        })
    return jsonify(result)

@app.route("/disconnect", methods=["POST"])
def disconnect():
    if "username" not in session:
        return "Unauthorized", 401

    username = session.get("username")
    disconnect_username(username)
    session.pop("username", None)
    return "OK"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
