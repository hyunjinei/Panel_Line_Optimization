"""Schemas for the LLM-assisted interactive scheduling wrapper."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class EditConstraint:
    """Structured schedule edit request.

    # [AGENT-ADD] This is the canonical schema the future LLM API should emit.
    """

    type: str
    block_id: Optional[int] = None
    block_ids: List[int] = field(default_factory=list)
    position: Optional[int] = None
    before_block_id: Optional[int] = None
    after_block_id: Optional[int] = None
    bay: Optional[str] = None
    date_key: Optional[str] = None
    date_token: Optional[str] = None
    max_blocks: Optional[int] = None
    seam_limit: Optional[int] = None
    target: Optional[str] = None
    enabled: Optional[bool] = None
    scope: str = "both"
    components: List[int] = field(default_factory=list)
    note: str = ""


@dataclass
class ScheduleEditRequest:
    """Batch of structured constraints produced from one user utterance."""

    raw_request: str
    mode: str = "strict"
    preserve_existing_schedule_as_much_as_possible: bool = False
    constraints: List[EditConstraint] = field(default_factory=list)
    unparsed_fragments: List[str] = field(default_factory=list)


@dataclass
class ResultSummary:
    """Compact result summary for explanation/UI."""

    label: str
    sequence: List[int]
    makespan_hours: float
    primary_violations: int
    raw_violations: Optional[int] = None
    top_constraints: Dict[str, int] = field(default_factory=dict)
    date_counts: Dict[str, int] = field(default_factory=dict)
    bay_assignments: Dict[int, str] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)


@dataclass
class ComparisonSummary:
    """Before/after comparison for explanation."""

    before: ResultSummary
    after: ResultSummary
    user_request: str = ""
