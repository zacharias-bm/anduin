from __future__ import annotations
import json
import re
from pathlib import Path
from typing import Callable

import requests

OLLAMA_URL = "http://localhost:11434/api/generate"

HYBRID_PROMPT = """\
You are a meeting summarizer. Using the transcript below, produce a structured summary.

Always include these sections:

## Attendees
List each speaker (use their name if known).

## Decisions
Bullet list of decisions made.

## Action Items
Each item as: - [ ] <task> — **<owner>** by <deadline or "no deadline">

## Discussion Summary
Concise freeform summary of the main topics discussed.

Transcript:
{transcript}
"""


def summarize(
    segments: list[dict],
    model: str,
    mode: str = "hybrid",
    template: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> str:
    transcript = _format_transcript(segments)
    if not transcript.strip():
        return "No speech was detected in the recording, so no summary could be generated."

    if mode == "dynamic":
        prompt = f"Summarize this meeting transcript:\n\n{transcript}"
    elif mode == "template" and template:
        prompt = _fill_template(template, transcript)
    else:
        prompt = HYBRID_PROMPT.format(transcript=transcript)

    return _call_ollama(model, prompt, progress)


def _format_transcript(segments: list[dict]) -> str:
    return "\n".join(f"[{s['speaker']}]: {s['text']}" for s in segments)


def _fill_template(template: str, transcript: str) -> str:
    filled = template.replace("{{transcript}}", transcript)
    filled = re.sub(r"\{\{.*?\}\}", "[fill this in]", filled)
    return f"Fill in this meeting summary template using the transcript:\n\n{filled}"


def _call_ollama(model: str, prompt: str, progress: Callable[[str], None] | None) -> str:
    streaming = progress is not None

    # Full Metal + all CPU cores for fastest possible load and inference.
    # keep_alive=0 unloads the model immediately after generation, so
    # memory is only used while actively summarizing.
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": streaming,
        "keep_alive": 0,
    }

    response = requests.post(
        OLLAMA_URL,
        json=payload,
        stream=streaming,
        timeout=600,
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
