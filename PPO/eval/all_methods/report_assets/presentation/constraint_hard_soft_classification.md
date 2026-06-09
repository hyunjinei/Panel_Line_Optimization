<!-- [AGENT-ADD] 발표/논문용 제약군 설명과 완화 순서 상세 정리. -->

# 제약군 분류와 완화 순서

## 0. 분류 기준

본 연구의 제약조건은 크게 네 가지 제약군으로 정리한다.

| Constraint family | 한글 표현 | 핵심 역할 |
|---|---|---|
| Capacity | 용량 제약 | 하루 또는 특정 기간에 투입 가능한 생산 부하를 제한한다. |
| Sequencing | 순서 제약 | 블록 간 선후 관계와 착수 순서를 보존한다. |
| Adjacency | 인접 배치 제약 | 특정 블록군이 연속으로 몰리는 것을 방지한다. |
| Bay Assignment | 베이 배정 제약 | 블록 특성과 베이 운용 조건에 따라 베이 선택을 제한하거나 유도한다. |

본 문서에서 hard와 soft는 다음 의미로 사용한다.

| 구분 | 의미 |
|---|---|
| Hard | 완화하지 않는 제약 또는 반드시 우선적으로 만족해야 하는 제약 |
| Soft | 현장 운용 선호 또는 완화 가능한 제약 |
| Information metric | 제약 위반 수가 아니라 결과 해석을 위한 지표 |

중요한 점은 다음과 같다.

- Capacity는 전부 hard이다. 용량 제약은 완화하지 않는다.
- Sequencing은 hard이다. 생산 순서나 대응 관계를 깨지 않기 위한 제약이다.
- Adjacency는 주로 soft이다. 후보가 모두 차단될 때 단계적으로 완화할 수 있다.
- Bay Assignment는 hard와 soft가 섞여 있다.
  - 물리적 베이 적합성이나 대응 블록 동일 베이는 hard이다.
  - 베이 선호, 베이 연속 패턴, 부하 균형은 soft 또는 information metric 성격이다.

---

## 1. Capacity Constraints

### Definition

Capacity constraints limit candidate block insertion according to available production capacity.

### 한국어 설명

용량 제약은 특정 날짜 또는 기간에 처리할 수 있는 생산 부하의 한계를 나타낸다.  
여기서 생산 부하는 주로 심수 작업량을 의미하며, 평일, 주말, 혹서기, 명절 전날처럼 작업 조건이 달라지는 경우 서로 다른 용량 기준을 적용한다.

본 연구에서는 용량 제약을 hard constraint로 둔다.  
즉, 용량 제약은 완화하지 않는다.

### 포함되는 제약

| 제약 | 설명 | Hard / Soft | 평가 해석 |
|---|---|---|---|
| 평일 생산 용량 제한 | 평일 하루에 투입 가능한 심수 작업량을 제한한다. | Hard | 위반 시 해당 일자의 생산 가능 용량을 초과한 것으로 본다. |
| 주말 생산 용량 제한 | 주말 작업일의 심수 작업량 한계를 제한한다. | Hard | 주말의 낮은 생산 가능 용량을 반영한다. |
| 고부하일 최소 블록 수 조건 | 일정 수준 이상의 심수 작업량이 배정되는 날에는 충분한 블록 수가 함께 배정되어야 한다. | Hard | 큰 작업량이 소수 블록에 집중되는 상황을 방지한다. |
| 혹서기 생산 용량 제한 | 혹서기에는 작업 환경을 고려해 더 낮은 생산 용량을 적용한다. | Hard | 계절별 작업 능력 저하를 반영한다. |
| 명절 전날 생산 용량 제한 | 명절 전날에는 별도 심수 용량 한계를 적용한다. | Hard | 특수 달력일의 생산 제약을 반영한다. |

### 이 제약군을 넣는 이유

용량 제약은 일정의 실행 가능성과 직접 연결된다.  
작업 순서가 좋아도 특정 날짜의 생산 능력을 초과하면 실제 현장에서는 그대로 실행하기 어렵다.  
따라서 본 연구에서는 용량 제약을 완화 가능한 규칙으로 보지 않고, 가장 우선적으로 만족해야 하는 hard constraint로 둔다.

### 발표 표에 넣을 표현

| Constraint | Meaning |
|---|---|
| Capacity | Candidate-limiting constraints induced by daily or period capacity. Capacity constraints are treated as hard constraints and are not relaxed. |

---

## 2. Sequencing Constraints

### Definition

Sequencing constraints preserve precedence relations, paired-block order, and start-order consistency.

### 한국어 설명

순서 제약은 블록이 투입되는 순서가 생산 논리와 맞도록 유지하는 제약이다.  
대응 관계가 있는 블록의 선후 관계, 같은 작업장 안에서의 착수 순서, 이미 정해진 공정 흐름이 뒤집히지 않도록 한다.

### 포함되는 제약

| 제약 | 설명 | Hard / Soft | 평가 해석 |
|---|---|---|---|
| 대응 블록 선후 관계 | 대응 관계가 있는 블록 사이의 기본 선후 관계를 보존한다. | Hard | 대응 관계가 깨지면 생산 순서가 왜곡된 것으로 본다. |
| 대응 블록 연속성 | 대응 관계가 있는 블록이 지나치게 떨어지지 않도록 순서 연속성을 보존한다. | Hard | 대응 블록 사이의 흐름이 끊기는 것을 방지한다. |
| 같은 작업장 내 착수 순서 | 같은 작업장 또는 같은 공정 흐름의 블록이 조립착수 순서를 크게 역전하지 않도록 한다. | Hard | 후공정 착수 순서 역전을 방지한다. |

### 이 제약군을 넣는 이유

순서 제약은 단순한 선호가 아니라 생산 흐름의 일관성을 유지하는 규칙이다.  
특히 실적 데이터 기반 평가에서는 작업장 내 착수 순서가 실제 운영 순서를 반영하므로, 이를 크게 역전하는 일정은 현장 적용성이 낮다.

### 발표 표에 넣을 표현

| Constraint | Meaning |
|---|---|
| Sequencing | Constraints on precedence, paired-block order, and workshop start-order consistency. |

---

## 3. Adjacency Constraints

### Definition

Adjacency constraints prevent undesirable consecutive placement of specific block types or high-load blocks.

### 한국어 설명

인접 배치 제약은 특정 블록군이 연속으로 몰리는 것을 막기 위한 제약이다.  
같은 라인 그룹, 특정 공정 특성, 곡판, 고심수, C/Seam 특성처럼 작업 부담이나 공정 위험이 비슷한 블록이 연속으로 배치되면 현장 운용성이 떨어질 수 있다.

본 연구에서는 인접 배치 제약을 주로 soft constraint로 처리한다.  
즉, 모든 후보가 차단되는 경우에는 일정 생성 실패를 막기 위해 단계적으로 완화할 수 있다.  
하지만 완화로 인해 발생한 위반은 최종 audit에서 다시 집계한다.

### 포함되는 제약

| 제약 | 설명 | Hard / Soft | 평가 해석 |
|---|---|---|---|
| 라인 그룹 연속 배치 제한 | 같은 라인 그룹의 블록이 과도하게 연속 배치되지 않도록 제한한다. | Soft | 같은 계열 작업이 몰리는 것을 줄인다. |
| 조립 타입 혼합 제한 | 특정 조립 타입이 한쪽으로 몰려 연속 배치되는 것을 제한한다. | Soft | 조립 타입 편중을 줄인다. |
| Cross seam 혼합 배치 제한 | Cross seam 특성을 가진 블록이 부적절하게 연속 또는 혼합 배치되는 것을 제한한다. | Soft | 특수 공정 부담이 연속되는 것을 줄인다. |
| C/Seam 블록 간격 제한 | C/Seam 특성을 가진 블록 사이에 간격을 유지하도록 한다. | Soft | C/Seam 작업 집중을 줄인다. |
| 곡판 블록 간격 제한 | 곡판 블록이 연속으로 몰리지 않도록 한다. | Soft | 곡판 작업 집중을 줄인다. |
| 고심수 블록 간격 제한 | 심수 작업량이 큰 블록이 연속 배치되지 않도록 한다. | Soft | 고부하 블록이 연속되는 것을 줄인다. |

### 이 제약군을 넣는 이유

인접 배치 제약은 일정의 실행 가능성보다는 작업 안정성과 부하 분산을 높이기 위한 성격이 강하다.  
따라서 용량 제약처럼 절대 금지하기보다는, 가능한 경우에는 지키고 후보가 모두 막히면 단계적으로 완화한다.

### 발표 표에 넣을 표현

| Constraint | Meaning |
|---|---|
| Adjacency | Constraints on consecutive placement of specific block groups, special process blocks, or high-load blocks. These constraints can be relaxed step by step when no feasible candidate remains. |

---

## 4. Bay Assignment Constraints

### Definition

Bay assignment constraints restrict or guide bay selection based on block geometry, paired relations, material characteristics, longi workload, and bay operation patterns.

### 한국어 설명

베이 배정 제약은 각 블록을 어느 베이에 배정할지 결정하거나 유도하는 제약이다.  
이 제약군은 하나의 hard 또는 하나의 soft로 단순하게 나누면 부정확하다.  
베이 배정 안에는 물리적으로 지켜야 하는 베이 적합성 규칙과, 현장 운용을 좋게 만들기 위한 베이 선호 규칙이 함께 들어 있다.

따라서 Bay Assignment는 **hard와 soft가 섞인 mixed constraint family**로 정리한다.

### 포함되는 제약

| 제약 | 설명 | Hard / Soft | 평가 해석 |
|---|---|---|---|
| 폭 기준 베이 적합성 | 폭이 큰 블록은 작업 가능한 베이에 배정되어야 한다. | Hard | 물리적 또는 설비 적합성에 가까운 제약이다. |
| 대응 블록 동일 베이 | 대응 관계가 있는 블록은 같은 베이에 배정되어야 한다. | Hard | 대응 블록의 작업 흐름을 유지한다. |
| 고론지 블록 우선 베이 배정 | 론지 수가 많은 블록은 권장 베이에 배정되도록 유도한다. | Soft | 작업 부하와 베이 특성을 고려한 선호 규칙이다. |
| 특정 자재 블록 우선 베이 배정 | 특정 자재 특성을 가진 블록은 권장 베이에 배정되도록 유도한다. | Soft | 자재 특성에 따른 베이 선호 규칙이다. |
| 동일 베이 연속 배치 제한 | 같은 베이에 블록이 과도하게 연속 배정되지 않도록 제한한다. | Soft | 베이 운용 패턴을 안정화하기 위한 규칙이다. |
| 주판 Only 블록 연속 배치 제한 | 주판 Only 블록이 과도하게 연속 배정되지 않도록 제한한다. | Soft | 특정 작업 유형의 편중을 줄이기 위한 규칙이다. |
| 베이 부하 균형 | 두 베이 사이의 작업량 불균형을 평가한다. | Information metric | 위반 수보다는 결과 해석 지표로 사용한다. |

### 왜 hard와 soft가 섞이는가

베이 배정에는 두 성격이 동시에 존재한다.

1. **Bay feasibility**
   - 블록이 실제로 작업 가능한 베이에 들어가야 하는 문제이다.
   - 폭 기준 베이 적합성, 대응 블록 동일 베이가 여기에 가깝다.
   - 이 부분은 hard로 보는 것이 맞다.

2. **Bay operation preference**
   - 더 좋은 베이 배정을 유도하는 운영 규칙이다.
   - 고론지 블록의 권장 베이, 특정 자재의 권장 베이, 베이 연속 패턴이 여기에 가깝다.
   - 비교 알고리즘이나 강제 선택 과정에서 위반이 발생할 수 있으며, 이 경우 최종 audit에서 위반으로 집계한다.

따라서 Bay Assignment 전체를 hard라고 쓰면, 권장 베이와 연속 패턴까지 절대 금지처럼 보이므로 과도한 표현이 된다.  
반대로 Bay Assignment 전체를 soft라고 쓰면, 폭 기준 베이 적합성과 대응 블록 동일 베이처럼 중요한 규칙이 약하게 보인다.  
그래서 Bay Assignment는 mixed constraint family로 설명하는 것이 가장 안전하다.

### 발표 표에 넣을 표현

| Constraint | Meaning |
|---|---|
| Bay Assignment | Constraints on bay choice based on block geometry, paired relations, material characteristics, longi workload, and bay operation patterns. This family includes both hard bay-feasibility rules and soft bay-operation preferences. |

---

## 5. 완화 순서

본 연구에서는 용량 제약을 완화하지 않는다.  
따라서 완화 순서에서 용량 관련 항목은 제외한다.

실험 설명에 사용할 완화 순서는 다음과 같다.

| 순서 | 완화 단계 | 완화되는 내용 | 해석 |
|---:|---|---|---|
| 1 | 라인 그룹 연속 배치 제한 완화 | 같은 라인 그룹 연속 제한 | 가장 먼저 완화하는 인접 배치 제약이다. |
| 2 | 조립 타입 혼합 제한 완화 | 조립 타입 혼합 또는 연속 제한 | 조립 타입 편중 제한을 완화한다. |
| 3 | Cross seam 혼합 배치 제한 완화 | Cross seam 특성 블록 혼합 제한 | 특수 공정 혼합 제한을 완화한다. |
| 4 | C/Seam 블록 간격 제한 완화 | C/Seam 블록 간격 | C/Seam 작업 집중 제한을 완화한다. |
| 5 | 곡판 블록 간격 제한 완화 | 곡판 블록 간격 | 곡판 작업 집중 제한을 완화한다. |
| 6 | 고심수 블록 간격 제한 완화 | 고심수 블록 간격 | 고부하 블록 연속 제한을 완화한다. |

### 완화 순서 해석

완화는 “제약을 없애고 평가하지 않는다”는 뜻이 아니다.  
완화는 후보가 모두 차단되는 상황에서 일정 생성을 계속하기 위한 후보 복구 단계이다.  
완화로 선택된 결과도 최종 audit에서 다시 검사되며, 위반이 있으면 violation으로 집계된다.

### 완화하지 않는 항목

| 제약군 | 완화 여부 | 이유 |
|---|---|---|
| Capacity | 완화하지 않음 | 생산 가능 용량과 직접 연결된다. |
| Sequencing | 완화하지 않음 | 생산 선후 관계와 착수 순서 일관성을 유지해야 한다. |
| Bay feasibility | 완화하지 않음 | 물리적 또는 관계 기반 베이 적합성을 유지해야 한다. |

---

## 6. 전체 요약 표

| Constraint family | 한글 표현 | Hard / Soft | 포함 제약 |
|---|---|---|---|
| Capacity | 용량 제약 | Hard | 평일 용량, 주말 용량, 고부하일 조건, 혹서기 용량, 명절 전날 용량 |
| Sequencing | 순서 제약 | Hard | 대응 블록 선후 관계, 대응 블록 연속성, 작업장 내 착수 순서 |
| Adjacency | 인접 배치 제약 | Soft | 라인 그룹 연속, 조립 타입 혼합, Cross seam, C/Seam, 곡판, 고심수 간격 |
| Bay Assignment | 베이 배정 제약 | Hard + Soft | 폭 기준 베이 적합성, 대응 블록 동일 베이는 hard이고, 권장 베이, 베이 연속 패턴, 베이 부하 균형은 soft 또는 정보성 지표 |

---

## 7. 사용 시 주의할 표현

피해야 할 표현:

- Bay Assignment 전체를 hard constraint라고 쓰는 것
- Bay Assignment 전체를 soft constraint라고 쓰는 것
- Capacity를 relaxable constraint라고 쓰는 것
- 완화된 제약은 평가에서 제외된다고 쓰는 것

권장 표현:

- Capacity constraints are treated as hard constraints and are not relaxed.
- Adjacency constraints are relaxable operational constraints.
- Bay Assignment is a mixed constraint family including both hard bay-feasibility rules and soft bay-operation preferences.
- Relaxed constraints are still evaluated in the final audit.

