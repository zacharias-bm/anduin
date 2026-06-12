from __future__ import annotations
"""System audio capture using macOS ScreenCaptureKit (macOS 13+).

Captures system audio (and optionally microphone) without needing
BlackHole or any virtual audio driver. Uses SCStream with audio-only
capture — no screen content is recorded.

The one-time "Screen Recording" permission prompt is triggered
automatically by macOS when SCShareableContent is first accessed.
"""
import threading
from pathlib import Path

import warnings

import numpy as np
import objc
import soundfile as sf

import AppKit
import CoreMedia
import ScreenCaptureKit as SC

# Suppress PyObjC pointer warnings for CMSampleBuffer
warnings.filterwarnings("ignore", message=".*ObjCPointer.*")

SAMPLE_RATE = 16000
CHANNELS = 1


def has_permission() -> bool:
    """Check if Screen Recording permission has been granted."""
    event = threading.Event()
    result = {"ok": False}

    def handler(content, error):
        result["ok"] = content is not None and error is None
        event.set()

    SC.SCShareableContent.getShareableContentWithCompletionHandler_(handler)
    event.wait(timeout=5)
    return result["ok"]


def request_permission() -> bool:
    """Trigger the macOS permission prompt by accessing shareable content."""
    return has_permission()


class SystemAudioRecorder:
    """Records system audio (+ optional mic) via ScreenCaptureKit.

    System audio and mic are captured into separate timestamped buffers,
    then mixed together at stop time to avoid ordering issues.
    """

    def __init__(self):
        # Each entry is (timestamp_seconds, samples_array)
        self._system_buffers: list[tuple[float, np.ndarray]] = []
        self._mic_buffers: list[tuple[float, np.ndarray]] = []
        self._stream = None
        self._lock = threading.Lock()
        self._output_delegate = None
        self._include_mic = False
        self._debug_logged = False
        self.is_recording = False

    def start(self, include_mic: bool = True):
        """Start capturing system audio.

        Args:
            include_mic: Also capture microphone input (True for digital meetings).
        """
        self._system_buffers = []
        self._mic_buffers = []
        self._include_mic = include_mic
        self._debug_logged = False
        self.is_recording = True

        # Get shareable content (triggers permission if needed)
        event = threading.Event()
        content_result = {"content": None, "error": None}

        def on_content(content, error):
            content_result["content"] = content
            content_result["error"] = error
            event.set()

        SC.SCShareableContent.getShareableContentWithCompletionHandler_(on_content)
        event.wait(timeout=10)

        if content_result["error"] or not content_result["content"]:
            raise RuntimeError(
                "Cannot access screen content. Grant Screen Recording permission in "
                "System Settings → Privacy & Security → Screen Recording."
            )

        content = content_result["content"]
        displays = content.displays()
        if not displays:
            raise RuntimeError("No displays found")

        # Filter: capture the first display (needed for the API, but we only use audio)
        content_filter = SC.SCContentFilter.alloc().initWithDisplay_excludingApplications_exceptingWindows_(
            displays[0], [], []
        )

        # Configure: audio capture, minimal video
        config = SC.SCStreamConfiguration.alloc().init()
        config.setCapturesAudio_(True)
        config.setSampleRate_(SAMPLE_RATE)
        config.setChannelCount_(CHANNELS)
        config.setExcludesCurrentProcessAudio_(True)

        if include_mic:
            config.setCaptureMicrophone_(True)

        # Minimize video (can't fully disable, but make it tiny)
        config.setWidth_(2)
        config.setHeight_(2)

        # Create stream and delegate
        self._output_delegate = _AudioOutputDelegate.alloc().initWithRecorder_(self)
        self._stream = SC.SCStream.alloc().initWithFilter_captureOutputProperties_delegate_(
            content_filter, config, None
        )

        # Add system audio output
        success, error = self._stream.addStreamOutput_type_sampleHandlerQueue_error_(
            self._output_delegate,
            SC.SCStreamOutputTypeAudio,
            None,
            None,
        )
        if not success:
            raise RuntimeError(f"Failed to add audio output: {error}")

        # Add mic output
        if include_mic:
            success2, error2 = self._stream.addStreamOutput_type_sampleHandlerQueue_error_(
                self._output_delegate,
                SC.SCStreamOutputTypeMicrophone,
                None,
                None,
            )
            if not success2:
                print(f"[system_audio] Warning: mic output failed: {error2}", flush=True)

        # Start capture
        start_event = threading.Event()
        start_result = {"error": None}

        def on_start(error):
            start_result["error"] = error
            start_event.set()

        self._stream.startCaptureWithCompletionHandler_(on_start)
        start_event.wait(timeout=10)

        if start_result["error"]:
            raise RuntimeError(f"Failed to start capture: {start_result['error']}")

        print("[system_audio] capture started", flush=True)

    def flush_chunk(self, output_path: Path) -> Path | None:
        """Flush current buffers to a chunk file without stopping capture.

        Returns the output path if audio was written, or None if buffers were empty.
        """
        with self._lock:
            if not self._system_buffers and not self._mic_buffers:
                return None
            sys_bufs = list(self._system_buffers)
            mic_bufs = list(self._mic_buffers)
            self._system_buffers.clear()
            self._mic_buffers.clear()

        try:
            audio = self._mix_streams(sys_bufs, mic_bufs)
            if len(audio) == 0:
                return None
            sf.write(str(output_path), audio, SAMPLE_RATE)
            duration = len(audio) / SAMPLE_RATE
            print(f"[system_audio] flushed chunk: {duration:.1f}s → {output_path.name}", flush=True)
            return output_path
        except Exception as e:
            print(f"[system_audio] chunk flush failed: {e} — attempting emergency dump", flush=True)
            return self._emergency_dump(sys_bufs, mic_bufs, output_path)

    def stop(self, output_path: Path) -> Path:
        """Stop capture, mix streams, and write audio to file.

        Resilience strategy: if the normal mix/write path fails, dump
        raw buffers to individual WAV files so the audio is never lost.
        """
        if self._stream:
            stop_event = threading.Event()

            def on_stop(error):
                if error:
                    print(f"[system_audio] stop error: {error}", flush=True)
                stop_event.set()

            try:
                self._stream.stopCaptureWithCompletionHandler_(on_stop)
                if not stop_event.wait(timeout=10):
                    print("[system_audio] Warning: stop timed out, proceeding anyway", flush=True)
            except Exception as e:
                print(f"[system_audio] Warning: stop failed: {e}, proceeding with buffers", flush=True)

        self.is_recording = False

        with self._lock:
            sys_bufs = list(self._system_buffers)
            mic_bufs = list(self._mic_buffers)
            # Free RAM immediately — we have our copies
            self._system_buffers.clear()
            self._mic_buffers.clear()

        try:
            audio = self._mix_streams(sys_bufs, mic_bufs)

            if len(audio) == 0:
                print("[system_audio] Warning: no audio captured", flush=True)
                audio = np.zeros(SAMPLE_RATE, dtype="float32")
            else:
                duration = len(audio) / SAMPLE_RATE
                print(f"[system_audio] captured {duration:.2f}s "
                      f"({len(sys_bufs)} system + {len(mic_bufs)} mic buffers)", flush=True)

            sf.write(str(output_path), audio, SAMPLE_RATE)
        except Exception as e:
            print(f"[system_audio] ERROR in mix/write: {e} — attempting emergency dump", flush=True)
            output_path = self._emergency_dump(sys_bufs, mic_bufs, output_path)

        self._stream = None
        self._output_delegate = None
        return output_path

    def _emergency_dump(
        self,
        sys_bufs: list[tuple[float, np.ndarray]],
        mic_bufs: list[tuple[float, np.ndarray]],
        output_path: Path,
    ) -> Path:
        """Last-resort save: write raw buffers to disk without mixing.

        Tries system audio first, then mic, then individual chunks.
        Returns the path that was successfully written.
        """
        # Try writing each stream individually — simpler than mixing
        for label, bufs in [("system", sys_bufs), ("mic", mic_bufs)]:
            if not bufs:
                continue
            try:
                arrays = [b[1] for b in sorted(bufs, key=lambda x: x[0])]
                audio = np.concatenate(arrays)
                sf.write(str(output_path), audio, SAMPLE_RATE)
                print(f"[system_audio] emergency: saved {label} stream ({len(audio)/SAMPLE_RATE:.1f}s)", flush=True)
                return output_path
            except Exception as e2:
                print(f"[system_audio] emergency: {label} concat failed: {e2}", flush=True)

        # Last resort: write individual buffer chunks as separate files
        recovery_dir = output_path.parent / "_audio_recovery"
        recovery_dir.mkdir(exist_ok=True)
        saved = 0
        for label, bufs in [("sys", sys_bufs), ("mic", mic_bufs)]:
            for i, (ts, samples) in enumerate(bufs):
                try:
                    chunk_path = recovery_dir / f"{label}_{i:05d}_{ts:.3f}.wav"
                    sf.write(str(chunk_path), samples, SAMPLE_RATE)
                    saved += 1
                except Exception:
                    pass
        if saved > 0:
            print(f"[system_audio] emergency: saved {saved} raw chunks to {recovery_dir}", flush=True)
            # Write a marker so recovery knows about this
            marker = output_path.parent / "_audio_recovery.marker"
            marker.write_text(str(recovery_dir))
        else:
            print("[system_audio] emergency: could not save any audio data!", flush=True)

        # Still write a minimal file so downstream doesn't crash on missing path
        try:
            sf.write(str(output_path), np.zeros(SAMPLE_RATE, dtype="float32"), SAMPLE_RATE)
        except Exception:
            pass
        return output_path

    def _mix_streams(
        self,
        sys_bufs: list[tuple[float, np.ndarray]],
        mic_bufs: list[tuple[float, np.ndarray]],
    ) -> np.ndarray:
        """Mix system audio and mic streams into a single mono track.

        Each stream is assembled in timestamp order into a continuous
        array, then the two are summed together.
        """
        sys_audio = self._assemble_stream(sys_bufs)
        mic_audio = self._assemble_stream(mic_bufs)

        if len(sys_audio) == 0 and len(mic_audio) == 0:
            return np.array([], dtype=np.float32)

        # Clip each stream to [-1, 1] before mixing — a single corrupt
        # sample with a huge value would otherwise cause peak-based
        # normalization to crush the entire signal to near-silence.
        if len(sys_audio) > 0:
            sys_audio = np.clip(sys_audio, -1.0, 1.0)
        if len(mic_audio) > 0:
            mic_audio = np.clip(mic_audio, -1.0, 1.0)

        if len(sys_audio) == 0:
            return mic_audio
        if len(mic_audio) == 0:
            return sys_audio

        # Pad shorter to match longer
        max_len = max(len(sys_audio), len(mic_audio))
        if len(sys_audio) < max_len:
            sys_audio = np.pad(sys_audio, (0, max_len - len(sys_audio)))
        if len(mic_audio) < max_len:
            mic_audio = np.pad(mic_audio, (0, max_len - len(mic_audio)))

        # Average instead of sum — prevents clipping and preserves
        # both streams at a reasonable level
        mixed = (sys_audio + mic_audio) * 0.5

        return mixed

    def _assemble_stream(self, buffers: list[tuple[float, np.ndarray]]) -> np.ndarray:
        """Assemble timestamped buffers into a continuous audio stream."""
        if not buffers:
            return np.array([], dtype=np.float32)

        # Sort by timestamp
        buffers.sort(key=lambda x: x[0])

        # Simple concatenation in timestamp order
        arrays = [b[1] for b in buffers]
        return np.concatenate(arrays)

    def _handle_audio_buffer(self, sample_buffer, is_mic: bool):
        """Called by the delegate when audio data arrives."""
        try:
            # PyObjC may pass this as an ObjCPointer — extract the raw pointer
            if hasattr(sample_buffer, '__pointer__'):
                buf_ptr = sample_buffer.__pointer__
            else:
                buf_ptr = sample_buffer

            # Get presentation timestamp for ordering
            pts = CoreMedia.CMSampleBufferGetPresentationTimeStamp(buf_ptr)
            timestamp = pts.value / pts.timescale if pts.timescale > 0 else 0.0

            # Get the raw data block
            block_buffer = CoreMedia.CMSampleBufferGetDataBuffer(buf_ptr)
            if block_buffer is None:
                return

            length = CoreMedia.CMBlockBufferGetDataLength(block_buffer)
            if length == 0:
                return

            # Extract raw audio bytes
            result = CoreMedia.CMBlockBufferCopyDataBytes(
                block_buffer, 0, length, None
            )

            if isinstance(result, tuple):
                status, raw_bytes = result[0], result[1]
                if status != 0 or raw_bytes is None:
                    return
            else:
                return

            samples = np.frombuffer(raw_bytes, dtype=np.float32).copy()
            if len(samples) == 0:
                return

            np.nan_to_num(samples, copy=False, nan=0.0, posinf=0.0, neginf=0.0)
            np.clip(samples, -1.0, 1.0, out=samples)

            # Debug: log first buffer info per stream
            if not self._debug_logged:
                stream_type = "mic" if is_mic else "system"
                print(f"[system_audio] first {stream_type} buffer: "
                      f"{len(samples)} samples, ts={timestamp:.3f}", flush=True)
                if not is_mic:
                    self._debug_logged = True

            # Flatten to mono (already configured as 1 channel, but safety check)
            samples = samples.flatten()

            with self._lock:
                if is_mic:
                    self._mic_buffers.append((timestamp, samples))
                else:
                    self._system_buffers.append((timestamp, samples))

        except Exception as e:
            if not self._debug_logged:
                print(f"[system_audio] buffer error: {e}", flush=True)
                self._debug_logged = True


# ObjC delegate class for receiving audio samples.
_SCStreamOutput = objc.protocolNamed("SCStreamOutput")


class _AudioOutputDelegate(AppKit.NSObject, protocols=[_SCStreamOutput]):
    """SCStreamOutput delegate that receives audio sample buffers."""

    def initWithRecorder_(self, recorder):
        self = objc.super(_AudioOutputDelegate, self).init()
        if self is None:
            return None
        self._recorder = recorder
        return self

    @objc.typedSelector(b"v@:@^{opaqueCMSampleBuffer=}q")
    def stream_didOutputSampleBuffer_ofType_(self, stream, sample_buffer, output_type):
        """Called by ScreenCaptureKit when audio data is available."""
        if output_type == SC.SCStreamOutputTypeAudio:
            self._recorder._handle_audio_buffer(sample_buffer, is_mic=False)
        elif output_type == SC.SCStreamOutputTypeMicrophone:
            self._recorder._handle_audio_buffer(sample_buffer, is_mic=True)
