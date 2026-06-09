# [AGENT-ADD] Constraint-aware Cosine-Jaccard constructive insertion heuristic.
"""CA-CJH-Insertion for panel-line scheduling."""

from __future__ import annotations

import copy
import contextlib
import os
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from scheduling.assembly_start.action_sequence_조립착수일기준휴리스틱 import (
    run_assembly_decoding_sequence_with_blocks,
)
from enhanced_environment.common.utils_core import get_line_group_and_workshop_code
from scheduling.common.cjh_panel_features import (
    EPSILON,
    compute_cjh_scores,
)


_CA_CJH_WORKER_STATE: Dict[str, object] = {}


def _set_single_thread_env() -> None:
    """[AGENT-ADD] Prevent CPU oversubscription inside DES worker processes."""
    for key in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
        os.environ.setdefault(key, "1")


def _as_bool(value: object, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _as_float_config(value: object, default: float) -> float:
    """[AGENT-ADD] Parse numeric config without treating 0.0 as missing."""
    if value in (None, "", "null", "none", "None"):
        return float(default)
    try:
        return float(value)
    except Exception:
        return float(default)


def _block_id(block: object) -> int:
    return int(getattr(block, "block_id", block.get("block_id") if isinstance(block, dict) else 0))


def _normal_sequence(sequence: Iterable[int], block_ids: Sequence[int]) -> List[int]:
    allowed = set(int(v) for v in block_ids)
    seen = set()
    normalized: List[int] = []
    for value in sequence or []:
        block_id = int(value)
        if block_id in allowed and block_id not in seen:
            normalized.append(block_id)
            seen.add(block_id)
    return normalized


def _block_date_value(block: object) -> object:
    """[AGENT-ADD] Date key used for static routing-order gating."""
    value = getattr(block, "assembly_start_date", None)
    if hasattr(value, "date"):
        return value.date()
    return value


def _workshop_order_key(block: object) -> str:
    """[AGENT-ADD] Match ROUTING_WORKSHOP_ORDER key used by masking."""
    try:
        line_group, workshop_code = get_line_group_and_workshop_code(block)
        return str(workshop_code or line_group or "").strip()
    except Exception:
        return str(getattr(block, "assembly_workshop_code", "") or getattr(block, "line_group", "") or "").strip()


def _has_unselected_workshop_predecessor(
    block_id: int,
    remaining_ids: Sequence[int],
    blocks_dict: Dict[int, object],
) -> bool:
    """[AGENT-ADD] True if another remaining block must precede this block."""
    block = blocks_dict[int(block_id)]
    workshop_key = _workshop_order_key(block)
    block_date = _block_date_value(block)
    if not workshop_key or not block_date:
        return False
    for other_id in remaining_ids:
        other_id = int(other_id)
        if other_id == int(block_id):
            continue
        other = blocks_dict.get(other_id)
        if other is None or _workshop_order_key(other) != workshop_key:
            continue
        other_date = _block_date_value(other)
        if other_date and other_date < block_date:
            return True
    return False


def _build_workshop_feasible_priority_order(
    priority_ids: Sequence[int],
    selected_ids: Iterable[int],
    blocks_dict: Dict[int, object],
) -> List[int]:
    """[AGENT-ADD] Difficulty order with ROUTING_WORKSHOP_ORDER respected.

    This keeps the global CA-CJH score order as the tie-breaker, but prevents a
    later assembly-start block in the same workshop from being selected before
    all earlier workshop blocks have been selected.
    """
    selected = {int(v) for v in selected_ids}
    remaining = [int(v) for v in priority_ids if int(v) not in selected]
    ordered: List[int] = []
    while remaining:
        eligible = [
            block_id
            for block_id in remaining
            if not _has_unselected_workshop_predecessor(block_id, remaining, blocks_dict)
        ]
        if not eligible:
            # Cycles or missing dates should not crash the experiment; fall back
            # to the CA-CJH priority order for the unresolved remainder.
            eligible = list(remaining)
        chosen = next(block_id for block_id in remaining if block_id in set(eligible))
        ordered.append(chosen)
        remaining.remove(chosen)
    return ordered


def _filter_positions_by_workshop_order(
    sequence: Sequence[int],
    candidate_block_id: int,
    positions: Sequence[int],
    blocks_dict: Dict[int, object],
) -> List[int]:
    """[AGENT-ADD] Keep insertion positions that cannot invert workshop order."""
    candidate = blocks_dict[int(candidate_block_id)]
    candidate_key = _workshop_order_key(candidate)
    candidate_date = _block_date_value(candidate)
    if not candidate_key or not candidate_date:
        return list(positions)

    min_position = 0
    max_position = len(sequence)
    for idx, block_id in enumerate(sequence):
        block = blocks_dict.get(int(block_id))
        if block is None or _workshop_order_key(block) != candidate_key:
            continue
        block_date = _block_date_value(block)
        if not block_date:
            continue
        if block_date < candidate_date:
            min_position = max(min_position, idx + 1)
        elif block_date > candidate_date:
            max_position = min(max_position, idx)

    filtered = [int(pos) for pos in positions if min_position <= int(pos) <= max_position]
    return filtered or list(positions)


def _primary_violation_count(stats: Optional[Dict[str, object]]) -> int:
    stats = stats or {}
    for key in ("total_violations_primary_train", "total_violations_primary", "total_violations_train", "total_violations"):
        if key in stats:
            try:
                return int(stats.get(key) or 0)
            except Exception:
                return 0
    return 0


def _raw_violation_count(stats: Optional[Dict[str, object]]) -> int:
    stats = stats or {}
    try:
        return int(stats.get("total_violations_raw", stats.get("total_violations", 0)) or 0)
    except Exception:
        return 0


def _longi_summary(schedule_results: Sequence[Dict[str, object]]) -> Tuple[float, float, float, Dict[int, str]]:
    longi_35a = 0.0
    longi_36b = 0.0
    assignments: Dict[int, str] = {}
    for row in schedule_results or []:
        bay = str(row.get("assigned_bay") or row.get("original_bay") or "").upper()
        block_id = row.get("block_id")
        if block_id is not None and block_id not in assignments:
            assignments[int(block_id)] = bay
        longi = float(row.get("longi_count", 0) or 0)
        if "35A" in bay:
            longi_35a += longi
        elif "36B" in bay:
            longi_36b += longi
    return longi_35a, longi_36b, abs(longi_35a - longi_36b), assignments


def evaluate_panel_sequence_for_cjh(
    sequence: Sequence[int],
    blocks: List[object],
    metadata: Dict,
    config: Optional[Dict[str, object]] = None,
    partial: bool = True,
    save_csv: bool = False,
    save_detailed: bool = False,
) -> Dict[str, object]:
    """Evaluate a CA-CJH tentative sequence through the existing DES path.

    The current repository does not expose a partial-only DES metric. Therefore
    a partial insertion prefix is evaluated by replaying it as a forced prefix and
    completing the remaining blocks through the configured deterministic policy.
    Constraint violations are still audited by the existing final audit path.
    """
    config = config or {}
    block_ids = [_block_id(block) for block in blocks]
    prefix = _normal_sequence(sequence, block_ids)
    completion_sequence = _normal_sequence(config.get("completion_sequence") or [], block_ids)
    if completion_sequence:
        forced_prefix = _normal_sequence([*prefix, *completion_sequence], block_ids)
    else:
        forced_prefix = prefix

    completion_policy = str(config.get("completion_policy", "priority") or "priority").strip().lower()
    if completion_policy in {"none", "cjh_priority"}:
        completion_policy = "priority"

    output_csv = str(config.get("output_csv") or "ca_cjh_evaluation_results.csv")
    max_days = int(config.get("assembly_max_days", config.get("max_days", 20)) or 20)
    start_date = str(config.get("start_date", "2025-01-01") or "2025-01-01")
    date_offset = int(config.get("assembly_date_offset", config.get("date_offset", 0)) or 0)
    allow_override = _as_bool(config.get("allow_forced_prefix_override"), True)
    quiet = _as_bool(config.get("quiet"), True)

    try:
        with contextlib.ExitStack() as stack:
            if quiet:
                devnull = stack.enter_context(open(os.devnull, "w"))
                stack.enter_context(contextlib.redirect_stdout(devnull))
                stack.enter_context(contextlib.redirect_stderr(devnull))
            schedule_results, statistics = run_assembly_decoding_sequence_with_blocks(
                blocks=copy.deepcopy(blocks),
                metadata=copy.deepcopy(metadata),
                decoding_type="assembly",
                selection_method=completion_policy,
                max_days=max_days,
                start_date=start_date,
                date_offset=date_offset,
                output_csv=output_csv,
                save_csv=save_csv,
                save_detailed=save_detailed,
                forced_sequence=None,
                expand_rows=True,
                forced_prefix_block_ids=forced_prefix,
                allow_forced_prefix_override=allow_override,
            )
        statistics = statistics or {}
        primary = _primary_violation_count(statistics)
        raw = _raw_violation_count(statistics)
        expected = int(statistics.get("total_blocks_expected", len(blocks)) or len(blocks))
        processed = int(statistics.get("total_blocks_processed", len(schedule_results or [])) or 0)
        missing = max(0, expected - processed)
        hard_count = int(primary + missing)
        soft_count = int(max(0, raw - primary))
        longi_35a, longi_36b, longi_abs_diff, bay_assignments = _longi_summary(schedule_results)
        return {
            "error": False,
            "hard_violation_count": hard_count,
            "soft_violation_count": soft_count,
            # [AGENT-EDIT] CA-CJH objective must count soft/relaxation events
            # before makespan, while hard violations remain absolutely first.
            "total_violation_count": int(hard_count + soft_count),
            "raw_violation_count": int(raw),
            "missing_blocks": int(missing),
            "makespan_hours": float(statistics.get("makespan_hours", 0.0) or 0.0),
            "longi_35a": float(longi_35a),
            "longi_36b": float(longi_36b),
            "longi_abs_diff": float(longi_abs_diff),
            "bay_assignments": bay_assignments,
            "schedule_results": schedule_results,
            "statistics": statistics,
        }
    except Exception as exc:
        return {
            "error": True,
            "error_message": str(exc),
            "hard_violation_count": 10 ** 9,
            "soft_violation_count": 10 ** 9,
            "total_violation_count": 10 ** 9,
            "raw_violation_count": 10 ** 9,
            "missing_blocks": len(blocks),
            "makespan_hours": float("inf"),
            "longi_35a": 0.0,
            "longi_36b": 0.0,
            "longi_abs_diff": float("inf"),
            "bay_assignments": {},
            "schedule_results": [],
            "statistics": {},
        }


def compute_local_window_score(
    sequence: Sequence[int],
    candidate_block_id: int,
    insertion_position: int,
    score_context: Dict[str, object],
    w_cos: float = 0.5,
    w_jac: float = 0.5,
) -> Tuple[float, float, float]:
    """Compute local-window Cosine-Jaccard smoothing score for one insertion."""
    block_id_to_row = score_context.get("block_id_to_row", {})
    X_norm = score_context.get("X_norm")
    baseline = score_context.get("baseline")
    trimmed_std = score_context.get("trimmed_std")
    if X_norm is None or baseline is None or trimmed_std is None or not block_id_to_row:
        return 0.0, 0.0, 0.0

    current = list(sequence or [])
    t = len(current)
    pos = int(insertion_position)
    if t <= 0:
        window_ids = [int(candidate_block_id)]
    elif pos <= 0:
        window_ids = [int(candidate_block_id), int(current[0])]
    elif pos >= t:
        window_ids = [int(current[-1]), int(candidate_block_id)]
    else:
        window_ids = [int(current[pos - 1]), int(candidate_block_id), int(current[pos])]

    rows = [int(block_id_to_row[block_id]) for block_id in window_ids if block_id in block_id_to_row]
    if not rows:
        return 0.0, 0.0, 0.0
    x_loc = np.mean(X_norm[rows, :], axis=0)
    cos = float(np.dot(x_loc, baseline) / (np.linalg.norm(x_loc) * np.linalg.norm(baseline) + EPSILON))
    inside = (x_loc >= baseline - trimmed_std) & (x_loc <= baseline + trimmed_std)
    jac = float(inside.sum() / max(1, len(x_loc)))
    score = float(w_cos) * cos + float(w_jac) * jac
    return cos, jac, score


def build_insertion_trace_row(
    step: int,
    candidate_block_id: int,
    insertion_position: int,
    tentative_sequence: Sequence[int],
    evaluation: Dict[str, object],
    local_cosine_score: float,
    local_jaccard_normal_score: float,
    local_similarity_score: float,
    insertion_cost: float,
    objective_key: Tuple[float, ...],
    selected_position: Optional[int] = None,
    selected: bool = False,
    reason: str = "",
    worker_error: str = "",
    elapsed_sec: float = 0.0,
) -> Dict[str, object]:
    """Build one trace row for paper-level method auditing."""
    return {
        "step": int(step),
        "candidate_block_id": int(candidate_block_id),
        "insertion_position": int(insertion_position),
        "tentative_sequence": " ".join(str(v) for v in tentative_sequence),
        "hard_violation_count": int(evaluation.get("hard_violation_count", 0) or 0),
        "soft_violation_count": int(evaluation.get("soft_violation_count", 0) or 0),
        "total_violation_count": int(evaluation.get("total_violation_count", 0) or 0),
        "raw_violation_count": int(evaluation.get("raw_violation_count", 0) or 0),
        "makespan_hours": float(evaluation.get("makespan_hours", 0.0) or 0.0),
        "local_cosine_score": float(local_cosine_score),
        "local_jaccard_normal_score": float(local_jaccard_normal_score),
        "local_similarity_score": float(local_similarity_score),
        "insertion_cost": float(insertion_cost),
        "longi_35a": float(evaluation.get("longi_35a", 0.0) or 0.0),
        "longi_36b": float(evaluation.get("longi_36b", 0.0) or 0.0),
        "longi_abs_diff": float(evaluation.get("longi_abs_diff", 0.0) or 0.0),
        "objective_key": "|".join(f"{float(v):.10g}" for v in objective_key),
        "selected": bool(selected),
        "worker_error": worker_error,
        "elapsed_sec": float(elapsed_sec),
        "selected_position": "" if selected_position is None else int(selected_position),
        "reason": reason,
    }


def save_cjh_insertion_trace(trace_rows: List[Dict[str, object]], output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    path = os.path.join(output_dir, "insertion_trace.csv")
    pd.DataFrame(trace_rows).to_csv(path, index=False, encoding="utf-8-sig")
    return path


def _candidate_positions(
    sequence: Sequence[int],
    candidate_block_id: int,
    score_context: Dict[str, object],
    beam_width: Optional[int],
    w_cos: float,
    w_jac: float,
) -> List[int]:
    positions = list(range(len(sequence) + 1))
    if beam_width is None or int(beam_width) <= 0 or len(positions) <= int(beam_width):
        return positions
    scored = []
    for pos in positions:
        _, _, local_score = compute_local_window_score(sequence, candidate_block_id, pos, score_context, w_cos=w_cos, w_jac=w_jac)
        scored.append((pos, local_score))
    selected = {0, len(sequence)}
    for pos, _ in sorted(scored, key=lambda item: item[1], reverse=True):
        selected.add(pos)
        if len(selected) >= int(beam_width):
            break
    return sorted(selected)


def _objective_key_for_evaluation(
    evaluation: Dict[str, object],
    local_similarity_score: float,
    alpha: float,
    objective_mode: str,
    include_longi_balance: bool,
    insertion_position: int,
) -> Tuple[float, ...]:
    """[AGENT-ADD] Compute the CA-CJH insertion objective key."""
    makespan = float(evaluation.get("makespan_hours", float("inf")) or float("inf"))
    longi_diff = float(evaluation.get("longi_abs_diff", 0.0) or 0.0) if include_longi_balance else 0.0
    hard_count = float(evaluation.get("hard_violation_count", 10 ** 9) or 0)
    total_count = float(evaluation.get("total_violation_count", 10 ** 9) or 0)
    local_score = float(local_similarity_score or 0.0)
    position = float(insertion_position)

    if str(objective_mode or "").strip().lower() == "cost":
        return (
            hard_count,
            total_count,
            makespan - float(alpha or 0.0) * local_score,
            longi_diff,
            position,
        )

    return (
        hard_count,
        total_count,
        makespan,
        longi_diff,
        -local_score,
        position,
    )


def _init_ca_cjh_worker(
    blocks: List[object],
    metadata: Dict,
    eval_base_cfg: Dict[str, object],
    score_context: Dict[str, object],
    w_cos: float,
    w_jac: float,
    alpha: float,
    objective_mode: str,
    include_longi_balance: bool,
    completion_policy: str,
) -> None:
    """[AGENT-ADD] Initializer for CA-CJH ProcessPool workers."""
    _set_single_thread_env()
    _CA_CJH_WORKER_STATE.clear()
    _CA_CJH_WORKER_STATE.update({
        "blocks": blocks,
        "metadata": metadata,
        "eval_base_cfg": eval_base_cfg,
        "score_context": score_context,
        "w_cos": float(w_cos),
        "w_jac": float(w_jac),
        "alpha": float(alpha),
        "objective_mode": str(objective_mode),
        "include_longi_balance": bool(include_longi_balance),
        "completion_policy": str(completion_policy),
    })


def _evaluate_ca_cjh_position_worker(task: Dict[str, object]) -> Dict[str, object]:
    """[AGENT-ADD] Evaluate one insertion position in an isolated worker."""
    step_start = time.perf_counter()
    try:
        state = _CA_CJH_WORKER_STATE
        blocks = state["blocks"]
        metadata = state["metadata"]
        eval_base_cfg = dict(state["eval_base_cfg"])
        score_context = state["score_context"]
        w_cos = float(state["w_cos"])
        w_jac = float(state["w_jac"])
        alpha = float(state["alpha"])
        objective_mode = str(state["objective_mode"])
        include_longi_balance = bool(state["include_longi_balance"])
        completion_policy = str(state["completion_policy"])

        step = int(task["step"])
        candidate_block_id = int(task["candidate_block_id"])
        insertion_position = int(task["insertion_position"])
        sequence = [int(v) for v in task.get("sequence", [])]
        remaining_priority = [int(v) for v in task.get("remaining_priority", [])]

        tentative_sequence = list(sequence)
        tentative_sequence.insert(insertion_position, candidate_block_id)
        completion_sequence = [bid for bid in remaining_priority if bid not in tentative_sequence]

        eval_cfg = dict(eval_base_cfg)
        if completion_policy == "cjh_priority":
            eval_cfg["completion_sequence"] = completion_sequence

        evaluation = evaluate_panel_sequence_for_cjh(
            tentative_sequence,
            blocks,
            metadata,
            eval_cfg,
            partial=True,
            save_csv=False,
            save_detailed=False,
        )
        local_cos, local_jac, local_score = compute_local_window_score(
            sequence,
            candidate_block_id,
            insertion_position,
            score_context,
            w_cos=w_cos,
            w_jac=w_jac,
        )
        objective_key = _objective_key_for_evaluation(
            evaluation,
            local_similarity_score=local_score,
            alpha=alpha,
            objective_mode=objective_mode,
            include_longi_balance=include_longi_balance,
            insertion_position=insertion_position,
        )
        worker_error = str(evaluation.get("error_message") or "") if evaluation.get("error") else ""
        row = build_insertion_trace_row(
            step=step,
            candidate_block_id=candidate_block_id,
            insertion_position=insertion_position,
            tentative_sequence=tentative_sequence,
            evaluation=evaluation,
            local_cosine_score=local_cos,
            local_jaccard_normal_score=local_jac,
            local_similarity_score=local_score,
            insertion_cost=float(evaluation.get("makespan_hours", float("inf")) or float("inf")) - alpha * float(local_score),
            objective_key=objective_key,
            selected_position=None,
            selected=False,
            reason="candidate",
            worker_error=worker_error,
            elapsed_sec=time.perf_counter() - step_start,
        )
        return {
            "position": insertion_position,
            "tentative_sequence": tentative_sequence,
            "objective_key": objective_key,
            "row": row,
            "worker_error": worker_error,
        }
    except Exception as exc:
        insertion_position = int(task.get("insertion_position", -1))
        candidate_block_id = int(task.get("candidate_block_id", -1))
        sequence = [int(v) for v in task.get("sequence", [])]
        tentative_sequence = list(sequence)
        if insertion_position >= 0:
            tentative_sequence.insert(insertion_position, candidate_block_id)
        evaluation = {
            "hard_violation_count": 10 ** 9,
            "soft_violation_count": 10 ** 9,
            "total_violation_count": 10 ** 9,
            "raw_violation_count": 10 ** 9,
            "makespan_hours": float("inf"),
            "longi_35a": 0.0,
            "longi_36b": 0.0,
            "longi_abs_diff": float("inf"),
        }
        objective_key = (10 ** 9, 10 ** 9, float("inf"), float("inf"), 0.0, float(insertion_position))
        row = build_insertion_trace_row(
            step=int(task.get("step", 0)),
            candidate_block_id=candidate_block_id,
            insertion_position=insertion_position,
            tentative_sequence=tentative_sequence,
            evaluation=evaluation,
            local_cosine_score=0.0,
            local_jaccard_normal_score=0.0,
            local_similarity_score=0.0,
            insertion_cost=float("inf"),
            objective_key=objective_key,
            selected_position=None,
            selected=False,
            reason="worker_error",
            worker_error=str(exc),
            elapsed_sec=time.perf_counter() - step_start,
        )
        return {
            "position": insertion_position,
            "tentative_sequence": tentative_sequence,
            "objective_key": objective_key,
            "row": row,
            "worker_error": str(exc),
        }


def run_ca_cjh_insertion(
    blocks: List[object],
    metadata: Dict,
    start_date: str,
    result_folder: Optional[str] = None,
    settings: Optional[Dict[str, object]] = None,
) -> Tuple[List[Dict[str, object]], Dict[str, object], List[Dict[str, object]], object]:
    """Run the main CA-CJH-Insertion heuristic and return evaluation-style output."""
    start_t = time.perf_counter()
    settings = settings or {}
    ca_cfg = {}
    runtime_cfg = settings.get("ca_cjh") if isinstance(settings.get("ca_cjh"), dict) else {}
    ca_cfg.update(runtime_cfg or {})
    for key, value in settings.items():
        if str(key).startswith("ca_cjh_"):
            ca_cfg[str(key).replace("ca_cjh_", "", 1)] = value

    feature_mode = str(ca_cfg.get("feature_mode", "process_plus_core") or "process_plus_core")
    trim_ratio = _as_float_config(ca_cfg.get("trim_ratio"), 0.1)
    w_cos = _as_float_config(ca_cfg.get("w_cos"), 0.5)
    w_jac = _as_float_config(ca_cfg.get("w_jac"), 0.5)
    alpha = _as_float_config(ca_cfg.get("alpha"), 0.0)
    objective_mode = str(ca_cfg.get("objective_mode", "strict_lexicographic") or "strict_lexicographic").strip().lower()
    if objective_mode not in {"strict_lexicographic", "cost"}:
        objective_mode = "strict_lexicographic"
    priority_mode = str(ca_cfg.get("priority_mode", "cjh_difficulty") or "cjh_difficulty").strip().lower()
    beta_load = _as_float_config(ca_cfg.get("beta_load"), 0.7)
    # [AGENT-ADD] Difficulty-first CA-CJH global priority weights. The
    # insertion objective below remains strict and unchanged.
    w_load = _as_float_config(ca_cfg.get("w_load"), 0.0)
    w_abnormal = _as_float_config(ca_cfg.get("w_abnormal"), 0.7)
    w_shape_dev = _as_float_config(ca_cfg.get("w_shape_dev"), 0.3)
    w_urgency = _as_float_config(ca_cfg.get("w_urgency"), 0.0)
    w_risk = _as_float_config(ca_cfg.get("w_risk"), 0.0)
    load_amplifier = _as_float_config(ca_cfg.get("load_amplifier"), 0.0)
    completion_policy = str(ca_cfg.get("completion_policy", "cjh_priority") or "cjh_priority")
    trace_enabled = _as_bool(ca_cfg.get("trace_enabled"), True)
    include_longi_balance = _as_bool(ca_cfg.get("include_longi_balance"), True)
    cjh_priority_modes = {"difficulty_cjh", "cjh_difficulty"}
    feasible_priority = _as_bool(ca_cfg.get("feasible_priority"), priority_mode in cjh_priority_modes)
    respect_workshop_order = _as_bool(ca_cfg.get("respect_workshop_order"), priority_mode in cjh_priority_modes)
    # [AGENT-ADD] CA-CJH must not bypass action masking by default. Insertion
    # positions are still explored freely, but DES replay repairs impossible
    # forced prefixes instead of forcing capacity/C-seam violations.
    allow_forced_prefix_override = _as_bool(ca_cfg.get("allow_forced_prefix_override"), False)
    parallel_enabled = _as_bool(ca_cfg.get("parallel"), True)
    workers_value = ca_cfg.get("workers", ca_cfg.get("parallel_workers", 0))
    max_workers = os.cpu_count() if workers_value in (None, "", "null", "none", "None", 0, "0") else int(workers_value)
    max_workers = max(1, int(max_workers or 1))
    chunksize_value = ca_cfg.get("chunksize", "auto")
    max_blocks_for_full_insertion = int(ca_cfg.get("max_blocks_for_full_insertion", 10 ** 9) or 10 ** 9)
    beam_width_value = ca_cfg.get("beam_width", None)
    beam_width = None if beam_width_value in (None, "", "null", "none", "None") else int(beam_width_value)
    auto_beam_width = int(ca_cfg.get("auto_beam_width", 12) or 12)
    enable_beam_search = _as_bool(ca_cfg.get("enable_beam_search"), False)
    use_all_positions = _as_bool(ca_cfg.get("use_all_insertion_positions"), True)

    block_ids = [_block_id(block) for block in blocks]
    blocks_dict = {block_id: block for block_id, block in zip(block_ids, blocks)}
    max_days = int(settings.get("assembly_max_days", ca_cfg.get("assembly_max_days", 20)) or 20)
    date_offset = int(settings.get("assembly_date_offset", ca_cfg.get("assembly_date_offset", 0)) or 0)
    save_detailed = _as_bool(settings.get("save_detailed_csv", ca_cfg.get("save_detailed_csv")), False)

    trace_output_dir = str(ca_cfg.get("trace_output_dir") or "")
    if not trace_output_dir:
        trace_output_dir = os.path.join(result_folder or "results", "ca_cjh_traces")
    elif result_folder and not os.path.isabs(trace_output_dir):
        trace_output_dir = os.path.join(result_folder, trace_output_dir)
    os.makedirs(trace_output_dir, exist_ok=True)

    score_df, score_context = compute_cjh_scores(
        block_ids=block_ids,
        blocks_dict=blocks_dict,
        context={"start_date": start_date},
        feature_mode=feature_mode,
        trim_ratio=trim_ratio,
        w_cos=w_cos,
        w_jac=w_jac,
    )
    # [AGENT-ADD] Priority mode separates global ordering from insertion objective.
    for column in ("load_score", "abnormality_score", "shape_deviation", "urgency_score", "risk_score"):
        if column not in score_df.columns:
            score_df[column] = 0.0
    # [AGENT-ADD] Legacy additive difficulty is kept only for ablation. The
    # default cjh_difficulty mode is CJH-native: global order comes from
    # Cosine/Jaccard difficulty, while load is an optional amplifier and defaults
    # to zero.
    score_df["legacy_difficulty_score"] = (
        w_load * score_df["load_score"].astype(float)
        + w_abnormal * score_df["abnormality_score"].astype(float)
        + w_shape_dev * score_df["shape_deviation"].astype(float)
        + w_urgency * score_df["urgency_score"].astype(float)
        + w_risk * score_df["risk_score"].astype(float)
    )
    cjh_weight_sum = abs(w_abnormal) + abs(w_shape_dev)
    if cjh_weight_sum <= EPSILON:
        cjh_abnormal_weight = 0.7
        cjh_shape_weight = 0.3
    else:
        cjh_abnormal_weight = abs(w_abnormal) / cjh_weight_sum
        cjh_shape_weight = abs(w_shape_dev) / cjh_weight_sum
    score_df["cjh_difficulty_score"] = (
        cjh_abnormal_weight * score_df["abnormality_score"].astype(float)
        + cjh_shape_weight * score_df["shape_deviation"].astype(float)
    )
    score_df["difficulty_score"] = score_df["legacy_difficulty_score"]
    if priority_mode == "lpt":
        score_df["priority_score"] = score_df["normalized_total_processing_time"]
    elif priority_mode == "lpt_cjh":
        beta = max(0.0, min(1.0, beta_load))
        score_df["priority_score"] = (
            beta * score_df["normalized_total_processing_time"]
            + (1.0 - beta) * score_df["cjh_global_score"]
        )
    elif priority_mode == "cjh_difficulty":
        score_df["difficulty_score"] = (
            score_df["cjh_difficulty_score"].astype(float)
            * (1.0 + max(0.0, load_amplifier) * score_df["load_score"].astype(float))
            + w_urgency * score_df["urgency_score"].astype(float)
            + w_risk * score_df["risk_score"].astype(float)
        )
        score_df["priority_score"] = score_df["difficulty_score"]
    elif priority_mode == "difficulty_cjh":
        score_df["priority_score"] = score_df["difficulty_score"]
    else:
        priority_mode = "cjh"
        score_df["priority_score"] = score_df["cjh_global_score"]
    score_df["priority_mode"] = priority_mode
    score_df["beta_load"] = beta_load
    score_df["w_load"] = w_load
    score_df["w_abnormal"] = w_abnormal
    score_df["w_shape_dev"] = w_shape_dev
    score_df["w_urgency"] = w_urgency
    score_df["w_risk"] = w_risk
    score_df["load_amplifier"] = load_amplifier
    if priority_mode == "cjh_difficulty":
        score_df = score_df.sort_values(
            [
                "priority_score",
                "cjh_difficulty_score",
                "abnormality_score",
                "shape_deviation",
                "urgency_score",
                "risk_score",
                "block_id",
            ],
            ascending=[False, False, False, False, False, False, True],
        ).reset_index(drop=True)
    elif priority_mode == "difficulty_cjh":
        score_df = score_df.sort_values(
            [
                "priority_score",
                "cjh_difficulty_score",
                "abnormality_score",
                "shape_deviation",
                "risk_score",
                "load_score",
                "urgency_score",
                "block_id",
            ],
            ascending=[False, False, False, False, False, False, False, True],
        ).reset_index(drop=True)
    else:
        score_df = score_df.sort_values(
            ["priority_score", "cjh_global_score", "normalized_total_processing_time", "block_id"],
            ascending=[False, False, False, True],
        ).reset_index(drop=True)
    score_df["priority_rank"] = np.arange(1, len(score_df) + 1)
    priority_ids = [int(v) for v in score_df["block_id"].tolist()]
    if trace_enabled:
        # [AGENT-ADD] Keep the requested audit columns first for paper tables.
        required_priority_cols = [
            "block_id",
            "load_score",
            "cosine_score",
            "jaccard_normal_score",
            "shape_deviation",
            "abnormality_score",
            "urgency_score",
            "risk_score",
            "cjh_difficulty_score",
            "legacy_difficulty_score",
            "difficulty_score",
            "priority_rank",
        ]
        ordered_priority_cols = [col for col in required_priority_cols if col in score_df.columns]
        ordered_priority_cols.extend([col for col in score_df.columns if col not in ordered_priority_cols])
        score_df[ordered_priority_cols].to_csv(os.path.join(trace_output_dir, "global_priority.csv"), index=False, encoding="utf-8-sig")

    effective_beam_width = None if use_all_positions else (beam_width if enable_beam_search else None)
    beam_reason = ""
    if not use_all_positions and enable_beam_search and effective_beam_width is None and len(block_ids) > max_blocks_for_full_insertion:
        effective_beam_width = max(3, auto_beam_width)
        beam_reason = f"auto_beam_large_n>{max_blocks_for_full_insertion}"

    output_csv = os.path.join(result_folder, "ca_cjh_evaluation_results.csv") if result_folder else "ca_cjh_evaluation_results.csv"
    eval_base_cfg = {
        "assembly_max_days": max_days,
        "assembly_date_offset": date_offset,
        "start_date": start_date,
        "completion_policy": completion_policy,
        "output_csv": output_csv,
        "allow_forced_prefix_override": allow_forced_prefix_override,
        "quiet": not _as_bool(ca_cfg.get("verbose"), False),
    }

    sequence: List[int] = []
    trace_rows: List[Dict[str, object]] = []
    selection_rows: List[Dict[str, object]] = []
    total_evaluations = 0
    actual_parallel = bool(parallel_enabled and max_workers > 1)
    _set_single_thread_env()
    _init_ca_cjh_worker(
        blocks=blocks,
        metadata=metadata,
        eval_base_cfg=eval_base_cfg,
        score_context=score_context,
        w_cos=w_cos,
        w_jac=w_jac,
        alpha=alpha,
        objective_mode=objective_mode,
        include_longi_balance=include_longi_balance,
        completion_policy=completion_policy,
    )
    print(
        f" CA-CJH-Insertion 실행 중... "
        f"(blocks={len(block_ids)}, feature={feature_mode}, priority={priority_mode}, "
        f"beta={beta_load:.2f}, cjh_w={w_abnormal:.2f}/{w_shape_dev:.2f}, "
        f"load_amp={load_amplifier:.2f}, aux_w={w_urgency:.2f}/{w_risk:.2f}, "
        f"objective={objective_mode}, "
        f"feasible_priority={feasible_priority}, workshop_order={respect_workshop_order}, "
        f"forced_override={allow_forced_prefix_override}, "
        f"beam={effective_beam_width if effective_beam_width else 'full'}, "
        f"parallel={actual_parallel}, workers={max_workers})"
    )

    executor: Optional[ProcessPoolExecutor] = None
    if actual_parallel:
        executor = ProcessPoolExecutor(
            max_workers=max_workers,
            initializer=_init_ca_cjh_worker,
            initargs=(
                blocks,
                metadata,
                eval_base_cfg,
                score_context,
                w_cos,
                w_jac,
                alpha,
                objective_mode,
                include_longi_balance,
                completion_policy,
            ),
        )

    try:
        for step in range(1, len(priority_ids) + 1):
            step_t = time.perf_counter()
            selected_set = set(sequence)
            remaining_static = [bid for bid in priority_ids if bid not in selected_set]
            if not remaining_static:
                break
            if feasible_priority and respect_workshop_order:
                dynamic_priority = _build_workshop_feasible_priority_order(
                    priority_ids,
                    selected_ids=selected_set,
                    blocks_dict=blocks_dict,
                )
                candidate_block_id = int(dynamic_priority[0])
                eligible_count = sum(
                    1
                    for bid in remaining_static
                    if not _has_unselected_workshop_predecessor(bid, remaining_static, blocks_dict)
                )
                candidate_reason = "difficulty_feasible_workshop_order"
            else:
                dynamic_priority = remaining_static
                candidate_block_id = int(remaining_static[0])
                eligible_count = len(remaining_static)
                candidate_reason = "static_priority"
            positions = _candidate_positions(sequence, candidate_block_id, score_context, effective_beam_width, w_cos, w_jac)
            if feasible_priority and respect_workshop_order:
                positions = _filter_positions_by_workshop_order(sequence, candidate_block_id, positions, blocks_dict)
                remaining_priority = _build_workshop_feasible_priority_order(
                    priority_ids,
                    selected_ids={*selected_set, int(candidate_block_id)},
                    blocks_dict=blocks_dict,
                )
            else:
                remaining_priority = [bid for bid in priority_ids if bid not in sequence and bid != candidate_block_id]
            candidate_score_row = score_df[score_df["block_id"] == int(candidate_block_id)]
            candidate_score = float(candidate_score_row["priority_score"].iloc[0]) if not candidate_score_row.empty else 0.0
            candidate_difficulty = float(candidate_score_row["difficulty_score"].iloc[0]) if not candidate_score_row.empty and "difficulty_score" in candidate_score_row else 0.0
            candidate_cjh_difficulty = float(candidate_score_row["cjh_difficulty_score"].iloc[0]) if not candidate_score_row.empty and "cjh_difficulty_score" in candidate_score_row else 0.0
            selection_rows.append({
                "step": step,
                "candidate_block_id": int(candidate_block_id),
                "candidate_reason": candidate_reason,
                "eligible_count": int(eligible_count),
                "remaining_count": int(len(remaining_static)),
                "priority_score": candidate_score,
                "difficulty_score": candidate_difficulty,
                "cjh_difficulty_score": candidate_cjh_difficulty,
                "positions_evaluated": int(len(positions)),
            })
            tasks = [
                {
                    "step": step,
                    "candidate_block_id": int(candidate_block_id),
                    "insertion_position": int(position),
                    "sequence": list(sequence),
                    "remaining_priority": list(remaining_priority),
                }
                for position in positions
            ]

            if executor is not None:
                if str(chunksize_value).strip().lower() == "auto":
                    chunksize = max(1, len(tasks) // max(1, max_workers * 4))
                else:
                    chunksize = max(1, int(chunksize_value or 1))
                try:
                    step_results = list(executor.map(_evaluate_ca_cjh_position_worker, tasks, chunksize=chunksize))
                except Exception as exc:
                    print(f"   [CA-CJH][WARN] parallel step failed, marking all positions failed: {exc}")
                    step_results = [_evaluate_ca_cjh_position_worker(task) for task in tasks]
            else:
                step_results = [_evaluate_ca_cjh_position_worker(task) for task in tasks]

            total_evaluations += len(step_results)
            best_key: Optional[Tuple[float, ...]] = None
            best_position: Optional[int] = None
            best_sequence: Optional[List[int]] = None
            step_rows: List[Dict[str, object]] = []
            failed_positions = 0

            for result in sorted(step_results, key=lambda item: int(item.get("position", 10 ** 9))):
                key = tuple(float(v) for v in result.get("objective_key", (10 ** 9, 10 ** 9, float("inf"), float("inf"), 0.0, 10 ** 9)))
                position = int(result.get("position", len(sequence)))
                row = dict(result.get("row") or {})
                step_rows.append(row)
                if result.get("worker_error"):
                    failed_positions += 1
                if best_key is None or key < best_key:
                    best_key = key
                    best_position = position
                    best_sequence = [int(v) for v in result.get("tentative_sequence", [])]

            if best_sequence is None or best_position is None:
                best_sequence = [*sequence, int(candidate_block_id)]
                best_position = len(sequence)
                best_key = (10 ** 9, 10 ** 9, float("inf"), float("inf"), 0.0, float(best_position))
            sequence = best_sequence
            for row in step_rows:
                if int(row["insertion_position"]) == int(best_position):
                    row["selected_position"] = int(best_position)
                    row["selected"] = True
                    row["reason"] = "selected"
            trace_rows.extend(step_rows)

            step_elapsed = time.perf_counter() - step_t
            print(
                f"   [CA-CJH] step={step}/{len(priority_ids)}, block={candidate_block_id}, "
                f"positions={len(positions)}, workers={max_workers if actual_parallel else 1}, "
                f"eligible={eligible_count}/{len(remaining_static)}, "
                f"best_key={best_key}, elapsed={step_elapsed:.2f}s, "
                f"failed={failed_positions}, evals={total_evaluations}"
            )
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)

    final_eval_cfg = dict(eval_base_cfg)
    final_eval_cfg["completion_sequence"] = []
    final_eval = evaluate_panel_sequence_for_cjh(
        sequence,
        blocks,
        metadata,
        final_eval_cfg,
        partial=False,
        save_csv=True,
        save_detailed=save_detailed,
    )
    final_results = final_eval.get("schedule_results") or []
    final_statistics = dict(final_eval.get("statistics") or {})
    elapsed = time.perf_counter() - start_t
    final_statistics.update({
        "selected_result_csv_name": "ca_cjh_evaluation_results.csv",
        "ca_cjh_feature_mode": feature_mode,
        "ca_cjh_trim_ratio": trim_ratio,
        "ca_cjh_w_cos": w_cos,
        "ca_cjh_w_jac": w_jac,
        "ca_cjh_alpha": alpha,
        "ca_cjh_objective_mode": objective_mode,
        "ca_cjh_priority_mode": priority_mode,
        "ca_cjh_beta_load": beta_load,
        "ca_cjh_feasible_priority": bool(feasible_priority),
        "ca_cjh_respect_workshop_order": bool(respect_workshop_order),
        "ca_cjh_allow_forced_prefix_override": bool(allow_forced_prefix_override),
        "ca_cjh_w_load": w_load,
        "ca_cjh_w_abnormal": w_abnormal,
        "ca_cjh_w_shape_dev": w_shape_dev,
        "ca_cjh_w_urgency": w_urgency,
        "ca_cjh_w_risk": w_risk,
        "ca_cjh_load_amplifier": load_amplifier,
        "ca_cjh_completion_policy": completion_policy,
        "ca_cjh_evaluations": int(total_evaluations),
        "ca_cjh_beam_width": effective_beam_width if effective_beam_width is not None else "",
        "ca_cjh_beam_reason": beam_reason,
        "ca_cjh_parallel": bool(actual_parallel),
        "ca_cjh_workers": int(max_workers if actual_parallel else 1),
        "ca_cjh_chunksize": chunksize_value,
        "ca_cjh_use_all_insertion_positions": bool(use_all_positions),
        "ca_cjh_sequence_head": sequence[:12],
        "ca_cjh_hard_violation_count": int(final_eval.get("hard_violation_count", 0) or 0),
        "ca_cjh_soft_violation_count": int(final_eval.get("soft_violation_count", 0) or 0),
        "ca_cjh_total_violation_count": int(final_eval.get("total_violation_count", 0) or 0),
        "ca_cjh_longi_abs_diff": float(final_eval.get("longi_abs_diff", 0.0) or 0.0),
        "computation_seconds": elapsed,
    })

    if trace_enabled:
        save_cjh_insertion_trace(trace_rows, trace_output_dir)
        pd.DataFrame(selection_rows).to_csv(os.path.join(trace_output_dir, "selected_priority_order.csv"), index=False, encoding="utf-8-sig")
        pd.DataFrame([{
            "method": "CA_CJH",
            "number_of_blocks": len(block_ids),
            "total_evaluations": int(total_evaluations),
            "workers": int(max_workers if actual_parallel else 1),
            "parallel": bool(actual_parallel),
            "total_runtime_sec": elapsed,
            "makespan_hours": final_eval.get("makespan_hours", final_statistics.get("makespan_hours", 0)),
            "hard_violation_count": final_eval.get("hard_violation_count", 0),
            "soft_violation_count": final_eval.get("soft_violation_count", 0),
            "total_violation_count": final_eval.get("total_violation_count", 0),
            "longi_35a": final_eval.get("longi_35a", 0),
            "longi_36b": final_eval.get("longi_36b", 0),
            "longi_abs_diff": final_eval.get("longi_abs_diff", 0),
            "runtime_sec": elapsed,
            "feature_mode": feature_mode,
            "trim_ratio": trim_ratio,
            "w_cos": w_cos,
            "w_jac": w_jac,
            "alpha": alpha,
            "objective_mode": objective_mode,
            "priority_mode": priority_mode,
            "beta_load": beta_load,
            "feasible_priority": bool(feasible_priority),
            "respect_workshop_order": bool(respect_workshop_order),
            "allow_forced_prefix_override": bool(allow_forced_prefix_override),
            "w_load": w_load,
            "w_abnormal": w_abnormal,
            "w_shape_dev": w_shape_dev,
            "w_urgency": w_urgency,
            "w_risk": w_risk,
            "load_amplifier": load_amplifier,
            "evaluations": total_evaluations,
            "beam_width": effective_beam_width if effective_beam_width is not None else "",
        }]).to_csv(os.path.join(trace_output_dir, "final_result.csv"), index=False, encoding="utf-8-sig")

    print(
        f"   CA-CJH 완료: Makespan = {float(final_statistics.get('makespan_hours', 0) or 0):.2f}시간, "
        f"Primary = {final_statistics.get('total_violations_primary', final_statistics.get('total_violations', 0))}, "
        f"Total = {final_eval.get('total_violation_count', final_statistics.get('total_violations_raw', 0))}, "
        f"후보평가 = {total_evaluations}개, workers={max_workers if actual_parallel else 1}, 계산시간 = {elapsed:.2f}s"
    )
    return final_results, final_statistics, trace_rows, None
