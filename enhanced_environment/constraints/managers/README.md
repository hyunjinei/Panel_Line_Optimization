# enhanced_environment/constraints/managers 핸드북 (상세)

제약 상태를 누적 관리하는 매니저 모음입니다. 용량/캘린더/P-S/베이 상태를 관리합니다.

---

## 흐름(요약)
```
ConstraintConfig
  └─ Manager 상태
       └─ 마스킹/환경
```

## 핵심 개념
- CapacityManager는 일일 심수/블록 수 누적을 관리합니다.
- CalendarManager는 휴무/반일/점심을 처리합니다.
- PSBlockManager는 P/S 순서를 강제합니다.

## 파일별 상세
### `bay_manager.py`
- 역할: 베이 관련 규칙과 상태를 관리합니다.
- 입력: 현재 시간, 블록 정보, 베이 상태
- 출력: 위반 리스트 또는 상태 갱신
- 연결: masking과 validator에서 공통으로 사용됩니다.
- 주요 엔트리:
  - BayStateTracker (클래스): 핵심 로직을 수행합니다.

### `calendar_manager.py`
- 역할: 휴무/반일/점심 시간 등 캘린더 제약을 처리합니다.
- 입력: 현재 시간, 블록 정보, 베이 상태
- 출력: 위반 리스트 또는 상태 갱신
- 연결: masking과 validator에서 공통으로 사용됩니다.
- 주요 엔트리:
  - CalendarManager (클래스): 핵심 로직을 수행합니다.

### `capacity_manager.py`
- 역할: 일일 용량, 혹서기 용량 등을 검사합니다.
- 입력: 현재 시간, 블록 정보, 베이 상태
- 출력: 위반 리스트 또는 상태 갱신
- 연결: masking과 validator에서 공통으로 사용됩니다.
- 주요 엔트리:
  - CapacityTracker (클래스): 핵심 로직을 수행합니다.

### `ps_manager.py`
- 역할: P/S 쌍과 순서 규칙을 관리합니다.
- 입력: 현재 시간, 블록 정보, 베이 상태
- 출력: 위반 리스트 또는 상태 갱신
- 연결: masking과 validator에서 공통으로 사용됩니다.
- 주요 엔트리:
  - PSBlockManager (클래스): 핵심 로직을 수행합니다.

## 주요 클래스/함수 (요약)
| 이름 | 분류 |
|---|---|
| BayStateTracker | 클래스 |
| CalendarManager | 클래스 |
| CapacityTracker | 클래스 |
| PSBlockManager | 클래스 |

## 운영 팁
- 매니저는 env.reset 시점에 초기화되어야 합니다.

## 코드/API 상세 (자동 추출)
<!-- AUTO-GENERATED: DO NOT EDIT BELOW -->

### 파일: `__init__.py`

- 모듈 설명: Constraint managers (capacity, P/S, bay, calendar).

#### 클래스 없음

#### 함수 없음

### 파일: `bay_manager.py`

- 모듈 설명: Bay state tracking manager.

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| BayStateTracker | 없음 | P7#1,7,8,11: 베이 상태 추적 매니저 |

##### 클래스: `BayStateTracker` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| __init__ | (self, subassembly_info, ps_pair_info, bay_strategy, block_10_rule, dynamic_tracking, constraint_config) | - | 설명 없음 |
| _initialize_metadata_tracking | (self) | - | 메타데이터 기반 추적 시스템 초기화 |
| reset_consecutive_counters | (self) | - | 연속 배치·원자 히스토리만 초기화 (작업시간/누적부하는 유지). |
| _is_subassembly_atomic_unit | (self, block_id: int) | bool | 블록이 별판 원자 단위인지 확인 |
| _is_ps_atomic_unit | (self, block_id: int) | bool | 블록이 P/S 원자 단위인지 확인 (론지 < 7개) |
| _get_atomic_unit_count | (self, block_id: int) | int | 원자 단위의 연속성 카운트 값 반환 |
| _get_consecutive_count_without_current_block | (self, target_bay: BayType) | int | 현재 블록을 제외하고 연속성 계산 (Action Masking P7#7 중복 체크 해결용) |
| _get_consecutive_count_with_metadata | (self, target_bay: BayType) | int | 메타데이터 기반 정확한 연속성 계산 (P7#7용) |
| _record_atomic_assignment | (self, block_id: int, bay: BayType, processing_time: float) | - | 원자 단위 기반 할당 기록 |
| can_assign_bay | (self, block: EnhancedBlock, target_bay: BayType) | Tuple[bool, str] | 특정 베이 할당 가능 여부 확인 (모든 P7 제약조건 검증) |
| assign_bay | (self, block: EnhancedBlock, target_bay: BayType, processing_time: float) | List[ConstraintViolation] | 베이 할당 및 상태 업데이트 (물리적 제약 위반 기록 포함) |
| get_load_balance_score | (self) | float | 베이 간 부하 균형 점수 (0~1, 1이 완벽한 균형) |
| reset | (self) | - | 상태 리셋 |
| get_status | (self) | Dict | 현재 베이 상태 반환 |
| get_consecutive_count | (self, bay_type: BayType) | int | 특정 베이의 연속 배치 카운트 반환 (P7#7용) |
| get_main_plate_consecutive_count | (self, bay_type: BayType) | int | 특정 베이의 주판 Only 연속 카운트 반환 (P7#8용) |
| should_force_bay_for_block_10 | (self, block: EnhancedBlock) | bool | 10번 블록 처리 후 B베이 강제 할당 여부 (P7#11용) |
| get_required_bay_for_block_10 | (self) | Optional[BayType] | 10번 블록 관련 필수 베이 반환 (P7#11용) |

#### 함수 없음

### 파일: `calendar_manager.py`

- 모듈 설명: Calendar and shift manager.

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| CalendarManager | 없음 | P5#13,14,15,16: 달력 및 시간 관리 매니저 |

##### 클래스: `CalendarManager` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| __init__ | (self, daily_structure, calendar_overrides: Optional[Dict]) | - | 설명 없음 |
| is_holiday | (self, target_date: datetime) | bool | 해당 날짜가 휴일인지 확인 (holidays 라이브러리 사용) |
| is_closed_day | (self, target_date: datetime) | bool | 공장 휴무일 여부 |
| _is_closed_by_full_day | (self, target_time: datetime) | bool | 휴무일 08:00~익일 07:59 범위 |
| _is_closed_by_afternoon_shutdown | (self, target_time: datetime) | bool | 오후 가동 중지 (15:00~익일 07:59) |
| _is_closed_by_morning_shutdown | (self, target_time: datetime) | bool | 오전 가동 중지 (기본: 08:00~12:00) |
| _is_closed_by_lunch_break | (self, target_time: datetime) | bool | 설명 없음 |
| is_closed_time | (self, target_time: datetime) | bool | 공장 중지 시간 여부 (휴무일/부분중지/점심시간) |
| next_open_time | (self, target_time: datetime) | datetime | 다음 가동 가능 시각 반환 (하드코딩 규칙 반영) |
| _time_in_range | (self, t: time, start: time, end: time) | bool | 설명 없음 |
| _get_morning_shutdown_range | (self, target_date: date) | Tuple[time, time] | 설명 없음 |
| _get_afternoon_shutdown_range | (self, target_date: date) | Tuple[time, time] | 설명 없음 |
| _parse_time | (self, value: Optional[str]) | Optional[time] | 설명 없음 |
| _parse_time_range | (self, raw: str) | Optional[Tuple[time, time]] | 설명 없음 |
| is_holiday_eve | (self, target_date: datetime) | bool | 해당 날짜가 명절 전날인지 확인 (P5#15) - 설날, 추석 전날만 해당 |
| is_hot_season | (self, target_date: datetime) | bool | 해당 날짜가 혹서기인지 확인 (P5#16) - 6-8월 |
| has_night_work | (self, target_date: datetime) | bool | 해당 날짜에 야간 작업이 가능한지 확인 (P5#15) |
| get_capacity_factor | (self, target_date: datetime) | float | 해당 날짜의 용량 배수 반환 (P5#16) |
| add_holiday | (self, holiday_date: date) | - | 새로운 휴일 추가 (holidays 라이브러리 사용 중이므로 수동 추가 불가) |
| is_weekend | (self, target_date: datetime) | bool | 주말 여부 확인 |
| is_sunday | (self, target_date: datetime) | bool | 일요일 여부 확인 (P5#14) |
| get_shift_type | (self, current_time: datetime) | str | 현재 시간의 근무 시간대 반환 (현직자 정보 반영) |
| get_status | (self, date: datetime) | Dict | 특정 날짜의 달력 상태 반환 |

#### 함수 없음

### 파일: `capacity_manager.py`

- 모듈 설명: Capacity tracking for daily seam constraints.

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| CapacityTracker | 없음 | P5#8,9,10,P5#15,16: 용량 관리 매니저 (심수 기준으로 단순화) |

##### 클래스: `CapacityTracker` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _seam_load | (block: EnhancedBlock) | int | Compute combined SEAM and C/SEAM load for capacity checks. |
| __init__ | (self, daily_structure, fab_interval_tracking, constraint_config, capacity_hyperparams) | - | 설명 없음 |
| _resolve_daily_seam_override | (self, current_time: Optional[datetime]) | Optional[int] | 특정 날짜 심수 용량 오버라이드 조회 (YYYYMMDD 기반). |
| _resolve_daily_seam_scale | (self, current_time: Optional[datetime]) | Optional[float] | 특정 날짜 심수 배율 오버라이드 조회 (YYYYMMDD 기반). |
| get_capacity_limits | (self, is_weekend: bool, is_hot_season: bool, is_holiday_eve: bool, current_time: Optional[datetime]) | int | 현재 심수 용량 한계 반환 (하이퍼파라미터 기반) |
| get_capacity_used | (self, is_weekend: bool) | int | 현재 사용된 심수 반환 |
| get_block_count | (self, is_weekend: bool) | int | 현재 처리된 블록 수 반환 (P5#9용) |
| can_add_block | (self, block: EnhancedBlock, is_weekend: bool, is_hot_season: bool, is_holiday_eve: bool, current_time: Optional[datetime]) | Tuple[bool, str] | 블록 추가 가능 여부 확인 (심수 + P5#9 블록 수 체크) - 하이퍼파라미터 기반 |
| add_block | (self, block: EnhancedBlock, is_weekend: bool, is_hot_season: bool, is_holiday_eve: bool, current_time: Optional[datetime]) | List[ConstraintViolation] | 블록을 용량에 추가 및 제약조건 위반 검증 (심수만) - 하이퍼파라미터 기반 |
| check_calendar_constraints | (self, block: EnhancedBlock, current_time: datetime, calendar_manager: 'CalendarManager') | List[ConstraintViolation] | 달력 기반 제약조건 검증 (하이퍼파라미터 기반) |
| reset_daily | (self) | - | 일일 용량 리셋 (심수 + 블록 수) |
| reset_weekend | (self) | - | 주말 용량 리셋 (심수 + 블록 수) |
| get_status | (self, is_weekend: bool, is_hot_season: bool, is_holiday_eve: bool, current_time: Optional[datetime]) | Dict | 현재 용량 상태 반환 (심수 + 블록 수 + 하이퍼파라미터 포함) |

#### 함수 없음

### 파일: `ps_manager.py`

- 모듈 설명: P/S pairing manager.

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| PSBlockManager | 없음 | P5#3,4,P7#3,4: P/S 블록 쌍 관리 매니저 (PFSP 방식) |

##### 클래스: `PSBlockManager` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| __init__ | (self, ps_complete_info, subassembly_info) | - | 설명 없음 |
| _process_ps_complete_info | (self) | - | 완전한 P/S 정보를 내부 구조로 변환 |
| _process_subassembly_ps_info | (self) | - | 별판 중 P/S 쌍 정보 처리 |
| is_ps_pair_from_metadata | (self, block_id: int) | bool | 메타데이터 기반 P/S 쌍 확인 |
| get_ps_pair_requirements | (self, port_id: int, starboard_id: int) | Dict | P/S 쌍 요구사항 반환 |
| is_subassembly_ps_pair | (self, unified_block_id: int) | bool | 통합 블록이 별판 P/S 쌍인지 확인 |
| is_port_block | (self, block_id: int) | bool | 블록이 P(Port) 블록인지 확인 |
| get_starboard_for_port | (self, port_block_id: int) | Optional[int] | P 블록에 대응하는 S 블록 ID 반환 |
| is_ps_pair_continuous_required | (self, port_block_id: int) | bool | P/S 쌍이 연속성을 요구하는지 확인 |
| set_block_sequence | (self, sequence: List[int]) | - | PFSP 블록 순서 설정 |
| register_ps_pair | (self, port_block: EnhancedBlock, starboard_block: EnhancedBlock) | - | P/S 블록 쌍 등록 |
| can_process_block_in_sequence | (self, block: EnhancedBlock, current_position: int) | Tuple[bool, str] | PFSP 순서에서 블록 처리 가능 여부 확인 (대폭 단순화) |
| process_block | (self, block: EnhancedBlock, assigned_bay: BayType, current_time: datetime) | List[ConstraintViolation] | 블록 처리 및 상태 업데이트 (PFSP 방식) + 실시간 제약조건 검증 |
| _check_assembly_mixing_constraints | (self, block: EnhancedBlock, current_time: datetime) | List[ConstraintViolation] | P5#11,12: 혼합 배정 제약 실시간 검증 |
| _check_ps_continuity_constraints | (self, block: EnhancedBlock) | List[ConstraintViolation] | P5#3,4: P/S 연속성 제약 실시간 검증 |
| get_required_bay_for_starboard | (self, starboard_block: EnhancedBlock) | Optional[BayType] | Starboard 블록에 필요한 베이 반환 (P7#3,4 강화) |
| _is_ps_pair_requires_continuous_sending | (self, starboard_block: EnhancedBlock) | bool | P/S 쌍이 연속송선을 요구하는지 확인 |
| _get_preferred_bay_for_ps_pair | (self, starboard_block: EnhancedBlock) | Optional[BayType] | P/S 쌍에 대한 선호 베이 결정 |
| get_next_required_block | (self) | Optional[int] | PFSP 순서에서 다음에 처리해야 할 필수 블록 반환 |
| _is_block_completed | (self, block_id: int) | bool | 블록 완료 여부 확인 |
| reset | (self) | - | 상태 리셋 |
| get_status | (self) | Dict | 현재 P/S 관리 상태 반환 |

#### 함수 없음

<!-- /AUTO-GENERATED -->
