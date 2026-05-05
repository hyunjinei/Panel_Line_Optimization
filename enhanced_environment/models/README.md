# enhanced_environment/models 핸드북 (상세)

환경에서 사용하는 도메인 모델을 정의합니다. 블록, 상태, 위반, 결과 구조가 포함됩니다.

---

## 흐름(요약)
```
엑셀/생성 데이터
  └─ 모델 변환
       └─ 환경 상태
```

## 핵심 개념
- 모델 필드는 CSV/로그 스키마와 직결됩니다.

## 파일별 상세
### `block.py`
- 역할: 블록 모델과 속성 정의를 포함합니다.
- 입력: 엑셀 로우, 생성 블록
- 출력: EnhancedBlock, EnvironmentState 등
- 연결: 모든 경로에서 모델 구조를 사용합니다.
- 주요 엔트리:
  - EnhancedBlock (클래스): 핵심 로직을 수행합니다.

### `enums.py`
- 역할: 보조 기능을 모아 둔 파일입니다.
- 입력: 엑셀 로우, 생성 블록
- 출력: EnhancedBlock, EnvironmentState 등
- 연결: 모든 경로에서 모델 구조를 사용합니다.
- 주요 엔트리:
  - AssemblyType (클래스): 핵심 로직을 수행합니다.
  - PortStarboard (클래스): 핵심 로직을 수행합니다.
  - WorkshopType (클래스): 핵심 로직을 수행합니다.
  - BayType (클래스): 핵심 로직을 수행합니다.
  - MaterialType (클래스): 핵심 로직을 수행합니다.
  - ProcessPhase (클래스): 처리 로직을 수행합니다.

### `process.py`
- 역할: 공정 시간과 처리 단위를 정의합니다.
- 입력: 엑셀 로우, 생성 블록
- 출력: EnhancedBlock, EnvironmentState 등
- 연결: 모든 경로에서 모델 구조를 사용합니다.
- 주요 엔트리:
  - ProcessStep (클래스): 처리 로직을 수행합니다.

### `ps_pair.py`
- 역할: P/S 쌍과 순서 규칙을 관리합니다.
- 입력: 엑셀 로우, 생성 블록
- 출력: EnhancedBlock, EnvironmentState 등
- 연결: 모든 경로에서 모델 구조를 사용합니다.
- 주요 엔트리:
  - PSBlockPair (클래스): 핵심 로직을 수행합니다.

### `result.py`
- 역할: 스케줄 결과 구조를 정의합니다.
- 입력: 엑셀 로우, 생성 블록
- 출력: EnhancedBlock, EnvironmentState 등
- 연결: 모든 경로에서 모델 구조를 사용합니다.
- 주요 엔트리:
  - ActionResult (클래스): 핵심 로직을 수행합니다.

### `state.py`
- 역할: 상태 객체와 내부 상태 갱신 구조를 정의합니다.
- 입력: 엑셀 로우, 생성 블록
- 출력: EnhancedBlock, EnvironmentState 등
- 연결: 모든 경로에서 모델 구조를 사용합니다.
- 주요 엔트리:
  - EnvironmentState (클래스): 핵심 로직을 수행합니다.
  - SequenceState (클래스): 핵심 로직을 수행합니다.

### `violation.py`
- 역할: 위반 객체와 중복 제거, 집계를 담당합니다.
- 입력: 엑셀 로우, 생성 블록
- 출력: EnhancedBlock, EnvironmentState 등
- 연결: 모든 경로에서 모델 구조를 사용합니다.
- 주요 엔트리:
  - ConstraintViolation (클래스): 핵심 로직을 수행합니다.

## 주요 클래스/함수 (요약)
| 이름 | 분류 |
|---|---|
| EnhancedBlock | 클래스 |
| AssemblyType | 클래스 |
| PortStarboard | 클래스 |
| WorkshopType | 클래스 |
| BayType | 클래스 |
| MaterialType | 클래스 |
| ProcessPhase | 클래스 |
| ProcessStep | 클래스 |
| PSBlockPair | 클래스 |
| ActionResult | 클래스 |
| EnvironmentState | 클래스 |
| SequenceState | 클래스 |

## 운영 팁
- 필드 변경 시 csv_save와 평가 로직도 함께 확인합니다.

## 코드/API 상세 (자동 추출)
<!-- AUTO-GENERATED: DO NOT EDIT BELOW -->

### 파일: `__init__.py`

- 모듈 설명: Enhanced environment domain models (re-exported for convenience).

#### 클래스 없음

#### 함수 없음

### 파일: `block.py`

- 모듈 설명: EnhancedBlock definition (panel block domain model).

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| EnhancedBlock | 없음 | Extended block information used by constraints and scheduling. |

##### 클래스: `EnhancedBlock` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| __post_init__ | (self) | None | Post-init validation (kept identical). |
| is_p_s_pair | (self) | bool | Check if this block is a P/S pair. |
| needs_afternoon_start | (self) | bool | legacy 메서드. 논문 실험 기준 P6#1,2,3 제거로 항상 False 반환. |
| get_bay_constraint | (self) | BayType | Physical constraints are relaxed; keep AUTO. |
| get_physical_characteristics_key | (self) | tuple | Key for P5#17 physical identity. |
| is_physically_identical_to | (self, other: 'EnhancedBlock') | bool | P5#17: physical identity check. |
| is_ps_small_pair | (self, blocks_dict: Optional[Dict[int, 'EnhancedBlock']]) | bool | P/S small pair check (both longi < 7). |
| will_force_bay_b | (self, blocks_dict: Optional[Dict[int, 'EnhancedBlock']]) | bool | Check if this block forces Bay 36B (for 3-bay prevention). |

#### 함수 없음

### 파일: `enums.py`

- 모듈 설명: Enum definitions used across the scheduling environment.

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| AssemblyType | Enum | Assembly type. |
| PortStarboard | Enum | Port/Starboard type. |
| WorkshopType | Enum | Workshop type. |
| BayType | Enum | Bay type. |
| MaterialType | Enum | Material type. |
| ProcessPhase | Enum | PFSP process phase. |

##### 클래스: `AssemblyType` 메서드

메서드 없음

##### 클래스: `PortStarboard` 메서드

메서드 없음

##### 클래스: `WorkshopType` 메서드

메서드 없음

##### 클래스: `BayType` 메서드

메서드 없음

##### 클래스: `MaterialType` 메서드

메서드 없음

##### 클래스: `ProcessPhase` 메서드

메서드 없음

#### 함수 없음

### 파일: `process.py`

- 모듈 설명: Process step data class.

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| ProcessStep | 없음 | Single process step (for makespan reconstruction). |

##### 클래스: `ProcessStep` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| get_earliest_start_time | (self) | float | Earliest start time computed from predecessors. |

#### 함수 없음

### 파일: `ps_pair.py`

- 모듈 설명: P/S pair data class.

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| PSBlockPair | 없음 | P/S block pair info. |

##### 클래스: `PSBlockPair` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| needs_same_bay | (self) | bool | P7#3,4: same bay required for small pairs. |
| is_continuous_required | (self) | bool | Check if continuous placement is required. |

#### 함수 없음

### 파일: `result.py`

- 모듈 설명: Action result data class.

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| ActionResult | 없음 | Result for a single action/decision. |

##### 클래스: `ActionResult` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| add_violation | (self, constraint_id: str, message: str, severity: str) | None | Append a constraint violation. |

#### 함수 없음

### 파일: `state.py`

- 모듈 설명: Environment and sequence state data classes.

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| EnvironmentState | 없음 | Environment state snapshot (capacity, bay, P/S, etc.). |
| SequenceState | 없음 | PFSP sequence and step-by-step state. |

##### 클래스: `EnvironmentState` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| get_daily_capacity_limit | (self) | int | Daily seam capacity limit. |
| get_daily_capacity_used | (self) | int | Current seam usage. |
| can_add_capacity | (self, seam_count: int) | bool | Check if seam capacity can accept new load. |

##### 클래스: `SequenceState` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| advance_to_next_step | (self) | None | Move to next process or block. |
| get_next_block_to_process | (self) | Optional[int] | Return next block id to process. |
| is_branch_point | (self) | bool | True if current process is the branch point. |
| is_completed | (self) | bool | True if all steps are done. |
| get_progress_ratio | (self) | float | Progress ratio in [0, 1]. |
| get_progress_info | (self) | Dict[str, Any] | Progress info dict for logging/debugging. |

#### 함수 없음

### 파일: `violation.py`

- 모듈 설명: Constraint violation data class.

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| ConstraintViolation | 없음 | Constraint violation record. |

##### 클래스: `ConstraintViolation` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| __str__ | (self) | str | 설명 없음 |

#### 함수 없음

<!-- /AUTO-GENERATED -->
