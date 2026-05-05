# LLM Interface

이 폴더는 기존 PBS 스케줄러를 대체하지 않고, 바깥에서 보조 기능만 붙이는 모듈이다.

목표는 두 가지다.

1. 작업자의 자연어 수정 요청을 구조화된 제약으로 변환
2. 재스케줄링 전후 결과를 사람이 이해하기 쉬운 문장으로 설명

현재 상태는 다음과 같다.

- API 없이도 동작하는 deterministic parser / explainer 포함
- 기존 LPT / RL assembly 스케줄러에 interactive constraint를 optional argument로 전달 가능
- 최종 제약 위반 수는 기존 final audit 경로를 그대로 사용

즉, 기본 알고리즘은 유지하고, interactive wrapper만 옆에 붙는 구조다.

## 포함 모듈

- `schemas.py`
  - structured constraint schema
- `parser.py`
  - 한국어 요청을 `ScheduleEditRequest`로 파싱
- `reschedule.py`
  - fixed-position prefix, precedence, manual bay override 빌더
- `result_explainer.py`
  - before/after 결과 설명
- `summary_adapters.py`
  - 결과 rows / CSV를 `ResultSummary`로 변환
- `prompts.py`
  - 향후 API 연동용 prompt builder

## 현재 실제로 지원되는 interactive request

- `fixed_position`
  - 예: `11번 블록은 처음`, `11번 블록은 세 번째`
- `precedence`
  - 예: `11번은 4번보다 먼저`
- `manual_bay_assignment`
  - 예: `11번 블록은 35A로 배정`
- 복합 요청
  - 예: `11번 블록은 세 번째로 하고 35A로 배정`

## 실행 방식

1. 자연어 요청 파싱
2. 기존 PBS 스케줄러를 rerun
   - LPT / heuristic: prefix-resume + precedence filter + manual bay override
   - RL: prefix-resume + precedence filter + manual bay override
3. 결과 CSV 저장
4. 기존 final audit 결과를 다시 집계
5. before / after 설명 생성

## 중요한 원칙

- 코어 스케줄러를 새로 만들지 않는다.
- interactive constraint가 없으면 기존 동작은 그대로 유지된다.
- LLM 또는 parser는 제약 위반 수를 직접 계산하지 않는다.
- 실제 위반 판정은 기존 final audit가 담당한다.

## 다음 단계

1. 실제 현장 요청 문장 샘플을 더 모아 parser coverage 확장
2. LLM parser 사용 시 `OPENAI_API_KEY` / `GROQ_API_KEY`와 모델 운영값 정리
3. UI 또는 notebook에서 반복 재스케줄링 사용성 개선

## Groq 연결

Groq는 OpenAI 호환 `chat.completions` 경로로 붙는다. API key는 코드에 넣지 말고 쉘 환경변수로만 둔다.

```bash
export PBS_LLM_PROVIDER=groq
export GROQ_API_KEY="여기에_Groq_API_key"
export GROQ_MODEL="llama-3.3-70b-versatile"
```

요청 파싱 확인:

```bash
python main.py llm --config config_self_label_diff.yaml --yes -- parse \
  --parser groq \
  --request "11번 블록은 세 번째로 하고 35A로 배정"
```

명령어에서 직접 지정할 수도 있다.

```bash
python main.py llm --config config_self_label_diff.yaml --yes -- parse \
  --parser llm \
  --llm-provider groq \
  --llm-model "$GROQ_MODEL" \
  --request "11번은 4번보다 먼저"
```

`GROQ_MODEL`은 Groq 콘솔에서 현재 사용 가능한 모델명으로 바꾸면 된다.

## Open-source 로컬 LLM 연결

Ollama처럼 OpenAI 호환 `/v1` endpoint를 제공하는 로컬 LLM도 같은 parser 경로로 연결할 수 있다.

```bash
ollama serve
ollama pull llama3.1:8b
```

다른 터미널에서 확인:

```bash
python main.py llm --config config_self_label_diff.yaml --yes -- parse \
  --parser ollama \
  --llm-model "llama3.1:8b" \
  --request "11번 블록은 세 번째로 하고 35A로 배정"
```

UI에서는 `실행 설정 > LLM 연결 > 요청 해석 방식`에서 `Ollama 로컬`을 고르면 된다. 기본 base URL은 `http://localhost:11434/v1`이다.

## CLI

요청 파싱만 확인:

```bash
python main.py llm --config config_self_label_diff.yaml --yes -- parse \
  --request "11번 블록은 세 번째로 하고 35A로 배정"
```

기존 결과 CSV 두 개를 비교 설명:

```bash
python main.py llm --config config_self_label_diff.yaml --yes -- explain \
  --before-csv before_results.csv \
  --after-csv after_results.csv \
  --request "11번 블록은 세 번째로 하고 35A로 배정"
```

스케줄러까지 재실행하는 실험 스크립트:

```bash
python experiments/interactive_llm_reschedule.py \
  --excel-path environment/판넬\ 블록\ 데이터셋_250618_SNU.xlsx \
  --sheet-name Sheet1 \
  --method rl \
  --rl-model-path PPO/train/result/models/.../best_ppo_rollout.pth \
  --request "11번 블록은 세 번째로 하고 35A로 배정" \
  --output-dir PPO/eval/llm_connect_case
```

## LLM Connect 평가

LLM Connect는 makespan 자체가 아니라 다음 항목으로 평가한다.

- 요청 이해 정확도: 자연어가 기대한 `EditConstraint`로 변환됐는지
- 스키마 안전성: 지원하지 않는 타입, 없는 블록, 잘못된 bay가 차단됐는지
- 설명 근거성: 설명이 request, metric delta, trace, constraint delta에 근거하는지
- trace 증거성: 강제 적용 step, 변경 step, before/after CSV가 남는지
- 권고안 품질: 요청 대안이 제약을 악화하지 않으면서 개선을 주는지

기본 parser 평가:

```bash
python main.py llm --config config_self_label_diff.yaml --yes -- eval-parse \
  --cases llm_interface/eval_cases.jsonl \
  --output-dir PPO/eval/llm_connect_eval
```

분석 번들 설명 평가:

```bash
python main.py llm --config config_self_label_diff.yaml --yes -- eval-bundle \
  PPO/eval/llm_connect_case/analysis_bundle.json \
  --output-dir PPO/eval/llm_connect_eval
```
