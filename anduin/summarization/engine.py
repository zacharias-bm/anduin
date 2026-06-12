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


def clean_transcript(
    segments: list[dict],
    model: str,
    dictionary: list[str] | None = None,
) -> list[dict]:
    """LLM post-processing pass on raw ASR output.

    Fixes proper nouns (using the dictionary), removes filler words,
    corrects sentence boundaries, and cleans grammar — without changing
    meaning or removing content.  Returns segments with updated text.
    """
    transcript = _format_transcript(segments)
    if not transcript.strip():
        return segments

    word_count = len(transcript.split())
    if word_count < 10:
        return segments

    dict_hint = ""
    if dictionary:
        dict_hint = (
            "The following names and terms may appear in the transcript "
            "— use them to correct misspellings and misheard words: "
            + ", ".join(dictionary) + "\n\n"
        )

    prompt = (
        "You are a transcript editor. Clean up this raw speech-to-text transcript.\n\n"
        "Rules:\n"
        "- Fix misheard proper nouns, names, and technical terms\n"
        "- Remove filler words (um, uh, you know, like, I mean, sort of, kind of) "
        "UNLESS they carry meaning\n"
        "- Fix broken sentence boundaries and punctuation\n"
        "- Correct obvious grammar errors from speech-to-text\n"
        "- Do NOT change the meaning, remove content, or add information\n"
        "- Do NOT summarize — output the full cleaned transcript\n"
        "- Keep the same [Speaker]: format for each line\n"
        "- Output ONLY the cleaned transcript, nothing else\n\n"
        + dict_hint
        + "Transcript:\n" + transcript
    )

    try:
        cleaned = _call_ollama(model, prompt, progress=None)
    except Exception as e:
        print(f"[engine] transcript cleanup failed, using raw: {e}", flush=True)
        return segments

    # Parse the cleaned text back into segments, preserving timestamps
    return _merge_cleaned_text(segments, cleaned)


def _merge_cleaned_text(original_segments: list[dict], cleaned_text: str) -> list[dict]:
    """Map cleaned text lines back onto the original segments' timestamps.

    The LLM may merge or split lines. We do a best-effort match:
    one cleaned line per original segment, falling back to the original
    if the LLM changed the structure too much.
    """
    if not cleaned_text or not cleaned_text.strip():
        print("[engine] LLM returned empty cleanup, keeping originals", flush=True)
        return list(original_segments)

    cleaned_lines = []
    for line in cleaned_text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        # Strip the [Speaker]: prefix if present
        if line.startswith("[") and "]: " in line:
            text = line.split("]: ", 1)[1].strip()
        elif line.startswith("**") and "**" in line[2:]:
            # Handle **Speaker** format
            text = line.split("**", 2)[-1].strip()
            if text.startswith(":"):
                text = text[1:].strip()
        else:
            text = line
        if text:
            cleaned_lines.append(text)

    if not cleaned_lines:
        print("[engine] LLM cleanup produced no usable lines, keeping originals", flush=True)
        return list(original_segments)

    # If the LLM wildly changed the line count, the mapping is unreliable
    ratio = len(cleaned_lines) / len(original_segments) if original_segments else 0
    if ratio < 0.3 or ratio > 3.0:
        print(
            f"[engine] LLM cleanup line count mismatch "
            f"({len(cleaned_lines)} vs {len(original_segments)}), keeping originals",
            flush=True,
        )
        return list(original_segments)

    result = []
    for i, seg in enumerate(original_segments):
        new_seg = dict(seg)
        if i < len(cleaned_lines) and cleaned_lines[i].strip():
            new_seg["text"] = cleaned_lines[i]
        result.append(new_seg)

    # If the LLM produced extra lines, append them to the last segment
    if len(cleaned_lines) > len(original_segments) and result:
        extra = " ".join(cleaned_lines[len(original_segments):])
        result[-1]["text"] += " " + extra

    return result


def summarize_chunk(
    segments: list[dict],
    model: str,
) -> str:
    """Summarize a portion of transcript — used during streaming recording."""
    transcript = _format_transcript(segments)
    if not transcript.strip():
        return ""

    word_count = len(transcript.split())
    if word_count < MIN_WORDS_FOR_SUMMARY:
        return ""

    prompt = (
        "Summarize this meeting transcript. "
        "Include key points discussed, any decisions made, and action items. "
        "Be concise.\n\n"
        f"Transcript:\n{transcript}"
    )
    return _call_ollama(model, prompt, progress=None)


def synthesize_summaries(
    chunk_summaries: list[str],
    model: str,
    custom_prompt: str | None = None,
) -> str:
    """Synthesize multiple partial summaries into a final meeting summary."""
    parts = [s for s in chunk_summaries if s.strip()]
    if not parts:
        return "No speech was detected in the recording, so no summary could be generated."
    if len(parts) == 1:
        return parts[0]

    combined = "\n\n---\n\n".join(parts)

    if custom_prompt:
        base = custom_prompt.replace("{transcript}", "").rstrip()
    else:
        base = BUILTIN_TEMPLATES[DEFAULT_TEMPLATE]["prompt"]

    prompt = (
        f"{base}\n\n"
        "Below are summaries from different portions of this meeting. "
        "Synthesize them into one coherent summary. Combine related topics, "
        "remove redundancy, and present a unified view.\n\n"
        f"{combined}"
    )
    return _call_ollama(model, prompt, progress=None)


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
