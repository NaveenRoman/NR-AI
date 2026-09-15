"""
NR-AI Visual Studio Environment Detection & Inspection Engine (Step 7).

Safely discovers and inspects Visual Studio instances, MSBuild, .NET SDKs,
and developer tools using bounded, non-arbitrary subprocess calls (shell=False).
"""

from dataclasses import dataclass, field
import json
import logging
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("NRAI.VSEnvironment")

STANDARD_VSWHERE_PATHS = [
    Path(r"C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe"),
    Path(r"C:\Program Files\Microsoft Visual Studio\Installer\vswhere.exe"),
]

STANDARD_VS_ROOTS = [
    Path(r"C:\Program Files\Microsoft Visual Studio\2022"),
    Path(r"C:\Program Files (x86)\Microsoft Visual Studio\2019"),
    Path(r"C:\Program Files (x86)\Microsoft Visual Studio\2017"),
]


@dataclass
class VSInstance:
    """Represents a detected Visual Studio installation."""
    instance_id: str
    display_name: str
    installation_path: str
    installation_version: str
    product_id: str
    is_prerelease: bool = False
    is_complete: bool = True
    msbuild_path: Optional[str] = None
    devenv_path: Optional[str] = None

    @property
    def version(self) -> str:
        return self.installation_version

    def to_dict(self) -> Dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "display_name": self.display_name,
            "installation_path": self.installation_path,
            "installation_version": self.installation_version,
            "version": self.installation_version,
            "product_id": self.product_id,
            "is_prerelease": self.is_prerelease,
            "is_complete": self.is_complete,
            "msbuild_path": self.msbuild_path,
            "devenv_path": self.devenv_path,
        }


@dataclass
class VSEnvironmentInfo:
    """Comprehensive environment discovery report."""
    is_available: bool = False
    vs_instances: List[VSInstance] = field(default_factory=list)
    msbuild_path: Optional[str] = None
    dotnet_path: Optional[str] = None
    dotnet_sdk_version: Optional[str] = None
    installed_sdks: List[str] = field(default_factory=list)
    vswhere_path: Optional[str] = None
    devenv_path: Optional[str] = None
    vstest_path: Optional[str] = None
    supported_project_types: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    @property
    def instances(self) -> List[VSInstance]:
        return self.vs_instances

    @property
    def dotnet_version(self) -> Optional[str]:
        return self.dotnet_sdk_version

    @property
    def is_ready(self) -> bool:
        return self.is_available

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_available": self.is_available,
            "instance_count": len(self.vs_instances),
            "vs_instances": [inst.to_dict() for inst in self.vs_instances],
            "msbuild_path": self.msbuild_path,
            "dotnet_path": self.dotnet_path,
            "dotnet_sdk_version": self.dotnet_sdk_version,
            "installed_sdks": self.installed_sdks,
            "vswhere_path": self.vswhere_path,
            "devenv_path": self.devenv_path,
            "vstest_path": self.vstest_path,
            "supported_project_types": self.supported_project_types,
            "timestamp": self.timestamp,
        }


class VSEnvironmentDetector:
    """
    Safely inspects the host environment for Visual Studio and .NET build toolchains.
    Guarantees no arbitrary command execution, bounded timeouts, and clean degradation
    when tools are not present.
    """

    def __init__(self, vswhere_path: Optional[Path] = None):
        self.vswhere_path = self._locate_vswhere(vswhere_path)

    def _find_vswhere(self, custom_path: Optional[Path] = None) -> Optional[Path]:
        return self._locate_vswhere(custom_path)

    def _locate_vswhere(self, custom_path: Optional[Path] = None) -> Optional[Path]:
        if custom_path and Path(custom_path).exists():
            return Path(custom_path).resolve()
        for p in STANDARD_VSWHERE_PATHS:
            if p.exists():
                return p.resolve()
        which_p = shutil.which("vswhere")
        if which_p:
            return Path(which_p).resolve()
        return None

    def _run_vswhere(self) -> str:
        """Runs vswhere.exe safely and returns JSON string."""
        if not self.vswhere_path or not self.vswhere_path.exists():
            return "[]"
        try:
            cmd = [
                str(self.vswhere_path),
                "-all",
                "-products", "*",
                "-format", "json",
                "-utf8",
            ]
            res = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                shell=False,
                timeout=10.0,
            )
            if res.returncode == 0 and res.stdout.strip():
                return res.stdout
        except Exception as e:
            logger.warning(f"Failed to run vswhere: {e}")
        return "[]"

    def detect(self, force_refresh: bool = False) -> VSEnvironmentInfo:
        """Runs safe discovery returning a structured VSEnvironmentInfo."""
        instances = self._discover_vs_instances()
        dotnet_path, dotnet_version, installed_sdks = self._discover_dotnet()
        msbuild_path = self._discover_msbuild(instances, dotnet_path)
        devenv_path = self._discover_devenv(instances)
        vstest_path = self._discover_vstest(instances)

        supported_types: List[str] = []
        if dotnet_path or msbuild_path:
            supported_types.extend(["C#", "Visual Basic", "F#", ".NET Core / .NET 5+", "SDK-style Projects"])
        if instances:
            supported_types.extend([".NET Framework", "Classic .NET Solutions", "C++ / vcxproj"])

        is_available = bool(instances or msbuild_path or dotnet_path)
        warnings: List[str] = []
        if not is_available:
            warnings.append("No Visual Studio or .NET toolchain detected.")
        if not instances:
            warnings.append("No Visual Studio IDE instances detected via vswhere.")
        if not dotnet_path:
            warnings.append(".NET SDK (dotnet) not found in PATH or standard directories.")

        return VSEnvironmentInfo(
            is_available=is_available,
            vs_instances=instances,
            msbuild_path=msbuild_path,
            dotnet_path=dotnet_path,
            dotnet_sdk_version=dotnet_version,
            installed_sdks=installed_sdks,
            vswhere_path=str(self.vswhere_path) if self.vswhere_path else None,
            devenv_path=devenv_path,
            vstest_path=vstest_path,
            supported_project_types=supported_types,
            warnings=warnings,
        )

    def _discover_vs_instances(self) -> List[VSInstance]:
        """Queries vswhere or inspects standard directories safely."""
        instances: List[VSInstance] = []

        out = self._run_vswhere()
        if out and out.strip():
            try:
                raw_data = json.loads(out)
                for item in raw_data:
                    inst_path = item.get("installationPath", "")
                    inst_id = item.get("instanceId", "unknown")
                    disp_name = item.get("displayName", "Visual Studio")
                    inst_ver = item.get("installationVersion", "")
                    prod_id = item.get("productId", "")
                    is_pre = item.get("isPrerelease", False)
                    is_comp = item.get("isComplete", True)

                    msbuild = self._find_instance_tool(inst_path, [r"MSBuild\Current\Bin\MSBuild.exe", r"MSBuild\15.0\Bin\MSBuild.exe"])
                    devenv = self._find_instance_tool(inst_path, [r"Common7\IDE\devenv.exe"])

                    instances.append(VSInstance(
                        instance_id=inst_id,
                        display_name=disp_name,
                        installation_path=inst_path,
                        installation_version=inst_ver,
                        product_id=prod_id,
                        is_prerelease=is_pre,
                        is_complete=is_comp,
                        msbuild_path=msbuild,
                        devenv_path=devenv,
                    ))
                if instances:
                    return instances
            except Exception as e:
                logger.warning(f"Failed to query vswhere.exe: {e}")

        # Fallback directory check
        for vs_root in STANDARD_VS_ROOTS:
            if vs_root.exists() and vs_root.is_dir():
                for edition_dir in vs_root.iterdir():
                    if edition_dir.is_dir():
                        devenv = edition_dir / "Common7" / "IDE" / "devenv.exe"
                        if devenv.exists():
                            msbuild = self._find_instance_tool(str(edition_dir), [r"MSBuild\Current\Bin\MSBuild.exe"])
                            instances.append(VSInstance(
                                instance_id=f"dir_{vs_root.name}_{edition_dir.name}",
                                display_name=f"Visual Studio {vs_root.name} {edition_dir.name}",
                                installation_path=str(edition_dir),
                                installation_version=vs_root.name,
                                product_id="Microsoft.VisualStudio.Product." + edition_dir.name,
                                msbuild_path=msbuild,
                                devenv_path=str(devenv),
                            ))

        return instances

    def _find_instance_tool(self, inst_path: str, relative_candidates: List[str]) -> Optional[str]:
        p = Path(inst_path)
        for rel in relative_candidates:
            cand = p / rel
            if cand.exists():
                return str(cand)
        return None

    def _discover_dotnet(self) -> Tuple[Optional[str], Optional[str], List[str]]:
        """Queries the dotnet CLI safely."""
        dotnet = shutil.which("dotnet")
        if not dotnet:
            # Common paths on Windows
            for cand in [Path(r"C:\Program Files\dotnet\dotnet.exe"), Path(r"C:\Program Files (x86)\dotnet\dotnet.exe")]:
                if cand.exists():
                    dotnet = str(cand)
                    break

        if not dotnet:
            return None, None, []

        version = None
        sdks: List[str] = []

        try:
            res_v = subprocess.run([dotnet, "--version"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, shell=False, timeout=5.0)
            if res_v.returncode == 0:
                version = res_v.stdout.strip()

            res_sdks = subprocess.run([dotnet, "--list-sdks"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, shell=False, timeout=5.0)
            if res_sdks.returncode == 0:
                for line in res_sdks.stdout.splitlines():
                    cleaned = line.strip()
                    if cleaned:
                        sdks.append(cleaned)
        except Exception as e:
            logger.warning(f"Error querying dotnet CLI: {e}")

        return dotnet, version, sdks

    def _discover_msbuild(self, instances: List[VSInstance], dotnet_path: Optional[str]) -> Optional[str]:
        """Locates MSBuild executable."""
        # 1. First check detected VS instances
        for inst in instances:
            if inst.msbuild_path and Path(inst.msbuild_path).exists():
                return inst.msbuild_path

        # 2. Check PATH
        which_ms = shutil.which("msbuild")
        if which_ms:
            return which_ms

        # 3. Check dotnet msbuild
        if dotnet_path and Path(dotnet_path).exists():
            return dotnet_path  # Invoked as `dotnet build` or `dotnet msbuild`

        return None

    def _discover_devenv(self, instances: List[VSInstance]) -> Optional[str]:
        for inst in instances:
            if inst.devenv_path and Path(inst.devenv_path).exists():
                return inst.devenv_path
        which_dev = shutil.which("devenv")
        if which_dev:
            return which_dev
        return None

    def _discover_vstest(self, instances: List[VSInstance]) -> Optional[str]:
        for inst in instances:
            cand = Path(inst.installation_path) / "Common7" / "IDE" / "CommonExtensions" / "Microsoft" / "TestWindow" / "vstest.console.exe"
            if cand.exists():
                return str(cand)
        which_test = shutil.which("vstest.console")
        if which_test:
            return which_test
        return None
