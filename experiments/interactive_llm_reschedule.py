#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Run one interactive LLM Connect rescheduling case."""

# [AGENT-ADD] This script is the missing Streamlit/CLI bridge:
# natural language -> structured request -> existing scheduler rerun -> audit-grounded report.

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd
import torch

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from enhanced_environment.common.utils_core import DataConverter
from llm_interface.analysis_bundle import build_analysis_bundle, save_analysis_bundle
from llm_interface.analysis_report import save_analysis_report
from llm_interface.openai_parser import parse_request_auto
from llm_interface.reschedule import (
    build_forced_prefix_plan,
    build_manual_bay_assignments,
    build_precedence_rules,
    build_runtime_override_config,
)
from llm_interface.result_explainer import explain_comparison
from llm_interface.schemas import ComparisonSummary, ScheduleEditRequest
from llm_interface.solution_evaluator import evaluate_solution, save_solution_evaluation
from llm_interface.summary_adapters import summarize_result_rows
from llm_interface.validation import request_to_dict, validate_request
from runtime_config import get_runtime_config, set_runtime_config
from scheduling.assembly_start.action_sequence_조립착수일기준휴리스틱 import run_assembly_decoding_sequence_with_blocks
from scheduling.assembly_start.rl_assembly_scheduler import run_rl_assembly_decoding_sequence_with_blocks

from PPO.models.single_step_actor import (
    BLOCK_FEATURE_DIM,
    CONSTRAINT_BLOCK_FEATURE_DIM,
    CONSTRAINT_ENV_STATE_DIM,
    DIFF_BLOCK_FEATURE_DIM,
    DIFF_ENV_STATE_DIM,
    ENV_STATE_DIM,
    REDUCED_BLOCK_FEATURE_DIM,
    REDUCED_ENV_STATE_DIM,
    SingleStepPtrNet,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one interactive PBS rescheduling case.")
    parser.add_argument("--excel-path", required=True)
    parser.add_argument("--sheet-name", default="Sheet1")
    parser.add_argument("--method", default="lpt", choices=["lpt", "spt", "seam_min", "msf", "rl"])
    parser.add_argument("--request", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--start-date", default="2025-01-01")
    parser.add_argument("--date-offset", type=int, default=0)
    parser.add_argument("--max-days", type=int, default=20)
    parser.add_argument("--rl-model-path")
    parser.add_argument("--parser", default="deterministic", choices=["deterministic", "llm", "openai", "groq", "gemini", "ollama", "openai_compatible", "auto"])
    parser.add_argument("--llm-model", default=None)
    parser.add_argument("--llm-provider", default=None)
    parser.add_argument("--llm-base-url", default=None)
    parser.add_argument("--llm-api-key-env", default=None)
    parser.add_argument("--runtime-config-json", default="")
    parser.add_argument("--freeze-existing-prefix", action="store_true")
    parser.add_argument("--disable-current-state-baseline", action="store_true")
    parser.add_argument("--save-detailed-process", action="store_true")
    parser.add_argument("--verbose-scheduler", action="store_true")
    return parser.parse_args()


@contextmanager
def _pushd(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    merged = copy.deepcopy(base or {})
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = copy.deepcopy(value)
    return merged


def _load_runtime_json(raw_json: str) -> Dict[str, Any]:
    if not raw_json:
        return {}
    try:
        parsed = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"runtime-config-json 파싱 실패: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("runtime-config-json은 JSON object여야 합니다.")
    return parsed


def _load_blocks(excel_path: str, sheet_name: str):
    try:
        return DataConverter.excel_to_blocks_with_metadata(excel_path, sheet_name=sheet_name)
    except TypeError:
        return DataConverter.excel_to_blocks_with_metadata(excel_path)


def _metric_sequence(rows: Sequence[Dict[str, Any]]) -> List[int]:
    df = pd.DataFrame(list(rows or []))
    if df.empty or "block_id" not in df.columns:
        return []
    if "is_metric_anchor_row" in df.columns:
        mask = pd.to_numeric(df["is_metric_anchor_row"], errors="coerce").fillna(0).astype(int) == 1
        if mask.any():
            df = df.loc[mask].copy()
    if "am_sequence" in df.columns:
        df = df.sort_values("am_sequence")
    return [int(value) for value in df["block_id"].dropna().tolist()]


def _write_rows(rows: Sequence[Dict[str, Any]], path: Path) -> None:
    pd.DataFrame(list(rows or [])).to_csv(path, index=False, encoding="utf-8-sig")


def _write_trace(rows: Sequence[Dict[str, Any]], path: Path) -> Optional[str]:
    if not rows:
        return None
    pd.DataFrame(list(rows)).to_csv(path, index=False, encoding="utf-8-sig")
    return str(path)


def _selection_method(method: str) -> str:
    key = str(method or "").strip().lower()
    if key == "msf":
        return "seam_min"
    return key


def _extract_actor_state_dict(ckpt: Any) -> Dict[str, torch.Tensor]:
    if isinstance(ckpt, dict):
        if "actor_state_dict" in ckpt:
            return ckpt["actor_state_dict"]
        if "model_state_dict" in ckpt:
            return ckpt["model_state_dict"]
    return ckpt if isinstance(ckpt, dict) else {}


def _infer_feature_mode_from_dim(dim: Optional[int]) -> str:
    if dim == REDUCED_BLOCK_FEATURE_DIM:
        return "reduced"
    if dim == CONSTRAINT_BLOCK_FEATURE_DIM:
        return "constraint"
    if dim == DIFF_BLOCK_FEATURE_DIM:
        return "diff"
    if dim == BLOCK_FEATURE_DIM:
        return "full"
    return "diff"


def _load_rl_actor(model_path: str) -> Tuple[SingleStepPtrNet, torch.device]:
    if not model_path:
        raise ValueError("RL method requires --rl-model-path")
    if not Path(model_path).exists():
        raise FileNotFoundError(model_path)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    checkpoint = torch.load(model_path, map_location=device)
    actor_state = _extract_actor_state_dict(checkpoint)
    embed_w = actor_state.get("embedding.0.weight")
    embed2_w = actor_state.get("embedding.2.weight")
    feature_dim = int(embed_w.shape[1]) if embed_w is not None else DIFF_BLOCK_FEATURE_DIM
    feature_mode = _infer_feature_mode_from_dim(feature_dim)
    if feature_mode == "reduced":
        env_state_dim = REDUCED_ENV_STATE_DIM
    elif feature_mode == "constraint":
        env_state_dim = CONSTRAINT_ENV_STATE_DIM
    elif feature_mode == "diff":
        env_state_dim = DIFF_ENV_STATE_DIM
    else:
        env_state_dim = ENV_STATE_DIM
    actor_params = {
        "embedding_dim": int(embed_w.shape[0]) if embed_w is not None else 256,
        "hidden_dim": int(embed2_w.shape[0]) if embed2_w is not None else 256,
        "n_layers": 2,
        "n_heads": 4,
        "dropout": 0.1,
        "use_logit_clipping": True,
        "C": 10.0,
        "T": 0.5,
        "feature_dim": feature_dim,
        "feature_mode": feature_mode,
        "use_positional_encoding": "positional_encoding" in actor_state,
        "use_env_state": "env_state_mean" in actor_state,
        "env_state_dim": int(actor_state["env_state_mean"].shape[0]) if "env_state_mean" in actor_state else env_state_dim,
    }
    actor = SingleStepPtrNet(**actor_params).to(device)
    actor.load_state_dict(actor_state, strict=False)
    actor.eval()
    return actor, device


def _run_scheduler(
    *,
    method: str,
    blocks: List[Any],
    metadata: Dict[str, Any],
    start_date: str,
    date_offset: int,
    max_days: int,
    output_csv: Path,
    save_detailed: bool,
    forced_prefix: Optional[List[Optional[int]]] = None,
    precedence_rules: Optional[List[Tuple[int, int]]] = None,
    manual_bay_assignments: Optional[Dict[int, str]] = None,
    rl_model_path: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[Dict[str, Any]]]:
    key = str(method or "").strip().lower()
    if key == "rl":
        actor, device = _load_rl_actor(str(rl_model_path or ""))
        with torch.no_grad():
            rows, stats, trace, _ = run_rl_assembly_decoding_sequence_with_blocks(
                blocks=copy.deepcopy(blocks),
                metadata=copy.deepcopy(metadata),
                rl_agent=actor,
                device=device,
                start_date=start_date,
                max_days=max_days,
                date_offset=date_offset,
                output_csv=str(output_csv),
                save_csv=True,
                save_detailed=save_detailed,
                training_mode=False,
                forced_sequence=list(forced_prefix or []),
                allow_forced_prefix_override=bool(forced_prefix),
                precedence_rules=precedence_rules,
                manual_bay_assignments=manual_bay_assignments,
                collect_episode_data=False,
                enable_grad=False,
                collect_step_metrics=False,
            )
        return rows, stats or {}, trace or []

    result = run_assembly_decoding_sequence_with_blocks(
        blocks=copy.deepcopy(blocks),
        metadata=copy.deepcopy(metadata),
        decoding_type="assembly",
        selection_method=_selection_method(key),
        max_days=max_days,
        start_date=start_date,
        date_offset=date_offset,
        output_csv=str(output_csv),
        save_csv=True,
        save_detailed=save_detailed,
        expand_rows=True,
        forced_prefix_block_ids=list(forced_prefix or []),
        allow_forced_prefix_override=bool(forced_prefix),
        precedence_rules=precedence_rules,
        manual_bay_assignments=manual_bay_assignments,
        return_step_info=True,
    )
    if len(result) == 3:
        rows, stats, trace = result
    else:
        rows, stats = result
        trace = []
    return rows, stats or {}, trace or []


def _fill_frozen_prefix(plan: List[Optional[int]], before_sequence: Sequence[int], freeze: bool) -> List[Optional[int]]:
    if not freeze or not plan:
        return plan
    filled = list(plan)
    for idx, value in enumerate(filled):
        if value is None and idx < len(before_sequence):
            filled[idx] = int(before_sequence[idx])
    return filled


def _current_state_only_request(request: ScheduleEditRequest) -> ScheduleEditRequest:
    """Return only constraints that describe already-executed current state."""
    # [AGENT-ADD] freeze_prefix is not a schedule improvement request. It is the
    # operational state that both before and after schedules must share.
    return ScheduleEditRequest(
        raw_request=request.raw_request,
        mode=request.mode,
        preserve_existing_schedule_as_much_as_possible=request.preserve_existing_schedule_as_much_as_possible,
        constraints=[copy.deepcopy(c) for c in request.constraints if c.type == "freeze_prefix"],
        unparsed_fragments=[],
    )


def run_case(args: argparse.Namespace) -> Dict[str, Any]:
    out_dir = Path(args.output_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    # [AGENT-ADD] Interactive runs should stay readable unless the caller asks for scheduler logs.
    if not getattr(args, "verbose_scheduler", False):
        os.environ["PBS_FORCE_DEBUG"] = "0"
        os.environ["PBS_DEBUG_VERBOSE"] = "0"

    base_runtime_cfg = _load_runtime_json(args.runtime_config_json)
    set_runtime_config(copy.deepcopy(base_runtime_cfg))
    blocks, metadata = _load_blocks(args.excel_path, args.sheet_name)

    with _pushd(out_dir):
        # [AGENT-EDIT] Parse first so already-started prefix state can be applied
        # to the baseline run. Without this, before/after may compare different
        # current states and produce misleading metric deltas.
        request = parse_request_auto(
            args.request,
            current_sequence=[],
            parser_mode=args.parser,
            model=args.llm_model,
            provider=getattr(args, "llm_provider", None),
            base_url=getattr(args, "llm_base_url", None),
            api_key_env=getattr(args, "llm_api_key_env", None),
        )
        current_state_request = _current_state_only_request(request)
        current_state_forced_prefix = []
        if not bool(args.disable_current_state_baseline):
            current_state_forced_prefix = build_forced_prefix_plan(current_state_request)

        before_csv = out_dir / "before_results.csv"
        before_rows, before_stats, before_trace = _run_scheduler(
            method=args.method,
            blocks=blocks,
            metadata=metadata,
            start_date=args.start_date,
            date_offset=args.date_offset,
            max_days=args.max_days,
            output_csv=before_csv,
            save_detailed=args.save_detailed_process,
            forced_prefix=current_state_forced_prefix,
            rl_model_path=args.rl_model_path,
        )
        before_sequence = _metric_sequence(before_rows)

        errors, warnings = validate_request(request, sequence=before_sequence)
        request_json_path = out_dir / "request.json"
        request_json_path.write_text(
            json.dumps({"request": request_to_dict(request), "errors": errors, "warnings": warnings}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        if errors:
            raise ValueError("요청 검증 실패: " + " / ".join(errors))

        forced_prefix = _fill_frozen_prefix(
            build_forced_prefix_plan(request),
            before_sequence,
            freeze=bool(args.freeze_existing_prefix),
        )
        precedence_rules = build_precedence_rules(request)
        manual_bays = build_manual_bay_assignments(request)
        interactive_runtime_cfg = build_runtime_override_config(request, start_date=args.start_date)
        after_runtime_cfg = _deep_merge(base_runtime_cfg, interactive_runtime_cfg)
        set_runtime_config(after_runtime_cfg)

        after_csv = out_dir / "after_results.csv"
        after_rows, after_stats, after_trace = _run_scheduler(
            method=args.method,
            blocks=blocks,
            metadata=metadata,
            start_date=args.start_date,
            date_offset=args.date_offset,
            max_days=args.max_days,
            output_csv=after_csv,
            save_detailed=args.save_detailed_process,
            forced_prefix=forced_prefix,
            precedence_rules=precedence_rules,
            manual_bay_assignments=manual_bays,
            rl_model_path=args.rl_model_path,
        )

        before_trace_csv = _write_trace(before_trace, out_dir / "before_decision_trace.csv")
        after_trace_csv = _write_trace(after_trace, out_dir / "after_decision_trace.csv")
        if not before_csv.exists():
            _write_rows(before_rows, before_csv)
        if not after_csv.exists():
            _write_rows(after_rows, after_csv)

        before_summary = summarize_result_rows(before_rows, label=f"{args.method}_before")
        after_summary = summarize_result_rows(after_rows, label=f"{args.method}_after")
        comparison = ComparisonSummary(before=before_summary, after=after_summary, user_request=args.request)
        explanation = explain_comparison(comparison)
        bundle = build_analysis_bundle(
            comparison,
            user_request=args.request,
            before_stats=before_stats,
            after_stats=after_stats,
            before_trace_csv=before_trace_csv,
            after_trace_csv=after_trace_csv,
            explanation=explanation,
        )
        bundle_path = out_dir / "analysis_bundle.json"
        report_path = out_dir / "analysis_report.md"
        save_analysis_bundle(bundle, bundle_path)
        save_analysis_report(bundle, report_path)
        solution_eval = evaluate_solution(
            request=request,
            before_rows=before_rows,
            after_rows=after_rows,
        )
        solution_eval_paths = save_solution_evaluation(solution_eval, out_dir)

        summary = {
            "method": args.method,
            "request": args.request,
            "request_json": str(request_json_path),
            "before_csv": str(before_csv),
            "after_csv": str(after_csv),
            "before_decision_trace_csv": before_trace_csv,
            "after_decision_trace_csv": after_trace_csv,
            "analysis_bundle_json": str(bundle_path),
            "analysis_report_md": str(report_path),
            **solution_eval_paths,
            "explanation": explanation,
            "before": before_summary.__dict__,
            "after": after_summary.__dict__,
            "before_stats": before_stats,
            "after_stats": after_stats,
            "forced_prefix": forced_prefix,
            "precedence_rules": precedence_rules,
            "manual_bay_assignments": manual_bays,
            "current_state_forced_prefix": current_state_forced_prefix,
            "runtime_override": interactive_runtime_cfg,
            "solution_evaluation": solution_eval,
            "warnings": warnings,
        }
        summary_path = out_dir / "summary.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return summary


def main() -> None:
    args = _parse_args()
    summary = run_case(args)
    print(json.dumps({
        "summary_json": str(Path(args.output_dir).resolve() / "summary.json"),
        "before_csv": summary["before_csv"],
        "after_csv": summary["after_csv"],
        "analysis_bundle_json": summary["analysis_bundle_json"],
        "solution_evaluation_json": summary["solution_evaluation_json"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
