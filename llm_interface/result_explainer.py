"""Deterministic result explanation helpers.

# [AGENT-EDIT] Explanation quality was expanded from simple metric summary to
# request-type-aware trade-off narration so the output reads like grounded
# reasoning rather than a short status line.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from .schemas import ComparisonSummary, ResultSummary


def _classify_request(user_request: str) -> str:
    raw = (user_request or "").strip()
    if not raw:
        return "generic"
    lowered = raw.lower()
    if "runtime" in lowered or "audit" in lowered or "켜" in raw or "끄" in raw:
        return "constraint_toggle"
    if any(token in raw for token in ["작업 중", "이미 투입", "복구 후", "정상화 후", "고장", "우선 투입", "긴급"]):
        return "emergency_reschedule"
    if "35a" in lowered or "36b" in lowered or "배정" in raw:
        if "번째" in raw or "처음" in raw:
            return "combo"
        return "manual_bay"
    if "보다 먼저" in raw or "먼저 오게" in raw:
        return "precedence"
    if "최대" in raw and "블록" in raw:
        return "daily_block_cap"
    if "심" in raw and "용량" in raw:
        return "daily_seam_cap"
    if "번째" in raw or "처음" in raw:
        return "fixed_position"
    return "generic"


def _request_intro_line(user_request: str) -> str:
    request_type = _classify_request(user_request)
    if request_type == "fixed_position":
        return "이 요청은 특정 블록의 위치를 고정한 뒤, 남은 블록을 기존 스케줄러가 다시 선택하는 재스케줄링입니다."
    if request_type == "precedence":
        return "이 요청은 선후행 관계를 추가한 뒤, 그 제약을 만족하도록 기존 스케줄러를 다시 실행한 결과입니다."
    if request_type == "manual_bay":
        return "이 요청은 특정 블록의 베이를 강제로 지정한 뒤, 그 영향이 반영된 상태에서 전체 스케줄을 다시 계산한 결과입니다."
    if request_type == "combo":
        return "이 요청은 위치 고정과 베이 지정을 동시에 적용한 뒤, 그 상태에서 전체 스케줄을 다시 계산한 결과입니다."
    if request_type == "daily_block_cap":
        return "이 요청은 특정 날짜의 일일 처리 블록 수 상한을 추가한 뒤, 남는 작업이 뒤 날짜로 넘어가도록 재스케줄링한 결과입니다."
    if request_type == "daily_seam_cap":
        return "이 요청은 특정 날짜의 일일 심수 상한을 추가한 뒤, 용량을 넘는 작업을 뒤로 미루도록 재스케줄링한 결과입니다."
    if request_type == "constraint_toggle":
        return "이 요청은 runtime 제약과 audit 제약의 적용 범위를 바꾼 뒤, 같은 데이터에 대해 다시 스케줄링하고 사후 검증한 결과입니다."
    if request_type == "emergency_reschedule":
        return "이 요청은 이미 작업 중인 prefix를 고정하고, 긴급 블록과 지연 블록 조건을 구조화한 뒤 기존 스케줄러를 다시 실행한 결과입니다."
    return "이 요청은 사용자 조건을 반영한 뒤 기존 스케줄러를 다시 실행한 재스케줄링 결과입니다."


def _format_top_constraints(summary: ResultSummary) -> str:
    if not summary.top_constraints:
        return "주요 위반 항목은 별도로 기록되지 않았습니다."
    ordered = sorted(summary.top_constraints.items(), key=lambda item: (-item[1], item[0]))
    joined = ", ".join(f"{name} {count}건" for name, count in ordered[:3])
    return f"주요 위반은 {joined}입니다."


def _format_date_counts(summary: ResultSummary) -> str:
    if not summary.date_counts:
        return "날짜별 블록 수 정보는 별도로 기록되지 않았습니다."
    ordered = sorted(summary.date_counts.items())
    joined = ", ".join(f"{date} {count}개" for date, count in ordered[:5])
    return f"날짜별 처리 블록 수는 {joined}입니다."


def _constraint_delta_lines(before: ResultSummary, after: ResultSummary) -> List[str]:
    keys = sorted(set(before.top_constraints) | set(after.top_constraints))
    changes: List[Tuple[str, int]] = []
    for key in keys:
        delta = int(after.top_constraints.get(key, 0)) - int(before.top_constraints.get(key, 0))
        if delta != 0:
            changes.append((key, delta))
    changes.sort(key=lambda item: (-abs(item[1]), item[0]))
    lines: List[str] = []
    for key, delta in changes[:3]:
        direction = "증가" if delta > 0 else "감소"
        lines.append(f"{key}는 {abs(delta)}건 {direction}했습니다")
    return lines


def _date_count_delta_lines(before: ResultSummary, after: ResultSummary) -> List[str]:
    keys = sorted(set(before.date_counts) | set(after.date_counts))
    lines: List[str] = []
    for key in keys:
        before_count = int(before.date_counts.get(key, 0))
        after_count = int(after.date_counts.get(key, 0))
        if before_count == after_count:
            continue
        direction = "증가" if after_count > before_count else "감소"
        lines.append(f"{key}의 처리 블록 수는 {before_count}개에서 {after_count}개로 {direction}했습니다")
    return lines[:3]


def _sequence_change_line(before: ResultSummary, after: ResultSummary) -> str:
    if before.sequence == after.sequence:
        return "시퀀스 자체는 변경되지 않았습니다."

    before_pos = {block_id: idx + 1 for idx, block_id in enumerate(before.sequence)}
    after_pos = {block_id: idx + 1 for idx, block_id in enumerate(after.sequence)}
    moved: List[Tuple[int, int, int]] = []
    for block_id in after.sequence:
        if block_id not in before_pos:
            continue
        if before_pos[block_id] != after_pos[block_id]:
            moved.append((block_id, before_pos[block_id], after_pos[block_id]))
    moved.sort(key=lambda item: abs(item[1] - item[2]), reverse=True)
    if not moved:
        return f"시퀀스는 {before.sequence}에서 {after.sequence}로 변경되었습니다."
    preview = ", ".join(
        f"{block_id}번({before_idx}->{after_idx})"
        for block_id, before_idx, after_idx in moved[:5]
    )
    return f"순서가 바뀐 대표 블록은 {preview}입니다."


def _bay_change_line(before: ResultSummary, after: ResultSummary) -> str:
    changed: List[Tuple[int, str, str]] = []
    for block_id, after_bay in after.bay_assignments.items():
        before_bay = before.bay_assignments.get(block_id)
        if before_bay and before_bay != after_bay:
            changed.append((block_id, before_bay, after_bay))
    if not changed:
        return "베이 배정 변화는 없습니다."
    preview = ", ".join(
        f"{block_id}번({before_bay}->{after_bay})"
        for block_id, before_bay, after_bay in changed[:5]
    )
    return f"변경된 베이 배정은 {preview}입니다."


def _tradeoff_line(before: ResultSummary, after: ResultSummary) -> str:
    makespan_delta = after.makespan_hours - before.makespan_hours
    primary_delta = after.primary_violations - before.primary_violations
    raw_delta = None
    if before.raw_violations is not None and after.raw_violations is not None:
        raw_delta = after.raw_violations - before.raw_violations

    if makespan_delta <= 0 and primary_delta <= 0 and (raw_delta is None or raw_delta <= 0):
        if makespan_delta < 0 or primary_delta < 0 or (raw_delta is not None and raw_delta < 0):
            return "종합적으로 보면 일정과 제약 측면이 모두 악화되지 않았고, 적어도 한 지표에서는 개선이 있었습니다."
        return "종합적으로 보면 주요 지표 변화가 거의 없어서 운영 영향은 제한적입니다."

    if makespan_delta > 0 and primary_delta <= 0:
        return "해석하면, 이번 요청은 제약 수준을 유지하거나 개선하는 대신 일정이 길어지는 방향의 trade-off를 만들었습니다."
    if makespan_delta <= 0 and primary_delta > 0:
        return "해석하면, 이번 요청은 일정 측면에서는 유지 또는 개선됐지만 제약 위반 비용을 더 많이 치르는 방향으로 작동했습니다."
    if makespan_delta > 0 and primary_delta > 0:
        return "해석하면, 이번 요청은 일정과 제약 두 측면 모두 비용을 증가시킨 강한 개입이었습니다."

    return "종합적으로 보면 시간과 제약 지표가 서로 다른 방향으로 움직여 추가 판단이 필요한 결과입니다."


def _operational_reasoning_line(before: ResultSummary, after: ResultSummary, user_request: str) -> str:
    request_type = _classify_request(user_request)
    if request_type in {"fixed_position", "precedence"}:
        return "즉 특정 블록의 초기 순서를 바꾸면 초반 상태가 달라지고, 그 뒤 후보 평가가 연쇄적으로 달라져 후속 블록 배치와 베이 분포까지 함께 재편됩니다."
    if request_type == "manual_bay":
        return "즉 베이를 강제로 바꾸면 해당 블록 하나만 바뀌는 것이 아니라 이후 A/B 패턴과 연속 배치 규칙에도 영향을 주기 때문에 bay 관련 위반이 함께 늘 수 있습니다."
    if request_type == "combo":
        return "즉 순서와 베이를 동시에 고정하면 초반 상태와 bay 패턴이 함께 바뀌므로, 단일 요청보다 파급효과가 크게 나타나는 것이 자연스럽습니다."
    if request_type == "daily_block_cap":
        return "즉 특정 날짜의 처리 상한을 낮추면 그 날짜의 과부하는 줄지만 남은 작업이 다음 날짜로 밀리면서 makespan이 커지는 것이 정상적인 반응입니다."
    if request_type == "daily_seam_cap":
        return "즉 특정 날짜의 심수 상한을 낮추면 그날 용량은 안정되지만 잔여 심수가 뒤 날짜로 넘어가면서 일정이 늘어날 가능성이 큽니다."
    if request_type == "constraint_toggle":
        return "즉 runtime에서는 더 자유롭게 선택했더라도 audit가 같은 제약을 계속 세기 때문에, 최종 위반 수가 크게 증가하면 이는 마스킹 완화의 직접적인 결과로 해석할 수 있습니다."
    if request_type == "emergency_reschedule":
        return "즉 이미 시작된 블록은 되돌리지 않고 고정하며, 긴급 블록은 남은 시퀀스 앞쪽으로 당기고 지연 블록은 우선 블록 뒤로 밀어 전체 상태를 다시 전개한 결과입니다."
    return "즉 요청은 단순 순서 치환이 아니라 상태를 바꾼 뒤 전체 스케줄을 다시 전개한 결과로 이해하는 것이 맞습니다."


def explain_single_result(summary: ResultSummary) -> str:
    """Explain one schedule result in Korean."""
    parts: List[str] = [
        f"{summary.label} 결과는 makespan {summary.makespan_hours:.2f}시간, "
        f"primary violation {summary.primary_violations}건입니다."
    ]
    if summary.raw_violations is not None:
        parts.append(f"raw violation은 {summary.raw_violations}건입니다.")
    parts.append(_format_top_constraints(summary))
    parts.append(_format_date_counts(summary))
    if summary.notes:
        parts.append("추가 메모: " + " / ".join(summary.notes))
    return " ".join(parts)


def explain_comparison(comparison: ComparisonSummary) -> str:
    """Explain before/after schedule changes in Korean."""
    before = comparison.before
    after = comparison.after

    makespan_delta = after.makespan_hours - before.makespan_hours
    primary_delta = after.primary_violations - before.primary_violations
    raw_delta = None
    if before.raw_violations is not None and after.raw_violations is not None:
        raw_delta = after.raw_violations - before.raw_violations

    delta_parts: List[str] = []
    if abs(makespan_delta) < 1e-9:
        delta_parts.append("makespan 변화는 없습니다")
    elif makespan_delta > 0:
        delta_parts.append(f"makespan은 {makespan_delta:.2f}시간 증가했습니다")
    else:
        delta_parts.append(f"makespan은 {abs(makespan_delta):.2f}시간 감소했습니다")

    if primary_delta == 0:
        delta_parts.append("primary violation 변화는 없습니다")
    elif primary_delta > 0:
        delta_parts.append(f"primary violation은 {primary_delta}건 증가했습니다")
    else:
        delta_parts.append(f"primary violation은 {abs(primary_delta)}건 감소했습니다")

    if raw_delta is not None:
        if raw_delta > 0:
            delta_parts.append(f"raw violation은 {raw_delta}건 증가했습니다")
        elif raw_delta < 0:
            delta_parts.append(f"raw violation은 {abs(raw_delta)}건 감소했습니다")
        else:
            delta_parts.append("raw violation 변화는 없습니다")

    request_prefix = ""
    if comparison.user_request:
        request_prefix = f"사용자 요청 '{comparison.user_request}'을 반영한 결과입니다. "

    explanation_parts: List[str] = [request_prefix + ". ".join(delta_parts) + "."]
    explanation_parts.append(_request_intro_line(comparison.user_request))
    explanation_parts.append(_sequence_change_line(before, after))

    constraint_deltas = _constraint_delta_lines(before, after)
    if constraint_deltas:
        explanation_parts.append("제약 측면에서는 " + ", ".join(constraint_deltas) + ".")
    else:
        explanation_parts.append("주요 제약 분포 변화는 크지 않았습니다.")

    date_deltas = _date_count_delta_lines(before, after)
    if date_deltas:
        explanation_parts.append("일자별 처리량 기준으로는 " + ", ".join(date_deltas) + ".")
    else:
        explanation_parts.append("일자별 처리 블록 수 분포 변화는 없습니다.")

    bay_line = _bay_change_line(before, after)
    if bay_line != "베이 배정 변화는 없습니다.":
        explanation_parts.append(bay_line)

    explanation_parts.append(_tradeoff_line(before, after))
    explanation_parts.append(_operational_reasoning_line(before, after, comparison.user_request))
    explanation_parts.append(_format_top_constraints(after))
    if after.notes:
        explanation_parts.append("추가 메모: " + " / ".join(after.notes))

    return " ".join(explanation_parts)
