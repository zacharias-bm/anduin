from __future__ import annotations
import threading
from pathlib import Path

import sounddevice as sd


SAMPLE_RATE = 16000
CHANNELS = 1


class Recorder:
    """Audio recorder using ScreenCaptureKit for both in-person and digital modes.

    In-person: mic only via SCK.
    Digital: system audio + mic via SCK.
    """

    def __init__(self):
        self._system_recorder = None
        self._mode = "inperson"
        self.is_recording = False

    def start(self, device: int | str | None = None, mode: str = "inperson"):
        self._mode = mode
        self.is_recording = True

        from anduin.capture.system_audio import SystemAudioRecorder
        self._system_recorder = SystemAudioRecorder()
        if mode == "digital":
            self._system_recorder.start(include_mic=True)
        else:
            self._system_recorder.start(mic_only=True)

    def flush_chunk(self, output_path: Path) -> Path | None:
        return self._system_recorder.flush_chunk(output_path)

    def stop_stream(self):
        if self._system_recorder and self._system_recorder._stream:
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

        self.is_recording = False

    def stop(self, output_path: Path) -> Path:
        result = self._system_recorder.stop(output_path)
        self._system_recorder = None
        self.is_recording = False
        return result

    @staticmethod
    def list_devices() -> list[dict]:
        return [
            {"index": i, "name": d["name"], "channels": d["max_input_channels"]}
            for i, d in enumerate(sd.query_devices())
            if d["max_input_channels"] > 0
        ]
