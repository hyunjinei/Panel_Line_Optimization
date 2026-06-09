# [AGENT-ADD] CP-SAT 현재 구현 상태와 남은 작업

## 1. 목적

이 문서는 현재 코드에 들어간 CP-SAT 비교 알고리즘이 어떻게 짜여 있는지, 그리고 논문이나 발표에서 더 강하게 주장하려면 앞으로 무엇을 해야 하는지 정리한 문서다.

현재 CP-SAT은 RL, GA, LPT, SPT, SEAM_MIN과 같은 생성 데이터 문제를 풀기 위한 비교 알고리즘으로 붙어 있다.

핵심 목적은 다음 두 가지다.

- 블록 투입 순서를 만든다.
- 각 블록을 35A 또는 36B Bay에 배정한다.

그 뒤 결과를 기존 PBS 스케줄러와 final audit 경로로 다시 평가한다.

즉, 현재 비교 지표인 makespan과 제약 위반 수는 CP-SAT 내부 값이 아니라 기존 평가기에서 다시 계산된 값이다.

---

## 2. 관련 코드 위치

현재 CP-SAT 관련 코드는 주로 아래 파일에 있다.

- `PPO/eval/methods.py`
  - `run_cp_sat_baseline`
  - `_cp_sat_fast_candidate_baseline`
  - `_run_cp_sat_solver_candidate_task`
  - `_run_parallel_cp_replay_task`
  - `_ga_score_from_stats`
- `PPO/eval/runner.py`
  - 평가 방법 `CP_SAT` 실행 연결
  - CP-SAT CLI 옵션 연결
- `PPO/eval/helpers.py`
  - `cp`, `cpsat`, `cp_sat`, `ortools`, `or_tools`를 `CP_SAT`으로 인식

기존 설명 자료는 아래에 있다.

- `PPO/eval/all_methods/report_assets/cp_sat_method_explanation_ko.txt`

---

## 3. 현재 CP-SAT이 푸는 문제

현재 CP-SAT은 전체 PBS 문제를 완전히 그대로 푸는 방식이라기보다, PBS 문제의 핵심 일부를 수학 모델로 옮긴 뒤 후보를 만드는 방식이다.

현재 CP-SAT 모델이 직접 결정하는 것은 다음 두 가지다.

- `position[block_id]`
  - 각 블록의 투입 순서 위치
  - 모든 블록은 서로 다른 위치를 가져야 하므로 `AllDifferent` 제약을 사용한다.
- `bay_b[block_id]`
  - 각 블록이 36B인지 여부
  - `False`이면 35A, `True`이면 36B로 해석한다.

결과적으로 CP-SAT은 다음 형태의 해를 만든다.

```text
sequence = [블록 순서]
manual_bay_assignments = {
  block_id: "35A" 또는 "36B"
}
```

---

## 4. 공정 구조 모델링

PBS 블록은 8개 공정을 가진다.

- 공통 공정 5개
- Bay 분기 공정 3개

현재 CP-SAT의 `exact` 계열 모델은 이를 다음처럼 표현한다.

### 공통 공정

공통 5개 공정은 모든 블록이 같은 순서로 통과하는 하나의 연속 라인처럼 모델링한다.

블록 `A`가 블록 `B`보다 앞이면, 공통 공정 1부터 5까지 모두 `A`가 끝난 뒤 `B`가 시작해야 한다.

### Bay 분기 공정

분기 3개 공정은 Bay가 같은 블록끼리만 같은 순서를 공유한다.

예를 들어 두 블록이 둘 다 35A라면 35A 분기 공정에서 서로 겹칠 수 없다.

반대로 하나는 35A, 하나는 36B라면 서로 다른 Bay이므로 분기 공정에서는 동시에 진행될 수 있다.

이 구조 때문에 Bay divide가 모델 안에 들어가 있다.

---

## 5. CP-SAT 내부로 옮긴 PBS 위반 항

현재 `exact`와 `audit_exact` 계열에는 final audit에서 자주 잡히는 주요 제약군을 CP-SAT soft penalty로 직접 넣었다.

핵심은 금지가 아니라 위반 변수다.

```text
violation_var = 1이면 해당 제약 위반
violation_var = 0이면 해당 제약 만족
```

현재 CP-SAT 내부 목적함수에 들어간 대표 제약군은 다음과 같다.

- P/S 순서 위반
- 같은 assembly type 연속 배치
- line group 연속 배치
- cross seam 3연속
- C-seam, curved, high-seam 간격 위반
- 폭 21m 초과 블록의 Bay 배정 위반
- longi 30개 이상 블록의 Bay 배정 위반
- LT material 블록의 Bay 배정 위반
- 작은 P/S 쌍의 동일 Bay 위반
- Bay 부하 균형 위반
- A-A, B-B-B, B-B 연속 Bay 패턴
- main plate only 3연속 Bay 패턴
- 같은 실제 작업장 내 조립착수일 순서 위반
- 일별 심수 용량 위반
- 주중 심수 72 초과와 17블록 미만 조합 위반
- 35A와 36B의 분기 작업 부하 균형

이 항들은 hard ban이 아니다. RL/GA/final audit처럼 어길 수는 있지만, 목적함수에서 큰 벌점을 받아 뒤로 밀린다.

`P7#1`의 prefix별 균형 검사는 `--cp_native_prefix_balance on`으로 켤 수 있다. 다만 이 옵션은 변수 수가 크게 늘어서 큰 문제에서는 첫 해를 찾기 전에 시간이 끝날 수 있으므로 기본값은 off다.

또한 `cp_enforce_basic_bay_rules`가 켜져 있으면 final replay 직전에 기본 Bay 규칙 repair를 한 번 수행한다. 이것은 CP-SAT 탐색에서 위반을 금지하는 것이 아니라, 시간 제한 때문에 `FEASIBLE` 해가 일찍 반환될 때 폭 21m 초과나 작은 P/S 동일 Bay 같은 기본 Bay 실수를 평가 전에 보정하기 위한 장치다. 보정 횟수는 `cp_basic_bay_repair_count`에 저장된다.

---

## 6. 마스킹 힌트와 프로파일 재플레이 개선

현재 CP-SAT `fast` 모드에는 RL/GA 평가 구조에 더 가깝게 만들기 위한 개선이 들어가 있다.

### 6.1 마스킹 인식 목적함수

`cp_masking_hint`가 켜져 있으면 CP-SAT 후보 생성 전에 LPT를 한 번 실행한다.

이 LPT 실행은 기존 PBS 마스킹을 통과하면서 블록들이 어느 순서로 자연스럽게 배정되는지 보기 위한 용도다.

그 결과로 다음 값을 만든다.

```text
feasibility_rank_normalized
```

의미는 다음과 같다.

- 값이 작다: PBS 마스킹 기준으로 빨리 배정된 블록
- 값이 크다: PBS 마스킹 기준으로 뒤로 밀린 블록

이 값은 CP-SAT 후보 생성 목적함수에 들어간다.

목적은 다음과 같다.

```text
PBS 마스킹이 빨리 허용하는 블록은 CP-SAT도 앞쪽으로 유도한다.
PBS 마스킹이 뒤로 미루는 블록은 CP-SAT도 뒤쪽으로 유도한다.
```

관련 옵션은 다음과 같다.

```text
--cp_masking_hint 1
--cp_feasibility_weight 30
```

주의할 점은 이것이 hard 제약은 아니라는 점이다.

CP-SAT은 여전히 다른 목적 항과 함께 절충해서 순서를 만든다.

### 6.2 마스킹 힌트 warm start

기존 CP-SAT hint는 주로 조립착수일 순서인 `date_rank`를 기준으로 넣었다.

현재는 `feasibility_rank_normalized`와 `date_rank`를 함께 사용한다.

```text
hint_order = feasibility_rank_normalized 우선, date_rank 보조
```

즉 solver가 처음 탐색을 시작할 때 PBS 마스킹에 더 가까운 순서에서 시작하도록 유도한다.

### 6.3 프로파일 기반 재플레이

기존 CP-SAT 후보는 `allow_forced_prefix_override=True`로 한 번만 재생했다.

이 경우 CP-SAT 순서를 강제로 밀어 넣기 때문에, 기존 PBS 마스킹이 실제로 막았을 상황을 덜 반영할 수 있다.

현재는 추가로 상위 K개 CP-SAT 후보를 골라 hard profile별로 다시 재생한다.

```text
상위 K개 CP-SAT 후보
× 8개 hard profile
× allow_forced_prefix_override=False
```

기본값은 다음과 같다.

```text
--cp_profile_replay 1
--cp_profile_replay_top_k 6
```

따라서 기본적으로 최대 48개 추가 재플레이가 수행된다.

이때 `allow_forced_prefix_override=False`이므로 기존 PBS 마스킹을 존중한다.

최종 선택 기준은 phase 1 후보와 phase 2 프로파일 재플레이 후보를 모두 합쳐서 다음 사전식 기준으로 고른다.

```text
1. 누락 블록 수
2. primary violation 수
3. makespan
4. raw violation 수
```

중요한 점은 최종 CSV 저장도 선택 당시의 replay 조건을 유지한다는 것이다.

즉 프로파일 재플레이에서 선택된 해라면 최종 저장도 같은 hard profile과 `allow_forced_prefix_override=False` 조건으로 다시 실행한다.

---

## 7. 현재 실행 모드

현재 CP-SAT에는 크게 세 가지 모드가 있다.

### 7.1 `fast` 모드

기본 모드다.

`fast` 모드는 완전한 최적화라기보다 여러 CP-SAT 후보를 빠르게 생성하고, 기존 final audit으로 다시 평가해서 가장 좋은 후보를 고른다.

후보 종류는 다음과 같은 proxy 기준으로 만들어진다.

- 긴 작업 우선
- 짧은 작업 우선
- 심수 많은 작업 우선
- 심수 적은 작업 우선
- 폭 큰 작업 우선
- 론지 부하 고려
- 기존 날짜 순서 참고
- Bay 분기 작업 부하 고려

각 후보는 별도 CP-SAT 문제로 풀고, 결과 순서와 Bay 배정을 얻는다.

그 뒤 모든 후보를 기존 PBS 평가기로 다시 넣는다.

```text
CP-SAT 후보 생성
→ forced_prefix_block_ids로 순서 고정
→ manual_bay_assignments로 Bay 배정 고정
→ 기존 PBS 스케줄러 재생
→ final audit 결과로 후보 선택
```

### 7.2 `exact` 모드

`exact` 모드는 공통 공정, Bay 분기 공정, 순서, Bay 배정을 하나의 CP-SAT 모델로 풀려고 한다.

목적 함수는 현재 다음 구조다.

```text
native_primary_violations * very_large_weight
+ internal_makespan * cp_makespan_weight
+ native_raw_violations
+ bay_balance_abs
```

즉 CP-SAT 내부에서도 violation first 구조다.

먼저 CP-SAT 내부에 이식된 주요 위반 수를 줄이고, 그 다음 makespan을 줄인다.

Bay 부하 균형은 마지막 보조 항이다.

또한 기존 코드에서는 `--cp_model_mode exact`를 주더라도 `--cp_exact_max_blocks` 기본값이 0이면 `fast` 모드로 떨어질 수 있었다.

현재는 이 silent fallback을 막았다.

```text
--cp_model_mode exact
```

를 명시했고 `--cp_exact_max_blocks`를 주지 않으면, 해당 문제 크기에 대해 실제 exact 모드를 실행한다.

제한 시간 안에 `OPTIMAL`이 나오면 CP-SAT 내부 모델에 대해서는 최적성이 증명된 것이다.

제한 시간 안에 `FEASIBLE`만 나오면 해는 찾았지만 최적성 증명은 끝나지 않은 상태다.

이 차이를 보려고 결과에 `cp_objective_value`, `cp_best_bound`, `cp_gap`을 저장한다.

### 7.3 `audit_exact` 모드

`audit_exact` 모드는 CP-SAT이 해를 하나 만들면, 그 해를 기존 final audit으로 평가한다.

그리고 같은 해가 다시 나오지 않도록 no-good cut을 추가한 뒤 다음 해를 찾는다.

선택 기준은 GA와 맞춘다.

```text
1. 누락 블록 수
2. primary violation 수
3. makespan
4. raw violation 수
```

따라서 `audit_exact`는 final audit까지 포함해서 제약 위반을 makespan보다 먼저 보는 방식이다.

현재는 `exact` 모델 자체도 주요 PBS 위반 항을 native penalty로 가지고 있고, `audit_exact`는 그 위에 final audit 재검사를 추가한다.

기존 final audit을 제약 판단 도구처럼 사용하면서 여러 CP 해를 평가하는 구조다.

---

## 8. 최종 선택 기준

CP-SAT 후보의 최종 선택 기준은 `_ga_score_from_stats`와 같다.

```text
(missing_penalty, primary_violations, makespan_hours, raw_violations)
```

의미는 다음과 같다.

- `missing_penalty`
  - 처리되지 않은 블록 수
  - 누락 블록이 있으면 가장 나쁘게 본다.
- `primary_violations`
  - 주요 제약 위반 수
  - makespan보다 먼저 본다.
- `makespan_hours`
  - 전체 완료 시간
  - 제약 위반이 같을 때 비교한다.
- `raw_violations`
  - 세부 위반 수
  - 마지막 보조 기준이다.

즉 최종 비교 기준은 사용자가 요구한 것처럼 violation first, makespan second 구조다.

---

## 9. 현재 저장되는 결과 파일

CP-SAT을 평가하면 결과 폴더에 다음 파일이 저장될 수 있다.

- `cp_sat_evaluation_results.csv`
  - 선택된 CP-SAT 해를 기존 PBS 경로로 재생한 상세 결과
- `cp_sat_solver_candidates.csv`
  - `fast` 모드에서 CP-SAT 후보 생성 결과 요약
- `cp_sat_candidate_summary.csv`
  - `fast` 모드에서 후보별 final audit 평가 요약
- `cp_sat_solution_summary.csv`
  - `exact` 모드에서 CP 내부 순서와 Bay 요약
- `cp_sat_audit_exact_attempts.csv`
  - `audit_exact` 모드에서 시도별 CP 해와 final audit 평가 결과

---

## 10. 현재 구현의 장점

현재 구현의 장점은 다음과 같다.

- GA, RL, 휴리스틱과 같은 생성 데이터 문제를 그대로 사용할 수 있다.
- CP-SAT도 최종적으로 기존 PBS final audit으로 평가하므로 비교 지표가 같다.
- 순서뿐 아니라 Bay 배정도 CP-SAT이 직접 만든다.
- 35A/36B Bay divide가 반영되어 있다.
- LPT 기반 마스킹 힌트를 CP-SAT 목적함수와 warm start에 반영한다.
- 상위 CP-SAT 후보를 hard profile별로 다시 재생해서 마스킹을 존중하는 후보도 비교한다.
- CPU 코어와 RAM을 보고 worker 수를 자동으로 잡을 수 있다.
- `fast` 모드는 큰 문제에서도 후보 비교용으로 실행 가능하다.

---

## 11. 현재 구현의 한계

중요한 한계는 다음과 같다.

이전 버전과 달리, 현재 CP-SAT은 주요 PBS 제약군을 native soft penalty로 옮긴 상태다.

현재 직접 모델링된 것은 다음이다.

- 블록 순서
- 35A/36B Bay 배정
- 8개 공정의 공통 라인과 Bay 분기 구조
- P/S 순서 위반 penalty
- assembly type, line group 연속 위반 penalty
- C-seam, curved, high-seam 간격 위반 penalty
- Bay 패턴, Bay 배정, P/S 동일 Bay, Bay 부하 균형 위반 penalty
- 같은 실제 작업장 내 조립착수일 순서 위반 penalty
- 일별 심수 용량과 일부 날짜 용량 위반 penalty
- Bay 부하 균형

다만 아직 조심할 점은 있다.

- final audit의 모든 세부 메시지와 CP-SAT 내부 penalty가 1대1로 완전히 같은지는 계속 검증해야 한다.
- action masking의 hard profile, bias profile 전체를 CP-SAT 변수로 전부 복사한 것은 아니다.
- 별판 그룹, 세부 론지 부하, 3-bay 위험 패턴 중 일부는 아직 final audit 검증 의존도가 남아 있다.
- 큰 문제에서는 제한 시간 안에 `OPTIMAL`이 아니라 `FEASIBLE`이 나올 수 있다.

따라서 발표에서 현재 버전을 설명할 때는 다음 표현이 정확하다.

```text
CP-SAT baseline directly optimizes block insertion sequence and 35A/36B Bay assignment with a violation-first objective that includes the major PBS soft-constraint families, and the generated schedule is replayed through the same PBS final-audit pipeline used for RL, GA, and heuristics.
```

다음 표현은 아직 조심해야 한다.

```text
CP-SAT perfectly reproduces every detailed final-audit message one-to-one.
```

이 표현은 작은 검증 케이스를 더 쌓은 뒤에 쓰는 것이 맞다.

---

## 12. 앞으로 해야 할 검증

주요 제약 이식은 들어갔지만, 논문 방어를 더 강하게 하려면 아래 검증이 필요하다.

### 12.1 제약 목록 고정

먼저 기존 PBS에서 평가하는 제약을 전부 표로 정리해야 한다.

각 제약마다 다음을 정해야 한다.

- 제약 이름
- 기존 코드 위치
- action masking에서 쓰이는지
- final audit에서 쓰이는지
- hard 제약인지 soft 제약인지
- 위반을 허용해야 하는지
- CP-SAT에서 어떤 변수와 식으로 표현할지
- 위반 수를 어떻게 셀지

이 작업이 먼저 되어야 CP-SAT 이식이 정확해진다.

### 12.2 hard 금지와 soft 위반을 분리

사용자가 말한 것처럼 모든 제약을 무조건 금지하면 안 된다.

현실에서는 제약을 어길 수밖에 없는 경우가 있고, 이때는 해가 없어지는 것보다 위반 수를 계산해서 비교하는 것이 맞다.

현재 CP-SAT은 이 구조로 바뀌었다.

```text
hard constraint:
  물리적으로 불가능한 것은 금지

soft constraint:
  위반 가능
  violation_var를 만들고 목적 함수에서 큰 벌점으로 반영
```

예를 들면 다음과 같다.

- 공정 순서와 장비 겹침 방지는 hard에 가깝다.
- 일별 심수 초과, 특정 Bay 패턴, 일부 연속성 규칙은 soft violation으로 둘 수 있다.

### 12.3 목적 함수를 사전식 구조로 고정

모든 비교 알고리즘과 맞추려면 목적 함수는 다음 우선순위를 가져야 한다.

```text
1. 누락 블록 최소화
2. primary violation 최소화
3. makespan 최소화
4. raw violation 최소화
5. Bay 부하 보조 균형
```

현재 구현은 첫 번째 방식인 큰 가중치 기반 목적함수를 사용한다.

첫 번째 방식은 큰 가중치를 주는 단일 목적 함수다.

```text
minimize
  W1 * missing_blocks
+ W2 * primary_violations
+ W3 * makespan
+ W4 * raw_violations
+ W5 * bay_balance
```

두 번째 방식은 여러 번 푸는 방식이다.

```text
1단계: primary violation 최솟값 찾기
2단계: primary violation을 그 값으로 고정
3단계: makespan 최솟값 찾기
4단계: makespan을 그 값으로 고정
5단계: raw violation과 Bay 균형 최적화
```

추후 논문 방어를 더 깔끔하게 하려면 두 번째 방식도 추가 실험으로 둘 수 있다.

### 12.4 final audit과 CP-SAT penalty를 맞추기

이제 가장 중요한 남은 작업이다.

CP-SAT 내부의 violation 수와 기존 final audit의 violation 수가 같아야 한다.

검증 방법은 다음과 같다.

- 작은 문제를 만든다.
- 사람이 의도적으로 위반이 있는 순서와 Bay를 만든다.
- 기존 final audit의 위반 수를 기록한다.
- CP-SAT 내부 penalty 계산도 같은 수가 나오는지 확인한다.
- 다르면 어느 규칙에서 차이가 나는지 추적한다.

이 과정을 통과해야 “CP-SAT으로 같은 문제를 정확히 풀었다”고 말할 수 있다.

### 12.5 성능 구조 개선

완전 CP-SAT은 20개 블록도 무거울 수 있다.

그래서 다음 구조가 필요하다.

- 20, 30, 40 블록부터 정확성 검증
- 50개 이상은 시간 제한 600초에서 best bound와 gap 저장
- 큰 문제는 `fast` 후보 생성과 `audit_exact`를 병행
- 모든 문제에서 `cp_status`, `cp_best_bound`, `cp_wall_time`, `computation_seconds` 저장
- 제한 시간 안에 최적성을 증명하지 못하면 `FEASIBLE_WITH_LIMIT`처럼 표시

### 12.6 결과 표에 상태 컬럼 추가

CP-SAT은 최적성을 증명했는지 여부가 중요하다.

결과 CSV에는 최소한 다음 컬럼이 있어야 한다.

- `cp_model_mode`
- `cp_status`
- `cp_objective_value`
- `cp_best_bound`
- `cp_gap`
- `cp_time_limit_sec`
- `cp_wall_time`
- `computation_seconds`
- `cp_selection_score_order`
- `cp_audit_attempts`
- `cp_native_primary_terms`
- `cp_native_raw_terms`
- `cp_native_constraint_counts`
- `cp_native_objective_order`
- `cp_native_prefix_balance`
- `cp_basic_bay_repair_count`
- `total_violations_primary`
- `total_violations_raw`
- `makespan_hours`

---

## 13. 바로 실행할 수 있는 명령어 예시

### 13.1 빠른 후보 생성 모드

큰 문제까지 빠르게 훑어볼 때 사용한다.

```bash
MPLCONFIGDIR=/tmp/matplotlib python main.py eval --config config_self_label_diff.yaml --yes -- --methods CP_SAT --mode 1 --generation_mode grid --block_counts 20:200:10 --grid_repeats 1 --cp_model_mode fast --cp_time_limit_sec 600 --cp_workers 0 --cp_full_cpu on --cp_parallel on --cp_parallel_workers 0 --cp_candidate_threads 1 --cp_candidates 48 --cp_solver_time_slice_sec 5 --cp_seed 42 --cp_masking_hint 1 --cp_feasibility_weight 30 --cp_profile_replay 1 --cp_profile_replay_top_k 6
```

### 13.2 전체 격자 exact 모드

20개부터 200개까지 같은 생성 격자에서 CP-SAT exact 모델을 돌릴 때 사용한다.

```bash
MPLCONFIGDIR=/tmp/matplotlib python main.py eval --config config_self_label_diff.yaml --yes -- --methods CP_SAT --mode 1 --generation_mode grid --block_counts 20:200:10 --grid_repeats 1 --cp_model_mode exact --cp_time_limit_sec 600 --cp_workers 0 --cp_full_cpu on --cp_seed 42
```

### 13.3 final audit를 같이 보는 audit_exact 모드

CP-SAT 해를 여러 개 만들고, 기존 final audit 기준으로 가장 좋은 해를 고를 때 사용한다.

```bash
MPLCONFIGDIR=/tmp/matplotlib python main.py eval --config config_self_label_diff.yaml --yes -- --methods CP_SAT --mode 1 --generation_mode grid --block_counts 20:200:10 --grid_repeats 1 --cp_model_mode audit_exact --cp_time_limit_sec 600 --cp_workers 0 --cp_full_cpu on --cp_audit_max_rejections 200 --cp_audit_continue_after_zero on --cp_seed 42
```

---

## 14. 발표에서 쓸 수 있는 정확한 설명

현재 버전은 다음처럼 설명하는 것이 가장 안전하다.

```text
CP-SAT baseline was implemented as a constraint-programming comparison method.
It explicitly determines the block insertion sequence and 35A/36B Bay assignment while modeling the common panel-line process and the Bay-divided longi processes.
The generated CP-SAT schedule is then replayed through the same PBS scheduling and final-audit pipeline used for RL, GA, and heuristics.
Therefore, makespan and violation metrics are compared under the same evaluation logic.
```

한국어로는 다음처럼 말하면 된다.

```text
CP-SAT은 블록 투입 순서와 35A/36B Bay 배정을 제약 모델로 생성하고, 생성된 해를 기존 PBS 스케줄러와 final audit 경로에 다시 넣어 평가한다.
따라서 RL, GA, 휴리스틱과 동일한 기준으로 makespan과 제약 위반 수를 비교한다.
다만 현재 버전은 모든 현장 제약을 CP-SAT 내부에 완전히 이식한 단계는 아니며, 완전 이식을 위해서는 final audit의 제약별 violation 계산을 CP-SAT penalty 변수와 일치시키는 작업이 남아 있다.
```

---

## 15. 최종 정리

현재 CP-SAT은 비교 실험용 기준선으로는 사용할 수 있다.

특히 다음 점은 이미 구현되어 있다.

- 같은 생성 데이터 문제 사용
- 블록 순서 생성
- 35A/36B Bay 배정 생성
- 공통 공정과 Bay 분기 구조 반영
- 기존 final audit으로 최종 평가
- violation first, makespan second 기준으로 후보 선택

하지만 논문에서 “모든 PBS 제약을 CP-SAT에 완전히 이식했다”고 말하려면 아직 부족하다.

앞으로의 핵심 작업은 다음 하나로 요약된다.

```text
기존 final audit의 제약 위반 계산을 CP-SAT 내부 soft penalty 변수로 하나씩 옮기고, 작은 검증 문제에서 위반 수가 완전히 일치하는지 확인해야 한다.
```
