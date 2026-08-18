const emptyState = document.getElementById("emptyState");
const transcript = document.getElementById("transcript");
const chatArea = document.getElementById("chatArea");
const input = document.getElementById("messageInput");
const sendBtn = document.getElementById("sendBtn");
const chips = document.getElementById("chips");
const newChatBtn = document.getElementById("newChatBtn");

const historyList = document.getElementById("historyList");
const showArchivedToggle = document.getElementById("showArchivedToggle");

let conversationId = null;
let previousResponseId = null;

function scrollToBottom() {
  chatArea.scrollTop = chatArea.scrollHeight;
}

function showTranscript() {
  emptyState.style.display = "none";
  transcript.classList.add("active");
}

function showEmptyState() {
  transcript.innerHTML = "";
  transcript.classList.remove("active");
  emptyState.style.display = "flex";
}

function addUserMessage(text) {
  const tpl = document.getElementById("tpl-user").content.cloneNode(true);
  tpl.querySelector(".bubble").textContent = text;
  transcript.appendChild(tpl);
  scrollToBottom();
}

function addLoading() {
  const tpl = document.getElementById("tpl-loading").content.cloneNode(true);
  transcript.appendChild(tpl);
  scrollToBottom();
  return transcript.lastElementChild;
}

function addAgentMessage(text) {
  const tpl = document.getElementById("tpl-agent").content.cloneNode(true);
  tpl.querySelector(".ai-body").textContent = text;
  transcript.appendChild(tpl);
  scrollToBottom();
}

function addError(text) {
  const tpl = document.getElementById("tpl-error").content.cloneNode(true);
  tpl.querySelector(".ai-body").textContent = text;
  transcript.appendChild(tpl);
  scrollToBottom();
}

function addApproval(approval) {
  const tpl = document.getElementById("tpl-approval").content.cloneNode(true);
  tpl.querySelector(".approval-name").textContent = approval.name;
  tpl.querySelector(".approval-args").textContent = approval.arguments;
  const approveBtn = tpl.querySelector(".approve");
  const denyBtn = tpl.querySelector(".deny");
  approveBtn.addEventListener("click", () => respondToApproval(approval.id, true, approveBtn, denyBtn));
  denyBtn.addEventListener("click", () => respondToApproval(approval.id, false, approveBtn, denyBtn));
  transcript.appendChild(tpl);
  scrollToBottom();
}

function renderResult(result) {
  if (result.text) {
    addAgentMessage(result.text);
  }
  (result.approvals || []).forEach(addApproval);
  (result.consents || []).forEach((c) => {
    addError("This tool needs sign-in first: " + c.consent_link);
  });
  previousResponseId = result.response_id;
  conversationId = result.conversation_id || conversationId;
  loadHistory();
}

async function sendMessage(text) {
  if (!text.trim()) return;
  showTranscript();
  addUserMessage(text);
  input.value = "";
  autoGrow();
  sendBtn.disabled = true;

  const loadingNode = addLoading();

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, conversation_id: conversationId }),
    });
    loadingNode.remove();
    const data = await res.json();
    if (!res.ok) {
      addError(data.error || "Something went wrong calling the agent.");
    } else {
      renderResult(data);
    }
  } catch (err) {
    loadingNode.remove();
    addError(String(err));
  } finally {
    sendBtn.disabled = false;
  }
}

async function respondToApproval(approvalRequestId, approved, approveBtn, denyBtn) {
  approveBtn.disabled = true;
  denyBtn.disabled = true;
  const loadingNode = addLoading();
  try {
    const res = await fetch("/api/approve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        conversation_id: conversationId,
        approval_request_id: approvalRequestId,
        approved: approved,
      }),
    });
    loadingNode.remove();
    const data = await res.json();
    if (!res.ok) {
      addError(data.error || "Something went wrong continuing after approval.");
    } else {
      renderResult(data);
    }
  } catch (err) {
    loadingNode.remove();
    addError(String(err));
  }
}

function autoGrow() {
  input.style.height = "auto";
  const next = Math.min(input.scrollHeight, 120);
  input.style.height = next + "px";
  input.classList.toggle("overflowing", input.scrollHeight > 120);
}
autoGrow();

sendBtn.addEventListener("click", () => sendMessage(input.value));

input.addEventListener("input", autoGrow);
input.addEventListener("keydown", (e) => {
  const isEnter = e.key === "Enter" || e.keyCode === 13 || e.which === 13;
  if (isEnter && !e.shiftKey) {
    e.preventDefault();
    sendMessage(input.value);
  }
});

chips.addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (!chip) return;
  sendMessage(chip.dataset.prompt);
});

newChatBtn.addEventListener("click", () => {
  conversationId = null;
  previousResponseId = null;
  showEmptyState();
});

/* ---------- history (always visible in the sidebar) ---------- */

function formatTime(iso) {
  const d = new Date(iso);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

showArchivedToggle.addEventListener("change", loadHistory);
loadHistory();

async function loadHistory() {
  const includeArchived = showArchivedToggle.checked ? "1" : "0";
  historyList.innerHTML = "";
  try {
    const res = await fetch("/api/conversations?archived=" + includeArchived);
    const items = await res.json();
    renderHistoryList(items);
  } catch (err) {
    historyList.innerHTML = '<div class="history-empty">Could not load history.</div>';
  }
}

function renderHistoryList(items) {
  historyList.innerHTML = "";
  if (!items.length) {
    historyList.innerHTML = '<div class="history-empty">No conversations yet.</div>';
    return;
  }
  items.forEach((item) => {
    const tpl = document.getElementById("tpl-history-item").content.cloneNode(true);
    const row = tpl.querySelector(".history-item");
    if (item.archived) row.classList.add("archived");
    tpl.querySelector(".history-item-title").textContent = item.title || "New conversation";
    tpl.querySelector(".history-item-time").textContent = formatTime(item.updated_at);

    tpl.querySelector(".history-item-main").addEventListener("click", () => selectConversation(item.id));

    tpl.querySelector(".rename-btn").addEventListener("click", async (e) => {
      e.stopPropagation();
      const newTitle = prompt("Rename conversation", item.title || "");
      if (newTitle === null || !newTitle.trim()) return;
      await fetch(`/api/conversations/${item.id}/rename`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: newTitle.trim() }),
      });
      loadHistory();
    });

    const archiveBtn = tpl.querySelector(".archive-btn");
    archiveBtn.title = item.archived ? "Unarchive" : "Archive";
    archiveBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      await fetch(`/api/conversations/${item.id}/archive`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ archived: !item.archived }),
      });
      loadHistory();
    });

    tpl.querySelector(".delete-btn").addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm("Delete this conversation? This can't be undone.")) return;
      await fetch(`/api/conversations/${item.id}`, { method: "DELETE" });
      if (conversationId === item.id) {
        conversationId = null;
        previousResponseId = null;
        showEmptyState();
      }
      loadHistory();
    });

    historyList.appendChild(tpl);
  });
}

async function selectConversation(id) {
  try {
    const res = await fetch(`/api/conversations/${id}`);
    if (!res.ok) return;
    const convo = await res.json();
    conversationId = convo.id;
    previousResponseId = convo.last_response_id;
    showTranscript();
    transcript.innerHTML = "";
    (convo.messages || []).forEach((m) => {
      if (m.role === "user") addUserMessage(m.text);
      else addAgentMessage(m.text);
    });
  } catch (err) {
    addError("Could not load that conversation.");
  }
}
