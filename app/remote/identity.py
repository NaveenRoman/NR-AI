"""
NR-AI Device & PC Identity and Pairing State Machine.
Step 10 Phase 1 — Secure Phone <-> PC Communication Foundation.
"""

from dataclasses import dataclass, field
from enum import Enum
import hashlib
import hmac
import os
import secrets
import socket
import threading
import time
from typing import Dict, List, Optional, Tuple

from app.remote.config import (
    DEVICE_SECRET_BYTES,
    MAX_PAIRING_ATTEMPTS,
    PAIRING_CODE_LENGTH,
    PAIRING_CODE_TTL_SECONDS,
)


class PairingState(str, Enum):
    UNPAIRED = "UNPAIRED"
    PENDING = "PENDING"
    PAIRED = "PAIRED"
    REVOKED = "REVOKED"


@dataclass
class DeviceIdentity:
    device_id: str
    device_name: str
    platform: str
    app_version: str
    fingerprint: str
    state: PairingState = PairingState.UNPAIRED
    paired_at: Optional[float] = None
    last_seen_at: Optional[float] = None
    shared_secret_hash: Optional[str] = None  # SHA-256 hash of shared secret
    metadata: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, any]:
        return {
            "device_id": self.device_id,
            "device_name": self.device_name,
            "platform": self.platform,
            "app_version": self.app_version,
            "fingerprint": self.fingerprint,
            "state": self.state.value,
            "paired_at": self.paired_at,
            "last_seen_at": self.last_seen_at,
            "metadata": dict(self.metadata),
        }


@dataclass
class PCIdentity:
    pc_id: str
    hostname: str
    service_name: str = "NR-AI Companion Backend"
    version: str = "1.0.0"

    @classmethod
    def get_local_identity(cls) -> "PCIdentity":
        hostname = socket.gethostname() or "localhost"
        # Deterministic PC identity based on machine-specific entropy
        raw_seed = f"NRAI-PC:{hostname}".encode("utf-8")
        pc_id = "PC-" + hashlib.sha256(raw_seed).hexdigest()[:16].upper()
        return cls(pc_id=pc_id, hostname=hostname)

    def to_dict(self) -> Dict[str, str]:
        return {
            "pc_id": self.pc_id,
            "hostname": self.hostname,
            "service_name": self.service_name,
            "version": self.version,
        }


@dataclass
class ActivePairingCode:
    code: str
    created_at: float
    expires_at: float
    attempts_remaining: int
    target_device_id: Optional[str] = None

    def is_valid(self) -> bool:
        return time.time() < self.expires_at and self.attempts_remaining > 0


class PairingManager:
    """
    Manages deterministic device registration, explicit pairing code verification,
    device states, shared secret generation, and device revocation.
    """

    def __init__(self, pc_identity: Optional[PCIdentity] = None):
        self.pc_identity = pc_identity or PCIdentity.get_local_identity()
        self._devices: Dict[str, DeviceIdentity] = {}
        # In-memory store of shared secrets (hex strings keyed by device_id)
        # Note: Secrets are never exposed via APIs or serialized to disk/logs
        self._device_secrets: Dict[str, str] = {}
        self._active_code: Optional[ActivePairingCode] = None
        self._lock = threading.Lock()

    def generate_pairing_code(self, target_device_id: Optional[str] = None) -> str:
        """
        Generate a cryptographically secure numeric OTP code with bounded TTL.
        """
        with self._lock:
            # Generate 6-digit zero-padded OTP using secure secrets module
            num = secrets.randbelow(10 ** PAIRING_CODE_LENGTH)
            code = f"{num:0{PAIRING_CODE_LENGTH}d}"
            now = time.time()
            self._active_code = ActivePairingCode(
                code=code,
                created_at=now,
                expires_at=now + PAIRING_CODE_TTL_SECONDS,
                attempts_remaining=MAX_PAIRING_ATTEMPTS,
                target_device_id=target_device_id,
            )
            return code

    def register_device(
        self,
        device_id: str,
        device_name: str,
        platform: str,
        app_version: str,
        fingerprint: Optional[str] = None,
    ) -> DeviceIdentity:
        """
        Register or update device record in UNPAIRED state.
        """
        with self._lock:
            if not device_id or not device_id.strip():
                raise ValueError("Device ID cannot be empty.")

            existing = self._devices.get(device_id)
            if existing and existing.state == PairingState.REVOKED:
                # Revoked devices cannot simply re-register without explicit un-revocation
                return existing

            calc_fp = fingerprint or hashlib.sha256(
                f"{device_id}:{platform}:{device_name}".encode("utf-8")
            ).hexdigest()

            dev = DeviceIdentity(
                device_id=device_id.strip(),
                device_name=device_name.strip(),
                platform=platform.strip(),
                app_version=app_version.strip(),
                fingerprint=calc_fp,
                state=existing.state if existing else PairingState.UNPAIRED,
                paired_at=existing.paired_at if existing else None,
                last_seen_at=time.time(),
                shared_secret_hash=existing.shared_secret_hash if existing else None,
            )
            self._devices[device_id] = dev
            return dev

    def confirm_pairing(
        self,
        device_id: str,
        pairing_code: str,
        device_name: str = "Android Companion",
        platform: str = "Android",
        app_version: str = "1.0",
    ) -> Tuple[bool, str, Optional[str]]:
        """
        Verify pairing code and establish paired state.
        Returns (success, message, shared_secret_hex).
        """
        with self._lock:
            if not self._active_code:
                return False, "NO_ACTIVE_PAIRING_CODE", None

            if not self._active_code.is_valid():
                self._active_code = None
                return False, "PAIRING_CODE_EXPIRED_OR_EXHAUSTED", None

            if self._active_code.target_device_id and self._active_code.target_device_id != device_id:
                return False, "DEVICE_ID_MISMATCH", None

            # Constant-time comparison
            if not hmac.compare_digest(self._active_code.code, pairing_code.strip()):
                self._active_code.attempts_remaining -= 1
                if self._active_code.attempts_remaining <= 0:
                    self._active_code = None
                return False, "INVALID_PAIRING_CODE", None

            # Code verified! Expire it immediately to prevent reuse
            self._active_code = None

            # Check if device is revoked
            dev = self._devices.get(device_id)
            if dev and dev.state == PairingState.REVOKED:
                return False, "DEVICE_REVOKED", None

            # Generate fresh cryptographic shared secret for this device
            secret_hex = secrets.token_hex(DEVICE_SECRET_BYTES)
            secret_hash = hashlib.sha256(secret_hex.encode("utf-8")).hexdigest()

            now = time.time()
            if not dev:
                calc_fp = hashlib.sha256(f"{device_id}:{platform}:{device_name}".encode("utf-8")).hexdigest()
                dev = DeviceIdentity(
                    device_id=device_id,
                    device_name=device_name,
                    platform=platform,
                    app_version=app_version,
                    fingerprint=calc_fp,
                )
                self._devices[device_id] = dev

            dev.state = PairingState.PAIRED
            dev.paired_at = now
            dev.last_seen_at = now
            dev.shared_secret_hash = secret_hash
            self._device_secrets[device_id] = secret_hex

            return True, "PAIRING_SUCCESS", secret_hex

    def revoke_device(self, device_id: str, reason: str = "MANUAL_REVOCATION") -> bool:
        """
        Explicitly revoke a device. Revocation invalidates shared secrets immediately.
        """
        with self._lock:
            dev = self._devices.get(device_id)
            if not dev:
                # Still create a revoked record so the ID cannot connect
                dev = DeviceIdentity(
                    device_id=device_id,
                    device_name="Unknown",
                    platform="Unknown",
                    app_version="Unknown",
                    fingerprint="",
                    state=PairingState.REVOKED,
                    metadata={"revoked_reason": reason, "revoked_at": str(time.time())},
                )
                self._devices[device_id] = dev
            else:
                dev.state = PairingState.REVOKED
                dev.metadata["revoked_reason"] = reason
                dev.metadata["revoked_at"] = str(time.time())

            # Delete active secret
            self._device_secrets.pop(device_id, None)
            return True

    def is_device_paired(self, device_id: str) -> bool:
        with self._lock:
            dev = self._devices.get(device_id)
            return bool(dev and dev.state == PairingState.PAIRED)

    def is_device_revoked(self, device_id: str) -> bool:
        with self._lock:
            dev = self._devices.get(device_id)
            return bool(dev and dev.state == PairingState.REVOKED)

    def get_device(self, device_id: str) -> Optional[DeviceIdentity]:
        with self._lock:
            return self._devices.get(device_id)

    def get_device_secret(self, device_id: str) -> Optional[str]:
        with self._lock:
            dev = self._devices.get(device_id)
            if not dev or dev.state != PairingState.PAIRED:
                return None
            return self._device_secrets.get(device_id)

    def rotate_device_secret(self, device_id: str) -> Optional[str]:
        """
        Rotate shared secret for an existing paired device.
        """
        with self._lock:
            dev = self._devices.get(device_id)
            if not dev or dev.state != PairingState.PAIRED:
                return None
            new_secret_hex = secrets.token_hex(DEVICE_SECRET_BYTES)
            dev.shared_secret_hash = hashlib.sha256(new_secret_hex.encode("utf-8")).hexdigest()
            self._device_secrets[device_id] = new_secret_hex
            return new_secret_hex
