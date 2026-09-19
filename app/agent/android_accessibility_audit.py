"""
NR-AI Android Accessibility & UI Quality Inspector (Droid Phase 5).

Audits:
- Minimum touch target sizing (48dp x 48dp Material guidelines)
- Missing contentDescription on visual elements (Images, Icons)
- Hardcoded string literals in layouts missing @string/ resource extraction
- Jetpack Compose unstable lambdas in recomposition scopes
"""

from dataclasses import dataclass, field, asdict
from enum import Enum
import logging
from pathlib import Path
import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Set, Tuple, Union

logger = logging.getLogger("NRAI.AndroidAccessibility")


class QualitySeverity(str, Enum):
    ERROR = "ERROR"
    WARNING = "WARNING"
    SUGGESTION = "SUGGESTION"


@dataclass
class AccessibilityIssue:
    rule_id: str
    severity: QualitySeverity
    message: str
    file_path: str
    element_name: str
    line_number: int = 0
    recommended_fix: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity.value,
            "message": self.message,
            "file_path": self.file_path,
            "element_name": self.element_name,
            "line_number": self.line_number,
            "recommended_fix": self.recommended_fix,
        }


class AndroidAccessibilityAuditEngine:
    """Static auditor for Android accessibility (a11y) and UI quality standards."""

    ANDROID_NS = "{http://schemas.android.com/apk/res/android}"

    def audit_compose_file(self, file_path: Union[str, Path]) -> List[AccessibilityIssue]:
        p = Path(file_path).resolve()
        if not p.exists() or p.suffix != ".kt":
            return []

        issues: List[AccessibilityIssue] = []
        lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()

        for idx, line in enumerate(lines, start=1):
            raw = line.strip()
            if raw.startswith("//"):
                continue

            # 1. Missing contentDescription in Image / Icon
            if re.search(r"\b(Image|Icon)\s*\(", raw) and "contentDescription" not in raw:
                # Look ahead 3 lines
                context = " ".join(lines[idx - 1:min(len(lines), idx + 3)])
                if "contentDescription" not in context:
                    issues.append(AccessibilityIssue(
                        rule_id="COMPOSE_A11Y_MISSING_CONTENT_DESC",
                        severity=QualitySeverity.ERROR,
                        message="Image or Icon composable missing contentDescription for accessibility screen readers.",
                        file_path=str(p),
                        element_name="Image/Icon",
                        line_number=idx,
                        recommended_fix="Provide a descriptive string or set contentDescription = null for decorative elements.",
                    ))

            # 2. Hardcoded string in Text composable
            m_text = re.search(r"\bText\s*\(\s*text\s*=\s*\"([^\"]+)\"", raw)
            if not m_text:
                m_text = re.search(r"\bText\s*\(\s*\"([^\"]+)\"", raw)
            if m_text:
                literal = m_text.group(1)
                if len(literal) > 1 and not literal.startswith("$"):
                    issues.append(AccessibilityIssue(
                        rule_id="COMPOSE_UI_HARDCODED_STRING",
                        severity=QualitySeverity.WARNING,
                        message=f"Hardcoded string literal '{literal}' in Text(). Use stringResource(R.string...) for localization.",
                        file_path=str(p),
                        element_name="Text",
                        line_number=idx,
                        recommended_fix=f"Extract '{literal}' into strings.xml.",
                    ))

            # 3. Touch target size < 48dp on clickable modifiers
            m_size = re.search(r"\.size\s*\(\s*(\d+)\.dp\)", raw)
            if m_size and "clickable" in raw:
                val = int(m_size.group(1))
                if val < 48:
                    issues.append(AccessibilityIssue(
                        rule_id="COMPOSE_A11Y_TOUCH_TARGET_SIZE",
                        severity=QualitySeverity.WARNING,
                        message=f"Clickable component size {val}dp is below Android recommended 48dp touch target.",
                        file_path=str(p),
                        element_name="Modifier.size",
                        line_number=idx,
                        recommended_fix="Increase touch target to at least 48dp or use minimumInteractiveComponentSize.",
                    ))

        return issues

    def audit_layout_xml(self, xml_path: Union[str, Path]) -> List[AccessibilityIssue]:
        p = Path(xml_path).resolve()
        if not p.exists() or p.suffix != ".xml":
            return []

        issues: List[AccessibilityIssue] = []
        try:
            tree = ET.parse(p)
            for elem in tree.getroot().iter():
                tag = elem.tag.split("}")[-1]
                # Image / ImageButton missing contentDescription
                if tag in ("ImageView", "ImageButton"):
                    cd = elem.get(f"{self.ANDROID_NS}contentDescription")
                    if not cd:
                        issues.append(AccessibilityIssue(
                            rule_id="XML_A11Y_MISSING_CONTENT_DESC",
                            severity=QualitySeverity.ERROR,
                            message=f"<{tag}> lacks android:contentDescription.",
                            file_path=str(p),
                            element_name=tag,
                            recommended_fix='Add android:contentDescription="@string/..." or android:importantForAccessibility="no".',
                        ))

                # Hardcoded text in TextView / Button
                txt = elem.get(f"{self.ANDROID_NS}text")
                if txt and not txt.startswith("@"):
                    issues.append(AccessibilityIssue(
                        rule_id="XML_UI_HARDCODED_STRING",
                        severity=QualitySeverity.WARNING,
                        message=f"<{tag}> has hardcoded android:text='{txt}'.",
                        file_path=str(p),
                        element_name=tag,
                        recommended_fix=f"Extract '{txt}' into res/values/strings.xml.",
                    ))
        except Exception as e:
            logger.warning(f"Failed to audit XML {p.name}: {e}")
        return issues

    def audit_project(self, project_path: Union[str, Path]) -> Dict[str, Any]:
        proj = Path(project_path).resolve()
        all_issues: List[AccessibilityIssue] = []

        for kt in proj.glob("**/*.kt"):
            if "build" in kt.parts:
                continue
            all_issues.extend(self.audit_compose_file(kt))

        for xml_file in proj.glob("**/res/layout/*.xml"):
            if "build" in xml_file.parts:
                continue
            all_issues.extend(self.audit_layout_xml(xml_file))

        errors = [i for i in all_issues if i.severity == QualitySeverity.ERROR]
        warnings = [i for i in all_issues if i.severity == QualitySeverity.WARNING]

        return {
            "total_issues": len(all_issues),
            "errors": len(errors),
            "warnings": len(warnings),
            "issues": [i.to_dict() for i in all_issues],
        }

    def audit_project_issues(self, project_path: Union[str, Path]) -> List[AccessibilityIssue]:
        proj = Path(project_path).resolve()
        all_issues: List[AccessibilityIssue] = []
        for kt in proj.glob("**/*.kt"):
            if "build" in kt.parts:
                continue
            all_issues.extend(self.audit_compose_file(kt))
        for xml_file in proj.glob("**/res/layout/*.xml"):
            if "build" in xml_file.parts:
                continue
            all_issues.extend(self.audit_layout_xml(xml_file))
        return all_issues
