"""Natural-language request parser for scheduling edits."""

from __future__ import annotations

import re
from typing import Dict, List, Optional

from .schemas import EditConstraint, ScheduleEditRequest


_ORDINAL_TO_INDEX: Dict[str, int] = {
    "처음": 0,
    "첫번째": 0,
    "첫 번째": 0,
    "첫번재": 0,
    "첫번쨰": 0,
    "두번째": 1,
    "두 번째": 1,
    "세번째": 2,
    "세 번째": 2,
    "네번째": 3,
    "네 번째": 3,
    "다섯번째": 4,
    "다섯 번째": 4,
}

# [AGENT-ADD] Supported natural-language aliases for runtime/audit toggles.
_CONSTRAINT_TARGET_ALIASES: Dict[str, str] = {
    "c/seam간격": "enable_c_seam_spacing",
    "cseam간격": "enable_c_seam_spacing",
    "c-seam간격": "enable_c_seam_spacing",
    "c/seam": "enable_c_seam_spacing",
    "c-seam": "enable_c_seam_spacing",
    "곡판간격": "enable_routing_curved_spacing",
    "고심수간격": "enable_routing_high_seam_spacing",
    "작업장순서": "enable_routing_workshop_order",
    "라인그룹": "enable_line_group_constraint",
    "라인고정간격": "enable_line_group_constraint",
    "3bay": "enable_consecutive_3bay_prevention",
    "3베이": "enable_consecutive_3bay_prevention",
    "자재미입고": "enable_p5_13_material_ready",
    "평일용량": "enable_p5_8_weekday_capacity",
    "72심초과17블록": "enable_p5_9_block_count_check",
    "주말용량": "enable_p5_10_weekend_capacity",
    "혹서기용량": "enable_p5_16_hot_season_capacity",
}


def _extract_block_mentions(text: str) -> List[int]:
    """[AGENT-ADD] Return block ids in the order mentioned by the user."""
    block_ids: List[int] = []
    for raw_block_id in re.findall(r"(\d+)\s*번(?:\s*블록)?", text):
        block_id = int(raw_block_id)
        if block_id not in block_ids:
            block_ids.append(block_id)
    return block_ids


def _extract_position(text: str) -> Optional[int]:
    """Return zero-based position if the request specifies one."""
    lowered = text.replace(" ", "")
    for key, value in _ORDINAL_TO_INDEX.items():
        if key.replace(" ", "") in lowered:
            return value

    numeric_match = re.search(r"(\d+)\s*(?:번째|번쨰|번째로|번째에)", text)
    if numeric_match:
        return max(int(numeric_match.group(1)) - 1, 0)
    return None


def _extract_freeze_prefix_request(text: str) -> Optional[EditConstraint]:
    """Parse already-started/frozen prefix requests."""
    lowered = text.replace(" ", "")
    if not any(token in lowered for token in ["작업중", "이미투입", "이미작업", "순서고정", "고정"]):
        return None
    # [AGENT-ADD] Explicit ordinal requests belong to fixed_position, not freeze_prefix.
    if _extract_position(text) is not None:
        return None
    block_ids = _extract_block_mentions(text)
    if not block_ids:
        return None
    return EditConstraint(
        type="freeze_prefix",
        block_ids=block_ids,
        note="natural-language already-started prefix freeze request",
    )


def _extract_priority_block_request(text: str) -> Optional[EditConstraint]:
    """Parse urgent/next-after-recovery insertion requests."""
    lowered = text.replace(" ", "")
    if "보다먼저" in lowered:
        return None
    if any(token in lowered for token in ["제외", "지연", "후순위", "나중", "고장"]):
        return None
    if not any(token in lowered for token in ["가장먼저", "우선투입", "먼저투입", "최우선", "다음투입"]):
        return None
    block_ids = _extract_block_mentions(text)
    if len(block_ids) != 1:
        return None
    target = "next_after_recovery" if any(token in lowered for token in ["복구후", "정상화후"]) else "immediate_next"
    return EditConstraint(
        type="priority_block",
        block_id=block_ids[0],
        target=target,
        note="natural-language priority insertion request",
    )


def _extract_delayed_block_request(text: str) -> Optional[EditConstraint]:
    """Parse requests that remove a block from the immediate next insertion."""
    lowered = text.replace(" ", "")
    if not any(token in lowered for token in ["바로다음", "다음투입", "후순위", "나중", "지연", "제외"]):
        return None
    if not any(token in lowered for token in ["고장", "지연", "제외", "후순위", "나중"]):
        return None
    block_ids = _extract_block_mentions(text)
    if len(block_ids) != 1:
        return None
    return EditConstraint(
        type="delayed_block",
        block_id=block_ids[0],
        target="not_immediate_next",
        note="natural-language delayed block request",
    )


def _extract_move_block_request(text: str) -> Optional[EditConstraint]:
    """Parse fixed-position constraints like '4번 블록은 반드시 처음'."""
    match = re.search(r"(\d+)\s*번\s*블록?", text)
    if not match:
        return None
    position = _extract_position(text)
    if position is None:
        if any(token in text for token in ["앞으로", "맨 앞", "맨앞"]):
            position = 0
        else:
            return None
    return EditConstraint(
        type="fixed_position",
        block_id=int(match.group(1)),
        position=position,
        note="natural-language fixed-position request",
    )



def _extract_precedence_request(text: str) -> Optional[EditConstraint]:
    """Parse precedence like '7번은 2번보다 먼저'."""
    match = re.search(
        r"(\d+)\s*번(?:\s*블록)?(?:은|는)?\s*(\d+)\s*번(?:\s*블록)?보다\s*먼저",
        text,
    )
    if not match:
        return None
    return EditConstraint(
        type="precedence",
        before_block_id=int(match.group(1)),
        after_block_id=int(match.group(2)),
        note="natural-language precedence request",
    )



def _extract_bay_assignment_request(text: str) -> Optional[EditConstraint]:
    """Parse manual bay assignment like '12번은 35A로' or '12번은 35A로 배정'."""
    explicit_match = re.search(
        r"(\d+)\s*번(?:\s*블록)?(?:은|는)?\s*(35A|36B)\s*(?:로|으로|에|베이로|베이에)(?:\s*배정(?:해줘|해주세요|)?|\s*고정(?:해줘|해주세요|)?|)?",
        text,
        re.IGNORECASE,
    )
    if explicit_match:
        return EditConstraint(
            type="manual_bay_assignment",
            block_id=int(explicit_match.group(1)),
            bay=explicit_match.group(2).upper(),
            note="natural-language bay assignment request",
        )

    implicit_bay_match = re.search(
        r"(35A|36B)\s*(?:로|으로|에|베이로|베이에)(?:\s*배정(?:해줘|해주세요|)?|\s*고정(?:해줘|해주세요|)?|)",
        text,
        re.IGNORECASE,
    )
    if not implicit_bay_match:
        return None

    block_mentions = re.findall(r"(\d+)\s*번(?:\s*블록)?", text)
    unique_block_ids = sorted({int(block_id) for block_id in block_mentions})
    if len(unique_block_ids) != 1:
        return None

    return EditConstraint(
        type="manual_bay_assignment",
        block_id=unique_block_ids[0],
        bay=implicit_bay_match.group(1).upper(),
        note="natural-language bay assignment request (implicit block reference)",
    )



def _extract_date_token(text: str) -> Optional[str]:
    full_match = re.search(r"(20\d{2}[./-]?\d{1,2}[./-]?\d{1,2})", text)
    if full_match:
        return full_match.group(1)
    day_match = re.search(r"(?<!\d)(\d{1,2})\s*일(?:날|에는|은|엔)?", text)
    if day_match:
        return f"DAY:{int(day_match.group(1))}"
    return None



def _extract_daily_block_cap_request(text: str) -> Optional[EditConstraint]:
    if "블록" not in text:
        return None
    date_token = _extract_date_token(text)
    if not date_token:
        return None
    count_match = re.search(r"(\d+)\s*개\s*블록", text)
    if not count_match:
        return None
    if not any(token in text for token in ["최대", "까지만", "이하", "넘지", "제한"]):
        return None
    return EditConstraint(
        type="date_specific_daily_block_cap",
        date_token=date_token,
        max_blocks=int(count_match.group(1)),
        note="natural-language daily block cap request",
    )



def _extract_daily_seam_cap_request(text: str) -> Optional[EditConstraint]:
    if not any(token in text for token in ["심", "용량"]):
        return None
    date_token = _extract_date_token(text)
    if not date_token:
        return None
    seam_match = re.search(r"(\d+)\s*심", text)
    if not seam_match:
        return None
    if not any(token in text for token in ["최대", "까지만", "이하", "줄여", "낮춰", "제한"]):
        return None
    return EditConstraint(
        type="date_specific_daily_seam_cap",
        date_token=date_token,
        seam_limit=int(seam_match.group(1)),
        note="natural-language daily seam cap request",
    )



def _normalize_constraint_alias(text: str) -> Optional[str]:
    lowered = text.lower().replace(" ", "")
    for alias, target in sorted(_CONSTRAINT_TARGET_ALIASES.items(), key=lambda item: -len(item[0])):
        if alias in lowered:
            return target
    return None



def _extract_constraint_toggle_requests(text: str) -> List[EditConstraint]:
    lowered = text.lower().replace(" ", "")
    target = _normalize_constraint_alias(text)
    if target is None:
        return []

    enabled: Optional[bool] = None
    if any(token in lowered for token in ["꺼줘", "끄기", "끄고", "off", "비활성"]):
        enabled = False
    elif any(token in lowered for token in ["켜줘", "켜기", "키고", "on", "활성"]):
        enabled = True
    if enabled is None:
        return []

    constraints: List[EditConstraint] = []
    if "runtime만" in lowered:
        constraints.append(
            EditConstraint(
                type="constraint_toggle",
                target=target,
                enabled=enabled,
                scope="runtime",
                note="natural-language runtime-only constraint toggle",
            )
        )
        if any(token in lowered for token in ["audit는유지", "audit유지", "audit는켜", "audit켜"]):
            constraints.append(
                EditConstraint(
                    type="constraint_toggle",
                    target=target,
                    enabled=True,
                    scope="audit",
                    note="natural-language audit keep-on request",
                )
            )
        return constraints

    if "audit만" in lowered:
        constraints.append(
            EditConstraint(
                type="constraint_toggle",
                target=target,
                enabled=enabled,
                scope="audit",
                note="natural-language audit-only constraint toggle",
            )
        )
        return constraints

    constraints.append(
        EditConstraint(
            type="constraint_toggle",
            target=target,
            enabled=enabled,
            scope="both",
            note="natural-language constraint toggle",
        )
    )
    return constraints



def _extract_bias_component_request(text: str) -> Optional[EditConstraint]:
    lowered = text.replace(" ", "")
    if not any(token in lowered for token in ["후보축소마스킹", "bias", "후보마스킹"]):
        return None
    if any(token in lowered for token in ["꺼줘", "off", "비활성"]):
        return EditConstraint(
            type="bias_components",
            components=[],
            note="natural-language candidate reduction masking off request",
        )

    numbers = [int(value) for value in re.findall(r"(?<!\d)([123])(?!\d)", lowered)]
    if not numbers:
        return None
    return EditConstraint(
        type="bias_components",
        components=sorted(set(numbers)),
        note="natural-language candidate reduction masking component request",
    )



def _split_request_fragments(text: str) -> List[str]:
    """# [AGENT-ADD] Split multi-line UI-composed requests into parseable fragments."""
    fragments: List[str] = []
    for raw in re.split(r"[\n;。.!?]+|그리고", text):
        for sub_raw in re.split(r",", raw):
            fragment = sub_raw.strip()
            if fragment:
                fragments.append(fragment)
    return fragments



def _parse_fragment(fragment: str) -> List[EditConstraint]:
    """# [AGENT-ADD] Parse one fragment so UI can submit multiple lines safely."""
    matched_constraints: List[EditConstraint] = []
    extractor_results = [
        _extract_freeze_prefix_request(fragment),
        _extract_priority_block_request(fragment),
        _extract_delayed_block_request(fragment),
        _extract_precedence_request(fragment),
        _extract_bay_assignment_request(fragment),
        _extract_daily_block_cap_request(fragment),
        _extract_daily_seam_cap_request(fragment),
        _extract_bias_component_request(fragment),
        _extract_move_block_request(fragment),
    ]
    matched_constraints.extend([item for item in extractor_results if item is not None])
    matched_constraints.extend(_extract_constraint_toggle_requests(fragment))
    return matched_constraints


def _is_filler_fragment(fragment: str) -> bool:
    """[AGENT-ADD] Ignore short emphasis fragments created by sentence splitting."""
    compact = fragment.replace(" ", "").strip(".。!?")
    return compact in {"최대", "해줘", "해주세요", "부탁", "반드시"}



def parse_natural_language_request(text: str) -> ScheduleEditRequest:
    """Parse a Korean natural-language edit request into structured constraints.

    # [AGENT-ADD] Deterministic v2 parser.
    # Future LLM API integration should produce the same schema.
    """

    request = ScheduleEditRequest(raw_request=text)
    stripped = text.strip()

    if any(token in stripped for token in ["최대한 유지", "가능한 유지", "건드리지 말고", "유지하고"]):
        request.mode = "repair"
        request.preserve_existing_schedule_as_much_as_possible = True

    fragments = _split_request_fragments(stripped) or [stripped]
    for fragment in fragments:
        matched_constraints = _parse_fragment(fragment)
        request.constraints.extend(matched_constraints)
        if not matched_constraints and not _is_filler_fragment(fragment):
            request.unparsed_fragments.append(fragment)

    return request
