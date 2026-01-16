# scheduling/assembly_start 핸드북 (상세)

조립착수일 기준 스케줄링과 RL 실행이 있는 폴더입니다. 강한 마스킹 기반 경로의 중심입니다.

---

## 흐름(요약)
```
blocks
  └─ 조립착수일 마스킹
       └─ 선택 규칙
            └─ 결과 저장
```

## 핵심 개념
- RL 경로는 best-of-k 샘플링을 사용합니다.

## 파일별 상세
### `action_sequence_조립착수일기준휴리스틱.py`
- 역할: 보조 기능을 모아 둔 파일입니다.
- 입력: 블록 목록, RL 모델
- 출력: 스케줄 결과, 상세 CSV
- 연결: rl_assembly_scheduler.py가 RL 경로 중심입니다.
- 주요 엔트리:
  - save_detailed_process_schedule (함수): 처리 로직을 수행합니다.
  - save_detailed_process_schedule_assembly (함수): 처리 로직을 수행합니다.
  - get_test_settings (함수): 상태/정보를 조회합니다.
  - create_block_result (함수): 핵심 로직을 수행합니다.
  - run_assembly_decoding_sequence (함수): 실행 진입점 또는 루프를 수행합니다.
  - run_assembly_decoding_sequence_with_blocks (함수): 휴리스틱/조립착수일 경로의 스케줄링 루프를 실행합니다.

### `rl_assembly_scheduler.py`
- 역할: 보조 기능을 모아 둔 파일입니다.
- 입력: 블록 목록, RL 모델
- 출력: 스케줄 결과, 상세 CSV
- 연결: rl_assembly_scheduler.py가 RL 경로 중심입니다.
- 주요 엔트리:
  - RLAssemblyScheduler (클래스): 핵심 로직을 수행합니다.
  - run_rl_assembly_decoding_sequence_with_blocks (함수): RL 경로의 스케줄링 루프를 실행합니다.

## 주요 클래스/함수 (요약)
| 이름 | 분류 |
|---|---|
| save_detailed_process_schedule | 함수 |
| save_detailed_process_schedule_assembly | 함수 |
| get_test_settings | 함수 |
| create_block_result | 함수 |
| run_assembly_decoding_sequence | 함수 |
| run_assembly_decoding_sequence_with_blocks | 함수 |
| save_assembly_results | 함수 |
| RLAssemblyScheduler | 클래스 |
| run_rl_assembly_decoding_sequence_with_blocks | 함수 |

## 운영 팁
- 디버그 로그는 PBS_RL_STEP_LOG로 제어합니다.

## 코드/API 상세 (자동 추출)
<!-- AUTO-GENERATED: DO NOT EDIT BELOW -->

### 파일: `__init__.py`

- 모듈 설명: Assembly-start scheduling methods.

#### 클래스 없음

#### 함수 없음

### 파일: `action_sequence_조립착수일기준휴리스틱.py`

- 모듈 설명: 설명 없음

#### 클래스 없음

#### 함수 목록

| 함수 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _get_capacity_load | (block) | int | Return SEAM+C/SEAM count used for capacity decisions. |
| save_detailed_process_schedule | (date_key: str, sequence: List[int], bay_assignments: Dict, ct_tables: Dict, blocks_dict: Dict, date_start_time: datetime) | - | 공정별 상세 스케줄링 CSV 저장 (공통 유틸 위임). |
| save_detailed_process_schedule_assembly | (date_key: str, sequence: List[int], bay_assignments: Dict, ct_tables: Dict, blocks_dict: Dict, date_start_time: datetime) | - | 공정별 상세 스케줄링 CSV 저장 (Assembly Decoding 방식). |
| get_test_settings | () | - | test.py 설정 가져오기 (circular import 방지) |
| create_block_result | (block, assigned_bay, sequence, bay_analysis, violations, date_str, start_time, end_time, makespan_minutes, makespan_hours, total_completion_time, date_start_time, actual_machine_2_start_time) | - | 블록 결과 생성 (action_sequence_오토베이수정.py와 동일한 형식) |
| run_assembly_decoding_sequence | (excel_path: str, decoding_type: str, selection_method: str, max_days: int, start_date: str, date_offset: int, output_csv: str, save_csv: bool, save_detailed: bool) | Tuple[List[Dict], Dict] | Assembly Decoding을 사용한 시퀀싱 실행 - 전체 블록 풀에서 하나씩 선택 |
| run_assembly_decoding_sequence_with_blocks | (blocks: List, metadata: Dict, decoding_type: str, selection_method: str, max_days: int, start_date: str, date_offset: int, output_csv: str, save_csv: bool, save_detailed: bool, forced_sequence: List[int], expand_rows: bool) | Tuple[List[Dict], Dict] | Assembly Decoding (메모리 데이터 직접 사용) |
| _run_assembly_decoding_core | (blocks: List, metadata: Dict, constraint_config, decoding_type: str, selection_method: str, max_days: int, start_date: str, date_offset: int, output_csv: str, save_csv: bool, save_detailed: bool, use_env_step: bool, expand_rows: bool) | Tuple[List[Dict], Dict] | Assembly Decoding 시퀀싱의 핵심 로직을 분리하여 재사용 |
| _evaluate_forced_sequence | (blocks: List, metadata: Dict, forced_sequence: List[int], decoding_type: str, max_days: int, start_date: str, date_offset: int, save_csv: bool, save_detailed: bool) | Tuple[List[Dict], Dict] | 강화학습에서 생성된 시퀀스를 강제로 적용하여 평가 |
| save_assembly_results | (results: List[Dict], statistics: Dict, output_path: str) | - | Assembly Decoding 결과를 CSV로 저장 |

### 파일: `rl_assembly_scheduler.py`

- 모듈 설명: 설명 없음

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| RLAssemblyScheduler | 없음 | RL Agent를 사용한 조립 스케줄링 (단순화) |

##### 클래스: `RLAssemblyScheduler` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| __init__ | (self, rl_agent, device) | - | 설명 없음 |
| _normalize_line_key | (self, value) | str | 설명 없음 |
| _normalize_workshop_key | (self, value) | str | 설명 없음 |
| _compute_bay_balance_delta | (self) | float | 35A-36B 누적 편차를 -1~1 범위로 정규화. |
| _safe_ratio | (value: float, base: float) | float | 설명 없음 |
| _compute_ps_pair_ratio | (self, blocks_dict: Dict[int, object], selected_block_ids: List[int]) | float | 설명 없음 |
| _compute_capacity_snapshot | (self, env, current_time: datetime) | Dict[str, float] | 현재 용량 사용률 정보 요약. |
| _compute_constraint_debt | (self, env) | Tuple[float, float] | 최근 위반 내역을 기반으로 경고/오류 비율 계산. |
| _classify_line_flags | (self, block) | Tuple[float, float] | 설명 없음 |
| _compute_deadline_urgency | (slack_days: float) | float | 설명 없음 |
| _resolve_line_root | (self, block) | Optional[str] | 설명 없음 |
| _resolve_fixed_code | (self, block) | Optional[str] | 설명 없음 |
| _compute_branch_counts | (self, blocks_dict: Dict[int, object], selected_block_ids: List[int]) | Dict[str, int] | 설명 없음 |
| _compute_backlogs | (self, blocks_dict: Dict[int, object], selected_block_ids: List[int]) | Tuple[Dict[str, int], Dict[str, int]] | 설명 없음 |
| _compute_slack_days | (self, block, current_time: datetime) | float | 설명 없음 |
| _encode_masking_stage | (self, stage_label: Optional[str]) | float | 설명 없음 |
| _update_branch_usage | (self, block, assigned_bay: Optional[BayType]) | - | 설명 없음 |
| _extract_environment_state | (self, env, selected_blocks: List[int], blocks_dict: Dict, block_analysis: Optional[List[Dict]]) | List[float] | 환경 상태 벡터 추출 (ENV_STATE_DIM 차원) |
| _rl_block_selection | (self, available_blocks: List, blocks_dict: Dict, selected_blocks: List[int], current_time, env, training_mode: bool, env_state_vector: Optional[List[float]], analysis_map: Optional[Dict[int, Dict]], forced_block_id: Optional[int]) | Tuple[int, float, torch.Tensor] | hope.txt 방식: 통합 상태 벡터를 사용한 RL 블록 선택 |
| _get_current_makespan | (self, env, remaining_blocks: List) | float | 현재 시점에서의 실제 makespan 계산 |

#### 함수 목록

| 함수 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _finalize_rl_results | (schedule_results: List[Dict], all_violations: List[ConstraintViolation], env: EnhancedPanelBlockShop, blocks: List, rl_scheduler: RLAssemblyScheduler, training_mode: bool, afternoon_guard_blocks_all: Set[int]) | Tuple[List[Dict], Dict, List[Dict]] | 설명 없음 |
| run_rl_assembly_decoding_sequence_with_blocks | (blocks: List, metadata: Dict, rl_agent, device: torch.device, decoding_type: str, max_days: int, start_date: str, date_offset: int, output_csv: str, save_csv: bool, save_detailed: bool, training_mode: bool, use_env_step: bool, forced_sequence: Optional[List[int]]) | Tuple[List[Dict], Dict, List[Dict], 'EnhancedPanelBlockShop'] | RL Agent를 사용한 Assembly Decoding 실행 |

<!-- /AUTO-GENERATED -->
