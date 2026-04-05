import socket
import threading
from cryptography.fernet import Fernet

from env_loader import get_fernet_key_bytes

cipher = Fernet(get_fernet_key_bytes())

MAX_FILE_SIZE = 5 * 1024 * 1024 #5 MB

HOST = "0.0.0.0"
PORT = 6767

#define server
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

#attach socket to an adress
server.bind((HOST, PORT))

#listen for connections
server.listen()

#Epstein's Client List
clients = []

usernames = []

print("Server started...")

def encrypt(data: bytes) -> bytes:
    return cipher.encrypt(data)

def decrypt(data: bytes) -> bytes:
    return cipher.decrypt(data)

#brodcast messages across all users 
def brodcast(message, sender):
    for client in clients[:]:
        if client != sender:
            try:
                client.send(message)
            except:
                remove_client(client)

def remove_client(client):
    if client in clients:
        try:
            index = clients.index(client)
            username = usernames[index]

            clients.remove(client)
            usernames.pop(index)

            #leave message
            brodcast(f"{username} left the chat\n".encode(), client)
            print(f"{username} left the chat".encode(), client)

        except ValueError:
            # This can happen if client was already removed by another thread
            print("Value Error")
            pass

        try:
            client.close()
        except: 
            pass

def handle_client(client):
    while True:
        try: 
            message = receive_line(client)
            if not message:                     # <- client closed connection
                remove_client(client)
                break

            try:
                message_decoded = message.decode('utf-8').strip()
            except UnicodeDecodeError:
                client.send("Invalid encoding\n".encode())
                continue

            index = clients.index(client)
            username = usernames[index]

            if message_decoded.startswith("MSG|"):
                parts = message_decoded.split("|", 1)
                encrypted_part = parts[1]
                decrypted_message = decrypt(encrypted_part.encode()).decode()

                # handle private messaging (/msg <username> <message>)
                if decrypted_message.startswith("/msg"):
                    parts = decrypted_message.split(" ", 2)

                    if len(parts) >= 3:
                        target_user = parts[1]
                        private_msg = parts[2]
                        private_encrypted = encrypt(private_msg.encode())

                        # Encode so the UI can both:
                        # - group by the conversation partner (the user you selected in the sidebar)
                        # - display the original sender correctly on both sides
                        #
                        # Format: (PRIVATE) <bucketUser>|<senderUser>: <message>
                        #
                        # Receiver side: bucketUser == sender, senderUser == sender
                        formatted_text = f"(PRIVATE) {username}|{username}: {private_msg}"
                        encrypted_msg = encrypt(formatted_text.encode())
                        send_to_user(target_user, b"MSG|" + encrypted_msg + b"\n")

                        # Sender side echo: bucketUser == receiver, senderUser == sender
                        echo_text = f"(PRIVATE) {target_user}|{username}: {private_msg}"
                        echo_encrypted = encrypt(echo_text.encode())
                        send_to_user(username, b"MSG|" + echo_encrypted + b"\n")
                    else:
                        client.send("Invalid command format! Usage: /msg <username> <message>\n".encode())

                    continue

                full_text = f"{username}: {decrypted_message}"
                encrypted_msg = encrypt(full_text.encode())

                brodcast(b"MSG|" + encrypted_msg + b"\n", client)

            # handles private files (FILEMSG|target_username|filename|size)
            elif message_decoded.startswith("FILEMSG|"):
                parts = message_decoded.split("|")
                if len(parts) != 4:
                    client.send(b"Bad file header\n")
                    continue

                target = parts[1]
                filename = parts[2]
                size_str = parts[3]

                try:
                    index = usernames.index(target)
                    target_client = clients[index]
                except ValueError:
                    client.send(f"Could not find user: {target}\n".encode())
                    continue

                try:
                    size = int(size_str)
                except ValueError:
                    client.send(b"Invalid size\n")

                if size > MAX_FILE_SIZE:
                    client.send("File too large!\n".encode())
                    continue

                print(f"Receiving file: {filename} ({size} bytes) from {username} privately to {target_client}")

                # Forward the encrypted bytes exactly as received.
                # Fernet ciphertext length changes on re-encrypt, so do NOT re-encrypt here.
                encrypted_data = receive_exact(client, size)
                if len(encrypted_data) != size:
                    print(f"Incomplete file from {username}")
                    client.send(b"Incomplete file transfer\n")
                    continue

                target_client.send(f"FILEFROM|{username}|{filename}|{size}\n".encode())
                target_client.send(encrypted_data)


            #handle files (FILE|filename|size)
            elif message_decoded.startswith("FILE|"):
                parts = message_decoded.split("|")
                if len(parts) != 3:
                    client.send(b"Bad file header\n")
                    continue

                filename = parts[1]
                size_str = parts[2]

                try:
                    size = int(size_str)
                except ValueError:
                    client.send(b"Invalid size\n")

                if size > MAX_FILE_SIZE:
                    client.send("File too large!\n".encode())
                    continue
                
                print(f"Receiving file: {filename} ({size} bytes) from {username}")

                encrypted_data = receive_exact(client, size)
                if len(encrypted_data) != size:
                    print(f"Incomplete file from {username}")
                    client.send(b"Incomplete file transfer\n")
                    continue

                brodcast(f"FILE|{filename}|{size}\n".encode(), client)

                for c in clients:
                    if c is not client:
                        try:
                            c.send(encrypted_data)
                        except:
                            remove_client(c)
                client.send(b"File sent succesfuly!\n") #feedback to sender
            
            else:
                full_message = f"{username}: {message_decoded}\n"
                print(full_message)
                brodcast(full_message.encode(), client)

        except:
            remove_client(client)
            break

def send_to_user(target_username, message):
    if target_username in usernames:
        index = usernames.index(target_username)
        target_client = clients[index]
        target_client.send(message)

def receive_line(client):
    data = b""
    while not data.endswith(b"\n"):
        chunk = client.recv(1)
        if not chunk:
            break
        data += chunk
    return data

def receive_exact(client, size):
    data = b""
    while len(data) < size:
        packet = client.recv(1024)
        if not packet:
            break
        data += packet
    return data

while True:
    #wait until connection
    client_socket, address = server.accept()

    client_socket.send(b"USERNAME\n")
    username = receive_line(client_socket).decode().strip()

    # check if only normal characters were used
    if not username.isalnum():
        error_msg = encrypt(b"Connection refused: Username must contain only letters and numbers.")
        client_socket.send(b"SERVER:" + error_msg + b"\n")
        client_socket.close()
        continue

    # Same username logging in again = take over (kick old TCP session).
    if username in usernames:
        idx = usernames.index(username)
        old_sock = clients[idx]
        clients[idx] = client_socket
        try:
            old_sock.shutdown(socket.SHUT_RDWR)
        except Exception:
            pass
        try:
            old_sock.close()
        except Exception:
            pass
        print(f"{username} reconnected (session takeover) from {address}")
    else:
        usernames.append(username)
        clients.append(client_socket)
        print(f"{username} connected from {address}")
        brodcast(f"{username} joined the chat.\n".encode(), client_socket)

    thread = threading.Thread(target=handle_client, args=(client_socket,))
    thread.start()