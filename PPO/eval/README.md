# PPO/eval 핸드북 (상세)

평가 실행과 방법 비교를 담당합니다. RL, SPT/LPT/SEAM, 엑셀/착수일 비교를 처리합니다.

---

## 흐름(요약)
```
모델 로드
  └─ 샘플링
       └─ 결과 집계
            └─ CSV 저장
```

## 핵심 개념
- best-of-k 샘플링으로 RL 결과를 선택합니다.

## 파일별 상세
### `files.py`
- 역할: 보조 기능을 모아 둔 파일입니다.
- 입력: 모델 경로, 데이터셋, 평가 모드
- 출력: 평가 CSV, 상세 로그
- 연결: runner.py가 평가 진입점입니다.
- 주요 엔트리:
  - rename_excel_detailed_csv_files (함수): 핵심 로직을 수행합니다.
  - rename_actionmasking_detailed_csv_files (함수): 마스킹/필터링을 수행합니다.
  - rename_detailed_csv_files (함수): 핵심 로직을 수행합니다.

### `helpers.py`
- 역할: 공통 헬퍼 함수 모음입니다.
- 입력: 모델 경로, 데이터셋, 평가 모드
- 출력: 평가 CSV, 상세 로그
- 연결: runner.py가 평가 진입점입니다.
- 주요 엔트리:
  - set_random_seeds (함수): 설정을 갱신합니다.
  - sanitize_label_for_filename (함수): 핵심 로직을 수행합니다.
  - apply_generated_data_variant (함수): 핵심 로직을 수행합니다.

### `methods.py`
- 역할: 평가 방법과 비교 로직을 담습니다.
- 입력: 모델 경로, 데이터셋, 평가 모드
- 출력: 평가 CSV, 상세 로그
- 연결: runner.py가 평가 진입점입니다.
- 주요 엔트리:
  - run_excel_heuristic (함수): 실행 진입점 또는 루프를 수행합니다.
  - run_actionmasking_heuristic (함수): 실행 진입점 또는 루프를 수행합니다.
  - run_spt_heuristic (함수): 실행 진입점 또는 루프를 수행합니다.
  - run_lpt_heuristic (함수): 실행 진입점 또는 루프를 수행합니다.
  - run_seam_min_heuristic (함수): 실행 진입점 또는 루프를 수행합니다.
  - run_rl_evaluation (함수): 실행 진입점 또는 루프를 수행합니다.

### `runner.py`
- 역할: 실행 진입점과 CLI 처리를 담당합니다.
- 입력: 모델 경로, 데이터셋, 평가 모드
- 출력: 평가 CSV, 상세 로그
- 연결: runner.py가 평가 진입점입니다.
- 주요 엔트리:
  - main (함수): 핵심 로직을 수행합니다.

### `train_evaluation.py`
- 역할: 보조 기능을 모아 둔 파일입니다.
- 입력: 모델 경로, 데이터셋, 평가 모드
- 출력: 평가 CSV, 상세 로그
- 연결: runner.py가 평가 진입점입니다.
- 주요 엔트리:
  - comprehensive_evaluation (함수): 핵심 로직을 수행합니다.

## 주요 클래스/함수 (요약)
| 이름 | 분류 |
|---|---|
| rename_excel_detailed_csv_files | 함수 |
| rename_actionmasking_detailed_csv_files | 함수 |
| rename_detailed_csv_files | 함수 |
| set_random_seeds | 함수 |
| sanitize_label_for_filename | 함수 |
| apply_generated_data_variant | 함수 |
| run_excel_heuristic | 함수 |
| run_actionmasking_heuristic | 함수 |
| run_spt_heuristic | 함수 |
| run_lpt_heuristic | 함수 |
| run_seam_min_heuristic | 함수 |
| run_rl_evaluation | 함수 |

## 운영 팁
- 샘플 수가 많을수록 시간이 늘어납니다.

## 코드/API 상세 (자동 추출)
<!-- AUTO-GENERATED: DO NOT EDIT BELOW -->

### 파일: `__init__.py`

- 모듈 설명: 설명 없음

#### 클래스 없음

#### 함수 없음

### 파일: `files.py`

- 모듈 설명: 평가 결과 파일 정리 유틸 (runner.py에서 분리).

#### 클래스 없음

#### 함수 목록

| 함수 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| rename_excel_detailed_csv_files | (date_keys: List[str], result_folder: Optional[str], plan_label: Optional[str]) | None | 엑셀 방식에서 생성된 상세 CSV 파일들을 이름 변경 |
| rename_actionmasking_detailed_csv_files | (date_keys: List[str], result_folder: Optional[str]) | None | 착수일기준휴리스틱에서 생성된 상세 CSV 파일들을 이름 변경 |
| rename_detailed_csv_files | (method_name: str, date_keys: List[str], result_folder: Optional[str]) | None | 생성된 상세 CSV 파일들을 방법별로 이름 변경 |

### 파일: `helpers.py`

- 모듈 설명: 평가 유틸 모음 (runner.py에서 분리).

#### 클래스 없음

#### 함수 목록

| 함수 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _normalize_method_name | (name: str) | str | 설명 없음 |
| _get_selected_methods | () | Optional[set] | 설명 없음 |
| _should_run | (method_key: str, selected: Optional[set], default: bool) | bool | 설명 없음 |
| set_random_seeds | (seed: int) | - | 모든 라이브러리의 랜덤 시드 설정 |
| sanitize_label_for_filename | (label: Optional[str]) | str | 파일명에 사용할 수 있도록 계획 시트 라벨 정리 |
| apply_generated_data_variant | (df: pd.DataFrame, seam_scale: float, tact_time_scale: float, length_scale: float, width_scale: float, thickness_scale: float) | pd.DataFrame | 생성 데이터의 심수/용접장/처리시간/물리 스케일을 조정 |
| _sample_util_bucket | (buckets: List[Dict[str, object]]) | Tuple[str, float, Tuple[float, float]] | 설명 없음 |
| _compute_spread_days | (total_blocks: int, util: float, max_daily_blocks: int, min_days: int) | int | 설명 없음 |
| _adjust_reserved_counts | (total_blocks: int, ps_pairs: int, sub_groups: int, min_basic_blocks: int) | Tuple[int, int] | Ensure at least a minimal base block pool for pair/subassembly duplication. |

### 파일: `methods.py`

- 모듈 설명: 평가 방법 실행 모음 (runner.py에서 분리).

#### 클래스 없음

#### 함수 목록

| 함수 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _get_setting | (settings: Optional[Dict[str, object]], key: str, default: object) | object | 설명 없음 |
| run_excel_heuristic | (blocks: List, metadata: Dict, start_date: str, result_folder: str, plan_label: Optional[str]) | Tuple[List[Dict], Dict, List[Dict], object] | 엑셀 순번 기반 휴리스틱 실행 (MODE 2에서만 사용) |
| run_actionmasking_heuristic | (excel_path: str, result_folder: str) | Tuple[List[Dict], Dict, List[Dict], object] | 착수일기준휴리스틱 실행 (MODE 2에서만 사용) |
| _get_common_settings | (settings: Optional[Dict[str, object]]) | Tuple[int, int, bool] | 설명 없음 |
| run_spt_heuristic | (blocks: List, metadata: Dict, start_date: str, result_folder: str, settings: Optional[Dict[str, object]]) | Tuple[List[Dict], Dict, List[Dict], object] | 설명 없음 |
| run_lpt_heuristic | (blocks: List, metadata: Dict, start_date: str, result_folder: str, settings: Optional[Dict[str, object]]) | Tuple[List[Dict], Dict, List[Dict], object] | 설명 없음 |
| run_seam_min_heuristic | (blocks: List, metadata: Dict, start_date: str, result_folder: str, settings: Optional[Dict[str, object]]) | Tuple[List[Dict], Dict, List[Dict], object] | 설명 없음 |
| run_rl_evaluation | (blocks: List, metadata: Dict, start_date: str, result_folder: str, num_samples: int, settings: Optional[Dict[str, object]]) | Tuple[List[Dict], Dict, List[Dict], object] | RL 모델 평가 실행 (샘플링 후 최고 성능 선택) |

### 파일: `runner.py`

- 모듈 설명: 포괄적 평가: SPT, Random, RL 비교 및 상세 CSV 생성

#### 클래스 없음

#### 함수 목록

| 함수 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _apply_calendar_overrides | (metadata: Dict) | Dict | 설명 없음 |
| _should_run | (method_key: str, default: bool) | bool | 설명 없음 |
| _apply_evaluation_config_overrides | () | - | 설명 없음 |
| main | () | - | 메인 평가 함수 - integrated_learning_and_scheduling.py와 동일한 구조 |

### 파일: `train_evaluation.py`

- 모듈 설명: 종합 평가 함수: RL vs Random vs SPT/LPT/SEAM 비교

#### 클래스 없음

#### 함수 목록

| 함수 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| comprehensive_evaluation | (trainer, device, generator, episode, csv_path, eval_dir) | - | RL vs Random vs SPT 종합 평가 |

<!-- /AUTO-GENERATED -->
