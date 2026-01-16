# utils 핸드북 (상세)

분석/생성/저장 유틸 모음입니다. 가공 데이터 생성과 CSV/간트 출력이 포함됩니다.

---

## 흐름(요약)
```
데이터 생성
  └─ 저장
       └─ 시각화
```

## 핵심 개념
- OptimizedBlockGenerator는 학습 분포를 결정합니다.

## 파일별 상세
### `csv_save.py`
- 역할: CSV 저장 유틸을 제공합니다.
- 입력: 블록/스케줄 결과
- 출력: CSV, 그래프
- 연결: 학습/평가/분석 경로에서 공통 사용됩니다.
- 주요 엔트리:
  - save_detailed_masking_info (함수): 마스킹/필터링을 수행합니다.
  - save_detailed_bay_selection_info (함수): 선택 로직을 수행합니다.
  - save_assembly_decoding_schedule_info (함수): 핵심 로직을 수행합니다.
  - save_assembly_decoding_bay_info (함수): 핵심 로직을 수행합니다.

### `gantt_chart_enhanced.py`
- 역할: 간트 차트 출력과 시각화를 담당합니다.
- 입력: 블록/스케줄 결과
- 출력: CSV, 그래프
- 연결: 학습/평가/분석 경로에서 공통 사용됩니다.
- 주요 엔트리:
  - find_latest_result_folder (함수): 핵심 로직을 수행합니다.
  - create_gantt_folders (함수): 핵심 로직을 수행합니다.
  - load_process_csv_files (함수): 처리 로직을 수행합니다.
  - create_machine_mapping (함수): 핵심 로직을 수행합니다.
  - assign_machine_line (함수): 배정 로직을 수행합니다.
  - get_enhanced_color_and_style (함수): 상태/정보를 조회합니다.

### `optimized_block_generator.py`
- 역할: 블록 모델과 속성 정의를 포함합니다.
- 입력: 블록/스케줄 결과
- 출력: CSV, 그래프
- 연결: 학습/평가/분석 경로에서 공통 사용됩니다.
- 주요 엔트리:
  - debug_print (함수): 핵심 로직을 수행합니다.
  - info_print (함수): 핵심 로직을 수행합니다.
  - OptimizedBlockGenerator (클래스): 핵심 로직을 수행합니다.

## 주요 클래스/함수 (요약)
| 이름 | 분류 |
|---|---|
| save_detailed_masking_info | 함수 |
| save_detailed_bay_selection_info | 함수 |
| save_assembly_decoding_schedule_info | 함수 |
| save_assembly_decoding_bay_info | 함수 |
| find_latest_result_folder | 함수 |
| create_gantt_folders | 함수 |
| load_process_csv_files | 함수 |
| create_machine_mapping | 함수 |
| assign_machine_line | 함수 |
| get_enhanced_color_and_style | 함수 |
| create_enhanced_gantt_chart | 함수 |
| calculate_method_stats | 함수 |

## 운영 팁
- CSV 스키마 변경 시 분석 코드도 함께 수정합니다.

## 코드/API 상세 (자동 추출)
<!-- AUTO-GENERATED: DO NOT EDIT BELOW -->

### 파일: `__init__.py`

- 모듈 설명: Utility scripts and helpers.

#### 클래스 없음

#### 함수 없음

### 파일: `csv_save.py`

- 모듈 설명: 설명 없음

#### 클래스 없음

#### 함수 목록

| 함수 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| save_detailed_masking_info | (date_key: str, daily_step_info: List[Dict], blocks_in_date: List) | - | 날짜별 Action Masking 상세 분석 정보 CSV 저장 |
| save_detailed_bay_selection_info | (bay_analyses_by_date: Dict[str, List[Dict]]) | - | 베이 선택 상세 분석 정보를 날짜별 CSV로 저장 |
| save_assembly_decoding_schedule_info | (assembly_step_info: List[Dict], date_key: str, all_blocks: List) | - | Assembly Decoding 방식의 날짜별 상세 스케줄링 분석 정보 CSV 저장 |
| save_assembly_decoding_bay_info | (bay_analyses: List[Dict], date_key: str) | - | Assembly Decoding 방식의 베이 선택 상세 분석 정보를 CSV로 저장 |

### 파일: `gantt_chart_enhanced.py`

- 모듈 설명: 향상된 공정별 간트차트 생성기 - 더 명확한 시각화

#### 클래스 없음

#### 함수 목록

| 함수 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| find_latest_result_folder | () | - | PPO 폴더에서 가장 최신의 결과 폴더를 찾기 |
| create_gantt_folders | (data_by_method, result_base_folder) | - | 간트차트 저장을 위한 폴더 구조 생성 |
| load_process_csv_files | (pattern, search_dir) | - | 공정별 상세 스케줄링 CSV 파일들을 로드 |
| create_machine_mapping | () | - | 기계 순서 매핑 생성 |
| assign_machine_line | (df) | - | 각 공정을 적절한 기계 라인에 할당 |
| get_enhanced_color_and_style | (row) | - | 향상된 색상 및 스타일 반환 |
| create_enhanced_gantt_chart | (combined_df, method_name, chart_title, save_path) | - | 향상된 간트차트 생성 |
| calculate_method_stats | (data_by_method) | - | 방법론별 통계 계산 |
| create_comparison_chart | (stats, save_folder) | - | 방법론 비교 차트 생성 |
| generate_enhanced_gantt_charts | (search_dir) | - | 향상된 간트차트 생성 |

### 파일: `optimized_block_generator.py`

- 모듈 설명: 최적화된 블록 생성기

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| OptimizedBlockGenerator | 없음 | 최적화된 블록 생성기 |

##### 클래스: `OptimizedBlockGenerator` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| __init__ | (self) | - | 초기화 - 실제 데이터 값들 저장 |
| _extract_real_values | (self) | - | 실제 데이터에서 기본 규격 값들 추출 |
| _extract_area_distributions | (self) | - | 면적별 경험적 분포 파라미터 추출 |
| _sample_from_distribution | (self, distribution: dict, default_value) | - | 설명 없음 |
| _sample_workshop_code | (self) | str | 설명 없음 |
| _derive_line_group | (self, workshop_code: str) | str | 설명 없음 |
| _derive_assembly_type | (self, workshop_code: str) | str | 설명 없음 |
| _normalize_allowed_workshop_code | (self, code: str) | str | 설명 없음 |
| _get_block_base_and_suffix | (self, block_name: str) | Tuple[str, Optional[str]] | 블록명을 기본명과 접미사(P/S 등)로 분리 |
| _compute_physical_key | (self, block: Dict[str, Any]) | Tuple | 별판/쌍 검증을 위한 물리 특성 키 생성 |
| _get_assembly_date_token | (self, block: Dict[str, Any]) | Optional[str] | 조립 착수일을 비교 가능한 문자열 토큰으로 변환 |
| _load_full_data_json | (self, json_path: str) | bool | Compatibility stub: real_block_data_full.json is no longer used. |
| load_from_saved_values | (cls, json_path) | - | 저장된 값들로부터 생성기 로드 |
| generate_single_block | (self) | - | 단일 블록 생성 |
| generate_complete_learning_data | (self, n_blocks, save_filename) | - | 완전한 학습데이터 생성 (블록 특성 + 조립착수일 + Tact Time) |
| _analyze_generated_assembly_dates | (self, df) | - | 생성된 조립착수일 분포 분석 |
| generate_assembly_start_date | (self) | - | 현실적인 조립착수일 생성 (클러스터링 방식) |
| calculate_tact_times | (self, block) | - | Tact Time 계산 (enhanced_environment/utils.py의 최적화된 공식 사용) |
| _calibrate_saw_time | (self, raw_time: float, stage: str) | float | 실적 기반 스케일/클램프로 SAW 시간을 조정한다. |
| generate_blocks | (self, n_blocks) | - | 여러 블록 생성 |
| generate_100_blocks_with_ps_pairs_correct | (self) | - | 기존 호환성을 위한 래퍼 함수 - 기본값으로 100개 블록 생성 |
| _sample_random_block_type_count | (self, key: str) | int | 지정된 키에 대한 랜덤 수량 샘플링 |
| _clamp_reserved_block_counts | (self, total_blocks: int, ps_pairs: int, sub_groups: int) | Tuple[int, int] | 총 블록 수를 초과하지 않도록 예약 블록 수량 보정 |
| generate_blocks_with_ps_pairs_configurable | (self, total_blocks, ps_pairs_count, subassembly_groups) | - | 완벽한 Flow: 기본 생성 → P/S 쌍 복사 → 별판 복사 → 초과분 제거 |
| _log_workshop_summary | (self, blocks: List[dict]) | - | 생성된 데이터의 작업장별 핵심 정보를 로그로 출력 |
| _generate_single_base_date_for_all | (self) | - | 기준일을 연중 임의일(365일)로 복원 |
| _generate_individual_date_from_base | (self, base_date) | - | 개별 블록용: 기준일에서 +0~7일 범위로 날짜 생성 |
| _assign_workshop_and_dates | (self, blocks: List[dict]) | - | 생성된 블록들에 현실적인 작업장/조립착수일을 부여 |
| _synchronize_ps_pairs | (self, blocks: List[dict]) | - | P/S 쌍의 조립 작업장과 조립착수일을 강제로 맞춘다 |
| _synchronize_subassembly_groups | (self, blocks: List[dict]) | - | 별판 그룹 구성원이 동일한 조립 메타데이터를 갖도록 정렬 |
| _validate_pairing_integrity | (self, blocks: List[dict]) | - | 별판 및 P/S 쌍이 정의한 규칙을 만족하는지 검증 |
| _build_block_groups | (self, blocks: List[dict]) | List[dict] | P/S 쌍 및 별판 그룹을 묶어 처리 |
| _copy_block_characteristics | (self, source_block, target_block) | - | 소스 블록의 특성을 타겟 블록에 복사 (블록 특성, 조립착수일, Tact Time 포함) |
| _analyze_final_assembly_dates | (self, df, base_date) | - | 최종 조립착수일 분석 |
| compare_correlation_matrices | (self, generated_data) | - | 실제 데이터와 생성 데이터의 상관관계 비교 (final_block_generation_method.py에서 가져옴) |
| create_correlation_heatmap | (self, real_corr, generated_corr, filename) | - | 상관관계 히트맵 생성 (final_block_generation_method.py에서 가져옴) |
| save_generated_blocks | (self, df, filename) | - | 생성된 블록 저장 |
| generate_and_validate | (self, n_blocks, save_filename) | - | 블록 생성 + 상관관계 검증 + 저장 (통합 메서드) |
| generate_ship_number | (self) | - | 호선번호 생성 (예: PROJ_1, PROJ_2, PROJ_15) |
| generate_sub_assembly_number | (self) | - | 소조번호 생성 (예: TP_1, TP_2) |
| generate_block_name | (self, base_number, block_type) | - | 블록번호 생성 (예: BLK_1P, BLK_2S, BLK_3C) |
| _generate_common_base_date | (self) | - | 전체 100개 블록을 위한 공통 기준일 생성 |
| _generate_clustered_assembly_date | (self, base_date) | - | 공통 기준일 기반 클러스터링된 조립착수일 생성 (±7일 내) |
| generate_single_block_without_date | (self) | - | 조립착수일 제외하고 단일 블록 생성 (기존 generate_single_block에서 날짜 부분만 제거) |
| _analyze_clustered_assembly_dates | (self, df, base_date) | - | 클러스터링된 조립착수일 분석 |

#### 함수 목록

| 함수 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _is_truthy | (value) | bool | 설명 없음 |
| debug_print | (*args, **kwargs) | - | 디버깅 출력 제어 |
| info_print | (*args, **kwargs) | - | 정보 출력 제어 |

<!-- /AUTO-GENERATED -->
