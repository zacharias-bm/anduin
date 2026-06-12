const SPEAKER_COLORS = [
  "#3E5C82", "#5E7A8C", "#7A8C5E", "#8C6E5E",
  "#6E5E8C", "#7FA0C2", "#8C7A5E", "#5E8C7A",
];

let currentMeetingId = null;
let speakerColorMap = {};
let currentMeetingRaw = null; // store raw meeting data for copy

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

// ── Custom select component ────────────────────────────

function initCustomSelect(el, options, selectedValue, onChange) {
  const selected = options.find(o => String(o.value) === String(selectedValue)) || options[0];
  el.dataset.value = selected ? selected.value : "";

  el.innerHTML = `
    <div class="custom-select-trigger">${selected ? esc(selected.label) : ""}</div>
    <div class="custom-select-menu">
      ${options.map(o => `
        <div class="custom-select-option${String(o.value) === String(selectedValue) ? " selected" : ""}"
             data-value="${esc(String(o.value))}">${esc(o.label)}</div>
      `).join("")}
    </div>
  `;

  const trigger = el.querySelector(".custom-select-trigger");
  const menu = el.querySelector(".custom-select-menu");

  trigger.addEventListener("click", (e) => {
    e.stopPropagation();
    // Close any other open selects
    document.querySelectorAll(".custom-select.open").forEach(s => {
      if (s !== el) s.classList.remove("open");
    });
    el.classList.toggle("open");
  });

  menu.querySelectorAll(".custom-select-option").forEach(opt => {
    opt.addEventListener("click", (e) => {
      e.stopPropagation();
      const val = opt.dataset.value;
      el.dataset.value = val;
      trigger.textContent = opt.textContent;
      menu.querySelectorAll(".custom-select-option").forEach(o => o.classList.remove("selected"));
      opt.classList.add("selected");
      el.classList.remove("open");
      if (onChange) onChange(val);
    });
  });
}

// Close all custom selects when clicking outside
document.addEventListener("click", () => {
  document.querySelectorAll(".custom-select.open").forEach(s => s.classList.remove("open"));
});

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
    list.innerHTML = '<div class="meeting-empty">No meetings yet</div>';
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
  currentMeetingRaw = m;
  document.getElementById("empty-state").style.display = "none";
  document.getElementById("meeting-view").style.display = "block";

  const eyebrowParts = [formatEyebrow(m.date)];
  if (m.duration_secs) eyebrowParts.push(formatDurationLong(m.duration_secs));
  document.getElementById("meeting-eyebrow").textContent = eyebrowParts.filter(Boolean).join(" · ");

  const titleEl = document.getElementById("meeting-title");
  titleEl.textContent = m.title;
  titleEl.dataset.id = m.id;

  const summaryBody = document.getElementById("summary-body");
  const summarizeBtn = document.getElementById("summarize-btn");
  if (m.summary) {
    summaryBody.innerHTML = renderMarkdown(m.summary);
    summarizeBtn.textContent = "Re-summarize";
  } else {
    summaryBody.innerHTML = '<span class="no-summary">No summary generated yet.</span>';
    summarizeBtn.textContent = "Summarize";
  }
  summarizeBtn.disabled = false;

  // Load templates into picker — show the template used for this summary
  loadTemplateSelect(m.template_id);

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
    container.innerHTML = '<div style="color:var(--color-slate)">No transcript available.</div>';
    return;
  }

  // Detect if diarization was used (all speakers = "Speaker" means no diarization)
  const speakers = new Set(segments.map(s => s.speaker));
  const hasDiarization = !(speakers.size === 1 && speakers.has("Speaker"));

  if (hasDiarization) {
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
  } else {
    // No diarization — just timestamps + text
    container.innerHTML = segments
      .map((s) => `<div class="segment segment-no-speaker">
        <div class="segment-time-inline">${formatTimestamp(s.start)}</div>
        <div class="segment-text">${esc(s.text || "")}</div>
      </div>`)
      .join("");
  }
}

function renderMarkdown(text) {
  return esc(text)
    .replace(/^#### (.+)$/gm, "<h5>$1</h5>")
    .replace(/^### (.+)$/gm, "<h4>$1</h4>")
    .replace(/^## (.+)$/gm, "<h3>$1</h3>")
    .replace(/^# (.+)$/gm, "<h2>$1</h2>")
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/^- (.+)$/gm, "<li>$1</li>")
    .replace(/(<li>.*<\/li>)/gs, "<ul>$1</ul>")
    .replace(/\n{2,}/g, '<span class="p-break"></span>')
    .replace(/\n/g, "<br>");
}

function esc(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

// Copy buttons
document.getElementById("copy-summary-btn").addEventListener("click", () => {
  if (currentMeetingRaw && currentMeetingRaw.summary) {
    navigator.clipboard.writeText(currentMeetingRaw.summary);
    showStatusMessage("Summary copied");
  }
});

document.getElementById("copy-transcript-btn").addEventListener("click", () => {
  if (currentMeetingRaw && currentMeetingRaw.transcript) {
    const text = currentMeetingRaw.transcript
      .map(s => `[${s.speaker}]: ${s.text}`)
      .join("\n");
    navigator.clipboard.writeText(text);
    showStatusMessage("Transcript copied");
  }
});

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

// Template picker
async function loadTemplateSelect(meetingTemplateId) {
  const data = await fetchJSON("/api/templates");
  const el = document.getElementById("template-select");
  const options = data.templates.map(t => ({ value: t.id, label: t.name }));
  const defaultId = data.default_template || (data.templates.length ? data.templates[0].id : "standard");
  const selectedId = meetingTemplateId || defaultId;
  initCustomSelect(el, options, selectedId, null);
}

// Summarize / Re-summarize button
const summarizeBtn = document.getElementById("summarize-btn");
if (summarizeBtn) {
  summarizeBtn.addEventListener("click", async () => {
    if (!currentMeetingId) return;
    summarizeBtn.disabled = true;
    summarizeBtn.textContent = "Summarizing...";
    const templateId = document.getElementById("template-select").dataset.value;
    await fetch(`/api/meetings/${currentMeetingId}/summarize`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ template_id: templateId }),
    });
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
  if (!confirm("Delete this meeting? This cannot be undone.")) return;
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
  document.getElementById("settings-inner").style.display = "block";
  document.getElementById("dictionary-subpage").style.display = "none";
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
  // Theme
  document.getElementById("s-dark-mode").checked = localStorage.getItem("theme") === "dark";

  const settings = await fetchJSON("/api/settings");
  document.getElementById("s-auto-summarize").checked = settings.auto_summarize;
  document.getElementById("s-keep-audio").checked = settings.keep_audio;
  // Load dictionary
  const dictData = await fetchJSON("/api/dictionary");
  document.getElementById("s-dictionary").value = (dictData.words || []).join("\n");

  // Load custom templates
  loadCustomTemplatesEditor();

  // Load audio devices
  const deviceData = await fetchJSON("/api/devices");
  const micOptions = [{ value: "", label: "System default" },
    ...deviceData.inputs.map(d => ({ value: String(d.index), label: d.name }))];
  initCustomSelect(
    document.getElementById("s-mic-device"),
    micOptions,
    deviceData.selected_input != null ? String(deviceData.selected_input) : "",
    val => saveSetting("device_inperson", val === "" ? null : parseInt(val))
  );

  const speakerOptions = [{ value: "", label: "System default" },
    ...deviceData.outputs.map(d => ({ value: String(d.index), label: d.name }))];
  initCustomSelect(
    document.getElementById("s-speaker-device"),
    speakerOptions,
    deviceData.selected_output != null ? String(deviceData.selected_output) : "",
    val => saveSetting("device_speaker", val === "" ? null : parseInt(val))
  );

  // Version info
  try {
    const status = await fetchJSON("/api/status");
    document.getElementById("s-version-desc").textContent = `v${status.version || "0.1.0"}`;
  } catch (e) {
    document.getElementById("s-version-desc").textContent = "";
  }
}

async function saveSetting(key, value) {
  await fetch("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ [key]: value }),
  });
}

document.getElementById("s-dark-mode").addEventListener("change", e => {
  const dark = e.target.checked;
  document.body.classList.toggle("dark-mode", dark);
  localStorage.setItem("theme", dark ? "dark" : "light");
});

document.getElementById("s-auto-summarize").addEventListener("change", e => saveSetting("auto_summarize", e.target.checked));
document.getElementById("s-keep-audio").addEventListener("change", e => saveSetting("keep_audio", e.target.checked));

// ── Custom template editor ─────────────────────────────

let _customTemplates = [];
let _defaultTemplateId = null;

async function loadCustomTemplatesEditor() {
  const data = await fetchJSON("/api/templates");
  _customTemplates = data.templates || [];
  _defaultTemplateId = data.default_template || (_customTemplates.length ? _customTemplates[0].id : null);
  renderCustomTemplates();
}

function renderCustomTemplates() {
  const list = document.getElementById("custom-templates-list");
  list.innerHTML = _customTemplates.map((t, i) => {
    const isDef = t.id === _defaultTemplateId;
    const canDelete = _customTemplates.length > 1;
    return `<div class="custom-template-card" data-index="${i}">
      <div class="ct-header">
        <span class="ct-chevron">&#9654;</span>
        <span class="ct-name-wrap" onclick="event.stopPropagation()">
          <input type="text" value="${esc(t.name)}" placeholder="Template name" class="ct-name">
        </span>
        <button class="ct-default-btn ${isDef ? "is-default" : ""}" data-index="${i}" title="${isDef ? "Default template" : "Set as default"}" onclick="event.stopPropagation()">&#9733;</button>
        ${canDelete ? `<button class="ct-delete-btn" data-index="${i}" title="Delete" onclick="event.stopPropagation()">&times;</button>` : ""}
      </div>
      <div class="ct-body">
        <textarea class="ct-prompt" rows="8" placeholder="Instructions for how the meeting should be summarized...">${esc(t.prompt || "")}</textarea>
        <div class="ct-body-actions">
          <button class="template-save-btn" data-index="${i}">Save</button>
        </div>
      </div>
    </div>`;
  }).join("");

  list.querySelectorAll(".ct-header").forEach(hdr => {
    hdr.addEventListener("click", (e) => {
      if (e.target.tagName === "INPUT") return;
      hdr.closest(".custom-template-card").classList.toggle("expanded");
    });
  });

  function autoSizeInput(input) {
    const measure = document.createElement("span");
    measure.style.cssText = "visibility:hidden;position:absolute;white-space:pre;font:inherit;font-size:13px;font-weight:600;padding:0";
    measure.textContent = input.value || input.placeholder;
    document.body.appendChild(measure);
    input.style.width = (measure.offsetWidth + 4) + "px";
    measure.remove();
  }
  list.querySelectorAll(".ct-name").forEach(inp => {
    autoSizeInput(inp);
    inp.addEventListener("input", () => autoSizeInput(inp));
  });
  list.querySelectorAll(".template-save-btn").forEach(btn => {
    btn.addEventListener("click", () => saveCustomTemplate(parseInt(btn.dataset.index)));
  });
  list.querySelectorAll(".ct-delete-btn").forEach(btn => {
    btn.addEventListener("click", () => deleteCustomTemplate(parseInt(btn.dataset.index)));
  });
  list.querySelectorAll(".ct-default-btn").forEach(btn => {
    btn.addEventListener("click", () => setDefaultTemplate(parseInt(btn.dataset.index)));
  });
}

async function setDefaultTemplate(index) {
  _defaultTemplateId = _customTemplates[index].id;
  await fetch("/api/templates/default", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ template_id: _defaultTemplateId }),
  });
  renderCustomTemplates();
  showStatusMessage("Default template set");
}

async function saveCustomTemplate(index) {
  const card = document.querySelectorAll(".custom-template-card")[index];
  const name = card.querySelector(".ct-name").value.trim();
  const prompt = card.querySelector(".ct-prompt").value.trim();
  if (!name || !prompt) return;

  _customTemplates[index] = {
    id: _customTemplates[index].id || "custom_" + Date.now(),
    name,
    prompt,
  };
  await saveAllCustomTemplates();
  showStatusMessage("Template saved");
}

async function deleteCustomTemplate(index) {
  const removedId = _customTemplates[index].id;
  _customTemplates.splice(index, 1);
  if (removedId === _defaultTemplateId && _customTemplates.length) {
    _defaultTemplateId = _customTemplates[0].id;
  }
  await saveAllCustomTemplates();
  renderCustomTemplates();
  showStatusMessage("Template deleted");
}

async function saveAllCustomTemplates() {
  await fetch("/api/templates", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ templates: _customTemplates }),
  });
}

document.getElementById("add-template-btn").addEventListener("click", () => {
  _customTemplates.push({
    id: "custom_" + Date.now(),
    name: "",
    prompt: "",
  });
  renderCustomTemplates();
  const cards = document.querySelectorAll(".custom-template-card");
  if (cards.length) {
    const last = cards[cards.length - 1];
    last.classList.add("expanded");
    last.querySelector(".ct-name").focus();
  }
});

document.getElementById("reset-templates-btn").addEventListener("click", async () => {
  const res = await fetchJSON("/api/templates/reset-defaults", {
    method: "POST",
  });
  if (res.templates) {
    _customTemplates = res.templates;
    _defaultTemplateId = _customTemplates.length ? _customTemplates[0].id : null;
    renderCustomTemplates();
    showStatusMessage("Default templates restored");
  }
});

document.getElementById("save-dictionary-btn").addEventListener("click", async () => {
  const text = document.getElementById("s-dictionary").value;
  const words = text.split("\n").map(w => w.trim()).filter(Boolean);
  await fetch("/api/dictionary", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ words }),
  });
  showStatusMessage("Dictionary saved");
});

// ── Dictionary sub-page navigation ─────────────────────
document.getElementById("open-dictionary-btn").addEventListener("click", () => {
  document.getElementById("settings-inner").style.display = "none";
  document.getElementById("dictionary-subpage").style.display = "block";
});

document.getElementById("dictionary-back-btn").addEventListener("click", () => {
  document.getElementById("dictionary-subpage").style.display = "none";
  document.getElementById("settings-inner").style.display = "block";
});

document.getElementById("s-check-updates").addEventListener("click", async () => {
  const btn = document.getElementById("s-check-updates");
  btn.disabled = true;
  btn.textContent = "Checking...";
  try {
    await fetch("/api/check-update", { method: "POST" });
  } catch (e) {
    showStatusMessage("Update check failed");
    btn.disabled = false;
    btn.textContent = "Check for updates";
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
      const updateBtn = document.getElementById("s-check-updates");
      if (updateBtn) { updateBtn.disabled = false; updateBtn.textContent = "Check for updates"; }
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
      // Don't hide status bar — pipeline events will take over
    }
  });

  // App-level errors from pipeline/summarization
  es.addEventListener("app_error", (e) => {
    const data = JSON.parse(e.data);
    if (statusBar) statusBar.style.display = "flex";
    if (statusText) statusText.textContent = data.message || "An error occurred";
    // Auto-hide after 6 seconds
    setTimeout(() => { if (statusBar) statusBar.style.display = "none"; }, 6000);
    // Re-enable summarize button
    const btn = document.getElementById("summarize-btn");
    if (btn) { btn.disabled = false; btn.textContent = "Summarize"; }
    const updateBtn = document.getElementById("s-check-updates");
    if (updateBtn) { updateBtn.disabled = false; updateBtn.textContent = "Check for updates"; }
  });

  es.onerror = () => {
    setTimeout(connectSSE, 3000);
  };
}

// Theme
function initTheme() {
  const dark = localStorage.getItem("theme") === "dark";
  document.body.classList.toggle("dark-mode", dark);
}

// Init
initTheme();
loadMeetings(null, true);
loadSettings();
connectSSE();
