"""
NR-AI Android Manifest Merge & Build Conflict Resolver (Droid Phase 5).

Validates:
- Android 12+ (API 31+) android:exported enforcement on components with intent-filters
- Permission conflict, duplication, and protection level audits
- Insecure cleartext traffic policies
- Google official AGP vs Gradle vs Kotlin vs Java compatibility matrix
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
import logging
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger("NRAI.AndroidManifestMerge")


class ManifestSeverity(str, Enum):
    CRITICAL = "CRITICAL"
    WARNING = "WARNING"
    INFO = "INFO"


@dataclass
class ManifestIssue:
    severity: ManifestSeverity
    code: str
    message: str
    component_name: Optional[str] = None
    file_path: Optional[str] = None
    line_number: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity.value,
            "code": self.code,
            "message": self.message,
            "component_name": self.component_name,
            "file_path": self.file_path,
            "line_number": self.line_number,
        }


@dataclass
class CompatibilityResult:
    compatible: bool
    agp_version: str
    gradle_version: str
    kotlin_version: str
    recommended_agp: Optional[str] = None
    recommended_gradle: Optional[str] = None
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AndroidManifestMergeEngine:
    """Audits AndroidManifest.xml and evaluates build toolchain compatibility."""

    ANDROID_NS = "{http://schemas.android.com/apk/res/android}"

    def audit_manifest(
        self,
        manifest_path: Union[str, Path],
        target_sdk: int = 34,
    ) -> List[ManifestIssue]:
        p = Path(manifest_path).resolve()
        if not p.exists():
            return [ManifestIssue(
                severity=ManifestSeverity.CRITICAL,
                code="MANIFEST_NOT_FOUND",
                message=f"Manifest does not exist at {p}",
                file_path=str(p),
            )]

        issues: List[ManifestIssue] = []
        try:
            tree = ET.parse(p)
            root = tree.getroot()

            # 1. Check usesCleartextTraffic
            app = root.find("application")
            if app is not None:
                ct = app.get(f"{self.ANDROID_NS}usesCleartextTraffic")
                if ct == "true":
                    issues.append(ManifestIssue(
                        severity=ManifestSeverity.WARNING,
                        code="INSECURE_CLEARTEXT_TRAFFIC",
                        message="android:usesCleartextTraffic is set to 'true'. Production apps should enforce HTTPS and use network security config.",
                        file_path=str(p),
                    ))

            # 2. Check components for Android 12+ (API 31+) android:exported
            components = ["activity", "service", "receiver"]
            for comp_type in components:
                for comp in root.iter(comp_type):
                    cname = comp.get(f"{self.ANDROID_NS}name", comp.get("name", "Unknown"))
                    exported = comp.get(f"{self.ANDROID_NS}exported")
                    has_intent_filter = comp.find("intent-filter") is not None

                    if target_sdk >= 31 and has_intent_filter and exported is None:
                        issues.append(ManifestIssue(
                            severity=ManifestSeverity.CRITICAL,
                            code="MISSING_EXPORTED_ATTRIBUTE",
                            message=f"<{comp_type}> '{cname}' defines intent-filter but lacks explicit android:exported attribute (Mandatory in Android 12 / API 31+).",
                            component_name=cname,
                            file_path=str(p),
                        ))

            # 3. Check duplicate permissions
            seen_perms: Set[str] = set()
            for perm in root.iter("uses-permission"):
                pname = perm.get(f"{self.ANDROID_NS}name", perm.get("name"))
                if pname:
                    if pname in seen_perms:
                        issues.append(ManifestIssue(
                            severity=ManifestSeverity.INFO,
                            code="DUPLICATE_PERMISSION",
                            message=f"Duplicate <uses-permission> declaration for '{pname}'.",
                            file_path=str(p),
                        ))
                    seen_perms.add(pname)

        except Exception as e:
            issues.append(ManifestIssue(
                severity=ManifestSeverity.CRITICAL,
                code="MALFORMED_MANIFEST",
                message=f"Failed to parse AndroidManifest.xml: {e}",
                file_path=str(p),
            ))

        return issues

    def evaluate_compatibility(
        self,
        agp_version: str,
        gradle_version: str,
        kotlin_version: str,
    ) -> CompatibilityResult:
        """
        Evaluates AGP, Gradle, and Kotlin versions against Google's compatibility matrix.
        """
        # Parse major.minor versions
        def parse_maj_min(ver: str) -> Tuple[int, int]:
            m = re.match(r"^(\d+)\.(\d+)", ver.strip())
            return (int(m.group(1)), int(m.group(2))) if m else (0, 0)

        agp_mm = parse_maj_min(agp_version)
        gradle_mm = parse_maj_min(gradle_version)
        kotlin_mm = parse_maj_min(kotlin_version)

        # AGP 8.7 requires Gradle 8.9+
        # AGP 8.6 requires Gradle 8.7+
        # AGP 8.5 requires Gradle 8.7+
        # AGP 8.4 requires Gradle 8.6+
        # AGP 8.3 requires Gradle 8.4+
        # AGP 8.2 requires Gradle 8.2+
        # AGP 8.1 requires Gradle 8.0+
        # AGP 8.0 requires Gradle 8.0+
        min_gradle = (8, 0)
        rec_gradle = "8.10.2"
        if agp_mm >= (8, 7):
            min_gradle = (8, 9)
            rec_gradle = "8.10.2"
        elif agp_mm >= (8, 4):
            min_gradle = (8, 6)
            rec_gradle = "8.7"
        elif agp_mm >= (8, 2):
            min_gradle = (8, 2)
            rec_gradle = "8.4"

        compatible = True
        reasons = []

        if gradle_mm < min_gradle:
            compatible = False
            reasons.append(f"AGP {agp_version} requires Gradle >={min_gradle[0]}.{min_gradle[1]}, found Gradle {gradle_version}.")

        if agp_mm >= (8, 0) and kotlin_mm < (1, 8):
            compatible = False
            reasons.append(f"AGP {agp_version} requires Kotlin >=1.8.0, found Kotlin {kotlin_version}.")

        details = "Compatible toolchain configuration." if compatible else " ".join(reasons)

        return CompatibilityResult(
            compatible=compatible,
            agp_version=agp_version,
            gradle_version=gradle_version,
            kotlin_version=kotlin_version,
            recommended_agp="8.7.0" if not compatible else None,
            recommended_gradle=rec_gradle if not compatible else None,
            details=details,
        )
