"""Evaluation utilities for LLM Connect.

# [AGENT-ADD] These metrics evaluate the LLM side-car itself, not scheduler
# optimality. Makespan and violations remain scheduler metrics.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Tuple

import pandas as pd

from .openai_parser import parse_request_auto
from .schemas import EditConstraint, ScheduleEditRequest
from .validation import request_from_dict, request_to_dict, validate_request


CONSTRAINT_FIELDS_BY_TYPE: Dict[str, Tuple[str, ...]] = {
    "fixed_position": ("block_id", "position"),
    "precedence": ("before_block_id", "after_block_id"),
    "manual_bay_assignment": ("block_id", "bay"),
    "constraint_toggle": ("target", "enabled", "scope"),
    "bias_components": ("components",),
    "date_specific_daily_block_cap": ("date_key", "date_token", "max_blocks"),
    "date_specific_daily_seam_cap": ("date_key", "date_token", "seam_limit"),
}


def _constraint_signature(constraint: EditConstraint | Dict[str, Any]) -> Tuple[Any, ...]:
    if isinstance(constraint, EditConstraint):
        payload = request_to_dict(ScheduleEditRequest(raw_request="", constraints=[constraint]))["constraints"][0]
    else:
        payload = dict(constraint)
    ctype = str(payload.get("type") or "")
    fields = CONSTRAINT_FIELDS_BY_TYPE.get(ctype, tuple(sorted(payload.keys())))
    values: List[Any] = [ctype]
    for field in fields:
        value = payload.get(field)
        if isinstance(value, list):
            value = tuple(sorted(int(item) for item in value))
        elif isinstance(value, str):
            value = value.upper() if field == "bay" else value
        values.append((field, value))
    return tuple(values)


def _constraint_set(constraints: Iterable[EditConstraint | Dict[str, Any]]) -> set[Tuple[Any, ...]]:
    return {_constraint_signature(constraint) for constraint in constraints}


def _safe_json_loads(raw: Any) -> Any:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return None
    if isinstance(raw, (dict, list)):
        return raw
    text = str(raw).strip()
    if not text:
        return None
    return json.loads(text)


def _load_cases(path: str | Path) -> List[Dict[str, Any]]:
    case_path = Path(path)
    if case_path.suffix.lower() in {".jsonl", ".ndjson"}:
        cases: List[Dict[str, Any]] = []
        for line in case_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped:
                cases.append(json.loads(stripped))
        return cases
    if case_path.suffix.lower() == ".json":
        loaded = json.loads(case_path.read_text(encoding="utf-8"))
        return loaded if isinstance(loaded, list) else loaded.get("cases", [])
    df = pd.read_csv(case_path, encoding="utf-8-sig")
    cases = []
    for _, row in df.iterrows():
        item = row.to_dict()
        if "expected_constraints" in item:
            item["expected_constraints"] = _safe_json_loads(item.get("expected_constraints"))
        cases.append(item)
    return cases


def evaluate_parse_cases(
    cases: Sequence[Dict[str, Any]],
    *,
    parser_mode: str = "deterministic",
    sequence: Sequence[int] | None = None,
    llm_model: str | None = None,
    llm_provider: str | None = None,
    llm_base_url: str | None = None,
    llm_api_key_env: str | None = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Evaluate request understanding and schema safety on a labeled suite."""

    rows: List[Dict[str, Any]] = []
    for idx, case in enumerate(cases, 1):
        case_id = str(case.get("case_id") or f"case_{idx:03d}")
        request_text = str(case.get("request") or case.get("raw_request") or "")
        expected_constraints = case.get("expected_constraints")
        if expected_constraints is None and isinstance(case.get("expected"), dict):
            expected_constraints = case["expected"].get("constraints")
        if expected_constraints is None:
            expected_constraints = []

        started = time.perf_counter()
        error_text = ""
        try:
            parsed = parse_request_auto(
                request_text,
                current_sequence=sequence or [],
                parser_mode=parser_mode,
                model=llm_model,
                provider=llm_provider,
                base_url=llm_base_url,
                api_key_env=llm_api_key_env,
            )
        except Exception as exc:
            parsed = ScheduleEditRequest(raw_request=request_text)
            error_text = str(exc)
        latency_ms = (time.perf_counter() - started) * 1000.0

        validation_errors, validation_warnings = validate_request(parsed, sequence=sequence)
        predicted_set = _constraint_set(parsed.constraints)
        expected_set = _constraint_set(expected_constraints)
        intersection = predicted_set & expected_set
        precision = len(intersection) / len(predicted_set) if predicted_set else (1.0 if not expected_set else 0.0)
        recall = len(intersection) / len(expected_set) if expected_set else (1.0 if not predicted_set else 0.0)
        f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)

        rows.append({
            "case_id": case_id,
            "request": request_text,
            "parser_mode": parser_mode,
            "llm_provider": llm_provider or "",
            "validation_ok": not validation_errors,
            "parse_exact_match": predicted_set == expected_set,
            "constraint_precision": precision,
            "constraint_recall": recall,
            "constraint_f1": f1,
            "predicted_count": len(predicted_set),
            "expected_count": len(expected_set),
            "unparsed_count": len(parsed.unparsed_fragments),
            "parser_error": error_text,
            "validation_errors": " | ".join(validation_errors),
            "validation_warnings": " | ".join(validation_warnings),
            "latency_ms": round(latency_ms, 3),
            "predicted_constraints_json": json.dumps(request_to_dict(parsed)["constraints"], ensure_ascii=False),
            "expected_constraints_json": json.dumps(expected_constraints, ensure_ascii=False),
        })

    detail = pd.DataFrame(rows)
    summary = pd.DataFrame([{
        "cases": int(len(detail)),
        "parser_mode": parser_mode,
        "llm_provider": llm_provider or "",
        "validation_pass_rate": float(detail["validation_ok"].mean()) if len(detail) else 0.0,
        "parse_exact_match_rate": float(detail["parse_exact_match"].mean()) if len(detail) else 0.0,
        "constraint_f1_mean": float(detail["constraint_f1"].mean()) if len(detail) else 0.0,
        "constraint_precision_mean": float(detail["constraint_precision"].mean()) if len(detail) else 0.0,
        "constraint_recall_mean": float(detail["constraint_recall"].mean()) if len(detail) else 0.0,
        "unparsed_rate": float((detail["unparsed_count"] > 0).mean()) if len(detail) else 0.0,
        "parser_error_rate": float((detail["parser_error"].astype(str) != "").mean()) if len(detail) else 0.0,
        "latency_ms_mean": float(detail["latency_ms"].mean()) if len(detail) else 0.0,
    }])
    return detail, summary


def _direction_ok(explanation: str, value: Any, metric_name: str) -> bool:
    try:
        delta = float(value)
    except Exception:
        return True
    text = str(explanation or "")
    if abs(delta) < 1e-9:
        return metric_name in text and "변화는 없습니다" in text
    if delta > 0:
        return metric_name in text and "증가" in text
    return metric_name in text and "감소" in text


def evaluate_analysis_bundle(bundle: Dict[str, Any]) -> Dict[str, Any]:
    """Score whether the explanation is grounded in request, metrics, and trace."""

    request = str(bundle.get("request", "") or "")
    explanation = str(bundle.get("explanation", "") or "")
    delta = bundle.get("delta", {}) or {}
    trace = bundle.get("trace", {}) or {}
    before = bundle.get("before", {}) or {}
    after = bundle.get("after", {}) or {}
    constraint_changes = delta.get("constraint_changes", []) or []

    request_echo = bool(request and request in explanation)
    makespan_ok = _direction_ok(explanation, delta.get("makespan_hours"), "makespan")
    primary_ok = _direction_ok(explanation, delta.get("primary_violations"), "primary violation")
    raw_ok = _direction_ok(explanation, delta.get("raw_violations"), "raw violation") if delta.get("raw_violations") is not None else True
    metric_delta_consistency = makespan_ok and primary_ok and raw_ok

    trace_files_present = bool(trace.get("before_csv") or trace.get("after_csv"))
    trace_evidence_present = bool(trace.get("changed_steps_preview") or trace.get("forced_steps_after") or trace.get("before_preview"))
    trace_grounding = trace_files_present and trace_evidence_present

    if constraint_changes:
        top_id = str(constraint_changes[0].get("constraint_id") or "")
        constraint_grounding = bool(top_id and top_id in explanation)
    else:
        constraint_grounding = "주요 제약" in explanation or "제약" in explanation

    output_completeness = bool(before and after and explanation)
    scores = {
        "request_echo": float(request_echo),
        "metric_delta_consistency": float(metric_delta_consistency),
        "trace_grounding": float(trace_grounding),
        "constraint_grounding": float(constraint_grounding),
        "output_completeness": float(output_completeness),
    }
    grounded_score = sum(scores.values()) / len(scores)
    return {
        **scores,
        "llm_grounded_explanation_score": grounded_score,
        "makespan_delta": delta.get("makespan_hours"),
        "primary_delta": delta.get("primary_violations"),
        "raw_delta": delta.get("raw_violations"),
        "changed_steps_preview_count": len(trace.get("changed_steps_preview") or []),
        "forced_steps_after_count": len(trace.get("forced_steps_after") or []),
    }


def evaluate_bundle_files(paths: Sequence[str | Path]) -> Tuple[pd.DataFrame, pd.DataFrame]:
    rows: List[Dict[str, Any]] = []
    for path in paths:
        bundle_path = Path(path)
        bundle = json.loads(bundle_path.read_text(encoding="utf-8"))
        row = evaluate_analysis_bundle(bundle)
        row["bundle_path"] = str(bundle_path)
        row["request"] = str(bundle.get("request", "") or "")
        rows.append(row)
    detail = pd.DataFrame(rows)
    score_cols = [
        "request_echo",
        "metric_delta_consistency",
        "trace_grounding",
        "constraint_grounding",
        "output_completeness",
        "llm_grounded_explanation_score",
    ]
    summary = pd.DataFrame([{
        "bundles": int(len(detail)),
        **{f"{col}_mean": float(detail[col].mean()) if len(detail) else 0.0 for col in score_cols},
    }])
    return detail, summary


def write_parse_evaluation(
    cases_path: str | Path,
    output_dir: str | Path,
    *,
    parser_mode: str = "deterministic",
    sequence: Sequence[int] | None = None,
    llm_model: str | None = None,
    llm_provider: str | None = None,
    llm_base_url: str | None = None,
    llm_api_key_env: str | None = None,
) -> Tuple[Path, Path]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = _load_cases(cases_path)
    detail, summary = evaluate_parse_cases(
        cases,
        parser_mode=parser_mode,
        sequence=sequence,
        llm_model=llm_model,
        llm_provider=llm_provider,
        llm_base_url=llm_base_url,
        llm_api_key_env=llm_api_key_env,
    )
    detail_path = out_dir / "llm_parse_eval_detail.csv"
    summary_path = out_dir / "llm_parse_eval_summary.csv"
    detail.to_csv(detail_path, index=False, encoding="utf-8-sig")
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    return detail_path, summary_path


def write_bundle_evaluation(paths: Sequence[str | Path], output_dir: str | Path) -> Tuple[Path, Path]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    detail, summary = evaluate_bundle_files(paths)
    detail_path = out_dir / "llm_explanation_eval_detail.csv"
    summary_path = out_dir / "llm_explanation_eval_summary.csv"
    detail.to_csv(detail_path, index=False, encoding="utf-8-sig")
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    return detail_path, summary_path
