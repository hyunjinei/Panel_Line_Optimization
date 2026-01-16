# -*- coding: utf-8 -*-
"""모델 공용 레이어 모음."""
# [AGENT-ADD] single_step_actor.py에서 분리한 레이어

from __future__ import annotations

import torch
import torch.nn as nn


class EnvironmentAwareAttention(nn.Module):
    """환경-블록 간 관계 학습용 cross-attention 모듈."""

    def __init__(self, hidden_dim: int, num_heads: int = 4):
        super().__init__()
        self.cross_attention = nn.MultiheadAttention(hidden_dim, num_heads, batch_first=True)
        self.layer_norm = nn.LayerNorm(hidden_dim)

    def forward(self, block_embeddings: torch.Tensor, env_encoding: torch.Tensor) -> torch.Tensor:
        """
        Args:
            block_embeddings: [batch, num_blocks, hidden_dim]
            env_encoding: [batch, 1, hidden_dim]
        Returns:
            enhanced_blocks: [batch, num_blocks, hidden_dim]
        """
        enhanced, _ = self.cross_attention(
            query=block_embeddings,
            key=env_encoding,
            value=env_encoding
        )
        enhanced = self.layer_norm(block_embeddings + enhanced)
        return enhanced


__all__ = ["EnvironmentAwareAttention"]
