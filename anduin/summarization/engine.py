from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Callable

import requests

OLLAMA_URL = "http://localhost:11434/api/generate"

# ── Built-in summary templates ────────────────────────────────────────────────

BUILTIN_TEMPLATES = {
    "standard": {
        "name": "Meeting Summary",
        "prompt": "Summarize this meeting. Include who was present, what was discussed, any decisions made, and action items with owners if mentioned. Be concise.",
    },
}

DEFAULT_TEMPLATE = "standard"


MIN_WORDS_FOR_SUMMARY = 30  # Below this, transcript is too short to summarize


def summarize(
    segments: list[dict],
    model: str,
    template_id: str = "standard",
    custom_prompt: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> str:
    transcript = _format_transcript(segments)
    if not transcript.strip():
        return "No speech was detected in the recording, so no summary could be generated."

    # Guard against hallucinating summaries for trivial transcripts
    word_count = len(transcript.split())
    if word_count < MIN_WORDS_FOR_SUMMARY:
        print(f"[engine] transcript too short ({word_count} words), skipping summary", flush=True)
        return (
            "This recording was too short to generate a meaningful summary.\n\n"
            f"**Transcript** ({word_count} words):\n\n"
            + "\n".join(f"> {s['text']}" for s in segments if s.get("text", "").strip())
        )

    if custom_prompt:
        base = custom_prompt
    elif template_id in BUILTIN_TEMPLATES:
        base = BUILTIN_TEMPLATES[template_id]["prompt"]
    else:
        base = BUILTIN_TEMPLATES[DEFAULT_TEMPLATE]["prompt"]

    # Strip any legacy {transcript} placeholder, then always append
    base = base.replace("{transcript}", "").rstrip()
    prompt = f"{base}\n\nTranscript:\n{transcript}"

    return _call_ollama(model, prompt, progress)


def get_default_templates() -> list[dict]:
    """Return the default templates as user-editable dicts."""
    return [
        {"id": tid, "name": t["name"], "prompt": t["prompt"]}
        for tid, t in BUILTIN_TEMPLATES.items()
    ]


def ensure_default_templates(existing: list[dict]) -> list[dict]:
    """Seed default templates into the list if it's empty (first run)."""
    if existing:
        return existing
    return get_default_templates()


def _format_transcript(segments: list[dict]) -> str:
    return "\n".join(f"[{s['speaker']}]: {s['text']}" for s in segments)


def _fill_template(template: str, transcript: str) -> str:
    filled = template.replace("{{transcript}}", transcript)
    filled = re.sub(r"\{\{.*?\}\}", "[fill this in]", filled)
    return f"Fill in this meeting summary template using the transcript:\n\n{filled}"


def _call_ollama(model: str, prompt: str, progress: Callable[[str], None] | None) -> str:
    streaming = progress is not None

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": streaming,
        "keep_alive": 0,
    }

    try:
        response = requests.post(
            OLLAMA_URL,
            json=payload,
            stream=streaming,
            timeout=600,
        )
    except requests.ConnectionError:
        raise RuntimeError(
            "Cannot connect to Ollama. Make sure Ollama is running "
            "(open the Ollama app or run 'ollama serve' in a terminal)."
        )

    if response.status_code == 404:
        raise RuntimeError(
            f"Model '{model}' not found. Run 'ollama pull {model}' to download it."
        )
    response.raise_for_status()

    if not streaming:
        return response.json()["response"]

    output: list[str] = []
    for line in response.iter_lines():
        if line:
            chunk = json.loads(line)
            token = chunk.get("response", "")
            output.append(token)
            progress(token)
            if chunk.get("done"):
                break

    return "".join(output)
