# single_step_actor_new.py
# actor.py와 동일한 구조로 완전히 재작성

import torch
import torch.nn as nn
import torch.nn.functional as F
import math
import numpy as np
from typing import List, Tuple, Optional, Dict

# [AGENT-EDIT] 피처 스펙을 분리 모듈에서 import
from PPO.models.feature_specs import (
    FULL_BLOCK_FEATURE_DIM,
    FULL_ENV_STATE_DIM,
    REDUCED_BLOCK_FEATURE_DIM,
    REDUCED_ENV_STATE_DIM,
    CONSTRAINT_BLOCK_FEATURE_DIM,
    CONSTRAINT_ENV_STATE_DIM,
    BLOCK_FEATURE_DIM,
    ENV_STATE_DIM,
    FULL_BLOCK_BINARY_DIM,
    FULL_BLOCK_LOG1P_INDICES,
    FULL_BLOCK_SIGNED_LOG1P_INDICES,
    REDUCED_BLOCK_BINARY_INDICES,
    REDUCED_BLOCK_LOG1P_INDICES,
    REDUCED_BLOCK_SIGNED_LOG1P_INDICES,
    CONSTRAINT_BLOCK_BINARY_INDICES,
    CONSTRAINT_BLOCK_LOG1P_INDICES,
    CONSTRAINT_BLOCK_SIGNED_LOG1P_INDICES,
)


class SingleStepPtrNet(nn.Module):
    """actor.py와 동일한 구조의 Pointer Network"""
    
    def __init__(self, 
                 embedding_dim=128,
                 hidden_dim=128,
                 n_layers=6,
                 n_heads=8,
                 dropout=0.1,
                 use_logit_clipping=True,
                 C=10.0,
                 T=1.0,
                 feature_dim=BLOCK_FEATURE_DIM,
                 feature_mode: str = "full",
                 use_env_state=False,
                 env_state_dim=ENV_STATE_DIM,
                 use_positional_encoding: bool = False,
                 use_norm=False):
        super().__init__()
        
        # 기본 파라미터
        self.embedding_dim = embedding_dim
        self.hidden_dim = hidden_dim
        self.n_layers = n_layers
        self.n_heads = n_heads
        self.dropout = dropout
        self.use_logit_clipping = use_logit_clipping
        self.C = C
        self.T = T
        self.feature_mode = (feature_mode or "full").lower().strip()
        self.feature_dim = feature_dim
        self.use_env_state = use_env_state
        self.env_state_dim = env_state_dim
        self.use_norm = use_norm
        self.use_positional_encoding = bool(use_positional_encoding)

        # [AGENT-EDIT] Override dims when feature mode is requested.
        if self.feature_mode == "reduced":
            self.feature_dim = REDUCED_BLOCK_FEATURE_DIM
            self.env_state_dim = REDUCED_ENV_STATE_DIM
        elif self.feature_mode == "constraint":
            self.feature_dim = CONSTRAINT_BLOCK_FEATURE_DIM
            self.env_state_dim = CONSTRAINT_ENV_STATE_DIM

        # [AGENT-EDIT] Binary feature indices align with extract_block_features_single ordering.
        if self.feature_mode == "reduced":
            binary_idx_list = list(REDUCED_BLOCK_BINARY_INDICES)
        elif self.feature_mode == "constraint":
            binary_idx_list = list(CONSTRAINT_BLOCK_BINARY_INDICES)
        else:
            binary_idx_list = list(range(FULL_BLOCK_BINARY_DIM))
        self.register_buffer("binary_feature_indices", torch.tensor(binary_idx_list, dtype=torch.long))
        continuous_idx_list = [i for i in range(self.feature_dim) if i not in binary_idx_list]
        self.register_buffer("continuous_feature_indices", torch.tensor(continuous_idx_list, dtype=torch.long))
        # [AGENT-ADD] Transform indices for skewed continuous features.
        if self.feature_mode == "reduced":
            log1p_indices = REDUCED_BLOCK_LOG1P_INDICES
            signed_indices = REDUCED_BLOCK_SIGNED_LOG1P_INDICES
        elif self.feature_mode == "constraint":
            log1p_indices = CONSTRAINT_BLOCK_LOG1P_INDICES
            signed_indices = CONSTRAINT_BLOCK_SIGNED_LOG1P_INDICES
        else:
            log1p_indices = FULL_BLOCK_LOG1P_INDICES
            signed_indices = FULL_BLOCK_SIGNED_LOG1P_INDICES
        self.register_buffer("log1p_feature_indices", torch.tensor(log1p_indices, dtype=torch.long))
        self.register_buffer("signed_log1p_feature_indices", torch.tensor(signed_indices, dtype=torch.long))
        
        # 입력 차원 결정 (환경 상태 concat 여부 반영)
        self.input_dim = self.feature_dim

        # [AGENT-EDIT] 블록 임베딩을 MLP 2층으로 확장 (표현력 강화)
        self.embedding = nn.Sequential(
            nn.Linear(self.input_dim, self.embedding_dim, bias=True),
            nn.ReLU(),
            nn.Linear(self.embedding_dim, hidden_dim, bias=True)
        )
        
        ######################################################################################################################################################################################
        #####                                                                          fix: 시퀀스 구분력 향상을 위해 정현(positional) 인코딩 복원 및 개선                           #####
        ######################################################################################################################################################################################
        self.max_position_embeddings = 512
        if self.use_positional_encoding:
            self.register_buffer(
                "positional_encoding",
                self._build_positional_encoding(self.max_position_embeddings, hidden_dim),
                persistent=False
            )
        else:
            self.positional_encoding = None
        
        # 🆕 Environment state embedding (use_env_state=True일 때만 사용)
        # ==== [AGENT-EDIT] Simple MLP-based env fusion (no cross-attention) ====
        if self.use_env_state:
            self.env_state_encoder = nn.Sequential(
                nn.Linear(self.env_state_dim, self.env_state_dim),
                nn.ReLU(),
                nn.Linear(self.env_state_dim, self.env_state_dim),
                nn.LayerNorm(self.env_state_dim)
            )
            self.register_buffer("env_state_mean", torch.zeros(self.env_state_dim))
            self.register_buffer("env_state_var", torch.ones(self.env_state_dim))
            self.env_normalization_momentum = 0.01
            self.env_token_projection = nn.Linear(self.env_state_dim, hidden_dim)
            # [AGENT-EDIT] Simple MLP fusion: env_context -> hidden bias (residual add).
            self.env_fusion_mlp = nn.Sequential(
                nn.Linear(hidden_dim, hidden_dim),
                nn.ReLU(),
                nn.Linear(hidden_dim, hidden_dim)
            )
        else:
            self.env_state_encoder = None
            self.env_token_projection = None
            self.env_fusion_mlp = None

        # [AGENT-ADD] Running normalization for continuous block features.
        self.register_buffer("feature_mean", torch.zeros(self.feature_dim))
        self.register_buffer("feature_var", torch.ones(self.feature_dim))
        self.feature_norm_momentum = 0.01
        # [AGENT-EDIT] Learnable mix for global/step normalization.
        self.feature_norm_mix_logit = nn.Parameter(torch.tensor(0.85))

        # [AGENT-ADD] env_state normalization mode: "identity" or "running".
        self.env_state_norm_mode = "identity"
        # [AGENT-ADD] Precomputed normalization stats (optional).
        self.register_buffer("fixed_norm_stats_flag", torch.tensor(0, dtype=torch.uint8))
        self.register_buffer("fixed_feature_mean", torch.zeros(self.feature_dim))
        self.register_buffer("fixed_feature_std", torch.ones(self.feature_dim))
        self.register_buffer("fixed_env_mean", torch.zeros(self.env_state_dim))
        self.register_buffer("fixed_env_std", torch.ones(self.env_state_dim))

        # [AGENT-EDIT] Simplified env fusion: multi-token projection 비활성화.
        self.env_token_projection_multi = None

        # Pointer 관련 레이어 (actor.py와 동일)
        self.Vec = nn.Parameter(torch.FloatTensor(hidden_dim))
        self.W_q = nn.Linear(hidden_dim, hidden_dim, bias=True)
        self.W_ref = nn.Conv1d(hidden_dim, hidden_dim, 1, 1)

        # [AGENT-EDIT] Strong masking 환경에서는 추가 로짓 편향을 제거하여 과결정 방지
        self.logit_feature_bias_head = None
        
        # [AGENT-EDIT] Strong masking 환경에서는 평균 풀링이 더 안정적
        self.attention_pooling = None
        
        # 환경 융합은 로짓 조정 단계에서 수행

        # 🔥 Context projection 크기 조정 (환경 정보는 블록에 융합되므로 단일 차원)
        self.context_projection = nn.Linear(hidden_dim, hidden_dim)

        # [AGENT-EDIT] Value head 제거 (policy-only 학습)
        
        # 초기화
        self._initialize_weights()
        
        # Decode type
        self.default_decode_type = "sampling"
        self._debug_logit_prints = 0
        self._debug_prob_prints = 0
        self.sampling_top_k_ratio: float = 1.0
    
    def _initialize_weights(self):
        """가중치 초기화"""
        for param in self.parameters():
            if len(param.shape) >= 2:
                nn.init.xavier_uniform_(param)
            else:
                nn.init.uniform_(param, -0.08, 0.08)

    @staticmethod
    def _build_positional_encoding(max_len: int, hidden_dim: int) -> torch.Tensor:
        """Sinusoidal positional encoding (Transformer 스타일)"""
        pe = torch.zeros(max_len, hidden_dim)
        position = torch.arange(0, max_len, dtype=torch.float32).unsqueeze(1)
        div_term = torch.exp(torch.arange(0, hidden_dim, 2).float() * (-math.log(10000.0) / hidden_dim))
        pe[:, 0::2] = torch.sin(position * div_term)
        pe[:, 1::2] = torch.cos(position * div_term)
        return pe.unsqueeze(0)  # [1, max_len, hidden_dim]

    def _add_positional_encoding(self, enc_h: torch.Tensor) -> torch.Tensor:
        """요청 길이에 맞춰 포지셔널 인코딩을 더한다"""
        if not self.use_positional_encoding or self.positional_encoding is None:
            return enc_h
        seq_len = enc_h.size(1)
        if seq_len > self.positional_encoding.size(1):
            raise ValueError(f"지원 가능한 최대 블록 수({self.positional_encoding.size(1)})를 초과했습니다: {seq_len}")
        pe_slice = self.positional_encoding[:, :seq_len, :].to(enc_h.device)
        return enc_h + pe_slice

    def _build_env_token(self, env_state: np.ndarray, device: torch.device, update_stats: bool) -> torch.Tensor:
        if not self.use_env_state:
            return None
        if env_state is None:
            raise ValueError("env_state is required when use_env_state=True")
        if len(env_state) != self.env_state_dim:
            raise ValueError(
                f"env_state dimension mismatch: expected {self.env_state_dim}, got {len(env_state)}"
            )
        env_state_tensor = torch.tensor(
            env_state,
            dtype=torch.float32,
            device=device
        ).unsqueeze(0)
        env_state_norm = self._normalize_env_state(env_state_tensor, update_stats=update_stats)
        env_state_encoded = self.env_state_encoder(env_state_norm) if self.env_state_encoder else env_state_norm
        env_token = self.env_token_projection(env_state_encoded)  # [1, hidden_dim]
        return env_token.unsqueeze(1)  # [1, 1, hidden_dim]

    def _build_env_tokens(self, env_state: np.ndarray, device: torch.device, update_stats: bool) -> torch.Tensor:
        """Multi-token environment encoding."""
        if not self.use_env_state:
            return None
        if env_state is None:
            raise ValueError("env_state is required when use_env_state=True")
        if len(env_state) != self.env_state_dim:
            raise ValueError(
                f"env_state dimension mismatch: expected {self.env_state_dim}, got {len(env_state)}"
            )
        env_state_tensor = torch.tensor(
            env_state,
            dtype=torch.float32,
            device=device
        ).unsqueeze(0)
        env_state_norm = self._normalize_env_state(env_state_tensor, update_stats=update_stats)
        env_state_encoded = self.env_state_encoder(env_state_norm) if self.env_state_encoder else env_state_norm
        if self.env_token_projection_multi is None:
            env_token = self.env_token_projection(env_state_encoded)
            return env_token.unsqueeze(1)

        # [AGENT-EDIT] Split env_state into semantic groups (global/capacity/workload/availability).
        global_idx = torch.tensor([0, 1, 2, 3, 4], device=device)
        capacity_idx = torch.tensor([5, 6, 7], device=device)
        workload_idx = torch.tensor(list(range(8, 23)), device=device)
        available_idx = torch.tensor(list(range(23, 31)), device=device)

        def _mean_pool(tensor: torch.Tensor, idx: torch.Tensor) -> torch.Tensor:
            if idx.numel() == 0:
                return torch.zeros_like(tensor[:, :1])
            pooled = torch.mean(tensor[:, idx], dim=1, keepdim=True)
            return pooled

        g = _mean_pool(env_state_encoded, global_idx)
        c = _mean_pool(env_state_encoded, capacity_idx)
        w = _mean_pool(env_state_encoded, workload_idx)
        a = _mean_pool(env_state_encoded, available_idx)
        pooled = torch.cat([g, c, w, a], dim=1)
        projected = self.env_token_projection_multi(pooled)
        tokens = projected.view(1, 4, -1)
        return tokens

    # ==== [AGENT-ADD] Env fusion helpers ====
    def _encode_env_context(self, env_state: np.ndarray, device: torch.device, update_stats: bool) -> Tuple[torch.Tensor, torch.Tensor]:
        env_token = self._build_env_token(env_state, device, update_stats=update_stats)
        env_context = env_token.squeeze(1)
        return env_token, env_context

    # [AGENT-ADD] Mean pooling helper (mask-aware)
    def _mean_pooling(self, enc_h: torch.Tensor, mask: Optional[torch.Tensor]) -> torch.Tensor:
        if mask is None:
            return enc_h.mean(dim=1)
        mask_f = mask.float().unsqueeze(-1)
        summed = (enc_h * mask_f).sum(dim=1)
        denom = mask_f.sum(dim=1).clamp(min=1.0)
        return summed / denom

    def _fuse_env_into_blocks(
        self,
        block_embeddings: torch.Tensor,
        env_token: torch.Tensor,
        env_context: torch.Tensor
    ) -> torch.Tensor:
        fused = block_embeddings
        if self.env_fusion_mlp is not None:
            # [AGENT-EDIT] MLP 기반 단순 결합 (env_bias를 잔차로 더함)
            env_bias = self.env_fusion_mlp(env_context)
            fused = fused + env_bias.unsqueeze(1)
        return fused

    # ==== [AGENT-ADD] Fixed normalization helpers ====
    def _fixed_stats_enabled(self) -> bool:
        return bool(self.fixed_norm_stats_flag.item())

    def enable_fixed_norm_stats(
        self,
        from_running: bool = True,
        feature_mean: Optional[torch.Tensor] = None,
        feature_std: Optional[torch.Tensor] = None,
        env_mean: Optional[torch.Tensor] = None,
        env_std: Optional[torch.Tensor] = None
    ) -> None:
        """고정 통계 정규화 활성화."""
        if from_running:
            self.fixed_feature_mean.copy_(self.feature_mean)
            self.fixed_feature_std.copy_(torch.sqrt(self.feature_var + 1e-6))
            if self.use_env_state:
                self.fixed_env_mean.copy_(self.env_state_mean)
                self.fixed_env_std.copy_(torch.sqrt(self.env_state_var + 1e-6))
        if feature_mean is not None:
            self.fixed_feature_mean.copy_(feature_mean.to(self.fixed_feature_mean.device))
        if feature_std is not None:
            self.fixed_feature_std.copy_(feature_std.to(self.fixed_feature_std.device))
        if env_mean is not None:
            self.fixed_env_mean.copy_(env_mean.to(self.fixed_env_mean.device))
        if env_std is not None:
            self.fixed_env_std.copy_(env_std.to(self.fixed_env_std.device))
        self.fixed_norm_stats_flag.fill_(1)

    def disable_fixed_norm_stats(self) -> None:
        """고정 통계 정규화 비활성화."""
        self.fixed_norm_stats_flag.fill_(0)

    def copy_fixed_norm_stats_from(self, other: "SingleStepPtrNet") -> None:
        """다른 모델의 고정 통계 버퍼를 복사."""
        self.fixed_feature_mean.copy_(other.fixed_feature_mean)
        self.fixed_feature_std.copy_(other.fixed_feature_std)
        self.fixed_env_mean.copy_(other.fixed_env_mean)
        self.fixed_env_std.copy_(other.fixed_env_std)
        self.fixed_norm_stats_flag.copy_(other.fixed_norm_stats_flag)
    
    def forward_single_step(self, 
                           available_block_features: List[np.ndarray],
                           available_indices: List[int],
                           device: torch.device,
                           decode_type: str = None,
                           env_state: Optional[np.ndarray] = None) -> Tuple[int, float, torch.Tensor]:
        """
        단일 스텝 forward (actor.py 스타일)
        
        Args:
            available_block_features: 선택 가능한 블록들의 특성
            available_indices: 선택 가능한 블록들의 인덱스
            device: 디바이스
            decode_type: 디코딩 타입 (greedy/sampling)
            env_state: 🆕 환경 상태 벡터 (use_env_state=True일 때만)
        
        Returns:
            selected_idx: 선택된 블록 인덱스
            confidence: 신뢰도
            log_probs: 로그 확률
        """
        if decode_type is None:
            decode_type = self.default_decode_type
        
        # 입력 준비
        num_blocks = len(available_block_features)
        if num_blocks == 0:
            return 0, 0.0, torch.zeros(1)
        
        raw_features = np.array(available_block_features, dtype=np.float32)
        block_features = torch.tensor(
            raw_features,
            dtype=torch.float32, 
            device=device
        ).unsqueeze(0)  # [1, num_blocks, feature_dim]

        normalized_features = self._normalize_block_features(block_features, update_stats=self.training)

        block_embeddings = self.embedding(normalized_features)
        block_embeddings = self._add_positional_encoding(block_embeddings)
        block_mask = torch.ones(1, num_blocks, dtype=torch.bool, device=device)

        # ==== [AGENT-EDIT] Env fusion (cross-attn + FiLM) ====
        if self.use_env_state:
            env_token, env_context = self._encode_env_context(env_state, device, update_stats=self.training)
            block_embeddings = self._fuse_env_into_blocks(block_embeddings, env_token, env_context)
            graph_embedding = self._mean_pooling(block_embeddings, block_mask)
            context = self.context_projection(graph_embedding + env_context)
            logits = self.pointer(context, block_embeddings, mask=block_mask)
        else:
            graph_embedding = self._mean_pooling(block_embeddings, block_mask)
            context = self.context_projection(graph_embedding)
            logits = self.pointer(context, block_embeddings, mask=block_mask)

        # [AGENT-EDIT] logit_feature_bias_head 제거됨

        # 🔍 로짓 분석 및 경고 시스템
        top_logits, top_indices = torch.topk(logits, min(3, logits.size(-1)), dim=-1)

        # if self._debug_logit_prints < 5:
        #     sample_idx = self._debug_logit_prints + 1
        #     top_info = [
        #         f"id={available_indices[top_indices[0, i].item()]:>4}, logit={top_logits[0, i].item():+.3f}"
        #         for i in range(top_logits.size(-1))
        #     ]
        #     print(f"[Actor-Logit] sample#{sample_idx}: std={torch.std(logits).item():.3f}, range={(torch.max(logits)-torch.min(logits)).item():.3f} | {top_info}")
        #     self._debug_logit_prints += 1
        
        # # 🔍 로짓 출력 (상위 3개) - 주석 처리
        # print(f"🔍 [forward_single_step] 상위 3개 로짓:")
        # for i in range(top_logits.size(-1)):
        #     idx = top_indices[0, i].item()
        #     logit_val = top_logits[0, i].item()
        #     print(f"  블록{idx}: 로짓={logit_val:.3f}")
        
        # 디코딩
        if decode_type == "greedy":
            selected_local_idx = torch.argmax(logits, dim=1).item()
        elif decode_type == "sampling":
            # 일반 softmax sampling
            probs = F.softmax(logits / self.T, dim=-1)

            if self.sampling_top_k_ratio < 0.999:
                k = max(1, int(math.ceil(num_blocks * max(self.sampling_top_k_ratio, 0.01))))
                top_probs, top_indices = torch.topk(probs, k, dim=-1)
                mask = torch.zeros_like(probs)
                mask.scatter_(1, top_indices, top_probs)
                probs = mask / (mask.sum(dim=-1, keepdim=True) + 1e-8)
            top_probs, top_prob_indices = torch.topk(probs, min(3, probs.size(-1)), dim=-1)

            # if self._debug_prob_prints < 5:
            #     sample_idx = self._debug_prob_prints + 1
            #     entries = [
            #         f"id={available_indices[top_prob_indices[0, i].item()]:>4}, prob={top_probs[0, i].item():.3f}"
            #         for i in range(top_probs.size(-1))
            #     ]
            #     entropy = -torch.sum(probs * torch.log(probs + 1e-8)).item()
            #     print(f"[Actor-Prob] sample#{sample_idx}: entropy={entropy:.3f}, max={torch.max(probs).item():.3f} | {entries}")
            #     self._debug_prob_prints += 1
            
            # # 🔍 확률 출력 (상위 3개) - 주석 처리
            # print(f"🔍 [forward_single_step] 상위 3개 확률:")
            # for i in range(top_probs.size(-1)):
            #     idx = top_prob_indices[0, i].item()
            #     prob_val = top_probs[0, i].item()
            #     print(f"  블록{idx}: 확률={prob_val:.3f}")
            
            selected_local_idx = torch.multinomial(probs, 1).squeeze(-1).item()            
        else:
            # Gumbel-Softmax sampling
            gumbel_noise = -torch.log(-torch.log(torch.rand_like(logits) + 1e-10) + 1e-10)
            gumbel_logits = (logits + gumbel_noise) / self.T
            probs = F.softmax(gumbel_logits, dim=-1)
            selected_local_idx = torch.multinomial(probs, 1).squeeze(-1).item()
        
        # print(f"🎯 [forward_single_step] 선택된 블록: {selected_local_idx}")  # 주석 처리
        
        # 실제 블록 인덱스로 변환
        selected_idx = available_indices[selected_local_idx]
        
        # 신뢰도 계산
        probs = F.softmax(logits / self.T, dim=-1)
        confidence = probs[0, selected_local_idx].item()
        
        # 로그 확률
        log_probs = F.log_softmax(logits / self.T, dim=-1).squeeze(0)

        # 🔍 최종 로짓 확인 (랜덤 2개 블록)
        # if seq_len >= 2:
        #     logits_np = logits[0].cpu().detach().numpy()
        #     probs_np = F.softmax(logits / self.T, dim=-1)[0].cpu().detach().numpy()
        #     print(f"📊 [Step] 최종 로짓 - 블록 {indices[0]}, {indices[1]}:")
        #     for i, idx in enumerate(indices):
        #         print(f"  블록{idx}: 로짓={logits_np[idx]:.3f}, 확률={probs_np[idx]:.3f}")
        #     print(f"  선택된 블록: {selected_local_idx} (확률={probs_np[selected_local_idx]:.3f})")
        
        return selected_idx, confidence, log_probs

    def set_sampling_top_k_ratio(self, ratio: float) -> None:
        ratio = max(0.05, min(1.0, float(ratio)))
        self.sampling_top_k_ratio = ratio
    
    def pointer(self, query, ref, mask=None):
        """
        Pointer mechanism (actor.py와 동일)
        
        Args:
            query: [batch_size, hidden_dim]
            ref: [batch_size, num_nodes, hidden_dim]
            mask: [batch_size, num_nodes] (optional)
        """
        batch_size = query.size(0)
        num_nodes = ref.size(1)
        
        # Query projection
        u1 = self.W_q(query)  # [batch_size, hidden_dim]
        
        # Reference projection
        u2 = self.W_ref(ref.permute(0, 2, 1))  # [batch_size, hidden_dim, num_nodes]
        
        # Vec 확장
        V = self.Vec.unsqueeze(0).expand(batch_size, 1, -1)  # [batch_size, 1, hidden_dim]
        
        # 호환성 점수 계산
        u1 = u1.unsqueeze(2)  # [batch_size, hidden_dim, 1]
        u = torch.bmm(V, torch.tanh(u1 + u2))  # [batch_size, 1, num_nodes]
        u = u.squeeze(1)  # [batch_size, num_nodes]
        
        # 클리핑 적용
        if self.use_logit_clipping:
            u = self.C * torch.tanh(u)
        
        # 마스크 적용
        if mask is not None:
            u = u.masked_fill(~mask, -float('inf'))
        
        return u
    
    def get_action_log_prob(self,
                           available_block_features: List[np.ndarray],
                           selected_local_idx: int,
                           device: torch.device,
                           env_state: Optional[np.ndarray] = None,
                           available_indices: Optional[List[int]] = None) -> torch.Tensor:
        num_blocks = len(available_block_features)
        block_features = torch.tensor(
            np.array(available_block_features, dtype=np.float32),
            dtype=torch.float32,
            device=device,
            requires_grad=False
        ).unsqueeze(0)

        normalized_features = self._normalize_block_features(block_features, update_stats=False)
        block_embeddings = self.embedding(normalized_features)
        block_embeddings = self._add_positional_encoding(block_embeddings)
        block_mask = torch.ones(1, num_blocks, dtype=torch.bool, device=device)

        if self.use_env_state:
            env_token, env_context = self._encode_env_context(env_state, device, update_stats=False)
            block_embeddings = self._fuse_env_into_blocks(block_embeddings, env_token, env_context)
            graph_embedding = self._mean_pooling(block_embeddings, block_mask)
            context = self.context_projection(graph_embedding + env_context)
            logits = self.pointer(context, block_embeddings, mask=block_mask)
        else:
            graph_embedding = self._mean_pooling(block_embeddings, block_mask)
            context = self.context_projection(graph_embedding)
            logits = self.pointer(context, block_embeddings, mask=block_mask)

        # [AGENT-EDIT] logit_feature_bias_head 제거됨

        log_probs = F.log_softmax(logits / self.T, dim=-1)
        return log_probs[0, selected_local_idx]

    # [AGENT-EDIT] Value head 관련 함수 제거 (policy-only 학습)

    def _normalize_env_state(self, env_state_tensor: torch.Tensor, update_stats: bool = True) -> torch.Tensor:
        """
        Env state normalization (no running stats).
        """
        flat = env_state_tensor.squeeze(0)
        # [AGENT-EDIT] 고정 통계가 명시된 경우만 사용 (러닝 평균 금지).
        if self._fixed_stats_enabled():
            mean = self.fixed_env_mean
            std = torch.clamp(self.fixed_env_std, min=1e-6)
            normalized = (flat - mean) / std
            return torch.clamp(normalized, -5.0, 5.0).unsqueeze(0)
        # [AGENT-EDIT] env_state는 이미 0~1 또는 -1~1 범위로 스케일됨 → 단순 클립만 수행.
        clipped = torch.clamp(flat, -1.5, 1.5)
        return clipped.unsqueeze(0)

    def _normalize_block_features(self, block_features: torch.Tensor, update_stats: bool = True) -> torch.Tensor:
        """
        Per-step Z-score normalization (no running stats).
        Args:
            block_features: [1, num_blocks, feature_dim]
        """
        flat = block_features.squeeze(0)  # [num_blocks, feature_dim]
        normalized = flat.clone()
        cont_idx = self.continuous_feature_indices
        if cont_idx.numel() > 0:
            # [AGENT-EDIT] Skewed continuous features use log1p / signed log1p.
            transformed = flat.clone()
            if self.log1p_feature_indices.numel() > 0:
                log_idx = self.log1p_feature_indices
                transformed[:, log_idx] = torch.log1p(torch.clamp(transformed[:, log_idx], min=0.0))
            if self.signed_log1p_feature_indices.numel() > 0:
                signed_idx = self.signed_log1p_feature_indices
                signed_vals = transformed[:, signed_idx]
                transformed[:, signed_idx] = torch.sign(signed_vals) * torch.log1p(torch.abs(signed_vals))

            cont_feats = transformed[:, cont_idx]

            # Per-step stats only (running mean/var 금지)
            batch_mean = cont_feats.mean(dim=0)
            batch_var = cont_feats.var(dim=0, unbiased=False)
            batch_std = torch.sqrt(batch_var + 1e-6)

            if self._fixed_stats_enabled():
                fixed_mean = self.fixed_feature_mean[cont_idx]
                fixed_std = torch.clamp(self.fixed_feature_std[cont_idx], min=1e-6)
                normalized_cont = (cont_feats - fixed_mean) / fixed_std
            else:
                normalized_cont = (cont_feats - batch_mean) / batch_std

            normalized[:, cont_idx] = torch.clamp(normalized_cont, -5.0, 5.0)

        if self.binary_feature_indices.numel() > 0:
            normalized[:, self.binary_feature_indices] = flat[:, self.binary_feature_indices]
        return normalized.unsqueeze(0)


############
# 수정했음 #
############

############
# 수정했음 #
############

def extract_block_features_single(
    block,
    slack_days: float = 0.0,
    deadline_urgency: float = 0.0,
    feature_mode: str = "full",
    **kwargs,
) -> List[float]:
    """블록 + 제약 맥락 피처 벡터 (feature_mode에 따라 차원 변경)"""

    features: List[float] = []

    feature_mode = (feature_mode or "full").lower().strip()

    # ==== Reduced features (total 6) ====
    if feature_mode == "reduced":
        port_state = getattr(block, 'port_starboard', None)
        port_value = getattr(port_state, 'value', str(port_state)).upper() if port_state is not None else ""
        if port_value == 'P':
            ps_type = 1.0
        elif port_value == 'S':
            ps_type = -1.0
        else:
            ps_type = 0.0

        has_ps_pair = 1.0 if getattr(block, 'pair_block_id', None) else 0.0
        seam_count = float(getattr(block, 'seam_count', 0.0) or 0.0)
        c_seam_count = float(getattr(block, 'c_seam_count', 0.0) or 0.0)
        longi_count = float(getattr(block, 'longi_count', 0.0) or 0.0)

        features.extend([
            ps_type,
            has_ps_pair,
            seam_count,
            c_seam_count,
            longi_count,
            float(deadline_urgency)
        ])

        if len(features) != REDUCED_BLOCK_FEATURE_DIM:
            raise ValueError(f"block feature length mismatch (reduced): expected {REDUCED_BLOCK_FEATURE_DIM}, got {len(features)}")
        return features

    # ==== [AGENT-EDIT] Constraint-aligned features (11 dims) ====
    if feature_mode == "constraint":
        has_ps_pair = 1.0 if getattr(block, 'pair_block_id', None) else 0.0
        seam_count = float(getattr(block, 'seam_count', 0.0) or 0.0)
        c_seam_count = float(getattr(block, 'c_seam_count', 0.0) or 0.0)
        longi_count = float(getattr(block, 'longi_count', 0.0) or 0.0)
        width = float(getattr(block, 'width', 0.0) or 0.0)
        main_plate_count = float(getattr(block, 'main_plate_count', 0.0) or 0.0)
        is_subassembly = 1.0 if getattr(block, 'is_subassembly', False) else 0.0

        three_bay_state = float(kwargs.get('three_bay_state', 0.0) or 0.0)
        masking_stage_value = float(kwargs.get('masking_stage_value', 0.0) or 0.0)
        workshop_backlog_ratio = float(kwargs.get('workshop_backlog_ratio', 0.0) or 0.0)

        # [AGENT-EDIT] constraint 모드: 11개 피처만 유지
        features.extend([
            has_ps_pair,
            seam_count,
            c_seam_count,
            longi_count,
            width,
            main_plate_count,
            is_subassembly,
            float(deadline_urgency),
            three_bay_state,
            masking_stage_value,
            workshop_backlog_ratio
        ])

        if len(features) != CONSTRAINT_BLOCK_FEATURE_DIM:
            raise ValueError(
                f"block feature length mismatch (constraint): expected {CONSTRAINT_BLOCK_FEATURE_DIM}, got {len(features)}"
            )
        return features

    # ==== Binary features (0~17) ====
    has_ps_pair = 1.0 if getattr(block, 'pair_block_id', None) else 0.0
    port_state = getattr(block, 'port_starboard', None)
    port_value = getattr(port_state, 'value', str(port_state)).upper() if port_state is not None else ""
    is_port = 1.0 if port_value == 'P' else 0.0
    is_starboard = 1.0 if port_value == 'S' else 0.0
    is_center = 1.0 if port_value == 'C' else 0.0

    has_curved_plate = 1.0 if getattr(block, 'has_curved_plate', False) else 0.0
    is_draft = 1.0 if getattr(block, 'is_draft', False) else 0.0
    is_cross_seam = 1.0 if getattr(block, 'is_cross_seam', False) else 0.0
    is_main_plate_only = 1.0 if getattr(block, 'is_main_plate_only', False) else 0.0

    material_type = getattr(block, 'material_type', None)
    material_name = getattr(material_type, 'value', str(material_type)).upper() if material_type is not None else ""
    material_is_outsourcing = 1.0 if 'OUT' in material_name else 0.0
    material_is_lt = 1.0 if 'LT' in material_name else 0.0
    material_ready = 1.0 if getattr(block, 'material_ready', True) else 0.0

    is_subassembly = 1.0 if getattr(block, 'is_subassembly', False) else 0.0

    needs_pm = False
    try:
        needs_pm = block.needs_afternoon_start()
    except Exception:
        needs_pm = False
    needs_pm_flag = 1.0 if needs_pm else 0.0

    candidate_branch_35a = 1.0 if float(kwargs.get('candidate_branch_35a', 0.0) or 0.0) > 0.0 else 0.0
    candidate_branch_36b = 1.0 if float(kwargs.get('candidate_branch_36b', 0.0) or 0.0) > 0.0 else 0.0
    ps_forced_flag = 1.0 if float(kwargs.get('ps_forced_flag', 0.0) or 0.0) > 0.0 else 0.0
    line_is_fixed = 1.0 if float(kwargs.get('line_is_fixed', 0.0) or 0.0) > 0.0 else 0.0
    line_is_line = 1.0 if float(kwargs.get('line_is_line', 0.0) or 0.0) > 0.0 else 0.0

    features.extend([
        has_ps_pair,
        is_port,
        is_starboard,
        is_center,
        has_curved_plate,
        is_draft,
        is_cross_seam,
        is_main_plate_only,
        material_is_outsourcing,
        material_is_lt,
        material_ready,
        is_subassembly,
        needs_pm_flag,
        candidate_branch_35a,
        candidate_branch_36b,
        ps_forced_flag,
        line_is_fixed,
        line_is_line
    ])

    # ==== Continuous features (18~33) ====
    seam_count = float(getattr(block, 'seam_count', 0.0) or 0.0)
    c_seam_count = float(getattr(block, 'c_seam_count', 0.0) or 0.0)
    longi_count = float(getattr(block, 'longi_count', 0.0) or 0.0)
    width = float(getattr(block, 'width', 0.0) or 0.0)
    length = float(getattr(block, 'length', 0.0) or 0.0)
    max_thickness = float(getattr(block, 'max_thickness', 0.0) or 0.0)
    main_plate_count = float(getattr(block, 'main_plate_count', 0.0) or 0.0)
    angle_count = float(getattr(block, 'angle_count', 0.0) or 0.0)
    buildup_count = float(getattr(block, 'buildup_count', 0.0) or 0.0)
    processing_times = getattr(block, 'processing_times', None)
    total_processing_time = float(sum(processing_times)) if processing_times else 0.0

    workshop_backlog_ratio = float(kwargs.get('workshop_backlog_ratio', 0.0) or 0.0)
    line_backlog_ratio = float(kwargs.get('line_backlog_ratio', 0.0) or 0.0)
    masking_stage_value = float(kwargs.get('masking_stage_value', 0.0) or 0.0)
    three_bay_state = float(kwargs.get('three_bay_state', 0.0) or 0.0)

    features.extend([
        seam_count,
        c_seam_count,
        longi_count,
        width,
        length,
        max_thickness,
        main_plate_count,
        angle_count,
        buildup_count,
        total_processing_time,
        float(slack_days),
        float(deadline_urgency),
        workshop_backlog_ratio,
        line_backlog_ratio,
        masking_stage_value,
        three_bay_state
    ])

    # [AGENT-EDIT] Safety check to keep feature length in sync with model input.
    if len(features) != FULL_BLOCK_FEATURE_DIM:
        raise ValueError(f"block feature length mismatch: expected {FULL_BLOCK_FEATURE_DIM}, got {len(features)}")

    return features


# Critic은 제거됨 (actor.py와 동일하게)
