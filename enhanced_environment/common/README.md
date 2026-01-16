# enhanced_environment/common 핸드북 (상세)

공통 유틸 모음입니다. 엑셀 변환, 시간 계산, 위반 집계, 설정 로딩을 담당합니다.

---

## 흐름(요약)
```
엑셀/CSV
  └─ DataConverter
       └─ EnhancedBlock/메타 생성
            └─ 환경/스케줄링
```

## 핵심 개념
- DataConverter는 엑셀 스키마 변경에 민감합니다.
- time_utils는 하루 경계와 근무시간 계산에 사용됩니다.

## 파일별 상세
### `data_converter.py`
- 역할: 엑셀/CSV 변환과 블록 생성 입력을 처리합니다.
- 입력: 파일 경로, 날짜 문자열, 블록 리스트
- 출력: 블록/메타/시간 계산 결과
- 연결: 환경/스케줄링/평가에서 공통으로 호출됩니다.
- 주요 엔트리:
  - DataConverter (클래스): 핵심 로직을 수행합니다.

### `logger_utils.py`
- 역할: 로그 포맷과 출력 보조를 담당합니다.
- 입력: 파일 경로, 날짜 문자열, 블록 리스트
- 출력: 블록/메타/시간 계산 결과
- 연결: 환경/스케줄링/평가에서 공통으로 호출됩니다.
- 주요 엔트리:
  - Logger (클래스): 핵심 로직을 수행합니다.

### `performance_analyzer.py`
- 역할: 성과 요약과 통계를 계산합니다.
- 입력: 파일 경로, 날짜 문자열, 블록 리스트
- 출력: 블록/메타/시간 계산 결과
- 연결: 환경/스케줄링/평가에서 공통으로 호출됩니다.
- 주요 엔트리:
  - PerformanceAnalyzer (클래스): 핵심 로직을 수행합니다.

### `settings.py`
- 역할: 런타임 설정 로딩과 기본값을 관리합니다.
- 입력: 파일 경로, 날짜 문자열, 블록 리스트
- 출력: 블록/메타/시간 계산 결과
- 연결: 환경/스케줄링/평가에서 공통으로 호출됩니다.
- 주요 엔트리:
  - (공개 엔트리 없음)

### `time_utils.py`
- 역할: 날짜/시간 계산과 창(window) 판정을 담당합니다.
- 입력: 파일 경로, 날짜 문자열, 블록 리스트
- 출력: 블록/메타/시간 계산 결과
- 연결: 환경/스케줄링/평가에서 공통으로 호출됩니다.
- 주요 엔트리:
  - TimeUtils (클래스): 핵심 로직을 수행합니다.

### `utils_core.py`
- 역할: 해당 폴더의 중심 로직입니다.
- 입력: 파일 경로, 날짜 문자열, 블록 리스트
- 출력: 블록/메타/시간 계산 결과
- 연결: 환경/스케줄링/평가에서 공통으로 호출됩니다.
- 주요 엔트리:
  - (공개 엔트리 없음)

### `validation_utils.py`
- 역할: 입력값 검증과 안전한 변환을 담당합니다.
- 입력: 파일 경로, 날짜 문자열, 블록 리스트
- 출력: 블록/메타/시간 계산 결과
- 연결: 환경/스케줄링/평가에서 공통으로 호출됩니다.
- 주요 엔트리:
  - ValidationUtils (클래스): 핵심 로직을 수행합니다.

### `violation_utils.py`
- 역할: 위반 객체와 중복 제거, 집계를 담당합니다.
- 입력: 파일 경로, 날짜 문자열, 블록 리스트
- 출력: 블록/메타/시간 계산 결과
- 연결: 환경/스케줄링/평가에서 공통으로 호출됩니다.
- 주요 엔트리:
  - extract_relax_constraints (함수): 핵심 로직을 수행합니다.
  - count_relax_events (함수): 핵심 로직을 수행합니다.
  - summarize_violations (함수): 핵심 로직을 수행합니다.
  - dedup_violations (함수): 핵심 로직을 수행합니다.

## 주요 클래스/함수 (요약)
| 이름 | 분류 |
|---|---|
| DataConverter | 클래스 |
| Logger | 클래스 |
| PerformanceAnalyzer | 클래스 |
| TimeUtils | 클래스 |
| ValidationUtils | 클래스 |
| extract_relax_constraints | 함수 |
| count_relax_events | 함수 |
| summarize_violations | 함수 |
| dedup_violations | 함수 |

## 운영 팁
- 엑셀 파일 경로는 상대 경로로 유지합니다.

## 코드/API 상세 (자동 추출)
<!-- AUTO-GENERATED: DO NOT EDIT BELOW -->

### 파일: `__init__.py`

- 모듈 설명: 설명 없음

#### 클래스 없음

#### 함수 없음

### 파일: `data_converter.py`

- 모듈 설명: Data conversion utilities

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| DataConverter | 없음 | 데이터 변환 유틸리티 |

##### 클래스: `DataConverter` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| excel_to_blocks | (excel_path: str, sheet_name: Optional[str]) | List[EnhancedBlock] | 실제 데이터셋 엑셀 파일을 EnhancedBlock 리스트로 변환 (250618_SNU.xlsx 버전) |
| _extract_actual_tact_times | (row) | Optional[List[float]] | 실제 Tact Time 데이터 추출 (250618_SNU.xlsx 버전) |
| _sort_blocks_by_sequence_number | (blocks: List[EnhancedBlock]) | List[EnhancedBlock] | 순번열 기준으로 블록 정렬 (동일 조립 착수일 내에서) |
| _format_assembly_date_key | (date_value: Optional[datetime], fallback: str) | str | 조립 착수일을 YYYYMMDD 문자열로 변환 (없으면 고유 fallback 사용) |
| _get_block_base_and_suffix | (block_name: str) | Tuple[str, Optional[str]] | 블록명을 기본명과 접미사(P/S 등)로 분리. |
| _physical_key_from_block_data | (block_data: Dict[str, Any]) | Tuple | EnhancedBlock.get_physical_characteristics_key와 동일한 물리 키 생성 |
| _auto_match_ps_pairs | (block_data_list: List[Dict]) | Dict[int, int] | P/S 쌍 자동 매칭 (착수일 명시적 확인 추가) |
| _extract_block_number | (block_name: str) | Optional[str] | 블록명에서 숫자 부분 추출 |
| _parse_block_ps_type | (block_name: str) | PortStarboard | 블록명에서 P/S/C 구분 추출 |
| _parse_date_format | (date_value) | datetime | 20240509 형식 날짜를 datetime으로 변환 |
| _parse_assembly_workshop | (workshop_str: str) | AssemblyType | 조립 작업장 문자열을 AssemblyType으로 변환 |
| _normalize_line_group | (value: Optional[str]) | str | 설명 없음 |
| _line_group_from_longi_workshop | (workshop_value) | str | 설명 없음 |
| _extract_line_group | (workshop_str: str) | Optional[str] | 설명 없음 |
| _generate_processing_times | (seam_count: int, longi_count: int, main_plate_count: int, angle_count: int, buildup_count: int, width: float, length: float, max_thickness: float, min_thickness: float, total_weight: float, total_seam_length: float, longi_length: float, is_step_block: bool, is_special_block: bool, c_seam_count: int, assembly_type: str) | List[float] | 산출식.txt 기반 8개 공정 처리시간 정확 계산 |
| blocks_to_dataframe | (blocks: List[EnhancedBlock]) | pd.DataFrame | EnhancedBlock 리스트를 DataFrame으로 변환 |
| normalize_processing_times | (blocks: List[EnhancedBlock], method: str) | List[EnhancedBlock] | 처리 시간 정규화 |
| _process_subassembly_grouping | (blocks: List[EnhancedBlock]) | List[EnhancedBlock] | P5#17: 별판 처리 및 순번 생성 (회의 내용 반영 + 물리적/구조적 특성 일치 조건 추가) |
| expand_subassembly_results | (sequence: List[int], blocks: List[EnhancedBlock]) | List[Tuple[int, int]] | 별판 결과 확장: 통합된 블록 순서를 원본 별판들로 복원 |
| expand_rows_with_subassembly | (rows: List[Dict]) | List[Dict] | 설명 없음 |
| _validate_p5_9_constraint_pre_check | (blocks: List[EnhancedBlock]) | List[EnhancedBlock] | P5#9: 제약 사전 검증 (시퀀싱 시작 전) |
| _calculate_panel_start_date | (assembly_start_date: datetime, assembly_type: AssemblyType) | datetime | P5#1 제약조건: 조립 타입별 판넬 최대 착수일 계산 |
| excel_to_blocks_with_metadata | (excel_path: str, sheet_name: Optional[str]) | Tuple[List[EnhancedBlock], Dict] | 실제 데이터셋 엑셀 파일을 EnhancedBlock 리스트와 메타데이터로 변환 |
| _resolve_data_sheet_name | (workbook: pd.ExcelFile, preferred_sheet: Optional[str]) | str | 엑셀 데이터 시트 결정 (환경 변수 / 기본 우선순위 지원) |
| _sheet_has_required_columns | (workbook: pd.ExcelFile, sheet_name: str, required_columns: Optional[Set[str]]) | bool | 시트가 필요한 컬럼을 포함하는지 확인 |
| dataframe_to_blocks_with_metadata | (df: pd.DataFrame) | Tuple[List[EnhancedBlock], Dict] | DataFrame을 EnhancedBlock 리스트와 메타데이터로 변환 (PPO용) |
| _generate_complete_metadata | (blocks: List[EnhancedBlock]) | Dict | 완전한 메타데이터 생성 |
| _extract_daily_structure | (blocks: List[EnhancedBlock]) | Dict | 일별 구조 정보 추출 |
| _extract_ps_pair_complete_info | (blocks: List[EnhancedBlock]) | Dict | P/S 쌍 완전 정보 추출 |
| _extract_subassembly_complete_info | (blocks: List[EnhancedBlock]) | Dict | 별판 완전 정보 추출 (물리적/구조적 특성 포함) |
| _extract_constraint_application_map | (blocks: List[EnhancedBlock]) | Dict | 제약조건 적용 맵 추출 |
| _extract_bay_assignment_strategy | (blocks: List[EnhancedBlock]) | Dict | 베이 할당 전략 추출 |
| _extract_fab_interval_tracking | (blocks: List[EnhancedBlock]) | Dict | P5#6: FAB 간격 추적 정보 추출 |
| _extract_assembly_mixing_control | (blocks: List[EnhancedBlock]) | Dict | P5#11,12: 혼합 배정 제어 정보 추출 |
| _extract_cross_seam_mixing_control | (blocks: List[EnhancedBlock]) | Dict | P6#4: 혼합 배치 제어 정보 추출 |
| _extract_block_10_special_rule | (blocks: List[EnhancedBlock]) | Dict | P7#11: 10번 블록 특별 규칙 추출 |
| _extract_constraint_conflicts | () | Dict | 제약조건 충돌 처리 규칙 |
| resolve_line_group_value | (line_group, assembly_type, assembly_code) | str | 설명 없음 |
| resolve_workshop_code_value | (assembly_code, line_group, assembly_type) | str | 설명 없음 |
| resolve_line_group_for_block | (block) | str | 설명 없음 |
| get_line_group_and_workshop_code | (block) | - | 라인 그룹 요약과 원본 조립 작업장 코드를 동시에 반환 |
| resolve_workshop_code_for_block | (block) | str | 설명 없음 |
| _assembly_type_to_str | (assembly_type) | str | 설명 없음 |
| _initialize_dynamic_state_tracking | () | Dict | 동적 상태 추적 초기화 |

#### 함수 없음

### 파일: `logger_utils.py`

- 모듈 설명: Logging utilities

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| Logger | 없음 | 로깅 유틸리티 |

##### 클래스: `Logger` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| setup_logger | (name: str, level: int, log_file: Optional[str], format_str: Optional[str]) | logging.Logger | 로거 설정 |
| log_constraint_violation | (logger: logging.Logger, constraint_id: str, message: str, block_id: Optional[int], severity: str) | - | 제약조건 위반 로그 |
| log_performance_metrics | (logger: logging.Logger, metrics: Dict[str, float], step: int) | - | 성능 메트릭 로그 |

#### 함수 없음

### 파일: `performance_analyzer.py`

- 모듈 설명: Performance analysis utilities

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| PerformanceAnalyzer | 없음 | 성능 분석 유틸리티 |

##### 클래스: `PerformanceAnalyzer` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| analyze_makespan | (action_history: List[Dict]) | Dict[str, float] | makespan 분석 |
| analyze_constraint_violations | (violation_history: List) | Dict[str, Any] | 제약조건 위반 분석 |
| generate_performance_report | (env_info: Dict[str, Any]) | str | 성능 리포트 생성 |

#### 함수 없음

### 파일: `settings.py`

- 모듈 설명: 설명 없음

#### 클래스 없음

#### 함수 없음

### 파일: `time_utils.py`

- 모듈 설명: Time utilities

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| TimeUtils | 없음 | 시간 관련 유틸리티 함수들 |

##### 클래스: `TimeUtils` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| is_weekend | (date: datetime) | bool | 주말인지 확인 (토요일: 5, 일요일: 6) |
| is_work_hour | (time: datetime) | bool | 작업 시간인지 확인 (오전 8시 ~ 오후 10시) |
| is_afternoon | (time: datetime) | bool | 오후인지 확인 (오후 3시 이후) |
| get_work_hours_between | (start_time: datetime, end_time: datetime) | float | 두 시간 사이의 작업 시간 계산 (시간 단위) |
| add_work_hours | (start_time: datetime, work_hours: float) | datetime | 작업 시간만큼 시간 추가 |
| add_work_hours_with_calendar | (start_time: datetime, work_hours: float, calendar_manager) | datetime | CalendarManager 규칙을 고려해 작업 시간을 더한다. |
| _next_work_time | (current_time: datetime) | datetime | 다음 작업 시간 반환 |
| format_duration | (seconds: float) | str | 초를 읽기 쉬운 형태로 변환 |
| parse_time_string | (time_str: str) | datetime | 시간 문자열을 datetime으로 변환 |

#### 함수 없음

### 파일: `utils_core.py`

- 모듈 설명: Legacy utilities aggregator for backward compatibility.

#### 클래스 없음

#### 함수 없음

### 파일: `validation_utils.py`

- 모듈 설명: Validation utilities

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| ValidationUtils | 없음 | 검증 유틸리티 |

##### 클래스: `ValidationUtils` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| validate_blocks | (blocks: List[EnhancedBlock], start_time: datetime) | Tuple[bool, List[str]] | 블록 리스트 검증 |
| _validate_single_block | (block: EnhancedBlock, reference_time: datetime) | List[str] | 단일 블록 검증 |
| _validate_ps_pairs | (blocks: List[EnhancedBlock]) | List[str] | P/S 쌍 검증 |
| validate_environment_config | (config: Dict[str, Any]) | Tuple[bool, List[str]] | 환경 설정 검증 |

#### 함수 없음

### 파일: `violation_utils.py`

- 모듈 설명: 설명 없음

#### 클래스 없음

#### 함수 목록

| 함수 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| extract_relax_constraints | (violations: List[ConstraintViolation]) | List[str] | 완화 단계에서 해제된 제약 ID를 메시지에서 추출 |
| count_relax_events | (violations: List[ConstraintViolation]) | int | 완화 이벤트 횟수(RELAX_STAGE 등)를 집계 |
| summarize_violations | (violations: List[ConstraintViolation], keep_info: bool, include_guard: bool) | Dict[str, Any] | CSV 저장용 제약 요약(카운트/상세/완화) 생성 |
| dedup_violations | (violations: List[ConstraintViolation], keep_info: bool, include_guard: bool, guard_constraint_ids: Optional[Set[str]]) | Tuple[List[ConstraintViolation], List[ConstraintViolation]] | 제약 위반 목록을 constraint_id 기준으로 중복 제거하고 대표 위반만 남깁니다. |

<!-- /AUTO-GENERATED -->
