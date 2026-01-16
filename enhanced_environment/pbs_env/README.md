# enhanced_environment/pbs_env 핸드북 (상세)

PBS 환경의 핵심 로직입니다. reset/step, 관측 구성, 상태 갱신이 여기서 일어납니다.

---

## 흐름(요약)
```
reset()
  └─ 데이터/상태 초기화
step(action)
  └─ 마스킹/베이 배정
       └─ 상태 갱신
            └─ 관측/보상 반환
```

## 핵심 개념
- step은 상태 갱신과 위반 계산을 포함합니다.
- observation은 block 피처 + env_state로 구성됩니다.

## 파일별 상세
### `assembly_mode.py`
- 역할: 보조 기능을 모아 둔 파일입니다.
- 입력: 블록 목록, config, action
- 출력: observation, reward, done, info
- 연결: PPO 학습과 휴리스틱 경로 모두 이 환경을 사용합니다.
- 주요 엔트리:
  - AssemblyModeMixin (클래스): 핵심 로직을 수행합니다.

### `bay_ops.py`
- 역할: P/S 쌍과 순서 규칙을 관리합니다.
- 입력: 블록 목록, config, action
- 출력: observation, reward, done, info
- 연결: PPO 학습과 휴리스틱 경로 모두 이 환경을 사용합니다.
- 주요 엔트리:
  - BayOpsMixin (클래스): 핵심 로직을 수행합니다.

### `core.py`
- 역할: 해당 폴더의 중심 로직입니다.
- 입력: 블록 목록, config, action
- 출력: observation, reward, done, info
- 연결: PPO 학습과 휴리스틱 경로 모두 이 환경을 사용합니다.
- 주요 엔트리:
  - EnhancedPanelBlockShop (클래스): 핵심 로직을 수행합니다.

### `debug.py`
- 역할: 디버그 출력과 플래그 처리를 담당합니다.
- 입력: 블록 목록, config, action
- 출력: observation, reward, done, info
- 연결: PPO 학습과 휴리스틱 경로 모두 이 환경을 사용합니다.
- 주요 엔트리:
  - EnvDebugMixin (클래스): 핵심 로직을 수행합니다.

### `metadata.py`
- 역할: 보조 기능을 모아 둔 파일입니다.
- 입력: 블록 목록, config, action
- 출력: observation, reward, done, info
- 연결: PPO 학습과 휴리스틱 경로 모두 이 환경을 사용합니다.
- 주요 엔트리:
  - MetadataMixin (클래스): 핵심 로직을 수행합니다.

### `observation.py`
- 역할: 관측 벡터 생성과 정규화를 담당합니다.
- 입력: 블록 목록, config, action
- 출력: observation, reward, done, info
- 연결: PPO 학습과 휴리스틱 경로 모두 이 환경을 사용합니다.
- 주요 엔트리:
  - ObservationMixin (클래스): 핵심 로직을 수행합니다.

### `rendering.py`
- 역할: 보조 기능을 모아 둔 파일입니다.
- 입력: 블록 목록, config, action
- 출력: observation, reward, done, info
- 연결: PPO 학습과 휴리스틱 경로 모두 이 환경을 사용합니다.
- 주요 엔트리:
  - RenderingMixin (클래스): 핵심 로직을 수행합니다.

### `setup.py`
- 역할: 환경 초기화와 데이터 준비를 담당합니다.
- 입력: 블록 목록, config, action
- 출력: observation, reward, done, info
- 연결: PPO 학습과 휴리스틱 경로 모두 이 환경을 사용합니다.
- 주요 엔트리:
  - SetupMixin (클래스): 핵심 로직을 수행합니다.

### `step_logic.py`
- 역할: 보조 기능을 모아 둔 파일입니다.
- 입력: 블록 목록, config, action
- 출력: observation, reward, done, info
- 연결: PPO 학습과 휴리스틱 경로 모두 이 환경을 사용합니다.
- 주요 엔트리:
  - StepLogicMixin (클래스): 핵심 로직을 수행합니다.

### `validation.py`
- 역할: 입력값 검증과 안전한 변환을 담당합니다.
- 입력: 블록 목록, config, action
- 출력: observation, reward, done, info
- 연결: PPO 학습과 휴리스틱 경로 모두 이 환경을 사용합니다.
- 주요 엔트리:
  - ValidationRewardMixin (클래스): 핵심 로직을 수행합니다.

## 주요 클래스/함수 (요약)
| 이름 | 분류 |
|---|---|
| AssemblyModeMixin | 클래스 |
| BayOpsMixin | 클래스 |
| EnhancedPanelBlockShop | 클래스 |
| EnvDebugMixin | 클래스 |
| MetadataMixin | 클래스 |
| ObservationMixin | 클래스 |
| RenderingMixin | 클래스 |
| SetupMixin | 클래스 |
| StepLogicMixin | 클래스 |
| ValidationRewardMixin | 클래스 |

## 운영 팁
- step API 시그니처는 변경하지 않습니다.

## 코드/API 상세 (자동 추출)
<!-- AUTO-GENERATED: DO NOT EDIT BELOW -->

### 파일: `__init__.py`

- 모듈 설명: 설명 없음

#### 클래스 없음

#### 함수 없음

### 파일: `assembly_mode.py`

- 모듈 설명: Assembly decoding helpers

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| AssemblyModeMixin | 없음 | 설명 없음 |

##### 클래스: `AssemblyModeMixin` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _initialize_assembly_decoding_mode | (self) | None | Assembly Decoding 모드 기본 세팅 |
| _reset_assembly_state | (self) | None | Assembly Decoding 전용 상태 초기화 |
| _advance_assembly_day | (self, reason: str) | None | Assembly Decoding 날짜 전환 및 머신 상태 갱신 |
| get_available_actions_assembly | (self) | Tuple[List[int], List[ConstraintViolation], List[Dict]] | Assembly Decoding에서 선택 가능한 블록 목록 반환 (상태 내부 관리) |

#### 함수 없음

### 파일: `bay_ops.py`

- 모듈 설명: Bay assignment and makespan helpers

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| BayOpsMixin | 없음 | 설명 없음 |

##### 클래스: `BayOpsMixin` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _auto_assign_bay | (self, block: EnhancedBlock, return_analysis: bool) | - | 설명 없음 |
| _preview_assign_bay | (self, block: EnhancedBlock, return_analysis: bool) | - | 상태를 변경하지 않는 베이 배정 |
| calculate_makespan | (self, sequence: List[int], branch_assignments: Dict[int, BayType], previous_machine_state: Dict, afternoon_guard_blocks: Optional[Set[int]]) | Tuple[float, Dict] | 설명 없음 |

#### 함수 없음

### 파일: `core.py`

- 모듈 설명: 설명 없음

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| EnhancedPanelBlockShop | EnvDebugMixin, MetadataMixin, SetupMixin, AssemblyModeMixin, StepLogicMixin, BayOpsMixin, ValidationRewardMixin, ObservationMixin, RenderingMixin | PFSP 방식 패널 블록 샵 환경 |

##### 클래스: `EnhancedPanelBlockShop` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| __init__ | (self, blocks: List[EnhancedBlock], start_time: datetime, max_steps: int, logging_level: int, constraint_config: 'ConstraintConfig', enable_visualization: bool, metadata: Dict, decoding_mode: str, assembly_max_days: int, assembly_capacity_bypass: bool, assembly_update_state_on_capacity: bool, assembly_update_state_on_empty: bool, assembly_keep_bay_assignments_on_capacity: bool, calendar_overrides: Optional[Dict]) | - | PFSP 환경 초기화 |
| copy | (self) | - | 환경 복사 (reinforce_train.py의 copy.deepcopy 대체) |
| reset | (self) | np.ndarray | PFSP 환경 리셋 |

#### 함수 없음

### 파일: `debug.py`

- 모듈 설명: Environment debug helpers

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| EnvDebugMixin | 없음 | 설명 없음 |

##### 클래스: `EnvDebugMixin` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _resolve_debug_flag | (self, config: Optional['ConstraintConfig']) | bool | verbose 디버그 출력 여부를 결정한다. |

#### 함수 없음

### 파일: `metadata.py`

- 모듈 설명: Environment metadata helpers

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| MetadataMixin | 없음 | 설명 없음 |

##### 클래스: `MetadataMixin` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _process_metadata | (self) | - | 메타데이터를 내부 구조로 변환 및 저장 |
| _initialize_constraint_managers_with_metadata | (self) | - | 제약조건 매니저들을 메타데이터와 함께 초기화 |
| _is_subassembly | (self, block_id: int) | bool | 블록이 별판인지 확인 (메타데이터 기반) |
| _get_original_subassembly_blocks | (self, block_id: int) | List[int] | 별판의 원본 블록 ID들 반환 |
| _is_ps_pair_from_metadata | (self, block_id: int) | bool | 블록이 P/S 쌍인지 메타데이터에서 확인 |
| get_expanded_schedule_results | (self) | List[Dict] | 메타데이터를 활용하여 별판 자동 확장된 스케줄 결과 반환 |
| generate_final_schedule_with_metadata | (self, sequence_results: List[Dict]) | List[Dict] | 스케줄링 결과를 메타데이터 기반으로 최종 형태로 변환 |
| get_metadata_summary | (self) | Dict[str, Any] | 메타데이터 요약 정보 반환 |
| _register_ps_pairs | (self) | - | P/S 블록 쌍들을 PSBlockManager에 등록 |

#### 함수 없음

### 파일: `observation.py`

- 모듈 설명: Observation encoding helpers

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| ObservationMixin | 없음 | 설명 없음 |

##### 클래스: `ObservationMixin` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _get_observation | (self) | np.ndarray | 현재 관찰 상태 반환 |
| _encode_sequence_state | (self) | List[float] | 순서 상태를 특성 벡터로 인코딩 |
| _encode_capacity_state | (self) | List[float] | 용량 상태를 특성 벡터로 인코딩 (심수 기준으로 단순화) |
| _encode_bay_state | (self) | List[float] | 베이 상태를 특성 벡터로 인코딩 |
| _encode_block_state | (self) | List[float] | 블록 상태를 간소화하여 인코딩 |

#### 함수 없음

### 파일: `rendering.py`

- 모듈 설명: Rendering and info helpers

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| RenderingMixin | 없음 | 설명 없음 |

##### 클래스: `RenderingMixin` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| get_info | (self) | Dict[str, Any] | 환경 정보 반환 |
| render | (self, mode: str) | - | 환경 시각화 |
| _render_gantt_chart | (self) | - | 간소화된 간트 차트 출력 |
| close | (self) | - | 환경 정리 |

#### 함수 없음

### 파일: `setup.py`

- 모듈 설명: Environment setup helpers

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| SetupMixin | 없음 | 설명 없음 |

##### 클래스: `SetupMixin` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _setup_action_observation_spaces | (self) | - | PFSP용 액션/관찰 공간 설정 |
| _calculate_observation_dimension | (self) | int | 관찰 공간 차원 계산 |

#### 함수 없음

### 파일: `step_logic.py`

- 모듈 설명: Step handling logic

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| StepLogicMixin | 없음 | 설명 없음 |

##### 클래스: `StepLogicMixin` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| step | (self, action: int) | Tuple[np.ndarray, float, bool, Dict[str, Any]] | PFSP 액션 실행 - 단계별 처리 |
| _handle_sequence_decision | (self, action: int, step_info: Dict) | Tuple[np.ndarray, float, bool, Dict] | Step-by-Step 순서 결정 단계 처리 |
| _handle_assembly_decision | (self, action: int, step_info: Dict) | Tuple[np.ndarray, float, bool, Dict] | Assembly Decoding 전용 step 처리 |
| _handle_process_execution | (self, step_info: Dict) | Tuple[np.ndarray, float, bool, Dict] | 공정 실행 단계 처리 (자동 진행) |
| _handle_branch_selection | (self, action: int, step_info: Dict) | Tuple[np.ndarray, float, bool, Dict] | 분기 선택 단계 처리 - 환경 자동 베이 할당 방식 |
| _process_common_step | (self, block: EnhancedBlock, process_num: int, step_info: Dict) | Tuple[np.ndarray, float, bool, Dict] | Line 1 공정 단계 처리 |
| _process_branch_step | (self, block: EnhancedBlock, process_num: int, assigned_bay: BayType, step_info: Dict) | Tuple[np.ndarray, float, bool, Dict] | Line 2 공정 단계 처리 |
| _process_branch_step_with_auto_bay | (self, block: EnhancedBlock, process_num: int, step_info: Dict) | Tuple[np.ndarray, float, bool, Dict] | 분기 단계 처리 - 환경 자동 베이 할당 방식 + 모든 제약조건 실시간 검증 |

#### 함수 없음

### 파일: `validation.py`

- 모듈 설명: Validation and reward helpers

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| ValidationRewardMixin | 없음 | 설명 없음 |

##### 클래스: `ValidationRewardMixin` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _validate_all_constraints_realtime | (self, block: EnhancedBlock, assigned_bay: BayType, current_time: datetime) | List[ConstraintViolation] | 설명 없음 |
| _validate_all_constraints_realtime_action | (self, block: EnhancedBlock, assigned_bay: BayType, current_time: datetime, actual_machine_2_start_time: datetime, capacity_time_override: datetime) | List[ConstraintViolation] | 설명 없음 |
| _validate_saw_constraints_realtime | (self, block: EnhancedBlock, current_time: datetime, actual_machine_2_start_time: datetime) | List[ConstraintViolation] | 설명 없음 |
| _calculate_reward | (self, step_info: Dict, violations: List[ConstraintViolation]) | float | 보상 계산 (제약조건 위반 반영) |
| _check_completion | (self) | - | 완료 조건 확인 |

#### 함수 없음

<!-- /AUTO-GENERATED -->
