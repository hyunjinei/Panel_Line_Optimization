# -*- coding: utf-8 -*-
"""PPO 학습 러너 공통 유틸 (runner.py에서 분리)."""
# [AGENT-ADD] runner.py에서 분리한 포맷/시드 유틸

from __future__ import annotations

import random
from typing import Any, Dict, List, Optional

import numpy as np
import torch


def _format_sequence_preview(sequence: List[int], limit: int = 30) -> str:
    if not sequence:
        return "-"
    preview = ", ".join(str(v) for v in sequence[:limit])
    if len(sequence) > limit:
        preview += " ..."
    return preview


def _safe_value(value: Optional[int]) -> str:
    return str(value) if value is not None else "∅"


def _compare_sequences(actor_seq: List[int], baseline_seq: List[int], top_k: int = 5) -> Dict[str, Any]:
    top_mismatches = []
    first_mismatch = None
    max_compare = min(len(actor_seq), len(baseline_seq))

    for idx in range(max_compare):
        a_val = actor_seq[idx]
        b_val = baseline_seq[idx]
        if a_val != b_val:
            mismatch = {'index': idx, 'actor': a_val, 'baseline': b_val}
            if first_mismatch is None:
                first_mismatch = mismatch
            if len(top_mismatches) < top_k:
                top_mismatches.append(mismatch)

    if len(actor_seq) != len(baseline_seq):
        idx = max_compare
        a_val = actor_seq[idx] if idx < len(actor_seq) else None
        b_val = baseline_seq[idx] if idx < len(baseline_seq) else None
        mismatch = {'index': idx, 'actor': a_val, 'baseline': b_val}
        if first_mismatch is None:
            first_mismatch = mismatch
        if len(top_mismatches) < top_k:
            top_mismatches.append(mismatch)

    return {
        'same_length': len(actor_seq) == len(baseline_seq),
        'first_mismatch': first_mismatch,
        'top_mismatches': top_mismatches
    }


def _format_mismatch_summary(mismatches: List[Dict[str, Any]]) -> str:
    if not mismatches:
        return "없음"
    return ", ".join(f"{m['index']}:{_safe_value(m['actor'])}≠{_safe_value(m['baseline'])}" for m in mismatches)


def _format_daily_lines(daily_stats: List[Any]) -> List[str]:
    if not daily_stats:
        return ["      (데이터 없음)"]
    lines = []
    for date_label, hours in daily_stats:
        try:
            value = float(hours)
        except (TypeError, ValueError):
            value = 0.0
        lines.append(f"      {date_label}: {value:.2f}h")
    return lines


def _format_violation_lines(violations: List[Dict[str, Any]], limit: int = 5) -> List[str]:
    if not violations:
        return ["      (없음)"]
    lines = []
    if limit is None or limit <= 0:
        limit = len(violations)
    for violation in violations[:limit]:
        lines.append(
            "      [{sev}] constraint={cid} block={bid} msg={msg}".format(
                sev=violation.get('severity', 'INFO'),
                cid=violation.get('constraint', 'N/A'),
                bid=violation.get('block_id', 'N/A'),
                msg=violation.get('message', '')
            )
        )
    if len(violations) > limit:
        lines.append(f"      ... (+{len(violations) - limit} more)")
    return lines


def set_seed(seed: int = 42) -> None:
    """재현성을 위한 시드 설정"""
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed(seed)
        torch.cuda.manual_seed_all(seed)


__all__ = [
    "_format_sequence_preview",
    "_safe_value",
    "_compare_sequences",
    "_format_mismatch_summary",
    "_format_daily_lines",
    "_format_violation_lines",
    "set_seed",
]
