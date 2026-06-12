from __future__ import annotations
"""Chunked recording session.

Orchestrates audio capture, background transcription, and streaming
summarization so that:
  1. Audio is saved to disk in chunks (crash-safe, constant RAM).
  2. Transcription runs in the background while recording continues.
  3. Each chunk is summarised independently (map step).
  4. On stop, only the final partial chunk needs processing — then a
     fast synthesis (reduce step) produces the full meeting summary.
"""

import json
import queue
import shutil
import threading
from pathlib import Path
from typing import Callable

import soundfile as sf

from anduin.capture.recorder import SAMPLE_RATE, Recorder
from anduin.hardware.detect import detect as detect_hardware
from anduin.merge.aligner import align, write_transcript
from anduin.storage import store
from anduin.storage.store import (
    get_config,
    get_speaker_names,
    meeting_dir,
    save_summary,
)

CHUNK_INTERVAL = 600  # 10 minutes

ProgressCallback = Callable[[str, str], None]  # (stage, message)


class ChunkInfo:
    """Metadata for a single recording chunk."""

    __slots__ = ("index", "path", "offset", "duration", "transcript", "summary")

    def __init__(self, index: int, path: Path, offset: float):
        self.index = index
        self.path = path
        self.offset = offset
        self.duration: float = 0.0
        self.transcript: list[dict] | None = None
        self.summary: str | None = None


class RecordingSession:
    """Manages a chunked recording with background processing.

    Usage::

        session = RecordingSession(recorder, on_progress=callback)
        session.start()
        # ... recording happens ...
        meeting_dir = session.finish("My Meeting")
    """

    def __init__(
        self,
        recorder: Recorder,
        on_progress: ProgressCallback | None = None,
    ):
        self._recorder = recorder
        self._on_progress = on_progress

        self._chunk_dir = store.APP_DIR / "_active_recording"
        self._chunks: list[ChunkInfo] = []
        self._time_offset: float = 0.0

        # Background processing
        self._work_queue: queue.Queue[ChunkInfo | None] = queue.Queue()
        self._worker_thread: threading.Thread | None = None
        self._flush_thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._flush_lock = threading.Lock()

        # Resolved once at start
        self._hw: dict = {}
        self._whisper_model: str = ""
        self._llm_model: str = ""
        self._dictionary: list[str] = []

    # ── Public API ────────────────────────────────────────────────────────

    def start(self):
        """Prepare chunk directory and start background workers."""
        # Clean any leftovers from a previous crashed session
        if self._chunk_dir.exists():
            shutil.rmtree(self._chunk_dir, ignore_errors=True)
        self._chunk_dir.mkdir(parents=True, exist_ok=True)

        self._chunks = []
        self._time_offset = 0.0
        self._stop_event.clear()

        # Resolve config once — avoids I/O on every chunk
        self._hw = detect_hardware()
        self._whisper_model = self._hw["whisper_model"]
        self._llm_model = self._hw["llm_model"]
        self._dictionary = get_config("dictionary", []) or []

        # Start the background transcription + summarization worker
        self._worker_thread = threading.Thread(
            target=self._worker_loop, daemon=True, name="session-worker"
        )
        self._worker_thread.start()

        # Start the periodic flush timer
        self._flush_thread = threading.Thread(
            target=self._flush_loop, daemon=True, name="session-flush"
        )
        self._flush_thread.start()

        print(f"[session] started — chunks in {self._chunk_dir}", flush=True)

    def finish(self, title: str) -> Path:
        """Stop recording, process remaining audio, assemble the meeting.

        Returns the meeting output directory.
        """
        self._p("transcribe", "Saving recording...")

        # 1. Stop the capture stream (no new audio after this)
        self._stop_event.set()
        self._recorder.stop_stream()

        # 2. Flush any remaining buffered audio as the final chunk
        self._flush_chunk()

        # Wait for flush thread to exit
        if self._flush_thread and self._flush_thread.is_alive():
            self._flush_thread.join(timeout=5)

        # 3. Signal the worker to finish remaining items and stop.
        #    No timeout — the worker WILL exit (finite queue + poison pill).
        #    A timeout here caused blank meetings when processing took long.
        self._work_queue.put(None)  # poison pill
        if self._worker_thread and self._worker_thread.is_alive():
            self._p("transcribe", "Transcribing...")
            self._worker_thread.join()

        # 4. Assemble the meeting from chunks
        out_dir = self._assemble_meeting(title)

        # 5. Clean up chunk directory
        try:
            shutil.rmtree(self._chunk_dir, ignore_errors=True)
        except Exception:
            pass

        self._p("done", "")
        print(f"[session] meeting assembled: {out_dir}", flush=True)
        return out_dir

    # ── Chunk flushing ────────────────────────────────────────────────────

    def _flush_loop(self):
        """Periodically flush audio buffers to chunk files."""
        while not self._stop_event.wait(timeout=CHUNK_INTERVAL):
            self._flush_chunk()

    def _flush_chunk(self):
        """Flush current recorder buffers into a numbered chunk WAV."""
        with self._flush_lock:
            chunk_index = len(self._chunks)
            chunk_path = self._chunk_dir / f"chunk_{chunk_index:04d}.wav"

            result = self._recorder.flush_chunk(chunk_path)
            if result is None:
                return  # No audio to flush

            # Read back the duration from the written file
            try:
                info = sf.info(str(chunk_path))
                duration = info.duration
            except Exception:
                duration = 0.0

            chunk = ChunkInfo(chunk_index, chunk_path, self._time_offset)
            chunk.duration = duration
            self._chunks.append(chunk)
            self._time_offset += duration

        # Queue for background transcription + summarization
        self._work_queue.put(chunk)

    # ── Background worker ─────────────────────────────────────────────────

    def _worker_loop(self):
        """Process chunks: transcribe → clean → summarize, with cross-chunk context."""
        from anduin.summarization.engine import clean_transcript, summarize_chunk
        from anduin.transcription.whisper import transcribe

        speaker_names = get_speaker_names()

        prev_context: str | None = None

        while True:
            item = self._work_queue.get()
            if item is None:
                break  # poison pill

            chunk = item
            # Only show UI progress after recording has stopped
            finishing = self._stop_event.is_set()
            try:
                # ── 1. Transcribe (with cross-chunk context) ──────────
                if finishing:
                    self._p("transcribe", "Transcribing...")

                segments = transcribe(
                    chunk.path,
                    model_size=self._whisper_model,
                    dictionary=self._dictionary or None,
                    context=prev_context,
                )

                for seg in segments:
                    seg["start"] = round(seg["start"] + chunk.offset, 3)
                    seg["end"] = round(seg["end"] + chunk.offset, 3)
                    for w in seg.get("words", []):
                        w["start"] = round(w["start"] + chunk.offset, 3)
                        w["end"] = round(w["end"] + chunk.offset, 3)

                segments = align([], segments, speaker_names=speaker_names)

                tail_texts = [s["text"] for s in segments[-3:] if s.get("text")]
                prev_context = " ".join(tail_texts) if tail_texts else None

                print(
                    f"[session] chunk {chunk.index} transcribed: "
                    f"{len(segments)} segments",
                    flush=True,
                )

                # ── 2. LLM transcript cleanup ─────────────────────────
                if segments:
                    if finishing:
                        self._p("cleaning", "Cleaning up transcript...")
                    segments = clean_transcript(
                        segments,
                        model=self._llm_model,
                        dictionary=self._dictionary or None,
                    )
                    print(
                        f"[session] chunk {chunk.index} cleaned",
                        flush=True,
                    )

                chunk.transcript = segments

                # Persist alongside the chunk file (crash recovery)
                transcript_path = chunk.path.with_suffix(".json")
                transcript_path.write_text(
                    json.dumps(segments, indent=2, ensure_ascii=False)
                )

                # ── 3. Summarize ──────────────────────────────────────
                if segments:
                    if finishing:
                        self._p("summarizing", "Generating summary...")
                    summary = summarize_chunk(
                        segments,
                        model=self._llm_model,
                    )
                    chunk.summary = summary

                    summary_path = chunk.path.with_name(
                        chunk.path.stem + "_summary.txt"
                    )
                    summary_path.write_text(summary)
                    print(
                        f"[session] chunk {chunk.index} summarized "
                        f"({len(summary)} chars)",
                        flush=True,
                    )

            except Exception as e:
                import traceback

                print(
                    f"[session] chunk {chunk.index} processing failed: {e}",
                    flush=True,
                )
                traceback.print_exc()

    # ── Meeting assembly ──────────────────────────────────────────────────

    def _assemble_meeting(self, title: str) -> Path:
        """Merge chunk transcripts and synthesize chunk summaries into a meeting."""
        from anduin.storage.store import _index_meeting

        out_dir = meeting_dir(title)

        # ── Merge audio chunks into one file (for playback / re-processing) ──
        try:
            import numpy as np
            all_audio = []
            for chunk in self._chunks:
                if chunk.path.exists():
                    data, sr = sf.read(str(chunk.path), dtype="float32")
                    all_audio.append(data)
            if all_audio:
                combined_audio = np.concatenate(all_audio)
                sf.write(str(out_dir / "audio.wav"), combined_audio, SAMPLE_RATE)
                print(f"[session] merged audio: {len(combined_audio)/SAMPLE_RATE:.1f}s", flush=True)
        except Exception as e:
            print(f"[session] audio merge failed (chunks still on disk): {e}", flush=True)
            # Fallback: copy individual chunks to meeting dir
            for chunk in self._chunks:
                if chunk.path.exists():
                    try:
                        shutil.copy2(chunk.path, out_dir / chunk.path.name)
                    except Exception:
                        pass

        # Index the meeting immediately so it's visible
        _index_meeting(out_dir, title=title)

        # ── Merge transcripts ─────────────────────────────────────────────
        all_segments = []
        for chunk in self._chunks:
            if chunk.transcript is not None:
                all_segments.extend(chunk.transcript)
            else:
                # Try loading from the JSON file the worker persists per-chunk
                transcript_path = chunk.path.with_suffix(".json")
                if transcript_path.exists():
                    try:
                        segments = json.loads(transcript_path.read_text())
                        all_segments.extend(segments)
                        print(f"[session] chunk {chunk.index}: recovered transcript from disk", flush=True)
                        continue
                    except Exception:
                        pass
                print(f"[session] WARNING: chunk {chunk.index} has no transcript", flush=True)

        if all_segments:
            write_transcript(all_segments, out_dir)
            # Re-index with transcript metadata (duration, speaker count)
            _index_meeting(out_dir, title=title)
            print(f"[session] merged transcript: {len(all_segments)} segments", flush=True)

        # ── Synthesize summaries ──────────────────────────────────────────
        auto_summarize = get_config("auto_summarize", True)
        if auto_summarize and any(c.summary for c in self._chunks):
            try:
                self._p("summarize", "Generating summary...")

                # Get user's template preference
                default_tid = get_config("default_template", None)
                templates = get_config("custom_templates", [])
                custom_prompt = None
                template_id = "standard"
                if default_tid:
                    for ct in templates:
                        if ct.get("id") == default_tid:
                            template_id = default_tid
                            custom_prompt = ct.get("prompt", "")
                            break

                chunk_summaries = [c.summary or "" for c in self._chunks]

                from anduin.summarization.engine import synthesize_summaries

                summary = synthesize_summaries(
                    chunk_summaries,
                    model=self._llm_model,
                    custom_prompt=custom_prompt,
                )
                save_summary(out_dir, summary, title=title, template_id=template_id)
                print(f"[session] final summary: {len(summary)} chars", flush=True)
            except Exception as e:
                import traceback

                print(f"[session] SYNTHESIS FAILED (transcript safe): {e}", flush=True)
                traceback.print_exc()

        # Delete audio file after processing unless the user opted to keep it
        if not get_config("keep_audio", False):
            audio_file = out_dir / "audio.wav"
            if audio_file.exists():
                try:
                    audio_file.unlink()
                    print("[session] audio file removed (keep_audio=off)", flush=True)
                except Exception:
                    pass

        return out_dir

    # ── Helpers ───────────────────────────────────────────────────────────

    def _p(self, stage: str, msg: str):
        if self._on_progress:
            self._on_progress(stage, msg)


def recover_chunks(on_progress: ProgressCallback | None = None) -> Path | None:
    """Recover a meeting from chunk files left by a crashed session.

    Returns the meeting directory, or None if nothing to recover.
    """
    chunk_dir = store.APP_DIR / "_active_recording"
    if not chunk_dir.exists():
        return None

    chunks = sorted(chunk_dir.glob("chunk_*.wav"))
    if not chunks:
        shutil.rmtree(chunk_dir, ignore_errors=True)
        return None

    total_size = sum(c.stat().st_size for c in chunks)
    if total_size < 1024:  # < 1 KB total — nothing meaningful
        shutil.rmtree(chunk_dir, ignore_errors=True)
        return None

    print(f"[recovery] found {len(chunks)} orphaned chunks ({total_size} bytes)", flush=True)

    if on_progress:
        on_progress("recover", f"Recovering {len(chunks)} audio chunks from last session...")

    from datetime import datetime

    from anduin.hardware.detect import detect as detect_hardware
    from anduin.merge.aligner import align, write_transcript
    from anduin.storage.store import _index_meeting
    from anduin.summarization.engine import summarize, synthesize_summaries
    from anduin.transcription.whisper import transcribe

    hw = detect_hardware()
    dictionary = get_config("dictionary", []) or []
    speaker_names = get_speaker_names()

    # Use the oldest chunk's mtime for the meeting date
    mtime = min(c.stat().st_mtime for c in chunks)
    title = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M") + " (recovered)"
    out_dir = store.meeting_dir(title)

    # Merge audio
    try:
        import numpy as np

        all_audio = []
        for c in chunks:
            data, sr = sf.read(str(c), dtype="float32")
            all_audio.append(data)
        if all_audio:
            sf.write(str(out_dir / "audio.wav"), np.concatenate(all_audio), SAMPLE_RATE)
    except Exception as e:
        print(f"[recovery] audio merge failed: {e}", flush=True)
        # Copy chunks individually
        for c in chunks:
            try:
                shutil.copy2(c, out_dir / c.name)
            except Exception:
                pass

    _index_meeting(out_dir, title=title)

    # Check for pre-existing chunk transcripts (worker might have saved some)
    all_segments = []
    chunk_summaries = []
    offset = 0.0

    for i, chunk_path in enumerate(chunks):
        # Check for saved transcript from the worker
        transcript_path = chunk_path.with_suffix(".json")
        summary_path = chunk_path.with_name(chunk_path.stem + "_summary.txt")

        if transcript_path.exists():
            try:
                segments = json.loads(transcript_path.read_text())
                all_segments.extend(segments)
                print(f"[recovery] chunk {i}: loaded saved transcript", flush=True)
            except Exception:
                pass
        else:
            # Transcribe this chunk
            try:
                if on_progress:
                    on_progress("transcribe", f"Transcribing recovered chunk {i+1}/{len(chunks)}...")
                segments = transcribe(chunk_path, model_size=hw["whisper_model"], dictionary=dictionary or None)
                for seg in segments:
                    seg["start"] = round(seg["start"] + offset, 3)
                    seg["end"] = round(seg["end"] + offset, 3)
                segments = align([], segments, speaker_names=speaker_names)
                all_segments.extend(segments)
            except Exception as e:
                print(f"[recovery] chunk {i} transcription failed: {e}", flush=True)

        if summary_path.exists():
            try:
                chunk_summaries.append(summary_path.read_text())
            except Exception:
                chunk_summaries.append("")
        else:
            chunk_summaries.append("")

        try:
            info = sf.info(str(chunk_path))
            offset += info.duration
        except Exception:
            offset += 300.0  # assume 5 min if we can't read

    if all_segments:
        write_transcript(all_segments, out_dir)
        _index_meeting(out_dir, title=title)

    # Summarize if we have transcripts
    auto_summarize = get_config("auto_summarize", True)
    if auto_summarize and all_segments:
        try:
            if on_progress:
                on_progress("summarize", "Generating summary for recovered meeting...")

            # If we have chunk summaries from the worker, synthesize them
            if any(s.strip() for s in chunk_summaries):
                summary = synthesize_summaries(chunk_summaries, model=hw["llm_model"])
            else:
                # Fall back to summarizing the full transcript
                summary = summarize(all_segments, model=hw["llm_model"])

            save_summary(out_dir, summary, title=title, template_id="standard")
        except Exception as e:
            print(f"[recovery] summarization failed: {e}", flush=True)

    # Clean up
    try:
        shutil.rmtree(chunk_dir, ignore_errors=True)
    except Exception:
        pass

    print(f"[recovery] meeting recovered: {out_dir}", flush=True)
    return out_dir
