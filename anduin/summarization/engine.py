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
        "name": "Standard",
        "prompt": """\
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
{transcript}""",
    },
    "brief": {
        "name": "Brief",
        "prompt": """\
Summarize this meeting in 3-5 bullet points. Be concise. No headers, just bullets.

Transcript:
{transcript}""",
    },
    "action_items": {
        "name": "Action Items Only",
        "prompt": """\
Extract only the action items and next steps from this meeting. Format each as:
- [ ] <task> — **<owner>** by <deadline or "TBD">

If no action items were discussed, say "No action items identified."

Transcript:
{transcript}""",
    },
    "narrative": {
        "name": "Narrative",
        "prompt": """\
Write a concise narrative summary of this meeting in 2-3 paragraphs. \
Use natural prose, not bullet points. Focus on what was discussed, \
what was decided, and what happens next.

Transcript:
{transcript}""",
    },
}

DEFAULT_TEMPLATE = "standard"


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

    if custom_prompt:
        # Custom template — inject transcript
        prompt = custom_prompt.replace("{transcript}", transcript)
    elif template_id in BUILTIN_TEMPLATES:
        prompt = BUILTIN_TEMPLATES[template_id]["prompt"].format(transcript=transcript)
    else:
        prompt = BUILTIN_TEMPLATES[DEFAULT_TEMPLATE]["prompt"].format(transcript=transcript)

    return _call_ollama(model, prompt, progress)


def get_templates(custom_templates: list[dict] | None = None) -> list[dict]:
    """Return all available templates (built-in + custom)."""
    templates = [
        {"id": tid, "name": t["name"], "builtin": True}
        for tid, t in BUILTIN_TEMPLATES.items()
    ]
    for ct in (custom_templates or []):
        templates.append({
            "id": ct.get("id", ""),
            "name": ct.get("name", "Custom"),
            "builtin": False,
        })
    return templates


def generate_title(segments: list[dict], model: str) -> str:
    """Generate a very short meeting title from transcript content."""
    transcript = _format_transcript(segments)
    if not transcript.strip():
        return ""

    prompt = (
        "Generate a very short title (max 6 words) for this meeting. "
        "Just the title, nothing else. No quotes. Examples: "
        '"Q3 Budget Review", "Backing Minds <> Norrsken", '
        '"Weekly Engineering Standup", "Product Launch Planning".\n\n'
        f"Transcript:\n{transcript}"
    )

    try:
        title = _call_ollama(model, prompt, None).strip().strip('"\'')
        # Truncate if the model got chatty
        if "\n" in title:
            title = title.split("\n")[0].strip()
        if len(title) > 60:
            title = title[:57] + "..."
        return title
    except Exception as e:
        print(f"[engine] title generation failed: {e}", flush=True)
        return ""


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
