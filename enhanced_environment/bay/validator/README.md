# enhanced_environment/bay/validator 핸드북 (상세)

사후 제약 검증을 수행하는 폴더입니다. 최종 스케줄을 기준으로 제약 위반을 다시 계산합니다.

---

## 흐름(요약)
```
schedule
  └─ 제약 재검증
       └─ 위반 집계
            └─ 리포트
```

## 핵심 개념
- 사후 검증은 결과 품질 평가에만 사용합니다.

## 파일별 상세
### `assembly.py`
- 역할: 보조 기능을 모아 둔 파일입니다.
- 입력: 스케줄 결과, 베이 배정 정보
- 출력: ConstraintViolation 리스트
- 연결: 마스킹 결과와 validator 결과가 다를 수 있습니다.
- 주요 엔트리:
  - AssemblyValidationMixin (클래스): 핵심 로직을 수행합니다.

### `bay_rules.py`
- 역할: 베이 관련 규칙과 상태를 관리합니다.
- 입력: 스케줄 결과, 베이 배정 정보
- 출력: ConstraintViolation 리스트
- 연결: 마스킹 결과와 validator 결과가 다를 수 있습니다.
- 주요 엔트리:
  - BayValidationMixin (클래스): 핵심 로직을 수행합니다.

### `core.py`
- 역할: 해당 폴더의 중심 로직입니다.
- 입력: 스케줄 결과, 베이 배정 정보
- 출력: ConstraintViolation 리스트
- 연결: 마스킹 결과와 validator 결과가 다를 수 있습니다.
- 주요 엔트리:
  - ConstraintValidatorCore (클래스): 핵심 로직을 수행합니다.

### `routing.py`
- 역할: C-Seam/곡판/고심수 등 간격 규칙을 검사합니다.
- 입력: 스케줄 결과, 베이 배정 정보
- 출력: ConstraintViolation 리스트
- 연결: 마스킹 결과와 validator 결과가 다를 수 있습니다.
- 주요 엔트리:
  - RoutingValidationMixin (클래스): 핵심 로직을 수행합니다.

### `saw.py`
- 역할: P6 시간 규칙과 오후 착수 제약을 검사합니다.
- 입력: 스케줄 결과, 베이 배정 정보
- 출력: ConstraintViolation 리스트
- 연결: 마스킹 결과와 validator 결과가 다를 수 있습니다.
- 주요 엔트리:
  - SawValidationMixin (클래스): 핵심 로직을 수행합니다.

## 주요 클래스/함수 (요약)
| 이름 | 분류 |
|---|---|
| AssemblyValidationMixin | 클래스 |
| BayValidationMixin | 클래스 |
| ConstraintValidatorCore | 클래스 |
| RoutingValidationMixin | 클래스 |
| SawValidationMixin | 클래스 |

## 운영 팁
- dedup 기준을 명확히 해야 위반 집계가 일관됩니다.

## 코드/API 상세 (자동 추출)
<!-- AUTO-GENERATED: DO NOT EDIT BELOW -->

### 파일: `__init__.py`

- 모듈 설명: 설명 없음

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| ConstraintValidator | ConstraintValidatorCore, SawValidationMixin, AssemblyValidationMixin, RoutingValidationMixin, BayValidationMixin | Composed validator; behavior mirrors legacy constraint_validator.ConstraintValidator. |

##### 클래스: `ConstraintValidator` 메서드

메서드 없음

#### 함수 없음

### 파일: `assembly.py`

- 모듈 설명: 설명 없음

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| AssemblyValidationMixin | 없음 | 설명 없음 |

##### 클래스: `AssemblyValidationMixin` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| validate_cross_seam_realtime | (self, block: EnhancedBlock) | List[ConstraintViolation] | P6#4: Cross seam 블록 혼합 배치 제약 실시간 검증 (환경에서 재검증) |
| validate_subassembly_constraints | (self, block: EnhancedBlock) | List[ConstraintViolation] | P5#17: 별판 처리 제약 검증 |
| validate_ps_order_realtime | (self, block: EnhancedBlock) | List[ConstraintViolation] | P5#3,4: P/S 순서 제약 실시간 검증 |
| validate_mixed_assembly_realtime | (self, block: EnhancedBlock, current_time: datetime) | List[ConstraintViolation] | P5#11,12: 혼합 배정 제약 실시간 검증 |
| validate_material_ready_realtime | (self, block: EnhancedBlock) | List[ConstraintViolation] | P5#13: 자재 미입고 제약 실시간 검증 |
| validate_mixing_constraints_realtime | (self, block: EnhancedBlock) | List[ConstraintViolation] | P5#11,12: 혼합 배정 제약 실시간 검증 (단순화) |
| validate_material_constraints_realtime | (self, block: EnhancedBlock) | List[ConstraintViolation] | P5#13: 자재 미입고 제약 실시간 검증 |
| validate_ps_sequence_order | (self, sequence: List[int]) | List[ConstraintViolation] | P/S 순서 검증 (모든 순서 제약조건 포함) |
| validate_mixed_assembly_order | (self, sequence: List[int]) | List[ConstraintViolation] | P5#11,12: 혼합 배정 순서 검증 (원본 엄격한 버전으로 복원) |
| validate_cross_seam_placement_order | (self, sequence: List[int]) | List[ConstraintViolation] | P6#4: Cross seam 혼합 배치 순서 검증 (원본 엄격한 버전으로 복원) |

#### 함수 없음

### 파일: `bay_rules.py`

- 모듈 설명: 설명 없음

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| BayValidationMixin | 없음 | 설명 없음 |

##### 클래스: `BayValidationMixin` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| validate_p7_constraints_with_existing_functions | (self, block: EnhancedBlock, assigned_bay: BayType) | List[ConstraintViolation] | 기존 BayStateTracker 함수들을 활용한 P7 제약조건 검증 |
| validate_p7_constraints_action_masking | (self, block: EnhancedBlock, assigned_bay: BayType) | List[ConstraintViolation] | Action Masking용 P7 제약조건 검증 (P7#7 중복 해결) |
| validate_consecutive_constraints_realtime_action | (self, block: EnhancedBlock, assigned_bay: BayType) | List[ConstraintViolation] | P7#7: 연속 배치 제약 검증 (Action Masking용 - 중복 카운팅 방지) |
| validate_balance_constraints_realtime | (self, block: EnhancedBlock, assigned_bay: BayType) | List[ConstraintViolation] | P7#1: 부하 균형 제약 실시간 검증 |

#### 함수 없음

### 파일: `core.py`

- 모듈 설명: 설명 없음

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| ConstraintValidatorCore | 없음 | 설명 없음 |

##### 클래스: `ConstraintValidatorCore` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| __init__ | (self, capacity_tracker: CapacityTracker, calendar_manager: CalendarManager, bay_tracker: BayStateTracker, ps_manager: PSBlockManager, subassembly_complete_info: dict, cross_seam_mixing_control: dict, completed_steps: list, blocks_dict: dict) | - | 설명 없음 |
| set_environment | (self, env) | - | 환경 참조 주입 |
| _is_subassembly | (self, block_id: int) | bool | 블록이 별판인지 확인 (메타데이터 기반) |
| validate_all_constraints_realtime | (self, block: EnhancedBlock, assigned_bay: BayType, current_time: datetime) | List[ConstraintViolation] | 모든 판계 제약조건 실시간 검증 (완전한 모든 제약조건 포함) |
| validate_all_constraints_realtime_action | (self, block: EnhancedBlock, assigned_bay: BayType, current_time: datetime, actual_machine_2_start_time: datetime, capacity_time_override: Optional[datetime]) | List[ConstraintViolation] | Action Masking용 모든 제약조건 실시간 검증 (P7#7 중복 해결) |

#### 함수 없음

### 파일: `routing.py`

- 모듈 설명: 설명 없음

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| RoutingValidationMixin | 없음 | 설명 없음 |

##### 클래스: `RoutingValidationMixin` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _validate_routing_spacing_constraints | (self, block: EnhancedBlock) | List[ConstraintViolation] | C-Seam 간격, 곡판/고심수 간격, 작업장 순서 등 라우팅 제약을 검증. |

#### 함수 없음

### 파일: `saw.py`

- 모듈 설명: 설명 없음

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| SawValidationMixin | 없음 | 설명 없음 |

##### 클래스: `SawValidationMixin` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| validate_saw_constraints_realtime | (self, block: EnhancedBlock, current_time: datetime, actual_machine_2_start_time: datetime) | List[ConstraintViolation] | SAW 제약조건 실시간 검증 (실제 머신 스케줄 기반) |
| _calculate_actual_machine_2_start_time | (self, block: EnhancedBlock, current_time: datetime) | datetime | 실제 머신 2번 시작 시간 계산 (CT 테이블 기반) |
| _fallback_machine_2_time_calculation | (self, block: EnhancedBlock, current_time: datetime) | datetime | Fallback 머신 2번 시간 계산 |
| _get_afternoon_constraint_id | (self, block: EnhancedBlock) | str | 블록에 해당하는 오후 3시 제약조건 ID 반환 |

#### 함수 없음

<!-- /AUTO-GENERATED -->
