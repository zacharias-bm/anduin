from __future__ import annotations
"""Post-launch UI helpers — all use osascript so they don't conflict with AppKit/rumps."""
import subprocess


# ── Speaker naming ────────────────────────────────────────────────────────────

def prompt_speaker_names(new_speaker_ids: list[str]) -> dict[str, str]:
    """Ask the user to name each new speaker via native osascript dialogs."""
    result: dict[str, str] = {}
    for sid in new_speaker_ids:
        script = (
            f'display dialog "Who is {sid}? (leave blank to skip)" '
            f'default answer "" buttons {{"Skip", "Save"}} default button "Save"'
        )
        r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
        if r.returncode != 0:
            continue
        for part in r.stdout.strip().split(", "):
            if part.startswith("text returned:"):
                name = part[len("text returned:"):].strip()
                if name:
                    result[sid] = name
    return result


# ── Settings ──────────────────────────────────────────────────────────────────

def open_settings():
    import threading
    threading.Thread(target=_run_settings, daemon=True).start()


def _run_settings():
    from anduin.capture.devices import (
        DIGITAL_KEY, INPERSON_KEY, find_digital_device,
        list_input_devices, system_default_input,
    )
    from anduin.setup import models
    from anduin.storage import store
    from anduin.storage.store import get_config, set_config

    # choose from list supports many items (no 3-button limit)
    section = _ask_list(
        "Anduin Settings — choose a section:",
        ["Audio Devices", "Speaker Names", "HuggingFace Token", "Ollama Management", "Audio Storage"],
    )
    if not section:
        return

    if section == "Audio Devices":
        devices = list_input_devices()
        names = [f"{d['index']}: {d['name']}" for d in devices]
        if not names:
            _alert("No input devices found.")
            return

        cur_inp = get_config(INPERSON_KEY)
        cur_inp_label = next((n for n in names if n.startswith(f"{cur_inp}:")), names[0])
        new_inp = _ask_list("In-Person device (mic only):", names, default=cur_inp_label)
        if new_inp:
            set_config(INPERSON_KEY, int(new_inp.split(":")[0]))

        cur_dig = get_config(DIGITAL_KEY)
        cur_dig_label = next((n for n in names if n.startswith(f"{cur_dig}:")), names[0])
        new_dig = _ask_list("Digital device (mic + BlackHole):", names, default=cur_dig_label)
        if new_dig:
            set_config(DIGITAL_KEY, int(new_dig.split(":")[0]))

        _alert("Audio device settings saved.")

    elif section == "Speaker Names":
        known = store.get_speaker_names()
        if not known:
            _alert("No speakers have been named yet.")
            return
        for sid, current_name in known.items():
            new_name = _ask_text(f"Name for {sid}:", default=current_name)
            if new_name is not None:
                store.set_speaker_name(sid, new_name)

    elif section == "HuggingFace Token":
        existing = models.get_hf_token() or ""
        new_token = _ask_text("HuggingFace token (Read access):", default=existing)
        if new_token and new_token != existing:
            import keyring
            keyring.set_password("Anduin", "huggingface_token", new_token)
            _alert("Token updated.")

    elif section == "Ollama Management":
        current = get_config("manage_ollama", True)
        label = "ON — Anduin starts/stops Ollama automatically" if current else "OFF — you manage Ollama yourself"
        choice = _ask_choice(
            f"Auto-manage Ollama?\n\nCurrently: {label}",
            buttons=["Cancel", "Turn Off", "Turn On"] if current else ["Cancel", "Turn On", "Turn Off"],
            default="Turn Off" if current else "Turn On",
        )
        if choice == "Turn On":
            set_config("manage_ollama", True)
            _alert("Ollama will be started/stopped automatically with Anduin.")
        elif choice == "Turn Off":
            set_config("manage_ollama", False)
            _alert("Ollama will not be managed by Anduin. Start and stop it yourself.")

    elif section == "Audio Storage":
        current = get_config("keep_audio", False)
        label = "ON — .wav files are kept" if current else "OFF — .wav files are deleted after processing"
        choice = _ask_choice(
            f"Keep audio recordings?\n\nCurrently: {label}\n\nWhen off, the .wav file is deleted after transcription completes. Transcripts and summaries are always kept.",
            buttons=["Cancel", "Keep Audio", "Delete Audio"] if not current else ["Cancel", "Delete Audio", "Keep Audio"],
            default="Keep Audio" if not current else "Delete Audio",
        )
        if choice == "Keep Audio":
            set_config("keep_audio", True)
            _alert("Audio recordings will be kept after processing.")
        elif choice == "Delete Audio":
            set_config("keep_audio", False)
            _alert("Audio recordings will be deleted after processing.")


# ── Notification / alert ──────────────────────────────────────────────────────

def _notify(message: str):
    """Show a visible alert dialog (display notification needs permissions we don't have)."""
    _alert(message)


def _alert(message: str):
    safe = message.replace('"', "'")
    subprocess.run(
        ["osascript", "-e", f'display alert "Anduin" message "{safe}"'],
        capture_output=True,
    )


# ── osascript primitives ──────────────────────────────────────────────────────

def _ask_text(prompt: str, default: str = "") -> str | None:
    safe_p = prompt.replace('"', '\\"')
    safe_d = default.replace('"', '\\"')
    script = (
        f'display dialog "{safe_p}" default answer "{safe_d}" '
        f'buttons {{"Cancel", "OK"}} default button "OK"'
    )
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    for part in r.stdout.strip().split(", "):
        if part.startswith("text returned:"):
            return part[len("text returned:"):].strip() or default
    return default


def _ask_choice(prompt: str, buttons: list[str], default: str) -> str | None:
    """Button dialog — max 3 buttons. Use _ask_list for more options."""
    safe_p = prompt.replace('"', '\\"')
    btn_list = "{" + ", ".join(f'"{b}"' for b in buttons[:3]) + "}"
    script = (
        f'display dialog "{safe_p}" buttons {btn_list} default button "{default}"'
    )
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    out = r.stdout.strip()
    if out.startswith("button returned:"):
        return out[len("button returned:"):].strip()
    return None


def _ask_list(prompt: str, items: list[str], default: str = "") -> str | None:
    """choose from list — no button limit, ideal for menus."""
    safe_p = prompt.replace('"', '\\"')
    item_list = "{" + ", ".join(f'"{i.replace(chr(34), chr(39))}"' for i in items) + "}"
    default_clause = f' default items {{"{default.replace(chr(34), chr(39))}"}}'  if default else ""
    script = f'choose from list {item_list} with prompt "{safe_p}"{default_clause}'
    r = subprocess.run(["osascript", "-e", script], capture_output=True, text=True)
    if r.returncode != 0:
        return None
    out = r.stdout.strip()
    return out if out and out != "false" else None
