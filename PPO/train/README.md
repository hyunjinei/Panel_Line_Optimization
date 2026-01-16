# PPO/train 핸드북 (상세)

PPO 학습 실행과 롤아웃 루프를 담당합니다. 학습/자기라벨 방식 모두 여기서 흐름이 시작됩니다.

---

## 흐름(요약)
```
데이터 생성
  └─ 롤아웃
       └─ 점수/어드밴티지
            └─ 파라미터 업데이트
```

## 핵심 개념
- self_label은 LPT 기준 비교로 업데이트 여부를 결정합니다.
- ppo 모드는 정책 확률과 손실을 사용합니다.

## 파일별 상세
### `assembly_rollout.py`
- 역할: 롤아웃 및 학습 루프를 담당합니다.
- 입력: 생성 블록, 모델 설정, 하이퍼파라미터
- 출력: 학습 로그, 모델 체크포인트
- 연결: runner.py가 학습 진입점입니다.
- 주요 엔트리:
  - AssemblyPPORollout (클래스): 핵심 로직을 수행합니다.

### `data_utils.py`
- 역할: 학습 데이터 생성과 샘플링을 지원합니다.
- 입력: 생성 블록, 모델 설정, 하이퍼파라미터
- 출력: 학습 로그, 모델 체크포인트
- 연결: runner.py가 학습 진입점입니다.
- 주요 엔트리:
  - (공개 엔트리 없음)

### `helpers.py`
- 역할: 공통 헬퍼 함수 모음입니다.
- 입력: 생성 블록, 모델 설정, 하이퍼파라미터
- 출력: 학습 로그, 모델 체크포인트
- 연결: runner.py가 학습 진입점입니다.
- 주요 엔트리:
  - set_seed (함수): 설정을 갱신합니다.

### `rollout_metrics.py`
- 역할: 롤아웃 및 학습 루프를 담당합니다.
- 입력: 생성 블록, 모델 설정, 하이퍼파라미터
- 출력: 학습 로그, 모델 체크포인트
- 연결: runner.py가 학습 진입점입니다.
- 주요 엔트리:
  - calculate_entropy (함수): 계산을 수행합니다.
  - get_violation_count (함수): 상태/정보를 조회합니다.
  - compute_longi_balance_details (함수): 핵심 로직을 수행합니다.
  - compute_longi_balance_ratio (함수): 핵심 로직을 수행합니다.
  - compute_score (함수): 핵심 로직을 수행합니다.

### `runner.py`
- 역할: 실행 진입점과 CLI 처리를 담당합니다.
- 입력: 생성 블록, 모델 설정, 하이퍼파라미터
- 출력: 학습 로그, 모델 체크포인트
- 연결: runner.py가 학습 진입점입니다.
- 주요 엔트리:
  - get_actor_params (함수): 상태/정보를 조회합니다.
  - generate_test_datasets (함수): 핵심 로직을 수행합니다.
  - generate_baseline_test_datasets (함수): 핵심 로직을 수행합니다.
  - main (함수): 핵심 로직을 수행합니다.

## 주요 클래스/함수 (요약)
| 이름 | 분류 |
|---|---|
| AssemblyPPORollout | 클래스 |
| set_seed | 함수 |
| calculate_entropy | 함수 |
| get_violation_count | 함수 |
| compute_longi_balance_details | 함수 |
| compute_longi_balance_ratio | 함수 |
| compute_score | 함수 |
| get_actor_params | 함수 |
| generate_test_datasets | 함수 |
| generate_baseline_test_datasets | 함수 |
| main | 함수 |

## 운영 팁
- 학습 분포는 eval_like 설정과 num_blocks 범위로 제어합니다.

## 코드/API 상세 (자동 추출)
<!-- AUTO-GENERATED: DO NOT EDIT BELOW -->

### 파일: `__init__.py`

- 모듈 설명: 설명 없음

#### 클래스 없음

#### 함수 없음

### 파일: `assembly_rollout.py`

- 모듈 설명: Assembly PPO with Rollout Baseline

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| AssemblyPPORollout | 없음 | PPO with Rollout Baseline for Assembly Scheduling + Self-Label mode switch |

##### 클래스: `AssemblyPPORollout` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| __init__ | (self, actor_params: dict, device: torch.device, lr: float, epsilon: float, ppo_iterations: int, baseline_update_interval: int, statistical_alpha: float, grad_clip: float, use_env_state: bool, fixed_norm_stats: bool, fixed_norm_warmup_episodes: int, start_date_change_interval: int, start_date_offset_days: int, entropy_coeff: float, entropy_decay: float, min_entropy_coeff: float, entropy_decay_interval: int, initial_entropy_coeff: float, entropy_warmup_episodes: int, baseline_improvement_margin: float, baseline_eval_samples: int, top_k_ratio_start: float, top_k_ratio_end: float, top_k_ratio_decay: float, top_k_warmup_episodes: int, top_k_decay_interval: int, optimizer_type: str, enable_optimizer: bool, use_block_generator: bool, block_generator: Optional[OptimizedBlockGenerator], mode: str, self_label_samples: int, self_label_mode: int, violation_penalty_weight: float, longi_balance_weight: float) | - | Args: |
| _should_change_start_date | (self) | bool | 기준일 변경 여부 확인 (REINFORCE와 동일) |
| update_entropy_coeff | (self) | - | 🔥 엔트로피 계수 스케줄링 (점진적 감소) |
| update_sampling_top_k_ratio | (self) | - | [AGENT-EDIT] 샘플링 시 상위 후보 비율을 점진적으로 축소 |
| _evaluate_heuristic_baseline | (self, blocks: List, metadata: Dict, start_date: str, method: str, trials: int) | Tuple[float, List[Dict], Dict] | 설명 없음 |
| get_rollout_baseline | (self, blocks: List, metadata: Dict, start_date: str, return_details: bool) | - | 🔥 휴리스틱 Baseline(LPT)으로 성능 및 (옵션) 상세 정보를 반환 |
| train_episode | (self, blocks: List, metadata: Dict) | Dict | 모드에 따라 PPO 또는 Self-Label 에피소드 학습 |
| _maybe_freeze_norm_stats | (self) | None | 설명 없음 |
| train_episode_ppo | (self, blocks: List, metadata: Dict) | Dict | PPO 방식으로 한 에피소드 학습 (기준일 관리 포함) |
| train_episode_self_label | (self, blocks: List, metadata: Dict) | Dict | Self-label 방식으로 한 에피소드 학습 |
| _sample_schedule | (self, model, blocks: List, metadata: Dict, start_date: str, training_mode: bool, tag: str, forced_sequence: Optional[List[int]]) | - | 모델로 한 번 스케줄을 샘플링하여 기록을 반환. |
| _select_best_record | (self, records: List[Dict]) | Dict | self_label_mode 기준으로 best-of-K 샘플 선택. |
| _teacher_forcing_update | (self, episode_data: List[Dict]) | float | 선택된 최적 시퀀스를 그대로 따라가도록 NLL 기반 학습. |
| _build_episode_diagnostics | (self, actor_env, actor_schedule: Optional[List[Dict]], actor_stats: Dict, baseline_details: Optional[Dict], baseline_makespan: float) | Dict | 로그용 진단 정보 구성 |
| _extract_sequence | (self, env, schedule_results: Optional[List[Dict]]) | List[int] | 환경/결과에서 실행된 시퀀스 추출 |
| _summarize_schedule_results | (self, schedule_results: Optional[List[Dict]]) | Tuple[List[Tuple[str, float]], List[Dict]] | 일별 makespan 및 제약 위반 요약 |
| _normalize_date_label | (raw_date: Optional[str]) | Tuple[Optional[str], Optional[object]] | 설명 없음 |
| _ppo_update | (self, episode_data: List[Dict], global_advantage: float) | Tuple[float, float] | PPO 업데이트 수행 |
| should_update_baseline | (self, test_blocks: List[List], test_metadata: List[Dict], num_tests: int, update_csv_path: str) | bool | 통계적 검정으로 baseline 업데이트 여부 결정 |
| _compute_episode_start_date | (self, blocks: List) | str | 블록 데이터의 최소 조립 착수일을 기반으로 기준일 계산 |
| compute_start_date | (self, blocks: List) | str | 외부 호출용 기준일 계산 |
| _generate_random_start_date | (self, year: int) | str | 랜덤 시작 날짜 생성 |
| save_model | (self, path: str) | - | 모델 저장 |
| load_model | (self, path: str) | - | 모델 로드 |

#### 함수 없음

### 파일: `data_utils.py`

- 모듈 설명: 학습 데이터 생성/스케일링 유틸 (runner.py에서 분리).

#### 클래스 없음

#### 함수 목록

| 함수 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _sample_util_bucket | (buckets: List[Dict[str, object]]) | Tuple[str, float, Tuple[float, float]] | 설명 없음 |
| _compute_spread_days | (total_blocks: int, util: float, max_daily_blocks: int, min_days: int) | int | 설명 없음 |
| _adjust_reserved_counts | (total_blocks: int, ps_pairs: int, sub_groups: int, min_basic_blocks: int) | Tuple[int, int] | 설명 없음 |
| _apply_generated_data_variant | (df: pd.DataFrame, seam_scale: float, tact_time_scale: float, length_scale: float, width_scale: float, thickness_scale: float) | pd.DataFrame | Apply scaling for seam/processing and physical attributes. |

### 파일: `helpers.py`

- 모듈 설명: PPO 학습 러너 공통 유틸 (runner.py에서 분리).

#### 클래스 없음

#### 함수 목록

| 함수 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _format_sequence_preview | (sequence: List[int], limit: int) | str | 설명 없음 |
| _safe_value | (value: Optional[int]) | str | 설명 없음 |
| _compare_sequences | (actor_seq: List[int], baseline_seq: List[int], top_k: int) | Dict[str, Any] | 설명 없음 |
| _format_mismatch_summary | (mismatches: List[Dict[str, Any]]) | str | 설명 없음 |
| _format_daily_lines | (daily_stats: List[Any]) | List[str] | 설명 없음 |
| _format_violation_lines | (violations: List[Dict[str, Any]], limit: int) | List[str] | 설명 없음 |
| set_seed | (seed: int) | None | 재현성을 위한 시드 설정 |

### 파일: `rollout_metrics.py`

- 모듈 설명: Rollout 스코어/통계 유틸 (assembly_rollout.py에서 분리).

#### 클래스 없음

#### 함수 목록

| 함수 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| calculate_entropy | (log_probs: torch.Tensor) | torch.Tensor | 🔥 엔트로피 계산: H(π) = -Σ π(a|s) * log π(a|s) |
| get_violation_count | (stats: Optional[Dict]) | int | 설명 없음 |
| compute_longi_balance_details | (schedule_results: Optional[List[Dict]]) | Tuple[float, int, int, int] | 베이별 론지 불균형 비율과 합계 (ratio, bay_a, bay_b, total). |
| compute_longi_balance_ratio | (schedule_results: Optional[List[Dict]]) | float | 설명 없음 |
| compute_score | (makespan_hours: float, violations: int, longi_balance: float, violation_penalty_weight: float, longi_balance_weight: float) | float | 설명 없음 |

### 파일: `runner.py`

- 모듈 설명: PPO with Rollout Baseline 학습 스크립트

#### 클래스 없음

#### 함수 목록

| 함수 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| get_actor_params | (args) | - | Actor 파라미터 설정 (args에서 가져오기) |
| _extract_assembly_date_range | (df: Optional[pd.DataFrame]) | Tuple[Optional[str], Optional[str]] | [AGENT-ADD] Extract min/max assembly date tokens for distribution logging. |
| _resolve_excel_path | (path_str: Optional[str]) | Path | [AGENT-ADD] Resolve excel path relative to repo root. |
| generate_test_datasets | (generator, num_datasets, use_snu) | - | 테스트용 데이터셋 미리 생성 (평가용이므로 SNU 데이터 사용) |
| generate_baseline_test_datasets | (generator) | - | 🔥 Baseline 검정용 3문제 데이터셋 생성 |
| main | (args) | - | 설명 없음 |

<!-- /AUTO-GENERATED -->
