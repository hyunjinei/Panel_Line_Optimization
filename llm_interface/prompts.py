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
                "type": "fixed_position|precedence|manual_bay_assignment|date_specific_daily_block_cap|date_specific_daily_seam_cap|constraint_toggle|bias_components",
                "block_id": 4,
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
