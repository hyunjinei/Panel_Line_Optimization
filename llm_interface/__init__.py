"""LLM-assisted interactive scheduling helpers.

# [AGENT-ADD] This package is intentionally side-car only.
# It does not modify the core PBS scheduling algorithm.
"""

from .parser import parse_natural_language_request
from .openai_parser import parse_request_auto, parse_with_openai
from .reschedule import (
    build_forced_prefix_plan,
    build_manual_bay_assignments,
    build_precedence_rules,
    build_runtime_override_config,
)
from .analysis_bundle import build_analysis_bundle, save_analysis_bundle
from .result_explainer import explain_comparison, explain_single_result
from .schemas import ComparisonSummary, EditConstraint, ResultSummary, ScheduleEditRequest
from .sequence_editor import apply_edit_request
from .summary_adapters import summarize_result_rows, summarize_results_csv
from .validation import request_from_dict, request_to_dict, validate_request
from .evaluation import evaluate_analysis_bundle, evaluate_parse_cases

__all__ = [
    "ComparisonSummary",
    "EditConstraint",
    "ResultSummary",
    "ScheduleEditRequest",
    "parse_natural_language_request",
    "parse_request_auto",
    "parse_with_openai",
    "build_forced_prefix_plan",
    "build_precedence_rules",
    "build_manual_bay_assignments",
    "build_runtime_override_config",
    "build_analysis_bundle",
    "save_analysis_bundle",
    "explain_comparison",
    "explain_single_result",
    "apply_edit_request",
    "summarize_result_rows",
    "summarize_results_csv",
    "request_from_dict",
    "request_to_dict",
    "validate_request",
    "evaluate_analysis_bundle",
    "evaluate_parse_cases",
]
