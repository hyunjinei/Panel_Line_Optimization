# CA-CJH-Insertion 설계 문서

<!-- [AGENT-ADD] CA-CJH-Insertion 구현 설계 문서 -->

## 1. 현재 스케줄링 흐름

`main.py`는 실행 모드를 받아 실제 작업을 하위 모듈로 넘기는 통합 실행기다. `eval` 또는 `compare` 모드에서는 `PPO.eval.runner`가 실행되고, 이 파일이 `PPO.eval.methods`의 방법별 실행 함수를 호출한다.

현재 생성 데이터와 실적 데이터 평가는 같은 방식으로 방법별 결과를 모은다.

- `SPT`, `LPT`, `SEAM_MIN`: `run_assembly_decoding_sequence_with_blocks`를 사용한다.
- `GA`: 순열 후보를 만들고, 각 순열을 `forced_prefix_block_ids`로 기존 DES 경로에 재생한다.
- `RL`: 학습된 actor가 블록을 선택하고, 기존 환경과 최종 감사로 평가한다.

## 2. 후보 블록 선택 위치

기존 휴리스틱은 `scheduling/common/selection_rules.py`의 `select_block_id`에서 한 스텝 후보 중 하나를 고른다. 이 방식은 dispatch rule이다.

CA-CJH-Insertion은 한 스텝 선택 규칙이 아니라 전체 시퀀스를 점진적으로 만드는 constructive insertion 방법이다. 따라서 `select_block_id`에 단순 선택 규칙으로 넣지 않고, `scheduling/common/cjh_panel_insertion.py`에서 별도 시퀀스 생성기로 구현했다.

## 3. 강제 시퀀스 평가 흐름

비교 기준을 맞추기 위해 `forced_sequence` 인자는 사용하지 않는다. 해당 경로는 과거 호환용 간이 평가이며 최종 제약 감사가 충분히 반영되지 않는다.

CA-CJH는 GA와 같은 방식으로 아래 경로를 사용한다.

```python
run_assembly_decoding_sequence_with_blocks(
    forced_prefix_block_ids=sequence,
    allow_forced_prefix_override=True,
)
```

이 경로는 기존 DES, 액션 마스킹, 베이 배정, 최종 감사 집계를 그대로 사용한다. 강제 prefix가 현재 마스킹 후보 밖에 있어도 환경이 위반 정보를 남기므로, 위반이 사라지는 방식이 아니다.

## 4. 부분 시퀀스 평가 지원 여부

현재 환경은 NEH 방식의 부분 시퀀스만 독립적으로 평가하는 공개 함수를 제공하지 않는다. 그래서 CA-CJH의 부분 삽입 후보는 다음 방식으로 안전하게 평가한다.

1. 현재 부분 시퀀스를 forced prefix로 둔다.
2. 남은 블록은 `completion_policy`로 채운다.
3. 기본값은 `cjh_priority`이며, 전역 CA-CJH 우선순위의 남은 순서로 완성한다.
4. 완성된 시퀀스를 기존 DES와 최종 감사로 평가한다.

이 방식은 순수 부분 평가보다 느리지만, 다른 비교 알고리즘과 같은 제약 감사 기준을 유지한다.

## 5. CA-CJH-Insertion 정체성

구현된 방법은 다음 조건을 지킨다.

- 블록별 생산 feature vector를 만든다.
- Cosine 점수와 Jaccard-normal 점수로 전역 우선순위를 계산한다.
- 전역 우선순위 순서대로 블록을 하나씩 넣는다.
- 현재 부분 시퀀스의 모든 삽입 위치를 평가한다.
- 제약 위반, makespan, local-window similarity, 론지 균형으로 삽입 위치를 고른다.

큰 문제에서는 `beam_width` 또는 자동 beam이 켜질 수 있다. 이 경우 결과에는 `ca_cjh_beam_width`, `ca_cjh_beam_reason`이 기록된다.

## 6. 제약 처리

CA-CJH는 새 제약 검사기를 만들지 않는다. 모든 제약 검사는 기존 환경과 최종 감사에 맡긴다.

삽입 후보의 key는 기본적으로 다음 순서다.

```text
hard_violation_count
soft_violation_count
makespan_hours - alpha * local_similarity
longi_abs_diff
```

현재 코드에서는 기존 통계의 `total_violations_primary`를 hard 성격의 주요 위반 수로 사용하고, `total_violations_raw - total_violations_primary`를 soft 성격의 보조 위반 수로 기록한다.

## 7. 베이 배정

CA-CJH는 베이를 직접 고르지 않는다. 시퀀스만 만든다. 베이 배정은 기존 `EnhancedPanelBlockShop`의 자동 베이 배정과 후속 감사가 수행한다.

## 8. 수식

블록 `b`의 feature vector를 다음과 같이 둔다.

```text
x_b = [x_b,1, ..., x_b,D]
```

기본 feature는 8개 공정 처리시간이다.

정규화:

```text
x_tilde_b,d = (x_b,d - min_d) / (max_d - min_d + epsilon)
```

trimmed mean baseline:

```text
x_B,d = TrimMean_b(x_tilde_b,d; gamma)
```

trimmed standard deviation:

```text
sigma_d = TrimStd_b(x_tilde_b,d; gamma)
```

normal range:

```text
R_d = [x_B,d - sigma_d, x_B,d + sigma_d]
```

Cosine:

```text
Cos(b) = dot(x_tilde_b, x_B) / (||x_tilde_b|| * ||x_B|| + epsilon)
```

Jaccard-normal:

```text
J(b) = |{d | x_tilde_b,d in R_d}| / D
```

전역 점수:

```text
S_global(b) = w_cos * Cos(b) + w_jac * J(b)
```

삽입 위치의 local-window 점수도 같은 방식으로 계산하되, 삽입 블록과 좌우 이웃의 평균 feature를 사용한다.

## 9. 추가 및 수정 파일

추가:

- `scheduling/common/cjh_panel_features.py`
- `scheduling/common/cjh_panel_insertion.py`
- `docs/ca_cjh_insertion_design.md`
- `scripts/run_ca_cjh_insertion_smoke.py`
- `scripts/run_ca_cjh_insertion_actual.py`

수정:

- `PPO/eval/methods.py`: `run_ca_cjh_insertion_baseline` 추가
- `PPO/eval/runner.py`: `CA_CJH` method 실행, CLI, CSV, plot 연결
- `PPO/eval/helpers.py`: method alias 추가
- `main.py`: method alias 추가
- `config.yaml`, `config_self_label_diff.yaml`: `ca_cjh` 설정 추가

## 10. 실행 예

실적 데이터 비교:

```bash
python main.py eval --config config_self_label_diff.yaml --yes -- --methods SPT,LPT,SEAM_MIN,GA,CA_CJH --mode 2
```

생성 데이터 작은 smoke 비교:

```bash
python main.py eval --config config_self_label_diff.yaml --yes -- --methods SPT,LPT,SEAM_MIN,CA_CJH --mode 1 --generation_mode grid --block_counts 20:40:20 --grid_repeats 1 --ca_cjh_beam_width 8
```
