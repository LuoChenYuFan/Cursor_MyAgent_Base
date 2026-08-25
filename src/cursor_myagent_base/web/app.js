const KEYS = {
  token: "myagent.apiToken",
  thread: "myagent.threadId",
};

const el = {
  token: document.getElementById("token"),
  thread: document.getElementById("thread"),
  connect: document.getElementById("connect"),
  openContacts: document.getElementById("openContacts"),
  statusLine: document.getElementById("statusLine"),
  contactsHint: document.getElementById("contactsHint"),
  contactsOverlay: document.getElementById("contactsOverlay"),
  contactsEmpty: document.getElementById("contactsEmpty"),
  contactList: document.getElementById("contactList"),
  closeContacts: document.getElementById("closeContacts"),
  threadLabel: document.getElementById("threadLabel"),
  domainPill: document.getElementById("domainPill"),
  log: document.getElementById("log"),
  composer: document.getElementById("composer"),
  message: document.getElementById("message"),
  send: document.getElementById("send"),
  clearChat: document.getElementById("clearChat"),
  overlay: document.getElementById("overlay"),
  mailTo: document.getElementById("mailTo"),
  mailEmail: document.getElementById("mailEmail"),
  mailSubject: document.getElementById("mailSubject"),
  mailBody: document.getElementById("mailBody"),
  approveMail: document.getElementById("approveMail"),
  cancelMail: document.getElementById("cancelMail"),
};

let busy = false;
let typingNode = null;
let lastConfirm = null;

function escapeHtml(text) {
  return String(text || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function currentThread() {
  return (el.thread.value || "").trim() || "web-local";
}

function setBusy(on) {
  busy = on;
  el.send.disabled = on || !(el.token.value || "").trim() || !(el.message.value || "").trim();
  el.approveMail.disabled = on;
  el.cancelMail.disabled = on;
  el.connect.disabled = on;
}

function setStatus(text, ok) {
  el.statusLine.textContent = text;
  el.statusLine.style.color = ok ? "#b7d7c3" : "#cbbbaa";
}

function domainLabel(data) {
  const domains = data.domains || [];
  if (data.current_domain) {
    return data.current_domain === "trip" ? "行程" : data.current_domain === "office" ? "办公" : data.current_domain;
  }
  if (domains.includes("trip") && domains.includes("office")) return "行程 → 办公";
  if (domains.includes("trip")) return "行程";
  if (domains.includes("office")) return "办公";
  if (data.intent === "chat") return "闲聊";
  return "待命";
}

function applyMeta(data) {
  if (!data) return;
  el.threadLabel.textContent = data.thread_id || currentThread();
  el.domainPill.textContent = domainLabel(data);
}

function ensureEmptyState() {
  if (el.log.children.length) return;
  el.log.innerHTML = `
    <div class="empty">
      <h2>从一句日常问题开始</h2>
      <p>查天气、规划路线，或给通讯录里的人发信。需要发邮件时，这里会弹出确认，不会直接寄出。</p>
    </div>`;
}

function addBubble(role, text) {
  const empty = el.log.querySelector(".empty");
  if (empty) empty.remove();
  const node = document.createElement("div");
  node.className = `bubble ${role}`;
  node.innerHTML = escapeHtml(text);
  el.log.appendChild(node);
  el.log.scrollTop = el.log.scrollHeight;
  return node;
}

function dotsHtml() {
  return '<span class="dots" aria-hidden="true"><span>.</span><span>.</span><span>.</span></span>';
}

function showTyping() {
  const empty = el.log.querySelector(".empty");
  if (empty) empty.remove();
  typingNode = document.createElement("div");
  typingNode.className = "bubble assistant typing";
  typingNode.innerHTML = `<div class="live-status">正在处理${dotsHtml()}</div><div class="live-text"></div>`;
  el.log.appendChild(typingNode);
  el.log.scrollTop = el.log.scrollHeight;
}

function setTypingStatus(text) {
  if (!typingNode) showTyping();
  const status = typingNode.querySelector(".live-status");
  if (status) status.innerHTML = `${escapeHtml(text)}${dotsHtml()}`;
}

function appendTypingToken(text) {
  if (!typingNode) showTyping();
  const live = typingNode.querySelector(".live-text");
  if (!live) return;
  live.textContent += text;
  el.log.scrollTop = el.log.scrollHeight;
}

function hideTyping() {
  if (typingNode) {
    typingNode.remove();
    typingNode = null;
  }
}

function finishTyping(reply) {
  if (!typingNode) {
    if (reply && reply !== "(没有生成回复)") addBubble("assistant", reply);
    return;
  }
  const live = typingNode.querySelector(".live-text");
  const streamed = live ? live.textContent.trim() : "";
  const finalText = (reply && reply !== "(没有生成回复)" ? reply : streamed).trim();
  typingNode.classList.remove("typing");
  typingNode.innerHTML = escapeHtml(finalText || "（没有生成回复）");
  typingNode = null;
  el.log.scrollTop = el.log.scrollHeight;
}

function showConfirm(payload) {
  if (!payload) {
    el.overlay.hidden = true;
    return;
  }
  lastConfirm = payload;
  el.mailTo.textContent = payload.to || "—";
  el.mailEmail.textContent = payload.email || "—";
  el.mailSubject.textContent = payload.subject || "—";
  el.mailBody.textContent = payload.body || "—";
  el.overlay.hidden = false;
}

function errorMessage(errBody, fallback) {
  if (!errBody) return fallback;
  const detail = errBody.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") return detail.message || fallback;
  return fallback;
}

async function api(path, options = {}) {
  const token = (el.token.value || "").trim();
  if (!token) throw new Error("请先填写 API Token");
  const headers = {
    Authorization: `Bearer ${token}`,
    ...(options.body ? { "Content-Type": "application/json" } : {}),
    ...(options.headers || {}),
  };
  const response = await fetch(path, { ...options, headers });
  let body = null;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (!response.ok) {
    const error = new Error(errorMessage(body, `请求失败（${response.status}）`));
    error.status = response.status;
    error.payload = body && body.detail && typeof body.detail === "object" ? body.detail : body;
    throw error;
  }
  return body;
}

function handlePayload(data, { streamed } = {}) {
  applyMeta(data);
  if (data.status === "needs_confirmation" && data.confirm) {
    hideTyping();
    addBubble("system", "邮件已拟好，发送前请确认收件人和正文。");
    showConfirm(data.confirm);
    return;
  }
  showConfirm(null);
  if (data.status === "pending_run") {
    hideTyping();
    addBubble("system", "上一轮任务还没跑完。再点一次发送，或点「连接会话」续跑。");
    return;
  }
  if (streamed) {
    finishTyping(data.reply || "");
    return;
  }
  if (data.reply && data.reply !== "(没有生成回复)") {
    addBubble("assistant", data.reply);
  }
}

async function readSse(response, onEvent) {
  if (!response.body) throw new Error("浏览器无法读取流式响应");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    const chunks = buf.split("\n\n");
    buf = chunks.pop() || "";
    for (const block of chunks) {
      const line = block.split("\n").find((item) => item.startsWith("data: "));
      if (!line) continue;
      onEvent(JSON.parse(line.slice(6)));
    }
  }
}

async function streamChat(message) {
  const token = (el.token.value || "").trim();
  if (!token) throw new Error("请先填写 API Token");
  const response = await fetch("/v1/chat/stream", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      message,
      thread_id: currentThread(),
      auto_resume: true,
    }),
  });
  if (!response.ok) {
    let body = null;
    try {
      body = await response.json();
    } catch {
      body = null;
    }
    const error = new Error(errorMessage(body, `请求失败（${response.status}）`));
    error.status = response.status;
    error.payload = body && body.detail && typeof body.detail === "object" ? body.detail : body;
    throw error;
  }
  let donePayload = null;
  let streamError = "";
  await readSse(response, (event) => {
    if (event.type === "status" && event.text) setTypingStatus(event.text);
    else if (event.type === "token" && event.text) appendTypingToken(event.text);
    else if (event.type === "done") donePayload = event.payload;
    else if (event.type === "error") streamError = event.message || "本轮执行失败";
  });
  if (streamError) throw new Error(streamError);
  return donePayload;
}

function renderContacts(items) {
  const list = Array.isArray(items) ? items : [];
  el.contactsHint.textContent = list.length
    ? `通讯录：${list.map((item) => item.name).join("、")}（点「通讯录」查看）`
    : "通讯录为空。请编辑项目根目录 contacts.json";
  el.contactsEmpty.hidden = list.length > 0;
  el.contactList.innerHTML = "";
  for (const item of list) {
    const li = document.createElement("li");
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "contact-card";
    btn.innerHTML = `<strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.email)}</span>`;
    btn.addEventListener("click", () => {
      el.message.value = `给${item.name}发一封邮件，`;
      el.message.dispatchEvent(new Event("input"));
      el.contactsOverlay.hidden = true;
      el.message.focus();
    });
    li.appendChild(btn);
    el.contactList.appendChild(li);
  }
}

async function loadContacts() {
  const data = await api("/v1/contacts");
  renderContacts(data.contacts || []);
}

async function openContacts() {
  try {
    await loadContacts();
    el.contactsOverlay.hidden = false;
  } catch (err) {
    setStatus(err.message || "无法读取通讯录", false);
  }
}

async function connect() {
  const thread = currentThread();
  el.thread.value = thread;
  localStorage.setItem(KEYS.token, el.token.value.trim());
  localStorage.setItem(KEYS.thread, thread);
  setBusy(true);
  try {
    const session = await api(`/v1/threads/${encodeURIComponent(thread)}`);
    applyMeta(session);
    await loadContacts();
    setStatus("已连接，可以开始对话。", true);
    el.log.innerHTML = "";
    if (session.last_user) addBubble("user", session.last_user);
    if (session.status === "needs_confirmation" && session.confirm) {
      addBubble("system", "该会话有一封待确认邮件。");
      showConfirm(session.confirm);
    } else if (session.reply && session.reply !== "(没有生成回复)") {
      addBubble("assistant", session.reply);
    } else {
      ensureEmptyState();
    }
  } catch (err) {
    setStatus(err.message || "连接失败", false);
    ensureEmptyState();
  } finally {
    setBusy(false);
  }
}

async function sendMessage(event) {
  event.preventDefault();
  const text = (el.message.value || "").trim();
  if (!text || busy) return;
  localStorage.setItem(KEYS.token, el.token.value.trim());
  localStorage.setItem(KEYS.thread, currentThread());
  addBubble("user", text);
  el.message.value = "";
  setBusy(true);
  showTyping();
  try {
    const data = await streamChat(text);
    if (!data) {
      hideTyping();
      addBubble("system", "没有收到完整回复，请再试一次。");
    } else {
      handlePayload(data, { streamed: true });
    }
    setStatus("本轮完成。", true);
  } catch (err) {
    hideTyping();
    if (err.status === 409 && err.payload && err.payload.confirm) {
      addBubble("system", err.message || "请先确认或取消上一封邮件。");
      showConfirm(err.payload.confirm);
      applyMeta(err.payload);
    } else {
      addBubble("system", err.message || "发送失败");
      setStatus(err.message || "发送失败", false);
    }
  } finally {
    setBusy(false);
    el.message.focus();
  }
}

async function decideMail(approved) {
  showConfirm(null);
  setBusy(true);
  showTyping();
  setTypingStatus(approved ? "正在发送邮件" : "正在取消发送");
  setStatus(approved ? "正在发送邮件…" : "正在取消发送…", true);
  try {
    const data = await api("/v1/confirm", {
      method: "POST",
      body: JSON.stringify({
        thread_id: currentThread(),
        approve: approved,
      }),
    });
    lastConfirm = null;
    if (data.reply && data.reply !== "(没有生成回复)") {
      finishTyping(data.reply);
    } else {
      hideTyping();
      addBubble("system", approved ? "已确认发送。" : "已取消发送。");
    }
    applyMeta(data);
    setStatus(approved ? "邮件已处理。" : "已取消发信。", true);
  } catch (err) {
    hideTyping();
    addBubble("system", err.message || "确认失败");
    setStatus(err.message || "确认失败", false);
    if (lastConfirm) showConfirm(lastConfirm);
  } finally {
    setBusy(false);
  }
}

el.token.value = localStorage.getItem(KEYS.token) || "";
el.thread.value = localStorage.getItem(KEYS.thread) || "web-local";
el.threadLabel.textContent = currentThread();
ensureEmptyState();
setBusy(false);

el.connect.addEventListener("click", connect);
el.openContacts.addEventListener("click", openContacts);
el.closeContacts.addEventListener("click", () => {
  el.contactsOverlay.hidden = true;
});
el.contactsOverlay.addEventListener("click", (event) => {
  if (event.target === el.contactsOverlay) el.contactsOverlay.hidden = true;
});
el.composer.addEventListener("submit", sendMessage);
el.clearChat.addEventListener("click", () => {
  el.log.innerHTML = "";
  ensureEmptyState();
});
el.approveMail.addEventListener("click", () => decideMail(true));
el.cancelMail.addEventListener("click", () => decideMail(false));
el.message.addEventListener("input", () => {
  el.send.disabled = busy || !(el.token.value || "").trim() || !(el.message.value || "").trim();
  el.message.style.height = "auto";
  el.message.style.height = `${Math.min(el.message.scrollHeight, 160)}px`;
});
el.token.addEventListener("input", () => {
  el.send.disabled = busy || !(el.token.value || "").trim() || !(el.message.value || "").trim();
});
el.message.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    el.composer.requestSubmit();
  }
});
