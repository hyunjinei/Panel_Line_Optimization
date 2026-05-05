# -*- coding: utf-8 -*-
"""Rollout 스코어/통계 유틸 (assembly_rollout.py에서 분리)."""
# [AGENT-ADD] assembly_rollout.py에서 분리한 스코어/통계 유틸

from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import torch


def calculate_entropy(log_probs: torch.Tensor) -> torch.Tensor:
    """🔥 엔트로피 계산: H(π) = -Σ π(a|s) * log π(a|s)"""
    probs = torch.exp(log_probs)
    entropy = -(probs * log_probs).sum(dim=-1)
    return entropy.mean()


def get_violation_count(stats: Optional[Dict]) -> int:
    if not stats:
        return 0
    if 'total_violations_primary_train' in stats:
        value = stats.get('total_violations_primary_train', 0)
    elif 'total_violations_primary' in stats:
        value = stats.get('total_violations_primary', 0)
    elif 'total_violations_train' in stats:
        value = stats.get('total_violations_train', 0)
    else:
        value = stats.get('total_violations', 0)
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def compute_longi_balance_details(schedule_results: Optional[List[Dict]]) -> Tuple[float, int, int, int]:
    """베이별 론지 불균형 비율과 합계 (ratio, bay_a, bay_b, total)."""
    if not schedule_results:
        return 0.0, 0, 0, 0
    latest_by_block: Dict[int, Tuple[str, float]] = {}
    for row in schedule_results:
        try:
            block_id = int(row.get('block_id')) if row.get('block_id') is not None else None
        except (TypeError, ValueError):
            block_id = None
        if block_id is None:
            continue
        bay = str(row.get('assigned_bay', '')).upper()
        try:
            longi = float(row.get('longi_count', 0.0) or 0.0)
        except (TypeError, ValueError):
            longi = 0.0
        latest_by_block[block_id] = (bay, longi)
    bay_a = 0.0
    bay_b = 0.0
    for bay, longi in latest_by_block.values():
        if '35A' in bay or bay == 'A':
            bay_a += longi
        elif '36B' in bay or bay == 'B':
            bay_b += longi
    total = bay_a + bay_b
    if total <= 0:
        return 0.0, int(bay_a), int(bay_b), int(total)
    ratio = abs(bay_a - bay_b) / total
    return ratio, int(bay_a), int(bay_b), int(total)


def compute_longi_balance_ratio(schedule_results: Optional[List[Dict]]) -> float:
    ratio, _, _, _ = compute_longi_balance_details(schedule_results)
    return ratio


def compute_score(makespan_hours: float,
                  violations: int,
                  longi_balance: float,
                  violation_penalty_weight: float,
                  longi_balance_weight: float) -> float:
    try:
        ms_value = float(makespan_hours)
    except (TypeError, ValueError):
        ms_value = float('inf')
    try:
        vio_value = float(violations)
    except (TypeError, ValueError):
        vio_value = float('inf')
    return ms_value + (violation_penalty_weight * vio_value) + (longi_balance_weight * float(longi_balance))


__all__ = [
    "calculate_entropy",
    "get_violation_count",
    "compute_longi_balance_details",
    "compute_longi_balance_ratio",
    "compute_score",
]
