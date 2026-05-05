# -*- coding: utf-8 -*-
"""모델 입력 피처 스펙 정의 (single_step_actor.py에서 분리)."""
# [AGENT-ADD] single_step_actor.py에서 분리한 피처 스펙

from __future__ import annotations

from typing import List

# [AGENT-EDIT] Full vs reduced/constraint/diff feature dimensions.
FULL_BLOCK_FEATURE_DIM = 34
FULL_ENV_STATE_DIM = 31
REDUCED_BLOCK_FEATURE_DIM = 6
REDUCED_ENV_STATE_DIM = 4
CONSTRAINT_BLOCK_FEATURE_DIM = 11  # constraint 모드 블록 피처 축소
CONSTRAINT_ENV_STATE_DIM = 9
DIFF_BLOCK_FEATURE_DIM = 40
DIFF_ENV_STATE_DIM = 39

# Backward-compatible defaults (full)
BLOCK_FEATURE_DIM = FULL_BLOCK_FEATURE_DIM
ENV_STATE_DIM = FULL_ENV_STATE_DIM

# [AGENT-ADD] Feature index groups for normalization/transforms (full).
FULL_BLOCK_BINARY_DIM = 18
FULL_BLOCK_LOG1P_INDICES = [18, 19, 20, 21, 22, 23, 24, 25, 26, 27]
FULL_BLOCK_SIGNED_LOG1P_INDICES = [28]

# [AGENT-ADD] Reduced feature index groups.
REDUCED_BLOCK_BINARY_INDICES = [1]          # has_ps_pair
REDUCED_BLOCK_LOG1P_INDICES = [2, 3, 4]     # seam/c_seam/longi
REDUCED_BLOCK_SIGNED_LOG1P_INDICES: List[int] = []

# [AGENT-ADD] Constraint-aligned feature indices.
CONSTRAINT_BLOCK_BINARY_INDICES = [0, 6]  # has_ps_pair, is_subassembly
CONSTRAINT_BLOCK_LOG1P_INDICES = [1, 2, 3, 4, 5]  # seam/c_seam/longi/width/main_plate
CONSTRAINT_BLOCK_SIGNED_LOG1P_INDICES: List[int] = []

# [AGENT-ADD] Diff-mode feature indices (leakage 제거 + raw-fact augmentation).
DIFF_BLOCK_BINARY_INDICES = list(range(19))
DIFF_BLOCK_LOG1P_INDICES = [19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 36]
DIFF_BLOCK_SIGNED_LOG1P_INDICES = [29]


__all__ = [
    "FULL_BLOCK_FEATURE_DIM",
    "FULL_ENV_STATE_DIM",
    "REDUCED_BLOCK_FEATURE_DIM",
    "REDUCED_ENV_STATE_DIM",
    "CONSTRAINT_BLOCK_FEATURE_DIM",
    "CONSTRAINT_ENV_STATE_DIM",
    "DIFF_BLOCK_FEATURE_DIM",
    "DIFF_ENV_STATE_DIM",
    "BLOCK_FEATURE_DIM",
    "ENV_STATE_DIM",
    "FULL_BLOCK_BINARY_DIM",
    "FULL_BLOCK_LOG1P_INDICES",
    "FULL_BLOCK_SIGNED_LOG1P_INDICES",
    "REDUCED_BLOCK_BINARY_INDICES",
    "REDUCED_BLOCK_LOG1P_INDICES",
    "REDUCED_BLOCK_SIGNED_LOG1P_INDICES",
    "CONSTRAINT_BLOCK_BINARY_INDICES",
    "CONSTRAINT_BLOCK_LOG1P_INDICES",
    "CONSTRAINT_BLOCK_SIGNED_LOG1P_INDICES",
    "DIFF_BLOCK_BINARY_INDICES",
    "DIFF_BLOCK_LOG1P_INDICES",
    "DIFF_BLOCK_SIGNED_LOG1P_INDICES",
]
