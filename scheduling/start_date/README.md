# scheduling/start_date 핸드북 (상세)

착수일 기반 휴리스틱 경로가 있는 폴더입니다. PFSP 경량 마스킹을 사용합니다.

---

## 흐름(요약)
```
blocks
  └─ 착수일 마스킹
       └─ 선택 규칙
            └─ 결과 저장
```

## 핵심 개념
- 완화 순서는 start_date 전용 리스트를 사용합니다.

## 파일별 상세
### `action_sequence_착수일기준휴리스틱.py`
- 역할: 보조 기능을 모아 둔 파일입니다.
- 입력: 블록 목록
- 출력: 스케줄 결과
- 연결: action_sequence_착수일기준휴리스틱.py가 중심입니다.
- 주요 엔트리:
  - save_detailed_process_schedule (함수): 처리 로직을 수행합니다.
  - create_actionmasking_schedule (함수): 착수일 기반 스케줄을 생성합니다.

## 주요 클래스/함수 (요약)
| 이름 | 분류 |
|---|---|
| save_detailed_process_schedule | 함수 |
| create_actionmasking_schedule | 함수 |

## 운영 팁
- 실적 재현과 혼동하지 않도록 경로를 구분합니다.

## 코드/API 상세 (자동 추출)
<!-- AUTO-GENERATED: DO NOT EDIT BELOW -->

### 파일: `__init__.py`

- 모듈 설명: Start-date scheduling methods.

#### 클래스 없음

#### 함수 없음

### 파일: `action_sequence_착수일기준휴리스틱.py`

- 모듈 설명: 설명 없음

#### 클래스 없음

#### 함수 목록

| 함수 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _to_violation_objects | (violations, block_id: Optional[int]) | List[ConstraintViolation] | 설명 없음 |
| save_detailed_process_schedule | (date_key: str, sequence: List[int], bay_assignments: Dict, ct_tables: Dict, blocks_dict: Dict, date_start_time: datetime) | - | 공정별 상세 스케줄링 CSV 저장 (공통 유틸 위임). |
| create_actionmasking_schedule | (excel_path: str, limit_days: Optional[int], reset_bay_continuity: bool, reset_worktime_daily: bool) | - | Step-by-Step Action Masking 기반 스케줄 생성 |

<!-- /AUTO-GENERATED -->
