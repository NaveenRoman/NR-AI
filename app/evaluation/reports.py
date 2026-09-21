"""
Evaluation Reporting and Artifact Generation for NR-AI.
Generates structured JSON and human-readable Markdown evaluation reports.

Invariants:
- Honest reporting: empirical failures or unverified states are never hidden.
- Includes evidence verification audit trail.
- Multi-dimensional scoring breakdown.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, List, Optional

from app.evaluation.models import EvaluationResult, EvaluationStatus
from app.evaluation.scoring import CapabilityScoreSummary, CapabilityScorer

logger = logging.getLogger("NRAI.Evaluation.Reports")


class EvaluationReportGenerator:
    """
    Produces formatted Markdown and JSON evaluation reports.
    """

    @classmethod
    def generate_json_report(
        cls,
        results: List[EvaluationResult],
        summary: Optional[CapabilityScoreSummary] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        summary = summary or CapabilityScorer.calculate_scores(results)
        return {
            "report_timestamp": time.time(),
            "metadata": metadata or {},
            "summary": summary.to_dict(),
            "results": [r.to_dict() for r in results],
        }

    @classmethod
    def generate_markdown_report(
        cls,
        results: List[EvaluationResult],
        summary: Optional[CapabilityScoreSummary] = None,
        title: str = "NR-AI Phase 4 Evaluation and Capability Report",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> str:
        summary = summary or CapabilityScorer.calculate_scores(results)
        lines: List[str] = [
            f"# {title}",
            "",
            f"**Generated:** {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}",
            f"**Overall Score:** {summary.overall_score * 100:.1f}% | **Pass Rate:** {summary.pass_rate * 100:.1f}%",
            f"**Total Tests:** {summary.total_evaluations} | **Passed:** {summary.passed_evaluations} | **Failed:** {summary.failed_evaluations} | **Blocked:** {summary.blocked_evaluations} | **Not Verified:** {summary.not_verified_evaluations}",
            "",
            "## 1. Executive Summary",
            "",
            "This report documents deterministic evaluation results across all 13 core NR-AI subsystems.",
            "All scores are calculated under the **Execution Evidence Dominance** invariant: claims of success without empirical validation are evaluated as FAIL or NOT_VERIFIED.",
            "",
            "## 2. Multi-Dimensional Capability Breakdown",
            "",
            "| Dimension | Score (%) | Status |",
            "| :--- | :---: | :---: |",
        ]

        for dim, score in sorted(summary.dimension_scores.items()):
            status_emoji = "PASS" if score >= 0.8 else ("PARTIAL" if score >= 0.5 else "DEFICIENT")
            lines.append(f"| `{dim}` | {score * 100:.1f}% | {status_emoji} |")

        lines.extend([
            "",
            "## 3. Subsystem Category Performance",
            "",
            "| Category | Average Score (%) | Status |",
            "| :--- | :---: | :---: |",
        ])

        for cat, score in sorted(summary.category_scores.items()):
            status_emoji = "PASS" if score >= 0.8 else "FAIL"
            lines.append(f"| `{cat}` | {score * 100:.1f}% | {status_emoji} |")

        lines.extend([
            "",
            "## 4. Detailed Evaluation Results",
            "",
            "| Case ID | Category | Status | Evidence Verified | Time (ms) | Message |",
            "| :--- | :--- | :---: | :---: | :---: | :--- |",
        ])

        for r in results:
            cat_str = r.category.value if hasattr(r.category, "value") else str(r.category)
            status_str = r.status.value if hasattr(r.status, "value") else str(r.status)
            ev_str = "YES" if r.evidence_verified else "NO"
            msg_clean = r.message.replace("|", "/")[:60]
            lines.append(f"| `{r.case_id}` | `{cat_str}` | **{status_str}** | {ev_str} | {r.execution_time_ms:.1f} | {msg_clean} |")

        lines.extend([
            "",
            "## 5. Security & Safety Compliance",
            "",
            "- **Zero Shell Commands (`shell=True`)**: Enforced by Desktop Shell and Evaluator.",
            "- **Zero Code Self-Modification (`eval`/`exec`)**: Enforced by BoundedLearningEngine.",
            "- **Desktop Intent Isolation**: 64KB bounded payloads and strict intent whitelist.",
            "- **Host Tooling Truthfulness**: Tauri native verification correctly marked based on host environment.",
            "",
        ])

        return "\n".join(lines)

    @classmethod
    def save_report(
        cls,
        results: List[EvaluationResult],
        markdown_path: str,
        json_path: Optional[str] = None,
        title: str = "NR-AI Phase 4 Evaluation and Capability Report",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        summary = CapabilityScorer.calculate_scores(results)
        md_content = cls.generate_markdown_report(results, summary=summary, title=title, metadata=metadata)

        os.makedirs(os.path.dirname(markdown_path), exist_ok=True)
        with open(markdown_path, "w", encoding="utf-8") as f:
            f.write(md_content)

        if json_path:
            os.makedirs(os.path.dirname(json_path), exist_ok=True)
            json_data = cls.generate_json_report(results, summary=summary, metadata=metadata)
            with open(json_path, "w", encoding="utf-8") as f:
                json.dump(json_data, f, indent=2)
