# PBS Scheduling 실행 안내 (main.py)

이 저장소는 판넬 라인 스케줄링을 위한 RL, 휴리스틱, 실적 재현을 통합 실행하는 구조입니다.  
현업 사용자는 **`main.py` 하나만 실행**하면 됩니다.

---

## 1) 실행 방법 (명령어 전체 정리)

아래는 `main.py` 기준 전체 명령어입니다.  
모든 실행은 `config.yaml`을 기본으로 읽습니다.

### 모듈/흐름 해설 문서
라인 단위 설명 대신 **모듈 단위 해설 + 코드 흐름 도식 + 표**로 정리했습니다.

- `docs/flow/index.md`

### 1.1 평가(추론)
```bash
python main.py eval --config config.yaml
python main.py eval --config config.yaml --yes
```

### 1.2 학습
```bash
python main.py train --config config.yaml
python main.py train --config config.yaml --yes
```

### 1.3 휴리스틱만 실행
```bash
python main.py heuristic --config config.yaml --yes
```

### 1.4 실적 재현(엑셀)
```bash
python main.py replay --config config.yaml --yes
```

### 1.5 실적 재현 + 착수일 휴리스틱 연속 실행
```bash
python main.py replay_start_date --config config.yaml --yes
```

### 1.6 compare (eval과 동일 동작)
```bash
python main.py compare --config config.yaml --yes
```

### 1.7 내부 스크립트로 추가 인자 전달
`--` 이후의 인자는 내부 실행 스크립트에 그대로 전달됩니다.

예시: 평가 모드 강제
```bash
python main.py eval --config config.yaml --yes -- --mode 1
```

예시: 학습 모드 지정
```bash
python main.py train --config config.yaml --yes -- --mode ppo
python main.py train --config config.yaml --yes -- --mode self_label
```

---

## 2) main.py 동작 흐름

1. `config.yaml` 로드  
2. 설정 유효성 검증  
3. 요약 출력  
4. 확인 질문 (기본)  
5. 해당 모드 실행 (runpy로 기존 스크립트 호출)

**즉, 기존 `PPO/eval/runner.py`와 동일하게 실행되며**  
main.py가 입력, 검증, 요약만 담당합니다.

---

## 3) 전체 구조 도식 (코드 흐름)

아래 도식은 `main.py` 기준 실행 흐름과 폴더 역할을 한눈에 보여줍니다.

```
[사용자 명령]
   |
   |  python main.py <mode> --config config.yaml
   v
┌────────────────────────────┐
│ main.py                     │
│  - config.yaml 로드         │
│  - 검증/요약 출력            │
│  - env 섹션 -> os.environ    │
│  - runpy로 모듈 실행         │
└──────────────┬─────────────┘
               |
               v
┌────────────────────────────┐
│ runtime_config.py           │
│  - set_runtime_config()     │
│  - get_runtime_config()     │
│  - 캘린더/제약 override 해석 │
└──────────────┬─────────────┘
               |
               v
      ┌────────┴─────────┐
      │                  │
      v                  v
┌────────────────────────────┐        ┌────────────────────────────┐
│ PPO/train/runner.py         │        │ PPO/eval/runner.py          │
│  - 학습 메인                │        │  - 평가 메인               │
│  - config 반영              │        │  - config 반영              │
└──────────────┬─────────────┘        └──────────────┬─────────────┘
               |                                    |
               v                                    v
┌────────────────────────────┐        ┌────────────────────────────┐
│ PPO/train/assembly_rollout.py│       │ PPO/eval/methods.py         │
│  - PPO/self_label 루프      │        │  - SPT/LPT/RL 실행           │
│  - LPT 비교 업데이트         │        │  - RL 평가, best 갱신        │
└──────────────┬─────────────┘        └──────────────┬─────────────┘
               |                                    |
               v                                    v
┌────────────────────────────┐        ┌────────────────────────────┐
│ PPO/models/single_step_actor│        │ scheduling/assembly_start   │
│  - RL 네트워크              │        │  - 조립착수일 스케줄링       │
└────────────────────────────┘        └────────────────────────────┘
```

---

## 3.1) 폴더별 README 바로가기

각 폴더에 간단한 설명과 핵심 파일을 정리한 README가 있습니다.

- `enhanced_environment/README.md`
- `enhanced_environment/masking/README.md`
- `enhanced_environment/constraints/README.md`
- `enhanced_environment/pbs_env/README.md`
- `enhanced_environment/bay/README.md`
- `PPO/README.md`
- `PPO/train/README.md`
- `PPO/eval/README.md`
- `scheduling/README.md`
- `utils/README.md`
- `environment/README.md`

### 모드별 실행 흐름

```
train 모드
main.py → PPO/train/runner.py → PPO/train/assembly_rollout.py
                  → PPO/models/single_step_actor.py
                  → scheduling/assembly_start/...

eval 모드
main.py → PPO/eval/runner.py → PPO/eval/methods.py
                  → SPT/LPT/SEAM/RL 실행
                  → 상세 CSV 생성/이동

heuristic 모드
main.py → scheduling/* 경로 선택 실행

replay 모드
main.py → scheduling/performance_replay/...
```

---

## 4) 지원 모드

| 모드 | 설명 | 실행 스크립트 |
|---|---|---|
| train | PPO 학습 | PPO/train/runner.py |
| eval | 포괄 평가 | PPO/eval/runner.py |
| compare | eval과 동일 | PPO/eval/runner.py |
| heuristic | 휴리스틱 단독 실행 | scheduling/* |
| replay | 엑셀 실적 재현 | scheduling/performance_replay/* |
| replay_start_date | 엑셀 + 착수일 휴리스틱 연속 실행 | replay → start_date |

---

## 5) config.yaml 전체 흐름

config.yaml은 실행 모드, 데이터 경로, 평가/학습 설정, 제약 설정을 모두 담습니다.  
main.py는 이 설정을 읽고 요약 출력 후 기존 스크립트를 실행합니다.

### 5.1 실행 설정
```yaml
mode: eval
confirm: true
```
설명
- mode: 실행 모드 (train / eval / compare / heuristic / replay / replay_start_date)
- confirm: true면 실행 전 확인 질문, false면 바로 실행

### 5.2 데이터 경로
```yaml
data:
  excel_path: environment/판넬 블록 데이터셋_250618_SNU.xlsx
  sheet: Sheet1
```
설명
- excel_path: 입력 엑셀 경로
- sheet: 정확한 시트 이름  
  비워두면 자동 후보(기존데이터/블록데이터/Sheet1 등)에서 선택

### 5.3 평가 설정 (MODE 1/2 + 방법 선택)
```yaml
eval:
  methods: [SPT, LPT, SEAM_MIN, RL, 엑셀, 착수일]

evaluation:
  mode: 2
  num_gen: 50
  block_count_range: [50, 200]
  sampling: 50
```
설명
- eval.methods는 평가 방법 선택  
- evaluation은 상세 평가 옵션  
- evaluation이 비어 있으면 eval을 사용

주의  
엑셀, 착수일은 MODE 2에서만 실행됩니다.

### 5.4 학습 설정 (training)
```yaml
training:
  episodes: 150001
  lr: 0.0001
  optimizer: ranger_adabelief
  use_env_state: true
  feature_mode: reduced
  mode: self_label
```
설명
- training 값은 CLI 인자를 주지 않았을 때 기본값으로 사용됩니다.
- CLI 인자를 직접 주면 CLI가 우선입니다.

### 5.5 학습 모드 선택 규칙
- `training.mode: ppo`  
- `training.mode: self_label`  
`main.py train`은 위 값을 기본으로 사용하고,  
`-- --mode ppo` 같은 추가 인자가 있으면 그 값이 우선됩니다.

---

## 6) 휴리스틱 실행 방법 (LPT/SPT 선택)

### 6.1 조립착수일 휴리스틱에서 LPT 실행
```yaml
mode: heuristic
heuristic:
  entry: assembly_start
  method: lpt
```

### 6.2 착수일 휴리스틱 실행
```yaml
mode: heuristic
heuristic:
  entry: start_date
```
설명
- start_date 경로는 Action Masking 기반  
- method 값은 무시됩니다.

### 6.3 엑셀 순번 재현
```yaml
mode: heuristic
heuristic:
  entry: performance_replay
```

### 6.4 heuristic에서 선택 가능한 method 목록
```text
SPT, LPT, SEAM_MIN, RL, 엑셀, 착수일
```
설명  
- `assembly_start` 경로에서만 SPT/LPT/SEAM_MIN/RL 선택이 의미 있습니다.  
- `start_date`는 항상 착수일 기반 휴리스틱으로 실행됩니다.

---

## 7) 제약 설정 (활성/하드/완화)

### 6.1 활성 제약 리스트
```yaml
constraints:
  enabled_constraints_start_date:
    - 작업장순서
    - PS연속
    - 라인고정간격
    ...
```
설명
- enabled 리스트에 없는 제약은 검사 자체를 하지 않습니다.
- 착수일과 조립착수일을 따로 설정합니다.

### 6.2 하드 제약
```yaml
constraints:
  hard_constraints_start_date:
    - 작업장순서
    - PS연속
```
설명
- 하드 제약은 완화 순서에서 자동 제외됩니다.

### 6.3 완화 순서
```yaml
constraints:
  relax_order_start_date:
    - 라인고정간격
    - 라인혼합
    - C-Seam
    - 곡판
    - 고심수
    - P6
    - P6#4
    - 3Bay완화
```
완화 키 전체 목록은 `relax_keys.md` 참고

---

## 8) 캘린더 (공장 휴무, 중지, 점심)

```yaml
calendar:
  enable_holidays_off: false
  enable_half_day_off: true
  enable_morning_shutdown: true
  enable_lunch_break: false

  holidays_off:
    - 20251225

  half_day_off:
    - 20251203
  afternoon_shutdown: "15:00-08:00"
  afternoon_shutdown_schedule:
    20251203: "14:00-08:00"

  morning_shutdown: "08:00-12:00"
  morning_shutdown_dates:
    - 20250221
  morning_shutdown_schedule:
    20250221: "09:00-11:00"

  lunch_break: "12:00-13:00"
```

---

## 9) 용량 제어

### 8.1 일일 블록 수 제한
```yaml
constraints:
  enable_daily_block_cap_overrides: true
  daily_block_cap_overrides:
    20251203: 15
```

### 8.2 심수 절대값 오버라이드
```yaml
constraints:
  enable_daily_seam_cap_overrides: true
  daily_seam_cap_overrides:
    20251203: 90
```

### 8.3 심수 배율 오버라이드
```yaml
constraints:
  enable_daily_seam_cap_scales: true
  daily_seam_cap_scales:
    20251204: 1.2
```

우선순위  
절대값 → 명절 전날 → 혹서기 → 배율

---

## 10) 예시 실행

```bash
# 평가 (기본)
python main.py eval --config config.yaml

# 학습
python main.py train --config config.yaml --yes

# 실적 재현
python main.py replay --config config.yaml --yes

# 엑셀 + 착수일 연속 실행
python main.py replay_start_date --config config.yaml --yes
```

---

## 11) 명령어 빠른 복사 목록
```bash
# 평가(추론)
python main.py eval --config config.yaml --yes

# 학습 (ppo)
python main.py train --config config.yaml --yes -- --mode ppo

# 학습 (self_label)
python main.py train --config config.yaml --yes -- --mode self_label

# 휴리스틱만
python main.py heuristic --config config.yaml --yes

# 실적 재현(엑셀)
python main.py replay --config config.yaml --yes

# 실적 재현 + 착수일
python main.py replay_start_date --config config.yaml --yes
```

---

## 12) 관련 문서

- `relax_keys.md` : 완화 키 전체 매핑  
- `constration.txt` : 제약조건 정리  
- `dynamic_action_masking.txt` : 액션 마스킹 설명

---

필요하면 논문용 설명 섹션도 추가할 수 있습니다.
