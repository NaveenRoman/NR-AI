"""
NR-AI Connector Registry.
Coordinates data connectors, enforces deterministic unconfigured fallbacks,
and handles explicit user authorization requirements.
"""

import logging
import threading
import time
from typing import Any, Dict, List, Optional

from app.connectors.base import (
    AuthState,
    BaseConnector,
    ConnectorConfig,
    ConnectorResponse,
)

logger = logging.getLogger("NRAI.Connectors.Registry")


class ConnectorRegistry:
    """
    Thread-safe registry for personal and data connectors.
    Never fabricates accounts or bypasses authorization.
    """

    def __init__(self):
        self._connectors: Dict[str, BaseConnector] = {}
        self._lock = threading.RLock()
        self._audit_trail: List[Dict[str, Any]] = []

    def register_connector(self, connector: BaseConnector) -> None:
        """Register a configured connector."""
        with self._lock:
            self._connectors[connector.config.connector_id] = connector
            self._record_audit("CONNECTOR_REGISTERED", connector.config.connector_id, {"provider": connector.config.provider})

    def unregister_connector(self, connector_id: str) -> bool:
        """Remove a connector from registry."""
        with self._lock:
            if connector_id in self._connectors:
                del self._connectors[connector_id]
                self._record_audit("CONNECTOR_UNREGISTERED", connector_id, {})
                return True
        return False

    def get_connector(self, connector_id: str) -> Optional[BaseConnector]:
        """Fetch connector instance."""
        with self._lock:
            return self._connectors.get(connector_id)

    def list_connectors(self) -> List[Dict[str, Any]]:
        """List metadata of all registered connectors."""
        with self._lock:
            return [c.config.to_dict() for c in self._connectors.values()]

    def execute_read(
        self,
        connector_id: str,
        query: str,
        params: Optional[Dict[str, Any]] = None,
        caller_id: str = "system",
    ) -> ConnectorResponse:
        """
        Execute read query against a connector with deterministic safety and status checks.
        """
        with self._lock:
            connector = self._connectors.get(connector_id)

        # 1. Deterministic Unconfigured Fallback
        if not connector:
            self._record_audit("CONNECTOR_ACCESS_REJECTED", connector_id, {"caller": caller_id, "reason": "NOT_CONFIGURED"})
            return ConnectorResponse(
                success=False,
                error=f"CONNECTOR_NOT_CONFIGURED: Connector '{connector_id}' is not registered or configured.",
                status_code="CONNECTOR_NOT_CONFIGURED",
                provenance="local",
            )

        # 2. Check Enabled State
        if not connector.config.enabled:
            self._record_audit("CONNECTOR_ACCESS_REJECTED", connector_id, {"caller": caller_id, "reason": "DISABLED"})
            return ConnectorResponse(
                success=False,
                error=f"CONNECTOR_DISABLED: Connector '{connector_id}' is currently disabled.",
                status_code="DISABLED",
                provenance=connector.config.provider,
            )

        # 3. Check Authentication / Authorization State
        if connector.config.authentication_state == AuthState.REQUIRES_AUTH:
            self._record_audit("AUTH_REQUIRED", connector_id, {"caller": caller_id, "provider": connector.config.provider})
            return ConnectorResponse(
                success=False,
                error=(
                    f"AUTHORIZATION_REQUIRED: Connector '{connector_id}' requires explicit user authorization "
                    f"for provider '{connector.config.provider}'. Automated access halted."
                ),
                status_code="REQUIRES_AUTH",
                provenance=connector.config.provider,
            )

        if connector.config.authentication_state == AuthState.UNCONFIGURED:
            self._record_audit("UNCONFIGURED", connector_id, {"caller": caller_id})
            return ConnectorResponse(
                success=False,
                error=f"CONNECTOR_NOT_CONFIGURED: Credentials or configuration missing for '{connector_id}'.",
                status_code="CONNECTOR_NOT_CONFIGURED",
                provenance=connector.config.provider,
            )

        # 4. Execute Read under Read-Only Guarantee
        try:
            res = connector.read(query=query, params=params)
            self._record_audit("READ_SUCCESS", connector_id, {"caller": caller_id, "success": res.success})
            return res
        except Exception as ex:
            logger.error(f"Connector '{connector_id}' execution failure: {ex}", exc_info=True)
            self._record_audit("EXECUTION_ERROR", connector_id, {"caller": caller_id, "error": str(ex)})
            return ConnectorResponse(
                success=False,
                error=f"CONNECTOR_EXECUTION_FAILURE: {str(ex)}",
                status_code="ERROR",
                provenance=connector.config.provider,
            )

    def _record_audit(self, event: str, connector_id: str, details: Dict[str, Any]):
        entry = {
            "timestamp": time.time(),
            "event": event,
            "connector_id": connector_id,
            "details": details,
        }
        with self._lock:
            self._audit_trail.append(entry)
            if len(self._audit_trail) > 1000:
                self._audit_trail.pop(0)

    def get_audit_trail(self, limit: int = 50) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._audit_trail[-limit:])


# Global singleton registry
global_connector_registry = ConnectorRegistry()
