# PPO/models 핸드북 (상세)

정책 네트워크와 피처 정의가 있는 폴더입니다. 입력 피처와 임베딩 구조를 정의합니다.

---

## 흐름(요약)
```
블록 피처 + env_state
  └─ 임베딩
       └─ 풀링/컨텍스트
            └─ 행동 로그잇
```

## 핵심 개념
- feature_mode에 따라 입력 차원이 바뀝니다.

## 파일별 상세
### `feature_specs.py`
- 역할: 피처 구성과 피처 선택을 정의합니다.
- 입력: 블록 피처, env_state
- 출력: action logits, 선택 확률
- 연결: feature_specs.py와 single_step_actor.py가 핵심입니다.
- 주요 엔트리:
  - (공개 엔트리 없음)

### `layers.py`
- 역할: 네트워크 구성 요소를 정의합니다.
- 입력: 블록 피처, env_state
- 출력: action logits, 선택 확률
- 연결: feature_specs.py와 single_step_actor.py가 핵심입니다.
- 주요 엔트리:
  - EnvironmentAwareAttention (클래스): 핵심 로직을 수행합니다.

### `single_step_actor.py`
- 역할: 정책 네트워크 모델을 정의합니다.
- 입력: 블록 피처, env_state
- 출력: action logits, 선택 확률
- 연결: feature_specs.py와 single_step_actor.py가 핵심입니다.
- 주요 엔트리:
  - SingleStepPtrNet (클래스): 핵심 로직을 수행합니다.
  - extract_block_features_single (함수): 핵심 로직을 수행합니다.

## 주요 클래스/함수 (요약)
| 이름 | 분류 |
|---|---|
| EnvironmentAwareAttention | 클래스 |
| SingleStepPtrNet | 클래스 |
| extract_block_features_single | 함수 |

## 운영 팁
- 모델 로드 시 feature_mode와 embedding_dim이 일치해야 합니다.

## 코드/API 상세 (자동 추출)
<!-- AUTO-GENERATED: DO NOT EDIT BELOW -->

### 파일: `__init__.py`

- 모듈 설명: 설명 없음

#### 클래스 없음

#### 함수 없음

### 파일: `feature_specs.py`

- 모듈 설명: 모델 입력 피처 스펙 정의 (single_step_actor.py에서 분리).

#### 클래스 없음

#### 함수 없음

### 파일: `layers.py`

- 모듈 설명: 모델 공용 레이어 모음.

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| EnvironmentAwareAttention | nn.Module | 환경-블록 간 관계 학습용 cross-attention 모듈. |

##### 클래스: `EnvironmentAwareAttention` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| __init__ | (self, hidden_dim: int, num_heads: int) | - | 설명 없음 |
| forward | (self, block_embeddings: torch.Tensor, env_encoding: torch.Tensor) | torch.Tensor | Args: |

#### 함수 없음

### 파일: `single_step_actor.py`

- 모듈 설명: 설명 없음

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| SingleStepPtrNet | nn.Module | actor.py와 동일한 구조의 Pointer Network |

##### 클래스: `SingleStepPtrNet` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| __init__ | (self, embedding_dim, hidden_dim, n_layers, n_heads, dropout, use_logit_clipping, C, T, feature_dim, feature_mode: str, use_env_state, env_state_dim, use_positional_encoding: bool, use_norm) | - | 설명 없음 |
| _initialize_weights | (self) | - | 가중치 초기화 |
| _build_positional_encoding | (max_len: int, hidden_dim: int) | torch.Tensor | Sinusoidal positional encoding (Transformer 스타일) |
| _add_positional_encoding | (self, enc_h: torch.Tensor) | torch.Tensor | 요청 길이에 맞춰 포지셔널 인코딩을 더한다 |
| _build_env_token | (self, env_state: np.ndarray, device: torch.device, update_stats: bool) | torch.Tensor | 설명 없음 |
| _build_env_tokens | (self, env_state: np.ndarray, device: torch.device, update_stats: bool) | torch.Tensor | Multi-token environment encoding. |
| _encode_env_context | (self, env_state: np.ndarray, device: torch.device, update_stats: bool) | Tuple[torch.Tensor, torch.Tensor] | 설명 없음 |
| _mean_pooling | (self, enc_h: torch.Tensor, mask: Optional[torch.Tensor]) | torch.Tensor | 설명 없음 |
| _fuse_env_into_blocks | (self, block_embeddings: torch.Tensor, env_token: torch.Tensor, env_context: torch.Tensor) | torch.Tensor | 설명 없음 |
| _fixed_stats_enabled | (self) | bool | 설명 없음 |
| enable_fixed_norm_stats | (self, from_running: bool, feature_mean: Optional[torch.Tensor], feature_std: Optional[torch.Tensor], env_mean: Optional[torch.Tensor], env_std: Optional[torch.Tensor]) | None | 고정 통계 정규화 활성화. |
| disable_fixed_norm_stats | (self) | None | 고정 통계 정규화 비활성화. |
| copy_fixed_norm_stats_from | (self, other: 'SingleStepPtrNet') | None | 다른 모델의 고정 통계 버퍼를 복사. |
| forward_single_step | (self, available_block_features: List[np.ndarray], available_indices: List[int], device: torch.device, decode_type: str, env_state: Optional[np.ndarray]) | Tuple[int, float, torch.Tensor] | 단일 스텝 forward (actor.py 스타일) |
| set_sampling_top_k_ratio | (self, ratio: float) | None | 설명 없음 |
| pointer | (self, query, ref, mask) | - | Pointer mechanism (actor.py와 동일) |
| get_action_log_prob | (self, available_block_features: List[np.ndarray], selected_local_idx: int, device: torch.device, env_state: Optional[np.ndarray], available_indices: Optional[List[int]]) | torch.Tensor | 설명 없음 |
| _normalize_env_state | (self, env_state_tensor: torch.Tensor, update_stats: bool) | torch.Tensor | Env state normalization (no running stats). |
| _normalize_block_features | (self, block_features: torch.Tensor, update_stats: bool) | torch.Tensor | Per-step Z-score normalization (no running stats). |

#### 함수 목록

| 함수 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| extract_block_features_single | (block, slack_days: float, deadline_urgency: float, feature_mode: str, **kwargs) | List[float] | 블록 + 제약 맥락 피처 벡터 (feature_mode에 따라 차원 변경) |

<!-- /AUTO-GENERATED -->
