# enhanced_environment/masking 핸드북 (상세)

액션 마스킹 로직입니다. 후보 필터링, 완화, 최후 선택의 전체 흐름을 담당합니다.

---

## 흐름(요약)
```
blocks
  └─ 레이어 분류
       └─ 작업장 헤드 + window
            └─ 기본 제약 검사
                 └─ 완화 순서 적용
                      └─ 최후 선택
```

## 핵심 개념
- emergency/urgent/normal 레이어가 후보 폭을 결정합니다.
- 완화 순서는 config에서 동적으로 제어합니다.
- strict_rules는 절대 완화되지 않습니다.

## 파일별 상세
### `capacity.py`
- 역할: 일일 용량, 혹서기 용량 등을 검사합니다.
- 입력: 남은 블록, 현재 시간, 제약 설정
- 출력: 후보 id 리스트, 위반 상세
- 연결: env.get_available_actions_assembly에서 호출됩니다.
- 주요 엔트리:
  - CapacityMixin (클래스): 핵심 로직을 수행합니다.

### `core.py`
- 역할: 해당 폴더의 중심 로직입니다.
- 입력: 남은 블록, 현재 시간, 제약 설정
- 출력: 후보 id 리스트, 위반 상세
- 연결: env.get_available_actions_assembly에서 호출됩니다.
- 주요 엔트리:
  - ConstraintChecker (클래스): 핵심 로직을 수행합니다.

### `debug.py`
- 역할: 디버그 출력과 플래그 처리를 담당합니다.
- 입력: 남은 블록, 현재 시간, 제약 설정
- 출력: 후보 id 리스트, 위반 상세
- 연결: env.get_available_actions_assembly에서 호출됩니다.
- 주요 엔트리:
  - DebugMixin (클래스): 핵심 로직을 수행합니다.

### `ps_mixing.py`
- 역할: P/S 쌍과 순서 규칙을 관리합니다.
- 입력: 남은 블록, 현재 시간, 제약 설정
- 출력: 후보 id 리스트, 위반 상세
- 연결: env.get_available_actions_assembly에서 호출됩니다.
- 주요 엔트리:
  - PSMixingMixin (클래스): 핵심 로직을 수행합니다.

### `routing.py`
- 역할: C-Seam/곡판/고심수 등 간격 규칙을 검사합니다.
- 입력: 남은 블록, 현재 시간, 제약 설정
- 출력: 후보 id 리스트, 위반 상세
- 연결: env.get_available_actions_assembly에서 호출됩니다.
- 주요 엔트리:
  - RoutingMixin (클래스): 핵심 로직을 수행합니다.

### `saw_time.py`
- 역할: 날짜/시간 계산과 창(window) 판정을 담당합니다.
- 입력: 남은 블록, 현재 시간, 제약 설정
- 출력: 후보 id 리스트, 위반 상세
- 연결: env.get_available_actions_assembly에서 호출됩니다.
- 주요 엔트리:
  - SawTimeMixin (클래스): 핵심 로직을 수행합니다.

### `scoring.py`
- 역할: 최후 선택의 위반 점수 계산을 담당합니다.
- 입력: 남은 블록, 현재 시간, 제약 설정
- 출력: 후보 id 리스트, 위반 상세
- 연결: env.get_available_actions_assembly에서 호출됩니다.
- 주요 엔트리:
  - ScoringMixin (클래스): 핵심 로직을 수행합니다.

### `workshop_window.py`
- 역할: 작업장 헤드와 window 후보 구성을 담당합니다.
- 입력: 남은 블록, 현재 시간, 제약 설정
- 출력: 후보 id 리스트, 위반 상세
- 연결: env.get_available_actions_assembly에서 호출됩니다.
- 주요 엔트리:
  - WorkshopWindowMixin (클래스): 핵심 로직을 수행합니다.

## 주요 클래스/함수 (요약)
| 이름 | 분류 |
|---|---|
| CapacityMixin | 클래스 |
| ConstraintChecker | 클래스 |
| DebugMixin | 클래스 |
| PSMixingMixin | 클래스 |
| RoutingMixin | 클래스 |
| SawTimeMixin | 클래스 |
| ScoringMixin | 클래스 |
| WorkshopWindowMixin | 클래스 |

## 운영 팁
- 디버그 로그는 PBS_FORCE_DEBUG/PBS_DEBUG_VERBOSE로 제어합니다.

## 코드/API 상세 (자동 추출)
<!-- AUTO-GENERATED: DO NOT EDIT BELOW -->

### 파일: `__init__.py`

- 모듈 설명: 설명 없음

#### 클래스 없음

#### 함수 없음

### 파일: `capacity.py`

- 모듈 설명: 설명 없음

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| CapacityMixin | 없음 | 설명 없음 |

##### 클래스: `CapacityMixin` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _get_block_capacity_load | (block: EnhancedBlock) | int | Return SEAM+C/SEAM count used for capacity constraints. |
| check_daily_capacity | (self, blocks_to_add: List[EnhancedBlock], current_date: datetime) | Tuple[bool, List[EnhancedBlock], str] | 🆕 일일 용량 체크 및 분할 |
| _check_delivery_date | (self, block: EnhancedBlock, current_time: datetime) | bool | P5#1: 납기 기반 착수일 체크 |
| _check_material_ready | (self, block: EnhancedBlock) | bool | P5#13: 자재 미입고 Skip |
| _check_afternoon_start_time | (self, block: EnhancedBlock, current_time: datetime) | bool | legacy no-op. 논문 실험 기준 P6#1,2,3 제거 후 항상 통과 |
| _get_afternoon_constraint_id | (self, block: EnhancedBlock) | str | legacy ID 반환 |
| _check_holiday_eve_constraint | (self, block: EnhancedBlock, current_time: datetime) | Tuple[bool, str] | P5#15: 명절 전날 야간(15:00 이후) 차단 |
| _check_integrated_capacity_constraints | (self, block: EnhancedBlock, current_time: datetime) | Tuple[bool, str] | P5#8,9,10,16: 통합 달력+용량 제약조건 |

#### 함수 없음

### 파일: `core.py`

- 모듈 설명: Enhanced Panel Block Shop - Action Masking for PFSP

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| ConstraintChecker | DebugMixin, CapacityMixin, RoutingMixin, PSMixingMixin, WorkshopWindowMixin, ScoringMixin, SawTimeMixin | 제약조건 검증 및 액션 마스킹 통합 클래스 |

##### 클래스: `ConstraintChecker` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| __init__ | (self, capacity_tracker: CapacityTracker, ps_manager: PSBlockManager, bay_tracker: BayStateTracker, calendar_manager: CalendarManager, constraint_config: 'ConstraintConfig', metadata: Dict) | - | 설명 없음 |
| _normalize_relax_key | (self, raw: str) | str | 완화 키 정규화 (한글/영문 혼용 지원). |
| _resolve_strict_rules | (self) | Set[str] | strict_rules(한글/영문)를 constraint id set으로 변환. |
| _get_relax_mapping | (self) | Dict[str, Tuple[str, List[str]]] | 완화 키 매핑 테이블. |
| _resolve_relax_stages | (self, default_stages: List[Tuple[str, List[str]]], relax_list: Optional[List[str]], label_prefix: str) | List[Tuple[str, List[str]]] | config.relax_order를 반영해 완화 단계 순서를 재구성. |
| set_blocks_dict | (self, blocks_dict: Dict[int, EnhancedBlock]) | - | 블록 딕셔너리 설정 (연속성 체크를 위해 필요) |
| get_next_available_blocks | (self, blocks: List[EnhancedBlock], current_time: datetime, selected_blocks: List[int], last_assembly_type: AssemblyType) | Tuple[List[int], List[ConstraintViolation], List[Dict]] | 완전 재구성: 올바른 제약조건 우선순위 적용 |
| get_next_available_blocks_assembly | (self, blocks: List[EnhancedBlock], current_time: datetime, selected_blocks: List[int], last_assembly_type: AssemblyType, include_relax_candidates: bool, panel_date: Optional[date], previous_machine_state: Optional[Dict], current_bay_assignments: Optional[Dict[int, BayType]], current_day_selected_blocks: Optional[List[int]]) | Tuple[List[int], List[ConstraintViolation], List[Dict]] | Assembly Decoding 전용 블록 선택 로직 (조립착수일 우선순위 기반) |
| set_decoding_type | (self, decoding_type: str) | - | 디코딩 타입 설정 |
| set_assembly_blocks | (self, blocks: List[EnhancedBlock]) | - | Assembly decoding용 전체 블록 설정 |
| set_assembly_expansion_mode | (self, mode: int) | - | 🚨 DEPRECATED: 이 함수는 더 이상 사용되지 않습니다. |
| set_current_panel_date | (self, panel_date: datetime) | - | 현재 판넬 착수일 설정 |
| calculate_panel_start_date | (self, assembly_start_date: datetime, assembly_type: AssemblyType, workload_factor: float) | datetime | 조립착수일 기준 판넬 착수일 계산 |
| get_relaxation_days | (self, relaxation_level: int) | int | 제약 완화 단계별 추가 여유 일수 반환 (+1일씩 단계적 확장) |
| filter_blocks_by_assembly_constraints | (self, blocks: List[EnhancedBlock], current_panel_date: datetime, relaxation_level: int) | List[int] | 조립착수일 기준 제약조건으로 블록 필터링 (+1일씩 단계적 완화) |
| mark_block_selected | (self, block_id: int) | - | 블록을 선택됨으로 표시 |
| set_sequence | (self, sequence: List[int]) | - | 선택된 블록 순서 설정 |
| reset | (self) | - | 상태 리셋 |
| reset_statistics | (self) | - | 통계 리셋 |
| get_constraint_summary | (self) | Dict[str, str] | 제약조건 요약 정보 반환 |
| update_state_after_action | (self, processed_block: EnhancedBlock) | - | 액션 후 상태 업데이트 |
| advance_to_next_step | (self) | - | 다음 단계로 진행 |
| _get_current_block_count | (self) | int | 현재 처리된 블록 수 반환 (P5#9 제약조건용) |
| _set_current_selected_blocks | (self, selected_blocks: List[int]) | - | 현재 선택된 블록 리스트를 임시 저장 (P5#9 제약조건 체크용) |

#### 함수 없음

### 파일: `debug.py`

- 모듈 설명: 설명 없음

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| DebugMixin | 없음 | 설명 없음 |

##### 클래스: `DebugMixin` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _resolve_debug_flag | (self, config: Optional[ConstraintConfig]) | bool | Determine whether verbose masking diagnostics should be printed. |
| _is_trace_block | (self, block_id: Optional[int]) | bool | Return True if the block_id is explicitly requested for tracing. |
| _debug_candidate_stage | (self, debug_enabled: bool, label: str, collection: Any, history: Optional[List[Tuple[str, Any]]]) | None | Helper to print candidate counts for each filtering stage when debugging is enabled. |
| _print_candidate_diagnostics | (self, selected_blocks: List[int], block_analysis: List[Dict[str, Any]], blocks: List[EnhancedBlock], debug_enabled: bool) | None | Print detailed diagnostics when no selectable blocks remain. |

#### 함수 없음

### 파일: `ps_mixing.py`

- 모듈 설명: 설명 없음

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| PSMixingMixin | 없음 | 설명 없음 |

##### 클래스: `PSMixingMixin` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _check_assembly_type_constraint | (self, block: EnhancedBlock, last_assembly_type: AssemblyType, selected_blocks: List[int], all_blocks: List[EnhancedBlock], relaxed_constraints: Optional[List[str]]) | Tuple[bool, str] | P5#11,12: Assembly Type 혼합 배정 제약 체크 |
| _check_ps_order_constraint | (self, block: EnhancedBlock, selected_blocks: List[int], all_blocks: List[EnhancedBlock]) | Tuple[bool, str] | P5#3,4: P/S 순서 제약 체크 |
| _is_ps_priority_override | (self, block: EnhancedBlock, selected_blocks: List[int]) | bool | PS 순서 제약으로 인해 다른 제약보다 우선해야 하는지 확인 |
| _apply_ps_pair_masking | (self, available_blocks: List[EnhancedBlock], selected_blocks: List[int], violations: List[ConstraintViolation], all_blocks: List[EnhancedBlock]) | List[EnhancedBlock] | P5#3: P/S 쌍 마스킹 적용 (PSBlockManager 연동) + P 선택 후 S 강제 선택 |

#### 함수 없음

### 파일: `routing.py`

- 모듈 설명: 설명 없음

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| RoutingMixin | 없음 | 설명 없음 |

##### 클래스: `RoutingMixin` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _check_consecutive_3bay_prevention | (self, candidate_block: EnhancedBlock, selected_blocks: List[int]) | Tuple[bool, str] | 연속 3판 B베이 방지 제약조건 확인 |
| _check_curved_plate_spacing | (self, block: EnhancedBlock, selected_blocks: List[int]) | Tuple[bool, str] | 설명 없음 |
| _check_high_seam_spacing | (self, block: EnhancedBlock, selected_blocks: List[int]) | Tuple[bool, str] | 설명 없음 |
| _check_c_seam_spacing | (self, block: EnhancedBlock, selected_blocks: List[int]) | Tuple[bool, str] | 설명 없음 |
| _check_spacing_rule | (self, block: EnhancedBlock, selected_blocks: List[int], predicate, min_gap: int, rule_name: str) | Tuple[bool, str] | 설명 없음 |
| _check_workshop_order_constraint | (self, block: EnhancedBlock, candidate_blocks: List[EnhancedBlock], selected_blocks: List[int]) | Tuple[bool, str] | 설명 없음 |

#### 함수 없음

### 파일: `saw_time.py`

- 모듈 설명: 설명 없음

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| SawTimeMixin | 없음 | 설명 없음 |

##### 클래스: `SawTimeMixin` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _check_saw_time_constraint | (self, block: EnhancedBlock, current_time: datetime, previous_machine_state: Optional[Dict], current_bay_assignments: Optional[Dict[int, BayType]], current_day_selected_blocks: Optional[List[int]], sequencing_date: Optional[date]) | Tuple[bool, str] | legacy no-op. 논문 실험 기준 P6#1,2,3 제거 |
| _calculate_actual_machine_2_start_time_action_masking | (self, block: EnhancedBlock, current_time: datetime, afternoon_guard_blocks: Optional[Set[int]], previous_machine_state: Optional[Dict], current_bay_assignments: Optional[Dict[int, BayType]], current_day_selected_blocks: Optional[List[int]]) | datetime | Action Masking용 실제 머신 2번 시작 시간 계산 |
| _fallback_machine_2_time_calculation | (self, block: EnhancedBlock, current_time: datetime) | datetime | Fallback 머신 2번 시간 계산 |

#### 함수 없음

### 파일: `scoring.py`

- 모듈 설명: 설명 없음

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| ScoringMixin | 없음 | 설명 없음 |

##### 클래스: `ScoringMixin` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _evaluate_blocks_stage | (self, blocks: List[EnhancedBlock], stage_label: str, selected_blocks: List[int], block_analysis: List[Dict[str, Any]], current_time: datetime, last_assembly_type: Optional[AssemblyType], all_blocks: List[EnhancedBlock], constraints_to_relax: Optional[List[str]], violations: Optional[List[ConstraintViolation]], previous_machine_state: Optional[Dict], current_bay_assignments: Optional[Dict[int, BayType]], current_day_selected_blocks: Optional[List[int]], **kwargs) | List[EnhancedBlock] | 설명 없음 |
| _score_block_violations | (self, block: EnhancedBlock, selected_blocks: List[int], all_blocks: List[EnhancedBlock], current_time: datetime, last_assembly_type: Optional[AssemblyType], previous_machine_state: Optional[Dict], current_bay_assignments: Optional[Dict[int, BayType]], current_day_selected_blocks: Optional[List[int]]) | Tuple[int, List[str]] | 설명 없음 |
| _pick_leadtime_guard_block | (self, blocks: List[EnhancedBlock], current_panel_date: datetime.date, override_required_days: Optional[int], prefer_time_feasible: bool, current_time: Optional[datetime], previous_machine_state: Optional[Dict], current_bay_assignments: Optional[Dict[int, BayType]]) | Optional[EnhancedBlock] | 설명 없음 |
| _force_select_earliest_block | (self, blocks: List[EnhancedBlock], selected_blocks: List[int]) | Tuple[Optional[EnhancedBlock], Optional[List[str]]] | 워크숍 우선으로 묶여 있는 경우 가장 빠른 착수일 블록을 강제로 선택. |
| _check_cross_seam_constraint | (self, block: EnhancedBlock, selected_blocks: List[int], all_blocks: List[EnhancedBlock]) | Tuple[bool, str] | P6#4: Cross seam 혼합 배치 제약 체크 |

#### 함수 없음

### 파일: `workshop_window.py`

- 모듈 설명: 설명 없음

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| WorkshopWindowMixin | 없음 | 설명 없음 |

##### 클래스: `WorkshopWindowMixin` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _get_minimum_lead_days | (self) | int | 설명 없음 |
| _is_overdue_block | (self, block: EnhancedBlock, current_panel_date: date) | bool | Assembly 착수 기준 리드타임을 넘긴 블록인지 확인. |
| _push_constraint_override | (self, stage_definitions: List[Tuple[str, List[str]]]) | Dict[str, bool] | stage_definitions에서 사용하는 제약 키를 임시로 비활성화하고, |
| _pop_constraint_override | (self, original: Dict[str, bool]) | None | _push_constraint_override로 변경한 제약 설정을 원복 |
| _is_leadtime_guard_block | (self, block: EnhancedBlock, current_date: date) | bool | 리드타임 가드 대상(P6 강제) 여부 판정 |
| _get_pre_relax_lead_floor | (self) | int | 설명 없음 |
| _get_relax_lead_floor | (self) | int | 설명 없음 |
| _check_minimum_lead_time | (self, block: EnhancedBlock, current_panel_date: date, override_required_days: Optional[int]) | Tuple[bool, Optional[str]] | Ensure assembly start date is at least configured days after current panel date. |
| _classify_block_type | (self, block: EnhancedBlock) | str | 블록을 3가지 타입으로 분류 |
| _get_workshop_priority_key | (self, block: EnhancedBlock) | Optional[Tuple[str, str, str]] | 설명 없음 |
| _normalize_workshop_code | (self, workshop_code: str) | str | 설명 없음 |
| _resolve_assembly_category | (self, block: EnhancedBlock) | Optional[str] | Return normalized assembly category (라인 그룹 or FIXED). |
| _compute_workshop_min_dates | (self, candidate_blocks: List[EnhancedBlock], selected_blocks: List[int]) | Dict[Tuple[str, str, str], datetime] | 설명 없음 |
| _collect_workshop_priority_ids | (self, candidate_blocks: List[EnhancedBlock], selected_blocks: List[int], analysis_map: Dict[int, Dict], violations: List[ConstraintViolation]) | Tuple[Set[int], Dict[int, str], int] | 설명 없음 |
| _collect_workshop_heads | (self, workshop_buckets: Dict[str, List[EnhancedBlock]], selected_set: Set[int], current_panel_date: date, window_days: Optional[int]) | List[EnhancedBlock] | Return earliest block per workshop filtered by initial window. |
| _build_workshop_buckets | (self, blocks: List[EnhancedBlock], selected_blocks: List[int]) | Dict[str, List[EnhancedBlock]] | 설명 없음 |
| _collect_workshop_window_candidates | (self, workshop_buckets: Dict[str, List[EnhancedBlock]], selected_blocks: List[int], current_panel_date: date, window_days: int, all_blocks: List[EnhancedBlock]) | Tuple[List[EnhancedBlock], Set[int], Dict[int, str]] | 설명 없음 |
| _select_workshop_priority_blocks | (self, blocks: List[EnhancedBlock], allowed_ids: Set[int]) | List[EnhancedBlock] | 설명 없음 |
| _is_workshop_priority_satisfied | (self, block: EnhancedBlock, workshop_min_dates: Dict[Tuple[str, str, str], datetime], allowed_window_days: int) | Tuple[bool, Optional[str]] | 설명 없음 |

#### 함수 없음

<!-- /AUTO-GENERATED -->
