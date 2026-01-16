# enhanced_environment/constraints 핸드북 (상세)

제약 설정과 기본 프리셋을 관리합니다. 어떤 제약을 켜고 끌지 여기서 결정합니다.

---

## 흐름(요약)
```
ConstraintConfig
  └─ presets
       └─ managers/masking/env
```

## 핵심 개념
- 프리셋을 바꾸면 전 경로 동작이 바뀝니다.

## 파일별 상세
### `config.py`
- 역할: 보조 기능을 모아 둔 파일입니다.
- 입력: 설정 플래그와 하이퍼파라미터
- 출력: ConstraintConfig 인스턴스
- 연결: masking/core.py와 pbs_env/core.py에서 읽습니다.
- 주요 엔트리:
  - ConstraintConfig (클래스): 핵심 로직을 수행합니다.

### `presets.py`
- 역할: 보조 기능을 모아 둔 파일입니다.
- 입력: 설정 플래그와 하이퍼파라미터
- 출력: ConstraintConfig 인스턴스
- 연결: masking/core.py와 pbs_env/core.py에서 읽습니다.
- 주요 엔트리:
  - get_all_enabled_config (함수): 상태/정보를 조회합니다.
  - get_all_disabled_config (함수): 상태/정보를 조회합니다.
  - get_basic_constraints_only_config (함수): 상태/정보를 조회합니다.
  - get_panel_work_only_config (함수): 상태/정보를 조회합니다.
  - get_saw_work_only_config (함수): 상태/정보를 조회합니다.
  - get_longi_work_only_config (함수): 상태/정보를 조회합니다.

## 주요 클래스/함수 (요약)
| 이름 | 분류 |
|---|---|
| ConstraintConfig | 클래스 |
| get_all_enabled_config | 함수 |
| get_all_disabled_config | 함수 |
| get_basic_constraints_only_config | 함수 |
| get_panel_work_only_config | 함수 |
| get_saw_work_only_config | 함수 |
| get_longi_work_only_config | 함수 |

## 운영 팁
- 완화 순서는 masking에서만 적용됩니다.

## 코드/API 상세 (자동 추출)
<!-- AUTO-GENERATED: DO NOT EDIT BELOW -->

### 파일: `__init__.py`

- 모듈 설명: Constraint configuration and presets (re-exported).

#### 클래스 없음

#### 함수 없음

### 파일: `config.py`

- 모듈 설명: Constraint configuration model.

#### 클래스 목록

| 클래스 | 상속 | 설명 |
|---|---|---|
| ConstraintConfig | 없음 | 제약조건 설정 클래스 |

##### 클래스: `ConstraintConfig` 메서드

| 메서드 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| __post_init__ | (self) | None | 런타임 설정 파일 기반 오버라이드 적용. |
| is_constraint_enabled | (self, constraint_id: str) | bool | 특정 제약조건 활성 여부 확인. |
| set_constraint_scope | (self, scope: str) | None | 현재 제약 스코프 설정 (start_date/assembly). |
| _normalize_constraint_keys | (self, raw_items: List[str]) | List[str] | 한글/영문 혼용 제약 키를 constraint id 리스트로 변환. |
| disable_all_constraints | (self) | None | 모든 제약조건 비활성화. |
| enable_all_constraints | (self) | None | 모든 제약조건 활성화. |
| disable_group | (self, group: str) | None | 특정 그룹 제약조건 비활성화. |
| enable_group | (self, group: str) | None | 특정 그룹 제약조건 활성화. |
| get_enabled_constraints | (self) | List[str] | 현재 활성화된 제약조건 목록. |
| get_capacity_hyperparams | (self) | Dict[str, any] | 용량 관리 하이퍼파라미터 반환. |
| get_summary | (self) | Dict[str, any] | 설정 상태 요약. |

#### 함수 없음

### 파일: `presets.py`

- 모듈 설명: Preset helpers for ConstraintConfig.

#### 클래스 없음

#### 함수 목록

| 함수 | 시그니처 | 반환 | 설명 |
|---|---|---|---|
| get_all_enabled_config | () | ConstraintConfig | 모든 제약조건 활성화 설정. |
| get_all_disabled_config | () | ConstraintConfig | 모든 제약조건 비활성화 설정. |
| get_basic_constraints_only_config | () | ConstraintConfig | 기본 제약조건만 활성화 (P5#1, P5#13, P7#2). |
| get_panel_work_only_config | () | ConstraintConfig | 판계 작업 제약조건만 활성화. |
| get_saw_work_only_config | () | ConstraintConfig | SAW 공정 제약조건만 활성화. |
| get_longi_work_only_config | () | ConstraintConfig | 론지 취부 제약조건만 활성화. |

<!-- /AUTO-GENERATED -->
