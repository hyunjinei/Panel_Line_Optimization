# -*- coding: utf-8 -*-
"""평가 유틸 모음 (runner.py에서 분리)."""
# [AGENT-ADD] runner.py에서 분리한 공통 유틸

from __future__ import annotations

import math
import random
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import torch

from runtime_config import get_runtime_config


# [AGENT-ADD] MODE 1 생성 데이터의 심수/처리시간 분포를 조정하는 유틸
TACT_TIME_COLUMNS = [
    '판계 Tact Time',
    '전면SAW Tact Time',
    'TurnOver Tact Time',
    '후면SAW Tact Time',
    'NC Tact Time',
    '론지취부 Tact Time',
    '론지용접 Tact Time',
    '수정 Tact Time'
]


def _normalize_method_name(name: str) -> str:
    if not name:
        return ""
    key = str(name).strip().lower()
    key = key.replace("-", "_").replace(" ", "")
    mapping = {
        "spt": "SPT",
        "spt휴리스틱": "SPT",
        "lpt": "LPT",
        "lpt휴리스틱": "LPT",
        "seam_min": "SEAM_MIN",
        "seammin": "SEAM_MIN",
        "seam": "SEAM_MIN",
        "seammin휴리스틱": "SEAM_MIN",
        "ga": "GA",
        "geneticalgorithm": "GA",
        "genetic": "GA",
        "유전알고리즘": "GA",
        "유전": "GA",
        "rl": "RL",
        "강화학습": "RL",
        "excel": "EXCEL",
        "엑셀": "EXCEL",
        "엑셀순번": "EXCEL",
        "엑셀순번기반": "EXCEL",
        "실적": "EXCEL",
        "실적데이터": "EXCEL",
        "actionmasking": "ACTIONMASKING",
        "action_masking": "ACTIONMASKING",
        "actionmask": "ACTIONMASKING",
        "착수일": "ACTIONMASKING",
        "착수일기준": "ACTIONMASKING",
        "startdate": "ACTIONMASKING",
        "start_date": "ACTIONMASKING",
    }
    return mapping.get(key, str(name).strip().upper())


def _get_selected_methods() -> Optional[set]:
    config = get_runtime_config() or {}
    if not isinstance(config, dict):
        return None
    evaluation_cfg = (config.get("evaluation") or {}) if isinstance(config, dict) else {}
    eval_fallback = (config.get("eval") or {}) if isinstance(config, dict) else {}
    if isinstance(evaluation_cfg, dict) and isinstance(eval_fallback, dict):
        evaluation_cfg = {**eval_fallback, **evaluation_cfg}
    methods = evaluation_cfg.get("methods") or evaluation_cfg.get("only_methods")
    if not methods:
        return None
    if isinstance(methods, str):
        methods = [m.strip() for m in methods.split(",") if m.strip()]
    return {_normalize_method_name(m) for m in methods}


def _should_run(method_key: str, selected: Optional[set], *, default: bool = True) -> bool:
    if selected is None:
        return default
    return method_key in selected


def set_random_seeds(seed: int = 42):
    """모든 라이브러리의 랜덤 시드 설정"""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)
    #  PyTorch 재현성 강화
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
    print(f" Random Seed 설정 완료: {seed}")


def sanitize_label_for_filename(label: Optional[str]) -> str:
    """파일명에 사용할 수 있도록 계획 시트 라벨 정리"""
    if not label:
        return ""
    unsafe_chars = '/\\:*?"<>|'
    safe = label
    for ch in unsafe_chars:
        safe = safe.replace(ch, "_")
    # 공백 제거
    safe = safe.replace(" ", "")
    return safe


def apply_generated_data_variant(
    df: pd.DataFrame,
    seam_scale: float = 1.0,
    tact_time_scale: float = 1.0,
    length_scale: float = 1.0,
    width_scale: float = 1.0,
    thickness_scale: float = 1.0
) -> pd.DataFrame:
    """생성 데이터의 심수/용접장/처리시간/물리 스케일을 조정"""
    if df is None:
        return df

    adjusted = df.copy()

    if seam_scale != 1.0:
        if '판넬 SEAM 수' in adjusted.columns:
            # 심수는 정수 유지 + 최소 1 보장
            def _scale_seam(value: object) -> object:
                if pd.isna(value):
                    return value
                try:
                    return max(1, int(round(float(value) * seam_scale)))
                except (TypeError, ValueError):
                    return value
            adjusted['판넬 SEAM 수'] = adjusted['판넬 SEAM 수'].apply(_scale_seam)
        if '판넬 SEAM 용접장' in adjusted.columns:
            adjusted['판넬 SEAM 용접장'] = adjusted['판넬 SEAM 용접장'].astype(float) * seam_scale

    if tact_time_scale != 1.0:
        for col in TACT_TIME_COLUMNS:
            if col in adjusted.columns:
                adjusted[col] = adjusted[col].astype(float) * tact_time_scale
        if '총 Tact Time' in adjusted.columns:
            adjusted['총 Tact Time'] = adjusted['총 Tact Time'].astype(float) * tact_time_scale

    # [AGENT-ADD] Physical scaling for length/width/thickness (keeps area/volume consistent).
    if length_scale != 1.0 and '길이' in adjusted.columns:
        adjusted['길이'] = adjusted['길이'].astype(float) * length_scale
    if width_scale != 1.0 and '폭' in adjusted.columns:
        adjusted['폭'] = adjusted['폭'].astype(float) * width_scale
    if thickness_scale != 1.0:
        if '최소두께' in adjusted.columns:
            adjusted['최소두께'] = adjusted['최소두께'].astype(float) * thickness_scale
        if '최대두께' in adjusted.columns:
            adjusted['최대두께'] = adjusted['최대두께'].astype(float) * thickness_scale
            if '최소두께' in adjusted.columns:
                adjusted['최대두께'] = adjusted[['최대두께', '최소두께']].max(axis=1)

    # [AGENT-ADD] Recompute area/volume if base features exist.
    if '길이' in adjusted.columns and '폭' in adjusted.columns:
        adjusted['면적'] = adjusted['길이'].astype(float) * adjusted['폭'].astype(float)
        if '최소두께' in adjusted.columns:
            adjusted['부피'] = adjusted['면적'].astype(float) * adjusted['최소두께'].astype(float) / 1000.0

    return adjusted


def _sample_util_bucket(buckets: List[Dict[str, object]]) -> Tuple[str, float, Tuple[float, float]]:
    roll = random.random()
    cumulative = 0.0
    last_bucket = buckets[-1]
    for bucket in buckets:
        cumulative += float(bucket.get("ratio", 0.0))
        if roll <= cumulative:
            util_range = tuple(bucket.get("util_range", (1.0, 1.0)))
            util_value = random.uniform(util_range[0], util_range[1])
            return str(bucket.get("name", "unknown")), util_value, util_range
    util_range = tuple(last_bucket.get("util_range", (1.0, 1.0)))
    util_value = random.uniform(util_range[0], util_range[1])
    return str(last_bucket.get("name", "unknown")), util_value, util_range


def _compute_spread_days(total_blocks: int, util: float, max_daily_blocks: int, min_days: int) -> int:
    if max_daily_blocks <= 0:
        return max(min_days, 1)
    raw_days = math.ceil(total_blocks / (max_daily_blocks * max(util, 0.01)))
    return max(min_days, raw_days)


def _adjust_reserved_counts(total_blocks: int, ps_pairs: int, sub_groups: int, min_basic_blocks: int = 1) -> Tuple[int, int]:
    """Ensure at least a minimal base block pool for pair/subassembly duplication."""
    basic_blocks = total_blocks - ps_pairs - sub_groups
    if basic_blocks >= min_basic_blocks:
        return ps_pairs, sub_groups
    shortage = min_basic_blocks - basic_blocks
    reserved = max(ps_pairs + sub_groups, 1)
    reduce_ps = int(round(shortage * (ps_pairs / reserved)))
    reduce_sub = shortage - reduce_ps
    ps_pairs = max(0, ps_pairs - reduce_ps)
    sub_groups = max(0, sub_groups - reduce_sub)
    return ps_pairs, sub_groups


__all__ = [
    "TACT_TIME_COLUMNS",
    "_normalize_method_name",
    "_get_selected_methods",
    "_should_run",
    "set_random_seeds",
    "sanitize_label_for_filename",
    "apply_generated_data_variant",
    "_sample_util_bucket",
    "_compute_spread_days",
    "_adjust_reserved_counts",
]
