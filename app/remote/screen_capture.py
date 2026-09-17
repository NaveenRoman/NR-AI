"""
NR-AI Safe Screen Capture Abstraction.
Step 10 Phase 2 — Remote Telemetry & Screen / Frame Streaming Protocol.

Provides replaceable screen capture engines:
1. DEVELOPMENT_MOCK_CAPTURE: Deterministic synthetic test patterns for unit tests.
2. LOCAL_SCREEN_CAPTURE: Isolated Windows desktop pixel capture without shell or external processes.
"""

from abc import ABC, abstractmethod
import io
import time
from typing import Optional, Tuple

from app.remote.config import (
    MAX_FRAME_HEIGHT,
    MAX_FRAME_WIDTH,
)
from app.remote.frame import (
    FrameEncoding,
    StreamFrame,
    create_stream_frame,
)


class ScreenCaptureEngine(ABC):
    """Abstract interface for screen capture implementations."""

    @abstractmethod
    def capture_frame(
        self,
        stream_id: str,
        sequence_number: int,
        max_width: int = MAX_FRAME_WIDTH,
        max_height: int = MAX_FRAME_HEIGHT,
        encoding: FrameEncoding = FrameEncoding.JPEG,
    ) -> Tuple[bool, str, Optional[StreamFrame]]:
        """Capture a single bounded visual frame."""
        pass

    @abstractmethod
    def get_capture_type(self) -> str:
        """Returns the explicit identifier of the capture engine."""
        pass


class MockScreenCaptureEngine(ScreenCaptureEngine):
    """
    Deterministic synthetic test pattern frame generator.
    Clearly identifies itself as DEVELOPMENT_MOCK_CAPTURE to prevent false claims of live capture.
    """

    def __init__(self, default_width: int = 640, default_height: int = 480):
        self.default_width = default_width
        self.default_height = default_height
        self._capture_count = 0

    def get_capture_type(self) -> str:
        return "DEVELOPMENT_MOCK_CAPTURE"

    def capture_frame(
        self,
        stream_id: str,
        sequence_number: int,
        max_width: int = MAX_FRAME_WIDTH,
        max_height: int = MAX_FRAME_HEIGHT,
        encoding: FrameEncoding = FrameEncoding.MOCK_RGB,
    ) -> Tuple[bool, str, Optional[StreamFrame]]:
        self._capture_count += 1
        w = min(self.default_width, max_width)
        h = min(self.default_height, max_height)

        # Generate deterministic synthetic pattern bytes
        pattern_seed = (sequence_number * 37 + self._capture_count * 17) % 256
        # Generate bounded mock payload (header + repeat pattern)
        header = f"MOCK_FRAME:stream={stream_id}:seq={sequence_number}:res={w}x{h}:ts={time.time():.3f}:".encode("utf-8")
        payload = header + bytes([pattern_seed ^ (i % 255) for i in range(1024)])

        try:
            frame = create_stream_frame(
                stream_id=stream_id,
                sequence_number=sequence_number,
                width=w,
                height=h,
                encoding=encoding if encoding in (FrameEncoding.MOCK_RGB, FrameEncoding.JPEG, FrameEncoding.PNG) else FrameEncoding.MOCK_RGB,
                payload_bytes=payload,
            )
            return True, "MOCK_FRAME_CAPTURED", frame
        except Exception as e:
            return False, f"MOCK_CAPTURE_ERROR: {e}", None


class LocalScreenCaptureEngine(ScreenCaptureEngine):
    """
    Isolated local desktop pixel capture using in-process PIL / GDI without subprocesses.
    Identifies itself as LOCAL_SCREEN_CAPTURE.
    """

    def __init__(self):
        self._is_available = self._probe_capture_backend()

    def get_capture_type(self) -> str:
        return "LOCAL_SCREEN_CAPTURE"

    def _probe_capture_backend(self) -> bool:
        try:
            from PIL import ImageGrab
            return True
        except Exception:
            return False

    def capture_frame(
        self,
        stream_id: str,
        sequence_number: int,
        max_width: int = MAX_FRAME_WIDTH,
        max_height: int = MAX_FRAME_HEIGHT,
        encoding: FrameEncoding = FrameEncoding.JPEG,
    ) -> Tuple[bool, str, Optional[StreamFrame]]:
        if not self._is_available:
            return False, "CAPTURE_BACKEND_UNAVAILABLE", None

        try:
            from PIL import ImageGrab

            # Direct in-process desktop capture (0 subprocesses, no shell invocation)
            img = ImageGrab.grab()
            if img is None:
                return False, "HEADLESS_OR_NO_DISPLAY", None

            # Bounded resolution clamping
            orig_w, orig_h = img.size
            target_w = min(orig_w, max_width)
            target_h = min(orig_h, max_height)
            if orig_w > target_w or orig_h > target_h:
                img.thumbnail((target_w, target_h))

            # Encode to in-memory buffer
            buf = io.BytesIO()
            fmt = "JPEG" if encoding == FrameEncoding.JPEG else "PNG"
            if fmt == "JPEG" and img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            img.save(buf, format=fmt, quality=75 if fmt == "JPEG" else None)
            payload_bytes = buf.getvalue()

            frame = create_stream_frame(
                stream_id=stream_id,
                sequence_number=sequence_number,
                width=img.size[0],
                height=img.size[1],
                encoding=FrameEncoding.JPEG if fmt == "JPEG" else FrameEncoding.PNG,
                payload_bytes=payload_bytes,
            )
            return True, "LOCAL_FRAME_CAPTURED", frame
        except Exception as e:
            # Clean handling if running in headless or lock screen environment
            return False, f"LOCAL_CAPTURE_FAILED: {e}", None
