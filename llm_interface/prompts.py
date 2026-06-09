"""Prompt builders for future LLM API integration."""

from __future__ import annotations

import json
from typing import Sequence

from .schemas import ComparisonSummary


def build_request_parser_prompt(user_request: str, current_sequence: Sequence[int]) -> str:
    """Prompt template for natural-language request -> JSON constraints."""
    schema = {
        "mode": "strict|repair",
        "preserve_existing_schedule_as_much_as_possible": True,
        "constraints": [
            {
                # [AGENT-EDIT] Keep the LLM contract aligned with the current parser/schema.
                "type": "fixed_position|freeze_prefix|priority_block|delayed_block|precedence|manual_bay_assignment|date_specific_daily_block_cap|date_specific_daily_seam_cap|constraint_toggle|bias_components",
                "block_id": 4,
                "block_ids": [3, 2],
                "position": 0,
                "before_block_id": 7,
                "after_block_id": 2,
                "bay": "35A|36B",
                "date_token": "DAY:12|2025-01-12",
                "max_blocks": 12,
                "seam_limit": 72,
                "target": "enable_c_seam_spacing",
                "enabled": True,
                "scope": "runtime|audit|both",
                "components": [1, 2, 3],
                "note": "short explanation",
            }
        ],
        "unparsed_fragments": [],
    }
    return (
        "You are converting a Korean scheduling edit request into structured JSON.\n"
        "Return JSON only.\n"
        # [AGENT-EDIT] Gemini needs explicit negative rules; otherwise it may copy
        # placeholder schema fields into invalid partial constraints.
        "Only include constraints that are explicitly requested by the user.\n"
        "Do not copy placeholder values from the target schema into the output.\n"
        "For fixed_position, include type, block_id, and zero-based position.\n"
        "For freeze_prefix, include type and block_ids in the order already started or frozen.\n"
        "For priority_block, include type, block_id, and target such as 'next_after_recovery' or 'immediate_next'.\n"
        "For delayed_block, include type, block_id, and target such as 'not_immediate_next'.\n"
        "For precedence, include type, before_block_id, and after_block_id.\n"
        "For manual_bay_assignment, include type, block_id, and bay.\n"
        "Do not infer fixed_position from words like '고정' unless an explicit ordinal position is present.\n"
        "If multiple blocks are described as already running or frozen without explicit positions, use freeze_prefix rather than fixed_position.\n"
        "If a phrase cannot be represented by the schema, put that phrase in unparsed_fragments instead of creating an invalid constraint.\n"
        "Examples:\n"
        "- '7번 블록은 두 번째로' -> {\"type\":\"fixed_position\",\"block_id\":7,\"position\":1}\n"
        "- '3번과 2번은 이미 작업 중이라 고정' -> {\"type\":\"freeze_prefix\",\"block_ids\":[3,2]}\n"
        "- '복구 후 1번을 가장 먼저 투입' -> {\"type\":\"priority_block\",\"block_id\":1,\"target\":\"next_after_recovery\"}\n"
        "- '4번은 바로 다음 투입에서 제외' -> {\"type\":\"delayed_block\",\"block_id\":4,\"target\":\"not_immediate_next\"}\n"
        "- '11번은 4번보다 먼저' -> {\"type\":\"precedence\",\"before_block_id\":11,\"after_block_id\":4}\n"
        "- '15번은 36B 베이' -> {\"type\":\"manual_bay_assignment\",\"block_id\":15,\"bay\":\"36B\"}\n"
        f"Current sequence: {list(current_sequence)}\n"
        f"User request: {user_request}\n"
        f"Target schema: {json.dumps(schema, ensure_ascii=False)}"
    )


def build_result_explainer_prompt(comparison: ComparisonSummary) -> str:
    """Prompt template for before/after schedule explanation."""
    payload = {
        "user_request": comparison.user_request,
        "before": comparison.before.__dict__,
        "after": comparison.after.__dict__,
    }
    return (
        "You are explaining scheduling changes to a shipyard user in Korean.\n"
        "Use the numbers as-is. Do not invent new metrics.\n"
        "Explain what changed, whether makespan improved or worsened, "
        "and which constraint counts changed.\n"
        f"Input: {json.dumps(payload, ensure_ascii=False)}"
    )
