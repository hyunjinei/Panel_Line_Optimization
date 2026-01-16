# 전체 실행 흐름 (main.py 기준)

이 문서는 **main.py 기준 실행 흐름**을 단계별로 설명합니다.  
현업 운영자와 개발자가 모두 이해할 수 있도록 **실행 순서, 설정 우선순위, 출력 경로**를 포함합니다.

---

## 1) 최고 수준 흐름

```
[사용자]
  └─ python main.py <mode> --config config.yaml
        ├─ config.yaml 로드
        ├─ env 섹션 → os.environ 반영
        ├─ runtime_config 저장
        └─ runpy로 모듈 실행
             ├─ PPO/train/runner.py
             ├─ PPO/eval/runner.py
             ├─ scheduling/*
             └─ replay 경로
```

---

## 2) main.py 단계별 상세

### 2.1 CLI 인자 파싱
- `--config`로 설정 파일 경로 지정
- `--yes`로 확인 질문 생략
- `--` 이후 인자는 내부 스크립트에 전달

### 2.2 config.yaml 로드
- `mode`, `data`, `constraints`, `calendar`, `train`, `eval`을 읽음
- 모드가 비어 있으면 기본값 사용

### 2.3 환경변수 반영
- `config.yaml`의 `env` 섹션을 `os.environ`에 적용
- 예시: 디버그 로그 출력 제어

### 2.4 runtime_config 저장
- `runtime_config.py`에 설정을 저장
- 이후 모든 모듈이 `get_runtime_config()`로 읽음

### 2.5 runpy 실행
- 모드에 따라 지정된 모듈을 실행
- 기존 스크립트 구조를 건드리지 않고 연결

---

## 3) 실행 모드별 경로

| 모드 | 실행 모듈 | 역할 |
|---|---|---|
| train | PPO/train/runner.py | PPO/self_label 학습 |
| eval | PPO/eval/runner.py | 휴리스틱/RL 평가 |
| compare | PPO/eval/runner.py | eval과 동일 |
| heuristic | scheduling/* | 휴리스틱 단독 실행 |
| replay | scheduling/performance_replay | 엑셀 실적 재현 |
| replay_start_date | replay → start_date | 재현 + 착수일 휴리스틱 |

---

## 4) config → 코드 반영 매핑 (핵심)

| config 경로 | 실제 반영 위치 | 의미 |
|---|---|---|
| data.excel_path | DataConverter | 엑셀 입력 경로 |
| data.sheet | DataConverter | 엑셀 시트명 |
| constraints.relax_order_* | masking/core.py | 완화 순서 |
| constraints.strict_rules | masking/core.py | 완화 금지 목록 |
| calendar.* | calendar_manager | 휴무/반일/점심 |
| eval.methods | PPO/eval/runner.py | 실행할 방법 목록 |
| evaluation.* | PPO/eval/runner.py | MODE/샘플 수 등 |
| train.cli_args | PPO/train/runner.py | 학습 인자 전달 |

---

## 5) 실행 예시

### 평가
```bash
python main.py eval --config config.yaml --yes
```

### 학습 (PPO)
```bash
python main.py train --config config.yaml --yes -- --mode ppo
```

### 학습 (self_label)
```bash
python main.py train --config config.yaml --yes -- --mode self_label
```

---

## 6) 모드별 출력 경로 요약

| 모드 | 주요 출력 |
|---|---|
| train | `PPO/result/log/`, `PPO/result/models/` |
| eval | `PPO/<날짜_시간>_seed*/` |
| heuristic | eval과 동일한 결과 CSV |
| replay | 실적 재현 CSV |

---

## 7) 설정 우선순위

1. `config.yaml`
2. `--` 이후 전달된 CLI 인자
3. 코드 내부 기본값

즉, **CLI 인자가 있으면 config보다 우선**됩니다.

---

## 8) 상대 경로 규칙

- `config.yaml`은 **저장소 루트 기준** 상대 경로 권장
- `data.excel_path`도 동일하게 처리
- 내부 경로는 runtime_config에서 정규화

---

## 9) 실무 체크리스트

- config.yaml 경로가 올바른가
- 엑셀 파일 경로가 repo 기준 상대 경로인가
- relax_order 목록이 masking/core의 키와 일치하는가
- strict_rules에 반드시 지킬 제약이 들어 있는가
- eval.methods가 실제로 원하는 방법만 포함하는가

