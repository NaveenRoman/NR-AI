"""
NR-AI Selective Personal & Data Connectors Subsystem.
Phase 2 Foundation: Safe, read-only, explicit-authorization provider framework.
"""

from app.connectors.base import (
    AuthState,
    BaseConnector,
    ConnectorConfig,
    ConnectorResponse,
    PrivacyClassification,
)
from app.connectors.registry import ConnectorRegistry, global_connector_registry
from app.connectors.local_file_connector import LocalFileConnector
from app.connectors.system_info_connector import SystemInfoConnector

__all__ = [
    "AuthState",
    "BaseConnector",
    "ConnectorConfig",
    "ConnectorResponse",
    "PrivacyClassification",
    "ConnectorRegistry",
    "global_connector_registry",
    "LocalFileConnector",
    "SystemInfoConnector",
]
