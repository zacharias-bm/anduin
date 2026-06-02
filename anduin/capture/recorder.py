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

    def stop(self, output_path: Path) -> Path:
        if self._mode == "digital" and self._system_recorder:
            result = self._system_recorder.stop(output_path)
            self._system_recorder = None
            self.is_recording = False
            return result

        self._stream.stop()
        self._stream.close()
        self.is_recording = False
        if not self._frames:
            print("[recorder] Warning: No audio frames captured!", flush=True)
            audio = np.zeros((SAMPLE_RATE, CHANNELS), dtype="float32")
        else:
            audio = np.concatenate(self._frames, axis=0)
            print(f"[recorder] Captured {len(self._frames)} frames ({len(audio)/SAMPLE_RATE:.2f} seconds)", flush=True)
        sf.write(str(output_path), audio, SAMPLE_RATE)
        return output_path

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
