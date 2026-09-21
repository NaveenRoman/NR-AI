"""
NR-AI Local File Read-Only Connector.
Safe provider reading allowed workspace files with path traversal defense,
secret file blocking, and output sanitization.
"""

import os
from pathlib import Path
from typing import Any, Dict, Optional

from app.connectors.base import (
    AuthState,
    BaseConnector,
    ConnectorConfig,
    ConnectorResponse,
    PrivacyClassification,
)
from app.security.guardrails import PromptGuardrails
from app.task.checkpoint_store import sanitize_secrets


PROHIBITED_FILE_PATTERNS = [
    ".env", ".env.", "id_rsa", "id_ed25519", ".pem", ".key", ".pfx",
    "credentials", "secrets", "tokens", "private_key"
]


class LocalFileConnector(BaseConnector):
    """
    Read-only local file connector.
    Guarantees strict containment within allowed root directory.
    """

    def __init__(self, root_dir: Optional[Path] = None, connector_id: str = "local_file_connector"):
        self.root_dir = Path(root_dir or r"C:\NR-AI").resolve()
        config = ConnectorConfig(
            connector_id=connector_id,
            provider="local_filesystem",
            capabilities=["file:read", "file:list"],
            data_scopes=["workspace:read"],
            authentication_state=AuthState.CONFIGURED,
            read_only=True,
            privacy_classification=PrivacyClassification.INTERNAL,
        )
        super().__init__(config)

    def read(self, query: str, params: Optional[Dict[str, Any]] = None) -> ConnectorResponse:
        """
        Read file contents safely from local workspace.
        `query` represents the relative or absolute path to read.
        """
        raw_path = query.strip()
        if not raw_path:
            return ConnectorResponse(
                success=False,
                error="EMPTY_PATH: No file path provided.",
                status_code="INVALID_ARGUMENT",
            )

        # 1. Path Traversal & Prohibited File Check
        low_path = raw_path.lower()
        if ".." in raw_path or any(p in low_path for p in PROHIBITED_FILE_PATTERNS):
            return ConnectorResponse(
                success=False,
                error=f"ACCESS_DENIED: Access to path '{raw_path}' is prohibited by safety policy.",
                status_code="SECURITY_VIOLATION",
            )

        target_path = (self.root_dir / raw_path).resolve()

        # 2. Boundary Check: Must reside inside root_dir
        try:
            target_path.relative_to(self.root_dir)
        except ValueError:
            return ConnectorResponse(
                success=False,
                error=f"WORKSPACE_VIOLATION: Path '{raw_path}' escapes authorized workspace root.",
                status_code="BOUNDARY_VIOLATION",
            )

        if not target_path.exists():
            return ConnectorResponse(
                success=False,
                error=f"FILE_NOT_FOUND: File '{raw_path}' does not exist.",
                status_code="NOT_FOUND",
            )

        if not target_path.is_file():
            return ConnectorResponse(
                success=False,
                error=f"NOT_A_FILE: Path '{raw_path}' is not a regular file.",
                status_code="INVALID_TARGET",
            )

        # 3. Read & Sanitize (size bounded to 64KB)
        try:
            max_bytes = 65536
            with open(target_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read(max_bytes)

            scrubbed = sanitize_secrets(content)
            if isinstance(scrubbed, str):
                guardrail_res = PromptGuardrails.sanitize_text(scrubbed)
                final_content = guardrail_res.sanitized_text
            else:
                final_content = str(scrubbed)

            return ConnectorResponse(
                success=True,
                data=final_content,
                status_code="OK",
                provenance=str(target_path),
            )
        except Exception as ex:
            return ConnectorResponse(
                success=False,
                error=f"READ_ERROR: {str(ex)}",
                status_code="IO_ERROR",
            )
