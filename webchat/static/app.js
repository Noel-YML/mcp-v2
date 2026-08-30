const emptyState = document.getElementById("emptyState");
const transcript = document.getElementById("transcript");
const chatArea = document.getElementById("chatArea");
const input = document.getElementById("messageInput");
const sendBtn = document.getElementById("sendBtn");
const chips = document.getElementById("chips");
const newChatBtn = document.getElementById("newChatBtn");
const switchHotelBtn = document.getElementById("switchHotelBtn");

const historyList = document.getElementById("historyList");
const showArchivedToggle = document.getElementById("showArchivedToggle");

const identifyOverlay = document.getElementById("identifyOverlay");
const hotelCodeInput = document.getElementById("hotelCodeInput");
const identifySubmitBtn = document.getElementById("identifySubmitBtn");
const identifyError = document.getElementById("identifyError");
const hotelAvatar = document.getElementById("hotelAvatar");
const hotelNameLabel = document.getElementById("hotelNameLabel");

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

// Live MCP App instances currently mounted in the transcript. At most one
// entry ever lives here (see teardownActiveMcpApps). H1.3H: this is no
// longer unconditionally torn down at the start of every new turn - a
// same-result evidence follow-up (see shouldPreserveMcpApp) now leaves it
// mounted on purpose, since evidence is supplemental to the analytical
// result it was requested for, not a replacement for it. Every OTHER new
// analytical response still tears it down before rendering (see
// renderResult) - the resultId/conversationId binding below is what makes
// that decision safe rather than inferred from text.
let activeMcpApps = [];

async function teardownActiveMcpApps() {
  const apps = activeMcpApps;
  activeMcpApps = [];
  await Promise.all(apps.map((app) => app.teardown().catch(() => {})));
}

// H1.3H: true only when ALL of - an app is currently mounted/pending, this
// turn's tool was actually get_result_evidence (evidence_for_result_id is
// BFF-authoritative and non-null ONLY in that case - see server.py's
// _summarize_response), it was authorized against the exact result_id the
// active App represents, and it's the same conversation. Never inferred
// from the assistant's own text (no "evidence"/"source" word-matching) -
// every input here is a value the server computed, not the model's prose.
function shouldPreserveMcpApp(result) {
  const activeApp = activeMcpApps[0];
  if (!activeApp || !result.evidence_for_result_id) return false;
  return activeApp.resultId === result.evidence_for_result_id && activeApp.conversationId === (result.conversation_id || conversationId);
}

function mountMcpAppIfPresent(container, mcpApp) {
  if (!mcpApp || !window.ArielMcpAppHost) return;
  // Registered synchronously, before the fetch below even starts, so a
  // teardownActiveMcpApps() triggered by the NEXT turn (sendMessage disables
  // the send button for the duration of one turn, but a fast click right as
  // it re-enables could still race this fetch) always finds this pending
  // mount and can cancel it - instead of the fetch resolving into a
  // now-orphaned iframe that nothing ever tracked or tore down. resultId/
  // conversationId are bound here too, synchronously, from the BFF-derived
  // descriptor and the module-level conversationId (already updated to this
  // turn's value by renderResult before this runs) - so a same-result
  // evidence turn that arrives while this fetch is still pending can still
  // correctly recognize and preserve it (H1.3H negative test 7).
  let mountedApp = null;
  let cancelled = false;
  activeMcpApps.push({
    resultId: mcpApp.tool_result && mcpApp.tool_result.result_id,
    conversationId,
    teardown: async () => {
      cancelled = true;
      if (mountedApp) await mountedApp.teardown();
    },
  });
  fetch(mcpApp.resource_url)
    .then((res) => {
      if (!res.ok) throw new Error("failed to fetch MCP App template");
      return res.text();
    })
    .then((templateHtml) => {
      if (cancelled) return;
      mountedApp = window.ArielMcpAppHost.mountRevenueApp({
        container,
        templateHtml,
        toolInput: mcpApp.tool_input,
        toolResult: mcpApp.tool_result,
      });
    })
    .catch((err) => {
      // The ordinary textual answer already rendered regardless - a failed
      // app mount is never surfaced as a chat error.
      console.warn("MCP App failed to mount:", err);
    });
}

function addAgentMessage(text, presentation, insights, actions, mcpApp) {
  const tpl = document.getElementById("tpl-agent").content.cloneNode(true);
  tpl.querySelector(".ai-body").textContent = text;
  if (presentation) {
    renderPresentation(tpl.querySelector(".ai-presentation"), presentation);
  }
  if (insights && insights.length) {
    renderInsights(tpl.querySelector(".ai-insights-slot"), insights);
  }
  if (actions && actions.length) {
    renderActions(tpl.querySelector(".ai-actions-slot"), actions, onActionClicked);
  }
  const mcpAppSlot = tpl.querySelector(".ai-mcp-app-slot");
  transcript.appendChild(tpl);
  if (mcpApp) {
    mountMcpAppIfPresent(mcpAppSlot, mcpApp);
  }
  scrollToBottom();
}

function onActionClicked(action) {
  sendMessage(action.promptFallback);
}

function addError(text) {
  const tpl = document.getElementById("tpl-error").content.cloneNode(true);
  tpl.querySelector(".ai-body").textContent = text;
  transcript.appendChild(tpl);
  scrollToBottom();
}

async function renderResult(result) {
  // Set BEFORE any teardown/mount decision below - mountMcpAppIfPresent
  // binds a new app to the module-level conversationId, and
  // shouldPreserveMcpApp compares against it, so both must already see
  // THIS turn's (possibly newly-created) conversation id.
  previousResponseId = result.response_id;
  conversationId = result.conversation_id || conversationId;

  // H1.3H: replace (new app) or remove (anything else non-preservable)
  // always tears down first; a same-result evidence turn preserves the
  // existing app untouched instead - see shouldPreserveMcpApp.
  if (result.mcp_app || !shouldPreserveMcpApp(result)) {
    await teardownActiveMcpApps();
  }

  if (result.text) {
    addAgentMessage(result.text, result.presentation, result.insights, result.actions, result.mcp_app);
  }
  (result.consents || []).forEach((c) => {
    addError("This tool needs sign-in first: " + c.consent_link);
  });
  loadHistory();
}

async function sendMessage(text) {
  if (!text.trim()) return;
  // H1.3H: teardown is no longer unconditional here - it now happens
  // (or doesn't) inside renderResult, once the response is known, so a
  // same-result evidence turn's existing chart is never destroyed before
  // the BFF has even said whether to preserve it. See renderResult/
  // shouldPreserveMcpApp.
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
      if (res.status === 401) {
        // H1.3H: a session invalidation must remove any active App
        // immediately, same as an explicit hotel switch/logout - it can no
        // longer be trusted to represent this (now-unidentified) session.
        await teardownActiveMcpApps();
        showIdentifyOverlay();
      }
      addError(data.error || "Something went wrong calling the agent.");
    } else {
      await renderResult(data);
    }
  } catch (err) {
    loadingNode.remove();
    addError(String(err));
  } finally {
    sendBtn.disabled = false;
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

newChatBtn.addEventListener("click", async () => {
  await teardownActiveMcpApps();
  conversationId = null;
  previousResponseId = null;
  showEmptyState();
});

/* ---------- hotel identification ---------- */

function setHotelDisplay(hotelName) {
  hotelNameLabel.textContent = hotelName || "Not identified";
  hotelAvatar.textContent = hotelName ? hotelName.trim().charAt(0).toUpperCase() : "?";
}

function showIdentifyOverlay() {
  identifyOverlay.classList.add("open");
  identifyError.textContent = "";
  hotelCodeInput.value = "";
  setTimeout(() => hotelCodeInput.focus(), 50);
}

function hideIdentifyOverlay() {
  identifyOverlay.classList.remove("open");
}

async function checkSession() {
  try {
    const res = await fetch("/api/session");
    const data = await res.json();
    if (data.hotel_name) {
      setHotelDisplay(data.hotel_name);
      hideIdentifyOverlay();
    } else {
      showIdentifyOverlay();
    }
  } catch (err) {
    showIdentifyOverlay();
  }
}

async function submitHotelCode() {
  const code = hotelCodeInput.value.trim();
  if (!code) {
    identifyError.textContent = "Enter a hotel code.";
    return;
  }
  identifySubmitBtn.disabled = true;
  identifyError.textContent = "";
  try {
    const res = await fetch("/api/identify-hotel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ hotel_code: code }),
    });
    const data = await res.json();
    if (!res.ok) {
      identifyError.textContent = data.error || "Could not identify that hotel code.";
      return;
    }
    setHotelDisplay(data.hotel_name);
    hideIdentifyOverlay();
    // H1.3D: the sidebar must reflect the NEWLY identified hotel's own
    // history immediately, not whatever the previously-identified hotel's
    // conversations left rendered in #historyList - the server now filters
    // by the current session's hotel, but only a fresh fetch picks that up.
    loadHistory();
  } catch (err) {
    identifyError.textContent = String(err);
  } finally {
    identifySubmitBtn.disabled = false;
  }
}

identifySubmitBtn.addEventListener("click", submitHotelCode);
hotelCodeInput.addEventListener("keydown", (e) => {
  const isEnter = e.key === "Enter" || e.keyCode === 13 || e.which === 13;
  if (isEnter) {
    e.preventDefault();
    submitHotelCode();
  }
});

switchHotelBtn.addEventListener("click", async () => {
  // H1.3D: same active-state reset as "New chat" (tear down any live MCP
  // App instead of merely hiding its iframe via showEmptyState's DOM wipe)
  // plus clearing the identified hotel itself - a hotel switch must leave
  // no trace of the previous hotel's active conversation/result state.
  await teardownActiveMcpApps();
  await fetch("/api/logout", { method: "POST" });
  setHotelDisplay(null);
  conversationId = null;
  previousResponseId = null;
  showEmptyState();
  // The old hotel's conversations must disappear from the sidebar the
  // moment its session ends, not linger until the next successful chat
  // turn under the new hotel eventually calls loadHistory() via renderResult.
  loadHistory();
  showIdentifyOverlay();
});

checkSession();

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
        await teardownActiveMcpApps();
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
  // H1.3H: switching to a DIFFERENT conversation is a conversation-
  // ownership change - any App mounted for the one being left must not
  // persist into the newly-loaded one (its own history never remounts an
  // App - see the loop below - so there is nothing to replace it with).
  await teardownActiveMcpApps();
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
      else addAgentMessage(m.text, m.presentation, m.insights, m.actions);
    });
  } catch (err) {
    addError("Could not load that conversation.");
  }
}
