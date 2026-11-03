"use strict";
const chat = document.getElementById("chat");
const input = document.getElementById("input");
const send = document.getElementById("send");

// 会话凭证：由服务端签发（带 HMAC 签名），前端只当作不透明字符串保存。
// 刷新后仍沿用（localStorage），保证多轮记忆跨刷新不丢。
let sessionId = localStorage.getItem("dse_session");

async function ensureSession(force = false) {
  if (sessionId && !force) return sessionId;
  const resp = await fetch("/session", { method: "POST" });
  if (!resp.ok) throw new Error("无法建立会话（" + resp.status + "）");
  const data = await resp.json();
  sessionId = data.session_id;
  localStorage.setItem("dse_session", sessionId);
  return sessionId;
}

const INTENT_LABEL = {
  schools: "查学校", policy: "问政策", chitchat: "闲聊",
  clarify: "追问", follow_up: "接着上轮",
};

function addBubble(role, text, isProcess) {
  const div = document.createElement("div");
  div.className = "msg " + role + (isProcess ? " process" : "");
  div.textContent = text;
  chat.appendChild(div);
  chat.scrollTop = chat.scrollHeight;
  return div;
}

async function sendMessage() {
  const text = input.value.trim();
  if (!text) return;
  input.value = "";
  addBubble("user", text);
  const bubble = addBubble("assistant", "思考中…", true);
  const ui = { streaming: false };

  try {
    await ensureSession();
    const post = () => fetch("/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: sessionId, message: text }),
    });

    let resp = await post();
    if (resp.status === 401) {
      // 凭证失效（服务端重启换了密钥）：重新签发一次，重试
      await ensureSession(true);
      resp = await post();
    }
    if (!resp.ok) throw new Error("HTTP " + resp.status);

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) >= 0) {
        const raw = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        const line = raw.split("\n").find((l) => l.startsWith("data: "));
        if (!line) continue;
        handleEvent(JSON.parse(line.slice(6)), bubble, ui);
      }
    }
  } catch (e) {
    bubble.classList.remove("process");
    bubble.textContent = "请求失败：" + e;
  }
}

function handleEvent(data, bubble, ui) {
  if (data.type === "stream") {
    if (!ui.streaming) {
      bubble.classList.remove("process");
      bubble.textContent = "";
      ui.streaming = true;
    }
    bubble.textContent += data.text;   // token 级追加（打字机）
  } else if (data.type === "node" && data.node === "tool") {
    if (!ui.streaming) {
      bubble.classList.remove("process");
      bubble.textContent = "";
      ui.streaming = true;
    }
    bubble.textContent += "\n\n🔍 正在查询数据…\n\n";
  } else if (data.type === "answer") {
    bubble.classList.remove("process");
    bubble.textContent = data.text;
    ui.streaming = true;
  } else if (data.type === "error") {
    bubble.classList.remove("process");
    bubble.textContent = data.text;
  }
  chat.scrollTop = chat.scrollHeight;
}

send.addEventListener("click", sendMessage);
input.addEventListener("keydown", (e) => { if (e.key === "Enter") sendMessage(); });
