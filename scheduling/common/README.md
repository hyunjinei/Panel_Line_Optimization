# scheduling/common 핸드북 (상세)

휴리스틱 공통 규칙과 결과 생성 로직이 모여 있습니다.

---

## 흐름(요약)
```
선택 규칙
  └─ 스케줄 처리
       └─ 결과 빌드
```

## 핵심 개념
- SPT/LPT/SEAM_MIN 규칙이 여기에 정의됩니다.

## 파일별 상세
### `defaults.py`
- 역할: 기본 상수와 경로를 보관합니다.
- 입력: 후보 목록, 선택 규칙
- 출력: 선택 id, 결과 구조
- 연결: assembly_start와 start_date에서 함께 사용합니다.
- 주요 엔트리:
  - (공개 엔트리 없음)

### `process_schedule.py`
- 역할: 공정 시간과 처리 단위를 정의합니다.
- 입력: 후보 목록, 선택 규칙
- 출력: 선택 id, 결과 구조
- 연결: assembly_start와 start_date에서 함께 사용합니다.
- 주요 엔트리:
  - save_detailed_process_schedule (함수): 처리 로직을 수행합니다.

### `result_builders.py`
- 역할: 스케줄 결과 구조를 정의합니다.
- 입력: 후보 목록, 선택 규칙
- 출력: 선택 id, 결과 구조
- 연결: assembly_start와 start_date에서 함께 사용합니다.
- 주요 엔트리:
  - create_block_result (함수): 핵심 로직을 수행합니다.

### `selection_rules.py`
- 역할: SPT/LPT/SEAM_MIN 규칙을 정의합니다.
- 입력: 후보 목록, 선택 규칙
- 출력: 선택 id, 결과 구조
- 연결: assembly_start와 start_date에서 함께 사용합니다.
- 주요 엔트리:
  - select_block_id (함수): 선택 로직을 수행합니다.

## 주요 클래스/함수 (요약)
| 이름 | 분류 |
|---|---|
| save_detailed_process_schedule | 함수 |
| create_block_result | 함수 |
| select_block_id | 함수 |

## 운영 팁
- 선택 규칙 변경 시 평가 비교 결과가 바뀝니다.

## 코드/API 상세 (자동 추출)
<!-- AUTO-GENERATED: DO NOT EDIT BELOW -->

### 파일: `__init__.py`

- 모듈 설명: Common scheduling helpers.

#### 클래스 없음

#### 함수 없음

### 파일: `defaults.py`

- 모듈 설명: Shared defaults for scheduling modules.

#### 클래스 없음

#### 함수 없음

### 파일: `process_schedule.py`

- 모듈 설명: Shared process schedule CSV helpers.

#### 클래스 없음

#### 함수 목록

| 함수 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _build_process_records | (date_key: str, sequence: List[int], bay_assignments: Dict, ct_tables: Dict, blocks_dict: Dict, date_start_time: datetime) | List[Dict] | Build per-process schedule records (shared logic). |
| save_detailed_process_schedule | (date_key: str, sequence: List[int], bay_assignments: Dict, ct_tables: Dict, blocks_dict: Dict, date_start_time: datetime, filename: str, verbose: bool, label: str) | None | Save per-process schedule CSV with common formatting. |

### 파일: `result_builders.py`

- 모듈 설명: Shared result builders for scheduling outputs.

#### 클래스 없음

#### 함수 목록

| 함수 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| create_block_result | (block, assigned_bay, sequence, bay_analysis, violations, date_str, start_time, end_time, makespan_minutes, makespan_hours, total_completion_time, date_start_time, actual_machine_2_start_time) | Dict | 블록 결과 생성 (공통 포맷). |

### 파일: `selection_rules.py`

- 모듈 설명: Selection rules for scheduling candidates (SPT/LPT/SEAM/priority/random).

#### 클래스 없음

#### 함수 목록

| 함수 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| select_block_id | (available_ids: List[int], blocks_dict: Dict, selection_method: str) | Tuple[int, str] | Select a block id and return (id, reason). |

<!-- /AUTO-GENERATED -->
