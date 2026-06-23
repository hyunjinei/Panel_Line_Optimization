# 판넬라인 시각화 데이터 안내

이 폴더는 Unity 또는 별도 시각화 도구에서 판넬라인 스케줄 결과를 그리기 위한 전달용 데이터입니다.  
시각화 담당자가 바로 쓰기 쉽도록 기본 CSV는 필요한 컬럼만 남긴 slim 형태로 정리했습니다.

기존 전체 컬럼 CSV는 삭제하지 않고 같은 폴더에 `original_*.csv` 이름으로 보존했습니다.  
따라서 Unity는 기본 CSV를 읽고, 연구 검증이나 디버그가 필요할 때만 `original_*.csv`를 열면 됩니다.

## 0. CSV를 다시 생성했을 때 정리 명령어

`unity/by_case`에서 전달용 폴더를 다시 만들 때는 아래 명령어를 사용합니다.

```bash
python3 scripts/build_unity_delivery.py --source unity/by_case --output unity_for_delivery
```

검증만 하고 싶으면 아래 명령어를 사용합니다.

```bash
python3 scripts/build_unity_delivery.py --output unity_for_delivery --check-only
```

처리 방식은 다음과 같습니다.

- `unity/by_case`의 전체 컬럼 파일을 `original_*.csv`로 복사합니다.
- 같은 위치에 Unity가 바로 읽을 slim CSV를 생성합니다.
- `expanded_verification.csv`는 기본 시각화에 필요 없으므로 `original_expanded_verification.csv`로만 보존합니다.

## 1. 폴더 구조

```text
unity_for_delivery/
  README.md
  column_spec.csv
  b20_base_r1/
    README.md
    case_graph_values_all_methods.csv
    original_case_graph_values_all_methods.csv
    SPT/
      process_gantt.csv
      block_results.csv
      original_process_gantt.csv
      original_block_results.csv
      original_expanded_verification.csv
    MSF/
    LPT/
    GA/
    Proposed/
  b20_base_r2/
  ...
```

- `b20_base_r1` 같은 폴더 하나가 하나의 문제 케이스입니다.
- `SPT`, `MSF`, `LPT`, `GA`, `Proposed`는 같은 문제를 서로 다른 방법으로 푼 결과입니다.
- `*.csv`는 시각화용 slim 파일입니다.
- `original_*.csv`는 전체 컬럼이 들어 있는 보존용 원본입니다.
- `expanded_verification.csv`는 기본 시각화에 필요 없어서 활성 파일에서는 제외했고, `original_expanded_verification.csv`로만 보존했습니다.

## 2. 어떤 CSV를 쓰면 되는가

Unity에서 기본 화면을 만들 때는 아래 3개 파일만 읽으면 됩니다.

| 목적 | 사용하는 파일 | 필수 여부 |
|---|---|---:|
| 공정 흐름, 간트 차트, 블록 이동 애니메이션 | `{case}/{method}/process_gantt.csv` | 필수 |
| 블록 리스트, 클릭 상세, 블록 속성 표시 | `{case}/{method}/block_results.csv` | 선택, 상세 화면이면 사용 |
| 방법별 비교, 상단 요약 지표 | `{case}/case_graph_values_all_methods.csv` | 선택, 비교 화면이면 사용 |

정리하면 `process_gantt.csv`가 메인 입력입니다.  
하지만 블록 상세 정보나 방법별 성능 비교까지 보여줄 거면 `block_results.csv`, `case_graph_values_all_methods.csv`도 같이 씁니다.

주의: 이 전달 데이터는 최종 스케줄 결과와 공정 로그를 위한 데이터입니다.  
step별 candidate mask, action masking on/off 상태, 선택 불가 후보 사유 같은 action masking trace는 포함하지 않습니다.  
따라서 action masking 과정을 시각화하려면 고정 예시를 쓰거나, 별도의 masking trace CSV를 추가로 생성해야 합니다.

### 공정 흐름, 간트 차트, 애니메이션

사용 파일:

```text
{case}/{method}/process_gantt.csv
```

예시:

```text
b20_base_r1/Proposed/process_gantt.csv
```

이 파일이 시각화의 핵심입니다.  
블록별 공정 막대, 시작 시간, 종료 시간, 공정명, 라인 정보가 들어 있습니다.

### 블록별 클릭 상세

사용 파일:

```text
{case}/{method}/block_results.csv
```

이 파일은 블록 단위 요약 정보입니다.  
블록명, 배정 line, 블록 속성, 블록 단위 위반 수 정도만 남겼습니다.

### 방법별 성능 비교

사용 파일:

```text
{case}/case_graph_values_all_methods.csv
```

이 파일은 같은 문제 케이스에서 `SPT`, `MSF`, `LPT`, `GA`, `Proposed`의 전체 성능을 비교할 때 씁니다.  
주요 값은 `makespan_hours`와 `violations`입니다.

## 3. 시각화에 필요한 컬럼 구분

더 자세한 컬럼별 설명은 `column_spec.csv`에 있습니다.

### 간트 차트와 공정 애니메이션 필수 컬럼

`process_gantt.csv`에서 사용합니다.

| 컬럼 | 필요 여부 | 설명 |
|---|---:|---|
| `grid_case_id` | O | 문제 케이스 이름입니다. 예: `b20_base_r1` |
| `method_label` | O | 방법 이름입니다. 예: `SPT`, `MSF`, `LPT`, `GA`, `Proposed` |
| `block_id` | O | 블록 식별자입니다. Unity 오브젝트 키로 쓰기 좋습니다. |
| `block_name` | O | 화면에 표시할 블록명입니다. |
| `sequence` | O | 해당 방법에서 블록이 투입된 순서입니다. |
| `process_name` | O | 공정명입니다. 화면의 공정 박스, 레인 이름, 툴팁에 사용합니다. |
| `process_index` | O | 한 블록 안에서 공정 순서를 정렬할 때 씁니다. |
| `start_time` | O | 공정 시작 시간입니다. |
| `end_time` | O | 공정 종료 시간입니다. |
| `duration_min` | O | 공정 소요 시간입니다. 단위는 분입니다. |
| `assigned_bay` | O | 컬럼명은 legacy name입니다. 화면에서는 assigned line으로 해석합니다. downstream line 공정일 때 `35A` 또는 `36B`가 들어갑니다. shared upstream line 공정은 비어 있을 수 있습니다. |
| `machine_type` | O | 공정 line 구분입니다. 값은 `공통`, `베이35A`, `베이36B`입니다. 화면 표기는 shared upstream line, downstream line A, downstream line B로 쓰면 됩니다. |

### 비교 대시보드 필수 컬럼

`case_graph_values_all_methods.csv`에서 사용합니다.

| 컬럼 | 필요 여부 | 설명 |
|---|---:|---|
| `grid_case_id` | O | 문제 케이스 이름입니다. |
| `method_label` | O | 비교 방법 이름입니다. |
| `total_blocks` | 선택 | 문제에 포함된 블록 수입니다. |
| `distribution_profile` | 선택 | 문제 생성 분포입니다. 예: `base`, `overload` |
| `makespan_hours` | O | 전체 완료 시간입니다. 기존 그래프에 사용한 값입니다. |
| `violations` | O | 전체 제약 위반 수입니다. 기존 그래프에 사용한 값입니다. |

### 블록 클릭 상세 컬럼

`block_results.csv`에서 사용합니다.

| 컬럼 | 필요 여부 | 설명 |
|---|---:|---|
| `grid_case_id` | O | 문제 케이스 이름입니다. |
| `method_label` | O | 방법 이름입니다. |
| `block_id` | O | `process_gantt.csv`와 연결하는 블록 식별자입니다. |
| `block_name` | O | 화면에 표시할 블록명입니다. |
| `am_sequence` | 선택 | 블록 투입 순서입니다. |
| `assigned_bay` | O | 컬럼명은 legacy name입니다. 화면에서는 최종 배정 line으로 표시합니다. |
| `total_time_min` | 선택 | 블록 전체 공정 시간입니다. 단위는 분입니다. |
| `port_starboard` | 선택 | P/S 정보입니다. |
| `assembly_type` | 선택 | 조립 구분입니다. |
| `line_group` | 선택 | 라인 그룹입니다. |
| `width_m` | 선택 | 블록 너비입니다. |
| `longi_count` | 선택 | 론지 수입니다. |
| `seam_count` | 선택 | 심수 또는 용접량 계열 값입니다. |
| `c_seam_count` | 선택 | C seam 수입니다. |
| `violations` | 선택 | 블록 단위 위반 수입니다. 경고 표시가 필요할 때 사용합니다. |
| `start_time` | 선택 | 블록 전체 시작 시간입니다. |
| `end_time` | 선택 | 블록 전체 종료 시간입니다. |
| `final_end_time` | 선택 | 블록 최종 완료 시간입니다. |

## 4. 기본 시각화에 제거한 컬럼

기본 간트 차트, 공정 흐름 애니메이션, 방법별 비교 화면에는 아래 컬럼들이 필요 없습니다.

- `constraint_ids`
- `constraint_families`
- `violation_details`
- `violations_primary_count`
- `violations_raw_count`
- `unity_process_row_source`
- `source_representative_block_id`
- 각종 내부 스케일 컬럼
- `expanded_verification.csv`의 상세 검증 컬럼 전체

이 정보들은 `original_*.csv`에 보존되어 있습니다.  
즉, 기본 화면은 slim CSV만 쓰고, 제약 위반 사유까지 설명하는 상세 분석 화면을 만들 때만 원본 파일을 열면 됩니다.

## 5. 공정명과 Line 의미

`process_gantt.csv`의 `process_name`, `machine_type`, `assigned_bay`를 같이 보면 됩니다.

용어는 아래처럼 통일합니다.

| CSV 내부 값 | 화면 표기 추천 | 의미 |
|---|---|---|
| `공통` | shared upstream line | 초반 공정이 공통으로 흐르는 라인입니다. |
| `베이35A` | downstream line A | 후반 공정이 병렬로 분기되는 라인 A입니다. |
| `베이36B` | downstream line B | 후반 공정이 병렬로 분기되는 라인 B입니다. |
| `assigned_bay` | assigned line | 컬럼명은 기존 코드 호환 때문에 bay로 남아 있지만, 화면에서는 line으로 표시합니다. |

### 공통 공정

`machine_type = 공통`인 공정입니다.  
블록이 특정 downstream line으로 갈라지기 전, 모든 블록이 공유하는 shared upstream line 공정으로 보면 됩니다.

| 공정명 | 설명 |
|---|---|
| `판계` | 초기 판넬 계열 준비 공정입니다. |
| `전면SAW` | 전면 SAW 용접 공정입니다. |
| `TurnOver` | 블록을 뒤집는 공정입니다. |
| `후면SAW` | 후면 SAW 용접 공정입니다. |
| `NC` | NC 절단 또는 가공 계열 공정입니다. |

공통 공정은 보통 `assigned_bay`가 비어 있습니다.  
화면에서는 shared upstream line 또는 상단 공통 구간으로 표시하면 됩니다.

### 분기 세부 라인 공정

`machine_type = 베이35A` 또는 `machine_type = 베이36B`인 공정입니다.  
공통 공정 이후 실제 downstream line별로 갈라지는 세부 작업 구간입니다.

| 공정명 | 라인 | 설명 |
|---|---|---|
| `베이35A 론지취부` | downstream line A | line A에서 론지 부재를 붙이는 공정입니다. |
| `베이35A 론지용접` | downstream line A | line A에서 론지를 용접하는 공정입니다. |
| `베이35A 문지취부` | downstream line A | line A 문지 계열 취부 공정입니다. |
| `베이35A 문지용접` | downstream line A | line A 문지 계열 용접 공정입니다. |
| `베이35A 수정` | downstream line A | line A 수정 작업입니다. |
| `베이36B 론지취부` | downstream line B | line B에서 론지 부재를 붙이는 공정입니다. |
| `베이36B 론지용접` | downstream line B | line B에서 론지를 용접하는 공정입니다. |
| `베이36B 문지취부` | downstream line B | line B 문지 계열 취부 공정입니다. |
| `베이36B 문지용접` | downstream line B | line B 문지 계열 용접 공정입니다. |
| `베이36B 수정` | downstream line B | line B 수정 작업입니다. |

downstream line 공정은 `assigned_bay`가 `35A` 또는 `36B`로 들어갑니다.  
Unity에서는 이 값을 기준으로 두 개의 parallel downstream lines를 만들면 됩니다.

## 6. Unity에서 추천하는 읽기 순서

1. 케이스 폴더를 선택합니다. 예: `b20_base_r1`
2. 방법 폴더를 선택합니다. 예: `Proposed`
3. `{case}/{method}/process_gantt.csv`를 읽습니다.
4. `start_time` 기준으로 정렬하고, 같은 블록 안에서는 `process_index` 기준으로 정렬합니다.
5. `machine_type`이 `공통`이면 shared upstream line에 그립니다.
6. `machine_type`이 `베이35A` 또는 `베이36B`이면 해당 parallel downstream line에 그립니다.
7. 블록 클릭 상세가 필요하면 같은 폴더의 `block_results.csv`를 `block_id`로 연결합니다.
8. 방법별 성능 비교가 필요하면 `{case}/case_graph_values_all_methods.csv`를 읽습니다.

## 7. 시간 컬럼 사용법

시간을 그릴 때는 `process_gantt.csv`의 아래 컬럼을 기준으로 쓰면 됩니다.

- `start_time`: 막대 시작점
- `end_time`: 막대 끝점
- `duration_min`: 막대 길이 검산용

`duration_min`은 분 단위입니다.  
`start_time`, `end_time`은 문자열로 저장되어 있으므로, Unity에서 날짜 시간 형식으로 파싱하거나 상대 시간으로 변환해서 사용하면 됩니다.

## 8. 제약 위반 수 사용법

전체 방법 비교 화면에서는 아래 값을 씁니다.

```text
case_graph_values_all_methods.csv -> violations
```

블록 경고 표시에는 아래 값을 씁니다.

```text
block_results.csv -> violations
```

제약 위반 사유, 제약 식별자, 원시 검증 로그까지 필요하면 slim CSV가 아니라 아래 원본 파일을 사용합니다.

```text
original_block_results.csv
original_expanded_verification.csv
```

정리하면 다음과 같습니다.

- 간트 차트만 만들 때: 제약 상세 컬럼 필요 없음
- 블록에 경고 표시만 할 때: `block_results.csv`의 `violations`만 사용
- 위반 사유까지 보여줄 때: `original_*.csv` 사용
