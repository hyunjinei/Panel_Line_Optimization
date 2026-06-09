# [AGENT-ADD] Feature extraction and Cosine-Jaccard scoring for CA-CJH insertion.
"""Production-feature scoring utilities for CA-CJH-Insertion."""

from __future__ import annotations

from datetime import datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd

from enhanced_environment.models import AssemblyType, MaterialType, PortStarboard, WorkshopType

EPSILON = 1e-9


def _as_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except Exception:
        return default


def _as_bool_float(value: object) -> float:
    return 1.0 if bool(value) else 0.0


def _as_datetime(value: object) -> Optional[datetime]:
    """[AGENT-ADD] Parse optional date-like values for CA-CJH urgency scoring."""
    if isinstance(value, datetime):
        return value
    if value is None:
        return None
    if hasattr(value, "to_pydatetime"):
        try:
            return value.to_pydatetime()
        except Exception:
            pass
    text = str(value).strip()
    if not text or text.lower() in {"nan", "nat", "none", "null"}:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    try:
        return datetime.fromisoformat(text)
    except Exception:
        return None


def _enum_value(value: object) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value))


def _days_between(left: object, right: object) -> float:
    if not isinstance(left, datetime) or not isinstance(right, datetime):
        return 0.0
    return float((left - right).days)


def _append_one_hot(values: List[float], names: List[str], prefix: str, value: object, choices: Sequence[object]) -> None:
    text = _enum_value(value)
    for choice in choices:
        choice_text = _enum_value(choice)
        values.append(1.0 if text == choice_text else 0.0)
        names.append(f"{prefix}_{choice_text}")


def extract_block_feature_vector(
    block: object,
    context: Optional[Dict[str, object]] = None,
    feature_mode: str = "process_only",
) -> Tuple[List[float], List[str]]:
    """Return one CA-CJH production feature vector and feature names.

    `process_only` is the default because the original CJH identity is based on
    process-time vectors. Other modes are provided for ablation.
    """
    context = context or {}
    feature_mode = str(feature_mode or "process_only").strip().lower()

    values: List[float] = []
    names: List[str] = []

    processing_times = list(getattr(block, "processing_times", []) or [])
    for idx in range(8):
        values.append(_as_float(processing_times[idx] if idx < len(processing_times) else 0.0))
        names.append(f"process_{idx + 1}_time")

    if feature_mode == "process_only":
        return values, names

    values.extend([
        float(sum(_as_float(v) for v in processing_times)),
        _as_float(getattr(block, "seam_count", 0)),
        _as_float(getattr(block, "c_seam_count", 0)),
        _as_float(getattr(block, "longi_count", 0)),
        _as_float(getattr(block, "width", 0.0)),
    ])
    names.extend([
        "total_processing_time",
        "seam_count",
        "c_seam_count",
        "longi_count",
        "width",
    ])

    if feature_mode == "process_plus_core":
        return values, names

    start_time = context.get("start_time")
    values.extend([
        _as_float(getattr(block, "length", 0.0)),
        _as_float(getattr(block, "min_thickness", 0.0)),
        _as_float(getattr(block, "max_thickness", 0.0)),
        _as_float(getattr(block, "main_plate_count", 0)),
        _as_float(getattr(block, "curved_plate_count", 0)),
        _as_bool_float(getattr(block, "has_curved_plate", False)),
        _as_bool_float(getattr(block, "is_high_seam_block", False)),
        _as_bool_float(getattr(block, "material_ready", False)),
        _days_between(getattr(block, "assembly_start_date", None), start_time),
        _days_between(getattr(block, "max_start_date", None), start_time),
    ])
    names.extend([
        "length",
        "min_thickness",
        "max_thickness",
        "main_plate_count",
        "curved_plate_count",
        "has_curved_plate",
        "is_high_seam_block",
        "material_ready",
        "assembly_start_slack_days",
        "max_start_slack_days",
    ])

    _append_one_hot(values, names, "assembly_type", getattr(block, "assembly_type", None), list(AssemblyType))
    _append_one_hot(values, names, "port_starboard", getattr(block, "port_starboard", None), list(PortStarboard))
    _append_one_hot(values, names, "workshop_type", getattr(block, "workshop_type", None), list(WorkshopType))
    _append_one_hot(values, names, "material_type", getattr(block, "material_type", None), list(MaterialType))
    return values, names


def build_feature_matrix(
    block_ids: Iterable[int],
    blocks_dict: Dict[int, object],
    context: Optional[Dict[str, object]] = None,
    feature_mode: str = "process_only",
) -> Tuple[np.ndarray, List[str]]:
    """Build the feature matrix for block ids in the given order."""
    rows: List[List[float]] = []
    feature_names: List[str] = []
    for block_id in block_ids:
        values, names = extract_block_feature_vector(blocks_dict[int(block_id)], context=context, feature_mode=feature_mode)
        rows.append(values)
        if not feature_names:
            feature_names = names
    if not rows:
        return np.zeros((0, 0), dtype=float), []
    return np.asarray(rows, dtype=float), feature_names


def normalize_feature_matrix(X: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Min-max normalize a feature matrix."""
    if X.size == 0:
        return X.astype(float), np.asarray([], dtype=float), np.asarray([], dtype=float)
    col_min = np.nanmin(X, axis=0)
    col_max = np.nanmax(X, axis=0)
    X_safe = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)
    X_norm = (X_safe - col_min) / (col_max - col_min + EPSILON)
    return X_norm, col_min, col_max


def _minmax_array(values: Sequence[float]) -> np.ndarray:
    """[AGENT-ADD] Min-max normalize a one-dimensional score safely."""
    arr = np.asarray(values, dtype=float)
    if arr.size == 0:
        return arr
    arr = np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0)
    low = float(np.min(arr))
    high = float(np.max(arr))
    if abs(high - low) <= EPSILON:
        return np.zeros_like(arr, dtype=float)
    return (arr - low) / (high - low)


def _is_pair_side_block(block: object) -> float:
    """[AGENT-ADD] Static pair-side risk flag used only for difficulty priority."""
    text = _enum_value(getattr(block, "port_starboard", None)).strip().upper()
    if text in {"P", "S", "PORT", "STARBOARD"}:
        return 1.0
    return 1.0 if getattr(block, "pair_block_id", None) is not None else 0.0


def _compute_urgency_scores(ordered_ids: Sequence[int], blocks_dict: Dict[int, object], context: Dict[str, object]) -> np.ndarray:
    """[AGENT-ADD] Smaller schedule slack receives a larger urgency score."""
    start_time = _as_datetime(context.get("start_time") or context.get("start_date"))
    block_dates: List[Optional[datetime]] = []
    for block_id in ordered_ids:
        block = blocks_dict[int(block_id)]
        date_value = _as_datetime(getattr(block, "max_start_date", None))
        if date_value is None:
            date_value = _as_datetime(getattr(block, "assembly_start_date", None))
        block_dates.append(date_value)
    valid_dates = [date for date in block_dates if date is not None]
    if not valid_dates:
        return np.zeros(len(ordered_ids), dtype=float)
    if start_time is None:
        start_time = min(valid_dates)
    slack_days = []
    for date_value in block_dates:
        if date_value is None:
            slack_days.append(max((date - start_time).days for date in valid_dates))
        else:
            slack_days.append((date_value - start_time).days)
    normalized_slack = _minmax_array(slack_days)
    return 1.0 - normalized_slack


def _compute_static_risk_scores(ordered_ids: Sequence[int], blocks_dict: Dict[int, object]) -> np.ndarray:
    """[AGENT-ADD] Simple static constraint-risk proxy for difficulty-first CA-CJH."""
    seam = []
    c_seam = []
    longi = []
    width = []
    high_seam = []
    curved = []
    pair_side = []
    for block_id in ordered_ids:
        block = blocks_dict[int(block_id)]
        seam.append(_as_float(getattr(block, "seam_count", 0)))
        c_seam.append(_as_float(getattr(block, "c_seam_count", 0)))
        longi.append(_as_float(getattr(block, "longi_count", 0)))
        width.append(_as_float(getattr(block, "width", 0.0)))
        high_seam.append(_as_bool_float(getattr(block, "is_high_seam_block", False)))
        curved.append(max(_as_bool_float(getattr(block, "has_curved_plate", False)), 1.0 if _as_float(getattr(block, "curved_plate_count", 0)) > 0 else 0.0))
        pair_side.append(_is_pair_side_block(block))

    risk_raw = (
        0.20 * _minmax_array(seam)
        + 0.20 * _minmax_array(c_seam)
        + 0.20 * _minmax_array(longi)
        + 0.15 * _minmax_array(width)
        + 0.10 * np.asarray(high_seam, dtype=float)
        + 0.10 * np.asarray(curved, dtype=float)
        + 0.05 * np.asarray(pair_side, dtype=float)
    )
    return _minmax_array(risk_raw)


def _trimmed_matrix(X_norm: np.ndarray, trim_ratio: float) -> np.ndarray:
    if X_norm.size == 0:
        return X_norm
    trim_ratio = max(0.0, min(float(trim_ratio or 0.0), 0.45))
    n_rows = X_norm.shape[0]
    trim = int(n_rows * trim_ratio)
    if trim <= 0 or n_rows - 2 * trim <= 0:
        return X_norm
    sorted_x = np.sort(X_norm, axis=0)
    return sorted_x[trim:n_rows - trim, :]


def compute_trimmed_baseline(X_norm: np.ndarray, trim_ratio: float = 0.1) -> np.ndarray:
    """Compute a robust trimmed-mean baseline vector."""
    if X_norm.size == 0:
        return np.asarray([], dtype=float)
    return np.mean(_trimmed_matrix(X_norm, trim_ratio), axis=0)


def compute_trimmed_std(X_norm: np.ndarray, trim_ratio: float = 0.1) -> np.ndarray:
    """Compute trimmed standard deviation for normal-load ranges."""
    if X_norm.size == 0:
        return np.asarray([], dtype=float)
    return np.std(_trimmed_matrix(X_norm, trim_ratio), axis=0)


def compute_normal_coverage_scores(
    X_norm: np.ndarray,
    baseline: np.ndarray,
    trimmed_std: np.ndarray,
) -> np.ndarray:
    """Jaccard-normal score: share of dimensions inside the robust normal range."""
    if X_norm.size == 0:
        return np.asarray([], dtype=float)
    lower = baseline - trimmed_std
    upper = baseline + trimmed_std
    inside = (X_norm >= lower) & (X_norm <= upper)
    return inside.sum(axis=1) / max(1, X_norm.shape[1])


def compute_cosine_scores(X_norm: np.ndarray, baseline: np.ndarray) -> np.ndarray:
    """Cosine alignment with the robust baseline vector."""
    if X_norm.size == 0:
        return np.asarray([], dtype=float)
    denom = np.linalg.norm(X_norm, axis=1) * np.linalg.norm(baseline) + EPSILON
    return np.dot(X_norm, baseline) / denom


def compute_cjh_scores(
    block_ids: Iterable[int],
    blocks_dict: Dict[int, object],
    context: Optional[Dict[str, object]] = None,
    feature_mode: str = "process_only",
    trim_ratio: float = 0.1,
    w_cos: float = 0.5,
    w_jac: float = 0.5,
) -> Tuple[pd.DataFrame, Dict[str, object]]:
    """Compute global CA-CJH priority scores for all blocks."""
    ordered_ids = [int(block_id) for block_id in block_ids]
    X, feature_names = build_feature_matrix(ordered_ids, blocks_dict, context=context, feature_mode=feature_mode)
    X_norm, col_min, col_max = normalize_feature_matrix(X)
    baseline = compute_trimmed_baseline(X_norm, trim_ratio=trim_ratio)
    trimmed_std = compute_trimmed_std(X_norm, trim_ratio=trim_ratio)
    cosine = compute_cosine_scores(X_norm, baseline)
    jaccard = compute_normal_coverage_scores(X_norm, baseline, trimmed_std)
    score = float(w_cos) * cosine + float(w_jac) * jaccard
    total_processing = np.asarray([
        float(sum(getattr(blocks_dict[block_id], "processing_times", []) or []))
        for block_id in ordered_ids
    ], dtype=float)
    if total_processing.size:
        total_min = float(np.min(total_processing))
        total_max = float(np.max(total_processing))
        total_processing_norm = (total_processing - total_min) / (total_max - total_min + EPSILON)
    else:
        total_processing_norm = np.asarray([], dtype=float)
    # [AGENT-ADD] Difficulty-first CA-CJH uses inverse CJH normality plus
    # lightweight static risk and date urgency. Existing CJH scores are kept.
    load_score = total_processing_norm if total_processing_norm.size else np.asarray([], dtype=float)
    shape_deviation = 1.0 - cosine
    abnormality_score = 1.0 - jaccard
    urgency_score = _compute_urgency_scores(ordered_ids, blocks_dict, context or {})
    risk_score = _compute_static_risk_scores(ordered_ids, blocks_dict)

    rows = []
    for idx, block_id in enumerate(ordered_ids):
        block = blocks_dict[block_id]
        rows.append({
            "block_id": block_id,
            "load_score": float(load_score[idx]) if load_score.size else 0.0,
            "cosine_score": float(cosine[idx]),
            "jaccard_normal_score": float(jaccard[idx]),
            "shape_deviation": float(shape_deviation[idx]),
            "abnormality_score": float(abnormality_score[idx]),
            "urgency_score": float(urgency_score[idx]) if urgency_score.size else 0.0,
            "risk_score": float(risk_score[idx]) if risk_score.size else 0.0,
            "cjh_global_score": float(score[idx]),
            "total_processing_time": float(sum(getattr(block, "processing_times", []) or [])),
            "normalized_total_processing_time": float(total_processing_norm[idx]) if total_processing_norm.size else 0.0,
            "feature_mode": feature_mode,
        })
    df = pd.DataFrame(rows).sort_values(
        ["cjh_global_score", "cosine_score", "jaccard_normal_score", "total_processing_time", "block_id"],
        ascending=[False, False, False, False, True],
    ).reset_index(drop=True)
    if not df.empty:
        df["priority_rank"] = np.arange(1, len(df) + 1)

    context_out = {
        "feature_names": feature_names,
        "X": X,
        "X_norm": X_norm,
        "col_min": col_min,
        "col_max": col_max,
        "baseline": baseline,
        "trimmed_std": trimmed_std,
        "block_ids": ordered_ids,
        "block_id_to_row": {block_id: idx for idx, block_id in enumerate(ordered_ids)},
    }
    return df, context_out
