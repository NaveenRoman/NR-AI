"""
NR-AI Host System Information Read-Only Connector.
Safe provider reading OS platform, CPU architecture, RAM metrics,
and runtime information without shell execution.
"""

import os
import platform
import sys
import time
from typing import Any, Dict, Optional

from app.connectors.base import (
    AuthState,
    BaseConnector,
    ConnectorConfig,
    ConnectorResponse,
    PrivacyClassification,
)

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False


class SystemInfoConnector(BaseConnector):
    """
    Read-only system information connector.
    Zero shell execution, zero credentials, pure diagnostic data.
    """

    def __init__(self, connector_id: str = "system_info_connector"):
        config = ConnectorConfig(
            connector_id=connector_id,
            provider="host_os_telemetry",
            capabilities=["telemetry:read", "system:specs"],
            data_scopes=["system:hardware", "system:os"],
            authentication_state=AuthState.CONFIGURED,
            read_only=True,
            privacy_classification=PrivacyClassification.PUBLIC,
        )
        super().__init__(config)

    def read(self, query: str = "all", params: Optional[Dict[str, Any]] = None) -> ConnectorResponse:
        """Read host telemetry safely."""
        try:
            total_ram_gb = 0.0
            available_ram_gb = 0.0
            cpu_percent = 0.0
            if _HAS_PSUTIL:
                mem = psutil.virtual_memory()
                total_ram_gb = round(mem.total / (1024 ** 3), 2)
                available_ram_gb = round(mem.available / (1024 ** 3), 2)
                cpu_percent = psutil.cpu_percent(interval=None)

            cpu_count = os.cpu_count() or 1

            specs = {
                "os_platform": platform.system(),
                "os_release": platform.release(),
                "os_version": platform.version(),
                "architecture": platform.machine(),
                "cpu_cores": cpu_count,
                "total_ram_gb": total_ram_gb,
                "available_ram_gb": available_ram_gb,
                "cpu_utilization_percent": cpu_percent,
                "python_version": sys.version.split()[0],
                "timestamp": time.time(),
            }

            if query and query != "all" and query in specs:
                return ConnectorResponse(
                    success=True,
                    data={query: specs[query]},
                    status_code="OK",
                    provenance="local_kernel",
                )

            return ConnectorResponse(
                success=True,
                data=specs,
                status_code="OK",
                provenance="local_kernel",
            )
        except Exception as ex:
            return ConnectorResponse(
                success=False,
                error=f"SYSTEM_INFO_ERROR: {str(ex)}",
                status_code="ERROR",
            )
