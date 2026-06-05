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

import numpy as np
import objc
import soundfile as sf

import AppKit
import ScreenCaptureKit as SC

SAMPLE_RATE = 16000
CHANNELS = 1


def has_permission() -> bool:
    """Check if Screen Recording permission has been granted.

    There's no direct API to check this pre-capture, so we attempt
    to get shareable content. If it fails, permission hasn't been granted.
    """
    event = threading.Event()
    result = {"ok": False}

    def handler(content, error):
        result["ok"] = content is not None and error is None
        event.set()

    SC.SCShareableContent.getShareableContentWithCompletionHandler_(handler)
    event.wait(timeout=5)
    return result["ok"]


def request_permission() -> bool:
    """Trigger the macOS permission prompt by accessing shareable content.

    Returns True if permission is granted.
    """
    return has_permission()


class SystemAudioRecorder:
    """Records system audio (+ optional mic) via ScreenCaptureKit."""

    def __init__(self):
        self._frames: list[np.ndarray] = []
        self._stream = None
        self._lock = threading.Lock()
        self._output_delegate = None
        self.is_recording = False

    def start(self, include_mic: bool = True):
        """Start capturing system audio.

        Args:
            include_mic: Also capture microphone input (True for digital meetings).
        """
        self._frames = []
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
                f"Cannot access screen content. Grant Screen Recording permission in "
                f"System Settings → Privacy & Security → Screen Recording."
            )

        content = content_result["content"]

        # Create a filter that captures all displays (we only want audio)
        displays = content.displays()
        if not displays:
            raise RuntimeError("No displays found")

        # Filter: capture the first display (needed for the API, but we only use audio)
        content_filter = SC.SCContentFilter.alloc().initWithDisplay_excludingApplications_exceptingWindows_(
            displays[0], [], []
        )

        # Configure: audio only, no video
        config = SC.SCStreamConfiguration.alloc().init()
        config.setCapturesAudio_(True)
        config.setSampleRate_(SAMPLE_RATE)
        config.setChannelCount_(CHANNELS)
        config.setExcludesCurrentProcessAudio_(True)  # Don't capture our own app's audio

        # Capture mic too if requested
        if include_mic:
            config.setCaptureMicrophone_(True)

        # Minimize video capture (can't fully disable it, but make it tiny)
        config.setWidth_(2)
        config.setHeight_(2)

        # Create stream
        self._output_delegate = _AudioOutputDelegate.alloc().initWithRecorder_(self)
        self._stream = SC.SCStream.alloc().initWithFilter_captureOutputProperties_delegate_(
            content_filter, config, None
        )

        # Add audio output handler
        audio_queue = AppKit.NSOperationQueue.alloc().init()
        audio_queue.setMaxConcurrentOperationCount_(1)

        success, error = self._stream.addStreamOutput_type_sampleHandlerQueue_error_(
            self._output_delegate,
            SC.SCStreamOutputTypeAudio,
            None,  # Use default queue
            None,
        )
        if not success:
            raise RuntimeError(f"Failed to add audio output: {error}")

        # If capturing mic, add mic output too
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

    def stop(self, output_path: Path) -> Path:
        """Stop capture and write audio to file."""
        if self._stream:
            stop_event = threading.Event()

            def on_stop(error):
                if error:
                    print(f"[system_audio] stop error: {error}", flush=True)
                stop_event.set()

            self._stream.stopCaptureWithCompletionHandler_(on_stop)
            stop_event.wait(timeout=5)

        self.is_recording = False

        with self._lock:
            if not self._frames:
                print("[system_audio] Warning: no audio captured", flush=True)
                audio = np.zeros((SAMPLE_RATE, CHANNELS), dtype="float32")
            else:
                audio = np.concatenate(self._frames, axis=0)
                print(
                    f"[system_audio] captured {len(self._frames)} buffers "
                    f"({len(audio)/SAMPLE_RATE:.2f} seconds)",
                    flush=True,
                )

        sf.write(str(output_path), audio, SAMPLE_RATE)
        self._stream = None
        self._output_delegate = None
        return output_path

    def _handle_audio_buffer(self, sample_buffer):
        """Called by the delegate when audio data arrives."""
        try:
            import CoreMedia

            # Get the audio buffer list from the sample buffer
            block_buffer = CoreMedia.CMSampleBufferGetDataBuffer(sample_buffer)
            if block_buffer is None:
                return

            length = CoreMedia.CMBlockBufferGetDataLength(block_buffer)
            if length == 0:
                return

            # Extract raw bytes
            data_bytes = CoreMedia.CMBlockBufferCreateContiguous(
                None, block_buffer, None, 0, 0, length, 0
            )
            if data_bytes is None or len(data_bytes) < 2:
                # Try alternative extraction
                status, data_pointer = CoreMedia.CMBlockBufferAccessDataBytes(
                    block_buffer, 0, length, None
                )
                if status == 0 and data_pointer:
                    samples = np.frombuffer(data_pointer, dtype=np.float32).copy()
                else:
                    return
            else:
                result_buffer = data_bytes[1] if isinstance(data_bytes, tuple) else data_bytes
                raw = CoreMedia.CMBlockBufferGetDataPointer(result_buffer, 0, None, None)
                if raw and len(raw) >= 3:
                    samples = np.frombuffer(raw[3], dtype=np.float32).copy()
                else:
                    return

            if len(samples) > 0:
                # Reshape to match our channel count
                if CHANNELS == 1 and len(samples.shape) == 1:
                    samples = samples.reshape(-1, 1)
                with self._lock:
                    self._frames.append(samples)

        except Exception as e:
            # Don't crash the audio callback
            pass


# ObjC delegate class for receiving audio samples.
# Declare protocol conformance via the protocols= parameter.
_SCStreamOutput = objc.protocolNamed("SCStreamOutput")


class _AudioOutputDelegate(AppKit.NSObject, protocols=[_SCStreamOutput]):
    """SCStreamOutput delegate that receives audio sample buffers."""

    def initWithRecorder_(self, recorder):
        self = objc.super(_AudioOutputDelegate, self).init()
        if self is None:
            return None
        self._recorder = recorder
        return self

    def stream_didOutputSampleBuffer_ofType_(self, stream, sample_buffer, output_type):
        """Called by ScreenCaptureKit when audio data is available."""
        if output_type in (SC.SCStreamOutputTypeAudio, SC.SCStreamOutputTypeMicrophone):
            self._recorder._handle_audio_buffer(sample_buffer)
