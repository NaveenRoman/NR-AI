"""
NR-AI Base Connector Specification & Security Model.
Defines abstract interfaces for data connectors with explicit scopes,
read-only enforcement, privacy tiers, and zero credential scraping.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
import time
from typing import Any, Dict, List, Optional, Set


class AuthState(str, Enum):
    """Authentication and pairing state for external/data connectors."""
    CONFIGURED = "CONFIGURED"
    UNCONFIGURED = "UNCONFIGURED"
    REQUIRES_AUTH = "REQUIRES_AUTH"


class PrivacyClassification(str, Enum):
    """Data sensitivity classification."""
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


@dataclass
class ConnectorConfig:
    """Explicit configuration and declaration of a connector."""
    connector_id: str
    provider: str
    capabilities: List[str] = field(default_factory=list)
    data_scopes: List[str] = field(default_factory=list)
    permissions: Set[str] = field(default_factory=set)
    authentication_state: AuthState = AuthState.UNCONFIGURED
    read_only: bool = True  # Strict Phase 2 invariant: read-only
    rate_limits: int = 60    # RPM
    privacy_classification: PrivacyClassification = PrivacyClassification.INTERNAL
    audit_policy: str = "STRICT"
    enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "connector_id": self.connector_id,
            "provider": self.provider,
            "capabilities": self.capabilities,
            "data_scopes": self.data_scopes,
            "permissions": sorted(list(self.permissions)),
            "authentication_state": self.authentication_state.value,
            "read_only": self.read_only,
            "rate_limits": self.rate_limits,
            "privacy_classification": self.privacy_classification.value,
            "audit_policy": self.audit_policy,
            "enabled": self.enabled,
            "metadata": self.metadata,
        }


@dataclass
class ConnectorResponse:
    """Standardized response from a data connector."""
    success: bool
    data: Any = None
    error: Optional[str] = None
    status_code: str = "OK"
    provenance: str = "local"
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "data": self.data,
            "error": self.error,
            "status_code": self.status_code,
            "provenance": self.provenance,
            "timestamp": self.timestamp,
        }


class BaseConnector(ABC):
    """
    Abstract base for all NR-AI data and personal connectors.
    Guarantees strict read-only execution, explicit authorization,
    zero cookie theft, and zero credential scraping.
    """

    def __init__(self, config: ConnectorConfig):
        self.config = config
        # Enforce Phase 2 read-only invariant
        self.config.read_only = True

    @abstractmethod
    def read(self, query: str, params: Optional[Dict[str, Any]] = None) -> ConnectorResponse:
        """Read data from the connector under configured scopes."""
        raise NotImplementedError

    def write(self, target: str, data: Any) -> ConnectorResponse:
        """Writing is strictly disabled for all Phase 2 connectors."""
        return ConnectorResponse(
            success=False,
            error="WRITE_DISABLED: Phase 2 connectors are strictly read-only by safety policy.",
            status_code="WRITE_PROHIBITED",
        )
