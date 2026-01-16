# enhanced_environment/bay/assigner 핸드북 (상세)

베이 배정 규칙을 구현한 폴더입니다. P7 계열 규칙과 부하 균형을 기준으로 베이를 결정합니다.

---

## 흐름(요약)
```
block
  └─ P7 우선순위 규칙
       └─ 베이 선택
            └─ 상태 반영
```

## 핵심 개념
- 폭, 론지, P/S 여부에 따라 강제 베이가 달라집니다.
- 부하 균형은 마지막 단계에서 적용됩니다.

## 파일별 상세
### `core.py`
- 역할: 해당 폴더의 중심 로직입니다.
- 입력: EnhancedBlock, BayStateTracker, PSBlockManager
- 출력: 선택 베이, 분석 dict
- 연결: preview_assign_bay는 상태 변경 없이 후보 평가에 사용됩니다.
- 주요 엔트리:
  - auto_assign_bay (함수): P7 우선순위를 적용해 베이를 결정하고 필요 시 상태를 갱신합니다.
  - preview_assign_bay (함수): 상태를 변경하지 않고 베이 선택을 미리 계산합니다.

## 주요 클래스/함수 (요약)
| 이름 | 분류 |
|---|---|
| auto_assign_bay | 함수 |
| preview_assign_bay | 함수 |

## 운영 팁
- return_analysis=True로 상세 분석 CSV를 생성할 수 있습니다.

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
| _finalize_analysis | (analysis: dict, bay_tracker: BayStateTracker, blocks_dict: dict) | - | 분석 정보 최종 정리 |
| _update_bay_state_after_assignment | (block: EnhancedBlock, assigned_bay: BayType, bay_tracker: BayStateTracker, logger, update_tracker: bool) | - | 베이 할당 후 자동으로 bay_tracker 상태 업데이트 |
| auto_assign_bay | (block: EnhancedBlock, constraint_config: ConstraintConfig, bay_tracker: BayStateTracker, ps_manager: PSBlockManager, blocks_dict: dict, logger, return_analysis: bool, update_tracker: bool) | - | P7 제약조건 기반 자동 베이 할당 (우선순위 순) + 자동 상태 업데이트 |
| preview_assign_bay | (block: EnhancedBlock, constraint_config: ConstraintConfig, bay_tracker: BayStateTracker, ps_manager: PSBlockManager, blocks_dict: dict, logger, return_analysis: bool) | - | 상태를 변경하지 않는 베이 배정 미리보기 |

<!-- /AUTO-GENERATED -->
