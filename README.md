# Super Crazy Secure Messenger

A browser-based chat client for a small encrypted TCP messaging server. Built for private, low-traffic use with public chat, private messages, file sharing, typing indicators, and optional preset users with persistent inboxes.

## Architecture

| Component | Role | Default |
|-----------|------|---------|
| `server.py` | TCP chat server (encryption, routing, files) | Listens on `0.0.0.0:6767` |
| `webapp.py` | Flask web UI and bridge to the TCP server | Connects to `127.0.0.1:6767` |
| Browser | HTTP(S) only — talks to Flask, not TCP directly | — |

In production, expose only **HTTP (ports 80/443)** via nginx. The TCP server should stay on **localhost** (or be firewalled). Browsers never connect to port 6767.

```
Browser ──HTTP──► nginx ──► gunicorn (webapp) ──TCP──► server.py
```

## Features

- **Public chat** — messages broadcast to all connected users
- **Private messaging** — select a user in the sidebar; messages use `/msg` on the wire
- **Preset users** — always listed in the sidebar; private chats between preset users are stored in memory while the web app runs
- **Ad-hoc usernames** — any alphanumeric name; online only while connected
- **Session takeover** — logging in with an existing username replaces the old connection (no “username taken” errors)
- **File sharing** — private chats only, max 5 MB; recipient must be online
- **Typing indicator** — shown in private conversations
- **Online / offline status** — green/gray dot next to each user
- **Disconnect** — closes the TCP session and logs you out
- **Idle timeout** — disconnects after 5 minutes without HTTP activity (configurable)
- **Secrets via `.env`** — Fernet key and Flask secret are not hardcoded

## Requirements

- Python 3.10+
- Linux recommended for production (Ubuntu 22.04 / 24.04 tested)

## Project structure

```
.
├── server.py          # TCP chat server
├── webapp.py          # Flask application
├── env_loader.py      # Loads secrets from .env
├── requirements.txt
├── .env.example       # Template for secrets (copy to .env)
├── templates/
│   ├── login.html
│   └── chat.html
└── static/
    ├── script.js
    └── style.css
```

## Quick start (local)

### 1. Clone the repository

```bash
git clone https://github.com/ThePeze/SuperCrazySecureMessenger.git
cd SuperCrazySecureMessenger
```

If the app lives in a subfolder, `cd` into that directory before the steps below.

### 2. Create a virtual environment

```bash
python3 -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure environment variables

```bash
cp .env.example .env
```

Edit `.env` and set:

```env
FERNET_KEY=your_fernet_key_here
FLASK_SECRET_KEY=your_long_random_secret_here
```

Generate a Fernet key:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

**Important:** `FERNET_KEY` must be **identical** for both `server.py` and `webapp.py` (they read the same `.env` file).

### 4. Configure preset users (optional)

In `webapp.py`, edit the list:

```python
PRESET_USERS = ["Test1", "Test2"]
```

Preset users always appear in the sidebar. Private messages between two preset users are kept in the server’s in-memory inbox even when offline. Other usernames only work while connected.

### 5. Run both processes

**Terminal 1 — TCP server:**

```bash
source venv/bin/activate
python server.py
```

**Terminal 2 — web application:**

```bash
source venv/bin/activate
python webapp.py
```

Open **http://127.0.0.1:5000** in your browser, enter a username, and start chatting.

## Production deployment (VPS)

### Recommended stack

- **systemd** — keeps `server.py` and gunicorn running
- **gunicorn** — serves `webapp.py` on `127.0.0.1:8000`
- **nginx** — reverse proxy on port 80/443
- **Let’s Encrypt** — HTTPS (recommended for phones and browsers)

### Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

Do **not** expose port `6767` to the public internet unless you have a specific reason.

### Install on the server

```bash
sudo adduser chat
sudo mkdir -p /opt/chat
sudo chown chat:chat /opt/chat

sudo -u chat -i
cd /opt/chat
git clone -b web-app https://github.com/ThePeze/SuperCrazySecureMessenger.git .
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install gunicorn
cp .env.example .env
nano .env    # set FERNET_KEY and FLASK_SECRET_KEY
```

Adjust paths if your clone layout differs.

### systemd — TCP server

Create `/etc/systemd/system/chat-server.service`:

```ini
[Unit]
Description=Chat TCP server
After=network.target

[Service]
Type=simple
User=chat
WorkingDirectory=/opt/chat
EnvironmentFile=/opt/chat/.env
ExecStart=/opt/chat/venv/bin/python /opt/chat/server.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

### systemd — web application (gunicorn)

Create `/etc/systemd/system/chat-web.service`:

```ini
[Unit]
Description=Chat Flask web app
After=network.target chat-server.service

[Service]
Type=simple
User=chat
WorkingDirectory=/opt/chat
EnvironmentFile=/opt/chat/.env
ExecStart=/opt/chat/venv/bin/gunicorn -w 1 -b 127.0.0.1:8000 --timeout 120 webapp:app
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now chat-server chat-web
sudo systemctl status chat-server chat-web
```

View logs:

```bash
journalctl -u chat-server -f
journalctl -u chat-web -f
```

> **Note:** Gunicorn binds to `127.0.0.1:8000` by design. You cannot reach the app from the internet on port 8000 unless you change the bind address and open the firewall. Use nginx on port 80 instead.

### nginx reverse proxy

Create `/etc/nginx/sites-available/chat`:

```nginx
server {
    listen 80;
    listen [::]:80;
    server_name your.domain.example;

    client_max_body_size 6m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 300s;
        proxy_send_timeout 300s;
        proxy_read_timeout 300s;
    }
}
```

Enable the site:

```bash
sudo ln -sf /etc/nginx/sites-available/chat /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default   # optional
sudo nginx -t
sudo systemctl reload nginx
```

### HTTPS with Let’s Encrypt

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d your.domain.example
```

## Updating the deployment

```bash
cd /opt/chat
git pull
source venv/bin/activate
pip install -r requirements.txt
sudo systemctl restart chat-server chat-web
```

## Configuration reference

| Setting | Location | Description |
|---------|----------|-------------|
| `FERNET_KEY` | `.env` | Symmetric encryption for messages and files (must match in both apps) |
| `FLASK_SECRET_KEY` | `.env` | Flask session cookie signing |
| `PRESET_USERS` | `webapp.py` | Usernames always shown in the sidebar with persistent private inboxes |
| `IDLE_DISCONNECT_SECONDS` | `webapp.py` | Auto-disconnect after inactivity (default: `300` seconds) |
| `HOST` / `PORT` in `webapp.py` | `webapp.py` | Where the web app connects to the TCP server (`127.0.0.1:6767`) |
| `HOST` / `PORT` in `server.py` | `server.py` | TCP listen address (`0.0.0.0:6767`) |
| `MAX_FILE_SIZE` | both | 5 MB upload limit |

## Usage

1. Open the site and enter a username (letters and numbers only on the TCP server).
2. **Public Chat** — default view; messages go to everyone online.
3. Click a user in the sidebar for a **private conversation**.
4. Use the **file picker** (left of the message input) in a private chat to send a file (max 5 MB; recipient must be online).
5. Click **Disconnect** when finished, or wait for the idle timeout.

## Troubleshooting

| Problem | What to check |
|---------|----------------|
| “Username taken” or user stuck online | Restart `chat-server`; ensure latest code (duplicate logins replace the old session) |
| Cannot open site on `http://IP:8000` | Gunicorn listens on localhost only — use nginx on port **80** |
| Site dies when uploading a file | Increase gunicorn `--timeout` (e.g. `120`); set nginx `client_max_body_size 6m` and proxy timeouts |
| Login works but messages fail | Is `chat-server` running? Do `HOST`/`PORT` in `webapp.py` match `server.py`? |
| Decryption / garbled messages | Same `FERNET_KEY` in `.env` for both systemd services |
| `FERNET_KEY is not set` on start | Copy `.env.example` to `.env` and fill in values; set `EnvironmentFile=` in systemd units |
| Worker timeout in gunicorn logs | File uploads on slow connections — raise `--timeout` and nginx `proxy_read_timeout` |

## Security notice

This project is intended for **private, low-traffic** use. There is no strong authentication: anyone who knows a username can use it (or take over an active session). Do not store or transmit highly sensitive data.

## Further Development
I don't plan on continuing this project, so don't expect any updates. However if you want to improve something, be welcome to do that!

**Seriously, don't use it!**

Check out my youtube video detailing the development process if you haven't already: https://youtu.be/kYETJjmP_AA
