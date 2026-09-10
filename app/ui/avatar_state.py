"""
NR AI Visual Avatar State Interface.

Provides a decoupled state model and event pipeline for visual avatar representation.
Enables future 2D web avatars, animated faces, Unity humanoid models, and Unreal MetaHumans
to subscribe and render synchronized facial expressions, eye gazes, mouth phonemes,
and body states without modifying the companion or orchestration logic.
"""

from dataclasses import asdict, dataclass, field
from enum import Enum
import json
import logging
import threading
import time
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("NRAI.AvatarState")


class AvatarMode(str, Enum):
    IDLE = "IDLE"
    WAITING_FOR_WAKE_WORD = "WAITING FOR WAKE WORD"
    WAKE_WORD_DETECTED = "WAKE WORD DETECTED"
    LISTENING = "LISTENING"
    THINKING = "THINKING"
    WORKING = "WORKING"
    SPEAKING = "SPEAKING"
    ERROR = "ERROR"


class AvatarEmotion(str, Enum):
    NEUTRAL = "NEUTRAL"
    ATTENTIVE = "ATTENTIVE"
    THINKING = "THINKING"
    HAPPY = "HAPPY"
    CONCERNED = "CONCERNED"
    SPEAKING = "SPEAKING"


class EyeDirection(str, Enum):
    CENTER = "CENTER"
    UP = "UP"
    DOWN = "DOWN"
    LEFT = "LEFT"
    RIGHT = "RIGHT"


@dataclass
class AvatarFrame:
    """A discrete frame of avatar expression and pose data."""

    mode: AvatarMode = AvatarMode.IDLE
    emotion: AvatarEmotion = AvatarEmotion.NEUTRAL
    mouth_open_ratio: float = 0.0  # 0.0 (closed) to 1.0 (fully open)
    blink_active: bool = False
    eye_direction: EyeDirection = EyeDirection.CENTER
    head_tilt_angle: float = 0.0  # degrees (-30.0 to 30.0)
    energy_level: float = 0.5  # 0.0 to 1.0
    status_message: str = "Ready"
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "mode": self.mode.value,
            "emotion": self.emotion.value,
            "mouth_open_ratio": round(self.mouth_open_ratio, 3),
            "blink_active": self.blink_active,
            "eye_direction": self.eye_direction.value,
            "head_tilt_angle": round(self.head_tilt_angle, 1),
            "energy_level": round(self.energy_level, 2),
            "status_message": self.status_message,
            "timestamp": self.timestamp,
        }

    def to_unity_blendshapes(self) -> Dict[str, float]:
        """Maps frame state into standard Unity humanoid facial blendshapes (0-100)."""
        return {
            "JawOpen": self.mouth_open_ratio * 100.0,
            "EyeBlinkLeft": 100.0 if self.blink_active else 0.0,
            "EyeBlinkRight": 100.0 if self.blink_active else 0.0,
            "BrowInnerUp": 40.0 if self.mode == AvatarMode.THINKING else 0.0,
            "MouthSmile": 60.0 if self.emotion == AvatarEmotion.HAPPY else 10.0,
            "HeadPitch": -10.0 if self.mode == AvatarMode.THINKING else 0.0,
        }

    def to_unreal_livelink(self) -> Dict[str, Any]:
        """Maps frame state into Unreal Engine Live Link / ARKit face curves."""
        return {
            "SubjectName": "NRAI_Avatar",
            "FrameTime": self.timestamp,
            "Curves": {
                "jawOpen": self.mouth_open_ratio,
                "eyeBlinkLeft": 1.0 if self.blink_active else 0.0,
                "eyeBlinkRight": 1.0 if self.blink_active else 0.0,
                "browInnerUp": 0.5 if self.mode == AvatarMode.THINKING else 0.0,
                "mouthSmileLeft": 0.4 if self.emotion == AvatarEmotion.HAPPY else 0.0,
                "mouthSmileRight": 0.4 if self.emotion == AvatarEmotion.HAPPY else 0.0,
            },
        }


class AvatarStateManager:
    """
    Manages avatar states and dispatches updates to connected frontends or rendering engines.
    """

    def __init__(self):
        self._lock = threading.RLock()
        self._current_frame = AvatarFrame()
        self._subscribers: List[Callable[[AvatarFrame], None]] = []
        self._history: List[AvatarFrame] = [self._current_frame]

    @property
    def current_frame(self) -> AvatarFrame:
        with self._lock:
            return self._current_frame

    def subscribe(self, listener: Callable[[AvatarFrame], None]) -> None:
        """Subscribe a renderer, websocket, or callback to receive real-time avatar frames."""
        with self._lock:
            if listener not in self._subscribers:
                self._subscribers.append(listener)

    def unsubscribe(self, listener: Callable[[AvatarFrame], None]) -> None:
        with self._lock:
            if listener in self._subscribers:
                self._subscribers.remove(listener)

    def _update_and_notify(self, frame: AvatarFrame) -> None:
        with self._lock:
            self._current_frame = frame
            self._history.append(frame)
            if len(self._history) > 100:
                self._history = self._history[-100:]
            listeners = list(self._subscribers)

        for cb in listeners:
            try:
                cb(frame)
            except Exception as e:
                logger.error(f"Avatar subscriber error: {e}")

    # -------------------------------------------------------------------------
    # State Transition Triggers
    # -------------------------------------------------------------------------

    def set_idle(self, message: str = "Ready and waiting for command.") -> AvatarFrame:
        frame = AvatarFrame(
            mode=AvatarMode.IDLE,
            emotion=AvatarEmotion.NEUTRAL,
            mouth_open_ratio=0.0,
            blink_active=False,
            eye_direction=EyeDirection.CENTER,
            head_tilt_angle=0.0,
            energy_level=0.3,
            status_message=message,
        )
        self._update_and_notify(frame)
        return frame

    def set_waiting_wake(self, message: str = "Waiting for wake word (Hey NR)...") -> AvatarFrame:
        frame = AvatarFrame(
            mode=AvatarMode.WAITING_FOR_WAKE_WORD,
            emotion=AvatarEmotion.ATTENTIVE,
            mouth_open_ratio=0.0,
            blink_active=False,
            eye_direction=EyeDirection.CENTER,
            head_tilt_angle=2.0,
            energy_level=0.5,
            status_message=message,
        )
        self._update_and_notify(frame)
        return frame

    def set_wake_detected(self, message: str = "Wake word detected! Listening...") -> AvatarFrame:
        frame = AvatarFrame(
            mode=AvatarMode.WAKE_WORD_DETECTED,
            emotion=AvatarEmotion.HAPPY,
            mouth_open_ratio=0.3,
            blink_active=False,
            eye_direction=EyeDirection.CENTER,
            head_tilt_angle=3.0,
            energy_level=0.9,
            status_message=message,
        )
        self._update_and_notify(frame)
        return frame

    def set_working(self, message: str = "Executing autonomous task...") -> AvatarFrame:
        frame = AvatarFrame(
            mode=AvatarMode.WORKING,
            emotion=AvatarEmotion.THINKING,
            mouth_open_ratio=0.0,
            blink_active=False,
            eye_direction=EyeDirection.CENTER,
            head_tilt_angle=-2.0,
            energy_level=0.8,
            status_message=message,
        )
        self._update_and_notify(frame)
        return frame

    def set_listening(self, message: str = "Listening to voice input...") -> AvatarFrame:
        frame = AvatarFrame(
            mode=AvatarMode.LISTENING,
            emotion=AvatarEmotion.ATTENTIVE,
            mouth_open_ratio=0.0,
            blink_active=False,
            eye_direction=EyeDirection.CENTER,
            head_tilt_angle=4.0,  # slight attentive tilt
            energy_level=0.8,
            status_message=message,
        )
        self._update_and_notify(frame)
        return frame

    def set_thinking(self, message: str = "Analyzing request & reasoning...") -> AvatarFrame:
        frame = AvatarFrame(
            mode=AvatarMode.THINKING,
            emotion=AvatarEmotion.THINKING,
            mouth_open_ratio=0.0,
            blink_active=False,
            eye_direction=EyeDirection.UP,
            head_tilt_angle=-3.0,
            energy_level=0.7,
            status_message=message,
        )
        self._update_and_notify(frame)
        return frame

    def set_speaking(self, speech_text: str = "") -> AvatarFrame:
        frame = AvatarFrame(
            mode=AvatarMode.SPEAKING,
            emotion=AvatarEmotion.SPEAKING,
            mouth_open_ratio=0.6,
            blink_active=False,
            eye_direction=EyeDirection.CENTER,
            head_tilt_angle=1.5,
            energy_level=0.9,
            status_message=f"Speaking: {speech_text[:50]}...",
        )
        self._update_and_notify(frame)
        return frame

    def set_error(self, error_msg: str) -> AvatarFrame:
        frame = AvatarFrame(
            mode=AvatarMode.ERROR,
            emotion=AvatarEmotion.CONCERNED,
            mouth_open_ratio=0.0,
            blink_active=False,
            eye_direction=EyeDirection.DOWN,
            head_tilt_angle=0.0,
            energy_level=0.4,
            status_message=f"Error: {error_msg}",
        )
        self._update_and_notify(frame)
        return frame

    def get_history(self) -> List[Dict[str, Any]]:
        with self._lock:
            return [f.to_dict() for f in self._history]
