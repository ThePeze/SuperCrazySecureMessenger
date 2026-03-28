const messagesDiv = document.getElementById("messages");
const form = document.getElementById("form");
const input = document.getElementById("input");
const fileInput = document.getElementById("fileInput");
const typingIndicator = document.getElementById("typingIndicator");

let selectedUser = "Public Chat";
const conversations = { "Public Chat": [] };

let lastMessageId = -1;
let lastUsersKey = "";
let typingTimer = null;

function ensureConversation(user) {
    if (!conversations[user]) conversations[user] = [];
}

function messageNode(entry) {
    const div = document.createElement("div");

    if (entry.type === "file") {
        div.textContent = entry.text + " ";
        if (entry.file_data && entry.file_name) {
            const a = document.createElement("a");
            a.textContent = "[download]";
            a.href = `data:application/octet-stream;base64,${entry.file_data}`;
            a.download = entry.file_name;
            a.style.color = "#93c5fd";
            div.appendChild(a);
        }
        return div;
    }

    div.textContent = entry.text || "";
    return div;
}

function renderConversation(user) {
    messagesDiv.innerHTML = "";

    const entries = conversations[user] || [];
    entries.forEach(entry => {
        messagesDiv.appendChild(messageNode(entry));
    });
}

function appendMessage(entry) {
    const targetUser = entry.conversation || "Public Chat";
    ensureConversation(targetUser);
    conversations[targetUser].push(entry);

    if (targetUser === selectedUser) {
        messagesDiv.appendChild(messageNode(entry));
    }
}

function selectUser(user) {
    selectedUser = user;

    const header = document.getElementById("chatHeader");
    if (header) header.textContent = user;

    // Update active highlighting immediately.
    const publicDiv = document.getElementById("publicChat");
    if (publicDiv) {
        publicDiv.classList.toggle("active", user === "Public Chat");
    }

    document.querySelectorAll("#users .user").forEach(div => {
        div.classList.toggle("active", div.textContent === user);
    });

    ensureConversation(selectedUser);
    renderConversation(selectedUser);
    fetchTypingStatus().catch(console.error);
}

document.getElementById("publicChat").onclick = () => selectUser("Public Chat");

form.addEventListener("submit", async (e) => {
    e.preventDefault();

    if (selectedUser !== "Public Chat" && fileInput.files.length > 0) {
        const file = fileInput.files[0];
        if (file.size > 5 * 1024 * 1024) {
            alert("File too large. Max 5MB.");
            return;
        }

        const fd = new FormData();
        fd.append("target", selectedUser);
        fd.append("file", file);

        const fileRes = await fetch("/send_file", { method: "POST", body: fd });
        if (fileRes.status === 401) window.location = "/";
        if (!fileRes.ok) alert(await fileRes.text());
        fileInput.value = "";
        return;
    }

    let message = input.value;
    if (!message.trim()) return;

    if (selectedUser !== "Public Chat") {
        message = `/msg ${selectedUser} ${message}`;
    }

    const res = await fetch("/send", {
        method: "POST",
        body: new URLSearchParams({ message })
    });
    if (res.status === 401) window.location = "/";
    if (!res.ok) alert(await res.text());

    input.value = "";
    if (selectedUser !== "Public Chat") {
        await sendTyping(false);
    }
});

const disconnectBtn = document.getElementById("disconnectBtn");
if (disconnectBtn) {
    disconnectBtn.addEventListener("click", async () => {
        await fetch("/disconnect", { method: "POST" }).catch(() => {});
        window.location = "/";
    });
}

async function sendTyping(isTyping) {
    if (selectedUser === "Public Chat") return;
    await fetch("/typing", {
        method: "POST",
        body: new URLSearchParams({
            target: selectedUser,
            typing: isTyping ? "1" : "0",
        }),
    }).catch(() => {});
}

input.addEventListener("input", async () => {
    if (selectedUser === "Public Chat") return;

    await sendTyping(input.value.trim().length > 0);

    if (typingTimer) clearTimeout(typingTimer);
    typingTimer = setTimeout(() => {
        sendTyping(false);
    }, 1500);
});

async function fetchTypingStatus() {
    if (selectedUser === "Public Chat") {
        typingIndicator.textContent = "";
        return;
    }

    const res = await fetch(`/typing_status?target=${encodeURIComponent(selectedUser)}`);
    if (res.status === 401) {
        window.location = "/";
        return;
    }

    const data = await res.json();
    typingIndicator.textContent = data.typing ? `${selectedUser} is typing...` : "";
}

async function fetchMessages() {
    const res = await fetch(`/messages?since=${encodeURIComponent(lastMessageId)}`);
    if (res.status === 401) {
        window.location = "/";
        return;
    }
    const data = await res.json();

    data.forEach(m => {
        appendMessage(m);
        if (typeof m.id === "number" && m.id > lastMessageId) lastMessageId = m.id;
    });
}

async function fetchUsers() {
    const res = await fetch("/users");
    if (res.status === 401) {
        window.location = "/";
        return;
    }
    const users = await res.json();

    const usersKey = JSON.stringify(users);
    if (usersKey === lastUsersKey) return;
    lastUsersKey = usersKey;

    const container = document.getElementById("users");
    container.innerHTML = "";

    users.forEach(userObj => {
        const user = userObj.username;
        const div = document.createElement("div");
        div.className = "user";

        const dot = document.createElement("span");
        dot.className = `statusDot ${userObj.online ? "online" : "offline"}`;
        const name = document.createElement("span");
        name.textContent = user;
        div.appendChild(dot);
        div.appendChild(name);

        if (user === selectedUser) div.classList.add("active");

        div.onclick = () => selectUser(user);
        container.appendChild(div);
    });

    const publicDiv = document.getElementById("publicChat");
    if (publicDiv) {
        publicDiv.classList.toggle("active", selectedUser === "Public Chat");
    }
}

// Initial paint.
selectUser("Public Chat");
fetchUsers().catch(console.error);
fetchMessages().catch(console.error);
fetchTypingStatus().catch(console.error);

setInterval(() => fetchMessages().catch(console.error), 750);
setInterval(() => fetchUsers().catch(console.error), 10000);
setInterval(() => fetchTypingStatus().catch(console.error), 600);