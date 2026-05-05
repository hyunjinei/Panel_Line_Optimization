# enhanced_environment/bay/makespan 핸드북 (상세)

완료 시간과 일자별 처리 시간을 계산합니다. 오후 착수 규칙을 반영한 시간 보정도 포함합니다.

---

## 흐름(요약)
```
schedule
  └─ 시간 누적
       └─ 오후 착수 보정
            └─ makespan 산출
```

## 핵심 개념
- 오후 착수 블록은 다음날 처리로 넘어갈 수 있습니다.

## 파일별 상세
### `core.py`
- 역할: 해당 폴더의 중심 로직입니다.
- 입력: 스케줄 결과, 시작 시간
- 출력: makespan, 일자별 처리량
- 연결: 환경과 평가 리포트에서 공통으로 사용됩니다.
- 주요 엔트리:
  - calculate_makespan (함수): 일자별 처리 시간을 누적해 makespan을 계산합니다.

## 주요 클래스/함수 (요약)
| 이름 | 분류 |
|---|---|
| calculate_makespan | 함수 |

## 운영 팁
- 실제 생산 규칙에 맞게 보정 규칙을 조정합니다.

## 코드/API 상세 (자동 추출)
<!-- AUTO-GENERATED: DO NOT EDIT BELOW -->

### 파일: `__init__.py`

- 모듈 설명: 설명 없음

#### 클래스 없음

#### 함수 없음

### 파일: `core.py`

- 모듈 설명: 설명 없음

#### 클래스 없음

#### 함수 목록

| 함수 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| _apply_afternoon_guard_shift | (i: int, block, ct_common, ct_branch_a, ct_branch_b, assigned_bay: BayType, afternoon_guard_blocks: Set[int]) | None | legacy no-op helper. P6#1,2,3 제거 이후 인터페이스 호환용. |
| calculate_makespan | (blocks_dict: dict, sequence: List[int], branch_assignments: Dict[int, BayType], previous_machine_state: Dict, afternoon_guard_blocks: Optional[Set[int]]) | Tuple[float, Dict] | 주어진 순서와 베이 할당에 대한 makespan 계산 (8개 공정 구조) |

<!-- /AUTO-GENERATED -->
