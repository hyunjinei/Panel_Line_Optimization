# -*- coding: utf-8 -*-
"""학습 데이터 생성/스케일링 유틸 (runner.py에서 분리)."""
# [AGENT-ADD] runner.py에서 분리한 데이터 생성 유틸

from __future__ import annotations

import math
import random
from typing import Dict, List, Tuple

import pandas as pd


TRAIN_TACT_TIME_COLUMNS = [
    '판계 Tact Time',
    '전면SAW Tact Time',
    'TurnOver Tact Time',
    '후면SAW Tact Time',
    'NC Tact Time',
    '론지취부 Tact Time',
    '론지용접 Tact Time',
    '수정 Tact Time'
]


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
    basic_blocks = total_blocks - ps_pairs - sub_groups
    if basic_blocks >= min_basic_blocks:
        return ps_pairs, sub_groups
    shortage = min_basic_blocks - basic_blocks
    reserved = max(ps_pairs + sub_groups, 1)
    reduce_ps = int(round(shortage * (ps_pairs / reserved)))
    reduce_sub = shortage - reduce_ps
    ps_pairs = max(0, ps_pairs - reduce_ps)
    sub_groups = max(0, sub_groups - reduce_sub)
    while total_blocks - ps_pairs - sub_groups < min_basic_blocks and (ps_pairs > 0 or sub_groups > 0):
        if ps_pairs >= sub_groups and ps_pairs > 0:
            ps_pairs -= 1
        elif sub_groups > 0:
            sub_groups -= 1
        else:
            break
    return ps_pairs, sub_groups


def _apply_generated_data_variant(
    df: pd.DataFrame,
    seam_scale: float,
    tact_time_scale: float,
    length_scale: float,
    width_scale: float,
    thickness_scale: float
) -> pd.DataFrame:
    """Apply scaling for seam/processing and physical attributes."""
    if df is None:
        return df
    adjusted = df.copy()
    if seam_scale != 1.0:
        if '판넬 SEAM 수' in adjusted.columns:
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
        for col in TRAIN_TACT_TIME_COLUMNS:
            if col in adjusted.columns:
                adjusted[col] = adjusted[col].astype(float) * tact_time_scale
        if '총 Tact Time' in adjusted.columns:
            adjusted['총 Tact Time'] = adjusted['총 Tact Time'].astype(float) * tact_time_scale
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
    if '길이' in adjusted.columns and '폭' in adjusted.columns:
        adjusted['면적'] = adjusted['길이'].astype(float) * adjusted['폭'].astype(float)
        if '최소두께' in adjusted.columns:
            adjusted['부피'] = adjusted['면적'].astype(float) * adjusted['최소두께'].astype(float) / 1000.0
    return adjusted


__all__ = [
    "TRAIN_TACT_TIME_COLUMNS",
    "_sample_util_bucket",
    "_compute_spread_days",
    "_adjust_reserved_counts",
    "_apply_generated_data_variant",
]
