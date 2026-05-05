"""Trace-grounded analysis report generation for interactive scheduling runs.

# [AGENT-ADD] This module consumes analysis_bundle.json outputs produced during
# test/eval reruns. It does not run the scheduler and it does not guess hidden
# state; every sentence is grounded in the stored bundle fields.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List


def _fmt_delta(value: int | float | None, unit: str = "") -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        if abs(value) < 1e-9:
            return f"0{unit}"
        sign = "+" if value > 0 else ""
        return f"{sign}{value:.2f}{unit}"
    if value == 0:
        return f"0{unit}"
    sign = "+" if value > 0 else ""
    return f"{sign}{value}{unit}"


def _request_type_hint(request_text: str) -> str:
    raw = (request_text or "").strip()
    if "번째" in raw or "처음" in raw:
        return "위치 고정 요청"
    if "보다 먼저" in raw or "먼저 오게" in raw:
        return "선후행 요청"
    if "35A" in raw or "36B" in raw or "배정" in raw:
        return "베이 지정 요청"
    if "최대" in raw and "블록" in raw:
        return "일일 블록 상한 요청"
    if "심" in raw and "용량" in raw:
        return "일일 심수 상한 요청"
    if "runtime" in raw or "audit" in raw or "켜" in raw or "끄" in raw:
        return "runtime/audit 토글 요청"
    return "일반 재스케줄링 요청"


def _top_constraint_lines(bundle: Dict[str, Any]) -> List[str]:
    rows = bundle.get("delta", {}).get("constraint_changes", []) or []
    lines: List[str] = []
    for row in rows[:3]:
        delta = int(row.get("delta", 0))
        if delta == 0:
            continue
        direction = "증가" if delta > 0 else "감소"
        lines.append(
            f"- `{row.get('constraint_id')}`: {row.get('before')}건 -> {row.get('after')}건 ({abs(delta)}건 {direction})"
        )
    return lines


def _top_date_lines(bundle: Dict[str, Any]) -> List[str]:
    rows = bundle.get("delta", {}).get("date_count_changes", []) or []
    lines: List[str] = []
    for row in rows[:3]:
        delta = int(row.get("delta", 0))
        direction = "증가" if delta > 0 else "감소"
        lines.append(
            f"- `{row.get('date')}`: {row.get('before')}개 -> {row.get('after')}개 ({abs(delta)}개 {direction})"
        )
    return lines


def _top_move_lines(bundle: Dict[str, Any]) -> List[str]:
    rows = bundle.get("delta", {}).get("top_moved_blocks", []) or []
    lines: List[str] = []
    for row in rows[:5]:
        lines.append(
            f"- `{row.get('block_id')}번`: {row.get('before_position')}번째 -> {row.get('after_position')}번째"
        )
    return lines


def _trace_lines(bundle: Dict[str, Any]) -> List[str]:
    trace = bundle.get("trace", {}) or {}
    lines: List[str] = []
    forced_steps = trace.get("forced_steps_after", []) or []
    if forced_steps:
        first = forced_steps[0]
        lines.append(
            f"- 강제 적용 step: `{first.get('step')}` / block `{first.get('selected_block_id')}` / reason `{first.get('selection_reason')}`"
        )

    changed_steps = trace.get("changed_steps_preview", []) or []
    lines.append(f"- trace 기준 변경된 초기 step 수(미리보기): `{len(changed_steps)}`")
    if changed_steps:
        first = changed_steps[0]
        lines.append(
            f"- 첫 변화 step `{first.get('step')}`: `{first.get('before_block_id')}` -> `{first.get('after_block_id')}`"
        )
        lines.append(
            f"- step `{first.get('step')}` 이유 변화: `{first.get('before_reason')}` -> `{first.get('after_reason')}`"
        )
    return lines


def explain_analysis_bundle(bundle: Dict[str, Any]) -> str:
    request = str(bundle.get("request", "")).strip()
    request_type = _request_type_hint(request)
    delta = bundle.get("delta", {}) or {}
    before = bundle.get("before", {}) or {}
    after = bundle.get("after", {}) or {}

    lines: List[str] = [
        "# Trace-Grounded Analysis",
        "",
        "## Request",
        "",
        f"- raw_request: `{request}`",
        f"- request_type: `{request_type}`",
        "",
        "## Metric Delta",
        "",
        f"- makespan_hours: `{before.get('makespan_hours')}` -> `{after.get('makespan_hours')}` (`{_fmt_delta(delta.get('makespan_hours'), 'h')}`)",
        f"- primary_violations: `{before.get('primary_violations')}` -> `{after.get('primary_violations')}` (`{_fmt_delta(delta.get('primary_violations'))}`)",
        f"- raw_violations: `{before.get('raw_violations')}` -> `{after.get('raw_violations')}` (`{_fmt_delta(delta.get('raw_violations'))}`)",
        "",
        "## Trace Findings",
        "",
    ]
    lines.extend(_trace_lines(bundle) or ["- 강제 step 또는 초기 변화 step이 기록되지 않았습니다."])
    lines.extend(
        [
            "",
            "## Top Moved Blocks",
            "",
        ]
    )
    lines.extend(_top_move_lines(bundle) or ["- 위치가 크게 이동한 블록이 기록되지 않았습니다."])
    lines.extend(
        [
            "",
            "## Constraint Delta",
            "",
        ]
    )
    lines.extend(_top_constraint_lines(bundle) or ["- 주요 constraint delta가 기록되지 않았습니다."])
    lines.extend(
        [
            "",
            "## Daily Load Delta",
            "",
        ]
    )
    lines.extend(_top_date_lines(bundle) or ["- 날짜별 처리량 변화가 기록되지 않았습니다."])
    lines.extend(
        [
            "",
            "## Existing Explanation",
            "",
            bundle.get("explanation", "설명 없음"),
            "",
        ]
    )
    return "\n".join(lines)


def load_analysis_bundle(path: str | Path) -> Dict[str, Any]:
    bundle_path = Path(path)
    return json.loads(bundle_path.read_text(encoding="utf-8"))


def save_analysis_report(bundle: Dict[str, Any], path: str | Path) -> None:
    report_path = Path(path)
    report_path.write_text(explain_analysis_bundle(bundle), encoding="utf-8")
