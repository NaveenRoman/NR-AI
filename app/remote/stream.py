"""
NR-AI Stream Lifecycle, Backpressure & Stream Session Engine.
Step 10 Phase 2 — Remote Telemetry & Screen / Frame Streaming Protocol.
"""

from dataclasses import dataclass, field
from enum import Enum
import secrets
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from app.remote.config import (
    DEFAULT_FPS,
    MAX_CONCURRENT_STREAMS_PER_DEVICE,
    MAX_FRAME_HEIGHT,
    MAX_FRAME_WIDTH,
    MAX_FRAMES_PER_SECOND,
    MAX_GLOBAL_CONCURRENT_STREAMS,
    MAX_QUEUE_BYTES,
    MAX_QUEUED_FRAMES,
    MAX_RECONNECT_ATTEMPTS,
    MAX_RECONNECT_WINDOW_SECONDS,
    MAX_STREAM_LIFETIME_SECONDS,
    MIN_FRAMES_PER_SECOND,
    STREAM_INACTIVITY_TIMEOUT_SECONDS,
)
from app.remote.emergency import EmergencyStopController
from app.remote.frame import (
    FrameEncoding,
    StreamFrame,
)
from app.remote.screen_capture import (
    MockScreenCaptureEngine,
    ScreenCaptureEngine,
)


class StreamState(str, Enum):
    IDLE = "IDLE"
    STARTING = "STARTING"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    STOPPING = "STOPPING"
    STOPPED = "STOPPED"
    FAILED = "FAILED"
    RECONNECTING = "RECONNECTING"


class StreamStateError(Exception):
    """Raised when an illegal stream lifecycle state transition is attempted."""
    pass


# Valid state transitions lookup table
VALID_STREAM_TRANSITIONS = {
    StreamState.IDLE: {StreamState.STARTING, StreamState.STOPPED, StreamState.FAILED},
    StreamState.STARTING: {StreamState.ACTIVE, StreamState.STOPPING, StreamState.STOPPED, StreamState.FAILED},
    StreamState.ACTIVE: {StreamState.PAUSED, StreamState.STOPPING, StreamState.STOPPED, StreamState.FAILED, StreamState.RECONNECTING},
    StreamState.PAUSED: {StreamState.ACTIVE, StreamState.STOPPING, StreamState.STOPPED, StreamState.FAILED, StreamState.RECONNECTING},
    StreamState.RECONNECTING: {StreamState.ACTIVE, StreamState.STOPPING, StreamState.STOPPED, StreamState.FAILED},
    StreamState.STOPPING: {StreamState.STOPPED, StreamState.FAILED},
    StreamState.STOPPED: set(),  # Terminal state
    StreamState.FAILED: {StreamState.STOPPED},
}


class StreamBackpressureQueue:
    """
    Thread-safe, bounded frame queue with DROP_OLDEST policy and total byte limits
    to guarantee zero unbounded memory growth and low latency.
    """

    def __init__(
        self,
        max_frames: int = MAX_QUEUED_FRAMES,
        max_bytes: int = MAX_QUEUE_BYTES,
    ):
        self.max_frames = max_frames
        self.max_bytes = max_bytes
        self._frames: List[StreamFrame] = []
        self._current_bytes: int = 0
        self._dropped_count: int = 0
        self._lock = threading.Lock()

    def push(self, frame: StreamFrame) -> Tuple[bool, str]:
        """
        Add a frame to the queue. If capacity or byte limits are exceeded,
        drops the oldest pending frame.
        """
        with self._lock:
            frame_size = frame.compressed_size

            # If single frame exceeds entire queue byte limit, reject it
            if frame_size > self.max_bytes:
                self._dropped_count += 1
                return False, f"FRAME_EXCEEDS_QUEUE_BYTE_LIMIT: {frame_size} > {self.max_bytes}"

            # Evict oldest frames if frame count ceiling reached
            while len(self._frames) >= self.max_frames and self._frames:
                oldest = self._frames.pop(0)
                self._current_bytes -= oldest.compressed_size
                self._dropped_count += 1

            # Evict oldest frames if adding new frame would exceed byte ceiling
            while (self._current_bytes + frame_size > self.max_bytes) and self._frames:
                oldest = self._frames.pop(0)
                self._current_bytes -= oldest.compressed_size
                self._dropped_count += 1

            self._frames.append(frame)
            self._current_bytes += frame_size
            return True, "FRAME_QUEUED"

    def pop(self) -> Optional[StreamFrame]:
        """Retrieve and remove the oldest pending frame."""
        with self._lock:
            if not self._frames:
                return None
            frame = self._frames.pop(0)
            self._current_bytes -= frame.compressed_size
            return frame

    # Standard Queue aliases
    def enqueue(self, frame: StreamFrame) -> bool:
        ok, _ = self.push(frame)
        return ok

    def dequeue(self) -> Optional[StreamFrame]:
        return self.pop()

    def peek(self) -> Optional[StreamFrame]:
        """Inspect the newest frame without removing."""
        with self._lock:
            if not self._frames:
                return None
            return self._frames[-1]

    def clear(self) -> int:
        with self._lock:
            count = len(self._frames)
            self._frames.clear()
            self._current_bytes = 0
            return count

    def get_stats(self) -> Dict[str, int]:
        with self._lock:
            return {
                "queued_frames": len(self._frames),
                "queued_bytes": self._current_bytes,
                "dropped_frames": self._dropped_count,
            }


@dataclass
class StreamSession:
    stream_id: str
    device_id: str
    session_id: str
    state: StreamState = StreamState.IDLE
    target_fps: float = DEFAULT_FPS
    max_width: int = MAX_FRAME_WIDTH
    max_height: int = MAX_FRAME_HEIGHT
    encoding: FrameEncoding = FrameEncoding.JPEG
    started_at: float = field(default_factory=time.time)
    expires_at: float = field(default_factory=lambda: time.time() + MAX_STREAM_LIFETIME_SECONDS)
    last_heartbeat_at: float = field(default_factory=time.time)
    reconnect_attempts: int = 0
    frame_sequence: int = 0
    total_frames_sent: int = 0
    queue: StreamBackpressureQueue = field(default_factory=StreamBackpressureQueue)
    state_history: List[Tuple[str, float, str]] = field(default_factory=list)

    def transition_to(self, new_state: StreamState, reason: str = "") -> None:
        """Execute a strictly validated lifecycle transition."""
        allowed_targets = VALID_STREAM_TRANSITIONS.get(self.state, set())
        # Emergency/terminal transitions to STOPPED or FAILED are universally allowed
        if new_state not in allowed_targets and new_state not in (StreamState.STOPPED, StreamState.FAILED):
            raise StreamStateError(
                f"Illegal stream transition: '{self.state.value}' -> '{new_state.value}' "
                f"(Allowed: {[s.value for s in allowed_targets]})"
            )

        self.state = new_state
        self.state_history.append((new_state.value, time.time(), reason))
        if new_state in (StreamState.STOPPED, StreamState.FAILED):
            self.queue.clear()

    def is_expired(self, current_time: Optional[float] = None) -> bool:
        now = current_time if current_time is not None else time.time()
        return now >= self.expires_at

    def touch(self, current_time: Optional[float] = None) -> None:
        now = current_time if current_time is not None else time.time()
        self.last_heartbeat_at = now

    def check_inactivity(self, current_time: Optional[float] = None) -> bool:
        """Returns True if stream has had no activity for > STREAM_INACTIVITY_TIMEOUT_SECONDS."""
        now = current_time if current_time is not None else time.time()
        return (now - self.last_heartbeat_at) > STREAM_INACTIVITY_TIMEOUT_SECONDS

    def record_disconnect(self) -> None:
        """Mark connection loss and transition to RECONNECTING state."""
        self.transition_to(StreamState.RECONNECTING, reason="CONNECTION_LOST")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stream_id": self.stream_id,
            "device_id": self.device_id,
            "session_id": self.session_id,
            "state": self.state.value,
            "target_fps": self.target_fps,
            "max_width": self.max_width,
            "max_height": self.max_height,
            "encoding": self.encoding.value,
            "started_at": self.started_at,
            "expires_at": self.expires_at,
            "last_heartbeat_at": self.last_heartbeat_at,
            "frame_sequence": self.frame_sequence,
            "total_frames_sent": self.total_frames_sent,
            "queue_stats": self.queue.get_stats(),
        }


class StreamManager:
    """
    Coordinates authenticated screen streaming sessions, captures,
    backpressure queues, connection recovery, and emergency stop integration.
    """

    def __init__(
        self,
        capture_engine: Optional[ScreenCaptureEngine] = None,
        emergency_stop: Optional[EmergencyStopController] = None,
    ):
        self.capture_engine = capture_engine or MockScreenCaptureEngine()
        self.emergency_stop = emergency_stop or EmergencyStopController()
        self._streams: Dict[str, StreamSession] = {}  # stream_id -> StreamSession
        self._lock = threading.Lock()

        # Register emergency stop cancellation callback to freeze and clear all streams
        self.emergency_stop.register_cancellation_callback(self._on_emergency_stop)

    def _on_emergency_stop(self) -> None:
        """Emergency stop hook: immediately halts and frees all active streams."""
        with self._lock:
            for stream in self._streams.values():
                if stream.state not in (StreamState.STOPPED, StreamState.FAILED):
                    try:
                        stream.transition_to(StreamState.STOPPED, reason="EMERGENCY_STOP_ACTIVATED")
                    except Exception:
                        stream.state = StreamState.STOPPED
                    stream.queue.clear()

    def create_stream(
        self,
        device_id: str,
        session_id: str,
        target_fps: float = DEFAULT_FPS,
        max_width: int = MAX_FRAME_WIDTH,
        max_height: int = MAX_FRAME_HEIGHT,
        encoding: FrameEncoding = FrameEncoding.JPEG,
    ) -> Tuple[bool, str, Optional[StreamSession]]:
        """
        Request and create a new bounded screen streaming session.
        """
        # 1. Emergency stop check
        if self.emergency_stop.is_active():
            return False, "EMERGENCY_STOP_ACTIVE: Cannot start screen stream while emergency stop is active", None

        # 2. Bounded parameters validation
        clamped_fps = max(MIN_FRAMES_PER_SECOND, min(float(target_fps), MAX_FRAMES_PER_SECOND))
        clamped_w = max(160, min(int(max_width), MAX_FRAME_WIDTH))
        clamped_h = max(120, min(int(max_height), MAX_FRAME_HEIGHT))

        with self._lock:
            # 3. Clean expired or stopped streams
            self._cleanup_streams_locked()

            # 4. Global concurrency check
            active_global = [s for s in self._streams.values() if s.state in (StreamState.STARTING, StreamState.ACTIVE, StreamState.PAUSED)]
            if len(active_global) >= MAX_GLOBAL_CONCURRENT_STREAMS:
                return False, f"GLOBAL_STREAM_LIMIT_EXCEEDED: Maximum {MAX_GLOBAL_CONCURRENT_STREAMS} active streams", None

            # 5. Per-device concurrency check (strictly 1 active stream per device)
            device_active = [s for s in active_global if s.device_id == device_id]
            if len(device_active) >= MAX_CONCURRENT_STREAMS_PER_DEVICE:
                return False, f"DEVICE_STREAM_LIMIT_EXCEEDED: Device '{device_id}' already has an active stream", None

            stream_id = f"STRM-{secrets.token_hex(8).upper()}"
            stream = StreamSession(
                stream_id=stream_id,
                device_id=device_id,
                session_id=session_id,
                state=StreamState.IDLE,
                target_fps=clamped_fps,
                max_width=clamped_w,
                max_height=clamped_h,
                encoding=encoding,
            )

            # Transition IDLE -> STARTING -> ACTIVE
            stream.transition_to(StreamState.STARTING, reason="STREAM_INITIALIZING")
            stream.transition_to(StreamState.ACTIVE, reason="STREAM_ACTIVE")

            self._streams[stream_id] = stream
            return True, "STREAM_CREATED", stream

    def produce_frame(self, stream_id: str) -> Tuple[bool, str, Optional[StreamFrame]]:
        """
        Capture a frame using the configured engine and enqueue it under backpressure rules.
        """
        if self.emergency_stop.is_active():
            return False, "EMERGENCY_STOP_ACTIVE", None

        with self._lock:
            stream = self._streams.get(stream_id)
            if not stream:
                return False, "STREAM_NOT_FOUND", None

            if stream.state != StreamState.ACTIVE:
                return False, f"STREAM_NOT_ACTIVE: Current state is {stream.state.value}", None

            if stream.is_expired():
                stream.transition_to(StreamState.STOPPED, reason="STREAM_LIFETIME_EXPIRED")
                return False, "STREAM_EXPIRED", None

            stream.frame_sequence += 1
            seq = stream.frame_sequence

        # Capture frame outside lock to minimize contention
        success, msg, frame = self.capture_engine.capture_frame(
            stream_id=stream.stream_id,
            sequence_number=seq,
            max_width=stream.max_width,
            max_height=stream.max_height,
            encoding=stream.encoding,
        )

        if not success or not frame:
            return False, f"CAPTURE_FAILED: {msg}", None

        # Push to bounded queue
        q_ok, q_msg = stream.queue.push(frame)
        if not q_ok:
            return False, q_msg, None

        return True, "FRAME_PRODUCED", frame

    def get_next_frame(self, stream_id: str, session_id: str) -> Tuple[bool, str, Optional[StreamFrame]]:
        """
        Pop the next frame from the stream's queue for transmission to the phone.
        Enforces stream ownership and session matching.
        """
        with self._lock:
            stream = self._streams.get(stream_id)
            if not stream:
                return False, "STREAM_NOT_FOUND", None

            if stream.session_id != session_id:
                return False, "STREAM_OWNERSHIP_MISMATCH", None

            if stream.state == StreamState.PAUSED:
                return False, "STREAM_PAUSED", None

            if stream.state not in (StreamState.ACTIVE, StreamState.RECONNECTING):
                return False, f"STREAM_INACTIVE: {stream.state.value}", None

            stream.touch()
            if stream.state == StreamState.RECONNECTING:
                # Successfully resumed reception
                stream.transition_to(StreamState.ACTIVE, reason="CONNECTION_RESUMED")

        frame = stream.queue.pop()
        if not frame:
            return False, "NO_FRAMES_IN_QUEUE", None

        with self._lock:
            stream.total_frames_sent += 1

        return True, "FRAME_RETRIEVED", frame

    def pause_stream(self, stream_id: str, session_id: str) -> Tuple[bool, str]:
        with self._lock:
            stream = self._streams.get(stream_id)
            if not stream or stream.session_id != session_id:
                return False, "STREAM_NOT_FOUND_OR_FORBIDDEN"
            try:
                stream.transition_to(StreamState.PAUSED, reason="USER_REQUESTED_PAUSE")
                stream.queue.clear()
                return True, "STREAM_PAUSED"
            except StreamStateError as e:
                return False, str(e)

    def resume_stream(self, stream_id: str, session_id: str) -> Tuple[bool, str]:
        if self.emergency_stop.is_active():
            return False, "EMERGENCY_STOP_ACTIVE"
        with self._lock:
            stream = self._streams.get(stream_id)
            if not stream or stream.session_id != session_id:
                return False, "STREAM_NOT_FOUND_OR_FORBIDDEN"
            try:
                stream.transition_to(StreamState.ACTIVE, reason="USER_REQUESTED_RESUME")
                stream.touch()
                return True, "STREAM_RESUMED"
            except StreamStateError as e:
                return False, str(e)

    def stop_stream(self, stream_id: str, session_id: str, reason: str = "USER_REQUESTED_STOP") -> Tuple[bool, str]:
        with self._lock:
            stream = self._streams.get(stream_id)
            if not stream or stream.session_id != session_id:
                return False, "STREAM_NOT_FOUND_OR_FORBIDDEN"
            try:
                stream.transition_to(StreamState.STOPPING, reason=reason)
                stream.transition_to(StreamState.STOPPED, reason=reason)
                stream.queue.clear()
                return True, "STREAM_STOPPED"
            except StreamStateError as e:
                stream.state = StreamState.STOPPED
                stream.queue.clear()
                return True, "STREAM_STOPPED"

    def handle_connection_loss(self, stream_id: str) -> Tuple[bool, str]:
        """
        Mark a stream as reconnecting when connection loss is detected.
        """
        with self._lock:
            stream = self._streams.get(stream_id)
            if not stream:
                return False, "STREAM_NOT_FOUND"

            if stream.reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
                stream.transition_to(StreamState.STOPPED, reason="MAX_RECONNECT_ATTEMPTS_EXCEEDED")
                stream.queue.clear()
                return False, "STREAM_TERMINATED_RECONNECTS_EXHAUSTED"

            stream.reconnect_attempts += 1
            try:
                stream.transition_to(StreamState.RECONNECTING, reason="CONNECTION_LOST_PENDING_RECONNECT")
                stream.queue.clear()
                return True, "STREAM_MARKED_RECONNECTING"
            except StreamStateError as e:
                return False, str(e)

    def reconnect_stream(self, stream_id: str, session_id: str) -> Tuple[bool, str]:
        """
        Reconnect an active or reconnecting stream.
        """
        if self.emergency_stop.is_active():
            return False, "EMERGENCY_STOP_ACTIVE"
        with self._lock:
            stream = self._streams.get(stream_id)
            if not stream or stream.session_id != session_id:
                return False, "STREAM_NOT_FOUND_OR_FORBIDDEN"

            if stream.reconnect_attempts >= MAX_RECONNECT_ATTEMPTS:
                try:
                    stream.transition_to(StreamState.FAILED, reason="MAX_RECONNECTS_EXCEEDED")
                except Exception:
                    stream.state = StreamState.FAILED
                return False, "Maximum reconnect attempts exceeded"

            try:
                stream.reconnect_attempts += 1
                stream.transition_to(StreamState.ACTIVE, reason="RECONNECTED")
                stream.touch()
                return True, "STREAM_RECONNECTED"
            except StreamStateError as e:
                return False, str(e)

    def get_stream(self, stream_id: str) -> Optional[StreamSession]:
        with self._lock:
            return self._streams.get(stream_id)

    def on_emergency_stop(self) -> None:
        """Public trigger for emergency stop shutdown of all active streams."""
        self._on_emergency_stop()

    def _cleanup_streams_locked(self, current_time: Optional[float] = None) -> int:
        now = current_time if current_time is not None else time.time()
        to_remove = []
        for sid, s in self._streams.items():
            if s.state in (StreamState.STOPPED, StreamState.FAILED):
                to_remove.append(sid)
            elif s.is_expired(now):
                s.state = StreamState.STOPPED
                s.queue.clear()
                to_remove.append(sid)
            elif s.check_inactivity(now):
                s.state = StreamState.STOPPED
                s.queue.clear()
                to_remove.append(sid)

        for sid in to_remove:
            del self._streams[sid]
        return len(to_remove)

    def cleanup_streams(self, current_time: Optional[float] = None) -> int:
        with self._lock:
            return self._cleanup_streams_locked(current_time=current_time)

    def cleanup_inactive_streams(self, current_time: Optional[float] = None) -> int:
        return self.cleanup_streams(current_time=current_time)
