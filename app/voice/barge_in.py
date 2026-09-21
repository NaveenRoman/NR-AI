"""
Barge-In / Interruption Controller for NR-AI.
OpenJarvis-inspired non-blocking playback interruption and speech onset capture.

Key Invariants:
- Instant playback cancellation: No blocking sounddevice.wait() or un-cancellable loops.
- Speech onset preservation: Audio chunks that caused the interruption are retained
  and passed into the next input pipeline stage so the user does not have to repeat themselves.
- Thread-safe synchronization: Uses threading.Event and non-blocking locks.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable, List, Optional

logger = logging.getLogger("NRAI.Voice.BargeIn")


class BargeInController:
    """
    Coordinates real-time interruption of assistant speech when user begins speaking.
    """

    def __init__(
        self,
        barge_in_threshold: float = 600.0,
        stop_playback_callback: Optional[Callable[[], None]] = None,
    ):
        self.barge_in_threshold = barge_in_threshold
        self.stop_playback_callback = stop_playback_callback

        self._is_speaking: bool = False
        self._interrupted: threading.Event = threading.Event()
        self._lock: threading.Lock = threading.Lock()
        self._captured_onset_audio: List[bytes] = []
        self._interruption_count: int = 0
        self._last_interrupted_at: float = 0.0

    @property
    def is_speaking(self) -> bool:
        with self._lock:
            return self._is_speaking

    @property
    def was_interrupted(self) -> bool:
        return self._interrupted.is_set()

    @property
    def interruption_count(self) -> int:
        return self._interruption_count

    def start_speaking(self) -> None:
        """Flag that TTS playback has commenced."""
        with self._lock:
            self._is_speaking = True
            self._interrupted.clear()
            self._captured_onset_audio.clear()
            logger.debug("BargeInController: Speech playback started.")

    def stop_speaking(self) -> None:
        """Flag that TTS playback has concluded or stopped."""
        with self._lock:
            self._is_speaking = False
            logger.debug("BargeInController: Speech playback stopped.")

    def trigger_interruption(self, reason: str = "speech_onset", onset_chunk: Optional[bytes] = None) -> bool:
        """
        Immediately interrupt ongoing speech playback.
        Returns True if an active playback was interrupted, False otherwise.
        """
        with self._lock:
            if not self._is_speaking:
                return False

            self._interrupted.set()
            self._is_speaking = False
            self._interruption_count += 1
            self._last_interrupted_at = time.perf_counter()

            if onset_chunk:
                self._captured_onset_audio.append(onset_chunk)

            logger.info("Barge-in triggered (reason='%s'). Halting playback.", reason)

        # Call playback termination callback outside of lock to avoid deadlocks
        if self.stop_playback_callback:
            try:
                self.stop_playback_callback()
            except Exception as exc:
                logger.warning("Error executing stop_playback_callback: %s", exc)

        return True

    def evaluate_incoming_chunk(self, chunk_bytes: bytes, chunk_energy: float) -> bool:
        """
        Evaluate an incoming microphone audio chunk while speaking.
        If chunk energy exceeds barge-in threshold, triggers interruption and stores chunk.
        """
        if not self.is_speaking:
            return False

        if chunk_energy >= self.barge_in_threshold:
            return self.trigger_interruption(reason=f"energy_trip_{chunk_energy:.1f}", onset_chunk=chunk_bytes)

        return False

    def get_onset_audio(self) -> bytes:
        """Retrieve any audio chunks captured during the interruption onset."""
        with self._lock:
            return b"".join(self._captured_onset_audio)

    def reset(self) -> None:
        """Reset interruption state."""
        with self._lock:
            self._is_speaking = False
            self._interrupted.clear()
            self._captured_onset_audio.clear()
