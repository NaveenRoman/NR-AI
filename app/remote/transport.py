"""
NR-AI Secure Transport Abstraction.
Step 10 Phase 1 — Secure Phone <-> PC Communication Foundation.

Supports both DEVELOPMENT_MOCK (for unit testing fixtures with clear transparency)
and ENCRYPTED_SESSION (production-grade AES-256-GCM authenticated encryption).
"""

from dataclasses import dataclass
from enum import Enum
import json
import secrets
from typing import Dict, Optional

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.remote.config import AES_GCM_NONCE_BYTES, AES_GCM_TAG_BYTES, SESSION_KEY_BYTES


class TransportSecurityMode(str, Enum):
    DEVELOPMENT_MOCK = "DEVELOPMENT_MOCK"
    ENCRYPTED_SESSION = "ENCRYPTED_SESSION"


class TransportIntegrityError(Exception):
    """Raised when transport decryption or authentication tag verification fails."""
    pass


class TransportModeError(Exception):
    """Raised when transport mode mismatch occurs."""
    pass


@dataclass
class EncryptedPacket:
    mode: str
    iv_hex: str
    ciphertext_hex: str
    tag_hex: str
    associated_data: str = ""

    def to_dict(self) -> Dict[str, str]:
        return {
            "mode": self.mode,
            "iv_hex": self.iv_hex,
            "ciphertext_hex": self.ciphertext_hex,
            "tag_hex": self.tag_hex,
            "associated_data": self.associated_data,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: Dict[str, any]) -> "EncryptedPacket":
        return cls(
            mode=str(data.get("mode", "")),
            iv_hex=str(data.get("iv_hex", "")),
            ciphertext_hex=str(data.get("ciphertext_hex", "")),
            tag_hex=str(data.get("tag_hex", "")),
            associated_data=str(data.get("associated_data", "")),
        )


class SecureTransport:
    """
    Authenticated transport layer providing AES-256-GCM encryption or explicit Mock mode.
    """

    def __init__(self, mode: TransportSecurityMode = TransportSecurityMode.ENCRYPTED_SESSION):
        self.mode = mode

    def is_encrypted(self) -> bool:
        """Returns True ONLY if using real cryptographic encryption."""
        return self.mode == TransportSecurityMode.ENCRYPTED_SESSION

    def encrypt(
        self,
        plaintext: bytes,
        key_bytes: bytes,
        associated_data: Optional[bytes] = None,
    ) -> EncryptedPacket:
        """
        Encrypt plaintext bytes using the configured transport mode.
        """
        if not isinstance(plaintext, (bytes, bytearray)):
            raise TypeError("Plaintext must be bytes.")

        if self.mode == TransportSecurityMode.DEVELOPMENT_MOCK:
            # Plaintext mock for local test fixtures; explicitly labeled
            return EncryptedPacket(
                mode=TransportSecurityMode.DEVELOPMENT_MOCK.value,
                iv_hex="",
                ciphertext_hex=bytes(plaintext).hex(),
                tag_hex="",
                associated_data=(associated_data or b"").decode("utf-8", errors="replace"),
            )

        # ENCRYPTED_SESSION: Production AES-256-GCM
        if len(key_bytes) != SESSION_KEY_BYTES:
            raise ValueError(f"AES-256-GCM requires a {SESSION_KEY_BYTES}-byte key, got {len(key_bytes)}")

        iv = secrets.token_bytes(AES_GCM_NONCE_BYTES)
        aesgcm = AESGCM(key_bytes)
        ad = associated_data if associated_data is not None else b""
        
        # cryptography's AESGCM.encrypt returns ciphertext + 16-byte tag appended
        full_cipher = aesgcm.encrypt(iv, plaintext, ad)
        ciphertext = full_cipher[:-AES_GCM_TAG_BYTES]
        tag = full_cipher[-AES_GCM_TAG_BYTES:]

        return EncryptedPacket(
            mode=TransportSecurityMode.ENCRYPTED_SESSION.value,
            iv_hex=iv.hex(),
            ciphertext_hex=ciphertext.hex(),
            tag_hex=tag.hex(),
            associated_data=ad.decode("utf-8", errors="replace"),
        )

    def decrypt(
        self,
        packet: EncryptedPacket,
        key_bytes: bytes,
        associated_data: Optional[bytes] = None,
    ) -> bytes:
        """
        Decrypt an EncryptedPacket using the configured transport mode.
        """
        if packet.mode == TransportSecurityMode.DEVELOPMENT_MOCK.value:
            if self.mode == TransportSecurityMode.ENCRYPTED_SESSION:
                raise TransportModeError(
                    "Rejected DEVELOPMENT_MOCK packet on an ENCRYPTED_SESSION transport."
                )
            # Decode mock packet
            return bytes.fromhex(packet.ciphertext_hex)

        if packet.mode == TransportSecurityMode.ENCRYPTED_SESSION.value:
            if len(key_bytes) != SESSION_KEY_BYTES:
                raise ValueError(f"AES-256-GCM requires a {SESSION_KEY_BYTES}-byte key, got {len(key_bytes)}")

            try:
                iv = bytes.fromhex(packet.iv_hex)
                ciphertext = bytes.fromhex(packet.ciphertext_hex)
                tag = bytes.fromhex(packet.tag_hex)
            except ValueError as e:
                raise TransportIntegrityError(f"Malformed packet hex encoding: {e}")

            if len(iv) != AES_GCM_NONCE_BYTES:
                raise TransportIntegrityError(f"Invalid IV length: {len(iv)} bytes")
            if len(tag) != AES_GCM_TAG_BYTES:
                raise TransportIntegrityError(f"Invalid tag length: {len(tag)} bytes")

            full_cipher = ciphertext + tag
            ad = (
                associated_data
                if associated_data is not None
                else packet.associated_data.encode("utf-8")
            )

            aesgcm = AESGCM(key_bytes)
            try:
                plaintext = aesgcm.decrypt(iv, full_cipher, ad)
                return plaintext
            except InvalidTag:
                raise TransportIntegrityError("AES-GCM authentication tag verification failed. Data was tampered with.")
            except Exception as e:
                raise TransportIntegrityError(f"Decryption failed: {e}")

        raise TransportModeError(f"Unknown transport packet mode: '{packet.mode}'")
