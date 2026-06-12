from __future__ import annotations
import threading
import numpy as np
import sounddevice as sd
import soundfile as sf
from pathlib import Path

SAMPLE_RATE = 16000
CHANNELS = 1


class Recorder:
    """Audio recorder that handles both in-person (mic only) and digital
    (system audio + mic via ScreenCaptureKit) modes."""

    def __init__(self):
        self._frames: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._system_recorder = None
        self._lock = threading.Lock()
        self._mode = "inperson"
        self.is_recording = False

    def start(self, device: int | str | None = None, mode: str = "inperson"):
        """Start recording.

        Args:
            device: Audio device index (used for inperson mode).
            mode: 'inperson' for mic-only, 'digital' for system audio + mic.
        """
        self._frames = []
        self._mode = mode
        self.is_recording = True

        if mode == "digital":
            from anduin.capture.system_audio import SystemAudioRecorder
            self._system_recorder = SystemAudioRecorder()
            self._system_recorder.start(include_mic=True)
        else:
            self._stream = sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype="float32",
                device=device,
                callback=self._callback,
            )
            self._stream.start()

    def flush_chunk(self, output_path: Path) -> Path | None:
        """Flush current buffers to a chunk file without stopping recording.

        Returns the output path if audio was written, or None if buffers were empty.
        """
        if self._mode == "digital" and self._system_recorder:
            return self._system_recorder.flush_chunk(output_path)

        with self._lock:
            if not self._frames:
                return None
            frames = list(self._frames)
            self._frames.clear()

        try:
            audio = np.concatenate(frames, axis=0)
        except Exception as e:
            print(f"[recorder] chunk concat error: {e}", flush=True)
            audio = self._emergency_concat(frames)

        if len(audio) == 0:
            return None

        sf.write(str(output_path), audio, SAMPLE_RATE)
        duration = len(audio) / SAMPLE_RATE
        print(f"[recorder] flushed chunk: {duration:.1f}s → {output_path.name}", flush=True)
        return output_path

    def stop_stream(self):
        """Stop the capture stream without writing audio.

        Call flush_chunk() after this to get any remaining buffered audio.
        """
        if self._mode == "digital" and self._system_recorder:
            if self._system_recorder._stream:
                stop_event = threading.Event()

                def on_stop(error):
                    if error:
                        print(f"[system_audio] stop error: {error}", flush=True)
                    stop_event.set()

                try:
                    self._system_recorder._stream.stopCaptureWithCompletionHandler_(on_stop)
                    if not stop_event.wait(timeout=10):
                        print("[system_audio] Warning: stop timed out", flush=True)
                except Exception as e:
                    print(f"[system_audio] Warning: stop failed: {e}", flush=True)
                self._system_recorder._stream = None
                self._system_recorder._output_delegate = None
                self._system_recorder.is_recording = False
        else:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:
                print(f"[recorder] Warning: stream stop error: {e}", flush=True)
            self._stream = None

        self.is_recording = False

    def stop(self, output_path: Path) -> Path:
        if self._mode == "digital" and self._system_recorder:
            result = self._system_recorder.stop(output_path)
            self._system_recorder = None
            self.is_recording = False
            return result

        try:
            self._stream.stop()
            self._stream.close()
        except Exception as e:
            print(f"[recorder] Warning: stream stop error: {e}", flush=True)

        self.is_recording = False

        with self._lock:
            frames = list(self._frames)
            self._frames.clear()

        if not frames:
            print("[recorder] Warning: No audio frames captured!", flush=True)
            audio = np.zeros((SAMPLE_RATE, CHANNELS), dtype="float32")
        else:
            try:
                audio = np.concatenate(frames, axis=0)
                print(f"[recorder] Captured {len(frames)} frames ({len(audio)/SAMPLE_RATE:.2f} seconds)", flush=True)
            except Exception as e:
                print(f"[recorder] ERROR concatenating frames: {e} — saving chunks individually", flush=True)
                audio = self._emergency_concat(frames)

        try:
            sf.write(str(output_path), audio, SAMPLE_RATE)
        except Exception as e:
            # Try a fallback location
            fallback = output_path.parent / f"_emergency_{output_path.name}"
            print(f"[recorder] ERROR writing {output_path}: {e} — trying {fallback}", flush=True)
            sf.write(str(fallback), audio, SAMPLE_RATE)
            return fallback

        return output_path

    def _emergency_concat(self, frames: list[np.ndarray]) -> np.ndarray:
        """Best-effort concatenation: skip corrupt frames."""
        good = []
        for i, f in enumerate(frames):
            try:
                arr = np.asarray(f, dtype="float32")
                if arr.size > 0 and not np.all(np.isnan(arr)):
                    good.append(arr)
            except Exception:
                print(f"[recorder] skipping corrupt frame {i}", flush=True)
        if good:
            return np.concatenate(good, axis=0)
        return np.zeros((SAMPLE_RATE, CHANNELS), dtype="float32")

    def _callback(self, indata, frames, time, status):
        if status:
            print(f"[recorder] status: {status}", flush=True)
        with self._lock:
            self._frames.append(indata.copy())

    @staticmethod
    def list_devices() -> list[dict]:
        return [
            {"index": i, "name": d["name"], "channels": d["max_input_channels"]}
            for i, d in enumerate(sd.query_devices())
            if d["max_input_channels"] > 0
        ]
