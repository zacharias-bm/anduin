# Anduin — Local Meeting Transcriber & Summarizer

## Project Overview

Anduin is a fully local, privacy-first meeting transcription and summarization tool, distributed as a native macOS app (.dmg). It records a single audio stream, transcribes and diarizes it post-meeting, auto-detects Swedish or English, and uses a local LLM to produce structured meeting summaries. No data ever leaves the machine. Users download the app and it works — no terminal, no manual pip installs.

---

## Constraints & Design Principles

- **macOS app** — distributed as `.dmg`, installs like any Mac app
- **Mac only** (Apple Silicon, M1+) for v1
- **No GPU** — all models run on CPU/ANE via Metal where possible
- **Post-processing only** — no real-time transcription or live diarization
- **Single audio stream** — one mic input, regardless of meeting type
- **Fully local** — no cloud APIs, no telemetry
- **Auto hardware detection** — model sizes selected based on detected chip and RAM
- **First-launch setup wizard** — handles model downloads, HuggingFace token entry, Ollama install, and BlackHole setup

---

## App Architecture

### Shell
**Menu bar app** using `rumps` — small tray icon, dropdown menu for primary controls. A separate window (via `rumps.Window` or lightweight `tkinter` panel) for settings, meeting history, and processing status.

### Python Packaging
**`briefcase`** (BeeWare) — packages the Python app + all dependencies into a `.app` bundle and `.dmg` installer.

```
Anduin.app/
└── Contents/
    ├── MacOS/          # briefcase launcher binary
    ├── Resources/
    │   └── app/        # all Python source + venv
    └── Info.plist
```

### External Dependencies (handled by setup wizard)
- **Ollama** — cannot be bundled; runs as a background service. Setup wizard detects it and prompts install if missing.
- **BlackHole** — optional, for digital meetings. Setup wizard guides install + Audio MIDI Setup configuration.
- **HuggingFace token** — required once for pyannote model download. Entered in setup wizard, stored in macOS Keychain. Never used again after models are downloaded.

---

## First-Launch Setup Wizard

Full-screen onboarding window, shown once (re-accessible from settings):

```
Step 1: Hardware detected
        "M2 Pro, 16GB RAM — recommended models selected"
        [Continue]

Step 2: Download transcription model
        "Downloading Whisper large-v3... [=====>    ] 1.5 GB / 3.1 GB"

Step 3: Speaker diarization model
        "Anduin uses pyannote.audio, which requires a free HuggingFace account."
        [Open HuggingFace — accept model license]
        [Paste token here: __________________]
        "Downloading diarization model... [==========] Done"
        Token stored securely in macOS Keychain. Not used again.

Step 4: Check Ollama
        "Ollama not found" → [Install Ollama]   (opens installer pkg)
        "Ollama found"     → [Download LLM: llama3.1:8b]  [========>  ]

Step 5: Digital meetings (optional)
        "For Zoom/Teams: install BlackHole audio driver"
        [Install BlackHole]   or   [Skip — in-person only]

Step 6: All set
        [Launch Anduin]
```

Models stored in `~/Library/Application Support/Anduin/models/`. Persist across app updates.

---

## Audio Input Strategy

### In-Person Meetings
- Record via room mic or laptop mic as a single mono/stereo stream
- Input device selectable from menu bar or settings

### Digital Meetings (Zoom, Teams, etc.)
- BlackHole merges system audio output + mic into one virtual device
- Anduin treats it identically to a room mic — no special code path
- Setup wizard walks through Audio MIDI Setup configuration

---

## Hardware-Aware Model Selection

Detected at startup via `platform.processor()` + `psutil`. User can override in settings.

| Hardware | Whisper Model | LLM Model |
|---|---|---|
| M1, 8GB RAM | `medium` | `llama3.2:3b` |
| M1, 16GB RAM | `large-v2` | `llama3.1:8b` |
| M2/M3, 16GB+ RAM | `large-v3` | `llama3.1:8b` |
| M2/M3, 32GB+ RAM | `large-v3` | `gemma3:27b` |

---

## Processing Pipeline

```
Audio File (.m4a / .wav / .mp3)
    │
    ▼
[VAD] — Silero VAD
Strips silence, segments speech chunks
    │
    ▼
[Diarization] — pyannote.audio (speaker-diarization-3.1)
Assigns speaker labels (Speaker_00, Speaker_01, ...)
    │
    ▼
[Transcription] — faster-whisper
Auto-detects Swedish / English (language=None)
Outputs timestamped word-level transcript per segment
    │
    ▼
[Merge] — align diarization + transcript
Produces structured JSON: [{speaker, start, end, text}, ...]
    │
    ▼
[Summarization] — Ollama (local LLM)
Mode: dynamic | template | hybrid
    │
    ▼
[Output]  ~/Library/Application Support/Anduin/meetings/<date>-<title>/
  ├── transcript.json
  ├── transcript.md
  └── summary.md
```

---

## UI: Menu Bar App

```
[Anduin icon in menu bar]
  ├── ● Record Meeting...         → title prompt, then starts recording
  ├── ○ Stop Recording            → stops, queues for processing
  ├── ─────────────────
  ├── Process Audio File...       → file picker for existing recordings
  ├── ─────────────────
  ├── Recent Meetings
  │     ├── 2025-05-14 Sprint Planning  → opens folder in Finder
  │     ├── 2025-05-12 Q2 Review
  │     └── Show All Meetings...
  ├── ─────────────────
  ├── Settings...
  └── Quit Anduin
```

### Settings Panel
- Input device selector
- Speaker name mapping (Speaker_00 → "Anna", etc.)
- Default summarization mode (dynamic / template / hybrid)
- Template selector + editor
- Model overrides
- Digital meeting setup (BlackHole guide)
- HuggingFace token management

### Processing Status Window
Non-blocking, shows progress while pipeline runs:
```
Processing: Sprint Planning
[===>      ] Transcribing...  2:14 / 6:30
```

---

## Component Breakdown

### `anduin/capture/recorder.py`
- `sounddevice` for audio capture
- Records to temp `.wav`, moves to meeting folder on stop

### `anduin/vad/silero.py`
- Silero VAD (CPU via `torch`)
- Segments audio before diarization + transcription

### `anduin/diarization/pyannote.py`
- `pyannote.audio` speaker-diarization-3.1
- HF token loaded from macOS Keychain at runtime
- Output: `[{speaker: "Speaker_00", start: 0.0, end: 4.2}, ...]`

### `anduin/transcription/whisper.py`
- `faster-whisper`, `compute_type="int8"` for CPU efficiency
- `language=None` for Swedish/English auto-detect
- Word-level timestamps

### `anduin/merge/aligner.py`
- Aligns diarization segments with whisper word timestamps
- Produces `transcript.json` and `transcript.md`

### `anduin/summarization/engine.py`
- Calls Ollama REST API (`localhost:11434`)
- Three modes:
  - **Dynamic**: freeform, LLM decides structure
  - **Template**: `.md` template with `{{placeholder}}` fields, LLM fills
  - **Hybrid** (default): forced fields (attendees, decisions, action items with owner + deadline) + freeform discussion summary
- Templates in `anduin/summarization/templates/`

### `anduin/hardware/detect.py`
- Detects chip generation + RAM at startup
- Returns recommended model config, user-overridable

### `anduin/setup/wizard.py`
- First-launch onboarding flow
- Model downloads with progress callbacks
- HF token entry + Keychain storage
- Ollama detection + LLM pull
- BlackHole detection + setup guide

### `anduin/storage/store.py`
- Manages `~/Library/Application Support/Anduin/meetings/`
- SQLite with FTS5 for full-text search across transcripts
- Speaker name mapping persistence

### `anduin/ui/menubar.py`
- `rumps` menu bar app — main entry point

---

## Project File Structure

```
anduin/
├── anduin/
│   ├── __init__.py
│   ├── capture/
│   │   └── recorder.py
│   ├── vad/
│   │   └── silero.py
│   ├── diarization/
│   │   └── pyannote.py
│   ├── transcription/
│   │   └── whisper.py
│   ├── merge/
│   │   └── aligner.py
│   ├── summarization/
│   │   ├── engine.py
│   │   └── templates/
│   │       ├── default.md
│   │       └── standup.md
│   ├── storage/
│   │   └── store.py
│   ├── hardware/
│   │   └── detect.py
│   ├── setup/
│   │   └── wizard.py
│   └── ui/
│       └── menubar.py
├── pyproject.toml              # briefcase config
├── requirements.txt
└── README.md
```

All output to `~/Library/Application Support/Anduin/` — nothing written to the app bundle.

---

## Dependencies

```
faster-whisper          # transcription
pyannote.audio          # speaker diarization
silero-vad              # voice activity detection
sounddevice             # audio capture
torch                   # CPU only — VAD + pyannote
torchaudio
psutil                  # hardware detection
rumps                   # menu bar UI
keyring                 # macOS Keychain for HF token
pyyaml                  # config
imageio-ffmpeg          # bundled ffmpeg for video → audio extraction
requests                # Ollama API + model download progress
briefcase               # packaging (dev dependency)
```

---

## Build & Distribution

```bash
# Dev
pip install -e ".[dev]"
python -m anduin.ui.menubar

# Package
briefcase build macOS
briefcase run macOS

# Distribute
briefcase package macOS         # produces Anduin.dmg
```

Code signing via Apple Developer ID required for Gatekeeper.

---

## macOS Permissions Required

Declared in `Info.plist`, prompted on first use:
- **Microphone** (`NSMicrophoneUsageDescription`)
- **Downloads / Documents** — for processing user-provided audio files

---

## Out of Scope (v1)

- Real-time / live transcription
- Windows / Linux support
- Multi-mic / per-person audio streams
- Speaker voice enrollment (name → voice matching)
- Cloud sync or sharing
- App Store distribution (sandboxing conflicts with Ollama subprocess)

---

## Decisions

- **ffmpeg**: Include `imageio-ffmpeg` from v1. Bundled cleanly with briefcase, enables processing recorded Zoom/Teams `.mp4` files by stripping audio before the pipeline. "Process Audio File..." picker accepts video formats in addition to `.m4a / .wav / .mp3`.
- **Ollama lifecycle**: Anduin auto-starts Ollama as a managed subprocess on launch if it isn't already running. No error surfaced to the user — transparent background start. Anduin does not stop Ollama on quit (it may be in use elsewhere).
- **Speaker naming UX**: Prompt the user to name new speakers immediately after a meeting finishes processing, while context is fresh. Names persist to settings and apply retroactively to all stored meetings for those speaker IDs.
