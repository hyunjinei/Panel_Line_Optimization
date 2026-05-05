"""Recommendation engine for interactive scheduling requests.

# [AGENT-ADD] This module does not invent recommendations heuristically.
# It generates nearby alternative requests that can be re-run by the existing
# scheduler, then ranks them using grounded before/after results.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

from .parser import parse_natural_language_request
from .schemas import EditConstraint, ScheduleEditRequest


@dataclass
class RecommendationScenario:
    label: str
    request_text: str
    request_distance: int
    rationale: str


_ORDINAL_TEXT = {
    0: "처음",
    1: "두 번째",
    2: "세 번째",
    3: "네 번째",
    4: "다섯 번째",
}


def _ordinal_text(position: int) -> str:
    return _ORDINAL_TEXT.get(position, f"{position + 1}번째")


def _date_phrase(date_token: Optional[str]) -> str:
    token = str(date_token or "").strip().upper()
    if token.startswith("DAY:"):
        return f"{int(token.split(':', 1)[1])}일"
    return token or "해당 날짜"


def build_recommendation_scenarios(request: ScheduleEditRequest) -> List[RecommendationScenario]:
    scenarios: List[RecommendationScenario] = [
        RecommendationScenario(
            label="requested",
            request_text=request.raw_request,
            request_distance=0,
            rationale="사용자 요청안",
        )
    ]

    if len(request.constraints) != 1:
        return scenarios

    constraint = request.constraints[0]
    if constraint.type == "date_specific_daily_block_cap" and constraint.max_blocks is not None:
        date_phrase = _date_phrase(constraint.date_token or constraint.date_key)
        base_cap = int(constraint.max_blocks)
        for delta in [1, 2, 3]:
            alt_cap = base_cap + delta
            scenarios.append(
                RecommendationScenario(
                    label=f"alt_cap_{alt_cap}",
                    request_text=f"{date_phrase}에는 {alt_cap}개 블록만 해야해. 최대.",
                    request_distance=delta,
                    rationale=f"요청 상한을 {delta}개 완화한 대안",
                )
            )
        return scenarios

    if constraint.type == "fixed_position" and constraint.block_id is not None and constraint.position is not None:
        block_id = int(constraint.block_id)
        target = int(constraint.position)
        candidates: List[int] = []
        for alt in [target + 1, target + 2, max(target - 1, 0), max(target - 2, 0)]:
            if alt == target or alt in candidates:
                continue
            if 0 <= alt <= 4:
                candidates.append(alt)
        for alt in candidates[:3]:
            scenarios.append(
                RecommendationScenario(
                    label=f"alt_pos_{alt+1}",
                    request_text=f"{block_id}번 블록은 {_ordinal_text(alt)}으로 해줘",
                    request_distance=abs(alt - target),
                    rationale=f"요청 위치에서 {abs(alt - target)}칸 조정한 대안",
                )
            )
        return scenarios

    return scenarios


def _metric_tuple(row: Dict) -> tuple:
    raw = row.get("raw_violations")
    raw_value = int(raw) if raw is not None else 0
    return (
        int(row.get("primary_violations", 0)),
        float(row.get("makespan_hours", 0.0)),
        raw_value,
    )


def choose_recommended_scenario(rows: List[Dict]) -> Dict:
    """Choose one recommendation from evaluated scenarios.

    Ranking logic:
    1. Keep only scenarios that do not worsen primary/raw violations relative to the requested scenario.
    2. If any of them reduce makespan, prefer the one with the best improvement-per-distance ratio.
       This avoids recommending a trivially close alternative when a slightly looser alternative gives a much larger gain.
    3. If no not-worse scenario improves makespan, keep the requested scenario.
    4. Fallback to lexicographic (primary, makespan, raw, distance).
    """

    if not rows:
        raise ValueError("rows must not be empty")

    requested = next((row for row in rows if row.get("label") == "requested"), rows[0])
    req_primary = int(requested.get("primary_violations", 0))
    req_raw = int(requested.get("raw_violations", 0) or 0)
    req_makespan = float(requested.get("makespan_hours", 0.0))

    not_worse: List[Dict] = []
    for row in rows:
        primary = int(row.get("primary_violations", 0))
        raw = int(row.get("raw_violations", 0) or 0)
        if primary <= req_primary and raw <= req_raw:
            not_worse.append(row)

    improving: List[tuple] = []
    for row in not_worse:
        if row.get("label") == "requested":
            continue
        makespan = float(row.get("makespan_hours", 0.0))
        gain = req_makespan - makespan
        if gain <= 1e-9:
            continue
        distance = max(int(row.get("request_distance", 999)), 1)
        score = gain / distance
        improving.append((-score, distance, makespan, row))

    if improving:
        improving.sort(key=lambda item: (item[0], item[1], item[2]))
        return improving[0][-1]

    if not_worse:
        return requested

    fallback = []
    for row in rows:
        primary, makespan, raw = _metric_tuple(row)
        distance = int(row.get("request_distance", 999))
        fallback.append((primary, makespan, raw, distance, row))
    fallback.sort(key=lambda item: (item[0], item[1], item[2], item[3]))
    return fallback[0][-1]


def explain_recommendation(rows: List[Dict]) -> str:
    requested = next((row for row in rows if row.get("label") == "requested"), rows[0])
    recommended = choose_recommended_scenario(rows)

    if recommended["label"] == "requested":
        return (
            f"권고안은 원래 요청안 유지입니다. 요청안의 makespan은 {float(requested['makespan_hours']):.2f}시간, "
            f"primary/raw violation은 {int(requested['primary_violations'])}/{int(requested.get('raw_violations') or 0)}건입니다. "
            f"비교한 대안 중에서 같은 수준의 제약을 유지하면서 일정 비용을 충분히 줄이는 안이 확인되지 않았습니다."
        )

    makespan_gain = float(requested['makespan_hours']) - float(recommended['makespan_hours'])
    return (
        f"권고안은 '{recommended['request_text']}'입니다. 요청안 '{requested['request_text']}'과 비교하면 "
        f"makespan은 {float(requested['makespan_hours']):.2f}시간에서 {float(recommended['makespan_hours']):.2f}시간으로 {makespan_gain:.2f}시간 줄고, "
        f"primary/raw violation은 {int(requested['primary_violations'])}/{int(requested.get('raw_violations') or 0)}건에서 "
        f"{int(recommended['primary_violations'])}/{int(recommended.get('raw_violations') or 0)}건으로 유지되거나 개선됩니다. "
        f"즉 요청 의도에서 완전히 벗어나지 않으면서 일정 비용을 훨씬 덜 치르는 대안으로 판단됩니다."
    )


def scenarios_from_text(text: str) -> List[RecommendationScenario]:
    return build_recommendation_scenarios(parse_natural_language_request(text))
