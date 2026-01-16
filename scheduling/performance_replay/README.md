# scheduling/performance_replay 핸드북 (상세)

실적 엑셀을 순번대로 재현하는 경로입니다. 실제 작업 순서를 재현할 때 사용합니다.

---

## 흐름(요약)
```
엑셀 실적
  └─ 순번 재현
       └─ 스케줄 생성
            └─ 결과 저장
```

## 핵심 개념
- 실적 재현은 평가 기준으로 사용될 수 있습니다.

## 파일별 상세
### `excel_실적데이터_순번기반시퀀싱.py`
- 역할: 보조 기능을 모아 둔 파일입니다.
- 입력: 실적 엑셀
- 출력: 재현 스케줄
- 연결: 엑셀 순번 로직은 변경하지 않는 것이 원칙입니다.
- 주요 엔트리:
  - apply_plan_sequence_from_excel (함수): 엑셀 순번대로 스케줄을 재현합니다.
  - load_excel_blocks_with_plan (함수): 핵심 로직을 수행합니다.
  - save_detailed_process_schedule_excel (함수): 처리 로직을 수행합니다.
  - save_detailed_schedule_info_excel (함수): 핵심 로직을 수행합니다.
  - save_detailed_bay_info_excel (함수): 핵심 로직을 수행합니다.
  - create_makespan_schedule (함수): 핵심 로직을 수행합니다.

## 주요 클래스/함수 (요약)
| 이름 | 분류 |
|---|---|
| apply_plan_sequence_from_excel | 함수 |
| load_excel_blocks_with_plan | 함수 |
| save_detailed_process_schedule_excel | 함수 |
| save_detailed_schedule_info_excel | 함수 |
| save_detailed_bay_info_excel | 함수 |
| create_makespan_schedule | 함수 |

## 운영 팁
- 엑셀 스키마 변경 시 변환이 깨질 수 있습니다.

## 코드/API 상세 (자동 추출)
<!-- AUTO-GENERATED: DO NOT EDIT BELOW -->

### 파일: `__init__.py`

- 모듈 설명: Performance replay scheduling methods.

#### 클래스 없음

#### 함수 없음

### 파일: `excel_실적데이터_순번기반시퀀싱.py`

- 모듈 설명: 설명 없음

#### 클래스 없음

#### 함수 목록

| 함수 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _normalise_plan_column | (text: object) | str | 간단한 문자열 정규화 (양 끝 공백 제거, NaN → 빈 문자열). |
| _parse_plan_date | (value: object) | Optional[datetime] | 수기계획/CP 시트의 착수일 값을 datetime으로 변환. |
| apply_plan_sequence_from_excel | (blocks: List, excel_path: str, plan_sheet: Optional[str], plan_priority: Optional[Tuple[str, ...]], suppress_missing_message: bool) | Optional[Tuple[str, ...]] | 외부 순번 시트를 읽어 블록 객체의 sequence_number 및 관련 정보를 업데이트. |
| load_excel_blocks_with_plan | (excel_path: str, plan_sheet: Optional[str], plan_priority: Optional[Tuple[str, ...]]) | - | 설명 없음 |
| save_detailed_process_schedule_excel | (date_key: str, sequence: List[int], bay_assignments: Dict, ct_tables: Dict, blocks_dict: Dict, date_start_time: datetime) | - | 공정별 상세 스케줄링 CSV 저장 (엑셀 방식) |
| save_detailed_schedule_info_excel | (date_key: str, rows: List[Dict]) | - | 엑셀 순번 방식의 블록별 일정 요약 CSV 저장 |
| save_detailed_bay_info_excel | (date_key: str, rows: List[Dict]) | - | 엑셀 순번 방식의 베이 배정 상세 CSV 저장 |
| create_makespan_schedule | (blocks, metadata) | - | 실제 makespan 계산 및 CSV 생성 - 날짜별 상세 분석 |

<!-- /AUTO-GENERATED -->
