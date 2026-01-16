# AGENTS.md

Guidelines for agents (human or AI) working on this repository.

This repository implements a **Panel Block Shop (PBS) scheduling system** for shipyard panel/longi production.  
The core goals are:

1. **Minimize makespan** (total completion time of all blocks),
2. **Minimize violations of real-world production constraints**, such as panel capacity, bay patterns, P/S continuity, longi rules, seasonal limits, etc.

The codebase combines:

- Domain-specific environment and constraints (`enhanced_environment/`),
- Reinforcement learning (PPO) training and evaluation (`PPO/`),
- Heuristic baselines and analysis scripts (top-level `.py`),
- External Excel data sources (`environment/*.xlsx`, not included in this repo for privacy).

---

## 1. Agent Role & Attitude

You act as a **research software engineer and RL engineer** for a shipyard PBS scheduling system.

When modifying or extending this project:

- Prioritize **correctness of constraints** and **stability of existing behavior** over aggressive refactoring.
- Assume that **domain rules reflect real production constraints**; do not "simplify" them without explicit instruction.
- Be **objective and conservative**:
  - If you do not understand a constraint, search the existing code and comments first.
  - Prefer adding small, well-isolated helpers over cross-cutting changes.
- When in doubt, **leave the behavior unchanged** and add comments or TODOs instead of guessing.

---

## 2. Domain & Problem Definition

### 2.1 Panel Block Shop (PBS)

- The system schedules **panel blocks** into specific **bays** (e.g., 35A, 36B) over time.
- Each block has attributes such as:
  - Processing time / work content,
  - **Seam length / "심수" (welding length)**,
  - Block width, length,
  - Number of longis,
  - P/S (Port / Starboard) pairing,
  - Line group / assembly category, etc.
- Real-world constraints include:
  - Daily/weekly capacity limits (seam length, block count),
  - Holiday / hot-season adjustments,
  - Bay assignment rules (A/B balance, wide blocks, high-longi behavior),
  - P/S continuity requirements,
  - C-seam spacing rules,
  - 3-bay risk patterns, etc.

### 2.2 Optimization Objectives

The optimization problem is:

1. **Primary objective**: Minimize makespan of the schedule.
2. **Secondary objectives** (implemented via constraints and penalties):
   - Reduce the number and severity of constraint violations,
   - Maintain balanced and realistic bay and longi usage over time.

The environment exposes these objectives through:

- The **reward function** in the RL environment,
- **ConstraintViolation** objects and logs in the analysis tooling,
- Final schedule quality metrics (makespan, violations, bay usage distributions).

---

## 3. Repository Layout

The repository root looks like this (simplified, shim 제거 기준):

```text
.
├─ main.py
├─ config.yaml
├─ model_result.ipynb
├─ scheduling/
│  ├─ assembly_start/
│  │  ├─ action_sequence_조립착수일기준휴리스틱.py
│  │  └─ rl_assembly_scheduler.py
│  ├─ start_date/
│  │  └─ action_sequence_착수일기준휴리스틱.py
│  ├─ performance_replay/
│  │  └─ excel_실적데이터_순번기반시퀀싱.py
│  └─ common/
│     ├─ selection_rules.py
│     ├─ process_schedule.py
│     └─ result_builders.py
├─ utils/
│  ├─ csv_save.py
│  ├─ gantt_chart_enhanced.py
│  └─ optimized_block_generator.py
│
├─ enhanced_environment/
│  ├─ __init__.py
│  ├─ models/
│  ├─ constraints/
│  ├─ masking/
│  ├─ pbs_env/
│  ├─ common/
│  └─ bay/
│     ├─ assigner/
│     ├─ validator/
│     └─ makespan/
│
├─ environment/
│  └─ *.xlsx  (panel/block input data — not included in this repo snapshot)
│
└─ PPO/
   ├─ train/assembly_rollout.py
   ├─ eval/runner.py
   ├─ generated_blocks_debug.csv
   ├─ real_block_values.json
   ├─ models/single_step_actor.py
   ├─ eval/train_evaluation.py
   └─ train/runner.py
```

High-level responsibilities:

- **Entrypoint & scheduling**  
  - `main.py`: 통합 실행기 (train/eval/heuristic/replay).
  - `scheduling/assembly_start/rl_assembly_scheduler.py`: RL decoding/스케줄링 오케스트레이션.
  - `scheduling/assembly_start/action_sequence_조립착수일기준휴리스틱.py`: 조립착수일 휴리스틱.
  - `scheduling/start_date/action_sequence_착수일기준휴리스틱.py`: 착수일 기반 휴리스틱.
  - `scheduling/performance_replay/excel_실적데이터_순번기반시퀀싱.py`: 실적 재현.
  - `utils/optimized_block_generator.py`: 블록 데이터 생성/전처리.
  - `utils/gantt_chart_enhanced.py`: Gantt 시각화.
  - `utils/csv_save.py`: CSV 저장 유틸.
  - `model_result.ipynb`: 분석 노트북.

- **`enhanced_environment/`**  
  - `models/`: 도메인 모델 (EnhancedBlock, ConstraintViolation 등).
  - `constraints/`: 제약 설정/프리셋 + managers.
  - `masking/`: 액션 마스킹 core + mixins.
  - `pbs_env/`: 환경 코어 및 단계별 로직.
  - `common/`: 공통 유틸(DataConverter, TimeUtils 등).

- **`enhanced_environment/bay/`**  
  - `assigner/`: 베이 배정 로직.
  - `validator/`: 사후 검증 로직.
  - `makespan/`: 메이크스팬 계산.

- **`environment/`**  
  - External Excel files that define the panel/block dataset and other input data.  
    **Do not change their schema** (columns, units) unless explicitly instructed; treat them as external data sources.

- **`PPO/`**  
  - `models/single_step_actor.py`: Policy (and possibly value) network for the RL agent.
  - `train/runner.py`: PPO training entry point; runs the environment, collects rollouts, and updates the policy.
  - `train/assembly_rollout.py`: Rollout / sampling script for generating trajectories.
  - `eval/train_evaluation.py`: Utilities to evaluate trained models, possibly including validation metrics.
  - `eval/runner.py`: Comprehensive evaluation pipeline across multiple seeds / scenarios.
  - `generated_blocks_debug.csv`, `real_block_values.json`: Sample data / debug artifacts for block values and generated blocks.

---

## 4. Runtime Environment

### 4.1 Virtual Environment

The main Python virtual environment for this project is:

```bash
source /home/hyunjin/accord_env/bin/activate
```

Guidelines:

- Always activate this environment before running training or evaluation scripts.
- You may install additional Python packages into this environment using `pip` or `conda` if necessary for:
  - analysis tools,
  - debugging utilities,
  - plotting / notebooks, etc.
- It is acceptable to install Miniconda or create new environments on this machine if that helps experimentation.  
  When you do so, **document**:
  - Environment name,
  - Python version,
  - Key dependency versions (e.g., PyTorch, RL library, pandas).

### 4.2 Dependencies

- Dependencies are primarily Python-based (RL, numerical optimization, plotting, etc.).
- Prefer **environment-local installs** (inside `accord_env`) over system-wide installs.
- When adding new dependencies, try to:
  - Use widely used, well-maintained libraries,
  - Avoid heavy or obscure libraries unless clearly justified.

---

## 5. Language & Communication

- This `AGENTS.md` file is written in English.
- The primary user, however, prefers **Korean** for explanations.

Guidelines:

- When generating **explanations, analysis, or summaries for the user**, respond in **Korean**.
- Function names, class names, and module names should remain in **English**.
- Code comments can primarily be in English, but short Korean remarks are acceptable where they clarify domain-specific behavior (e.g., explanations of shipyard terms or PBS constraints).
- [AGENT-ADD] **No Hanja**: Avoid using Chinese characters (한자) in explanations, documentation, and comments; use Hangul or English equivalents instead.

---

## 6. Core Components & Constraints

### 6.1 Data Structures & Environment

- `EnhancedBlock` (in `enhanced_environment/models/block.py`) represents a panel block with all attributes needed for scheduling.
- `EnvironmentState` tracks global state (time, bay status, remaining blocks, etc.).
- `ActionResult` encodes the outcome of scheduling a particular block at a step.
- `ConstraintViolation` captures violations with:
  - An identifier (e.g., code or label for the rule),
  - A severity (INFO / WARNING / ERROR),
  - Optional descriptive metadata (which block, which day, etc.).

The main environment class (in `enhanced_environment/pbs_env/core.py`) provides:

- `reset()`: Initializes state and loads datasets.
- `step(action)`: Applies an action (choosing a block or decision), updates time, checks constraints, and returns:
  - New observation,
  - Reward,
  - Done flag,
  - Info dict (including violations, debug info).
- `_calculate_reward(...)`: Combines constraint penalties, progress indicators, and possibly makespan-related terms into a scalar reward.

### 6.2 Constraint Architecture

Constraints are implemented in a **consistent pattern**:

1. **Configuration**  
   - `enhanced_environment/constraints/config.py` defines `ConstraintConfig` (or similar structure) with:
     - Flags: `enable_...` to turn constraints on/off,
     - Hyperparameters: capacity limits, spacing, streak thresholds, seasonal modifiers, etc.

2. **Managers**  
   - `enhanced_environment/constraints/managers/` holds classes like:
     - Capacity managers (daily seam / block count, hot-season adjustments, holiday/weekday differences),
     - Bay state managers (bay usage, patterns, longi distributions),
     - Calendar manager (holiday, weekends, hot season),
     - P/S pair managers and line-group trackers.
   - These classes store ongoing state and provide query/update methods.

3. **Action Masking**  
   - `enhanced_environment/masking/core.py` uses the managers and config to determine whether a candidate block is **feasible** at the current step.
   - It implements checks such as:
     - Daily seam and block capacity,
     - Weekend / holiday / hot-season behavior,
     - Bay pattern rules (A/B balance, wide blocks, high longi blocks),
     - P/S continuity (do not break P/S sets),
     - C-seam spacing (avoid consecutive C-seam blocks except in special allowed cases),
     - 3-bay risk pattern prevention,
     - Mixing/line-group rules (e.g., mixing line/fixed/outsourcing categories with limits).

4. **Validation & Reward**  
- `bay/validator/` can recompute constraints post-hoc to verify schedules.
   - Violations are logged as `ConstraintViolation` objects.
   - The environment’s reward function aggregates violation penalties (based on severity) and other metrics into the scalar reward.

When **new constraints** are added, they **must** follow this pattern (see Section 8).

---

## 7. How to Run

> Note: Exact CLI arguments may differ; adjust for your local configuration and scripts.

### 7.1 Heuristic-only Scheduling

To run a heuristic-based schedule (without RL):

```bash
# Example: heuristic sequencing based on start date rules
python scheduling/start_date/action_sequence_착수일기준휴리스틱.py

# 또는 통합 실행기 사용
python main.py heuristic --config config.yaml --yes
```

Other heuristic scripts:

- `scheduling/assembly_start/action_sequence_조립착수일기준휴리스틱.py` – alternative heuristic with different assembly start-date logic.
- `scheduling/performance_replay/excel_실적데이터_순번기반시퀀싱.py` – generates sequences from real performance data.
- `utils/gantt_chart_enhanced.py` – visualize schedules produced by heuristics or RL.

### 7.2 PPO Training

Typical PPO training entry point:

```bash
source /home/hyunjin/accord_env/bin/activate

cd /path/to/repo

python PPO/train/runner.py     --env enhanced_pbs     --total-timesteps 2000000     --use-constraints true     --log-dir PPO/result/log     --model-dir PPO/result/models
```

Check the script for actual argument names and defaults.

### 7.3 Evaluation & Analysis

To evaluate trained models and generate analysis:

```bash
# Comprehensive evaluation script
python PPO/eval/runner.py     --model-dir PPO/result/models     --output-dir PPO/result/eval

# Generate Gantt chart for a specific result
python utils/gantt_chart_enhanced.py --input schedule_output.csv
```

Use `model_result.ipynb` for interactive analysis in a notebook environment.

---

## 8. Code Editing Guidelines

### 8.1 Marking Edits Clearly

When you **modify or add code**, you must annotate your changes with clear inline comments.

Use explicit markers so that the maintainer can quickly locate your edits. For example:

```python
# [AGENT-EDIT] Changed capacity limit logic to reflect new hot-season rule.

# [AGENT-ADD] Added C-seam spacing check to prevent consecutive C-seam blocks.

# ==== [AGENT-EDIT BEGIN: bay pattern rule] ====
# (modified code here)
# ==== [AGENT-EDIT END] ====
```

Guidelines:

- Do **not** remove existing comments unless they are clearly obsolete.
- If you refactor a large block of code, add a short summary comment near the top of the file describing:
  - What you changed,
  - Why you changed it,
  - Which constraints or behaviors are affected.
- Try to keep edits **local and incremental** unless explicitly requested to perform large-scale refactoring.

### 8.2 Style & Structure

- Use **snake_case** for variables and functions, **PascalCase** for classes.
- Prefer **type hints** where possible, especially in new or refactored code.
- Use f-strings for string interpolation.
- Keep functions relatively small and focused; prefer extracting helpers over deeply nested conditionals.
- For logging or debugging:
  - Reuse the existing logging utilities (if present) instead of ad-hoc `print` spam,
  - Guard verbose logging with flags (e.g., DEBUG/VERBOSE modes) where available.

---

## 9. Constraint Extension Pattern

When adding or modifying a constraint, follow this checklist:

1. **Configuration**
   - Add a new flag and/or hyperparameters in `enhanced_environment/constraints/config.py` (e.g., `enable_new_rule`, `new_rule_limit`).
   - Provide a clear name and default value that keeps current behavior unchanged when disabled.

2. **Manager Logic**
   - If the constraint depends on accumulated state (daily counts, streaks, bay history, etc.), extend the relevant manager in `enhanced_environment/constraints/managers/` or create a new one.
   - Expose small, focused methods such as `can_apply_new_rule(...)`, `update_new_rule_state(...)`.

3. **Action Masking**
   - Integrate the new rule into `enhanced_environment/masking/core.py`:
     - Either add a dedicated check function (`_check_new_rule(...)`),
     - Or call the manager’s method inside an existing aggregated check.
   - Return informative messages for violations so that debugging is easier.

4. **Validation & Reward**
   - If needed, update `bay/validator/` to verify the rule post-hoc.
   - Ensure that violations are reported via `ConstraintViolation` with:
     - A meaningful identifier,
     - A severity level reflecting the importance of the rule.
   - Confirm that the reward calculation in `enhanced_environment/pbs_env/` (validation/reward 경로) properly accounts for the new violations (e.g., via severity-weighted penalties).

5. **Testing**
   - Add or update small scenario scripts or test cases (if available) to exercise:
     - A schedule that satisfies the new rule,
     - A schedule that deliberately breaks it.
   - Check that:
     - Violations are logged as expected,
     - Reward behaves reasonably,
     - Existing constraints still behave as before when your new rule is disabled.

---

## 10. Safety & “Do Not Touch” List

To keep the system stable:

- **Environment Interface**
  - Do not change the public signature of the environment’s `reset` and `step` methods.
  - Changes to the observation and action spaces should only be made with explicit instruction.

- **Data Schemas**
  - Do not change the column names or semantics of Excel files under `environment/` without explicit instruction.
  - Treat these files as **input data**, not as configuration.

- **Result Directories**
  - Preserve the general layout under `PPO/result/` (logging vs models) so that existing scripts and notebooks keep working.

If a change requires touching any of the above, describe it clearly in comments and (if possible) in a separate summary note.

---

## 11. Example Tasks for This Agent

Examples of tasks the agent may be asked to perform:

- **Code Analysis**
  - “Explain how panel capacity (심수) constraints are enforced during action masking.”
  - “Summarize how bay patterns and 3-bay risk rules are implemented in the environment.”

- **Constraint Extensions**
  - “Add a new rule that limits consecutive scheduling of very wide blocks to avoid overloading a specific bay.”
  - “Relax a particular constraint under a ‘debug’ or ‘what-if’ mode while keeping the default behavior unchanged.”

- **RL Pipeline Adjustments**
  - “Tune PPO hyperparameters for larger instances while keeping the environment interface unchanged.”
  - “Add logging for new metrics such as per-bay average longi usage or P/S imbalance.”

- **Heuristic Improvements**
  - “Modify the existing heuristic scripts to consider the new constraint while preserving current behavior when the constraint is disabled.”

---

## [AGENT-NOTE] 2025-12-03 오류 회고 및 재발 방지

- **발생한 문제**: `scheduling/start_date/action_sequence_착수일기준휴리스틱.py`가 `get_next_available_blocks`(PFSP용 기본 마스킹)를 호출하는데도, `get_next_available_blocks_assembly`를 쓴다고 잘못 답변함.
- **원인**: 호출 경로를 실제 코드 라인(리플레이서치)로 확인하지 않고 기억/추측에 의존함.
- **재발 방지 원칙**:
  1. 경로·호출을 말할 때는 반드시 `rg`/`nl` 등으로 **실제 호출 위치를 확인**하고 라인 근거를 보고 답한다.
  2. 함수 차이를 설명할 때는 **양쪽 스크립트에서 무엇을 호출하는지** 먼저 확인한다 (착수일 vs 조립착수일 휴리스틱).
  3. “착각/추정”이 느껴지면 즉시 검색·열람 후 답한다. 추측 답변을 하지 않는다.
  4. 변경/패치 후에는 최소한 `py_compile` 또는 간단 테스트를 돌려 확인한다.
- **실행**: 앞으로 호출 경로 언급 전 `rg get_next_available_blocks` 등으로 실제 파일을 열람한 뒤 답변한다.

For all such tasks, follow the patterns and guidelines described in this document, and always mark your edits clearly.

---
