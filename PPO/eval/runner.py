#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
포괄적 평가: SPT, Random, RL 비교 및 상세 CSV 생성
=================================================

integrated_learning_and_scheduling.py와 동일한 형식으로
각 방법별 상세 CSV 파일들을 생성합니다.

생성되는 파일들:
- detailed_spt_schedule_processes_*.csv
- detailed_random_schedule_processes_*.csv  
- detailed_rl_schedule_processes_*.csv
- detailed_spt_schedule_info_*.csv
- detailed_random_schedule_info_*.csv
- detailed_rl_schedule_info_*.csv
"""

import os
# [AGENT-EDIT] OpenMP 중복 로딩 에러 회피 (필요 시에만 적용)
if "KMP_DUPLICATE_LIB_OK" not in os.environ:
    os.environ["KMP_DUPLICATE_LIB_OK"] = "TRUE"
# [AGENT-ADD] Keep matplotlib cache local/writable without requiring MPLCONFIGDIR in every command.
if "MPLCONFIGDIR" not in os.environ:
    os.environ["MPLCONFIGDIR"] = "/tmp/matplotlib"
    os.makedirs(os.environ["MPLCONFIGDIR"], exist_ok=True)
import sys
from copy import deepcopy
#################################################################################################################################################
###############                                            완화 모드                                                               ###############  
#################################################################################################################################################
import shutil
import re
import glob
import argparse
import pandas as pd
import torch
import random
import numpy as np
import math
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional
import traceback

# [AGENT-ADD] Always enable action masking diagnostics unless user overrides externally.
if "PBS_FORCE_DEBUG" not in os.environ:
    os.environ["PBS_FORCE_DEBUG"] = "0"

# [AGENT-EDIT] Trace BLK_30P( block_id 33 ) by default so masking prints appear without manual env setup
if "PBS_TRACE_BLOCKS" not in os.environ:
    os.environ["PBS_TRACE_BLOCKS"] = "33"

# 현재 디렉토리를 Python 경로에 추가
current_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(current_dir)
project_root = os.path.dirname(parent_dir)

# [AGENT-EDIT] Direct script execution needs the repository root for absolute
# package imports such as `PPO.eval...`, `utils...`, and `enhanced_environment...`.
if project_root not in sys.path:
    sys.path.insert(0, project_root)

# [AGENT-EDIT] Keep legacy local import paths after repo root for compatibility.
for _path in (parent_dir, current_dir):
    if _path not in sys.path:
        sys.path.append(_path)


def _parse_cli_overrides() -> argparse.Namespace:
    """[AGENT-ADD] Allow one-off eval overrides without editing yaml files."""
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config")
    parser.add_argument("--model_path")
    parser.add_argument("--rl_model_path")
    parser.add_argument("--excel_path")
    parser.add_argument("--sheet")
    parser.add_argument("--sampling", type=int)
    parser.add_argument("--profile_sampling")
    parser.add_argument("--use_self_label_profiles")
    parser.add_argument("--rl_selection_mode")
    parser.add_argument("--save_rl_samples_summary")
    parser.add_argument("--heuristic_sampling")
    parser.add_argument("--heuristic_profile_sampling")
    parser.add_argument("--save_heuristic_all_mask_off")
    parser.add_argument("--heuristic_decode_mode", type=int, choices=[1, 2])  # [AGENT-ADD] 1=기존프로파일조합, 2=all_on+all_mask_off만
    parser.add_argument("--eval_parallel")
    parser.add_argument("--eval_parallel_workers", type=int)
    parser.add_argument("--eval_parallel_device")
    parser.add_argument("--eval_worker_threads", type=int)
    parser.add_argument("--eval_progress_interval", type=int)
    parser.add_argument("--num_gen", type=int)
    parser.add_argument("--num_blocks_min", type=int)
    parser.add_argument("--num_blocks_max", type=int)
    parser.add_argument("--generation_mode")
    parser.add_argument("--block_counts")
    parser.add_argument("--grid_repeats", type=int)
    parser.add_argument("--ga_population", type=int)
    parser.add_argument("--ga_generations", type=int)
    parser.add_argument("--ga_elite", type=int)
    parser.add_argument("--ga_mutation_rate", type=float)
    parser.add_argument("--ga_seed", type=int)
    parser.add_argument("--ga_parallel")
    parser.add_argument("--ga_parallel_workers", type=int)
    parser.add_argument("--ga_worker_threads", type=int)
    parser.add_argument("--ga_ram_gb_per_worker", type=float)
    parser.add_argument("--ga_progress_interval", type=int)
    # [AGENT-ADD] CP-SAT/OR-Tools comparison baseline controls.
    parser.add_argument("--cp_time_limit_sec", type=float)
    parser.add_argument("--cp_workers", type=int)
    parser.add_argument("--cp_ram_gb_per_worker", type=float)
    parser.add_argument("--cp_seed", type=int)
    parser.add_argument("--cp_time_scale", type=int)
    parser.add_argument("--cp_makespan_weight", type=int)
    parser.add_argument("--cp_enforce_basic_bay_rules")
    parser.add_argument("--cp_model_mode")
    parser.add_argument("--cp_exact_max_blocks", type=int)
    parser.add_argument("--cp_candidates", type=int)
    parser.add_argument("--cp_solver_time_slice_sec", type=float)
    parser.add_argument("--cp_bay_balance_weight", type=int)
    parser.add_argument("--cp_full_cpu")
    parser.add_argument("--cp_parallel")
    parser.add_argument("--cp_parallel_workers", type=int)
    parser.add_argument("--cp_candidate_threads", type=int)
    parser.add_argument("--cp_audit_max_rejections", type=int)
    parser.add_argument("--cp_audit_continue_after_zero")
    parser.add_argument("--cp_masking_hint")
    parser.add_argument("--cp_feasibility_weight", type=int)
    parser.add_argument("--cp_profile_replay")
    parser.add_argument("--cp_profile_replay_top_k", type=int)
    parser.add_argument("--cp_native_prefix_balance")
    # [AGENT-ADD] CA-CJH-Insertion comparison baseline controls.
    parser.add_argument("--ca_cjh_feature_mode")
    parser.add_argument("--ca_cjh_trim_ratio", type=float)
    parser.add_argument("--ca_cjh_w_cos", type=float)
    parser.add_argument("--ca_cjh_w_jac", type=float)
    parser.add_argument("--ca_cjh_alpha", type=float)
    parser.add_argument("--ca_cjh_objective_mode")
    parser.add_argument("--ca_cjh_priority_mode")
    parser.add_argument("--ca_cjh_beta_load", type=float)
    parser.add_argument("--ca_cjh_feasible_priority")
    parser.add_argument("--ca_cjh_respect_workshop_order")
    parser.add_argument("--ca_cjh_allow_forced_prefix_override")
    parser.add_argument("--ca_cjh_w_load", type=float)
    parser.add_argument("--ca_cjh_w_abnormal", type=float)
    parser.add_argument("--ca_cjh_w_shape_dev", type=float)
    parser.add_argument("--ca_cjh_w_urgency", type=float)
    parser.add_argument("--ca_cjh_w_risk", type=float)
    parser.add_argument("--ca_cjh_load_amplifier", type=float)
    parser.add_argument("--ca_cjh_completion_policy")
    parser.add_argument("--ca_cjh_trace_enabled")
    parser.add_argument("--ca_cjh_trace_output_dir")
    parser.add_argument("--ca_cjh_parallel")
    parser.add_argument("--ca_cjh_workers", type=int)
    parser.add_argument("--ca_cjh_chunksize")
    parser.add_argument("--ca_cjh_use_all_insertion_positions")
    parser.add_argument("--ca_cjh_max_blocks_for_full_insertion", type=int)
    parser.add_argument("--ca_cjh_beam_width")
    parser.add_argument("--ca_cjh_enable_beam_search")
    parser.add_argument("--ca_cjh_auto_beam_width", type=int)
    parser.add_argument("--methods")
    parser.add_argument("--mode", type=int, choices=[1, 2])
    args, _ = parser.parse_known_args()
    return args


_CLI_OVERRIDES = _parse_cli_overrides()

# ==================== 모드 설정 ====================
# 모드 1: 생성된 학습데이터 사용 (SPT, Random, RL)
# 모드 2: SNU 데이터셋 사용 (엑셀, 착수일기반휴리스틱, SPT, Random, RL)
MODE = 2  #  여기서 모드 변경: 1 또는 2

# ==================== 공장 휴무/중지 하드코딩 설정 ====================
# True/False 설정 + 날짜 지정 (요구사항: PPO/comprehensive_evaluation.py 상단에서 제어)
ENABLE_FACTORY_SHUTDOWN = False
FACTORY_SHUTDOWN_DATES = ["20250902"]  # 20251225 08:00 ~ 20251226 07:59

ENABLE_AFTERNOON_SHUTDOWN = False
AFTERNOON_SHUTDOWN_DATES = ["20250221"]  # 20250221 15:00 ~ 20250222 07:59

ENABLE_LUNCH_BREAK = False
LUNCH_BREAK_START = "12:00"
LUNCH_BREAK_END = "13:00"

CALENDAR_OVERRIDES = {
    "enable_closed_dates": ENABLE_FACTORY_SHUTDOWN,
    "closed_dates": FACTORY_SHUTDOWN_DATES if ENABLE_FACTORY_SHUTDOWN else [],
    "enable_afternoon_shutdown": ENABLE_AFTERNOON_SHUTDOWN,
    "afternoon_shutdown_dates": AFTERNOON_SHUTDOWN_DATES if ENABLE_AFTERNOON_SHUTDOWN else [],
    "enable_lunch_break": ENABLE_LUNCH_BREAK,
    "lunch_break_start": LUNCH_BREAK_START,
    "lunch_break_end": LUNCH_BREAK_END,
}

# [AGENT-ADD] MODE 2에서도 NameError 방지를 위한 기본 평가 파라미터 (MODE 1 전용 값)
NUM_GEN_DATASETS = 1
BLOCK_COUNT_RANGE = (50, 200)
GENERATION_MODE = "random"
GRID_REPEATS = 1
GRID_BLOCK_COUNTS = list(range(20, 201, 10))
GRID_DISTRIBUTION_PROFILES = [
    {
        "name": "base",
        "util_target": 1.00,
        "ps_ratio": 0.00,
        "sub_ratio": 0.00,
        "seam_scale": 1.00,
        "tact_scale": 1.00,
        "length_scale": 1.00,
        "width_scale": 1.00,
        "thickness_scale": 1.00,
    },
    {
        "name": "ps_heavy",
        "util_target": 1.00,
        "ps_ratio": 0.10,
        "sub_ratio": 0.00,
        "seam_scale": 1.00,
        "tact_scale": 1.00,
        "length_scale": 1.00,
        "width_scale": 1.00,
        "thickness_scale": 1.00,
    },
    {
        "name": "sub_heavy",
        "util_target": 1.00,
        "ps_ratio": 0.00,
        "sub_ratio": 0.10,
        "seam_scale": 1.00,
        "tact_scale": 1.00,
        "length_scale": 1.00,
        "width_scale": 1.00,
        "thickness_scale": 1.00,
    },
    {
        "name": "mixed_ps_sub",
        "util_target": 1.00,
        "ps_ratio": 0.10,
        "sub_ratio": 0.10,
        "seam_scale": 1.00,
        "tact_scale": 1.00,
        "length_scale": 1.00,
        "width_scale": 1.00,
        "thickness_scale": 1.00,
    },
    {
        "name": "heavy_work",
        "util_target": 1.00,
        "ps_ratio": 0.05,
        "sub_ratio": 0.05,
        "seam_scale": 1.20,
        "tact_scale": 1.20,
        "length_scale": 1.05,
        "width_scale": 1.05,
        "thickness_scale": 1.20,
    },
    {
        "name": "overload",
        "util_target": 1.25,
        "ps_ratio": 0.05,
        "sub_ratio": 0.05,
        "seam_scale": 1.10,
        "tact_scale": 1.10,
        "length_scale": 1.00,
        "width_scale": 1.00,
        "thickness_scale": 1.00,
    },
]
PS_RATIO_RANGE = (0.0, 0.1)
SUB_RATIO_RANGE = (0.0, 0.1)
SEAM_SCALE_RANGE = (1.0, 1.2)
TACT_TIME_SCALE_RANGE = (1.0, 1.2)
LENGTH_SCALE_RANGE = (0.9, 1.1)
WIDTH_SCALE_RANGE = (0.9, 1.1)
THICKNESS_SCALE_RANGE = (0.9, 1.2)
MAX_DAILY_BLOCKS = 17
MIN_SPREAD_DAYS = 3
UTIL_BUCKETS = [
    {"name": "normal", "ratio": 0.60, "util_range": (0.90, 1.05)},
    {"name": "overload", "ratio": 0.30, "util_range": (1.05, 1.25)},
    {"name": "heavy_overload", "ratio": 0.10, "util_range": (1.25, 1.45)},
]

# [AGENT-ADD] 평가 공통 기본값 (config override 대상)
ASSEMBLY_MAX_DAYS = 20
ASSEMBLY_DATE_OFFSET = 0
START_DATE_OFFSET_DAYS = 0
SAVE_DETAILED_CSV = True

# [AGENT-ADD] 기본 엑셀 경로/시트 (config.data.*로 덮어씀)
EXCEL_PATH = os.path.join(project_root, "environment", "판넬 블록 데이터셋_250618_SNU.xlsx")
EXCEL_SHEET = "Sheet1"

# 필요한 모듈들 import
from enhanced_environment.common.utils_core import DataConverter
from runtime_config import build_calendar_overrides  # [AGENT-ADD] main.py 런타임 설정 반영
from runtime_config import get_runtime_config  # [AGENT-ADD] 평가 방법 선택

# [AGENT-EDIT] config.yaml (evaluation.mode 또는 eval.mode)로 MODE 오버라이드 가능
_runtime_cfg = get_runtime_config() or {}
if isinstance(_runtime_cfg, dict):
    _eval_cfg = _runtime_cfg.get("evaluation") or _runtime_cfg.get("eval") or {}
    if isinstance(_eval_cfg, dict):
        try:
            _mode_override = int(_eval_cfg.get("mode", MODE))
            if _mode_override in (1, 2):
                MODE = _mode_override
        except Exception:
            pass
if getattr(_CLI_OVERRIDES, "mode", None) in (1, 2):
    # [AGENT-ADD] CLI mode override is useful for quick generated-vs-real eval switches.
    MODE = int(_CLI_OVERRIDES.mode)
from enhanced_environment.constraints import get_all_enabled_config
 
# 공통 import
from utils.optimized_block_generator import OptimizedBlockGenerator

# MODE 2에서 사용할 추가 import
if MODE == 2:
    from scheduling.performance_replay.excel_실적데이터_순번기반시퀀싱 import (
        apply_plan_sequence_from_excel,
        PLAN_SHEET_PRIORITY,
    )

# [AGENT-ADD] 캘린더 오버라이드 주입 (하드코딩 설정)
def _apply_calendar_overrides(metadata: Dict) -> Dict:
    metadata = metadata or {}
    # [AGENT-EDIT] 런타임 설정이 있으면 우선 적용
    runtime_overrides = build_calendar_overrides()
    if runtime_overrides:
        metadata['calendar_overrides'] = runtime_overrides
    else:
        metadata['calendar_overrides'] = CALENDAR_OVERRIDES
    return metadata

# [AGENT-ADD] Feature mode switch (env var 기반) - methods.py에서 사용
FEATURE_MODE = os.environ.get("PBS_FEATURE_MODE", "reduced").lower().strip()
USE_POSITIONAL_ENCODING = os.environ.get("PBS_USE_POSENC", "0").lower() in {"1", "true", "yes", "on"}

#  재현성을 위한 Random Seed 설정
# RANDOM_SEED = 42  # 고정 시드 (디버깅용) - 멀티 시드 평가로 전환
# [AGENT-ADD] 멀티 시드 평가 기본값 (논문용 평균/분산 목적)
DEFAULT_SEED = 42
EVAL_SEEDS = [42, 142, 242, 342, 442]
USE_FIXED_SEED = True  # True면 DEFAULT_SEED만 사용
CURRENT_BASE_SEED = DEFAULT_SEED

# [AGENT-ADD] 분리된 평가 유틸/메서드 import
# [AGENT-EDIT] 분리된 평가 유틸/메서드 import
from PPO.eval.helpers import (
    _normalize_method_name,
    _get_selected_methods,
    _should_run as _should_run_core,
    set_random_seeds,
    apply_generated_data_variant,
    _sample_util_bucket,
    _compute_spread_days,
    _adjust_reserved_counts,
    sanitize_label_for_filename,  # [AGENT-EDIT] 결과 파일명 정규화 헬퍼 누락 import 보완
)
from PPO.eval.methods import (
    run_excel_heuristic,
    run_actionmasking_heuristic,
    run_spt_heuristic,
    run_lpt_heuristic,
    run_seam_min_heuristic,
    run_ca_cjh_insertion_baseline,
    run_ga_metaheuristic,
    run_cp_sat_baseline,
    run_rl_evaluation,
)
from PPO.eval.files import (
    rename_excel_detailed_csv_files,
    rename_actionmasking_detailed_csv_files,
    rename_detailed_csv_files,
)

# [AGENT-ADD] 상세 공정 CSV에서 날짜 키를 추출 (결과 리스트가 1일만 포함하는 경우 보완)
def _collect_date_keys_from_process_files(result_folder_path: Optional[str]) -> List[str]:
    keys: set[str] = set()
    search_dirs = []
    if result_folder_path:
        search_dirs.append(result_folder_path)
    search_dirs.append(os.getcwd())
    for base in search_dirs:
        if not base:
            continue
        pattern = os.path.join(base, "detailed_assembly_decoding_schedule_processes_*.csv")
        for path in glob.glob(pattern):
            fname = os.path.basename(path)
            match = re.search(r"(\\d{8})", fname)
            if match:
                keys.add(match.group(1))
    return sorted(keys)

# [AGENT-ADD] 평가 방법 선택 (config.yaml -> evaluation.methods, CLI --methods overrides)
SELECTED_METHODS = _get_selected_methods()
if getattr(_CLI_OVERRIDES, "methods", None):
    SELECTED_METHODS = {
        _normalize_method_name(part)
        for part in str(_CLI_OVERRIDES.methods).replace("/", ",").split(",")
        if part.strip()
    }

# [AGENT-ADD] runner 전용 래퍼 (선택 목록을 캡쳐)
def _should_run(method_key: str, *, default: bool = True) -> bool:
    return _should_run_core(method_key, SELECTED_METHODS, default=default)


def _parse_int_list(value: object, default: Optional[List[int]] = None) -> List[int]:
    """[AGENT-ADD] Parse fixed generated-data block counts from yaml or CLI."""
    if value is None:
        return list(default or [])
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return list(default or [])
        if ":" in raw and "," not in raw:
            parts = [int(part.strip()) for part in raw.split(":") if part.strip()]
            if len(parts) == 2:
                start, stop = parts
                step = 1
            elif len(parts) == 3:
                start, stop, step = parts
            else:
                return list(default or [])
            if step == 0:
                step = 1
            limit = stop + (1 if step > 0 else -1)
            return list(range(start, limit, step))
        return [int(part.strip()) for part in raw.replace(";", ",").split(",") if part.strip()]
    if isinstance(value, (list, tuple)):
        return [int(item) for item in value]
    return list(default or [])


def _normalize_grid_profile(profile: Dict[str, object], index: int) -> Dict[str, object]:
    """[AGENT-ADD] Normalize one fixed distribution profile for generated eval."""
    profile = dict(profile or {})
    name = str(profile.get("name") or profile.get("profile") or f"profile_{index + 1}")
    tact_scale = profile.get("tact_time_scale", profile.get("tact_scale", 1.0))
    return {
        "name": name,
        "util_target": float(profile.get("util_target", profile.get("util", 1.0))),
        "ps_ratio": float(profile.get("ps_ratio", 0.0)),
        "sub_ratio": float(profile.get("sub_ratio", 0.0)),
        "seam_scale": float(profile.get("seam_scale", 1.0)),
        "tact_time_scale": float(tact_scale),
        "length_scale": float(profile.get("length_scale", 1.0)),
        "width_scale": float(profile.get("width_scale", 1.0)),
        "thickness_scale": float(profile.get("thickness_scale", 1.0)),
    }


def _build_generation_grid_cases() -> List[Dict[str, object]]:
    """[AGENT-ADD] Build deterministic generated-data cases: block count x profile x repeat."""
    block_counts = _parse_int_list(GRID_BLOCK_COUNTS, default=list(range(20, 201, 10)))
    profiles = [
        _normalize_grid_profile(profile, idx)
        for idx, profile in enumerate(GRID_DISTRIBUTION_PROFILES or [])
        if isinstance(profile, dict)
    ]
    if not profiles:
        profiles = [_normalize_grid_profile({"name": "base"}, 0)]

    repeats = max(1, int(GRID_REPEATS))
    cases: List[Dict[str, object]] = []
    for repeat_idx in range(repeats):
        for block_count in block_counts:
            for profile_idx, profile in enumerate(profiles):
                seed = int(DEFAULT_SEED) + repeat_idx * 100000 + int(block_count) * 100 + profile_idx
                case = dict(profile)
                case.update({
                    "case_id": f"b{int(block_count)}_{profile['name']}_r{repeat_idx + 1}",
                    "repeat": repeat_idx + 1,
                    "total_blocks": int(block_count),
                    "seed": seed,
                    "base_seed": int(DEFAULT_SEED),
                    "profile_index": profile_idx,
                })
                cases.append(case)
    return cases


def _apply_evaluation_config_overrides():
    global NUM_GEN_DATASETS, BLOCK_COUNT_RANGE, PS_RATIO_RANGE, SUB_RATIO_RANGE
    global GENERATION_MODE, GRID_REPEATS, GRID_BLOCK_COUNTS, GRID_DISTRIBUTION_PROFILES
    global SEAM_SCALE_RANGE, TACT_TIME_SCALE_RANGE, LENGTH_SCALE_RANGE, WIDTH_SCALE_RANGE, THICKNESS_SCALE_RANGE
    global MAX_DAILY_BLOCKS, MIN_SPREAD_DAYS, UTIL_BUCKETS
    global USE_FIXED_SEED, DEFAULT_SEED, EVAL_SEEDS
    global ASSEMBLY_MAX_DAYS, ASSEMBLY_DATE_OFFSET, START_DATE_OFFSET_DAYS
    global RL_SAMPLING_COUNT, EXCEL_PATH, EXCEL_SHEET

    cfg = get_runtime_config() or {}
    if not isinstance(cfg, dict):
        return
    eval_cfg = cfg.get("evaluation") or {}
    eval_fallback = cfg.get("eval") or {}
    if isinstance(eval_cfg, dict) and isinstance(eval_fallback, dict):
        eval_cfg = {**eval_fallback, **eval_cfg}
    if not isinstance(eval_cfg, dict):
        return
    data_cfg = cfg.get("data") or {}

    def _pair(val, default):
        if isinstance(val, (list, tuple)) and len(val) == 2:
            return (val[0], val[1])
        return default

    if "num_gen" in eval_cfg:
        NUM_GEN_DATASETS = int(eval_cfg["num_gen"])
    if "generation_mode" in eval_cfg:
        GENERATION_MODE = str(eval_cfg.get("generation_mode") or GENERATION_MODE).lower().strip()
    if "grid_repeats" in eval_cfg:
        GRID_REPEATS = max(1, int(eval_cfg.get("grid_repeats")))
    if "grid_block_counts" in eval_cfg or "block_counts" in eval_cfg:
        GRID_BLOCK_COUNTS = _parse_int_list(
            eval_cfg.get("grid_block_counts", eval_cfg.get("block_counts")),
            default=GRID_BLOCK_COUNTS,
        )
    if "grid_distribution_profiles" in eval_cfg:
        raw_profiles = eval_cfg.get("grid_distribution_profiles")
        if isinstance(raw_profiles, dict):
            GRID_DISTRIBUTION_PROFILES = [
                {"name": str(name), **(value if isinstance(value, dict) else {})}
                for name, value in raw_profiles.items()
            ]
        elif isinstance(raw_profiles, list):
            GRID_DISTRIBUTION_PROFILES = raw_profiles
    if "block_count_range" in eval_cfg:
        BLOCK_COUNT_RANGE = _pair(eval_cfg.get("block_count_range"), BLOCK_COUNT_RANGE)
    if "num_blocks_min" in eval_cfg or "num_blocks_max" in eval_cfg:
        min_val = eval_cfg.get("num_blocks_min", BLOCK_COUNT_RANGE[0])
        max_val = eval_cfg.get("num_blocks_max", BLOCK_COUNT_RANGE[1])
        BLOCK_COUNT_RANGE = (min_val, max_val)
    if "ps_ratio_range" in eval_cfg:
        PS_RATIO_RANGE = _pair(eval_cfg.get("ps_ratio_range"), PS_RATIO_RANGE)
    if "sub_ratio_range" in eval_cfg:
        SUB_RATIO_RANGE = _pair(eval_cfg.get("sub_ratio_range"), SUB_RATIO_RANGE)
    if "seam_scale_range" in eval_cfg:
        SEAM_SCALE_RANGE = _pair(eval_cfg.get("seam_scale_range"), SEAM_SCALE_RANGE)
    if "tact_scale_range" in eval_cfg:
        TACT_TIME_SCALE_RANGE = _pair(eval_cfg.get("tact_scale_range"), TACT_TIME_SCALE_RANGE)
    if "length_scale_range" in eval_cfg:
        LENGTH_SCALE_RANGE = _pair(eval_cfg.get("length_scale_range"), LENGTH_SCALE_RANGE)
    if "width_scale_range" in eval_cfg:
        WIDTH_SCALE_RANGE = _pair(eval_cfg.get("width_scale_range"), WIDTH_SCALE_RANGE)
    if "thickness_scale_range" in eval_cfg:
        THICKNESS_SCALE_RANGE = _pair(eval_cfg.get("thickness_scale_range"), THICKNESS_SCALE_RANGE)
    if "max_daily_blocks" in eval_cfg:
        MAX_DAILY_BLOCKS = int(eval_cfg.get("max_daily_blocks"))
    if "min_spread_days" in eval_cfg:
        MIN_SPREAD_DAYS = int(eval_cfg.get("min_spread_days"))
    if "util_buckets" in eval_cfg and isinstance(eval_cfg["util_buckets"], list):
        UTIL_BUCKETS = eval_cfg["util_buckets"]

    if "use_fixed_seed" in eval_cfg:
        USE_FIXED_SEED = bool(eval_cfg.get("use_fixed_seed"))
    if "default_seed" in eval_cfg or "base_seed" in eval_cfg:
        DEFAULT_SEED = int(eval_cfg.get("default_seed", eval_cfg.get("base_seed")))
    if "seeds" in eval_cfg and isinstance(eval_cfg["seeds"], list):
        EVAL_SEEDS = [int(s) for s in eval_cfg["seeds"]]
    # [AGENT-EDIT] top-level seed가 있고 eval 설정에 seed가 없으면 기본 시드로 사용
    if ("default_seed" not in eval_cfg and "base_seed" not in eval_cfg
            and "seeds" not in eval_cfg and cfg.get("seed") is not None):
        try:
            DEFAULT_SEED = int(cfg.get("seed"))
            EVAL_SEEDS = [DEFAULT_SEED]
        except Exception:
            pass

    if "assembly_max_days" in eval_cfg:
        ASSEMBLY_MAX_DAYS = int(eval_cfg.get("assembly_max_days"))
    if "assembly_date_offset" in eval_cfg:
        ASSEMBLY_DATE_OFFSET = int(eval_cfg.get("assembly_date_offset"))

    # [AGENT-ADD] data.excel_path / data.sheet 적용
    excel_path = None
    if isinstance(data_cfg, dict):
        excel_path = data_cfg.get("excel_path") or excel_path
        EXCEL_SHEET = data_cfg.get("sheet") or EXCEL_SHEET
    if excel_path:
        raw_path = str(excel_path)
        if not os.path.isabs(raw_path):
            raw_path = os.path.join(project_root, raw_path)
        EXCEL_PATH = os.path.normpath(raw_path)
    if "start_date_offset_days" in eval_cfg:
        START_DATE_OFFSET_DAYS = int(eval_cfg.get("start_date_offset_days"))

    rl_sampling = None
    if "sampling" in eval_cfg:
        rl_sampling = eval_cfg.get("sampling")
    if rl_sampling is None:
        model_cfg = cfg.get("model") or {}
        if isinstance(model_cfg, dict):
            rl_sampling = model_cfg.get("sampling")
    if rl_sampling is not None:
        try:
            RL_SAMPLING_COUNT = int(rl_sampling)
        except Exception:
            pass
    # [AGENT-ADD] CLI overrides are applied last so test commands do not require yaml edits.
    if getattr(_CLI_OVERRIDES, "excel_path", None):
        raw_path = str(_CLI_OVERRIDES.excel_path)
        if not os.path.isabs(raw_path):
            raw_path = os.path.join(project_root, raw_path)
        EXCEL_PATH = os.path.normpath(raw_path)
    if getattr(_CLI_OVERRIDES, "sheet", None):
        EXCEL_SHEET = str(_CLI_OVERRIDES.sheet)
    if getattr(_CLI_OVERRIDES, "sampling", None):
        RL_SAMPLING_COUNT = int(_CLI_OVERRIDES.sampling)
    if getattr(_CLI_OVERRIDES, "num_gen", None):
        NUM_GEN_DATASETS = int(_CLI_OVERRIDES.num_gen)
    if getattr(_CLI_OVERRIDES, "generation_mode", None):
        GENERATION_MODE = str(_CLI_OVERRIDES.generation_mode).lower().strip()
    if getattr(_CLI_OVERRIDES, "grid_repeats", None):
        GRID_REPEATS = max(1, int(_CLI_OVERRIDES.grid_repeats))
    if getattr(_CLI_OVERRIDES, "block_counts", None):
        GRID_BLOCK_COUNTS = _parse_int_list(_CLI_OVERRIDES.block_counts, default=GRID_BLOCK_COUNTS)
    if getattr(_CLI_OVERRIDES, "num_blocks_min", None) or getattr(_CLI_OVERRIDES, "num_blocks_max", None):
        min_val = int(_CLI_OVERRIDES.num_blocks_min) if getattr(_CLI_OVERRIDES, "num_blocks_min", None) else int(BLOCK_COUNT_RANGE[0])
        max_val = int(_CLI_OVERRIDES.num_blocks_max) if getattr(_CLI_OVERRIDES, "num_blocks_max", None) else int(BLOCK_COUNT_RANGE[1])
        BLOCK_COUNT_RANGE = (min_val, max_val)

RL_SAMPLING_COUNT = 50
_apply_evaluation_config_overrides()

# RL 모델 경로 (실제 존재하는 파일)
# RL_MODEL_PATH = os.path.join(current_dir, 'result', 'models', '0829_13_19_ranger_adabelief_envFalse', 'ppo_rollout_ep143400.pth')  # 실행별 폴더 경로 포함
# RL_MODEL_PATH = os.path.join(current_dir, 'result', 'models', '1014_09_34_ranger_adabelief_envFalse', 'ppo_rollout_ep2500.pth')  # 실행별 폴더 경로 포함
# RL_MODEL_PATH = os.path.join(current_dir, 'result', 'models', '1112_17_35_Adam_envTrue', 'best_ppo_rollout_ep600.pth')  # 실행별 폴더 경로 포함
# RL_MODEL_PATH = os.path.join(current_dir, 'result', 'models', '1129_11_22_ranger_adabelief_envTrue', 'best_ppo_rollout_ep2710.pth')  # 실행별 폴더 경로 포함

# RL_MODEL_PATH = os.path.join(current_dir, 'result', 'models', '1129_11_22_ranger_adabelief_envTrue', 'ppo_rollout_ep200.pth')  # 실행별 폴더 경로 포함

RL_MODEL_PATH = os.path.join(project_root, 'PPO', 'result', 'models', '0109_18_09_ranger_adabelief_envTrue', 'ppo_rollout_ep2400.pth')  # 실행별 폴더 경로 포함
# RL_MODEL_PATH = os.path.join(current_dir, 'result', 'models', '0827_01_59', 'ppo_rollout_ep100.pth')  # 실행별 폴더 경로 포함

# [AGENT-EDIT] config.yaml의 model.path가 있으면 RL 모델 경로를 덮어쓴다.
_runtime_cfg = get_runtime_config() or {}
if isinstance(_runtime_cfg, dict):
    _model_cfg = _runtime_cfg.get("model") or {}
    if isinstance(_model_cfg, dict) and _model_cfg.get("path"):
        _cfg_path = str(_model_cfg.get("path")).strip()
        if _cfg_path:
            if not os.path.isabs(_cfg_path):
                _cfg_path = os.path.abspath(os.path.join(project_root, _cfg_path))
            RL_MODEL_PATH = _cfg_path
_cli_model_path = getattr(_CLI_OVERRIDES, "model_path", None) or getattr(_CLI_OVERRIDES, "rl_model_path", None)
if _cli_model_path:
    # [AGENT-ADD] One-off model override for real-data tests.
    _cfg_path = str(_cli_model_path).strip()
    if _cfg_path:
        if not os.path.isabs(_cfg_path):
            _cfg_path = os.path.abspath(os.path.join(project_root, _cfg_path))
        RL_MODEL_PATH = _cfg_path
def main():
    """메인 평가 함수 - integrated_learning_and_scheduling.py와 동일한 구조"""
    # [AGENT-EDIT] Step-level RL 로그 기본 활성화 (환경변수로 끌 수 있음)
    os.environ.setdefault("PBS_RL_STEP_LOG", "1")
    #  Random Seed 설정 (가장 먼저 실행)
    # set_random_seeds(RANDOM_SEED)  # [AGENT-EDIT] 고정 시드 실행 비활성화 (멀티 시드 평가)
    seed_list = [DEFAULT_SEED] if USE_FIXED_SEED else EVAL_SEEDS  # [AGENT-ADD] 평가 시드 리스트
    generation_grid_cases = (
        _build_generation_grid_cases()
        if MODE == 1 and GENERATION_MODE in {"grid", "fixed_grid", "deterministic"}
        else []
    )
    global CURRENT_BASE_SEED
    # [AGENT-EDIT] main 내부 try/except 블록 들여쓰기 정리 (동작 유지 목적)
    # [AGENT-ADD] 결과 폴더명용 타임스탬프 (current_time 미정의 오류 방지)
    current_time = datetime.now().strftime('%m%d_%H_%M')
    # [AGENT-EDIT] MODE 1 실행 시각 폴더명 (YYYYMMDD_HHMMSS)
    current_run_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    # [AGENT-EDIT] ActionMasking 디버그 출력 억제 (평가 로그 정리)
    os.environ["PBS_FORCE_DEBUG"] = "0"
    run_base_folder = None
    if MODE == 1:
        run_base_folder = os.path.join(os.path.dirname(__file__), current_run_str)
        os.makedirs(run_base_folder, exist_ok=True)
    # [AGENT-ADD] 평가 메서드 공통 설정 (runner -> methods 전달)
    method_settings = {
        "assembly_max_days": ASSEMBLY_MAX_DAYS,
        "assembly_date_offset": ASSEMBLY_DATE_OFFSET,
        "save_detailed_csv": SAVE_DETAILED_CSV,
        "rl_model_path": RL_MODEL_PATH,
        "feature_mode": FEATURE_MODE,
        "use_positional_encoding": USE_POSITIONAL_ENCODING,
    }
    # [AGENT-ADD] 평가 메서드별 세부 설정 전달 (GA/CP-SAT 포함)
    _runtime_for_methods = get_runtime_config() or {}
    _eval_cfg_for_methods = {}
    if isinstance(_runtime_for_methods, dict):
        _eval_fallback_for_methods = _runtime_for_methods.get("eval") or {}
        _eval_primary_for_methods = _runtime_for_methods.get("evaluation") or {}
        if isinstance(_eval_fallback_for_methods, dict):
            _eval_cfg_for_methods.update(_eval_fallback_for_methods)
        if isinstance(_eval_primary_for_methods, dict):
            _eval_cfg_for_methods.update(_eval_primary_for_methods)
        # [AGENT-ADD] Keep method-specific CA-CJH config available to methods.py.
        if isinstance(_runtime_for_methods.get("ca_cjh"), dict):
            _eval_cfg_for_methods["ca_cjh"] = _runtime_for_methods.get("ca_cjh")
    for _key in (
        "ga_population", "ga_generations", "ga_elite", "ga_mutation_rate", "ga_seed",
        "ga_parallel", "ga_parallel_workers", "ga_worker_threads",
        "ga_ram_gb_per_worker", "ga_progress_interval",
        "cp_time_limit_sec", "cp_workers", "cp_ram_gb_per_worker", "cp_seed",
        "cp_time_scale", "cp_makespan_weight", "cp_enforce_basic_bay_rules",
        "cp_model_mode", "cp_exact_max_blocks", "cp_candidates",
        "cp_solver_time_slice_sec", "cp_bay_balance_weight",
        "cp_full_cpu", "cp_parallel", "cp_parallel_workers", "cp_candidate_threads",
        "cp_audit_max_rejections", "cp_audit_continue_after_zero",
        "sampling", "use_self_label_profiles", "eval_use_self_label_profiles",
        "profile_sampling", "rl_selection_mode", "save_rl_samples_summary",
        "heuristic_sampling", "heuristic_profile_sampling",
        "eval_parallel", "eval_parallel_workers", "eval_parallel_device",
        "eval_worker_threads", "eval_progress_interval",
        "eval_ram_gb_per_worker", "eval_gpu_gb_per_worker",
        "eval_gpu_mem_per_worker_gb", "eval_max_gpu_workers",
        "save_heuristic_all_mask_off",
        "ca_cjh",
        "ca_cjh_feature_mode", "ca_cjh_trim_ratio", "ca_cjh_w_cos", "ca_cjh_w_jac",
        "ca_cjh_alpha", "ca_cjh_objective_mode", "ca_cjh_priority_mode",
        "ca_cjh_beta_load", "ca_cjh_feasible_priority", "ca_cjh_respect_workshop_order",
        "ca_cjh_allow_forced_prefix_override",
        "ca_cjh_w_load", "ca_cjh_w_abnormal",
        "ca_cjh_w_shape_dev", "ca_cjh_w_urgency", "ca_cjh_w_risk",
        "ca_cjh_load_amplifier",
        "ca_cjh_completion_policy",
        "ca_cjh_trace_enabled", "ca_cjh_trace_output_dir",
        "ca_cjh_parallel", "ca_cjh_workers", "ca_cjh_chunksize",
        "ca_cjh_use_all_insertion_positions",
        "ca_cjh_max_blocks_for_full_insertion", "ca_cjh_beam_width",
        "ca_cjh_enable_beam_search", "ca_cjh_auto_beam_width",
    ):
        if _key in _eval_cfg_for_methods:
            method_settings[_key] = _eval_cfg_for_methods[_key]
    # [AGENT-ADD] CLI one-off profile/sample analysis overrides.
    if getattr(_CLI_OVERRIDES, "sampling", None):
        method_settings["sampling"] = int(_CLI_OVERRIDES.sampling)
    if getattr(_CLI_OVERRIDES, "profile_sampling", None) is not None:
        method_settings["profile_sampling"] = _CLI_OVERRIDES.profile_sampling
    if getattr(_CLI_OVERRIDES, "use_self_label_profiles", None) is not None:
        method_settings["use_self_label_profiles"] = _CLI_OVERRIDES.use_self_label_profiles
    if getattr(_CLI_OVERRIDES, "rl_selection_mode", None):
        method_settings["rl_selection_mode"] = _CLI_OVERRIDES.rl_selection_mode
    if getattr(_CLI_OVERRIDES, "save_rl_samples_summary", None) is not None:
        method_settings["save_rl_samples_summary"] = _CLI_OVERRIDES.save_rl_samples_summary
    if getattr(_CLI_OVERRIDES, "heuristic_sampling", None) is not None:
        method_settings["heuristic_sampling"] = _CLI_OVERRIDES.heuristic_sampling
    if getattr(_CLI_OVERRIDES, "heuristic_profile_sampling", None) is not None:
        method_settings["heuristic_profile_sampling"] = _CLI_OVERRIDES.heuristic_profile_sampling
    if getattr(_CLI_OVERRIDES, "save_heuristic_all_mask_off", None) is not None:
        method_settings["save_heuristic_all_mask_off"] = _CLI_OVERRIDES.save_heuristic_all_mask_off
    if getattr(_CLI_OVERRIDES, "heuristic_decode_mode", None) is not None:  # [AGENT-ADD] decode_mode wiring
        method_settings["heuristic_decode_mode"] = _CLI_OVERRIDES.heuristic_decode_mode
    for _ga_key in (
        "ga_population", "ga_generations", "ga_elite", "ga_mutation_rate", "ga_seed",
        "ga_parallel", "ga_parallel_workers", "ga_worker_threads",
        "ga_ram_gb_per_worker", "ga_progress_interval",
    ):
        if getattr(_CLI_OVERRIDES, _ga_key, None) is not None:
            method_settings[_ga_key] = getattr(_CLI_OVERRIDES, _ga_key)
    # [AGENT-ADD] CLI wiring for CP-SAT comparison baseline.
    for _cp_key in (
        "cp_time_limit_sec", "cp_workers", "cp_ram_gb_per_worker", "cp_seed",
        "cp_time_scale", "cp_makespan_weight", "cp_enforce_basic_bay_rules",
        "cp_model_mode", "cp_exact_max_blocks", "cp_candidates",
        "cp_solver_time_slice_sec", "cp_bay_balance_weight",
        "cp_full_cpu", "cp_parallel", "cp_parallel_workers", "cp_candidate_threads",
        "cp_audit_max_rejections", "cp_audit_continue_after_zero",
        "cp_masking_hint", "cp_feasibility_weight",
        "cp_profile_replay", "cp_profile_replay_top_k", "cp_native_prefix_balance",
    ):
        if getattr(_CLI_OVERRIDES, _cp_key, None) is not None:
            method_settings[_cp_key] = getattr(_CLI_OVERRIDES, _cp_key)
    # [AGENT-ADD] CLI wiring for CA-CJH-Insertion comparison baseline.
    for _ca_cjh_key in (
        "ca_cjh_feature_mode", "ca_cjh_trim_ratio", "ca_cjh_w_cos", "ca_cjh_w_jac",
        "ca_cjh_alpha", "ca_cjh_objective_mode", "ca_cjh_priority_mode",
        "ca_cjh_beta_load", "ca_cjh_feasible_priority", "ca_cjh_respect_workshop_order",
        "ca_cjh_allow_forced_prefix_override",
        "ca_cjh_w_load", "ca_cjh_w_abnormal",
        "ca_cjh_w_shape_dev", "ca_cjh_w_urgency", "ca_cjh_w_risk",
        "ca_cjh_load_amplifier",
        "ca_cjh_completion_policy",
        "ca_cjh_trace_enabled", "ca_cjh_trace_output_dir",
        "ca_cjh_parallel", "ca_cjh_workers", "ca_cjh_chunksize",
        "ca_cjh_use_all_insertion_positions",
        "ca_cjh_max_blocks_for_full_insertion", "ca_cjh_beam_width",
        "ca_cjh_enable_beam_search", "ca_cjh_auto_beam_width",
    ):
        if getattr(_CLI_OVERRIDES, _ca_cjh_key, None) is not None:
            method_settings[_ca_cjh_key] = getattr(_CLI_OVERRIDES, _ca_cjh_key)
    if getattr(_CLI_OVERRIDES, "eval_parallel", None) is not None:
        method_settings["eval_parallel"] = _CLI_OVERRIDES.eval_parallel
    if getattr(_CLI_OVERRIDES, "eval_parallel_workers", None) is not None:
        method_settings["eval_parallel_workers"] = _CLI_OVERRIDES.eval_parallel_workers
    if getattr(_CLI_OVERRIDES, "eval_parallel_device", None) is not None:
        method_settings["eval_parallel_device"] = _CLI_OVERRIDES.eval_parallel_device
    if getattr(_CLI_OVERRIDES, "eval_worker_threads", None) is not None:
        method_settings["eval_worker_threads"] = _CLI_OVERRIDES.eval_worker_threads
    if getattr(_CLI_OVERRIDES, "eval_progress_interval", None) is not None:
        method_settings["eval_progress_interval"] = _CLI_OVERRIDES.eval_progress_interval
    # [AGENT-ADD] CSV 요약 저장용 버퍼
    gen_param_rows: List[Dict[str, object]] = []
    method_result_rows: List[Dict[str, object]] = []
    
    try:
        # MODE 1일 때 여러 생성 데이터셋을 순회 평가
        num_runs = len(generation_grid_cases) if generation_grid_cases else (NUM_GEN_DATASETS if MODE == 1 else 1)
        if MODE == 1 and generation_grid_cases:
            profile_names = sorted({str(case.get("name")) for case in generation_grid_cases})
            block_counts = sorted({int(case.get("total_blocks", 0)) for case in generation_grid_cases})
            print(
                f"\n🧪 생성 데이터 고정 격자: 블록수 {block_counts}, "
                f"분포 {profile_names}, 반복 {GRID_REPEATS}회, 총 {num_runs}문제"
            )
                                                                                                                                                                                                             
        # [AGENT-ADD] 전체 gen 요약용 누적 버퍼
        all_problem_summaries = []
        overall_stats = {}

        try:
            for ds_idx in range(num_runs):                                                                                                                                                                        
                grid_case = generation_grid_cases[ds_idx] if generation_grid_cases else None
                base_seed = int(grid_case.get("base_seed", DEFAULT_SEED)) if grid_case else seed_list[ds_idx % len(seed_list)]
                run_seed = int(grid_case.get("seed", base_seed + ds_idx * 100)) if grid_case else base_seed + ds_idx * 100
                CURRENT_BASE_SEED = base_seed
                # 각 데이터셋별 시드 오프셋                                                                                                                                                                       
                set_random_seeds(run_seed)                                                                                                                                                      
                print(f"\n🔁 시드 적용: {run_seed} (dataset {ds_idx+1}/{num_runs})")
                # [AGENT-ADD] 시드별 폴더 분리 (MODE 1)
                seed_base_folder = run_base_folder
                if MODE == 1 and run_base_folder:
                    seed_base_folder = os.path.join(run_base_folder, f"seed{base_seed}")
                    os.makedirs(seed_base_folder, exist_ok=True)
                print(" 데이터 로드 중...")                                                                                                                                                                       
                                                                                                                                                                                                              
                gen_context: Dict[str, object] = {}
                if MODE == 1:                                                                                                                                                                                     
                    print(f"   생성된 학습데이터 사용 (dataset {ds_idx+1}/{num_runs})")                                                                                                                           
                    # [AGENT-ADD] 전역 import 누락 환경 대비
                    generator_cls = globals().get('OptimizedBlockGenerator')
                    if generator_cls is None:
                        from utils.optimized_block_generator import OptimizedBlockGenerator as generator_cls
                    generator = generator_cls.load_from_saved_values()                                                                                                                                  
                    # [AGENT-EDIT] MODE 1 supports fixed grid cases for paper-style generated tests.
                    if grid_case:
                        total_blocks = int(grid_case["total_blocks"])
                        util_bucket = str(grid_case["name"])
                        util_target = float(grid_case["util_target"])
                        util_range = (util_target, util_target)
                        ps_ratio_target = float(grid_case["ps_ratio"])
                        sub_ratio_target = float(grid_case["sub_ratio"])
                        seam_scale = float(grid_case["seam_scale"])
                        tact_time_scale = float(grid_case["tact_time_scale"])
                        length_scale = float(grid_case["length_scale"])
                        width_scale = float(grid_case["width_scale"])
                        thickness_scale = float(grid_case["thickness_scale"])
                    else:
                        # [AGENT-EDIT] Randomized instance parameters remain available for quick smoke tests.
                        total_blocks = random.randint(BLOCK_COUNT_RANGE[0], BLOCK_COUNT_RANGE[1])
                        util_bucket, util_target, util_range = _sample_util_bucket(UTIL_BUCKETS)
                        ps_ratio_target = random.uniform(PS_RATIO_RANGE[0], PS_RATIO_RANGE[1])
                        sub_ratio_target = random.uniform(SUB_RATIO_RANGE[0], SUB_RATIO_RANGE[1])
                        seam_scale = random.uniform(SEAM_SCALE_RANGE[0], SEAM_SCALE_RANGE[1])
                        tact_time_scale = random.uniform(TACT_TIME_SCALE_RANGE[0], TACT_TIME_SCALE_RANGE[1])
                        length_scale = random.uniform(LENGTH_SCALE_RANGE[0], LENGTH_SCALE_RANGE[1])
                        width_scale = random.uniform(WIDTH_SCALE_RANGE[0], WIDTH_SCALE_RANGE[1])
                        thickness_scale = random.uniform(THICKNESS_SCALE_RANGE[0], THICKNESS_SCALE_RANGE[1])
                    spread_days = _compute_spread_days(
                        total_blocks,
                        util_target,
                        MAX_DAILY_BLOCKS,
                        MIN_SPREAD_DAYS
                    )
                    ps_pairs = int(round(total_blocks * ps_ratio_target / 2))
                    sub_groups = int(round(total_blocks * sub_ratio_target / 2))
                    ps_pairs, sub_groups = _adjust_reserved_counts(total_blocks, ps_pairs, sub_groups, min_basic_blocks=1)
                    generator.assembly_date_config['spread_days'] = spread_days
                    blocks_data = generator.generate_blocks_with_ps_pairs_configurable(
                        total_blocks=total_blocks,
                        ps_pairs_count=ps_pairs,
                        subassembly_groups=sub_groups
                    )
                    # [AGENT-EDIT] 심수/처리시간/물리 스케일은 DataConverter 이전에 적용
                    blocks_data = apply_generated_data_variant(
                        blocks_data,
                        seam_scale=seam_scale,
                        tact_time_scale=tact_time_scale,
                        length_scale=length_scale,
                        width_scale=width_scale,
                        thickness_scale=thickness_scale
                    )
                    ps_pairs_actual = 0
                    sub_groups_actual = 0
                    if blocks_data is not None:
                        if 'pair_block_id' in blocks_data.columns:
                            ps_pairs_actual = int(blocks_data['pair_block_id'].notna().sum() // 2)
                        if 'block_type' in blocks_data.columns:
                            sub_groups_actual = int((blocks_data['block_type'] == 'SUB').sum() // 2)
                    ps_ratio_actual = (ps_pairs_actual * 2) / total_blocks if total_blocks else 0.0
                    sub_ratio_actual = (sub_groups_actual * 2) / total_blocks if total_blocks else 0.0
                    actual_util = total_blocks / (MAX_DAILY_BLOCKS * spread_days) if spread_days else 0.0
                    blocks, metadata = DataConverter.dataframe_to_blocks_with_metadata(blocks_data)
                    metadata = _apply_calendar_overrides(metadata)
                    print(
                        f"   생성된 블록 로드 완료: {len(blocks)}개 | P/S쌍={ps_pairs_actual}, 별판그룹={sub_groups_actual}, "
                        f"심수스케일={seam_scale:.2f}, Tact스케일={tact_time_scale:.2f}, spread_days={spread_days}"
                    )
                    print(
                        f"   샘플 파라미터: blocks={total_blocks}, bucket={util_bucket}, util={util_target:.2f} "
                        f"(range {util_range[0]:.2f}-{util_range[1]:.2f}), actual_util={actual_util:.2f}, "
                        f"ps_ratio={ps_ratio_target:.2f}, sub_ratio={sub_ratio_target:.2f}, "
                        f"len/width/thick={length_scale:.2f}/{width_scale:.2f}/{thickness_scale:.2f}"
                    )
                    # [AGENT-ADD] per-gen parameter row for CSV export
                    gen_context = {
                        "gen": ds_idx + 1,
                        "seed": run_seed,
                        "base_seed": base_seed,
                        "generation_mode": "grid" if grid_case else "random",
                        "grid_case_id": grid_case.get("case_id") if grid_case else "",
                        "distribution_profile": grid_case.get("name") if grid_case else util_bucket,
                        "repeat": grid_case.get("repeat") if grid_case else "",
                        "profile_index": grid_case.get("profile_index") if grid_case else "",
                        "total_blocks": total_blocks,
                        "ps_ratio_target": ps_ratio_target,
                        "sub_ratio_target": sub_ratio_target,
                        "ps_pairs_target": ps_pairs,
                        "sub_groups_target": sub_groups,
                        "ps_pairs_actual": ps_pairs_actual,
                        "sub_groups_actual": sub_groups_actual,
                        "ps_ratio_actual": ps_ratio_actual,
                        "sub_ratio_actual": sub_ratio_actual,
                        "seam_scale": seam_scale,
                        "tact_time_scale": tact_time_scale,
                        "length_scale": length_scale,
                        "width_scale": width_scale,
                        "thickness_scale": thickness_scale,
                        "util_bucket": util_bucket,
                        "util_target": util_target,
                        "actual_util": actual_util,
                        "spread_days": spread_days,
                        "max_daily_blocks": MAX_DAILY_BLOCKS
                    }
                    gen_param_rows.append(gen_context)
                elif MODE == 2:                                                                                                                                                                                   
                    print(f"   SNU 데이터셋 사용: {EXCEL_PATH}")                                                                                                                                                  
                    if not os.path.exists(EXCEL_PATH):                                                                                                                                                            
                        print(f"❌ 데이터 파일을 찾을 수 없습니다: {EXCEL_PATH}")                                                                                                                                 
                        return                                                                                                                                                                                    
                    blocks, metadata = DataConverter.excel_to_blocks_with_metadata(EXCEL_PATH, sheet_name=EXCEL_SHEET)
                    metadata = _apply_calendar_overrides(metadata)
                    print(f"   SNU 블록 로드 완료: {len(blocks)}개")                                                                                                                                              
                                                                                                                                                                                                              
                # P/S 분석                                                                                                                                                                                        
                p_blocks = [b for b in blocks if b.port_starboard.value == 'P']                                                                                                                                   
                s_blocks = [b for b in blocks if b.port_starboard.value == 'S']                                                                                                                                   
                c_blocks = [b for b in blocks if b.port_starboard.value == 'C']                                                                                                                                   
                print(f"   P 블록: {len(p_blocks)}개")                                                                                                                                                            
                print(f"   S 블록: {len(s_blocks)}개")                                                                                                                                                            
                print(f"   C 블록: {len(c_blocks)}개")                                                                                                                                                            
                                                                                                                                                                                                              
                # 시작 날짜 계산                                                                                                                                                                                  
                min_assembly_date = min(block.max_start_date for block in blocks)                                                                                                                                 
                start_date = min_assembly_date.strftime("%Y-%m-%d")                                                                                                                                               
                earliest_date_str = min_assembly_date.strftime("%Y%m%d")                                                                                                                                          
                if MODE == 1:
                    # [AGENT-EDIT] MODE 1: 실행 시각 폴더 아래 gen1~genN으로 정리
                    base_folder_path = seed_base_folder or run_base_folder or os.path.join(os.path.dirname(__file__), current_run_str)
                    os.makedirs(base_folder_path, exist_ok=True)
                    if gen_context.get("grid_case_id"):
                        case_label = sanitize_label_for_filename(str(gen_context.get("grid_case_id")))
                        result_folder_name = f"gen{ds_idx+1}_{case_label}"
                    else:
                        result_folder_name = f"gen{ds_idx+1}"
                    result_folder_path = os.path.join(base_folder_path, result_folder_name)
                else:
                    result_folder_name = f"{earliest_date_str}_{current_time}_seed{base_seed}"
                    result_folder_path = os.path.join(os.path.dirname(__file__), result_folder_name)
                os.makedirs(result_folder_path, exist_ok=True)
                print(f"   결과 폴더: {result_folder_name}")                                                                                                                                                      
                if MODE == 1 and gen_context:
                    gen_context["result_folder"] = result_folder_name
                                                                                                                                                                                                              
                # 제약조건 설정                                                                                                                                                                                   
                constraint_config = get_all_enabled_config()                                                                                                                                                      
                                                                                                                                                                                                              
                # 2. 각 방법별 평가 실행 (MODE별 처리)                                                                                                                                                            
                results = {}                                                                                                                                                                                      
                date_keys = []                                                                                                                                                                                    

                if MODE == 1:
                    print("\n MODE 1: 생성된 학습데이터로 선택 방법 평가")
                    # SPT
                    if _should_run("SPT"):
                        print(f"\n{'='*60}")
                        print(" 1. SPT 휴리스틱 평가")
                        print(f"{'='*60}")
                        spt_results, spt_stats, spt_episode_data, spt_env = run_spt_heuristic(
                            blocks,
                            metadata,
                            start_date,
                            result_folder_path,
                            settings=method_settings,
                        )
                        results['SPT'] = {
                            'results': spt_results,
                            'statistics': spt_stats,
                            'makespan': spt_stats.get('makespan_hours', 0)
                        }
                        date_keys_spt = list(set([r.get('date', '20250101') for r in spt_results if r.get('date')]))
                        date_keys_spt = sorted(set(date_keys_spt + _collect_date_keys_from_process_files(result_folder_path)))
                        rename_detailed_csv_files('spt', date_keys_spt, result_folder_path)
                    # LPT
                    if _should_run("LPT", default=False):
                        print(f"\n{'='*60}")
                        print(" 2. LPT 휴리스틱 평가")
                        print(f"{'='*60}")
                        lpt_results, lpt_stats, lpt_episode_data, lpt_env = run_lpt_heuristic(
                            blocks,
                            metadata,
                            start_date,
                            result_folder_path,
                            settings=method_settings,
                        )
                        results['LPT'] = {
                            'results': lpt_results,
                            'statistics': lpt_stats,
                            'makespan': lpt_stats.get('makespan_hours', 0)
                        }
                        date_keys_lpt = list(set([r.get('date', '20250101') for r in lpt_results if r.get('date')]))
                        date_keys_lpt = sorted(set(date_keys_lpt + _collect_date_keys_from_process_files(result_folder_path)))
                        rename_detailed_csv_files('lpt', date_keys_lpt, result_folder_path)
                    # SEAM_MIN
                    if _should_run("SEAM_MIN"):
                        print(f"\n{'='*60}")
                        print(" 3. SEAM_MIN 휴리스틱 평가")
                        print(f"{'='*60}")
                        seam_min_results, seam_min_stats, seam_min_episode_data, seam_min_env = run_seam_min_heuristic(
                            blocks,
                            metadata,
                            start_date,
                            result_folder_path,
                            settings=method_settings,
                        )
                        results['SEAM_MIN'] = {
                            'results': seam_min_results,
                            'statistics': seam_min_stats,
                            'makespan': seam_min_stats.get('makespan_hours', 0)
                        }
                        date_keys_seam = list(set([r.get('date', '20250101') for r in seam_min_results if r.get('date')]))
                        date_keys_seam = sorted(set(date_keys_seam + _collect_date_keys_from_process_files(result_folder_path)))
                        rename_detailed_csv_files('seam_min', date_keys_seam, result_folder_path)
                    if _should_run("GA", default=False):
                        print(f"\n{'='*60}")
                        print(" GA baseline 평가")
                        print(f"{'='*60}")
                        ga_results, ga_stats, ga_episode_data, ga_env = run_ga_metaheuristic(
                            blocks,
                            metadata,
                            start_date,
                            result_folder_path,
                            settings=method_settings,
                        )
                        results['GA'] = {
                            'results': ga_results,
                            'statistics': ga_stats,
                            'makespan': ga_stats.get('makespan_hours', 0)
                        }
                        if ga_results:
                            date_keys_ga = list(set([r.get('date', '20250101') for r in ga_results if r.get('date')]))
                            rename_detailed_csv_files('ga', date_keys_ga, result_folder_path)

                    if _should_run("CA_CJH", default=False):
                        print(f"\n{'='*60}")
                        print(" CA-CJH-Insertion 평가")
                        print(f"{'='*60}")
                        ca_cjh_results, ca_cjh_stats, ca_cjh_episode_data, ca_cjh_env = run_ca_cjh_insertion_baseline(
                            blocks,
                            metadata,
                            start_date,
                            result_folder_path,
                            settings=method_settings,
                        )
                        results['CA_CJH'] = {
                            'results': ca_cjh_results,
                            'statistics': ca_cjh_stats,
                            'makespan': ca_cjh_stats.get('makespan_hours', 0)
                        }
                        if ca_cjh_results:
                            date_keys_ca_cjh = list(set([r.get('date', '20250101') for r in ca_cjh_results if r.get('date')]))
                            rename_detailed_csv_files('ca_cjh', date_keys_ca_cjh, result_folder_path)

                    if _should_run("CP_SAT", default=False):
                        print(f"\n{'='*60}")
                        print(" CP-SAT baseline 평가")
                        print(f"{'='*60}")
                        cp_results, cp_stats, cp_episode_data, cp_env = run_cp_sat_baseline(
                            blocks,
                            metadata,
                            start_date,
                            result_folder_path,
                            settings=method_settings,
                        )
                        results['CP_SAT'] = {
                            'results': cp_results,
                            'statistics': cp_stats,
                            'makespan': cp_stats.get('makespan_hours', 0)
                        }
                        if cp_results:
                            date_keys_cp = list(set([r.get('date', '20250101') for r in cp_results if r.get('date')]))
                            rename_detailed_csv_files('cp_sat', date_keys_cp, result_folder_path)

                    # RL                                                                                                                                                                                          
                    if _should_run("RL"):
                        print(f"\n{'='*60}")                                                                                                                                                                          
                        print(" RL 모델 평가")                                                                                                                                                                     
                        print(f"{'='*60}")                                                                                                                                                                            
                        rl_results, rl_stats, rl_episode_data, rl_env = run_rl_evaluation(
                            blocks,
                            metadata,
                            start_date,
                            result_folder_path,
                            num_samples=RL_SAMPLING_COUNT,
                            settings=method_settings,
                        )
                        results['RL'] = {                                                                                                                                                                             
                            'results': rl_results,                                                                                                                                                                    
                            'statistics': rl_stats,                                                                                                                                                                   
                            'makespan': rl_stats.get('makespan_hours', 0),                                                                                                                                            
                            'violations_train': rl_stats.get('total_violations_train', rl_stats.get('total_violations', 0)),                                                                                          
                            'violations_total': rl_stats.get('total_violations', 0),                                                                                                                                  
                            'violations_cseam': rl_stats.get('total_cseam_violations', 0)                                                                                                                             
                        }                                                                                                                                                                                             
                        if rl_results:                                                                                                                                                                                
                            date_keys_rl = list(set([r.get('date', '20250101') for r in rl_results if r.get('date')]))
                            rename_detailed_csv_files('rl', date_keys_rl, result_folder_path)                                                                                                                         

                elif MODE == 2:                                                                                                                                                                                   
                    print("\n MODE 2: SNU 데이터로 선택 방법 평가")                                                                                                                                              
                    # 1) 엑셀 순번 기반 평가 (계획 시트 우선순위 적용)
                    excel_entry: Dict[str, object] = {}
                    if _should_run("EXCEL"):
                        excel_variants: Dict[str, Dict[str, object]] = {}
                        plan_sheets: List[str] = []
                        try:
                            workbook = pd.ExcelFile(EXCEL_PATH)
                            available_sheets = set(workbook.sheet_names)
                            plan_sheets = [s for s in PLAN_SHEET_PRIORITY if s in available_sheets]
                        except Exception as e:
                            print(f"   ⚠️ 계획 시트 확인 실패: {e}")
                            plan_sheets = []

                        if plan_sheets:
                            for plan_sheet in plan_sheets:
                                # 시트별로 순번 적용 후 실행
                                blocks_variant = deepcopy(blocks)
                                metadata_variant = deepcopy(metadata)
                                apply_plan_sequence_from_excel(
                                    blocks_variant,
                                    EXCEL_PATH,
                                    plan_sheet=plan_sheet,
                                    plan_priority=(plan_sheet,),
                                    suppress_missing_message=True
                                )
                                excel_results, excel_stats, excel_episode_data, excel_env = run_excel_heuristic(
                                    blocks_variant,
                                    metadata_variant,
                                    start_date,
                                    result_folder_path,
                                    plan_label=plan_sheet
                                )
                                excel_variants[plan_sheet] = {
                                    "results": excel_results,
                                    "statistics": excel_stats,
                                    "makespan": excel_stats.get("makespan_hours", 0),
                                    "episode_data": excel_episode_data,
                                    "env": excel_env
                                }
                            excel_entry["variants"] = excel_variants
                        else:
                            excel_results, excel_stats, excel_episode_data, excel_env = run_excel_heuristic(
                                blocks,
                                metadata,
                                start_date,
                                result_folder_path
                            )
                            excel_entry = {
                                "results": excel_results,
                                "statistics": excel_stats,
                                "makespan": excel_stats.get("makespan_hours", 0),
                                "episode_data": excel_episode_data,
                                "env": excel_env
                            }

                        if excel_entry:
                            results["Excel"] = excel_entry

                    # 2) 착수일기준휴리스틱
                    if _should_run("ACTIONMASKING"):
                        am_results, am_stats, am_episode_data, am_env = run_actionmasking_heuristic(
                            EXCEL_PATH,
                            result_folder_path
                        )
                        results["ActionMasking"] = {
                            "results": am_results,
                            "statistics": am_stats,
                            "makespan": am_stats.get("makespan_hours", 0)
                        }

                    # 3) SPT / LPT / SEAM_MIN / RL (선택 실행)
                    if _should_run("SPT"):
                        print(f"\n{'='*60}")
                        print(" 1. SPT 휴리스틱 평가")
                        print(f"{'='*60}")
                        spt_results, spt_stats, spt_episode_data, spt_env = run_spt_heuristic(
                            blocks,
                            metadata,
                            start_date,
                            result_folder_path,
                            settings=method_settings,
                        )
                        results['SPT'] = {
                            'results': spt_results,
                            'statistics': spt_stats,
                            'makespan': spt_stats.get('makespan_hours', 0)
                        }
                        date_keys_spt = list(set([r.get('date', '20250101') for r in spt_results if r.get('date')]))
                        # [AGENT-EDIT] process 파일 기반 날짜키도 합쳐서 누락 방지
                        date_keys_spt = sorted(set(date_keys_spt + _collect_date_keys_from_process_files(result_folder_path)))
                        rename_detailed_csv_files('spt', date_keys_spt, result_folder_path)

                    if _should_run("LPT", default=False):
                        print(f"\n{'='*60}")
                        print(" 2. LPT 휴리스틱 평가")
                        print(f"{'='*60}")
                        lpt_results, lpt_stats, lpt_episode_data, lpt_env = run_lpt_heuristic(
                            blocks,
                            metadata,
                            start_date,
                            result_folder_path,
                            settings=method_settings,
                        )
                        results['LPT'] = {
                            'results': lpt_results,
                            'statistics': lpt_stats,
                            'makespan': lpt_stats.get('makespan_hours', 0)
                        }
                        date_keys_lpt = list(set([r.get('date', '20250101') for r in lpt_results if r.get('date')]))
                        # [AGENT-EDIT] process 파일 기반 날짜키도 합쳐서 누락 방지
                        date_keys_lpt = sorted(set(date_keys_lpt + _collect_date_keys_from_process_files(result_folder_path)))
                        rename_detailed_csv_files('lpt', date_keys_lpt, result_folder_path)

                    if _should_run("SEAM_MIN"):
                        print(f"\n{'='*60}")
                        print(" 3. SEAM_MIN 휴리스틱 평가")
                        print(f"{'='*60}")
                        seam_min_results, seam_min_stats, seam_min_episode_data, seam_min_env = run_seam_min_heuristic(
                            blocks,
                            metadata,
                            start_date,
                            result_folder_path,
                            settings=method_settings,
                        )
                        results['SEAM_MIN'] = {
                            'results': seam_min_results,
                            'statistics': seam_min_stats,
                            'makespan': seam_min_stats.get('makespan_hours', 0)
                        }
                        date_keys_seam = list(set([r.get('date', '20250101') for r in seam_min_results if r.get('date')]))
                        # [AGENT-EDIT] process 파일 기반 날짜키도 합쳐서 누락 방지
                        date_keys_seam = sorted(set(date_keys_seam + _collect_date_keys_from_process_files(result_folder_path)))
                        rename_detailed_csv_files('seam_min', date_keys_seam, result_folder_path)

                    if _should_run("GA", default=False):
                        print(f"\n{'='*60}")
                        print(" GA baseline 평가")
                        print(f"{'='*60}")
                        ga_results, ga_stats, ga_episode_data, ga_env = run_ga_metaheuristic(
                            blocks,
                            metadata,
                            start_date,
                            result_folder_path,
                            settings=method_settings,
                        )
                        results['GA'] = {
                            'results': ga_results,
                            'statistics': ga_stats,
                            'makespan': ga_stats.get('makespan_hours', 0)
                        }
                        if ga_results:
                            date_keys_ga = list(set([r.get('date', '20250101') for r in ga_results if r.get('date')]))
                            rename_detailed_csv_files('ga', date_keys_ga, result_folder_path)

                    if _should_run("CA_CJH", default=False):
                        print(f"\n{'='*60}")
                        print(" CA-CJH-Insertion 평가")
                        print(f"{'='*60}")
                        ca_cjh_results, ca_cjh_stats, ca_cjh_episode_data, ca_cjh_env = run_ca_cjh_insertion_baseline(
                            blocks,
                            metadata,
                            start_date,
                            result_folder_path,
                            settings=method_settings,
                        )
                        results['CA_CJH'] = {
                            'results': ca_cjh_results,
                            'statistics': ca_cjh_stats,
                            'makespan': ca_cjh_stats.get('makespan_hours', 0)
                        }
                        if ca_cjh_results:
                            date_keys_ca_cjh = list(set([r.get('date', '20250101') for r in ca_cjh_results if r.get('date')]))
                            rename_detailed_csv_files('ca_cjh', date_keys_ca_cjh, result_folder_path)

                    if _should_run("CP_SAT", default=False):
                        print(f"\n{'='*60}")
                        print(" CP-SAT baseline 평가")
                        print(f"{'='*60}")
                        cp_results, cp_stats, cp_episode_data, cp_env = run_cp_sat_baseline(
                            blocks,
                            metadata,
                            start_date,
                            result_folder_path,
                            settings=method_settings,
                        )
                        results['CP_SAT'] = {
                            'results': cp_results,
                            'statistics': cp_stats,
                            'makespan': cp_stats.get('makespan_hours', 0)
                        }
                        if cp_results:
                            date_keys_cp = list(set([r.get('date', '20250101') for r in cp_results if r.get('date')]))
                            rename_detailed_csv_files('cp_sat', date_keys_cp, result_folder_path)

                    if _should_run("RL"):
                        print(f"\n{'='*60}")
                        print(" RL 모델 평가")
                        print(f"{'='*60}")
                        rl_results, rl_stats, rl_episode_data, rl_env = run_rl_evaluation(
                            blocks,
                            metadata,
                            start_date,
                            result_folder_path,
                            num_samples=RL_SAMPLING_COUNT,
                            settings=method_settings,
                        )
                        results['RL'] = {
                            'results': rl_results,
                            'statistics': rl_stats,
                            'makespan': rl_stats.get('makespan_hours', 0),
                            'violations_train': rl_stats.get('total_violations_train', rl_stats.get('total_violations', 0)),
                            'violations_total': rl_stats.get('total_violations', 0),
                            'violations_cseam': rl_stats.get('total_cseam_violations', 0)
                        }
                        if rl_results:
                            date_keys_rl = list(set([r.get('date', '20250101') for r in rl_results if r.get('date')]))
                            rename_detailed_csv_files('rl', date_keys_rl, result_folder_path)
                                                                                                                                                                                                              
                # [AGENT-EDIT] 문제별 결과 요약/정리 (중복 출력 제거)
                method_summaries = []
                
                def _add_method_summary(label, data):
                    if not data or not data.get("results"):
                        return
                    makespan = data.get("makespan", 0)
                    if makespan <= 0:
                        return
                    stats = data.get("statistics", {})
                    violations = stats.get("total_violations", data.get("violations_total", 0))
                    missing_ids = data.get("missing_ids", [])
                    method_summaries.append({
                        "label": label,
                        "selection_variant": "best",
                        "makespan": makespan,
                        "violations": violations,
                        "missing_ids": missing_ids,
                        "statistics": stats,
                        "result_csv_name": stats.get("selected_result_csv_name", "")
                    })
                    if stats.get("all_mask_off_result_csv_name"):
                        no_mask_makespan = float(stats.get("all_mask_off_makespan_hours", 0) or 0)
                        if no_mask_makespan > 0:
                            method_summaries.append({
                                "label": f"{label}_ALL_MASK_OFF",
                                "selection_variant": "all_mask_off",
                                "makespan": no_mask_makespan,
                                "violations": int(stats.get("all_mask_off_total_violations", stats.get("all_mask_off_primary", 0)) or 0),
                                "missing_ids": [],
                                "statistics": stats,
                                "result_csv_name": stats.get("all_mask_off_result_csv_name", "")
                            })
                
                _add_method_summary("SPT", results.get("SPT"))
                _add_method_summary("LPT", results.get("LPT"))
                _add_method_summary("SEAM_MIN", results.get("SEAM_MIN"))
                _add_method_summary("GA", results.get("GA"))
                _add_method_summary("CA_CJH", results.get("CA_CJH"))
                _add_method_summary("CP_SAT", results.get("CP_SAT"))
                _add_method_summary("RL", results.get("RL"))
                
                if MODE == 2:
                    excel_entry = results.get("Excel")
                    if excel_entry:
                        variants = excel_entry.get("variants") or {}
                        if variants:
                            for plan_label, data in variants.items():
                                if data.get("results") and data.get("makespan", 0) > 0:
                                    _add_method_summary(f"엑셀 순번({plan_label})", data)
                        elif excel_entry.get("results") and excel_entry.get("makespan", 0) > 0:
                            _add_method_summary("엑셀 순번", excel_entry)
                if "ActionMasking" in results:
                    _add_method_summary("착수일기준휴리스틱", results.get("ActionMasking"))

                # [AGENT-ADD] per-method result rows for CSV export (MODE 1 only)
                if MODE == 1 and gen_context:
                    for item in method_summaries:
                        method_result_rows.append({
                            "gen": gen_context.get("gen", ds_idx + 1),
                            "seed": gen_context.get("seed"),
                            "generation_mode": gen_context.get("generation_mode"),
                            "grid_case_id": gen_context.get("grid_case_id"),
                            "distribution_profile": gen_context.get("distribution_profile"),
                            "repeat": gen_context.get("repeat"),
                            "method": item["label"],
                            "selection_variant": item.get("selection_variant", "best"),
                            "makespan_hours": item["makespan"],
                            "violations": item["violations"],
                            "missing_blocks": len(item.get("missing_ids") or []),
                            "computation_seconds": (item.get("statistics") or {}).get("computation_seconds", ""),
                            "ga_candidates_evaluated": (item.get("statistics") or {}).get("ga_candidates_evaluated", ""),
                            "ga_population": (item.get("statistics") or {}).get("ga_population", ""),
                            "ga_generations": (item.get("statistics") or {}).get("ga_generations", ""),
                            "ga_parallel_enabled": (item.get("statistics") or {}).get("ga_parallel_enabled", ""),
                            "ga_parallel_workers": (item.get("statistics") or {}).get("ga_parallel_workers", ""),
                            "ga_worker_threads": (item.get("statistics") or {}).get("ga_worker_threads", ""),
                            # [AGENT-ADD] CA-CJH constructive insertion trace metadata.
                            "ca_cjh_feature_mode": (item.get("statistics") or {}).get("ca_cjh_feature_mode", ""),
                            "ca_cjh_objective_mode": (item.get("statistics") or {}).get("ca_cjh_objective_mode", ""),
                            "ca_cjh_priority_mode": (item.get("statistics") or {}).get("ca_cjh_priority_mode", ""),
                            "ca_cjh_beta_load": (item.get("statistics") or {}).get("ca_cjh_beta_load", ""),
                            "ca_cjh_feasible_priority": (item.get("statistics") or {}).get("ca_cjh_feasible_priority", ""),
                            "ca_cjh_respect_workshop_order": (item.get("statistics") or {}).get("ca_cjh_respect_workshop_order", ""),
                            "ca_cjh_allow_forced_prefix_override": (item.get("statistics") or {}).get("ca_cjh_allow_forced_prefix_override", ""),
                            "ca_cjh_w_load": (item.get("statistics") or {}).get("ca_cjh_w_load", ""),
                            "ca_cjh_w_abnormal": (item.get("statistics") or {}).get("ca_cjh_w_abnormal", ""),
                            "ca_cjh_w_shape_dev": (item.get("statistics") or {}).get("ca_cjh_w_shape_dev", ""),
                            "ca_cjh_w_urgency": (item.get("statistics") or {}).get("ca_cjh_w_urgency", ""),
                            "ca_cjh_w_risk": (item.get("statistics") or {}).get("ca_cjh_w_risk", ""),
                            "ca_cjh_load_amplifier": (item.get("statistics") or {}).get("ca_cjh_load_amplifier", ""),
                            "ca_cjh_alpha": (item.get("statistics") or {}).get("ca_cjh_alpha", ""),
                            "ca_cjh_evaluations": (item.get("statistics") or {}).get("ca_cjh_evaluations", ""),
                            "ca_cjh_total_violation_count": (item.get("statistics") or {}).get("ca_cjh_total_violation_count", ""),
                            "ca_cjh_parallel": (item.get("statistics") or {}).get("ca_cjh_parallel", ""),
                            "ca_cjh_workers": (item.get("statistics") or {}).get("ca_cjh_workers", ""),
                            "ca_cjh_beam_width": (item.get("statistics") or {}).get("ca_cjh_beam_width", ""),
                            "ca_cjh_beam_reason": (item.get("statistics") or {}).get("ca_cjh_beam_reason", ""),
                            "cp_status": (item.get("statistics") or {}).get("cp_status", ""),
                            "cp_model_mode": (item.get("statistics") or {}).get("cp_model_mode", ""),
                            "cp_audit_status": (item.get("statistics") or {}).get("cp_audit_status", ""),
                            "cp_audit_attempts": (item.get("statistics") or {}).get("cp_audit_attempts", ""),
                            "cp_candidates_evaluated": (item.get("statistics") or {}).get("cp_candidates_evaluated", ""),
                            "cp_parallel_enabled": (item.get("statistics") or {}).get("cp_parallel_enabled", ""),
                            "cp_parallel_workers": (item.get("statistics") or {}).get("cp_parallel_workers", ""),
                            "cp_candidate_threads": (item.get("statistics") or {}).get("cp_candidate_threads", ""),
                            "cp_time_limit_sec": (item.get("statistics") or {}).get("cp_time_limit_sec", ""),
                            "cp_workers": (item.get("statistics") or {}).get("cp_workers", ""),
                            "cp_wall_time": (item.get("statistics") or {}).get("cp_wall_time", ""),
                            "cp_solver_wall_time_total": (item.get("statistics") or {}).get("cp_solver_wall_time_total", ""),
                            # [AGENT-ADD] Persist CP-SAT exact proof/objective metadata for result analysis.
                            "cp_objective_value": (item.get("statistics") or {}).get("cp_objective_value", ""),
                            "cp_best_bound": (item.get("statistics") or {}).get("cp_best_bound", ""),
                            "cp_gap": (item.get("statistics") or {}).get("cp_gap", ""),
                            "cp_selection_score_order": (item.get("statistics") or {}).get("cp_selection_score_order", ""),
                            "cp_internal_makespan_minutes": (item.get("statistics") or {}).get("cp_internal_makespan_minutes", ""),
                            "cp_selected_candidate_label": (item.get("statistics") or {}).get("cp_selected_candidate_label", ""),
                            "cp_masking_hint": (item.get("statistics") or {}).get("cp_masking_hint", ""),
                            "cp_feasibility_weight": (item.get("statistics") or {}).get("cp_feasibility_weight", ""),
                            "cp_profile_replay": (item.get("statistics") or {}).get("cp_profile_replay", ""),
                            "cp_profile_replay_top_k": (item.get("statistics") or {}).get("cp_profile_replay_top_k", ""),
                            "cp_selected_allow_forced_prefix_override": (item.get("statistics") or {}).get("cp_selected_allow_forced_prefix_override", ""),
                            "cp_selected_hard_profile": (item.get("statistics") or {}).get("cp_selected_hard_profile", ""),
                            "cp_native_primary_terms": (item.get("statistics") or {}).get("cp_native_primary_terms", ""),
                            "cp_native_raw_terms": (item.get("statistics") or {}).get("cp_native_raw_terms", ""),
                            "cp_native_constraint_counts": (item.get("statistics") or {}).get("cp_native_constraint_counts", ""),
                            "cp_native_objective_order": (item.get("statistics") or {}).get("cp_native_objective_order", ""),
                            "cp_native_prefix_balance": (item.get("statistics") or {}).get("cp_native_prefix_balance", ""),
                            "cp_basic_bay_repair_count": (item.get("statistics") or {}).get("cp_basic_bay_repair_count", ""),
                            "total_blocks": gen_context.get("total_blocks"),
                            "spread_days": gen_context.get("spread_days"),
                            "util_bucket": gen_context.get("util_bucket"),
                            "util_target": gen_context.get("util_target"),
                            "actual_util": gen_context.get("actual_util"),
                            "ps_ratio_actual": gen_context.get("ps_ratio_actual"),
                            "sub_ratio_actual": gen_context.get("sub_ratio_actual"),
                            "seam_scale": gen_context.get("seam_scale"),
                            "tact_time_scale": gen_context.get("tact_time_scale"),
                            "length_scale": gen_context.get("length_scale"),
                            "width_scale": gen_context.get("width_scale"),
                            "thickness_scale": gen_context.get("thickness_scale"),
                            "result_csv_name": item.get("result_csv_name", ""),
                            "result_folder": gen_context.get("result_folder")
                        })
                
                print(f"\n 스케줄링 방식 비교 결과")
                print("=" * 80)
                for idx, item in enumerate(method_summaries, 1):
                    print(f" {idx}. {item['label']}: makespan={item['makespan']:.2f}h, violations={item['violations']}")
                
                # [AGENT-EDIT] makespan 동률이면 위반 적은 쪽 우선
                results_comparison = [(item["label"], item["makespan"], item["violations"]) for item in method_summaries]
                results_comparison.sort(key=lambda x: (x[1], x[2]))
                
                if results_comparison:
                    print(f"\n 성능 순위 (Makespan 기준):")
                    for rank, (method, makespan, violations) in enumerate(results_comparison, 1):
                        print(f"   {rank}위: {method} - {makespan:.2f}시간 ({violations}건 위반)")
                
                if len(results_comparison) > 1:
                    print(f"\n 상대적 성능 개선:")
                    baseline_method, baseline_makespan, _ = results_comparison[-1]
                    for method, makespan, _ in results_comparison[:-1]:
                        improvement = ((baseline_makespan - makespan) / baseline_makespan) * 100
                        status_label = "개선" if improvement > 0 else "악화"
                        print(f"   {method} vs {baseline_method}: {improvement:+.1f}% ({status_label})")
                
                print(f"\n 제약조건 위반 비교:")
                for item in method_summaries:
                    print(f"   {item['label']}: {item['violations']}건")
                
                print(f"\n 누락 블록 비교:")
                for item in method_summaries:
                    missing_count = len(item["missing_ids"]) if item["missing_ids"] is not None else 0
                    print(f"   {item['label']}: {missing_count}개 누락")
                
                print(f"\n 결과 파일 저장:")
                if MODE == 2:
                    excel_entry = results.get("Excel")
                    if excel_entry:
                        variants = excel_entry.get("variants") or {}
                        if variants:
                            for plan_label, data in variants.items():
                                if data.get("results"):
                                    label_suffix = sanitize_label_for_filename(plan_label)
                                    label_suffix = f"_{label_suffix}" if label_suffix else ""
                                    print(f"   엑셀 순번({plan_label}) 결과: excel_evaluation_results{label_suffix}.csv")
                        elif excel_entry.get("results"):
                            print(f"   엑셀 순번 결과: excel_evaluation_results.csv")
                if "ActionMasking" in results and results["ActionMasking"].get("results"):
                    print(f"   착수일기준휴리스틱 결과: actionmasking_evaluation_results.csv")
                if results.get("SPT", {}).get("results"):
                    print(f"   SPT 휴리스틱 결과: spt_evaluation_results.csv")
                if results.get("LPT", {}).get("results"):
                    print(f"   LPT 휴리스틱 결과: lpt_evaluation_results.csv")
                if results.get("SEAM_MIN", {}).get("results"):
                    print(f"   SEAM_MIN 휴리스틱 결과: seam_min_evaluation_results.csv")
                if results.get("GA", {}).get("results"):
                    print(f"   GA baseline 결과: ga_evaluation_results.csv")
                if results.get("CA_CJH", {}).get("results"):
                    print(f"   CA-CJH-Insertion 결과: ca_cjh_evaluation_results.csv")
                if results.get("CP_SAT", {}).get("results"):
                    print(f"   CP-SAT baseline 결과: cp_sat_evaluation_results.csv")
                if results.get("RL", {}).get("results"):
                    print(f"   RL 모델 결과: rl_best_results.csv")
                
                # 문제별 요약 저장
                rank_map = {label: idx + 1 for idx, (label, _, _) in enumerate(results_comparison)}
                problem_summary = {
                    "gen": ds_idx + 1,
                    "result_folder": result_folder_name,
                    "num_blocks": len(blocks),
                    "methods": {item["label"]: item for item in method_summaries},
                    "ranking": results_comparison,
                    "rank_map": rank_map
                }
                all_problem_summaries.append(problem_summary)
                for item in method_summaries:
                    label = item["label"]
                    if label not in overall_stats:
                        overall_stats[label] = {"makespan": [], "violations": []}
                    overall_stats[label]["makespan"].append(item["makespan"])
                    overall_stats[label]["violations"].append(item["violations"])
                # [AGENT-EDIT] CSV 출력 블록 들여쓰기/스코프 정리
                # 생성된 파일 목록
                print(f"\n📁 생성된 상세 CSV 파일들 (integrated_learning_and_scheduling.py와 동일 형식):")
                
                # MODE 2에서는 엑셀과 착수일기준휴리스틱 파일들도 포함
                if MODE == 2:
                    # 엑셀 방식 파일들
                    excel_entry = results.get("Excel")
                    if excel_entry:
                        variants = excel_entry.get("variants") or {}
                        if variants:
                            for plan_label, data in variants.items():
                                if data.get("makespan", 0) > 0:
                                    label_suffix = sanitize_label_for_filename(plan_label)
                                    label_suffix = f"_{label_suffix}" if label_suffix else ""
                                    print(f"   EXCEL 방식({plan_label}):")
                                    main_file = (
                                        os.path.join(result_folder_path, f"detailed_excel_constraint_schedule{label_suffix}.csv")
                                        if result_folder_path
                                        else f"detailed_excel_constraint_schedule{label_suffix}.csv"
                                    )
                                    if os.path.exists(main_file):
                                        print(f"     - {os.path.basename(main_file)}")
                                    variant_date_keys = data.get("date_keys") or date_keys
                                    for date_key in variant_date_keys:
                                        process_file = (
                                            os.path.join(result_folder_path, f"detailed_excel_schedule_processes{label_suffix}_{date_key}.csv")
                                            if result_folder_path
                                            else f"detailed_excel_schedule_processes{label_suffix}_{date_key}.csv"
                                        )
                                        if os.path.exists(process_file):
                                            print(f"     - {os.path.basename(process_file)}")
                                        info_file = (
                                            os.path.join(result_folder_path, f"detailed_excel_schedule_info{label_suffix}_{date_key}.csv")
                                            if result_folder_path
                                            else f"detailed_excel_schedule_info{label_suffix}_{date_key}.csv"
                                        )
                                        if os.path.exists(info_file):
                                            print(f"     - {os.path.basename(info_file)}")
                                        bay_file = (
                                            os.path.join(result_folder_path, f"detailed_excel_bayselect_info{label_suffix}_{date_key}.csv")
                                            if result_folder_path
                                            else f"detailed_excel_bayselect_info{label_suffix}_{date_key}.csv"
                                        )
                                        if os.path.exists(bay_file):
                                            print(f"     - {os.path.basename(bay_file)}")
                        elif excel_entry.get("makespan", 0) > 0:
                            print(f"   EXCEL 방식:")
                            main_file = (
                                os.path.join(result_folder_path, "detailed_excel_constraint_schedule.csv")
                                if result_folder_path
                                else "detailed_excel_constraint_schedule.csv"
                            )
                            if os.path.exists(main_file):
                                print(f"     - detailed_excel_constraint_schedule.csv")
                            for date_key in date_keys:
                                process_file = (
                                    os.path.join(result_folder_path, f"detailed_excel_schedule_processes_{date_key}.csv")
                                    if result_folder_path
                                    else f"detailed_excel_schedule_processes_{date_key}.csv"
                                )
                                if os.path.exists(process_file):
                                    print(f"     - detailed_excel_schedule_processes_{date_key}.csv")
                                info_file = (
                                    os.path.join(result_folder_path, f"detailed_excel_schedule_info_{date_key}.csv")
                                    if result_folder_path
                                    else f"detailed_excel_schedule_info_{date_key}.csv"
                                )
                                if os.path.exists(info_file):
                                    print(f"     - detailed_excel_schedule_info_{date_key}.csv")
                                bay_file = (
                                    os.path.join(result_folder_path, f"detailed_excel_bayselect_info_{date_key}.csv")
                                    if result_folder_path
                                    else f"detailed_excel_bayselect_info_{date_key}.csv"
                                )
                                if os.path.exists(bay_file):
                                    print(f"     - detailed_excel_bayselect_info_{date_key}.csv")
                
                # 착수일기준휴리스틱 방식 파일들
                if "ActionMasking" in results and results["ActionMasking"]["makespan"] > 0:
                    print(f"   ACTION MASKING 방식:")
                    for date_key in date_keys:
                        process_file = (
                            os.path.join(result_folder_path, f"detailed_actionmasking_schedule_processes_{date_key}.csv")
                            if result_folder_path
                            else f"detailed_actionmasking_schedule_processes_{date_key}.csv"
                        )
                        schedule_file = (
                            os.path.join(result_folder_path, f"detailed_actionmasking_schedule_info_{date_key}.csv")
                            if result_folder_path
                            else f"detailed_actionmasking_schedule_info_{date_key}.csv"
                        )
                        bay_file = (
                            os.path.join(result_folder_path, f"detailed_actionmasking_bayselect_info_{date_key}.csv")
                            if result_folder_path
                            else f"detailed_actionmasking_bayselect_info_{date_key}.csv"
                        )
                        if os.path.exists(process_file):
                            print(f"     - detailed_actionmasking_schedule_processes_{date_key}.csv")
                        if os.path.exists(schedule_file):
                            print(f"     - detailed_actionmasking_schedule_info_{date_key}.csv")
                        if os.path.exists(bay_file):
                            print(f"     - detailed_actionmasking_bayselect_info_{date_key}.csv")
                
                # SPT, SEAM_MIN, RL 방식 파일들
                for method in ["spt", "seam_min", "rl"]:
                    if method.upper() in results and results[method.upper()]["makespan"] > 0:
                        print(f"   {method.upper()} 방식:")
                        for date_key in date_keys:
                            process_file = (
                                os.path.join(result_folder_path, f"detailed_{method}_schedule_processes_{date_key}.csv")
                                if result_folder_path
                                else f"detailed_{method}_schedule_processes_{date_key}.csv"
                            )
                            schedule_file = (
                                os.path.join(result_folder_path, f"detailed_{method}_schedule_info_{date_key}.csv")
                                if result_folder_path
                                else f"detailed_{method}_schedule_info_{date_key}.csv"
                            )
                            bay_file = (
                                os.path.join(result_folder_path, f"detailed_{method}_bayselect_info_{date_key}.csv")
                                if result_folder_path
                                else f"detailed_{method}_bayselect_info_{date_key}.csv"
                            )
                            if os.path.exists(process_file):
                                print(f"     - detailed_{method}_schedule_processes_{date_key}.csv")
                            if os.path.exists(schedule_file):
                                print(f"     - detailed_{method}_schedule_info_{date_key}.csv")
                            if os.path.exists(bay_file):
                                print(f"     - detailed_{method}_bayselect_info_{date_key}.csv")
                
                print(f"\n✅ 포괄적 평가 완료!")
      
        except Exception as e:
            print(f"❌ 평가 중 오류 발생: {e}")
            traceback.print_exc()

        # [AGENT-ADD] 모든 gen 종료 후 전체 요약 출력
        if all_problem_summaries:
            print(f"\n 전체 gen 요약 (평균/분산)")
            for label, stats in overall_stats.items():
                makespans = stats.get("makespan", [])
                violations = stats.get("violations", [])
                if not makespans:
                    continue
                ms_mean = float(np.mean(makespans))
                ms_var = float(np.var(makespans))
                viol_mean = float(np.mean(violations)) if violations else 0.0
                viol_var = float(np.var(violations)) if violations else 0.0
                print(
                    f"   {label}: makespan 평균={ms_mean:.2f}h, 분산={ms_var:.4f}, "
                    f"위반 평균={viol_mean:.2f}, 분산={viol_var:.4f} (n={len(makespans)})"
                )

            print(f"\n 전체 문제별 결과/순위 요약")
            for summary in all_problem_summaries:
                gen_id = summary.get("gen", "?")
                num_blocks = summary.get("num_blocks", 0)
                result_folder = summary.get("result_folder", "")
                print(f"   [gen{gen_id}] 블록 {num_blocks}개 | 폴더={result_folder}")
                ranking = summary.get("ranking", [])
                for rank, (method, makespan, violations) in enumerate(ranking, 1):
                    print(f"     {rank}위: {method} - {makespan:.2f}시간 ({violations}건 위반)")

        # [AGENT-ADD] MODE 1 CSV outputs for plotting (parameters + results)
        if MODE == 1 and run_base_folder:
            try:
                if gen_param_rows:
                    params_path = os.path.join(run_base_folder, "gen_parameters.csv")
                    pd.DataFrame(gen_param_rows).to_csv(params_path, index=False)
                    print(f"\n📌 gen 파라미터 저장: {params_path}")
                if method_result_rows:
                    results_path = os.path.join(run_base_folder, "method_results.csv")
                    results_df = pd.DataFrame(method_result_rows)
                    results_df.to_csv(results_path, index=False)
                    print(f"📌 방법별 결과 저장: {results_path}")
                    # [AGENT-ADD] Fixed-grid summaries for generated-data experiments.
                    if "distribution_profile" in results_df.columns:
                        profile_summary_df = (
                            results_df
                            .groupby(["distribution_profile", "method"], dropna=False)
                            .agg(
                                n=("makespan_hours", "count"),
                                makespan_mean=("makespan_hours", "mean"),
                                makespan_std=("makespan_hours", "std"),
                                violations_mean=("violations", "mean"),
                                violations_std=("violations", "std"),
                            )
                            .reset_index()
                        )
                        profile_summary_path = os.path.join(run_base_folder, "distribution_profile_summary.csv")
                        profile_summary_df.to_csv(profile_summary_path, index=False)
                        print(f"📌 분포별 요약 저장: {profile_summary_path}")
                    block_count_summary_df = (
                        results_df
                        .groupby(["total_blocks", "method"], dropna=False)
                        .agg(
                            n=("makespan_hours", "count"),
                            makespan_mean=("makespan_hours", "mean"),
                            makespan_std=("makespan_hours", "std"),
                            violations_mean=("violations", "mean"),
                            violations_std=("violations", "std"),
                        )
                        .reset_index()
                        .sort_values(["total_blocks", "method"])
                    )
                    block_count_summary_path = os.path.join(run_base_folder, "block_count_summary.csv")
                    block_count_summary_df.to_csv(block_count_summary_path, index=False)
                    print(f"📌 블록수별 요약 저장: {block_count_summary_path}")
                    if "grid_case_id" in results_df.columns and results_df["grid_case_id"].fillna("").astype(str).str.len().gt(0).any():
                        grid_case_summary_df = (
                            results_df
                            .groupby(["grid_case_id", "total_blocks", "distribution_profile", "method"], dropna=False)
                            .agg(
                                n=("makespan_hours", "count"),
                                makespan_mean=("makespan_hours", "mean"),
                                makespan_std=("makespan_hours", "std"),
                                violations_mean=("violations", "mean"),
                                violations_std=("violations", "std"),
                            )
                            .reset_index()
                            .sort_values(["total_blocks", "distribution_profile", "method"])
                        )
                        grid_case_summary_path = os.path.join(run_base_folder, "grid_case_summary.csv")
                        grid_case_summary_df.to_csv(grid_case_summary_path, index=False)
                        print(f"📌 고정 격자 케이스 요약 저장: {grid_case_summary_path}")
                    # [AGENT-ADD] util_bucket별 평균/분산 요약 CSV
                    if generation_grid_cases:
                        bucket_order = list(dict.fromkeys(str(case.get("name", "")) for case in generation_grid_cases))
                    else:
                        bucket_order = [bucket.get("name", "") for bucket in UTIL_BUCKETS]
                    summary_df = (
                        results_df
                        .groupby(["util_bucket", "method"], dropna=False)
                        .agg(
                            n=("makespan_hours", "count"),
                            makespan_mean=("makespan_hours", "mean"),
                            makespan_std=("makespan_hours", "std"),
                            violations_mean=("violations", "mean"),
                            violations_std=("violations", "std"),
                            actual_util_mean=("actual_util", "mean"),
                            actual_util_std=("actual_util", "std"),
                        )
                        .reset_index()
                    )
                    if bucket_order:
                        summary_df["util_bucket"] = pd.Categorical(
                            summary_df["util_bucket"],
                            categories=bucket_order,
                            ordered=True
                        )
                        summary_df = summary_df.sort_values(["util_bucket", "method"])
                    summary_path = os.path.join(run_base_folder, "util_bucket_summary.csv")
                    summary_df.to_csv(summary_path, index=False)
                    print(f"📌 util_bucket 요약 저장: {summary_path}")

                    # [AGENT-ADD] matplotlib 그래프 자동 생성
                    try:
                        import matplotlib
                        matplotlib.use("Agg")
                        import matplotlib.pyplot as plt

                        # [AGENT-EDIT] Paper plots exclude *_ALL_MASK_OFF rows; those are saved as separate comparison baselines.
                        plot_df = results_df.copy()
                        if "selection_variant" in plot_df.columns:
                            plot_df = plot_df[plot_df["selection_variant"].fillna("best").astype(str) != "all_mask_off"].copy()
                        plot_df = plot_df[~plot_df["method"].astype(str).str.endswith("_ALL_MASK_OFF")].copy()

                        # [AGENT-EDIT] LPT 포함 (MODE 1에서도 선택 가능)
                        method_order_default = ["SPT", "LPT", "SEAM_MIN", "GA", "CA_CJH", "CP_SAT", "RL", "착수일기준휴리스틱"]
                        available_methods = list(plot_df["method"].unique())
                        method_order = [m for m in method_order_default if m in available_methods]
                        for m in available_methods:
                            if m not in method_order:
                                method_order.append(m)

                        # [AGENT-ADD] Size buckets for panel-style plots
                        def _size_bucket(blocks: float) -> str:
                            try:
                                val = float(blocks)
                            except (TypeError, ValueError):
                                return "Unknown"
                            if val <= 80:
                                return "Small (20-80)"
                            if val <= 140:
                                return "Medium (81-140)"
                            return "Large (141-200)"

                        plot_df["size_bucket"] = plot_df["total_blocks"].apply(_size_bucket)
                        size_bucket_order = ["Small (20-80)", "Medium (81-140)", "Large (141-200)"]

                        summary_df_plot = (
                            plot_df
                            .groupby(["util_bucket", "method"], dropna=False)
                            .agg(
                                n=("makespan_hours", "count"),
                                makespan_mean=("makespan_hours", "mean"),
                                makespan_std=("makespan_hours", "std"),
                                violations_mean=("violations", "mean"),
                                violations_std=("violations", "std"),
                                actual_util_mean=("actual_util", "mean"),
                                actual_util_std=("actual_util", "std"),
                            )
                            .reset_index()
                        )
                        if bucket_order:
                            summary_df_plot["util_bucket"] = pd.Categorical(
                                summary_df_plot["util_bucket"],
                                categories=bucket_order,
                                ordered=True
                            )
                            summary_df_plot = summary_df_plot.sort_values(["util_bucket", "method"])

                        size_summary_df = (
                            plot_df
                            .groupby(["size_bucket", "method"], dropna=False)
                            .agg(
                                n=("makespan_hours", "count"),
                                makespan_mean=("makespan_hours", "mean"),
                                makespan_std=("makespan_hours", "std"),
                                violations_mean=("violations", "mean"),
                                violations_std=("violations", "std"),
                                actual_util_mean=("actual_util", "mean"),
                                actual_util_std=("actual_util", "std"),
                            )
                            .reset_index()
                        )
                        size_summary_df["size_bucket"] = pd.Categorical(
                            size_summary_df["size_bucket"],
                            categories=size_bucket_order,
                            ordered=True
                        )
                        size_summary_df = size_summary_df.sort_values(["size_bucket", "method"])
                        size_summary_path = os.path.join(run_base_folder, "size_bucket_summary.csv")
                        size_summary_df.to_csv(size_summary_path, index=False)
                        print(f"📌 size_bucket 요약 저장: {size_summary_path}")

                        # Style setup
                        try:
                            plt.style.use("seaborn-v0_8-whitegrid")
                        except Exception:
                            pass
                        plt.rcParams.update({
                            "axes.titlesize": 12,
                            "axes.labelsize": 11,
                            "legend.fontsize": 9,
                            "font.size": 10,
                        })
                        color_map = {
                            "SPT": "#4C78A8",
                            "LPT": "#3B5BA5",
                            "SEAM_MIN": "#54A24B",
                            "GA": "#F58518",
                            "CA_CJH": "#8E6C8A",
                            "CP_SAT": "#B279A2",
                            "RL": "#E45756",
                            "착수일기준휴리스틱": "#72B7B2",
                        }
                        marker_map = {
                            "SPT": "o",
                            "LPT": "v",
                            "SEAM_MIN": "^",
                            "GA": "s",
                            "CA_CJH": "h",
                            "CP_SAT": "X",
                            "RL": "D",
                            "착수일기준휴리스틱": "P",
                        }

                        def _plot_grouped_metric(metric: str, ylabel: str, filename: str) -> None:
                            if not bucket_order:
                                return
                            fig, ax = plt.subplots(figsize=(10, 6))
                            x = np.arange(len(bucket_order))
                            n_methods = max(len(method_order), 1)
                            width = min(0.2, 0.8 / n_methods)
                            offset_base = (n_methods - 1) * width / 2
                            for idx, method in enumerate(method_order):
                                values = []
                                for bucket in bucket_order:
                                    subset = summary_df_plot[
                                        (summary_df_plot["util_bucket"] == bucket) &
                                        (summary_df_plot["method"] == method)
                                    ]
                                    if not subset.empty:
                                        values.append(float(subset[f"{metric}_mean"].iloc[0]))
                                    else:
                                        values.append(np.nan)
                                ax.bar(
                                    x + idx * width - offset_base,
                                    values,
                                    width,
                                    label=method,
                                    color=color_map.get(method),
                                    edgecolor="white"
                                )
                            ax.set_xticks(x)
                            ax.set_xticklabels(bucket_order)
                            ax.set_ylabel(ylabel)
                            ax.set_xlabel("util_bucket")
                            ax.set_title(f"{metric} by util_bucket")
                            ax.legend(loc="best")
                            fig.tight_layout()
                            fig_path = os.path.join(run_base_folder, filename)
                            fig.savefig(fig_path, dpi=200)
                            plt.close(fig)

                        def _plot_grouped_metric_size(metric: str, ylabel: str, filename: str) -> None:
                            fig, ax = plt.subplots(figsize=(10, 6))
                            x = np.arange(len(size_bucket_order))
                            n_methods = max(len(method_order), 1)
                            width = min(0.2, 0.8 / n_methods)
                            offset_base = (n_methods - 1) * width / 2
                            for idx, method in enumerate(method_order):
                                values = []
                                for bucket in size_bucket_order:
                                    subset = size_summary_df[
                                        (size_summary_df["size_bucket"] == bucket) &
                                        (size_summary_df["method"] == method)
                                    ]
                                    if not subset.empty:
                                        values.append(float(subset[f"{metric}_mean"].iloc[0]))
                                    else:
                                        values.append(np.nan)
                                ax.bar(
                                    x + idx * width - offset_base,
                                    values,
                                    width,
                                    label=method,
                                    color=color_map.get(method),
                                    edgecolor="white"
                                )
                            ax.set_xticks(x)
                            ax.set_xticklabels(size_bucket_order)
                            ax.set_ylabel(ylabel)
                            ax.set_xlabel("instance size")
                            ax.set_title(f"{metric} by size bucket")
                            ax.legend(loc="best")
                            fig.tight_layout()
                            fig_path = os.path.join(run_base_folder, filename)
                            fig.savefig(fig_path, dpi=200)
                            plt.close(fig)

                        def _plot_scatter(metric: str, ylabel: str, filename: str) -> None:
                            fig, ax = plt.subplots(figsize=(8, 6))
                            for method in method_order:
                                subset = plot_df[plot_df["method"] == method]
                                if subset.empty:
                                    continue
                                ax.scatter(
                                    subset["actual_util"],
                                    subset[metric],
                                    label=method,
                                    alpha=0.7
                                )
                            ax.set_xlabel("actual_util (blocks / (17 * days))")
                            ax.set_ylabel(ylabel)
                            ax.set_title(f"{metric} vs actual_util")
                            ax.legend(loc="best")
                            fig.tight_layout()
                            fig_path = os.path.join(run_base_folder, filename)
                            fig.savefig(fig_path, dpi=200)
                            plt.close(fig)

                        def _plot_panel(metric: str, ylabel: str, filename: str) -> None:
                            fig, axes = plt.subplots(1, len(size_bucket_order), figsize=(15, 4), sharey=True)
                            util_positions = {bucket: idx for idx, bucket in enumerate(bucket_order)}
                            for ax, size_bucket in zip(axes, size_bucket_order):
                                subset_bucket = plot_df[plot_df["size_bucket"] == size_bucket]
                                for idx, method in enumerate(method_order):
                                    subset = subset_bucket[subset_bucket["method"] == method]
                                    if subset.empty:
                                        continue
                                    x_vals = subset["util_bucket"].map(util_positions).astype(float)
                                    jitter = (idx - (len(method_order) - 1) / 2) * 0.06
                                    ax.scatter(
                                        x_vals + jitter,
                                        subset[metric],
                                        label=method,
                                        alpha=0.75,
                                        s=35,
                                        color=color_map.get(method),
                                        marker=marker_map.get(method, "o")
                                    )
                                ax.set_title(size_bucket)
                                ax.set_xticks(np.arange(len(bucket_order)))
                                ax.set_xticklabels(bucket_order)
                                ax.set_xlabel("util_bucket")
                            axes[0].set_ylabel(ylabel)
                            handles, labels = axes[0].get_legend_handles_labels()
                            fig.legend(handles, labels, loc="upper center", ncol=min(len(method_order), 5))
                            fig.suptitle(f"{metric} comparison by size bucket", y=1.04)
                            fig.tight_layout()
                            fig_path = os.path.join(run_base_folder, filename)
                            fig.savefig(fig_path, dpi=200, bbox_inches="tight")
                            plt.close(fig)

                        _plot_grouped_metric("makespan", "Makespan (h)", "plot_makespan_by_util_bucket.png")
                        _plot_grouped_metric("violations", "Violations", "plot_violations_by_util_bucket.png")
                        _plot_grouped_metric_size("makespan", "Makespan (h)", "plot_makespan_by_size_bucket.png")
                        _plot_grouped_metric_size("violations", "Violations", "plot_violations_by_size_bucket.png")
                        _plot_scatter("makespan_hours", "Makespan (h)", "plot_makespan_scatter_util.png")
                        _plot_scatter("violations", "Violations", "plot_violations_scatter_util.png")
                        _plot_panel("makespan_hours", "Makespan (h)", "plot_panel_makespan.png")
                        _plot_panel("violations", "Violations", "plot_panel_violations.png")
                        print("📌 그래프 저장: plot_makespan_by_util_bucket.png, plot_violations_by_util_bucket.png, plot_makespan_by_size_bucket.png, plot_violations_by_size_bucket.png, plot_makespan_scatter_util.png, plot_violations_scatter_util.png, plot_panel_makespan.png, plot_panel_violations.png")
                    except Exception as plot_err:
                        print(f"❌ 그래프 생성 실패: {plot_err}")
            except Exception as csv_err:
                print(f"❌ CSV 저장 실패: {csv_err}")

    except Exception as e:
        print(f"❌ 평가 실행 실패: {e}")
        traceback.print_exc()

if __name__ == "__main__":
    main()
