const SPEAKER_COLORS = [
  "#007aff", "#34c759", "#ff9500", "#af52de",
  "#ff3b30", "#5ac8fa", "#ffcc00", "#ff2d55",
];

let currentMeetingId = null;
let speakerColorMap = {};

async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  return res.json();
}

function formatDuration(secs) {
  if (!secs) return "";
  const m = Math.floor(secs / 60);
  const h = Math.floor(m / 60);
  if (h > 0) return `${h}h ${m % 60}m`;
  if (m > 0) return `${m}m`;
  return "< 1m";
}

function formatTimestamp(secs) {
  const m = Math.floor(secs / 60);
  const s = Math.floor(secs % 60);
  const h = Math.floor(m / 60);
  if (h > 0) {
    return `${h}:${(m % 60).toString().padStart(2, "0")}:${s.toString().padStart(2, "0")}`;
  }
  return `${m}:${s.toString().padStart(2, "0")}`;
}

function speakerColor(speaker) {
  if (!(speaker in speakerColorMap)) {
    const idx = Object.keys(speakerColorMap).length % SPEAKER_COLORS.length;
    speakerColorMap[idx] = SPEAKER_COLORS[idx];
    speakerColorMap[speaker] = SPEAKER_COLORS[idx];
  }
  return speakerColorMap[speaker];
}

function formatDurationLong(secs) {
  if (!secs) return "";
  const m = Math.floor(secs / 60);
  if (m === 0) return "< 1 minute";
  if (m === 1) return "1 minute";
  return `${m} minutes`;
}

function formatEyebrow(dateStr) {
  try {
    const d = new Date(dateStr);
    const day = d.toLocaleDateString("en-GB", { weekday: "long" }).toUpperCase();
    const date = d.toLocaleDateString("en-GB", { year: "numeric", month: "long", day: "numeric" });
    const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit", hour12: false });
    return `${day}, ${date} · ${time}`;
  } catch (e) {
    return "";
  }
}

async function loadMeetings(query, autoSelect = false) {
  const url = query
    ? `/api/meetings/search?q=${encodeURIComponent(query)}`
    : "/api/meetings";
  const meetings = await fetchJSON(url);
  renderSidebar(meetings);

  if (autoSelect && meetings.length > 0 && !currentMeetingId) {
    selectMeeting(meetings[0].id);
  }
}

function renderSidebar(meetings) {
  const list = document.getElementById("meeting-list");
  if (!meetings.length) {
    list.innerHTML = '<div class="meeting-item" style="color:var(--color-slate);cursor:default;font-size:12px;padding-left:12px">No meetings yet</div>';
    return;
  }
  list.innerHTML = meetings
    .map((m) => {
      const duration = m.duration_secs ? formatDuration(m.duration_secs) : "";
      return `<div class="meeting-item${m.id === currentMeetingId ? " active" : ""}" data-id="${m.id}">
        <div class="meeting-title-text">${esc(m.title)}</div>
        ${duration ? `<div class="meeting-info">${duration}</div>` : ""}
      </div>`;
    })
    .join("");

  list.querySelectorAll(".meeting-item[data-id]").forEach((el) => {
    el.addEventListener("click", () => selectMeeting(parseInt(el.dataset.id)));
  });
}

async function selectMeeting(id) {
  hideSettings();
  currentMeetingId = id;
  speakerColorMap = {};
  const meeting = await fetchJSON(`/api/meetings/${id}`);
  renderMeeting(meeting);
  document.querySelectorAll(".meeting-item").forEach((el) => {
    el.classList.toggle("active", parseInt(el.dataset.id) === id);
  });
}

function renderMeeting(m) {
  document.getElementById("empty-state").style.display = "none";
  document.getElementById("meeting-view").style.display = "block";

  const eyebrowEl = document.getElementById("meeting-eyebrow");
  eyebrowEl.textContent = formatEyebrow(m.date);

  const titleEl = document.getElementById("meeting-title");
  titleEl.textContent = m.title;
  titleEl.dataset.id = m.id;

  const meta = [
    m.speaker_count ? `${m.speaker_count} speaker${m.speaker_count > 1 ? "s" : ""}` : null,
    m.duration_secs ? formatDurationLong(m.duration_secs) : null,
    "Transcribed locally",
  ]
    .filter(Boolean)
    .join(" · ");
  document.getElementById("meeting-meta").textContent = meta;

  const summaryBody = document.getElementById("summary-body");
  const summarizeBtn = document.getElementById("summarize-btn");
  if (m.summary) {
    summaryBody.innerHTML = renderMarkdown(m.summary);
    summarizeBtn.style.display = "none";
  } else {
    summaryBody.innerHTML = '<span class="no-summary">No summary generated yet.</span>';
    summarizeBtn.style.display = "inline-block";
    summarizeBtn.disabled = false;
    summarizeBtn.textContent = "Summarize";
  }

  renderTranscript(m.transcript || []);

  // Show/hide delete audio option
  const deleteAudioBtn = document.getElementById("delete-audio-btn");
  if (deleteAudioBtn) {
    deleteAudioBtn.style.display = m.has_audio ? "block" : "none";
  }
}

function renderTranscript(segments) {
  const container = document.getElementById("transcript");
  if (!segments.length) {
    container.innerHTML = '<div style="color:var(--text-tertiary)">No transcript available.</div>';
    return;
  }
  container.innerHTML = segments
    .map((s) => {
      const color = speakerColor(s.speaker);
      return `<div class="segment">
        <div class="segment-speaker">
          <div class="speaker-name" style="color:${color}" contenteditable="true" data-original="${esc(s.speaker)}">${esc(s.speaker)}</div>
          <div class="segment-time">${formatTimestamp(s.start)}</div>
        </div>
        <div class="segment-text">${esc(s.text || "")}</div>
      </div>`;
    })
    .join("");

  // Speaker renaming
  container.querySelectorAll(".speaker-name").forEach((el) => {
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        e.preventDefault();
        el.blur();
      }
    });
    el.addEventListener("blur", async () => {
      const oldName = el.dataset.original;
      const newName = el.textContent.trim();
      if (oldName && newName && oldName !== newName) {
        await fetch(`/api/meetings/${currentMeetingId}/rename_speaker`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ old_name: oldName, new_name: newName }),
        });
        selectMeeting(currentMeetingId);
      }
    });
  });
}

function renderMarkdown(text) {
  return esc(text)
    .replace(/^### (.+)$/gm, "<h4>$1</h4>")
    .replace(/^## (.+)$/gm, "<h3>$1</h3>")
    .replace(/^# (.+)$/gm, "<h2>$1</h2>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/^- (.+)$/gm, "<li>$1</li>")
    .replace(/(<li>.*<\/li>)/gs, "<ul>$1</ul>")
    .replace(/\n{2,}/g, "<br><br>")
    .replace(/\n/g, "<br>");
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

// Title editing
document.getElementById("meeting-title").addEventListener("blur", async (e) => {
  const id = e.target.dataset.id;
  const newTitle = e.target.textContent.trim();
  if (id && newTitle) {
    await fetch(`/api/meetings/${id}/title`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title: newTitle }),
    });
    loadMeetings();
  }
});

document.getElementById("meeting-title").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    e.target.blur();
  }
});

// Summarize button
const summarizeBtn = document.getElementById("summarize-btn");
if (summarizeBtn) {
  summarizeBtn.addEventListener("click", async () => {
    summarizeBtn.disabled = true;
    summarizeBtn.textContent = "Summarizing...";
    await fetch(`/api/meetings/${currentMeetingId}/summarize`, { method: "POST" });
  });
}

// Delete dropdown
const deleteBtn = document.getElementById("delete-btn");
const deleteDropdown = document.getElementById("delete-dropdown");

function closeDeleteDropdown() {
  deleteDropdown.style.display = "none";
}

deleteBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  const isOpen = deleteDropdown.style.display !== "none";
  deleteDropdown.style.display = isOpen ? "none" : "block";
});

document.addEventListener("click", closeDeleteDropdown);

document.getElementById("delete-meeting-btn").addEventListener("click", async (e) => {
  e.stopPropagation();
  if (!currentMeetingId) return;
  closeDeleteDropdown();
  try {
    const res = await fetch(`/api/meetings/${currentMeetingId}`, { method: "DELETE" });
    if (res.ok) {
      currentMeetingId = null;
      document.getElementById("meeting-view").style.display = "none";
      document.getElementById("empty-state").style.display = "flex";
      loadMeetings();
      showStatusMessage("Meeting deleted");
    }
  } catch (err) {
    console.error("Delete meeting failed:", err);
  }
});

document.getElementById("delete-audio-btn").addEventListener("click", async (e) => {
  e.stopPropagation();
  if (!currentMeetingId) return;
  closeDeleteDropdown();
  try {
    const res = await fetch(`/api/meetings/${currentMeetingId}/audio`, { method: "DELETE" });
    if (res.ok) {
      document.getElementById("delete-audio-btn").style.display = "none";
      showStatusMessage("Audio recording deleted");
    }
  } catch (err) {
    console.error("Delete audio failed:", err);
  }
});

function showStatusMessage(msg) {
  const bar = document.getElementById("status-bar");
  const text = document.getElementById("status-text");
  if (bar && text) {
    text.textContent = msg;
    bar.style.display = "flex";
    setTimeout(() => { bar.style.display = "none"; }, 2500);
  }
}

// Search
let searchTimeout;
const searchInput = document.getElementById("search");
if (searchInput) {
  searchInput.addEventListener("input", (e) => {
    clearTimeout(searchTimeout);
    searchTimeout = setTimeout(() => loadMeetings(e.target.value.trim()), 300);
  });
}

// ── Settings panel ──────────────────────────────────────
let settingsOpen = false;

function showSettings() {
  settingsOpen = true;
  currentMeetingId = null;
  document.getElementById("empty-state").style.display = "none";
  document.getElementById("meeting-view").style.display = "none";
  document.getElementById("settings-view").style.display = "block";
  document.getElementById("settings-btn").classList.add("active");
  document.querySelectorAll(".meeting-item").forEach(el => el.classList.remove("active"));
  loadSettingsPanel();
}

function hideSettings() {
  settingsOpen = false;
  document.getElementById("settings-view").style.display = "none";
  document.getElementById("settings-btn").classList.remove("active");
}

async function loadSettingsPanel() {
  const settings = await fetchJSON("/api/settings");
  document.getElementById("s-auto-summarize").checked = settings.auto_summarize;
  document.getElementById("s-keep-audio").checked = settings.keep_audio;
  document.getElementById("s-diarization-enabled").checked = settings.diarization_enabled;

  // Show/hide HF token row based on diarization toggle
  const hfRow = document.getElementById("hf-token-row");
  if (hfRow) hfRow.style.display = settings.diarization_enabled ? "flex" : "none";

  // Load HF token status
  if (settings.diarization_enabled) {
    const tokenInfo = await fetchJSON("/api/hf-token");
    const tokenInput = document.getElementById("s-hf-token");
    if (tokenInfo.has_token) {
      tokenInput.placeholder = tokenInfo.masked;
    }
  }

  const speakers = await fetchJSON("/api/speakers");
  const list = document.getElementById("speaker-list");
  const entries = Object.entries(speakers);
  if (!entries.length) {
    list.innerHTML = '<div class="speaker-empty">No speakers recognized yet. They will appear here after your first meeting.</div>';
  } else {
    list.innerHTML = entries.map(([sid, name]) =>
      `<div class="speaker-row">
        <span class="speaker-id">${esc(sid)}</span>
        <input class="speaker-name-input" data-sid="${esc(sid)}" value="${esc(name)}" placeholder="Enter name...">
      </div>`
    ).join("");

    list.querySelectorAll(".speaker-name-input").forEach(input => {
      input.addEventListener("change", async () => {
        const sid = input.dataset.sid;
        const name = input.value.trim();
        if (name) {
          await fetch("/api/speakers", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ [sid]: name }),
          });
        }
      });
    });
  }
}

async function saveSetting(key, value) {
  await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ [key]: value }),
  });
}

document.getElementById("s-auto-summarize").addEventListener("change", e => saveSetting("auto_summarize", e.target.checked));
document.getElementById("s-keep-audio").addEventListener("change", e => saveSetting("keep_audio", e.target.checked));
document.getElementById("s-diarization-enabled").addEventListener("change", e => {
  saveSetting("diarization_enabled", e.target.checked);
  const hfRow = document.getElementById("hf-token-row");
  if (hfRow) hfRow.style.display = e.target.checked ? "flex" : "none";
});

// HF token save on blur
document.getElementById("s-hf-token").addEventListener("change", async (e) => {
  const token = e.target.value.trim();
  if (token) {
    await fetch("/api/hf-token", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token }),
    });
    e.target.value = "";
    e.target.placeholder = `${token.slice(0, 5)}...${token.slice(-4)}`;
  }
});

document.getElementById("settings-btn").addEventListener("click", () => {
  if (settingsOpen) {
    hideSettings();
    if (!currentMeetingId) {
      document.getElementById("empty-state").style.display = "flex";
    }
  } else {
    showSettings();
  }
});

// Legacy settings loader for backwards compat
async function loadSettings() {
  // Settings are now loaded via the panel; this is a no-op kept for init
}

// ── Record modal ────────────────────────────────────────

function showRecordModal() {
  document.getElementById("modal-overlay").style.display = "flex";
}

function hideRecordModal() {
  document.getElementById("modal-overlay").style.display = "none";
}

document.getElementById("modal-cancel").addEventListener("click", hideRecordModal);
document.getElementById("modal-overlay").addEventListener("click", (e) => {
  if (e.target === e.currentTarget) hideRecordModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && document.getElementById("modal-overlay").style.display !== "none") {
    hideRecordModal();
  }
});

document.querySelectorAll(".modal-option").forEach(btn => {
  btn.addEventListener("click", async () => {
    const mode = btn.dataset.mode;
    hideRecordModal();
    await fetch("/api/record", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
  });
});

// Record button in sidebar
const recordBtn = document.getElementById("record-btn");
if (recordBtn) {
  recordBtn.addEventListener("click", async () => {
    if (recordBtn.classList.contains("recording")) {
      await fetch("/api/stop", { method: "POST" });
    } else {
      showRecordModal();
    }
  });
}

// SSE
function connectSSE() {
  const es = new EventSource("/api/events");
  const statusBar = document.getElementById("status-bar");
  const statusText = document.getElementById("status-text");
  const recordBtn = document.getElementById("record-btn");

  es.addEventListener("pipeline", (e) => {
    const data = JSON.parse(e.data);
    if (data.stage === "done") {
      if (statusBar) statusBar.style.display = "none";
      loadMeetings();
      if (currentMeetingId) selectMeeting(currentMeetingId);
    } else if (data.stage === "summarize_stream") {
      // Ignore live tokens to prevent word-by-word printing
      return;
    } else {
      if (statusBar) statusBar.style.display = "flex";
      if (statusText) statusText.textContent = data.message || data.stage;
    }
  });

  es.addEventListener("summarize_start", () => {
    if (statusBar) statusBar.style.display = "flex";
    if (statusText) statusText.textContent = "Generating summary...";
  });

  es.addEventListener("summarize_done", (e) => {
    if (statusBar) statusBar.style.display = "none";
    const data = JSON.parse(e.data);
    if (data.meeting_id === currentMeetingId) {
      selectMeeting(currentMeetingId);
    }
    loadMeetings();
  });

  let recordingTimer = null;
  let recordingStartTime = null;

  function updateRecordingTime() {
    if (!recordingStartTime) return;
    const elapsed = Math.floor((Date.now() - recordingStartTime) / 1000);
    const m = Math.floor(elapsed / 60);
    const s = elapsed % 60;
    const timeStr = `${m}:${s.toString().padStart(2, "0")}`;
    if (statusText) statusText.textContent = `Recording · ${timeStr}`;
  }

  es.addEventListener("recording", (e) => {
    const data = JSON.parse(e.data);
    if (recordBtn) {
      if (data.active) {
        recordBtn.classList.add("recording");
        recordBtn.textContent = "Stop Recording";
      } else {
        recordBtn.classList.remove("recording");
        recordBtn.textContent = "Record Meeting";
      }
    }
    if (data.active) {
      recordingStartTime = Date.now();
      if (statusBar) statusBar.style.display = "flex";
      updateRecordingTime();
      recordingTimer = setInterval(updateRecordingTime, 1000);
    } else {
      recordingStartTime = null;
      if (recordingTimer) { clearInterval(recordingTimer); recordingTimer = null; }
      if (statusBar) statusBar.style.display = "none";
    }
  });

  es.addEventListener("error", () => {
    if (statusBar) statusBar.style.display = "none";
  });

  es.onerror = () => {
    setTimeout(connectSSE, 3000);
  };
}

// Theme Toggle
function initTheme() {
  const savedTheme = localStorage.getItem("theme") || "dark";
  document.body.classList.toggle("light-mode", savedTheme === "light");
}

const themeToggle = document.getElementById("theme-toggle");
if (themeToggle) {
  themeToggle.addEventListener("click", () => {
    const isLight = document.body.classList.toggle("light-mode");
    localStorage.setItem("theme", isLight ? "light" : "dark");
  });
}

// Init
initTheme();
loadMeetings(null, true);
loadSettings();
connectSSE();
