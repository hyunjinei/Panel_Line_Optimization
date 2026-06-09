# -*- coding: utf-8 -*-
"""평가 방법 실행 모음 (runner.py에서 분리)."""
# [AGENT-ADD] runner.py에서 분리한 평가 메서드

from __future__ import annotations

import copy
import contextlib
import os
import random
import traceback
import time
import multiprocessing as mp
from datetime import datetime, timedelta
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import torch

from runtime_config import get_runtime_config, set_runtime_config
from enhanced_environment.constraints import ConstraintConfig
from enhanced_environment.constraints.managers import CalendarManager
from scheduling.assembly_start.action_sequence_조립착수일기준휴리스틱 import (
    run_assembly_decoding_sequence_with_blocks,
    save_detailed_process_schedule_assembly,
    create_block_result
)
from scheduling.start_date.action_sequence_착수일기준휴리스틱 import create_actionmasking_schedule
from scheduling.performance_replay.excel_실적데이터_순번기반시퀀싱 import (
    create_makespan_schedule,
    apply_plan_sequence_from_excel,
    PLAN_SHEET_PRIORITY,
)
from utils.csv_save import (
    save_assembly_decoding_schedule_info,
    save_assembly_decoding_bay_info,
)

from PPO.models.single_step_actor import (
    SingleStepPtrNet,
    ENV_STATE_DIM,
    BLOCK_FEATURE_DIM,
    REDUCED_ENV_STATE_DIM,
    REDUCED_BLOCK_FEATURE_DIM,
    CONSTRAINT_ENV_STATE_DIM,
    CONSTRAINT_BLOCK_FEATURE_DIM,
    DIFF_ENV_STATE_DIM,
    DIFF_BLOCK_FEATURE_DIM,
)
from scheduling.assembly_start.rl_assembly_scheduler import run_rl_assembly_decoding_sequence_with_blocks
from scheduling.common.cjh_panel_insertion import run_ca_cjh_insertion

from PPO.eval.helpers import sanitize_label_for_filename
from PPO.eval.files import (
    rename_excel_detailed_csv_files,
    rename_actionmasking_detailed_csv_files,
    rename_detailed_csv_files,
)


_EVAL_RL_WORKER_ACTOR = None
_EVAL_RL_WORKER_DEVICE = None
_EVAL_RL_WORKER_BLOCKS = None
_EVAL_RL_WORKER_METADATA = None
_EVAL_RL_WORKER_START_DATE = None
_EVAL_RL_WORKER_MAX_DAYS = 20
_EVAL_RL_WORKER_DATE_OFFSET = 0
_GA_WORKER_BLOCKS = None
_GA_WORKER_METADATA = None
_GA_WORKER_START_DATE = None
_GA_WORKER_MAX_DAYS = 20
_GA_WORKER_DATE_OFFSET = 0
_CP_REPLAY_WORKER_BLOCKS = None
_CP_REPLAY_WORKER_METADATA = None
_CP_REPLAY_WORKER_START_DATE = None
_CP_REPLAY_WORKER_MAX_DAYS = 20
_CP_REPLAY_WORKER_DATE_OFFSET = 0


def _get_setting(settings: Optional[Dict[str, object]], key: str, default: object) -> object:
    if not settings:
        return default
    return settings.get(key, default)


def _get_total_memory_gb() -> Optional[float]:
    """[AGENT-ADD] Return total RAM in GiB without requiring psutil."""
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return float(pages * page_size) / (1024 ** 3)
    except Exception:
        return None


@contextlib.contextmanager
def _eval_worker_output_context():
    """[AGENT-ADD] Suppress noisy parallel eval worker logs unless requested."""
    keep_logs = os.environ.get("PBS_EVAL_WORKER_LOG", "").strip().lower() in {"1", "true", "on", "yes"}
    force_debug = os.environ.get("PBS_FORCE_DEBUG", "").strip().lower() in {"1", "true", "on", "yes"}
    verbose_debug = os.environ.get("PBS_DEBUG_VERBOSE", "").strip().lower() in {"1", "true", "on", "yes"}
    if keep_logs or force_debug or verbose_debug:
        yield
        return
    with open(os.devnull, "w") as devnull:
        with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
            yield


def _seed_eval_task(seed: int, device: torch.device) -> None:
    """[AGENT-ADD] Seed one parallel eval candidate."""
    seed = int(seed or 0) % (2 ** 31 - 1)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if device.type == "cuda" and torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _init_eval_rl_worker(
    actor_params: Dict,
    actor_state_dict: Dict[str, torch.Tensor],
    device_str: str,
    blocks: List,
    metadata: Dict,
    start_date: str,
    base_runtime_cfg: Dict,
    max_days: int,
    date_offset: int,
    worker_torch_threads: int,
) -> None:
    """[AGENT-ADD] Initialize one process for parallel RL eval candidates."""
    global _EVAL_RL_WORKER_ACTOR
    global _EVAL_RL_WORKER_DEVICE
    global _EVAL_RL_WORKER_BLOCKS
    global _EVAL_RL_WORKER_METADATA
    global _EVAL_RL_WORKER_START_DATE
    global _EVAL_RL_WORKER_MAX_DAYS
    global _EVAL_RL_WORKER_DATE_OFFSET

    try:
        torch.set_num_threads(max(1, int(worker_torch_threads or 1)))
        torch.set_num_interop_threads(1)
    except Exception:
        pass

    requested_device = torch.device(device_str)
    if requested_device.type == "cuda" and not torch.cuda.is_available():
        requested_device = torch.device("cpu")

    set_runtime_config(copy.deepcopy(base_runtime_cfg or {}))
    actor = SingleStepPtrNet(**copy.deepcopy(actor_params)).to(requested_device)
    actor.load_state_dict(actor_state_dict, strict=False)
    actor.default_decode_type = "sampling"
    actor.eval()

    _EVAL_RL_WORKER_ACTOR = actor
    _EVAL_RL_WORKER_DEVICE = requested_device
    _EVAL_RL_WORKER_BLOCKS = blocks
    _EVAL_RL_WORKER_METADATA = metadata
    _EVAL_RL_WORKER_START_DATE = start_date
    _EVAL_RL_WORKER_MAX_DAYS = int(max_days)
    _EVAL_RL_WORKER_DATE_OFFSET = int(date_offset)


def _run_parallel_eval_rl_task(task: Dict) -> Dict:
    """[AGENT-ADD] Execute one RL eval candidate in a worker process."""
    try:
        if _EVAL_RL_WORKER_ACTOR is None:
            raise RuntimeError("eval RL worker is not initialized")
        device = _EVAL_RL_WORKER_DEVICE or torch.device("cpu")
        _seed_eval_task(int(task.get("seed", 0) or 0), device)
        set_runtime_config(copy.deepcopy(task.get("runtime_cfg") or {}))
        start_t = time.perf_counter()
        with _eval_worker_output_context():
            with torch.no_grad():
                schedule_results, stats, _, _ = run_rl_assembly_decoding_sequence_with_blocks(
                    blocks=_EVAL_RL_WORKER_BLOCKS,
                    metadata=_EVAL_RL_WORKER_METADATA,
                    rl_agent=_EVAL_RL_WORKER_ACTOR,
                    device=device,
                    start_date=_EVAL_RL_WORKER_START_DATE,
                    max_days=_EVAL_RL_WORKER_MAX_DAYS,
                    date_offset=_EVAL_RL_WORKER_DATE_OFFSET,
                    save_csv=False,
                    save_detailed=False,
                    training_mode=False,
                    collect_episode_data=False,
                    enable_grad=False,
                    collect_step_metrics=False,
                )
        return {
            "ok": True,
            "sample_id": task.get("sample_id"),
            "profile_sample_id": task.get("profile_sample_id"),
            "bias_label": task.get("bias_label"),
            "hard_label": task.get("hard_label"),
            "schedule_results": schedule_results,
            "stats": stats or {},
            "seconds": time.perf_counter() - start_t,
        }
    except Exception:
        return {
            "ok": False,
            "sample_id": task.get("sample_id"),
            "bias_label": task.get("bias_label"),
            "hard_label": task.get("hard_label"),
            "error": traceback.format_exc(),
        }


def _resolve_eval_parallel_plan(task_count: int, device: torch.device, settings: Optional[Dict[str, object]]) -> Dict:
    """[AGENT-ADD] Dynamically size eval workers from CPU/GPU/RAM."""
    mode = str(_get_setting(settings, "eval_parallel", _get_setting(settings, "rl_eval_parallel", "auto"))).strip().lower()
    if mode in {"0", "false", "off", "no"}:
        return {"enabled": False, "reason": "disabled"}
    if task_count < 4 and mode == "auto":
        return {"enabled": False, "reason": "small_task_count"}

    cpu_count = os.cpu_count() or 1
    worker_device = str(_get_setting(settings, "eval_parallel_device", "auto")).strip().lower()
    if worker_device == "auto":
        worker_device = "cuda" if device.type == "cuda" and torch.cuda.is_available() else "cpu"
    if worker_device == "cuda" and not torch.cuda.is_available():
        worker_device = "cpu"

    requested_workers = int(_get_setting(settings, "eval_parallel_workers", 0) or 0)
    worker_threads = int(_get_setting(settings, "eval_worker_threads", 0) or 0)
    if worker_threads <= 0:
        worker_threads = 1

    reserve_cores = max(2, cpu_count // 16)
    cpu_limit = max(1, cpu_count - reserve_cores)
    ram_gb = _get_total_memory_gb()
    # [AGENT-EDIT] Allow yaml to drive resource sizing so commands do not need env vars.
    per_worker_ram_value = _get_setting(settings, "eval_ram_gb_per_worker", None)
    if per_worker_ram_value is None:
        per_worker_ram_value = os.environ.get("PBS_EVAL_RAM_GB_PER_WORKER", "1.0")
    per_worker_ram = float(per_worker_ram_value)
    ram_limit = int(ram_gb / max(0.1, per_worker_ram)) if ram_gb else cpu_limit
    gpu_gb = None
    gpu_limit = cpu_limit
    per_worker_gpu = None
    max_gpu_workers = cpu_limit
    if worker_device == "cuda":
        try:
            free_bytes, total_bytes = torch.cuda.mem_get_info()
            gpu_gb = float(free_bytes) / (1024 ** 3)
            per_worker_gpu_value = _get_setting(
                settings,
                "eval_gpu_gb_per_worker",
                _get_setting(settings, "eval_gpu_mem_per_worker_gb", None),
            )
            if per_worker_gpu_value is None:
                per_worker_gpu_value = os.environ.get("PBS_EVAL_GPU_GB_PER_WORKER", "0.75")
            per_worker_gpu = float(per_worker_gpu_value)
            gpu_limit = int(gpu_gb / max(0.1, per_worker_gpu))
        except Exception:
            gpu_limit = cpu_limit
        max_gpu_workers_value = _get_setting(settings, "eval_max_gpu_workers", None)
        if max_gpu_workers_value is None:
            max_gpu_workers_value = os.environ.get("PBS_EVAL_MAX_GPU_WORKERS", "0")
        try:
            max_gpu_workers = int(max_gpu_workers_value)
        except Exception:
            max_gpu_workers = 0
        if max_gpu_workers <= 0:
            max_gpu_workers = cpu_limit
        gpu_limit = min(gpu_limit, max_gpu_workers)

    if requested_workers > 0:
        workers = requested_workers
    else:
        workers = min(task_count, cpu_limit, max(1, ram_limit), max(1, gpu_limit))
    workers = max(1, min(int(workers), int(task_count)))
    if workers <= 1 and mode == "auto":
        return {"enabled": False, "reason": "single_worker"}
    return {
        "enabled": True,
        "workers": workers,
        "worker_device": worker_device,
        "worker_threads": worker_threads,
        "cpu_count": cpu_count,
        "cpu_limit": cpu_limit,
        "ram_gb": ram_gb,
        "ram_limit": ram_limit,
        "per_worker_ram_gb": per_worker_ram,
        "gpu_gb": gpu_gb,
        "gpu_limit": gpu_limit,
        "per_worker_gpu_gb": per_worker_gpu,
        "max_gpu_workers": max_gpu_workers,
        "task_count": task_count,
    }


def run_excel_heuristic(
    blocks: List,
    metadata: Dict,
    start_date: str,
    result_folder: str = None,
    plan_label: Optional[str] = None,
) -> Tuple[List[Dict], Dict, List[Dict], object]:
    """
    엑셀 순번 기반 휴리스틱 실행 (MODE 2에서만 사용)
    integrated_learning_and_scheduling.py와 동일한 방식
    """
    if plan_label:
        print(f" 엑셀 순번 기반 휴리스틱 실행 중... (계획 시트: {plan_label})")
    else:
        print(" 엑셀 순번 기반 휴리스틱 실행 중...")

    raw_label = (plan_label or "").strip()
    label_suffix = sanitize_label_for_filename(plan_label)
    if raw_label in ("기본", "default", "DEFAULT"):
        label_suffix = ""
    elif label_suffix:
        label_suffix = f"_{label_suffix}"

    suffixed_output_csv = None
    if label_suffix:
        suffixed_output_csv = (
            os.path.join(result_folder, f"excel_evaluation_results{label_suffix}.csv")
            if result_folder
            else f"excel_evaluation_results{label_suffix}.csv"
        )
        if os.path.exists(suffixed_output_csv):
            os.remove(suffixed_output_csv)

    try:
        # 🆕 현재 작업 디렉토리 변경 (CSV 파일이 결과 폴더에 생성되도록)
        original_cwd = os.getcwd()
        if result_folder:
            os.chdir(result_folder)

        try:
            # integrated_learning_and_scheduling.py와 동일한 방식으로 실행
            excel_results, excel_violations, excel_stats = create_makespan_schedule(blocks, metadata)
        finally:
            # 작업 디렉토리 복원
            os.chdir(original_cwd)

        statistics = {
            'makespan_hours': float(excel_stats.get('makespan_hours', 0.0)),
            'total_violations': int(excel_stats.get('total_violations', 0)),
            'total_violations_primary': int(excel_stats.get('total_violations_primary', excel_stats.get('total_violations', 0))),
            'total_violations_raw': int(excel_stats.get('total_violations_raw', 0)),
            'total_violations_meta': int(excel_stats.get('total_violations_meta', 0)),
        }

        # CSV 저장
        if excel_results:
            df = pd.DataFrame(excel_results)
            if suffixed_output_csv:
                df.to_csv(suffixed_output_csv, index=False, encoding='utf-8-sig')
            else:
                output_csv = (
                    os.path.join(result_folder, "excel_evaluation_results.csv")
                    if result_folder
                    else "excel_evaluation_results.csv"
                )
                if os.path.exists(output_csv):
                    os.remove(output_csv)
                df.to_csv(output_csv, index=False, encoding='utf-8-sig')

        # 🆕 엑셀 방식에서 생성된 상세 CSV 파일들 이름 변경
        if excel_results:
            date_keys = list(set([r.get('date', '20250101') for r in excel_results if r.get('date')]))
            #  파일들이 PPO 폴더에 생성되므로 작업 디렉토리 복원 후 처리
            os.chdir(original_cwd)
            rename_excel_detailed_csv_files(date_keys, result_folder, plan_label)
            # 다시 결과 폴더로 이동 (finally에서 복원됨)
            if result_folder:
                os.chdir(result_folder)

        episode_data = []
        env = None

        if plan_label:
            print(f"   엑셀 순번({plan_label}) 완료: Makespan = {statistics.get('makespan_hours', 0):.2f}시간")
        else:
            print(f"   엑셀 순번 완료: Makespan = {statistics.get('makespan_hours', 0):.2f}시간")
        return excel_results, statistics, episode_data, env

    except Exception as e:
        if plan_label:
            print(f"   ❌ 엑셀 순번({plan_label}) 실행 실패: {e}")
        else:
            print(f"   ❌ 엑셀 순번 실행 실패: {e}")
        return [], {'makespan_hours': 0, 'total_violations': 0}, [], None


def run_actionmasking_heuristic(excel_path: str, result_folder: str = None) -> Tuple[List[Dict], Dict, List[Dict], object]:
    """
    착수일기준휴리스틱 실행 (MODE 2에서만 사용)
    integrated_learning_and_scheduling.py와 동일한 방식
    """
    print("착수일기준휴리스틱 실행 중...")

    if result_folder:
        output_csv = os.path.join(result_folder, "actionmasking_evaluation_results.csv")
    else:
        output_csv = "actionmasking_evaluation_results.csv"

    if os.path.exists(output_csv):
        os.remove(output_csv)

    try:
        original_cwd = os.getcwd()
        if result_folder:
            os.chdir(result_folder)

        try:
            am_results, bay_analysis_by_date, step_info_by_date, am_stats = create_actionmasking_schedule(excel_path)
            from utils.csv_save import save_detailed_masking_info, save_detailed_bay_selection_info

            print(f"  상세 분석 CSV 저장 중...")
            save_detailed_bay_selection_info(bay_analysis_by_date)
            for date_key, (daily_step_info, blocks_in_date) in step_info_by_date.items():
                if daily_step_info:
                    save_detailed_masking_info(date_key, daily_step_info, blocks_in_date)
                    print(f"    Action Masking 스텝 분석: detailed_actionmasking_schedule_info_{date_key}.csv")
        finally:
            os.chdir(original_cwd)

        statistics = {
            'makespan_hours': float(am_stats.get('makespan_hours', 0.0)),
            'total_violations': int(am_stats.get('total_violations', 0)),
            'total_violations_primary': int(am_stats.get('total_violations_primary', am_stats.get('total_violations', 0))),
            'total_violations_raw': int(am_stats.get('total_violations_raw', 0)),
            'total_violations_meta': int(am_stats.get('total_violations_meta', 0)),
            'total_cseam_violations': int(am_stats.get('total_cseam_violations', 0)),
        }

        if am_results:
            df = pd.DataFrame(am_results)
            df.to_csv(output_csv, index=False, encoding='utf-8-sig')

        if am_results:
            date_keys = list(set([r.get('date', '20250101') for r in am_results if r.get('date')]))
            rename_actionmasking_detailed_csv_files(date_keys, result_folder)

        episode_data = []
        env = None
        print(f"   착수일기준휴리스틱 완료: Makespan = {statistics.get('makespan_hours', 0):.2f}시간")
        return am_results, statistics, episode_data, env

    except Exception as e:
        print(f"   ❌ 착수일기준휴리스틱 실행 실패: {e}")
        return [], {'makespan_hours': 0, 'total_violations': 0}, [], None


def _get_common_settings(settings: Optional[Dict[str, object]]) -> Tuple[int, int, bool]:
    max_days = int(_get_setting(settings, "assembly_max_days", 20))
    date_offset = int(_get_setting(settings, "assembly_date_offset", 0))
    save_detailed = bool(_get_setting(settings, "save_detailed_csv", True))
    return max_days, date_offset, save_detailed


def _as_bool(value: object, default: bool = False) -> bool:
    """[AGENT-ADD] Parse bool-like eval settings."""
    if value is None:
        return bool(default)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return bool(default)


def _primary_violation_count(stats: Optional[Dict]) -> int:
    """[AGENT-ADD] Evaluation best selection uses the primary training-style count."""
    stats = stats or {}
    for key in ("total_violations_primary_train", "total_violations_primary", "total_violations_train", "total_violations"):
        if key in stats:
            try:
                return int(stats.get(key) or 0)
            except Exception:
                return 0
    return 0


def _raw_violation_count(stats: Optional[Dict]) -> int:
    stats = stats or {}
    try:
        return int(stats.get("total_violations_raw", stats.get("total_violations", 0)) or 0)
    except Exception:
        return 0


def _stats_sort_key(stats: Optional[Dict]) -> Tuple[int, float, int]:
    """[AGENT-ADD] Same practical priority as self-label: violations first, then makespan."""
    stats = stats or {}
    try:
        makespan = float(stats.get("makespan_hours", float("inf")) or float("inf"))
    except Exception:
        makespan = float("inf")
    return (_primary_violation_count(stats), makespan, _raw_violation_count(stats))


def _profile_sampling_enabled(settings: Optional[Dict[str, object]]) -> bool:
    """[AGENT-ADD] Test-time option to evaluate the same profile family used by self-label."""
    # [AGENT-EDIT] Explicit CLI --profile_sampling must override config use_self_label_profiles.
    value = _get_setting(settings, "profile_sampling", None)
    if value is None:
        value = _get_setting(settings, "use_self_label_profiles", None)
    if value is None:
        value = _get_setting(settings, "eval_use_self_label_profiles", True)
    return _as_bool(value, True)


def _eval_sample_count(settings: Optional[Dict[str, object]], default: int = 50) -> int:
    value = _get_setting(settings, "sampling", None)
    if value is None:
        value = _get_setting(settings, "num_samples", default)
    try:
        return max(1, int(value))
    except Exception:
        return int(default)


def _heuristic_decode_mode(settings: Optional[Dict[str, object]]) -> int:
    """[AGENT-ADD] decode_mode 1=기존(다양한 프로파일 조합), 2=all_on+all_mask_off 두 가지만."""
    value = _get_setting(settings, "heuristic_decode_mode", 1)
    try:
        v = int(value)
        return v if v in (1, 2) else 1
    except Exception:
        return 1


def _heuristic_sample_count(settings: Optional[Dict[str, object]]) -> int:
    """[AGENT-ADD] Deterministic heuristics should normally run once."""
    if _heuristic_decode_mode(settings) == 2:
        # [AGENT-ADD] decode_mode 2: all_on 1회만. all_mask_off는 자동 추가.
        return 1
    value = _get_setting(settings, "heuristic_sampling", None)
    if value is None:
        value = _get_setting(settings, "heuristic_profile_samples", 1)
    if isinstance(value, str) and value.strip().lower() in {"profiles", "profile", "all", "each"}:
        return -1
    try:
        return max(1, int(value))
    except Exception:
        return 1


def _heuristic_profile_sampling_enabled(settings: Optional[Dict[str, object]]) -> bool:
    """[AGENT-ADD] Keep deterministic heuristic baselines on the fixed config by default."""
    if _heuristic_decode_mode(settings) == 2:
        # [AGENT-ADD] decode_mode 2: 프로파일 탐색 비활성화. all_on + all_mask_off만.
        return False
    value = _get_setting(settings, "heuristic_profile_sampling", None)
    if value is None:
        return False
    return _as_bool(value, False)


def _bias_profile_specs(expanded: bool = True) -> List[Dict]:
    """[AGENT-ADD] Self-label bias profiles used for test-time candidate analysis."""
    if not expanded:
        return [{"components": None, "label": "current", "display": "현재설정"}]
    profiles: List[Dict] = []
    for mask in range(8):
        components = [idx + 1 for idx in range(3) if mask & (1 << idx)]
        label = ",".join(str(x) for x in components) if components else "off"
        profiles.append({
            "components": components,
            "label": label,
            "display": label,
        })
    return profiles


def _hard_profile_specs(expanded: bool = True) -> List[Dict]:
    """[AGENT-ADD] Self-label hard profiles: all-on plus one non-capacity target off."""
    base_flags = {
        "enable_line_group_constraint": True,
        "enable_p5_11_fixed_line_mixing": True,
        "enable_p5_12_internal_external_mixing": True,
        "enable_p6_4_cross_seam_mixing": True,
        "enable_p5_15_holiday_shift": True,
        "enable_c_seam_spacing": True,
        "enable_routing_curved_spacing": True,
        "enable_routing_high_seam_spacing": True,
    }
    profiles = [{
        "label": "all_on",
        "display": "all-on",
        "off_ids": [],
        "off_flags": {},
        "base_flags": base_flags,
    }]
    if not expanded:
        return profiles
    profiles.extend([
        ("line_group_off", ["LINE_GROUP_CONSTRAINT"], {"enable_line_group_constraint": False}),
        ("mixing_off", ["P5#11", "P5#12"], {
            "enable_p5_11_fixed_line_mixing": False,
            "enable_p5_12_internal_external_mixing": False,
        }),
        ("p6_4_off", ["P6#4"], {"enable_p6_4_cross_seam_mixing": False}),
        ("p5_15_off", ["P5#15"], {"enable_p5_15_holiday_shift": False}),
        ("c_seam_off", ["ROUTING_C_SEAM_SPACING"], {"enable_c_seam_spacing": False}),
        ("curved_off", ["ROUTING_CURVED_SPACING"], {"enable_routing_curved_spacing": False}),
        ("high_seam_off", ["ROUTING_HIGH_SEAM_SPACING"], {"enable_routing_high_seam_spacing": False}),
    ])
    return [
        item if isinstance(item, dict) else {
            "label": item[0],
            "display": item[0],
            "off_ids": item[1],
            "off_flags": item[2],
            "base_flags": base_flags,
        }
        for item in profiles
    ]


def _build_profile_runtime_config(bias_profile: Optional[Dict], hard_profile: Optional[Dict]) -> Dict:
    """[AGENT-ADD] Build the same profile runtime override used by self-label candidate generation."""
    runtime_cfg = copy.deepcopy(get_runtime_config() or {})
    constraints = runtime_cfg.setdefault("constraints", {}) if isinstance(runtime_cfg, dict) else {}
    if not isinstance(constraints, dict):
        constraints = {}
        runtime_cfg["constraints"] = constraints

    components = (bias_profile or {}).get("components")
    if components is not None:
        constraints["bias_components"] = list(components)

    hard_profile = hard_profile or _hard_profile_specs(expanded=False)[0]
    target_ids: List[str] = []
    for flag_name, flag_value in (hard_profile.get("base_flags") or {}).items():
        constraints[flag_name] = bool(flag_value)
    for off_id in hard_profile.get("off_ids") or []:
        target_ids.append(str(off_id))
    for flag_name, flag_value in (hard_profile.get("off_flags") or {}).items():
        constraints[flag_name] = bool(flag_value)

    hard_target_ids = [
        "LINE_GROUP_CONSTRAINT", "P5#11", "P5#12", "P6#4", "P5#15",
        "ROUTING_C_SEAM_SPACING", "ROUTING_CURVED_SPACING", "ROUTING_HIGH_SEAM_SPACING",
    ]
    for enabled_key in ("enabled_constraints_assembly", "enabled_constraints_start_date"):
        enabled = list(constraints.get(enabled_key) or [])
        if enabled:
            seen = set(enabled)
            for cid in hard_target_ids:
                if cid not in seen:
                    enabled.append(cid)
                    seen.add(cid)
            off_set = set(target_ids)
            constraints[enabled_key] = [cid for cid in enabled if cid not in off_set]

    for list_key in ("hard_constraints_assembly", "hard_constraints_start_date", "strict_rules"):
        values = list(constraints.get(list_key) or [])
        if values and target_ids:
            off_set = set(target_ids)
            constraints[list_key] = [value for value in values if value not in off_set]

    return runtime_cfg


def _build_all_masking_disabled_runtime_config() -> Dict:
    """[AGENT-ADD] Build runtime config that disables execution-time masking only."""
    runtime_cfg = copy.deepcopy(get_runtime_config() or {})
    constraints = runtime_cfg.setdefault("constraints", {}) if isinstance(runtime_cfg, dict) else {}
    if not isinstance(constraints, dict):
        constraints = {}
        runtime_cfg["constraints"] = constraints

    # [AGENT-ADD] Turn off every ConstraintConfig enable_* switch for the scheduling pass.
    for field_name in getattr(ConstraintConfig, "__dataclass_fields__", {}):
        if isinstance(field_name, str) and field_name.startswith("enable_"):
            constraints[field_name] = False

    # [AGENT-ADD] The three bias components are candidate filters, so all-mask-off means [].
    constraints["bias_components"] = []
    for list_key in (
        "enabled_constraints_assembly",
        "enabled_constraints_start_date",
        "hard_constraints_assembly",
        "hard_constraints_start_date",
        "strict_rules",
        "relax_order",
        "relax_order_assembly",
        "relax_order_start_date",
    ):
        constraints[list_key] = []
    return runtime_cfg


def _run_with_runtime_config(runtime_cfg: Dict, callback):
    """[AGENT-ADD] Temporarily apply a full runtime config for one eval candidate."""
    original_runtime_cfg = copy.deepcopy(get_runtime_config() or {})
    try:
        set_runtime_config(runtime_cfg)
        return callback()
    finally:
        set_runtime_config(original_runtime_cfg)


def _all_profile_pairs(kind: str) -> List[Tuple[Optional[Dict], Optional[Dict]]]:
    """[AGENT-ADD] Return each self-label-style profile once."""
    if kind == "rl":
        return [(b, h) for b in _bias_profile_specs(True) for h in _hard_profile_specs(True)]
    # [AGENT-EDIT] When heuristic profile sampling is enabled, run one deterministic result per profile.
    return [(b, h) for b in _bias_profile_specs(True) for h in _hard_profile_specs(True)]


def _profile_pairs(settings: Optional[Dict[str, object]], kind: str, sample_count: int) -> List[Tuple[Optional[Dict], Optional[Dict]]]:
    """[AGENT-EDIT] RL uses sample_count per profile; non-profile mode uses total sample_count."""
    if not _profile_sampling_enabled(settings):
        return [(None, None) for _ in range(sample_count)]
    pairs = _all_profile_pairs(kind)
    if not pairs:
        return [(None, None) for _ in range(sample_count)]
    random.shuffle(pairs)
    if kind == "rl":
        # [AGENT-EDIT] RL final test: 8 bias x 8 hard profiles, each sampled K times.
        return [pair for pair in pairs for _ in range(sample_count)]
    return [pairs[idx % len(pairs)] for idx in range(sample_count)]


def _heuristic_profile_pairs(settings: Optional[Dict[str, object]]) -> List[Tuple[Optional[Dict], Optional[Dict]]]:
    """[AGENT-ADD] Deterministic heuristics run once per profile when profile sampling is enabled."""
    if not _heuristic_profile_sampling_enabled(settings):
        return [(None, None)]
    pairs = _all_profile_pairs("heuristic")
    if not pairs:
        return [(None, None)]
    random.shuffle(pairs)
    sample_count = _heuristic_sample_count(settings)
    if sample_count < 0:
        return pairs
    return [pairs[idx % len(pairs)] for idx in range(sample_count)]


def _run_with_profile(bias_profile: Optional[Dict], hard_profile: Optional[Dict], callback):
    """[AGENT-ADD] Temporarily apply one eval profile."""
    if bias_profile is None and hard_profile is None:
        return callback()
    original_runtime_cfg = copy.deepcopy(get_runtime_config() or {})
    try:
        set_runtime_config(_build_profile_runtime_config(bias_profile, hard_profile))
        return callback()
    finally:
        set_runtime_config(original_runtime_cfg)


def _profile_label(bias_profile: Optional[Dict], hard_profile: Optional[Dict]) -> Tuple[str, str]:
    return (
        str((bias_profile or {}).get("label", "current")),
        str((hard_profile or {}).get("label", "current")),
    )


def _run_profiled_assembly_heuristic(
    blocks: List,
    metadata: Dict,
    start_date: str,
    result_folder: Optional[str],
    settings: Optional[Dict[str, object]],
    *,
    method: str,
    output_name: str,
    label: str,
) -> Tuple[List[Dict], Dict, List[Dict], object]:
    """[AGENT-ADD] Evaluate SPT/LPT/SEAM over N self-label-style profile candidates."""
    profile_enabled = _heuristic_profile_sampling_enabled(settings)
    pairs = _heuristic_profile_pairs(settings)
    sample_count = len(pairs)
    print(f" {label} 후보 평가 중... (samples={sample_count}, profile_sampling={profile_enabled})")

    output_csv = os.path.join(result_folder, output_name) if result_folder else output_name
    if os.path.exists(output_csv):
        os.remove(output_csv)

    max_days, date_offset, save_detailed = _get_common_settings(settings)
    sample_records: List[Dict[str, object]] = []
    best_stats: Optional[Dict] = None
    best_results: Optional[List[Dict]] = None
    best_profile: Tuple[Optional[Dict], Optional[Dict]] = (None, None)

    for idx, (bias_profile, hard_profile) in enumerate(pairs, 1):
        results, statistics = _run_with_profile(
            bias_profile,
            hard_profile,
            lambda: run_assembly_decoding_sequence_with_blocks(
                blocks=copy.deepcopy(blocks),
                metadata=copy.deepcopy(metadata),
                decoding_type="assembly",
                selection_method=method,
                max_days=max_days,
                start_date=start_date,
                date_offset=date_offset,
                output_csv=output_csv,
                save_csv=False,
                save_detailed=False,
                forced_sequence=None,
            ),
        )
        bias_label, hard_label = _profile_label(bias_profile, hard_profile)
        makespan = float((statistics or {}).get("makespan_hours", float("inf")) or float("inf"))
        primary = _primary_violation_count(statistics)
        raw = _raw_violation_count(statistics)
        sample_records.append({
            "sample_id": idx,
            "method": method,
            "bias_profile": bias_label,
            "hard_profile": hard_label,
            "makespan_hours": makespan,
            "primary": primary,
            "raw": raw,
            "total_violations": int((statistics or {}).get("total_violations", primary) or 0),
        })
        if best_stats is None or _stats_sort_key(statistics) < _stats_sort_key(best_stats):
            best_stats = statistics
            best_results = results
            best_profile = (bias_profile, hard_profile)
            print(f"   [{label} 갱신] sample={idx} primary={primary}, makespan={makespan:.2f}h, raw={raw}, profile={bias_label}/{hard_label}")

    if result_folder and sample_records:
        summary_path = os.path.join(result_folder, f"{method}_samples_summary.csv")
        pd.DataFrame(sample_records).to_csv(summary_path, index=False, encoding="utf-8-sig")

    final_results, final_statistics = _run_with_profile(
        best_profile[0],
        best_profile[1],
        lambda: run_assembly_decoding_sequence_with_blocks(
            blocks=copy.deepcopy(blocks),
            metadata=copy.deepcopy(metadata),
            decoding_type="assembly",
            selection_method=method,
            max_days=max_days,
            start_date=start_date,
            date_offset=date_offset,
            output_csv=output_csv,
            save_csv=True,
            save_detailed=save_detailed,
            forced_sequence=None,
        ),
    )
    final_statistics = final_statistics or best_stats or {}
    all_mask_off_enabled = _as_bool(_get_setting(settings, "save_heuristic_all_mask_off", True), True)
    if all_mask_off_enabled:
        no_mask_output_name = f"{method}_all_mask_off_results.csv"
        no_mask_output_csv = os.path.join(result_folder, no_mask_output_name) if result_folder else no_mask_output_name
        if os.path.exists(no_mask_output_csv):
            os.remove(no_mask_output_csv)
        print(f" {label} 마스킹 전부 해제 결과 저장 중...")
        no_mask_results, no_mask_statistics = _run_with_runtime_config(
            _build_all_masking_disabled_runtime_config(),
            lambda: run_assembly_decoding_sequence_with_blocks(
                blocks=copy.deepcopy(blocks),
                metadata=copy.deepcopy(metadata),
                decoding_type="assembly",
                selection_method=method,
                max_days=max_days,
                start_date=start_date,
                date_offset=date_offset,
                output_csv=no_mask_output_csv,
                save_csv=True,
                save_detailed=False,
                forced_sequence=None,
            ),
        )
        no_mask_statistics = no_mask_statistics or {}
        no_mask_primary = _primary_violation_count(no_mask_statistics)
        no_mask_raw = _raw_violation_count(no_mask_statistics)
        no_mask_makespan = float(no_mask_statistics.get("makespan_hours", float("inf")) or float("inf"))
        final_statistics["all_mask_off_makespan_hours"] = no_mask_makespan
        final_statistics["all_mask_off_total_violations"] = int(no_mask_statistics.get("total_violations", no_mask_primary) or 0)
        final_statistics["all_mask_off_primary"] = int(no_mask_primary)
        final_statistics["all_mask_off_raw"] = int(no_mask_raw)
        final_statistics["all_mask_off_result_csv_name"] = no_mask_output_name
        final_statistics["all_mask_off_blocks"] = len(no_mask_results or [])
        sample_records.append({
            "sample_id": "all_mask_off",
            "method": method,
            "bias_profile": "all_mask_off",
            "hard_profile": "all_mask_off",
            "makespan_hours": no_mask_makespan,
            "primary": int(no_mask_primary),
            "raw": int(no_mask_raw),
            "total_violations": int(no_mask_statistics.get("total_violations", no_mask_primary) or 0),
            "result_csv_name": no_mask_output_name,
        })
        if result_folder:
            compare_path = os.path.join(result_folder, f"{method}_best_vs_all_mask_off_summary.csv")
            best_bias, best_hard = _profile_label(*best_profile)
            pd.DataFrame([
                {
                    "variant": "best",
                    "method": method,
                    "bias_profile": best_bias,
                    "hard_profile": best_hard,
                    "makespan_hours": float(final_statistics.get("makespan_hours", 0) or 0),
                    "primary": _primary_violation_count(final_statistics),
                    "raw": _raw_violation_count(final_statistics),
                    "total_violations": int(final_statistics.get("total_violations", _primary_violation_count(final_statistics)) or 0),
                    "result_csv_name": output_name,
                },
                {
                    "variant": "all_mask_off",
                    "method": method,
                    "bias_profile": "all_mask_off",
                    "hard_profile": "all_mask_off",
                    "makespan_hours": no_mask_makespan,
                    "primary": int(no_mask_primary),
                    "raw": int(no_mask_raw),
                    "total_violations": int(no_mask_statistics.get("total_violations", no_mask_primary) or 0),
                    "result_csv_name": no_mask_output_name,
                },
            ]).to_csv(compare_path, index=False, encoding="utf-8-sig")
            final_statistics["best_vs_all_mask_off_summary_csv_name"] = os.path.basename(compare_path)
        print(
            f" {label} 마스킹 해제 완료: Makespan = {no_mask_makespan:.2f}시간, "
            f"Primary = {no_mask_primary}, raw={no_mask_raw}"
        )

    if result_folder and sample_records:
        summary_path = os.path.join(result_folder, f"{method}_samples_summary.csv")
        pd.DataFrame(sample_records).to_csv(summary_path, index=False, encoding="utf-8-sig")

    final_statistics["profile_samples_evaluated"] = sample_count
    final_statistics["profile_sampling"] = profile_enabled
    final_statistics["best_bias_profile"], final_statistics["best_hard_profile"] = _profile_label(*best_profile)
    final_statistics["selected_result_csv_name"] = output_name
    final_statistics["selected_result_kind"] = "best"
    print(
        f" {label} 완료: Makespan = {float(final_statistics.get('makespan_hours', 0) or 0):.2f}시간, "
        f"Primary = {_primary_violation_count(final_statistics)}, samples={sample_count}"
    )
    return final_results or best_results or [], final_statistics, [], None


def run_spt_heuristic(blocks: List, metadata: Dict, start_date: str, result_folder: str = None,
                      settings: Optional[Dict[str, object]] = None) -> Tuple[List[Dict], Dict, List[Dict], object]:
    return _run_profiled_assembly_heuristic(
        blocks, metadata, start_date, result_folder, settings,
        method="spt",
        output_name="spt_evaluation_results.csv",
        label="SPT",
    )


def run_lpt_heuristic(blocks: List, metadata: Dict, start_date: str, result_folder: str = None,
                      settings: Optional[Dict[str, object]] = None) -> Tuple[List[Dict], Dict, List[Dict], object]:
    return _run_profiled_assembly_heuristic(
        blocks, metadata, start_date, result_folder, settings,
        method="lpt",
        output_name="lpt_evaluation_results.csv",
        label="LPT",
    )


def run_seam_min_heuristic(blocks: List, metadata: Dict, start_date: str, result_folder: str = None,
                           settings: Optional[Dict[str, object]] = None) -> Tuple[List[Dict], Dict, List[Dict], object]:
    return _run_profiled_assembly_heuristic(
        blocks, metadata, start_date, result_folder, settings,
        method="seam_min",
        output_name="seam_min_evaluation_results.csv",
        label="SEAM_MIN",
    )


# ==== [AGENT-ADD BEGIN: CA-CJH constructive insertion baseline] ====
def run_ca_cjh_insertion_baseline(blocks: List, metadata: Dict, start_date: str, result_folder: str = None,
                                  settings: Optional[Dict[str, object]] = None) -> Tuple[List[Dict], Dict, List[Dict], object]:
    """Run CA-CJH-Insertion through the same DES/final-audit path used by GA."""
    return run_ca_cjh_insertion(
        blocks=blocks,
        metadata=metadata,
        start_date=start_date,
        result_folder=result_folder,
        settings=settings,
    )
# ==== [AGENT-ADD END] ====


# ==== [AGENT-ADD BEGIN: lightweight GA evaluation baseline] ====
def _ga_block_id(block: object) -> int:
    return int(getattr(block, "block_id", block.get("block_id") if isinstance(block, dict) else 0))


def _ga_score_from_stats(statistics: Dict[str, object]) -> Tuple[float, float, float, float]:
    processed = float(statistics.get("total_blocks_processed", 0) or 0)
    expected = float(statistics.get("total_blocks_expected", processed) or processed)
    missing_penalty = max(0.0, expected - processed)
    primary = float(statistics.get("total_violations_primary", statistics.get("total_violations", 10**9)) or 0)
    makespan = float(statistics.get("makespan_hours", 10**9) or 10**9)
    raw = float(statistics.get("total_violations_raw", 0) or 0)
    return missing_penalty, primary, makespan, raw


def _ga_order_crossover(parent_a: List[int], parent_b: List[int], rng: random.Random) -> List[int]:
    if len(parent_a) < 2:
        return list(parent_a)
    left, right = sorted(rng.sample(range(len(parent_a)), 2))
    child: List[Optional[int]] = [None] * len(parent_a)
    child[left:right + 1] = parent_a[left:right + 1]
    fill_values = [value for value in parent_b if value not in child]
    fill_iter = iter(fill_values)
    for idx, value in enumerate(child):
        if value is None:
            child[idx] = next(fill_iter)
    return [int(value) for value in child if value is not None]


def _ga_mutate_swap(sequence: List[int], rng: random.Random, mutation_rate: float) -> List[int]:
    mutated = list(sequence)
    if len(mutated) >= 2 and rng.random() < mutation_rate:
        i, j = rng.sample(range(len(mutated)), 2)
        mutated[i], mutated[j] = mutated[j], mutated[i]
    return mutated


def _resolve_ga_parallel_plan(task_count: int, settings: Optional[Dict[str, object]]) -> Dict:
    """[AGENT-ADD] Dynamically size GA fitness-evaluation workers from CPU/RAM."""
    mode = str(_get_setting(settings, "ga_parallel", "auto")).strip().lower()
    if mode in {"0", "false", "off", "no"}:
        return {"enabled": False, "reason": "disabled"}
    if task_count < 2 and mode == "auto":
        return {"enabled": False, "reason": "small_task_count"}

    cpu_count = os.cpu_count() or 1
    requested_workers = int(_get_setting(settings, "ga_parallel_workers", 0) or 0)
    worker_threads = int(_get_setting(settings, "ga_worker_threads", 1) or 1)
    reserve_cores = max(2, cpu_count // 16)
    cpu_limit = max(1, cpu_count - reserve_cores)
    ram_gb = _get_total_memory_gb()
    per_worker_ram = float(_get_setting(settings, "ga_ram_gb_per_worker", 0.75) or 0.75)
    ram_limit = int(ram_gb / max(0.1, per_worker_ram)) if ram_gb else cpu_limit

    workers = requested_workers if requested_workers > 0 else min(task_count, cpu_limit, max(1, ram_limit))
    workers = max(1, min(int(workers), int(task_count)))
    if workers <= 1 and mode == "auto":
        return {"enabled": False, "reason": "single_worker"}
    return {
        "enabled": True,
        "workers": workers,
        "worker_threads": worker_threads,
        "cpu_count": cpu_count,
        "cpu_limit": cpu_limit,
        "ram_gb": ram_gb,
        "ram_limit": ram_limit,
        "per_worker_ram_gb": per_worker_ram,
        "task_count": task_count,
    }


def _init_ga_worker(
    blocks: List,
    metadata: Dict,
    start_date: str,
    base_runtime_cfg: Dict,
    max_days: int,
    date_offset: int,
    worker_threads: int,
) -> None:
    """[AGENT-ADD] Initialize one GA fitness worker."""
    global _GA_WORKER_BLOCKS, _GA_WORKER_METADATA, _GA_WORKER_START_DATE
    global _GA_WORKER_MAX_DAYS, _GA_WORKER_DATE_OFFSET
    try:
        torch.set_num_threads(max(1, int(worker_threads)))
    except Exception:
        pass
    _GA_WORKER_BLOCKS = blocks
    _GA_WORKER_METADATA = metadata
    _GA_WORKER_START_DATE = start_date
    _GA_WORKER_MAX_DAYS = int(max_days)
    _GA_WORKER_DATE_OFFSET = int(date_offset)
    set_runtime_config(copy.deepcopy(base_runtime_cfg or {}))


def _run_parallel_ga_task(task: Dict[str, object]) -> Dict[str, object]:
    """[AGENT-ADD] Evaluate one GA sequence in a worker process."""
    try:
        if _GA_WORKER_BLOCKS is None or _GA_WORKER_METADATA is None or _GA_WORKER_START_DATE is None:
            raise RuntimeError("GA worker is not initialized")
        sequence = [int(value) for value in task.get("sequence", [])]
        with _eval_worker_output_context():
            _, statistics = run_assembly_decoding_sequence_with_blocks(
                blocks=copy.deepcopy(_GA_WORKER_BLOCKS),
                metadata=copy.deepcopy(_GA_WORKER_METADATA),
                decoding_type="assembly",
                selection_method="priority",
                max_days=int(_GA_WORKER_MAX_DAYS),
                start_date=str(_GA_WORKER_START_DATE),
                date_offset=int(_GA_WORKER_DATE_OFFSET),
                output_csv=os.devnull,
                save_csv=False,
                save_detailed=False,
                forced_sequence=None,
                expand_rows=True,
                forced_prefix_block_ids=sequence,
                allow_forced_prefix_override=True,
            )
        statistics = statistics or {}
        return {
            "ok": True,
            "sequence": sequence,
            "generation": int(task.get("generation", 0) or 0),
            "source": str(task.get("source", "")),
            "statistics": statistics,
            "score": _ga_score_from_stats(statistics),
        }
    except Exception:
        return {
            "ok": False,
            "sequence": [int(value) for value in task.get("sequence", [])],
            "generation": int(task.get("generation", 0) or 0),
            "source": str(task.get("source", "")),
            "error": traceback.format_exc(),
        }


def run_ga_metaheuristic(blocks: List, metadata: Dict, start_date: str, result_folder: str = None,
                         settings: Optional[Dict[str, object]] = None) -> Tuple[List[Dict], Dict, List[Dict], object]:
    """Lightweight GA baseline.

    # [AGENT-ADD] GA chromosome generation itself does not use action masking.
    # Evaluation still runs through the normal DES path with forced-prefix override,
    # so canonical final-audit violation counting stays identical to other methods.
    """
    start_t = time.perf_counter()
    population_size = max(2, int(_get_setting(settings, "ga_population", 8)))
    generations = max(1, int(_get_setting(settings, "ga_generations", 4)))
    elite_count = max(1, min(population_size, int(_get_setting(settings, "ga_elite", 2))))
    mutation_rate = float(_get_setting(settings, "ga_mutation_rate", 0.25))
    seed = int(_get_setting(settings, "ga_seed", 42))
    rng = random.Random(seed)

    output_csv = os.path.join(result_folder, "ga_evaluation_results.csv") if result_folder else "ga_evaluation_results.csv"
    if os.path.exists(output_csv):
        os.remove(output_csv)

    max_days, date_offset, save_detailed = _get_common_settings(settings)
    block_ids = [_ga_block_id(block) for block in blocks]
    print(
        f" GA baseline 실행 중... "
        f"(population={population_size}, generations={generations}, mutation={mutation_rate:.2f}, seed={seed})"
    )

    def _normalize_sequence(sequence: List[int]) -> List[int]:
        seen = set()
        normalized = []
        for block_id in sequence:
            if block_id in block_ids and block_id not in seen:
                normalized.append(block_id)
                seen.add(block_id)
        for block_id in block_ids:
            if block_id not in seen:
                normalized.append(block_id)
        return normalized

    def _evaluate_sequence(sequence: List[int], *, save_final: bool = False) -> Tuple[List[Dict], Dict]:
        return run_assembly_decoding_sequence_with_blocks(
            blocks=copy.deepcopy(blocks),
            metadata=copy.deepcopy(metadata),
            decoding_type="assembly",
            selection_method="priority",
            max_days=max_days,
            start_date=start_date,
            date_offset=date_offset,
            output_csv=output_csv,
            save_csv=save_final,
            save_detailed=save_detailed if save_final else False,
            forced_sequence=None,
            expand_rows=True,
            forced_prefix_block_ids=_normalize_sequence(sequence),
            allow_forced_prefix_override=True,
        )

    population: List[List[int]] = []

    def _add_candidate(sequence: List[int]) -> None:
        normalized = _normalize_sequence(sequence)
        key = tuple(normalized)
        if key not in {tuple(candidate) for candidate in population}:
            population.append(normalized)

    # [AGENT-ADD] Seed only with neutral/order-based candidates; no domain masking profile is used here.
    _add_candidate(block_ids)
    date_seed = [_ga_block_id(block) for block in sorted(blocks, key=lambda b: str(getattr(b, "assembly_start_date", "")))]
    _add_candidate(date_seed)
    while len(population) < population_size:
        shuffled = list(block_ids)
        rng.shuffle(shuffled)
        _add_candidate(shuffled)
    population = population[:population_size]

    evaluated: Dict[Tuple[int, ...], Dict[str, object]] = {}
    best_key: Optional[Tuple[int, ...]] = None
    best_score: Optional[Tuple[float, float, float, float]] = None
    summary_rows: List[Dict[str, object]] = []
    progress_interval = max(1, int(_get_setting(settings, "ga_progress_interval", 10) or 10))
    parallel_plan = _resolve_ga_parallel_plan(population_size, settings)
    executor: Optional[ProcessPoolExecutor] = None
    if parallel_plan.get("enabled"):
        ram_text = f"{parallel_plan.get('ram_gb'):.1f}GiB" if parallel_plan.get("ram_gb") is not None else "unknown"
        print(
            "[GA 병렬] "
            f"workers={parallel_plan['workers']}, worker_threads={parallel_plan['worker_threads']}, "
            f"cpu={parallel_plan['cpu_count']}, cpu_limit={parallel_plan['cpu_limit']}, "
            f"ram={ram_text}, ram_limit={parallel_plan['ram_limit']}, "
            f"per_worker_ram={parallel_plan['per_worker_ram_gb']:.2f}GiB"
        )
        start_method = str(_get_setting(settings, "ga_start_method", "fork")).strip().lower()
        try:
            ctx = mp.get_context(start_method)
        except Exception:
            ctx = mp.get_context()
        executor = ProcessPoolExecutor(
            max_workers=int(parallel_plan["workers"]),
            mp_context=ctx,
            initializer=_init_ga_worker,
            initargs=(
                copy.deepcopy(blocks),
                copy.deepcopy(metadata),
                start_date,
                copy.deepcopy(get_runtime_config() or {}),
                max_days,
                date_offset,
                int(parallel_plan["worker_threads"]),
            ),
        )
    elif population_size >= 2:
        print(f"[GA 병렬] disabled: {parallel_plan.get('reason')}")

    def _store_record(
        sequence: List[int],
        generation: int,
        source: str,
        statistics: Optional[Dict[str, object]],
        error: Optional[str] = None,
    ) -> Dict[str, object]:
        nonlocal best_key, best_score
        normalized = _normalize_sequence(sequence)
        key = tuple(normalized)
        if key not in evaluated:
            if error:
                statistics = {
                    "makespan_hours": float("inf"),
                    "total_violations": 10**9,
                    "total_violations_primary": 10**9,
                    "ga_error": str(error),
                }
            statistics = statistics or {}
            score = _ga_score_from_stats(statistics)
            evaluated[key] = {
                "sequence": normalized,
                "statistics": statistics,
                "score": score,
                "first_generation": generation,
                "source": source,
            }
            summary_rows.append({
                "generation": generation,
                "source": source,
                "primary_violations": statistics.get("total_violations_primary", statistics.get("total_violations", 0)),
                "raw_violations": statistics.get("total_violations_raw", 0),
                "makespan_hours": statistics.get("makespan_hours", 0),
                "error": statistics.get("ga_error", ""),
                "sequence_head": " ".join(str(v) for v in normalized[:12]),
            })

        record = evaluated[key]
        score = record["score"]
        if best_score is None or score < best_score:
            best_score = score
            best_key = key
            stats = record["statistics"]
            print(
                "[GA 갱신] "
                f"gen={generation} source={source} "
                f"primary={stats.get('total_violations_primary', stats.get('total_violations', 0))} "
                f"raw={stats.get('total_violations_raw', 0)} "
                f"makespan={float(stats.get('makespan_hours', 0) or 0):.2f}h "
                f"seq_head={normalized[:8]}"
            )
        return record

    def _record_batch(candidates: List[List[int]], generation: int, source: str) -> None:
        new_tasks: List[Dict[str, object]] = []
        for candidate in candidates:
            normalized = _normalize_sequence(candidate)
            key = tuple(normalized)
            if key in evaluated:
                continue
            new_tasks.append({
                "sequence": normalized,
                "generation": generation,
                "source": source,
            })
        if not new_tasks:
            return
        if executor is not None:
            future_to_task = {
                executor.submit(_run_parallel_ga_task, task): task
                for task in new_tasks
            }
            completed = 0
            for future in as_completed(future_to_task):
                completed += 1
                result = future.result()
                _store_record(
                    result.get("sequence") or [],
                    int(result.get("generation", generation) or generation),
                    str(result.get("source", source)),
                    result.get("statistics") if result.get("ok") else None,
                    error=result.get("error") if not result.get("ok") else None,
                )
                if completed % progress_interval == 0 or completed == len(new_tasks):
                    print(f"   [GA 병렬] gen={generation} progress {completed}/{len(new_tasks)}")
        else:
            for idx, task in enumerate(new_tasks, 1):
                normalized = task["sequence"]
                try:
                    _, statistics = _evaluate_sequence(normalized, save_final=False)
                    _store_record(normalized, generation, source, statistics)
                except Exception as exc:
                    _store_record(normalized, generation, source, None, error=str(exc))
                if idx % progress_interval == 0 or idx == len(new_tasks):
                    print(f"   [GA 순차] gen={generation} progress {idx}/{len(new_tasks)}")

    try:
        _record_batch(population, generation=0, source="initial")

        for generation in range(1, generations + 1):
            ranked = sorted((record for record in evaluated.values()), key=lambda record: record["score"])
            elites = [list(record["sequence"]) for record in ranked[:elite_count]]
            next_population = list(elites)
            while len(next_population) < population_size:
                parent_pool = elites if len(elites) >= 2 else population
                parent_a, parent_b = rng.sample(parent_pool, 2)
                child = _ga_order_crossover(parent_a, parent_b, rng)
                child = _ga_mutate_swap(child, rng, mutation_rate)
                next_population.append(_normalize_sequence(child))
            population = next_population[:population_size]
            _record_batch(population, generation=generation, source="elite/mutation")
    finally:
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=False)

    if best_key is None:
        return [], {
            "makespan_hours": 0,
            "total_violations": 0,
            "ga_candidates_evaluated": 0,
            "computation_seconds": time.perf_counter() - start_t,
            "selected_result_csv_name": "ga_evaluation_results.csv",
        }, [], None

    best_sequence = list(best_key)
    final_results, final_statistics = _evaluate_sequence(best_sequence, save_final=True)
    elapsed_seconds = time.perf_counter() - start_t
    final_statistics["ga_candidates_evaluated"] = len(evaluated)
    final_statistics["ga_population"] = population_size
    final_statistics["ga_generations"] = generations
    final_statistics["ga_mutation_rate"] = mutation_rate
    final_statistics["ga_seed"] = seed
    final_statistics["ga_best_sequence_head"] = best_sequence[:12]
    final_statistics["computation_seconds"] = elapsed_seconds
    final_statistics["selected_result_csv_name"] = "ga_evaluation_results.csv"
    final_statistics["ga_parallel_enabled"] = bool(parallel_plan.get("enabled"))
    final_statistics["ga_parallel_workers"] = int(parallel_plan.get("workers", 1) or 1)
    final_statistics["ga_worker_threads"] = int(parallel_plan.get("worker_threads", 1) or 1)

    if result_folder and summary_rows:
        summary_path = os.path.join(result_folder, "ga_population_summary.csv")
        try:
            pd.DataFrame(summary_rows).to_csv(summary_path, index=False, encoding="utf-8-sig")
        except Exception as exc:
            print(f"   ⚠️ GA 후보 요약 저장 실패: {exc}")

    print(
        f"   GA 완료: Makespan = {float(final_statistics.get('makespan_hours', 0) or 0):.2f}시간, "
        f"Primary = {final_statistics.get('total_violations_primary', final_statistics.get('total_violations', 0))}, "
        f"후보평가 = {len(evaluated)}개, 계산시간 = {elapsed_seconds:.2f}s"
    )
    return final_results, final_statistics, [], None
# ==== [AGENT-ADD END] ====


# ==== [AGENT-ADD BEGIN: OR-Tools CP-SAT comparison baseline] ====
def _cp_sat_worker_count(settings: Optional[Dict[str, object]]) -> int:
    """[AGENT-ADD] Dynamically size CP-SAT search workers from the host CPU/RAM."""
    requested = int(_get_setting(settings, "cp_workers", 0) or 0)
    if requested > 0:
        return max(1, requested)

    cpu_count = os.cpu_count() or 1
    # [AGENT-EDIT] Honor cp_full_cpu for exact CP-SAT runs as well as fast mode.
    full_cpu = _as_bool(_get_setting(settings, "cp_full_cpu", False), False)
    reserve_cores = 0 if full_cpu else max(1, cpu_count // 16)
    ram_gb = _get_total_memory_gb()
    ram_limit = cpu_count
    if ram_gb:
        per_worker_ram = float(_get_setting(settings, "cp_ram_gb_per_worker", 0.50) or 0.50)
        ram_limit = max(1, int(ram_gb / max(0.1, per_worker_ram)))
    return max(1, min(cpu_count - reserve_cores, ram_limit))


def _cp_sat_processing_times(block: object, scale: int) -> List[int]:
    """[AGENT-ADD] Convert 8 process times in minutes to CP integer units."""
    raw_times = list(getattr(block, "processing_times", []) or [])
    if len(raw_times) < 8:
        raw_times.extend([0.0] * (8 - len(raw_times)))
    converted: List[int] = []
    for value in raw_times[:8]:
        try:
            converted.append(max(0, int(round(float(value) * scale))))
        except (TypeError, ValueError):
            converted.append(0)
    return converted


def _cp_sat_is_ps_small_pair(block: object, blocks_dict: Dict[int, object]) -> bool:
    """[AGENT-ADD] Match the existing small P/S pair bay rule where possible."""
    checker = getattr(block, "is_ps_small_pair", None)
    if callable(checker):
        try:
            return bool(checker(blocks_dict))
        except TypeError:
            return bool(checker())
    pair_id = getattr(block, "pair_block_id", None)
    if not pair_id:
        return False
    pair = blocks_dict.get(int(pair_id))
    try:
        current_longi = int(getattr(block, "longi_count", 99) or 99)
        pair_longi = int(getattr(pair, "longi_count", 99) or 99) if pair is not None else 99
        return current_longi < 7 and pair_longi < 7
    except (TypeError, ValueError):
        return False


def _cp_sat_normalize_sequence(block_ids: List[int], sequence: List[int]) -> List[int]:
    """[AGENT-ADD] Keep CP candidate sequences complete and duplicate-free."""
    seen = set()
    normalized: List[int] = []
    allowed = set(block_ids)
    for block_id in sequence:
        if block_id in allowed and block_id not in seen:
            normalized.append(int(block_id))
            seen.add(int(block_id))
    for block_id in block_ids:
        if block_id not in seen:
            normalized.append(int(block_id))
    return normalized


def _cp_add_and_bool(model, literals: List[object], name: str):
    """[AGENT-ADD] Reified AND helper for native CP-SAT violation variables."""
    z = model.NewBoolVar(name)
    if not literals:
        model.Add(z == 1)
        return z
    model.AddBoolAnd(literals).OnlyEnforceIf(z)
    model.AddBoolOr([lit.Not() for lit in literals] + [z])
    return z


def _cp_add_or_bool(model, literals: List[object], name: str):
    """[AGENT-ADD] Reified OR helper for native CP-SAT violation variables."""
    z = model.NewBoolVar(name)
    if not literals:
        model.Add(z == 0)
        return z
    model.AddBoolOr(literals).OnlyEnforceIf(z)
    model.AddBoolAnd([lit.Not() for lit in literals]).OnlyEnforceIf(z.Not())
    for lit in literals:
        model.AddImplication(lit, z)
    return z


def _cp_add_int_eq_bool(model, left, right, name: str):
    """[AGENT-ADD] Bool variable equivalent to left == right."""
    z = model.NewBoolVar(name)
    model.Add(left == right).OnlyEnforceIf(z)
    model.Add(left != right).OnlyEnforceIf(z.Not())
    return z


def _cp_add_linear_ge_bool(model, expr, threshold: int, name: str):
    """[AGENT-ADD] Bool variable equivalent to expr >= threshold."""
    z = model.NewBoolVar(name)
    model.Add(expr >= threshold).OnlyEnforceIf(z)
    model.Add(expr <= threshold - 1).OnlyEnforceIf(z.Not())
    return z


def _cp_add_linear_le_bool(model, expr, threshold: int, name: str):
    """[AGENT-ADD] Bool variable equivalent to expr <= threshold."""
    z = model.NewBoolVar(name)
    model.Add(expr <= threshold).OnlyEnforceIf(z)
    model.Add(expr >= threshold + 1).OnlyEnforceIf(z.Not())
    return z


def _cp_block_attr_value(block: object, attr: str, default: str = "") -> str:
    value = getattr(block, attr, default)
    if hasattr(value, "value"):
        value = value.value
    return str(value if value is not None else default)


def _cp_parse_start_datetime(start_date: str) -> datetime:
    text = str(start_date or "").strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10] if "-" in text or "/" in text else text[:8], fmt)
        except ValueError:
            continue
    return datetime(2025, 1, 1)


def _cp_repair_basic_manual_bays(
    manual_bays: Dict[int, str],
    blocks_dict: Dict[int, object],
    cfg: ConstraintConfig,
) -> Tuple[Dict[int, str], int]:
    """[AGENT-ADD] Apply obvious Bay-rule repair before final replay.

    This does not forbid CP-SAT from exploring violations. It only prevents a
    time-limited FEASIBLE solution from being replayed with basic Bay mistakes
    that the native objective already intended to avoid.
    """
    repaired = dict(manual_bays or {})
    repair_count = 0

    def _set(block_id: int, bay_name: str) -> None:
        nonlocal repair_count
        if repaired.get(int(block_id)) != bay_name:
            repaired[int(block_id)] = bay_name
            repair_count += 1

    if cfg.is_constraint_enabled("P7#2"):
        for block_id, block in blocks_dict.items():
            if float(getattr(block, "width", 0.0) or 0.0) > 21.0:
                _set(int(block_id), "36B")

    if cfg.is_constraint_enabled("P7#3") or cfg.is_constraint_enabled("P7#4"):
        seen_pairs = set()
        for block_id, block in blocks_dict.items():
            pair_id = getattr(block, "pair_block_id", None)
            if pair_id is None or int(pair_id) not in blocks_dict:
                continue
            pair_key = tuple(sorted((int(block_id), int(pair_id))))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            if not _cp_sat_is_ps_small_pair(block, blocks_dict):
                continue
            target_bay = "36B" if repaired.get(pair_key[0]) == "36B" or repaired.get(pair_key[1]) == "36B" else repaired.get(pair_key[0], "35A")
            _set(pair_key[0], target_bay)
            _set(pair_key[1], target_bay)

    return repaired, repair_count


def _cp_resolve_capacity_limit(cfg: ConstraintConfig, calendar_manager: Optional[CalendarManager], target_dt: datetime) -> int:
    """[AGENT-ADD] Mirror CapacityTracker.get_capacity_limits for CP-SAT daily buckets."""
    is_weekend = bool(calendar_manager.is_weekend(target_dt)) if calendar_manager else target_dt.weekday() >= 5
    is_hot = bool(calendar_manager.is_hot_season(target_dt)) if calendar_manager else target_dt.month in {6, 7, 8}
    try:
        is_holiday_eve = bool(calendar_manager.is_holiday_eve(target_dt)) if calendar_manager else False
    except Exception:
        is_holiday_eve = False

    base_limit = int(getattr(cfg, "weekend_seam_limit", 45) if is_weekend else getattr(cfg, "weekday_seam_limit", 75))
    if bool(getattr(cfg, "holiday_eve_seam_reduction_enabled", True)) and is_holiday_eve:
        base_limit = int(getattr(cfg, "holiday_eve_seam_limit", 35))
    if (
        is_hot
        and not (bool(getattr(cfg, "holiday_eve_seam_reduction_enabled", True)) and is_holiday_eve)
        and cfg.is_constraint_enabled("P5#16")
    ):
        if str(getattr(cfg, "hot_season_reduction_type", "absolute")) == "absolute":
            base_limit = max(1, int(base_limit - int(getattr(cfg, "hot_season_seam_reduction", 6))))
        else:
            pct = float(getattr(cfg, "hot_season_percentage_reduction", 15))
            base_limit = max(1, int(base_limit - int(base_limit * pct / 100.0)))

    override_map = getattr(cfg, "daily_seam_cap_overrides", {}) or {}
    scale_map = getattr(cfg, "daily_seam_cap_scales", {}) or {}
    key = target_dt.strftime("%Y%m%d")
    if key in override_map:
        try:
            return max(1, int(override_map[key]))
        except Exception:
            pass
    if key in scale_map:
        try:
            base_limit = max(1, int(base_limit * float(scale_map[key])))
        except Exception:
            pass
    return max(1, int(base_limit))


def _cp_add_native_pbs_violation_terms(
    *,
    model,
    block_ids: List[int],
    blocks_dict: Dict[int, object],
    position: Dict[int, object],
    bay_b: Dict[int, object],
    block_at_pos: List[object],
    common_start: Dict[Tuple[int, int], object],
    start_date: str,
    max_days: int,
    time_scale: int,
    horizon: int,
    cfg: ConstraintConfig,
) -> Tuple[List[object], List[object], Dict[str, int]]:
    """[AGENT-ADD] Native CP-SAT transfer of the main final-audit violation families.

    This moves representative ERROR/WARNING PBS rules into CP-SAT as soft
    violation variables instead of only checking them after replay.
    """
    n_blocks = len(block_ids)
    ordered_ids = list(block_ids)
    id_to_idx = {block_id: idx for idx, block_id in enumerate(ordered_ids)}
    primary_terms: List[object] = []
    raw_terms: List[object] = []
    counts: Dict[str, int] = {}

    def _add(term, cid: str, *, raw_only: bool = False) -> None:
        raw_terms.append(term)
        if not raw_only:
            primary_terms.append(term)
        counts[cid] = counts.get(cid, 0) + 1

    def _constant_array(values: List[int]):
        return [model.NewConstant(int(value)) for value in values]

    # Positional feature variables via inverse order.
    bay_at_pos = []
    type_codes: Dict[str, int] = {}
    group_codes: Dict[str, int] = {"": 0}
    features = {
        "assembly_type": [],
        "line_group": [],
        "cross_seam": [],
        "c_seam": [],
        "curved": [],
        "high_seam": [],
        "main_plate_only": [],
    }
    for block_id in ordered_ids:
        block = blocks_dict[block_id]
        assembly_type = _cp_block_attr_value(block, "assembly_type", "")
        if assembly_type not in type_codes:
            type_codes[assembly_type] = len(type_codes)
        features["assembly_type"].append(type_codes[assembly_type])

        line_group = str(getattr(block, "line_group", "") or "")
        if line_group not in group_codes:
            group_codes[line_group] = len(group_codes)
        features["line_group"].append(group_codes[line_group])
        features["cross_seam"].append(1 if bool(getattr(block, "is_cross_seam", False)) else 0)
        features["c_seam"].append(1 if int(getattr(block, "c_seam_count", 0) or 0) > 0 else 0)
        features["curved"].append(1 if bool(getattr(block, "has_curved_plate", False)) else 0)
        features["high_seam"].append(1 if bool(getattr(block, "is_high_seam_block", False)) else 0)
        features["main_plate_only"].append(1 if bool(getattr(block, "is_main_plate_only", False)) else 0)

    for pos_idx in range(n_blocks):
        bay_var = model.NewBoolVar(f"cp_native_bay_at_pos_{pos_idx}")
        model.AddElement(block_at_pos[pos_idx], [bay_b[block_id] for block_id in ordered_ids], bay_var)
        bay_at_pos.append(bay_var)

    def _feature_at_pos(name: str, pos_idx: int, ub: int = 1):
        target = model.NewIntVar(0, max(ub, 1), f"cp_native_{name}_at_{pos_idx}")
        model.AddElement(block_at_pos[pos_idx], _constant_array(features[name]), target)
        return target

    assembly_type_at_pos = [
        _feature_at_pos("assembly_type", pos_idx, max(type_codes.values()) if type_codes else 0)
        for pos_idx in range(n_blocks)
    ]
    line_group_at_pos = [
        _feature_at_pos("line_group", pos_idx, max(group_codes.values()) if group_codes else 0)
        for pos_idx in range(n_blocks)
    ]
    cross_at_pos = [_feature_at_pos("cross_seam", pos_idx, 1) for pos_idx in range(n_blocks)]
    cseam_at_pos = [_feature_at_pos("c_seam", pos_idx, 1) for pos_idx in range(n_blocks)]
    curved_at_pos = [_feature_at_pos("curved", pos_idx, 1) for pos_idx in range(n_blocks)]
    high_seam_at_pos = [_feature_at_pos("high_seam", pos_idx, 1) for pos_idx in range(n_blocks)]
    main_plate_at_pos = [_feature_at_pos("main_plate_only", pos_idx, 1) for pos_idx in range(n_blocks)]

    # P5#3,4: S block must not precede its P pair.
    if cfg.is_constraint_enabled("P5#3") or cfg.is_constraint_enabled("P5#4"):
        for block_id in ordered_ids:
            block = blocks_dict[block_id]
            if _cp_block_attr_value(block, "port_starboard", "") != "S":
                continue
            pair_id = getattr(block, "pair_block_id", None)
            if pair_id is None or int(pair_id) not in position:
                continue
            bad = model.NewBoolVar(f"cp_native_ps_order_bad_{block_id}")
            model.Add(position[block_id] <= position[int(pair_id)]).OnlyEnforceIf(bad)
            model.Add(position[block_id] > position[int(pair_id)]).OnlyEnforceIf(bad.Not())
            _add(bad, "P5#3,4")

    # P5#11,12: adjacent same assembly category is penalized, matching prefix validator.
    if cfg.is_constraint_enabled("P5#11") or cfg.is_constraint_enabled("P5#12"):
        for pos_idx in range(n_blocks - 1):
            bad = _cp_add_int_eq_bool(
                model,
                assembly_type_at_pos[pos_idx],
                assembly_type_at_pos[pos_idx + 1],
                f"cp_native_mixing_bad_{pos_idx}",
            )
            _add(bad, "P5#11,12")

    # LINE_GROUP_CONSTRAINT: more than configured same line-group streak.
    if cfg.is_constraint_enabled("LINE_GROUP_CONSTRAINT"):
        limit = (
            int(getattr(cfg, "line_group_soft_limit", 3))
            if bool(getattr(cfg, "line_group_use_soft_limit", False))
            else int(getattr(cfg, "line_group_strict_limit", 2))
        )
        window = max(2, limit + 1)
        line_code = type_codes.get("line")
        for start_idx in range(0, max(0, n_blocks - window + 1)):
            for group_value, group_code in group_codes.items():
                if not group_value or not str(group_value).startswith("L"):
                    continue
                lits = []
                for offset in range(window):
                    pos_idx = start_idx + offset
                    lits.append(_cp_add_int_eq_bool(model, line_group_at_pos[pos_idx], group_code, f"cp_native_lg_{start_idx}_{offset}_{group_code}"))
                    if line_code is not None:
                        lits.append(_cp_add_int_eq_bool(model, assembly_type_at_pos[pos_idx], line_code, f"cp_native_lg_line_{start_idx}_{offset}_{group_code}"))
                _add(_cp_add_and_bool(model, lits, f"cp_native_line_group_bad_{start_idx}_{group_code}"), "LINE_GROUP_CONSTRAINT")

    # P6#4 and routing spacing families.
    if cfg.is_constraint_enabled("P6#4"):
        for pos_idx in range(n_blocks - 2):
            _add(
                _cp_add_and_bool(
                    model,
                    [
                        _cp_add_int_eq_bool(model, cross_at_pos[pos_idx], 1, f"cp_native_cross_{pos_idx}_0"),
                        _cp_add_int_eq_bool(model, cross_at_pos[pos_idx + 1], 1, f"cp_native_cross_{pos_idx}_1"),
                        _cp_add_int_eq_bool(model, cross_at_pos[pos_idx + 2], 1, f"cp_native_cross_{pos_idx}_2"),
                    ],
                    f"cp_native_p64_bad_{pos_idx}",
                ),
                "P6#4",
            )
    spacing_specs = [
        ("ROUTING_C_SEAM_SPACING", cseam_at_pos, int(getattr(cfg, "c_seam_spacing_gap", 2) or 2)),
        ("ROUTING_CURVED_SPACING", curved_at_pos, 2),
        ("ROUTING_HIGH_SEAM_SPACING", high_seam_at_pos, 2),
    ]
    for cid, feature_vars, gap in spacing_specs:
        if not cfg.is_constraint_enabled(cid):
            continue
        for pos_idx in range(n_blocks):
            for next_idx in range(pos_idx + 1, min(n_blocks, pos_idx + max(1, gap) + 1)):
                _add(
                    _cp_add_and_bool(
                        model,
                        [
                            _cp_add_int_eq_bool(model, feature_vars[pos_idx], 1, f"cp_native_{cid}_{pos_idx}_{next_idx}_a"),
                            _cp_add_int_eq_bool(model, feature_vars[next_idx], 1, f"cp_native_{cid}_{pos_idx}_{next_idx}_b"),
                        ],
                        f"cp_native_{cid}_bad_{pos_idx}_{next_idx}",
                    ),
                    cid,
                )

    # P7 Bay assignment and consecutive-bay families.
    for block_id in ordered_ids:
        block = blocks_dict[block_id]
        if cfg.is_constraint_enabled("P7#2") and float(getattr(block, "width", 0.0) or 0.0) > 21.0:
            bay_a_bad = model.NewBoolVar(f"cp_native_p72_bad_{block_id}")
            model.Add(bay_a_bad + bay_b[block_id] == 1)
            _add(bay_a_bad, "P7#2")
        if cfg.is_constraint_enabled("P7#10") and int(getattr(block, "longi_count", 0) or 0) >= 30:
            _add(bay_b[block_id], "P7#10")
        if (
            cfg.is_constraint_enabled("P7#12")
            and _cp_block_attr_value(block, "material_type", "") == "LT"
        ):
            _add(bay_b[block_id], "P7#12")

    # [AGENT-ADD] P7#3,4: small P/S pairs should stay on the same Bay.
    if cfg.is_constraint_enabled("P7#3") or cfg.is_constraint_enabled("P7#4"):
        seen_pairs = set()
        for block_id in ordered_ids:
            block = blocks_dict[block_id]
            pair_id = getattr(block, "pair_block_id", None)
            if pair_id is None or int(pair_id) not in bay_b:
                continue
            pair_key = tuple(sorted((int(block_id), int(pair_id))))
            if pair_key in seen_pairs:
                continue
            seen_pairs.add(pair_key)
            if not _cp_sat_is_ps_small_pair(block, blocks_dict):
                continue
            bad = model.NewBoolVar(f"cp_native_p73_p74_bad_{pair_key[0]}_{pair_key[1]}")
            model.Add(bay_b[pair_key[0]] != bay_b[pair_key[1]]).OnlyEnforceIf(bad)
            model.Add(bay_b[pair_key[0]] == bay_b[pair_key[1]]).OnlyEnforceIf(bad.Not())
            _add(bad, "P7#3,4")

    if cfg.is_constraint_enabled("P7#7"):
        for pos_idx in range(n_blocks - 1):
            _add(
                _cp_add_and_bool(
                    model,
                    [bay_at_pos[pos_idx].Not(), bay_at_pos[pos_idx + 1].Not()],
                    f"cp_native_p77_a_bad_{pos_idx}",
                ),
                "P7#7",
            )
        for pos_idx in range(n_blocks - 2):
            _add(
                _cp_add_and_bool(
                    model,
                    [bay_at_pos[pos_idx], bay_at_pos[pos_idx + 1], bay_at_pos[pos_idx + 2]],
                    f"cp_native_p77_b_bad_{pos_idx}",
                ),
                "P7#7",
            )
    if cfg.is_constraint_enabled("CONSECUTIVE_B_BAY"):
        for pos_idx in range(n_blocks - 1):
            _add(_cp_add_and_bool(model, [bay_at_pos[pos_idx], bay_at_pos[pos_idx + 1]], f"cp_native_bb_bad_{pos_idx}"), "CONSECUTIVE_B_BAY")

    if cfg.is_constraint_enabled("P7#8"):
        for pos_idx in range(n_blocks - 2):
            main_lits = [
                _cp_add_int_eq_bool(model, main_plate_at_pos[pos_idx + offset], 1, f"cp_native_p78_main_{pos_idx}_{offset}")
                for offset in range(3)
            ]
            _add(_cp_add_and_bool(model, main_lits + [bay_at_pos[pos_idx].Not(), bay_at_pos[pos_idx + 1].Not(), bay_at_pos[pos_idx + 2].Not()], f"cp_native_p78_a_bad_{pos_idx}"), "P7#8")
            _add(_cp_add_and_bool(model, main_lits + [bay_at_pos[pos_idx], bay_at_pos[pos_idx + 1], bay_at_pos[pos_idx + 2]], f"cp_native_p78_b_bad_{pos_idx}"), "P7#8")

    # [AGENT-ADD] P7#1: Bay load balance. Prefix-level replay can be enabled
    # for small verification cases, but final-balance penalty stays the default
    # because full prefix reification makes larger exact runs stall before the
    # first solution.
    if cfg.is_constraint_enabled("P7#1"):
        work_values = {
            block_id: max(0, sum(_cp_sat_processing_times(blocks_dict[block_id], time_scale)))
            for block_id in ordered_ids
        }
        longi_values = {
            block_id: max(0, int(getattr(blocks_dict[block_id], "longi_count", 0) or 0))
            for block_id in ordered_ids
        }
        total_work_bound = max(1, sum(work_values.values()))
        total_longi_bound = max(1, sum(longi_values.values()))
        use_longi_balance = bool(getattr(cfg, "enable_longi_load_balance", False))
        prefix_positions = range(1, n_blocks) if bool(getattr(cfg, "cp_native_prefix_balance", False)) else [n_blocks - 1]
        for pos_idx in prefix_positions:
            prefix_a_work_terms = []
            prefix_b_work_terms = []
            prefix_a_longi_terms = []
            prefix_b_longi_terms = []
            for block_id in ordered_ids:
                if pos_idx >= n_blocks - 1:
                    in_b = bay_b[block_id]
                    in_a = bay_b[block_id].Not()
                else:
                    in_prefix = _cp_add_linear_le_bool(model, position[block_id], pos_idx, f"cp_native_p71_prefix_{pos_idx}_{block_id}")
                    in_b = _cp_add_and_bool(model, [in_prefix, bay_b[block_id]], f"cp_native_p71_prefix_b_{pos_idx}_{block_id}")
                    in_a = _cp_add_and_bool(model, [in_prefix, bay_b[block_id].Not()], f"cp_native_p71_prefix_a_{pos_idx}_{block_id}")
                prefix_a_work_terms.append(work_values[block_id] * in_a)
                prefix_b_work_terms.append(work_values[block_id] * in_b)
                prefix_a_longi_terms.append(longi_values[block_id] * in_a)
                prefix_b_longi_terms.append(longi_values[block_id] * in_b)

            a_work = sum(prefix_a_work_terms)
            b_work = sum(prefix_b_work_terms)
            work_diff = model.NewIntVar(0, total_work_bound, f"cp_native_p71_work_diff_{pos_idx}")
            model.AddAbsEquality(work_diff, a_work - b_work)
            work_bad = _cp_add_linear_ge_bool(
                model,
                10 * work_diff - 3 * (a_work + b_work),
                1,
                f"cp_native_p71_work_bad_{pos_idx}",
            )
            if use_longi_balance:
                a_longi = sum(prefix_a_longi_terms)
                b_longi = sum(prefix_b_longi_terms)
                longi_diff = model.NewIntVar(0, total_longi_bound, f"cp_native_p71_longi_diff_{pos_idx}")
                model.AddAbsEquality(longi_diff, a_longi - b_longi)
                longi_bad = _cp_add_linear_ge_bool(
                    model,
                    10 * longi_diff - 3 * (a_longi + b_longi),
                    1,
                    f"cp_native_p71_longi_bad_{pos_idx}",
                )
                _add(_cp_add_or_bool(model, [work_bad, longi_bad], f"cp_native_p71_bad_{pos_idx}"), "P7#1")
            else:
                _add(work_bad, "P7#1")

    # [AGENT-ADD] ROUTING_WORKSHOP_ORDER: within the same real workshop, keep
    # the original assembly-start order as a soft penalty instead of only audit.
    if cfg.is_constraint_enabled("ROUTING_WORKSHOP_ORDER"):
        sortable = []
        for block_id in ordered_ids:
            block = blocks_dict[block_id]
            workshop = _cp_block_attr_value(block, "assembly_workshop_code", "") or _cp_block_attr_value(block, "workshop_code", "")
            if not workshop:
                continue
            date_value = _cp_parse_start_datetime(getattr(block, "assembly_start_date", "")).toordinal()
            sortable.append((workshop, date_value, int(block_id)))
        for left_idx in range(len(sortable)):
            left_workshop, left_date, left_id = sortable[left_idx]
            for right_idx in range(left_idx + 1, len(sortable)):
                right_workshop, right_date, right_id = sortable[right_idx]
                if left_workshop != right_workshop or left_date == right_date:
                    continue
                earlier_id, later_id = (left_id, right_id) if left_date < right_date else (right_id, left_id)
                bad = model.NewBoolVar(f"cp_native_workshop_order_bad_{earlier_id}_{later_id}")
                model.Add(position[earlier_id] > position[later_id]).OnlyEnforceIf(bad)
                model.Add(position[earlier_id] <= position[later_id]).OnlyEnforceIf(bad.Not())
                _add(bad, "ROUTING_WORKSHOP_ORDER")

    # P5#8/9/10/15/16: daily seam capacity from CP start day buckets.
    try:
        calendar_manager = CalendarManager(calendar_overrides=getattr(cfg, "calendar_overrides", {}) or {})
    except Exception:
        calendar_manager = None
    start_dt = _cp_parse_start_datetime(start_date)
    day_units = max(1, 24 * 60 * int(time_scale))
    cp_days = max(1, min(max(1, int(max_days or 20)), int(horizon // day_units) + 2))
    day_vars: Dict[int, object] = {}
    on_day: Dict[Tuple[int, int], object] = {}
    for block_id in ordered_ids:
        day_var = model.NewIntVar(0, cp_days - 1, f"cp_native_day_{block_id}")
        model.AddDivisionEquality(day_var, common_start[(block_id, 0)], day_units)
        day_vars[block_id] = day_var
        for day_idx in range(cp_days):
            z = _cp_add_int_eq_bool(model, day_var, day_idx, f"cp_native_on_day_{block_id}_{day_idx}")
            on_day[(block_id, day_idx)] = z

    for day_idx in range(cp_days):
        day_dt = start_dt + timedelta(days=day_idx)
        limit = _cp_resolve_capacity_limit(cfg, calendar_manager, day_dt)
        day_seam_expr = sum(
            (
                int(getattr(blocks_dict[block_id], "seam_count", 0) or 0)
                + int(getattr(blocks_dict[block_id], "c_seam_count", 0) or 0)
            ) * on_day[(block_id, day_idx)]
            for block_id in ordered_ids
        )
        day_count_expr = sum(on_day[(block_id, day_idx)] for block_id in ordered_ids)
        is_weekend = bool(calendar_manager.is_weekend(day_dt)) if calendar_manager else day_dt.weekday() >= 5
        is_hot = bool(calendar_manager.is_hot_season(day_dt)) if calendar_manager else day_dt.month in {6, 7, 8}
        try:
            is_holiday_eve = bool(calendar_manager.is_holiday_eve(day_dt)) if calendar_manager else False
        except Exception:
            is_holiday_eve = False
        capacity_enabled = (
            (is_holiday_eve and cfg.is_constraint_enabled("P5#15"))
            or (is_hot and cfg.is_constraint_enabled("P5#16"))
            or (is_weekend and cfg.is_constraint_enabled("P5#10"))
            or ((not is_weekend) and cfg.is_constraint_enabled("P5#8"))
        )
        if capacity_enabled:
            over = _cp_add_linear_ge_bool(model, day_seam_expr, limit + 1, f"cp_native_capacity_bad_{day_idx}")
            cid = "P5#15" if is_holiday_eve else ("P5#16" if is_hot else ("P5#10" if is_weekend else "P5#8"))
            _add(over, cid)
        if (not is_weekend) and cfg.is_constraint_enabled("P5#9"):
            over72 = _cp_add_linear_ge_bool(model, day_seam_expr, 73, f"cp_native_p59_over72_{day_idx}")
            under17 = _cp_add_linear_le_bool(model, day_count_expr, 16, f"cp_native_p59_under17_{day_idx}")
            _add(_cp_add_and_bool(model, [over72, under17], f"cp_native_p59_bad_{day_idx}"), "P5#9")

    return primary_terms, raw_terms, counts


def _resolve_cp_parallel_plan(candidate_count: int, settings: Optional[Dict[str, object]], total_workers: int) -> Dict:
    """[AGENT-ADD] Size CP-SAT candidate/replay parallelism to occupy CPU cores."""
    mode = str(_get_setting(settings, "cp_parallel", "auto")).strip().lower()
    if mode in {"0", "false", "off", "no"}:
        return {"enabled": False, "reason": "disabled", "workers": 1, "candidate_threads": max(1, int(total_workers))}

    cpu_count = os.cpu_count() or 1
    requested_workers = int(_get_setting(settings, "cp_parallel_workers", 0) or 0)
    requested_threads = int(_get_setting(settings, "cp_candidate_threads", 0) or 0)
    ram_gb = _get_total_memory_gb()
    per_worker_ram = float(_get_setting(settings, "cp_ram_gb_per_worker", 0.75) or 0.75)
    ram_limit = int(ram_gb / max(0.1, per_worker_ram)) if ram_gb else cpu_count

    worker_limit = min(cpu_count, max(1, int(total_workers)), max(1, ram_limit), max(1, int(candidate_count)))
    workers = requested_workers if requested_workers > 0 else worker_limit
    workers = max(1, min(int(workers), int(candidate_count), cpu_count))
    candidate_threads = requested_threads if requested_threads > 0 else max(1, int(total_workers) // max(1, workers))

    if workers <= 1 and mode == "auto":
        return {
            "enabled": False,
            "reason": "single_worker",
            "workers": 1,
            "candidate_threads": max(1, int(total_workers)),
            "cpu_count": cpu_count,
            "ram_gb": ram_gb,
            "ram_limit": ram_limit,
        }
    return {
        "enabled": True,
        "workers": workers,
        "candidate_threads": max(1, int(candidate_threads)),
        "cpu_count": cpu_count,
        "ram_gb": ram_gb,
        "ram_limit": ram_limit,
        "per_worker_ram_gb": per_worker_ram,
    }


def _build_cp_variant_specs(candidate_limit: int) -> List[Tuple[str, str, int, str, int, int]]:
    """[AGENT-ADD] Create enough deterministic CP variants to keep CPU workers busy."""
    base_specs = [
        ("lpt_proxy", "total_time", 1, "branch_time", 1),
        ("spt_proxy", "total_time", -1, "date_rank", -1),
        ("seam_heavy_proxy", "seam", 1, "total_time", 1),
        ("seam_light_proxy", "seam", -1, "date_rank", -1),
        ("wide_first_proxy", "width", 1, "branch_time", 1),
        ("longi_balance_proxy", "longi", 1, "width", -1),
        ("date_hint_proxy", "date_rank", -1, "total_time", 1),
        ("branch_heavy_proxy", "branch_time", 1, "longi", 1),
        ("branch_light_proxy", "branch_time", -1, "date_rank", -1),
        ("wide_late_proxy", "width", -1, "seam", 1),
    ]
    specs: List[Tuple[str, str, int, str, int, int]] = []
    for idx in range(max(1, int(candidate_limit))):
        name, primary, primary_dir, secondary, secondary_dir = base_specs[idx % len(base_specs)]
        round_idx = idx // len(base_specs)
        specs.append((f"{name}_{idx + 1}", primary, primary_dir, secondary, secondary_dir, round_idx))
    return specs


def _cp_sat_get_feasibility_rank(
    blocks: List,
    metadata: Dict,
    start_date: str,
    max_days: int,
    date_offset: int,
) -> Dict[int, int]:
    """[AGENT-ADD] Run one LPT pass (all-on profile) to get block_id → natural scheduling position.

    This masking-informed rank is injected into the CP-SAT objective so that blocks
    the PBS scheduler naturally defers (due to capacity/bay/continuity constraints)
    are nudged toward later positions in the CP-SAT solution.
    """
    try:
        hard_all_on = _hard_profile_specs(expanded=False)[0]  # all_on profile
        scheduled_order: Dict[int, int] = {}

        def _lpt_run():
            return run_assembly_decoding_sequence_with_blocks(
                blocks=copy.deepcopy(blocks),
                metadata=copy.deepcopy(metadata),
                decoding_type="assembly",
                selection_method="LPT",
                max_days=max_days,
                start_date=start_date,
                date_offset=date_offset,
                output_csv=os.devnull,
                save_csv=False,
                save_detailed=False,
            )

        results, _ = _run_with_profile(None, hard_all_on, _lpt_run)
        for idx, row in enumerate(results or []):
            bid = int(row.get("block_id", 0) or 0)
            if bid and bid not in scheduled_order:
                scheduled_order[bid] = idx
        return scheduled_order
    except Exception:
        return {}


def _run_cp_sat_solver_candidate_task(task: Dict[str, object]) -> Dict[str, object]:
    """[AGENT-ADD] Solve one CP-SAT candidate in an independent process."""
    try:
        from ortools.sat.python import cp_model

        profiles = list(task["profiles"])
        block_ids = [int(profile["block_id"]) for profile in profiles]
        n_blocks = len(block_ids)
        variant_name = str(task["variant_name"])
        primary = str(task["primary"])
        primary_dir = int(task["primary_dir"])
        secondary = str(task["secondary"])
        secondary_dir = int(task["secondary_dir"])
        jitter_round = int(task["jitter_round"])
        seed = int(task["seed"])
        solver_workers = max(1, int(task["solver_workers"]))
        time_slice = float(task["time_slice"])
        bay_balance_weight = max(1, int(task["bay_balance_weight"]))
        # [AGENT-ADD] Masking-informed feasibility weight (nudges CP-SAT toward PBS-natural order)
        feasibility_weight = max(0, int(task.get("feasibility_weight", 0)))

        model = cp_model.CpModel()
        position = {block_id: model.NewIntVar(0, n_blocks - 1, f"pos_{block_id}") for block_id in block_ids}
        model.AddAllDifferent([position[block_id] for block_id in block_ids])
        bay_b = {block_id: model.NewBoolVar(f"bay36b_{block_id}") for block_id in block_ids}

        load_by_id = {int(profile["block_id"]): int(profile["branch_load"]) for profile in profiles}
        total_bay_load = max(1, sum(load_by_id.values()))
        bay_b_load = model.NewIntVar(0, total_bay_load, "bay_b_load")
        model.Add(bay_b_load == sum(load_by_id[block_id] * bay_b[block_id] for block_id in block_ids))
        bay_balance_abs = model.NewIntVar(0, total_bay_load, "bay_balance_abs")
        model.AddAbsEquality(bay_balance_abs, total_bay_load - 2 * bay_b_load)

        objective_terms = [bay_balance_weight * bay_balance_abs]
        for profile in profiles:
            block_id = int(profile["block_id"])
            if bool(profile.get("force_bay_b", False)):
                model.Add(bay_b[block_id] == 1)
            primary_coeff = int(round(float(profile.get(primary, 0.0)) * 100.0)) * primary_dir
            secondary_coeff = int(round(float(profile.get(secondary, 0.0)) * 20.0)) * secondary_dir
            jitter = ((block_id * 1103515245 + (seed + jitter_round * 9973) * 12345) % 2001) - 1000
            # [AGENT-ADD] Feasibility regularizer: prefer blocks the PBS scheduler defers to appear later
            feas_coeff = 0
            if feasibility_weight > 0:
                feas_rank = float(profile.get("feasibility_rank_normalized", 0.5))
                # [AGENT-EDIT] In a minimization objective, a larger positive
                # coefficient pulls a block earlier. Use inverse rank so blocks
                # that PBS masking schedules early are also hinted earlier.
                feas_coeff = int(round((1.0 - feas_rank) * feasibility_weight))
            objective_terms.append((primary_coeff + secondary_coeff + jitter + feas_coeff) * position[block_id])

        # [AGENT-ADD] Use feasibility rank as init hint (masking-natural order as warm start)
        hint_order = sorted(profiles, key=lambda p: (float(p.get("feasibility_rank_normalized", 0.5)), float(p.get("date_rank", 0.0))))
        for idx, profile in enumerate(hint_order):
            block_id = int(profile["block_id"])
            model.AddHint(position[block_id], idx)
            model.AddHint(bay_b[block_id], 1 if idx % 2 else 0)

        model.Minimize(sum(objective_terms))
        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = time_slice
        solver.parameters.num_search_workers = solver_workers
        solver.parameters.random_seed = seed + jitter_round
        status = solver.Solve(model)
        status_name = solver.StatusName(status)
        if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
            return {
                "ok": False,
                "variant_name": variant_name,
                "cp_status": status_name,
                "cp_wall_time": float(solver.WallTime()),
            }

        sequence = sorted(block_ids, key=lambda block_id: solver.Value(position[block_id]))
        manual_bays = {
            block_id: "36B" if solver.Value(bay_b[block_id]) else "35A"
            for block_id in block_ids
        }
        return {
            "ok": True,
            "variant_name": variant_name,
            "cp_status": status_name,
            "cp_wall_time": float(solver.WallTime()),
            "cp_objective_value": float(solver.ObjectiveValue()),
            "sequence": sequence,
            "manual_bays": manual_bays,
        }
    except Exception:
        return {
            "ok": False,
            "variant_name": str(task.get("variant_name", "")),
            "cp_status": "ERROR",
            "cp_wall_time": 0.0,
            "error": traceback.format_exc(),
        }


def _init_cp_replay_worker(
    blocks: List,
    metadata: Dict,
    start_date: str,
    base_runtime_cfg: Dict,
    max_days: int,
    date_offset: int,
    worker_threads: int,
) -> None:
    """[AGENT-ADD] Initialize one CP final-audit replay worker."""
    global _CP_REPLAY_WORKER_BLOCKS, _CP_REPLAY_WORKER_METADATA, _CP_REPLAY_WORKER_START_DATE
    global _CP_REPLAY_WORKER_MAX_DAYS, _CP_REPLAY_WORKER_DATE_OFFSET
    try:
        torch.set_num_threads(max(1, int(worker_threads)))
    except Exception:
        pass
    _CP_REPLAY_WORKER_BLOCKS = blocks
    _CP_REPLAY_WORKER_METADATA = metadata
    _CP_REPLAY_WORKER_START_DATE = start_date
    _CP_REPLAY_WORKER_MAX_DAYS = int(max_days)
    _CP_REPLAY_WORKER_DATE_OFFSET = int(date_offset)
    set_runtime_config(copy.deepcopy(base_runtime_cfg or {}))


def _run_parallel_cp_replay_task(task: Dict[str, object]) -> Dict[str, object]:
    """[AGENT-ADD] Replay one CP candidate through the canonical PBS audit path."""
    try:
        if _CP_REPLAY_WORKER_BLOCKS is None or _CP_REPLAY_WORKER_METADATA is None or _CP_REPLAY_WORKER_START_DATE is None:
            raise RuntimeError("CP replay worker is not initialized")
        sequence = [int(value) for value in task.get("sequence", [])]
        manual_bays = {int(k): str(v) for k, v in (task.get("manual_bays") or {}).items()}
        with _eval_worker_output_context():
            _, statistics = run_assembly_decoding_sequence_with_blocks(
                blocks=copy.deepcopy(_CP_REPLAY_WORKER_BLOCKS),
                metadata=copy.deepcopy(_CP_REPLAY_WORKER_METADATA),
                decoding_type="assembly",
                selection_method="priority",
                max_days=int(_CP_REPLAY_WORKER_MAX_DAYS),
                start_date=str(_CP_REPLAY_WORKER_START_DATE),
                date_offset=int(_CP_REPLAY_WORKER_DATE_OFFSET),
                output_csv=os.devnull,
                save_csv=False,
                save_detailed=False,
                forced_sequence=None,
                expand_rows=True,
                forced_prefix_block_ids=sequence,
                allow_forced_prefix_override=True,
                manual_bay_assignments=manual_bays,
            )
        return {
            "ok": True,
            "label": str(task.get("label", "")),
            "sequence": sequence,
            "manual_bays": manual_bays,
            "statistics": statistics or {},
        }
    except Exception:
        return {
            "ok": False,
            "label": str(task.get("label", "")),
            "sequence": [int(value) for value in task.get("sequence", [])],
            "manual_bays": {int(k): str(v) for k, v in (task.get("manual_bays") or {}).items()},
            "error": traceback.format_exc(),
        }


def _cp_sat_fast_candidate_baseline(
    *,
    cp_model,
    blocks: List,
    metadata: Dict,
    start_date: str,
    result_folder: Optional[str],
    settings: Optional[Dict[str, object]],
    output_csv: str,
    max_days: int,
    date_offset: int,
    save_detailed: bool,
    time_limit: float,
    time_scale: int,
    seed: int,
    workers: int,
    enforce_basic_bay_rules: bool,
) -> Tuple[List[Dict], Dict, List[Dict], object]:
    """[AGENT-ADD] Fast CP-SAT candidate generation + canonical violation-first replay selection."""
    start_t = time.perf_counter()
    block_ids = [_ga_block_id(block) for block in blocks]
    blocks_dict = {_ga_block_id(block): block for block in blocks}
    requested_candidates = int(_get_setting(settings, "cp_candidates", 0) or 0)
    full_cpu = _as_bool(_get_setting(settings, "cp_full_cpu", True), True)
    candidate_limit = requested_candidates if requested_candidates > 0 else max(6, int(workers))
    if full_cpu:
        candidate_limit = max(candidate_limit, int(workers))
    time_slice = float(_get_setting(settings, "cp_solver_time_slice_sec", 5.0) or 5.0)
    time_slice = max(0.1, min(float(time_limit), time_slice))
    bay_balance_weight = max(1, int(_get_setting(settings, "cp_bay_balance_weight", 10) or 10))
    # [AGENT-ADD] Masking-informed objective: run LPT to get PBS-natural block order
    cp_masking_hint = _as_bool(_get_setting(settings, "cp_masking_hint", True), True)
    feasibility_weight = max(0, int(_get_setting(settings, "cp_feasibility_weight", 30) or 30))

    durations = {block_id: _cp_sat_processing_times(blocks_dict[block_id], time_scale) for block_id in block_ids}
    date_hint = [
        _ga_block_id(block)
        for block in sorted(blocks, key=lambda b: str(getattr(b, "assembly_start_date", "")))
    ]
    date_rank = {block_id: idx for idx, block_id in enumerate(date_hint)}

    # [AGENT-ADD] Pre-compute masking feasibility rank via one LPT pass
    feasibility_rank: Dict[int, int] = {}
    if cp_masking_hint and feasibility_weight > 0:
        feasibility_rank = _cp_sat_get_feasibility_rank(blocks, metadata, start_date, max_days, date_offset)
        print(f"   [CP-SAT 마스킹 힌트] LPT feasibility rank 계산 완료 ({len(feasibility_rank)}개 블록)")

    profiles: List[Dict[str, object]] = []
    n_blocks = len(block_ids)
    for block_id in block_ids:
        block = blocks_dict[block_id]
        block_times = durations[block_id]
        width = float(getattr(block, "width", 0.0) or 0.0)
        profiles.append({
            "block_id": int(block_id),
            "branch_load": max(1, int(sum(block_times[5:8]))),
            "total_time": float(sum(getattr(block, "processing_times", []) or [])),
            "branch_time": float(sum((getattr(block, "processing_times", []) or [0] * 8)[5:8])),
            "seam": float(getattr(block, "seam_count", 0) or 0),
            "width": width,
            "longi": float(getattr(block, "longi_count", 0) or 0),
            "date_rank": float(date_rank.get(block_id, len(block_ids))),
            "force_bay_b": bool(enforce_basic_bay_rules and (width > 21.0 or _cp_sat_is_ps_small_pair(block, blocks_dict))),
            # [AGENT-ADD] Masking feasibility rank (normalized 0..1, 0=first scheduled by PBS)
            "feasibility_rank_normalized": float(feasibility_rank.get(block_id, n_blocks)) / max(1, n_blocks),
        })

    candidates: Dict[Tuple[Tuple[int, ...], Tuple[Tuple[int, str], ...]], Dict[str, object]] = {}
    solver_rows: List[Dict[str, object]] = []
    solver_wall_total = 0.0
    parallel_plan = _resolve_cp_parallel_plan(candidate_limit, settings, workers)
    solver_threads = int(parallel_plan.get("candidate_threads", workers) or 1)
    variant_specs = _build_cp_variant_specs(candidate_limit)
    candidate_tasks = [
        {
            "profiles": profiles,
            "variant_name": variant_name,
            "primary": primary,
            "primary_dir": primary_dir,
            "secondary": secondary,
            "secondary_dir": secondary_dir,
            "jitter_round": jitter_round,
            "seed": seed + variant_idx,
            "solver_workers": solver_threads,
            "time_slice": time_slice,
            "bay_balance_weight": bay_balance_weight,
            "feasibility_weight": feasibility_weight,  # [AGENT-ADD]
        }
        for variant_idx, (variant_name, primary, primary_dir, secondary, secondary_dir, jitter_round)
        in enumerate(variant_specs)
    ]

    def _store_solver_result(result: Dict[str, object]) -> None:
        nonlocal solver_wall_total
        solver_wall_total += float(result.get("cp_wall_time", 0.0) or 0.0)
        variant_name = str(result.get("variant_name", ""))
        if not result.get("ok"):
            solver_rows.append({
                "candidate_label": variant_name,
                "cp_status": result.get("cp_status", ""),
                "cp_wall_time": result.get("cp_wall_time", 0.0),
                "sequence_head": "",
                "bay_head": "",
                "selected_for_replay": False,
                "error": result.get("error", ""),
            })
            return
        sequence = _cp_sat_normalize_sequence(block_ids, [int(v) for v in result.get("sequence", [])])
        manual_bays = {int(k): str(v) for k, v in (result.get("manual_bays") or {}).items()}
        key = (tuple(sequence), tuple(sorted(manual_bays.items())))
        if key not in candidates:
            candidates[key] = {
                "label": variant_name,
                "sequence": sequence,
                "manual_bays": manual_bays,
                "cp_status": result.get("cp_status", ""),
                "cp_wall_time": float(result.get("cp_wall_time", 0.0) or 0.0),
                "cp_objective_value": result.get("cp_objective_value"),
            }
        solver_rows.append({
            "candidate_label": variant_name,
            "cp_status": result.get("cp_status", ""),
            "cp_wall_time": result.get("cp_wall_time", 0.0),
            "sequence_head": " ".join(str(v) for v in sequence[:12]),
            "bay_head": " ".join(f"{bid}:{manual_bays.get(bid, '')}" for bid in sequence[:12]),
            "selected_for_replay": True,
            "error": "",
        })

    ram_text = f"{parallel_plan.get('ram_gb'):.1f}GiB" if parallel_plan.get("ram_gb") is not None else "unknown"
    if parallel_plan.get("enabled"):
        print(
            "[CP-SAT 병렬] "
            f"candidate_workers={parallel_plan['workers']}, candidate_threads={solver_threads}, "
            f"requested_candidates={candidate_limit}, cpu={parallel_plan.get('cpu_count')}, ram={ram_text}"
        )
        start_method = str(_get_setting(settings, "cp_start_method", "fork")).strip().lower()
        try:
            ctx = mp.get_context(start_method)
        except Exception:
            ctx = mp.get_context()
        with ProcessPoolExecutor(max_workers=int(parallel_plan["workers"]), mp_context=ctx) as executor:
            futures = [executor.submit(_run_cp_sat_solver_candidate_task, task) for task in candidate_tasks]
            for idx, future in enumerate(as_completed(futures), 1):
                _store_solver_result(future.result())
                if idx == len(futures) or idx % max(1, int(parallel_plan["workers"])) == 0:
                    print(f"   [CP-SAT 병렬] 후보 생성 {idx}/{len(futures)}")
    else:
        print(f"[CP-SAT 병렬] disabled: {parallel_plan.get('reason')}")
        for task in candidate_tasks:
            _store_solver_result(_run_cp_sat_solver_candidate_task(task))

    if not candidates:
        fallback_bays = {
            block_id: "36B" if (
                float(getattr(blocks_dict[block_id], "width", 0.0) or 0.0) > 21.0
                or _cp_sat_is_ps_small_pair(blocks_dict[block_id], blocks_dict)
            ) else ("36B" if idx % 2 else "35A")
            for idx, block_id in enumerate(date_hint)
        }
        candidates[(tuple(date_hint), tuple(sorted(fallback_bays.items())))] = {
            "label": "date_hint_fallback",
            "sequence": _cp_sat_normalize_sequence(block_ids, date_hint),
            "manual_bays": fallback_bays,
            "cp_status": "FALLBACK",
            "cp_wall_time": 0.0,
            "cp_objective_value": None,
        }

    best_record: Optional[Dict[str, object]] = None
    best_score: Optional[Tuple[float, float, float, float]] = None
    replay_rows: List[Dict[str, object]] = []
    print(
        "[CP-SAT 빠른 후보] "
        f"unique_candidates={len(candidates)}, requested_candidates={candidate_limit}, time_slice={time_slice:.1f}s, "
        "최종선택=(누락, primary violation, makespan, raw violation)"
    )

    def _store_replay_result(result: Dict[str, object], idx: int) -> None:
        nonlocal best_record, best_score
        label = str(result.get("label", ""))
        sequence = _cp_sat_normalize_sequence(block_ids, [int(v) for v in result.get("sequence", [])])
        manual_bays = {int(k): str(v) for k, v in (result.get("manual_bays") or {}).items()}
        statistics = result.get("statistics") if result.get("ok") else None
        if not statistics:
            statistics = {
                "makespan_hours": float("inf"),
                "total_violations": 10**9,
                "total_violations_primary": 10**9,
                "cp_replay_error": str(result.get("error", "")),
            }
        score = _ga_score_from_stats(statistics or {})
        replay_rows.append({
            "candidate_label": label,
            "replay_index": idx,
            "primary_violations": (statistics or {}).get("total_violations_primary", (statistics or {}).get("total_violations", 0)),
            "raw_violations": (statistics or {}).get("total_violations_raw", 0),
            "makespan_hours": (statistics or {}).get("makespan_hours", 0),
            "score": str(score),
            "sequence_head": " ".join(str(v) for v in sequence[:12]),
            "bay_head": " ".join(f"{bid}:{manual_bays[bid]}" for bid in sequence[:12]),
            "error": (statistics or {}).get("cp_replay_error", ""),
        })
        # [AGENT-ADD] Store phase1 score into candidates dict for top-K profile replay selection
        cand_key = (tuple(sequence), tuple(sorted(manual_bays.items())))
        if cand_key in candidates and "_phase1_score" not in candidates[cand_key]:
            candidates[cand_key]["_phase1_score"] = score
        if best_score is None or score < best_score:
            best_score = score
            best_record = {
                "candidate": {
                    "label": label,
                    "sequence": sequence,
                    "manual_bays": manual_bays,
                    "cp_status": "REPLAYED",
                    # [AGENT-ADD] Preserve the replay mode that produced this
                    # score, so the final saved CSV is generated with the same
                    # masking/profile semantics.
                    "allow_forced_prefix_override": bool(result.get("allow_forced_prefix_override", True)),
                    "hard_profile": copy.deepcopy(result.get("hard_profile")),
                },
                "statistics": statistics or {},
                "score": score,
            }

    replay_tasks = [
        {
            "label": str(candidate["label"]),
            "sequence": list(candidate["sequence"]),
            "manual_bays": dict(candidate["manual_bays"]),
        }
        for candidate in candidates.values()
    ]
    replay_workers = min(int(parallel_plan.get("workers", 1) or 1), len(replay_tasks))
    if parallel_plan.get("enabled") and replay_workers > 1:
        start_method = str(_get_setting(settings, "cp_start_method", "fork")).strip().lower()
        try:
            ctx = mp.get_context(start_method)
        except Exception:
            ctx = mp.get_context()
        with ProcessPoolExecutor(
            max_workers=replay_workers,
            mp_context=ctx,
            initializer=_init_cp_replay_worker,
            initargs=(
                copy.deepcopy(blocks),
                copy.deepcopy(metadata),
                start_date,
                copy.deepcopy(get_runtime_config() or {}),
                max_days,
                date_offset,
                1,
            ),
        ) as executor:
            futures = [executor.submit(_run_parallel_cp_replay_task, task) for task in replay_tasks]
            for idx, future in enumerate(as_completed(futures), 1):
                _store_replay_result(future.result(), idx)
                if idx == len(futures) or idx % replay_workers == 0:
                    print(f"   [CP-SAT 병렬] final audit {idx}/{len(futures)}")
    else:
        for idx, task in enumerate(replay_tasks, 1):
            try:
                _, statistics = run_assembly_decoding_sequence_with_blocks(
                    blocks=copy.deepcopy(blocks),
                    metadata=copy.deepcopy(metadata),
                    decoding_type="assembly",
                    selection_method="priority",
                    max_days=max_days,
                    start_date=start_date,
                    date_offset=date_offset,
                    output_csv=os.devnull,
                    save_csv=False,
                    save_detailed=False,
                    forced_sequence=None,
                    expand_rows=True,
                    forced_prefix_block_ids=task["sequence"],
                    allow_forced_prefix_override=True,
                    manual_bay_assignments=task["manual_bays"],
                )
                _store_replay_result({**task, "ok": True, "statistics": statistics or {}}, idx)
            except Exception:
                _store_replay_result({**task, "ok": False, "error": traceback.format_exc()}, idx)

    # [AGENT-ADD] Phase 2: Profile-based masking-respecting replay for top-K candidates
    # Like RL/GA profile exploration: try each hard profile with override=False so PBS masking
    # filters the CP-SAT sequence and picks only feasible blocks at each step.
    cp_profile_replay_top_k = max(0, int(_get_setting(settings, "cp_profile_replay_top_k", 6) or 6))
    cp_profile_replay_enabled = _as_bool(_get_setting(settings, "cp_profile_replay", True), True)

    if cp_profile_replay_enabled and cp_profile_replay_top_k > 0 and candidates and best_record is not None:
        # Pick top-K candidates by Phase 1 score (best_score already updated in _store_replay_result)
        scored_cands = sorted(
            candidates.values(),
            key=lambda c: c.get("_phase1_score", (float("inf"),) * 4),
        )[:cp_profile_replay_top_k]

        hard_profiles = _hard_profile_specs(expanded=True)  # 8 profiles
        total_prof_replays = len(scored_cands) * len(hard_profiles)
        print(f"   [CP-SAT 프로파일 재플레이] top_k={len(scored_cands)}, hard_profiles={len(hard_profiles)}, total={total_prof_replays}")
        prof_replay_idx = 0

        for cand in scored_cands:
            for hard_profile in hard_profiles:
                prof_label = f"{cand['label']}_prof_{hard_profile['label']}_nooverride"
                try:
                    _, p_stats = _run_with_profile(
                        None,
                        hard_profile,
                        lambda seq=list(cand["sequence"]), bays=dict(cand["manual_bays"]): (
                            run_assembly_decoding_sequence_with_blocks(
                                blocks=copy.deepcopy(blocks),
                                metadata=copy.deepcopy(metadata),
                                decoding_type="assembly",
                                selection_method="priority",
                                max_days=max_days,
                                start_date=start_date,
                                date_offset=date_offset,
                                output_csv=os.devnull,
                                save_csv=False,
                                save_detailed=False,
                                forced_sequence=None,
                                expand_rows=True,
                                forced_prefix_block_ids=seq,
                                allow_forced_prefix_override=False,  # masking 존중
                                manual_bay_assignments=bays,
                            )
                        ),
                    )
                except Exception:
                    p_stats = None

                prof_replay_idx += 1
                _store_replay_result({
                    "ok": p_stats is not None,
                    "label": prof_label,
                    "sequence": list(cand["sequence"]),
                    "manual_bays": dict(cand["manual_bays"]),
                    "statistics": p_stats or {},
                    "allow_forced_prefix_override": False,
                    "hard_profile": copy.deepcopy(hard_profile),
                }, -(10000 + prof_replay_idx))
                primary_v = (p_stats or {}).get("total_violations_primary", (p_stats or {}).get("total_violations", "?"))
                makespan_v = float((p_stats or {}).get("makespan_hours", 0) or 0)
                print(f"   [프로파일 재플레이 {prof_replay_idx}/{total_prof_replays}] {prof_label} primary={primary_v} makespan={makespan_v:.2f}h")

    if best_record is None:
        elapsed = time.perf_counter() - start_t
        return [], {
            "makespan_hours": 0,
            "total_violations": 0,
            "cp_status": "NO_CANDIDATE",
            "cp_model_mode": "fast",
            "computation_seconds": elapsed,
            "selected_result_csv_name": "cp_sat_evaluation_results.csv",
        }, [], None

    selected = best_record["candidate"]
    selected_override = bool(selected.get("allow_forced_prefix_override", True))
    selected_hard_profile = selected.get("hard_profile")

    def _final_cp_replay():
        return run_assembly_decoding_sequence_with_blocks(
            blocks=copy.deepcopy(blocks),
            metadata=copy.deepcopy(metadata),
            decoding_type="assembly",
            selection_method="priority",
            max_days=max_days,
            start_date=start_date,
            date_offset=date_offset,
            output_csv=output_csv,
            save_csv=True,
            save_detailed=save_detailed,
            forced_sequence=None,
            expand_rows=True,
            forced_prefix_block_ids=selected["sequence"],
            allow_forced_prefix_override=selected_override,
            manual_bay_assignments=selected["manual_bays"],
        )

    if selected_hard_profile:
        final_results, final_statistics = _run_with_profile(None, selected_hard_profile, _final_cp_replay)
    else:
        final_results, final_statistics = _final_cp_replay()

    elapsed = time.perf_counter() - start_t
    final_statistics["cp_status"] = selected.get("cp_status", "")
    final_statistics["cp_model_mode"] = "fast"
    final_statistics["cp_selection_score_order"] = "missing,primary_violations,makespan_hours,raw_violations"
    final_statistics["cp_selected_candidate_label"] = selected.get("label", "")
    final_statistics["cp_candidates_evaluated"] = len(candidates)
    final_statistics["cp_candidates_requested"] = candidate_limit
    final_statistics["cp_solver_time_slice_sec"] = time_slice
    final_statistics["cp_solver_wall_time_total"] = solver_wall_total
    final_statistics["cp_parallel_enabled"] = bool(parallel_plan.get("enabled"))
    final_statistics["cp_parallel_workers"] = int(parallel_plan.get("workers", 1) or 1)
    final_statistics["cp_candidate_threads"] = int(solver_threads)
    final_statistics["cp_full_cpu"] = bool(full_cpu)
    final_statistics["cp_time_limit_sec"] = time_limit
    final_statistics["cp_workers"] = workers
    final_statistics["cp_seed"] = seed
    final_statistics["cp_time_scale"] = time_scale
    final_statistics["cp_sequence_head"] = selected["sequence"][:12]
    final_statistics["cp_manual_bay_head"] = " ".join(f"{bid}:{selected['manual_bays'][bid]}" for bid in selected["sequence"][:12])
    final_statistics["cp_masking_hint"] = bool(cp_masking_hint)
    final_statistics["cp_feasibility_weight"] = int(feasibility_weight)
    final_statistics["cp_profile_replay"] = bool(cp_profile_replay_enabled)
    final_statistics["cp_profile_replay_top_k"] = int(cp_profile_replay_top_k)
    final_statistics["cp_selected_allow_forced_prefix_override"] = bool(selected_override)
    final_statistics["cp_selected_hard_profile"] = str((selected_hard_profile or {}).get("label", "none"))
    final_statistics["computation_seconds"] = elapsed
    final_statistics["selected_result_csv_name"] = "cp_sat_evaluation_results.csv"

    if result_folder:
        try:
            pd.DataFrame(solver_rows).to_csv(
                os.path.join(result_folder, "cp_sat_solver_candidates.csv"),
                index=False,
                encoding="utf-8-sig",
            )
            pd.DataFrame(replay_rows).to_csv(
                os.path.join(result_folder, "cp_sat_candidate_summary.csv"),
                index=False,
                encoding="utf-8-sig",
            )
        except Exception as exc:
            print(f"   ⚠️ CP-SAT 후보 요약 저장 실패: {exc}")

    print(
        f"   CP-SAT 완료: mode=fast, selected={selected.get('label')}, "
        f"Makespan = {float(final_statistics.get('makespan_hours', 0) or 0):.2f}시간, "
        f"Primary = {final_statistics.get('total_violations_primary', final_statistics.get('total_violations', 0))}, "
        f"계산시간 = {elapsed:.2f}s"
    )
    return final_results, final_statistics, [], None


def run_cp_sat_baseline(blocks: List, metadata: Dict, start_date: str, result_folder: str = None,
                        settings: Optional[Dict[str, object]] = None) -> Tuple[List[Dict], Dict, List[Dict], object]:
    """OR-Tools CP-SAT baseline with explicit PBS process and Bay split.

    # [AGENT-ADD] CP-SAT creates a full block order and manual 35A/36B assignment.
    # The chosen solution is then replayed through the existing PBS scheduler with
    # forced sequence + manual bay override, so final audit metrics stay identical
    # to GA/RL/heuristic comparisons.
    """
    start_t = time.perf_counter()
    output_csv = os.path.join(result_folder, "cp_sat_evaluation_results.csv") if result_folder else "cp_sat_evaluation_results.csv"
    if os.path.exists(output_csv):
        os.remove(output_csv)

    try:
        from ortools.sat.python import cp_model
    except Exception as exc:
        elapsed = time.perf_counter() - start_t
        print(f"   ❌ CP-SAT 실행 불가: OR-Tools import 실패 ({exc})")
        return [], {
            "makespan_hours": 0,
            "total_violations": 0,
            "cp_status": "IMPORT_FAILED",
            "cp_error": str(exc),
            "computation_seconds": elapsed,
            "selected_result_csv_name": "cp_sat_evaluation_results.csv",
        }, [], None

    max_days, date_offset, save_detailed = _get_common_settings(settings)
    time_limit = float(_get_setting(settings, "cp_time_limit_sec", 60.0) or 60.0)
    time_scale = max(1, int(_get_setting(settings, "cp_time_scale", 60) or 60))
    seed = int(_get_setting(settings, "cp_seed", 42) or 42)
    workers = _cp_sat_worker_count(settings)
    enforce_basic_bay_rules = _as_bool(_get_setting(settings, "cp_enforce_basic_bay_rules", True), True)
    objective_weight = max(1, int(_get_setting(settings, "cp_makespan_weight", 1000) or 1000))
    model_mode = str(_get_setting(settings, "cp_model_mode", "fast") or "fast").strip().lower()
    exact_max_blocks = int(_get_setting(settings, "cp_exact_max_blocks", 0) or 0)
    native_cfg = ConstraintConfig()
    native_cfg.set_constraint_scope("assembly")
    # [AGENT-ADD] Prefix-level P7#1 is exact but expensive, so keep it opt-in.
    setattr(native_cfg, "cp_native_prefix_balance", _as_bool(_get_setting(settings, "cp_native_prefix_balance", False), False))

    block_ids = [_ga_block_id(block) for block in blocks]
    n_blocks = len(block_ids)
    if n_blocks == 0:
        return [], {
            "makespan_hours": 0,
            "total_violations": 0,
            "cp_status": "EMPTY",
            "computation_seconds": time.perf_counter() - start_t,
            "selected_result_csv_name": "cp_sat_evaluation_results.csv",
        }, [], None

    blocks_dict = {_ga_block_id(block): block for block in blocks}
    durations = {block_id: _cp_sat_processing_times(blocks_dict[block_id], time_scale) for block_id in block_ids}

    exact_modes = {"exact", "audit_exact", "exact_audit", "audit"}
    exact_requested = model_mode in exact_modes
    # [AGENT-EDIT] Do not silently downgrade an explicit exact/audit_exact request
    # to fast mode just because cp_exact_max_blocks was omitted. A value <= 0 now
    # means "run the requested exact mode for this instance size".
    if exact_requested and exact_max_blocks <= 0:
        exact_max_blocks = n_blocks
    if not exact_requested:
        return _cp_sat_fast_candidate_baseline(
            cp_model=cp_model,
            blocks=blocks,
            metadata=metadata,
            start_date=start_date,
            result_folder=result_folder,
            settings=settings,
            output_csv=output_csv,
            max_days=max_days,
            date_offset=date_offset,
            save_detailed=save_detailed,
            time_limit=time_limit,
            time_scale=time_scale,
            seed=seed,
            workers=workers,
            enforce_basic_bay_rules=enforce_basic_bay_rules,
        )
    if n_blocks > exact_max_blocks:
        print(
            "   ⚠️ CP-SAT exact 요청이지만 블록 수가 제한을 초과하여 fast 후보 모드로 전환: "
            f"blocks={n_blocks}, cp_exact_max_blocks={exact_max_blocks}"
        )
        return _cp_sat_fast_candidate_baseline(
            cp_model=cp_model,
            blocks=blocks,
            metadata=metadata,
            start_date=start_date,
            result_folder=result_folder,
            settings=settings,
            output_csv=output_csv,
            max_days=max_days,
            date_offset=date_offset,
            save_detailed=save_detailed,
            time_limit=time_limit,
            time_scale=time_scale,
            seed=seed,
            workers=workers,
            enforce_basic_bay_rules=enforce_basic_bay_rules,
        )

    horizon = max(1, sum(sum(values) for values in durations.values()))
    model = cp_model.CpModel()

    print(
        f" CP-SAT baseline 실행 중... "
        f"(blocks={n_blocks}, mode={model_mode}, time_limit={time_limit:.1f}s, workers={workers}, scale={time_scale})"
    )

    position = {
        block_id: model.NewIntVar(0, n_blocks - 1, f"pos_{block_id}")
        for block_id in block_ids
    }
    model.AddAllDifferent([position[block_id] for block_id in block_ids])

    bay_b = {
        block_id: model.NewBoolVar(f"bay36b_{block_id}")
        for block_id in block_ids
    }
    block_at_pos = [
        model.NewIntVar(0, n_blocks - 1, f"block_index_at_pos_{pos_idx}")
        for pos_idx in range(n_blocks)
    ]
    model.AddInverse([position[block_id] for block_id in block_ids], block_at_pos)

    common_start: Dict[Tuple[int, int], object] = {}
    common_end: Dict[Tuple[int, int], object] = {}
    branch_start: Dict[Tuple[int, int], object] = {}
    branch_end: Dict[Tuple[int, int], object] = {}

    for block_id in block_ids:
        block_durations = durations[block_id]
        for proc_idx in range(5):
            start_var = model.NewIntVar(0, horizon, f"c_s_{block_id}_{proc_idx}")
            end_var = model.NewIntVar(0, horizon, f"c_e_{block_id}_{proc_idx}")
            model.Add(end_var == start_var + block_durations[proc_idx])
            if proc_idx > 0:
                model.Add(start_var >= common_end[(block_id, proc_idx - 1)])
            common_start[(block_id, proc_idx)] = start_var
            common_end[(block_id, proc_idx)] = end_var

        for branch_idx in range(3):
            start_var = model.NewIntVar(0, horizon, f"b_s_{block_id}_{branch_idx}")
            end_var = model.NewIntVar(0, horizon, f"b_e_{block_id}_{branch_idx}")
            model.Add(end_var == start_var + block_durations[branch_idx + 5])
            if branch_idx == 0:
                model.Add(start_var >= common_end[(block_id, 4)])
            else:
                model.Add(start_var >= branch_end[(block_id, branch_idx - 1)])
            branch_start[(block_id, branch_idx)] = start_var
            branch_end[(block_id, branch_idx)] = end_var

        # [AGENT-EDIT] Exact modes must not forbid violations that final audit
        # would allow/count. Width and P/S-small Bay rules are modeled below as
        # native soft penalties, not hard bans.
        if False and enforce_basic_bay_rules:
            block = blocks_dict[block_id]
            width = float(getattr(block, "width", 0.0) or 0.0)
            if width > 21.0 or _cp_sat_is_ps_small_pair(block, blocks_dict):
                model.Add(bay_b[block_id] == 1)

    order_literals: Dict[Tuple[int, int], object] = {}
    for left_idx in range(n_blocks):
        left_id = block_ids[left_idx]
        for right_idx in range(left_idx + 1, n_blocks):
            right_id = block_ids[right_idx]
            left_before_right = model.NewBoolVar(f"order_{left_id}_before_{right_id}")
            order_literals[(left_id, right_id)] = left_before_right
            model.Add(position[left_id] < position[right_id]).OnlyEnforceIf(left_before_right)
            model.Add(position[left_id] > position[right_id]).OnlyEnforceIf(left_before_right.Not())

            # 공통 5개 공정은 하나의 연속 라인을 같은 순서로 통과한다.
            for proc_idx in range(5):
                model.Add(common_start[(right_id, proc_idx)] >= common_end[(left_id, proc_idx)]).OnlyEnforceIf(left_before_right)
                model.Add(common_start[(left_id, proc_idx)] >= common_end[(right_id, proc_idx)]).OnlyEnforceIf(left_before_right.Not())

            # 분기 3개 공정은 Bay가 같을 때만 같은 장비 순서를 공유한다.
            for branch_idx in range(3):
                model.Add(branch_start[(right_id, branch_idx)] >= branch_end[(left_id, branch_idx)]).OnlyEnforceIf(
                    [left_before_right, bay_b[left_id].Not(), bay_b[right_id].Not()]
                )
                model.Add(branch_start[(right_id, branch_idx)] >= branch_end[(left_id, branch_idx)]).OnlyEnforceIf(
                    [left_before_right, bay_b[left_id], bay_b[right_id]]
                )
                model.Add(branch_start[(left_id, branch_idx)] >= branch_end[(right_id, branch_idx)]).OnlyEnforceIf(
                    [left_before_right.Not(), bay_b[left_id].Not(), bay_b[right_id].Not()]
                )
                model.Add(branch_start[(left_id, branch_idx)] >= branch_end[(right_id, branch_idx)]).OnlyEnforceIf(
                    [left_before_right.Not(), bay_b[left_id], bay_b[right_id]]
                )

    makespan = model.NewIntVar(0, horizon, "makespan")
    for block_id in block_ids:
        model.Add(makespan >= branch_end[(block_id, 2)])

    # Bay 분할은 makespan 다음의 보조 목적이다. 최종 위반은 replay audit에서 다시 계산된다.
    bay_load_values = {
        block_id: max(1, sum(durations[block_id][5:8]))
        for block_id in block_ids
    }
    total_bay_load = max(1, sum(bay_load_values.values()))
    bay_b_load = model.NewIntVar(0, total_bay_load, "bay_b_load")
    model.Add(bay_b_load == sum(bay_load_values[block_id] * bay_b[block_id] for block_id in block_ids))
    bay_balance_abs = model.NewIntVar(0, total_bay_load, "bay_balance_abs")
    model.AddAbsEquality(bay_balance_abs, total_bay_load - 2 * bay_b_load)
    native_primary_terms, native_raw_terms, native_constraint_counts = _cp_add_native_pbs_violation_terms(
        model=model,
        block_ids=block_ids,
        blocks_dict=blocks_dict,
        position=position,
        bay_b=bay_b,
        block_at_pos=block_at_pos,
        common_start=common_start,
        start_date=start_date,
        max_days=max_days,
        time_scale=time_scale,
        horizon=horizon,
        cfg=native_cfg,
    )
    native_primary_expr = sum(native_primary_terms) if native_primary_terms else 0
    native_raw_expr = sum(native_raw_terms) if native_raw_terms else 0
    primary_weight = max(1, int(horizon * objective_weight + total_bay_load + len(native_raw_terms) + 1000))
    # [AGENT-EDIT] Native exact objective is violation-first. Makespan is only
    # optimized after the CP-native primary violation count is minimized.
    model.Minimize(native_primary_expr * primary_weight + makespan * objective_weight + native_raw_expr + bay_balance_abs)

    # 기존 착수일 순서를 힌트로만 준다. 강제하지 않기 때문에 CP가 더 좋은 순서를 찾을 수 있다.
    hinted_order = [
        _ga_block_id(block)
        for block in sorted(blocks, key=lambda b: str(getattr(b, "assembly_start_date", "")))
    ]
    for idx, block_id in enumerate(hinted_order):
        if block_id in position:
            model.AddHint(position[block_id], idx)
            if enforce_basic_bay_rules and (
                float(getattr(blocks_dict[block_id], "width", 0.0) or 0.0) > 21.0
                or _cp_sat_is_ps_small_pair(blocks_dict[block_id], blocks_dict)
            ):
                model.AddHint(bay_b[block_id], 1)
            else:
                model.AddHint(bay_b[block_id], idx % 2)

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_search_workers = workers
    solver.parameters.random_seed = seed

    if model_mode in {"audit_exact", "exact_audit", "audit"}:
        audit_deadline = time.perf_counter() + time_limit
        max_rejections = max(1, int(_get_setting(settings, "cp_audit_max_rejections", 200) or 200))
        continue_after_zero = _as_bool(_get_setting(settings, "cp_audit_continue_after_zero", True), True)
        audit_rows: List[Dict[str, object]] = []
        best_attempt: Optional[Dict[str, object]] = None
        best_score: Optional[Tuple[float, float, float, float]] = None

        print(
            "[CP-SAT audit_exact] "
            f"final audit를 제약 오라클로 사용, max_rejections={max_rejections}, "
            "선택 기준=(missing, primary violation, makespan, raw violation)"
        )

        for attempt in range(1, max_rejections + 1):
            remaining = audit_deadline - time.perf_counter()
            if remaining <= 0:
                break
            solver = cp_model.CpSolver()
            solver.parameters.max_time_in_seconds = max(0.1, remaining)
            solver.parameters.num_search_workers = workers
            solver.parameters.random_seed = seed + attempt - 1
            status = solver.Solve(model)
            status_name = solver.StatusName(status)
            if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
                elapsed = time.perf_counter() - start_t
                print(f"   ⚠️ CP-SAT audit_exact 중단: status={status_name}, attempts={attempt - 1}")
                if result_folder and audit_rows:
                    pd.DataFrame(audit_rows).to_csv(
                        os.path.join(result_folder, "cp_sat_audit_exact_attempts.csv"),
                        index=False,
                        encoding="utf-8-sig",
                    )
                return [], {
                    "makespan_hours": 0,
                    "total_violations": 0,
                    "cp_status": status_name,
                    "cp_model_mode": "audit_exact",
                    "cp_audit_status": "NO_CP_SOLUTION",
                    "cp_audit_attempts": attempt - 1,
                    "cp_time_limit_sec": time_limit,
                    "cp_workers": workers,
                    "cp_seed": seed,
                    "cp_time_scale": time_scale,
                    "cp_wall_time": solver.WallTime(),
                    "computation_seconds": elapsed,
                    "selected_result_csv_name": "cp_sat_evaluation_results.csv",
                }, [], None

            cp_sequence = sorted(block_ids, key=lambda block_id: solver.Value(position[block_id]))
            cp_manual_bays = {
                block_id: "36B" if solver.Value(bay_b[block_id]) else "35A"
                for block_id in block_ids
            }
            repair_count = 0
            if enforce_basic_bay_rules:
                cp_manual_bays, repair_count = _cp_repair_basic_manual_bays(cp_manual_bays, blocks_dict, native_cfg)
            try:
                _, audit_statistics = run_assembly_decoding_sequence_with_blocks(
                    blocks=copy.deepcopy(blocks),
                    metadata=copy.deepcopy(metadata),
                    decoding_type="assembly",
                    selection_method="priority",
                    max_days=max_days,
                    start_date=start_date,
                    date_offset=date_offset,
                    output_csv=os.devnull,
                    save_csv=False,
                    save_detailed=False,
                    forced_sequence=None,
                    expand_rows=True,
                    forced_prefix_block_ids=cp_sequence,
                    allow_forced_prefix_override=True,
                    manual_bay_assignments=cp_manual_bays,
                )
            except Exception as exc:
                audit_statistics = {
                    "makespan_hours": float("inf"),
                    "total_violations": 10**9,
                    "total_violations_primary": 10**9,
                    "cp_audit_error": str(exc),
                }

            score = _ga_score_from_stats(audit_statistics or {})
            audit_rows.append({
                "attempt": attempt,
                "cp_status": status_name,
                "cp_objective_value": float(solver.ObjectiveValue()),
                "cp_best_bound": float(solver.BestObjectiveBound()),
                "cp_wall_time": float(solver.WallTime()),
                "missing_penalty": score[0],
                "primary_violations": score[1],
                "makespan_hours": score[2],
                "raw_violations": score[3],
                "sequence_head": " ".join(str(v) for v in cp_sequence[:12]),
                "bay_head": " ".join(f"{bid}:{cp_manual_bays[bid]}" for bid in cp_sequence[:12]),
                "cp_basic_bay_repair_count": int(repair_count),
                "accepted": bool(score[0] == 0 and score[1] == 0),
                "error": (audit_statistics or {}).get("cp_audit_error", ""),
            })
            print(
                f"   [audit_exact {attempt}] cp={status_name} "
                f"primary={score[1]:.0f}, makespan={score[2]:.2f}h, raw={score[3]:.0f}"
            )

            if best_score is None or score < best_score:
                best_score = score
                best_attempt = {
                    "sequence": cp_sequence,
                    "manual_bays": cp_manual_bays,
                    "statistics": audit_statistics or {},
                    "score": score,
                    "cp_status": status_name,
                    "cp_objective_value": float(solver.ObjectiveValue()),
                    "cp_best_bound": float(solver.BestObjectiveBound()),
                    "cp_wall_time": float(solver.WallTime()),
                    "cp_basic_bay_repair_count": int(repair_count),
                }

            if score[0] == 0 and score[1] == 0 and not continue_after_zero:
                break

            no_good_terms = []
            for pos_idx, block_id in enumerate(cp_sequence):
                match_pos = model.NewBoolVar(f"audit_ng_{attempt}_pos_{block_id}")
                model.Add(position[block_id] == pos_idx).OnlyEnforceIf(match_pos)
                model.Add(position[block_id] != pos_idx).OnlyEnforceIf(match_pos.Not())
                no_good_terms.append(match_pos.Not())
            for block_id in block_ids:
                if cp_manual_bays[block_id] == "36B":
                    no_good_terms.append(bay_b[block_id].Not())
                else:
                    no_good_terms.append(bay_b[block_id])
            model.AddBoolOr(no_good_terms)

        if result_folder and audit_rows:
            pd.DataFrame(audit_rows).to_csv(
                os.path.join(result_folder, "cp_sat_audit_exact_attempts.csv"),
                index=False,
                encoding="utf-8-sig",
            )

        if best_attempt is None:
            elapsed = time.perf_counter() - start_t
            print(
                f"   ⚠️ CP-SAT audit_exact: 제한 내 평가 가능한 해를 찾지 못함 "
                f"(attempts={len(audit_rows)}, 계산시간={elapsed:.2f}s)"
            )
            return [], {
                "makespan_hours": 0,
                "total_violations": 0,
                "cp_status": (best_attempt or {}).get("cp_status", "NO_AUDIT_FEASIBLE"),
                "cp_model_mode": "audit_exact",
                "cp_audit_status": "NO_AUDIT_CANDIDATE_WITHIN_LIMIT",
                "cp_audit_attempts": len(audit_rows),
                "cp_audit_best_score": str(best_score),
                "cp_time_limit_sec": time_limit,
                "cp_workers": workers,
                "cp_seed": seed,
                "cp_time_scale": time_scale,
                "computation_seconds": elapsed,
                "selected_result_csv_name": "cp_sat_evaluation_results.csv",
            }, [], None

        accepted_payload = best_attempt
        cp_sequence = list(accepted_payload["sequence"])
        cp_manual_bays = dict(accepted_payload["manual_bays"])
        final_results, final_statistics = run_assembly_decoding_sequence_with_blocks(
            blocks=copy.deepcopy(blocks),
            metadata=copy.deepcopy(metadata),
            decoding_type="assembly",
            selection_method="priority",
            max_days=max_days,
            start_date=start_date,
            date_offset=date_offset,
            output_csv=output_csv,
            save_csv=True,
            save_detailed=save_detailed,
            forced_sequence=None,
            expand_rows=True,
            forced_prefix_block_ids=cp_sequence,
            allow_forced_prefix_override=True,
            manual_bay_assignments=cp_manual_bays,
        )

        elapsed = time.perf_counter() - start_t
        final_statistics["cp_status"] = accepted_payload.get("cp_status", "")
        final_statistics["cp_model_mode"] = "audit_exact"
        zero_primary = bool(best_score and best_score[0] == 0 and best_score[1] == 0)
        final_statistics["cp_audit_status"] = "ZERO_PRIMARY_ACCEPTED" if zero_primary else "BEST_AUDIT_SCORE_WITHIN_LIMIT"
        final_statistics["cp_selection_score_order"] = "missing,primary_violations,makespan_hours,raw_violations"
        final_statistics["cp_audit_attempts"] = len(audit_rows)
        final_statistics["cp_audit_continue_after_zero"] = bool(continue_after_zero)
        final_statistics["cp_audit_best_score"] = str(best_score)
        final_statistics["cp_objective_value"] = accepted_payload.get("cp_objective_value", "")
        final_statistics["cp_best_bound"] = accepted_payload.get("cp_best_bound", "")
        # [AGENT-ADD] Store proof gap so time-limited CP-SAT runs are not reported as fully proven.
        try:
            objective_value = float(final_statistics["cp_objective_value"])
            best_bound = float(final_statistics["cp_best_bound"])
            final_statistics["cp_gap"] = 0.0 if objective_value == 0 else abs(objective_value - best_bound) / max(1.0, abs(objective_value))
        except Exception:
            final_statistics["cp_gap"] = ""
        final_statistics["cp_wall_time"] = accepted_payload.get("cp_wall_time", "")
        final_statistics["cp_time_limit_sec"] = time_limit
        final_statistics["cp_workers"] = workers
        final_statistics["cp_seed"] = seed
        final_statistics["cp_time_scale"] = time_scale
        final_statistics["cp_sequence_head"] = cp_sequence[:12]
        final_statistics["cp_manual_bay_head"] = " ".join(f"{block_id}:{cp_manual_bays[block_id]}" for block_id in cp_sequence[:12])
        final_statistics["cp_basic_bay_repair_count"] = int(accepted_payload.get("cp_basic_bay_repair_count", 0) or 0)
        final_statistics["cp_native_primary_terms"] = len(native_primary_terms)
        final_statistics["cp_native_raw_terms"] = len(native_raw_terms)
        final_statistics["cp_native_constraint_counts"] = str(native_constraint_counts)
        final_statistics["cp_native_objective_order"] = "native_primary_violations,makespan,native_raw_violations,bay_balance"
        final_statistics["cp_native_prefix_balance"] = bool(getattr(native_cfg, "cp_native_prefix_balance", False))
        final_statistics["computation_seconds"] = elapsed
        final_statistics["selected_result_csv_name"] = "cp_sat_evaluation_results.csv"
        print(
            f"   CP-SAT audit_exact 완료: attempts={len(audit_rows)}, "
            f"Makespan = {float(final_statistics.get('makespan_hours', 0) or 0):.2f}시간, "
            f"Primary = {final_statistics.get('total_violations_primary', final_statistics.get('total_violations', 0))}, "
            f"계산시간 = {elapsed:.2f}s"
        )
        return final_results, final_statistics, [], None

    status = solver.Solve(model)
    status_name = solver.StatusName(status)
    if status not in {cp_model.OPTIMAL, cp_model.FEASIBLE}:
        elapsed = time.perf_counter() - start_t
        print(f"   ⚠️ CP-SAT 해를 찾지 못함: status={status_name}, 계산시간={elapsed:.2f}s")
        return [], {
            "makespan_hours": 0,
            "total_violations": 0,
            "cp_status": status_name,
            "cp_time_limit_sec": time_limit,
            "cp_workers": workers,
            "cp_seed": seed,
            "cp_time_scale": time_scale,
            "cp_wall_time": solver.WallTime(),
            "computation_seconds": elapsed,
            "selected_result_csv_name": "cp_sat_evaluation_results.csv",
        }, [], None

    cp_sequence = sorted(block_ids, key=lambda block_id: solver.Value(position[block_id]))
    cp_manual_bays = {
        block_id: "36B" if solver.Value(bay_b[block_id]) else "35A"
        for block_id in block_ids
    }
    repair_count = 0
    if enforce_basic_bay_rules:
        cp_manual_bays, repair_count = _cp_repair_basic_manual_bays(cp_manual_bays, blocks_dict, native_cfg)

    final_results, final_statistics = run_assembly_decoding_sequence_with_blocks(
        blocks=copy.deepcopy(blocks),
        metadata=copy.deepcopy(metadata),
        decoding_type="assembly",
        selection_method="priority",
        max_days=max_days,
        start_date=start_date,
        date_offset=date_offset,
        output_csv=output_csv,
        save_csv=True,
        save_detailed=save_detailed,
        forced_sequence=None,
        expand_rows=True,
        forced_prefix_block_ids=cp_sequence,
        allow_forced_prefix_override=True,
        manual_bay_assignments=cp_manual_bays,
    )

    elapsed = time.perf_counter() - start_t
    final_statistics["cp_status"] = status_name
    final_statistics["cp_model_mode"] = "exact"
    final_statistics["cp_selection_score_order"] = "single_exact_solution_replayed_by_final_audit"
    final_statistics["cp_objective_value"] = float(solver.ObjectiveValue())
    final_statistics["cp_best_bound"] = float(solver.BestObjectiveBound())
    # [AGENT-ADD] Store proof gap so exact runs can distinguish OPTIMAL from FEASIBLE.
    final_statistics["cp_gap"] = (
        0.0
        if float(solver.ObjectiveValue()) == 0
        else abs(float(solver.ObjectiveValue()) - float(solver.BestObjectiveBound()))
        / max(1.0, abs(float(solver.ObjectiveValue())))
    )
    final_statistics["cp_wall_time"] = float(solver.WallTime())
    final_statistics["cp_time_limit_sec"] = time_limit
    final_statistics["cp_workers"] = workers
    final_statistics["cp_seed"] = seed
    final_statistics["cp_time_scale"] = time_scale
    final_statistics["cp_internal_makespan_units"] = int(solver.Value(makespan))
    final_statistics["cp_internal_makespan_minutes"] = float(solver.Value(makespan)) / float(time_scale)
    final_statistics["cp_sequence_head"] = cp_sequence[:12]
    final_statistics["cp_manual_bay_head"] = " ".join(f"{block_id}:{cp_manual_bays[block_id]}" for block_id in cp_sequence[:12])
    final_statistics["cp_basic_bay_repair_count"] = int(repair_count)
    final_statistics["cp_native_primary_terms"] = len(native_primary_terms)
    final_statistics["cp_native_raw_terms"] = len(native_raw_terms)
    final_statistics["cp_native_constraint_counts"] = str(native_constraint_counts)
    final_statistics["cp_native_objective_order"] = "native_primary_violations,makespan,native_raw_violations,bay_balance"
    final_statistics["cp_native_prefix_balance"] = bool(getattr(native_cfg, "cp_native_prefix_balance", False))
    final_statistics["computation_seconds"] = elapsed
    final_statistics["selected_result_csv_name"] = "cp_sat_evaluation_results.csv"

    if result_folder:
        summary_path = os.path.join(result_folder, "cp_sat_solution_summary.csv")
        try:
            pd.DataFrame([
                {
                    "position": idx + 1,
                    "block_id": block_id,
                    "manual_bay": cp_manual_bays[block_id],
                    "cp_common_start_min": solver.Value(common_start[(block_id, 0)]) / float(time_scale),
                    "cp_common_end_min": solver.Value(common_end[(block_id, 4)]) / float(time_scale),
                    "cp_branch_end_min": solver.Value(branch_end[(block_id, 2)]) / float(time_scale),
                }
                for idx, block_id in enumerate(cp_sequence)
            ]).to_csv(summary_path, index=False, encoding="utf-8-sig")
        except Exception as exc:
            print(f"   ⚠️ CP-SAT 해 요약 저장 실패: {exc}")

    print(
        f"   CP-SAT 완료: status={status_name}, "
        f"Makespan = {float(final_statistics.get('makespan_hours', 0) or 0):.2f}시간, "
        f"Primary = {final_statistics.get('total_violations_primary', final_statistics.get('total_violations', 0))}, "
        f"계산시간 = {elapsed:.2f}s"
    )
    return final_results, final_statistics, [], None
# ==== [AGENT-ADD END] ====


def run_rl_evaluation(blocks: List, metadata: Dict, start_date: str, result_folder: str = None, num_samples: int = 50,
                      settings: Optional[Dict[str, object]] = None) -> Tuple[List[Dict], Dict, List[Dict], object]:
    """
    RL 모델 평가 실행 (샘플링 후 최고 성능 선택)
    """
    print(f" RL 모델 평가 실행 중 (profile당 {num_samples}번 샘플링)...")

    rl_model_path = str(_get_setting(settings, "rl_model_path", "") or "").strip()
    if not rl_model_path:
        print("❌ RL 모델 경로가 비어있습니다.")
        return [], {}, [], None

    if not os.path.exists(rl_model_path):
        print(f"❌ RL 모델 파일을 찾을 수 없습니다: {rl_model_path}")
        return [], {}, [], None

    device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')
    cfg = get_runtime_config() or {}
    if isinstance(cfg, dict):
        cfg_device = cfg.get("device") or (cfg.get("evaluation") or {}).get("device")
        if cfg_device:
            dev = str(cfg_device).strip().lower()
            if dev == "cpu":
                device = torch.device("cpu")
            elif dev.startswith("cuda"):
                device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"   🔧 디바이스: {device}")

    # ==== [AGENT-EDIT BEGIN: RL checkpoint shape auto-match] ====
    def _extract_actor_state_dict(ckpt: Dict) -> Dict:
        if isinstance(ckpt, dict):
            if 'actor_state_dict' in ckpt:
                return ckpt['actor_state_dict']
            if 'model_state_dict' in ckpt:
                return ckpt['model_state_dict']
        return ckpt

    def _infer_feature_mode_from_dim(dim: Optional[int]) -> str:
        if dim == REDUCED_BLOCK_FEATURE_DIM:
            return "reduced"
        if dim == CONSTRAINT_BLOCK_FEATURE_DIM:
            return "constraint"
        if dim == DIFF_BLOCK_FEATURE_DIM:
            return "diff"
        if dim == BLOCK_FEATURE_DIM:
            return "full"
        return str(_get_setting(settings, "feature_mode", "reduced"))

    def _infer_actor_params_from_state(state_dict: Dict) -> Dict:
        inferred = {}
        embed_w = state_dict.get('embedding.0.weight')
        embed2_w = state_dict.get('embedding.2.weight')
        if embed_w is not None:
            inferred['embedding_dim'] = int(embed_w.shape[0])
            inferred['feature_dim'] = int(embed_w.shape[1])
        if embed2_w is not None:
            inferred['hidden_dim'] = int(embed2_w.shape[0])
        if 'env_state_mean' in state_dict:
            inferred['use_env_state'] = True
            inferred['env_state_dim'] = int(state_dict['env_state_mean'].shape[0])
        else:
            inferred['use_env_state'] = False
        inferred['use_positional_encoding'] = 'positional_encoding' in state_dict
        if 'feature_dim' in inferred:
            inferred['feature_mode'] = _infer_feature_mode_from_dim(inferred['feature_dim'])
        return inferred

    checkpoint = torch.load(rl_model_path, map_location=device)
    actor_state = _extract_actor_state_dict(checkpoint)
    inferred = _infer_actor_params_from_state(actor_state)

    feature_mode = inferred.get('feature_mode', str(_get_setting(settings, "feature_mode", "reduced")))
    if feature_mode == "reduced":
        fallback_feature_dim = REDUCED_BLOCK_FEATURE_DIM
        fallback_env_dim = REDUCED_ENV_STATE_DIM
    elif feature_mode == "constraint":
        fallback_feature_dim = CONSTRAINT_BLOCK_FEATURE_DIM
        fallback_env_dim = CONSTRAINT_ENV_STATE_DIM
    elif feature_mode == "diff":
        fallback_feature_dim = DIFF_BLOCK_FEATURE_DIM
        fallback_env_dim = DIFF_ENV_STATE_DIM
    else:
        fallback_feature_dim = BLOCK_FEATURE_DIM
        fallback_env_dim = ENV_STATE_DIM

    actor_params = {
        'embedding_dim': inferred.get('embedding_dim', 256),
        'hidden_dim': inferred.get('hidden_dim', inferred.get('embedding_dim', 256)),
        'n_layers': 2,
        'n_heads': 4,
        'dropout': 0.1,
        'use_logit_clipping': True,
        'C': 10.0,
        'T': 0.5,
        'feature_dim': inferred.get('feature_dim', fallback_feature_dim),
        'feature_mode': feature_mode,
        'use_positional_encoding': inferred.get('use_positional_encoding', bool(_get_setting(settings, "use_positional_encoding", False))),
        'use_env_state': inferred.get('use_env_state', True),
        'env_state_dim': inferred.get('env_state_dim', fallback_env_dim)
    }
    print(
        "   🔧 RL 모델 파라미터 자동 추론: "
        f"feature_mode={actor_params['feature_mode']}, "
        f"feature_dim={actor_params['feature_dim']}, "
        f"env_state_dim={actor_params['env_state_dim']}, "
        f"embedding_dim={actor_params['embedding_dim']}, "
        f"hidden_dim={actor_params['hidden_dim']}, "
        f"use_env_state={actor_params['use_env_state']}, "
        f"use_positional_encoding={actor_params['use_positional_encoding']}"
    )
    # ==== [AGENT-EDIT END] ====

    model = SingleStepPtrNet(**actor_params).to(device)
    model.load_state_dict(actor_state, strict=False)
    model.eval()
    print(f"   ✅ PPO 모델 로드 완료: {rl_model_path}")

    best_results = None
    best_statistics = None
    best_episode_data = None
    best_env = None
    best_makespan = float('inf')
    # [AGENT-ADD] 로그 해석용: 제약 우선 best(p,m)도 별도로 추적한다.
    best_pm_primary = float('inf')
    best_pm_makespan = float('inf')
    best_pm_raw = float('inf')
    # [AGENT-ADD] dominance-best는 LPT보다 makespan/primary가 동시에 안 나쁜 샘플 중 대표해다.
    best_dom_results = None
    best_dom_statistics = None
    best_dom_episode_data = None
    best_dom_env = None
    best_dom_primary = float('inf')
    best_dom_makespan = float('inf')
    best_dom_raw = float('inf')
    best_pm_results = None
    best_pm_statistics = None
    best_pm_episode_data = None
    best_pm_env = None

    save_detailed_flag = bool(_get_setting(settings, "save_detailed_csv", True))
    selection_mode = str(_get_setting(settings, "rl_selection_mode", "best_pm")).strip().lower()
    save_samples_summary = bool(_get_setting(settings, "save_rl_samples_summary", False))
    lpt_ref_makespan = _get_setting(settings, "rl_lpt_reference_makespan", None)
    lpt_ref_primary = _get_setting(settings, "rl_lpt_reference_primary", None)
    lpt_ref_raw = _get_setting(settings, "rl_lpt_reference_raw", None)
    has_lpt_reference = lpt_ref_makespan is not None and lpt_ref_primary is not None
    if lpt_ref_makespan is not None:
        lpt_ref_makespan = float(lpt_ref_makespan)
    if lpt_ref_primary is not None:
        lpt_ref_primary = int(lpt_ref_primary)
    if lpt_ref_raw is not None:
        lpt_ref_raw = int(lpt_ref_raw)

    sample_records: List[Dict[str, object]] = []
    best_m_sample_id: Optional[int] = None
    best_pm_sample_id: Optional[int] = None
    best_dom_sample_id: Optional[int] = None
    profile_pairs = _profile_pairs(settings, "rl", num_samples)
    profile_enabled = _profile_sampling_enabled(settings)
    total_samples = len(profile_pairs)
    profile_count = len(_all_profile_pairs("rl")) if profile_enabled else 1
    print(
        f"   profile_sampling={profile_enabled}, selection_mode={selection_mode}, "
        f"profiles={profile_count}, samples_per_profile={num_samples}, total_samples={total_samples}"
    )
    verbose_samples = os.environ.get("PBS_EVAL_VERBOSE_SAMPLES", "").strip().lower() in {"1", "true", "on", "yes"}
    progress_interval = max(1, int(_get_setting(settings, "eval_progress_interval", 100) or 100))
    profile_seen_counts: Dict[Tuple[str, str], int] = {}
    eval_tasks: List[Dict[str, object]] = []
    for task_idx, (bias_profile, hard_profile) in enumerate(profile_pairs, 1):
        bias_label, hard_label = _profile_label(bias_profile, hard_profile)
        profile_key = (bias_label, hard_label)
        profile_seen_counts[profile_key] = profile_seen_counts.get(profile_key, 0) + 1
        runtime_cfg = (
            _build_profile_runtime_config(bias_profile, hard_profile)
            if profile_enabled
            else copy.deepcopy(get_runtime_config() or {})
        )
        eval_tasks.append({
            "sample_id": task_idx,
            "profile_sample_id": profile_seen_counts[profile_key],
            "bias_label": bias_label,
            "hard_label": hard_label,
            "bias_profile": bias_profile,
            "hard_profile": hard_profile,
            "runtime_cfg": runtime_cfg,
            "seed": random.randint(1, 2 ** 31 - 2),
        })

    def _save_schedule_rows(filename: str, rows: Optional[List[Dict]]) -> Optional[str]:
        if not result_folder or not rows:
            return None
        output_csv = os.path.join(result_folder, filename)
        pd.DataFrame(rows).to_csv(output_csv, index=False, encoding='utf-8-sig')
        return output_csv

    def _rebuild_detailed_for_sequence(rows: Optional[List[Dict]]) -> None:
        # [AGENT-EDIT] 최종 대표해 1개에 대해서만 detailed CSV를 재생성한다.
        if not rows or not save_detailed_flag:
            return
        print("    대표해 기반 Detailed CSV 생성 중...")
        date_keys = list(set([r.get('date', '20250101') for r in rows if r.get('date')]))
        for date_key in date_keys:
            for prefix in [
                "detailed_assembly_schedule_info_",
                "detailed_assembly_bayselect_info_",
                "detailed_assembly_decoding_schedule_processes_",
            ]:
                filename = f"{prefix}{date_key}.csv"
                candidates = [filename]
                if result_folder:
                    candidates.append(os.path.join(result_folder, filename))
                for candidate in candidates:
                    if os.path.exists(candidate):
                        try:
                            os.remove(candidate)
                        except Exception:
                            pass

        forced_sequence = [
            r.get("block_id")
            for r in sorted(rows, key=lambda r: r.get("am_sequence", 0))
            if r.get("block_id") is not None
        ]
        if forced_sequence:
            run_rl_assembly_decoding_sequence_with_blocks(
                blocks=blocks,
                metadata=metadata,
                rl_agent=model,
                device=device,
                start_date=start_date,
                max_days=int(_get_setting(settings, "assembly_max_days", 20)),
                date_offset=int(_get_setting(settings, "assembly_date_offset", 0)),
                output_csv=os.path.join(result_folder, "_rl_detail_rebuild.csv")
                if result_folder
                else "_rl_detail_rebuild.csv",
                save_csv=False,
                save_detailed=True,
                training_mode=False,
                forced_sequence=forced_sequence,
            )
        print("    대표해 기반 Detailed CSV 생성 완료")

    def _consume_eval_result(result: Dict) -> None:
        nonlocal best_results, best_statistics, best_episode_data, best_env, best_makespan
        nonlocal best_pm_primary, best_pm_makespan, best_pm_raw
        nonlocal best_dom_results, best_dom_statistics, best_dom_episode_data, best_dom_env
        nonlocal best_dom_primary, best_dom_makespan, best_dom_raw
        nonlocal best_pm_results, best_pm_statistics, best_pm_episode_data, best_pm_env
        nonlocal best_m_sample_id, best_pm_sample_id, best_dom_sample_id

        if not result.get("ok", True):
            raise RuntimeError(
                f"RL eval 후보 실패: sample={result.get('sample_id')} "
                f"profile={result.get('bias_label')}/{result.get('hard_label')}\n{result.get('error')}"
            )

        sample_id = int(result.get("sample_id") or 0)
        profile_sample_id = int(result.get("profile_sample_id") or 0)
        bias_label = str(result.get("bias_label", "current"))
        hard_label = str(result.get("hard_label", "current"))
        schedule_results = result.get("schedule_results") or []
        stats = result.get("stats") or {}
        episode_data = result.get("episode_data") or []
        env = result.get("env")
        makespan = stats.get('makespan_hours', float('inf')) if stats else float('inf')
        primary = int(stats.get('total_violations_primary', stats.get('total_violations', 0))) if stats else 0
        raw = int(stats.get('total_violations_raw', 0)) if stats else 0
        workshop_inv = int(stats.get('audit_workshop_order_inversions', 0)) if stats else 0
        dominates_lpt = False
        if has_lpt_reference:
            dominates_lpt = (
                makespan <= float(lpt_ref_makespan)
                and primary <= int(lpt_ref_primary)
                and (makespan < float(lpt_ref_makespan) or primary < int(lpt_ref_primary))
            )
        sample_record = {
            "sample_id": sample_id,
            "bias_profile": bias_label,
            "hard_profile": hard_label,
            "profile_sample_id": profile_sample_id,
            "samples_per_profile": int(num_samples),
            "makespan_hours": round(float(makespan), 6),
            "primary": int(primary),
            "raw": int(raw),
            "workshop_inv": int(workshop_inv),
            "lpt_reference_available": bool(has_lpt_reference),
            "lpt_makespan": round(float(lpt_ref_makespan), 6) if has_lpt_reference else None,
            "lpt_primary": int(lpt_ref_primary) if has_lpt_reference else None,
            "lpt_raw": int(lpt_ref_raw) if lpt_ref_raw is not None else None,
            "dominates_lpt": bool(dominates_lpt),
            "strictly_better_than_lpt": bool(dominates_lpt),
            "seconds": round(float(result.get("seconds", 0.0) or 0.0), 6),
        }
        sample_records.append(sample_record)
        if verbose_samples or sample_id <= 3 or sample_id % progress_interval == 0 or sample_id == total_samples:
            print(
                f"   샘플 {sample_id}/{total_samples}: makespan={makespan:.2f}h, "
                f"primary={primary}, raw={raw}, profile={bias_label}/{hard_label}, "
                f"profile_sample={profile_sample_id}/{num_samples}"
            )

        if makespan < best_makespan:
            best_makespan = makespan
            best_results = schedule_results
            best_statistics = stats
            best_episode_data = episode_data
            best_env = env
            best_m_sample_id = sample_id
            print(f"   🏆 새로운 최고 makespan! {best_makespan:.2f}h")

        # [AGENT-ADD] 제약 우선 best(p,m) 로그는 makespan best와 별도로 관리한다.
        if (primary, makespan, raw) < (best_pm_primary, best_pm_makespan, best_pm_raw):
            best_pm_primary = primary
            best_pm_makespan = makespan
            best_pm_raw = raw
            best_pm_results = schedule_results
            best_pm_statistics = stats
            best_pm_episode_data = episode_data
            best_pm_env = env
            best_pm_sample_id = sample_id
            print(
                "   🏆 새로운 최고 best(p,m)! "
                f"primary={best_pm_primary}, makespan={best_pm_makespan:.2f}h, raw={best_pm_raw}"
            )

        if dominates_lpt and (primary, makespan, raw) < (best_dom_primary, best_dom_makespan, best_dom_raw):
            best_dom_primary = primary
            best_dom_makespan = makespan
            best_dom_raw = raw
            best_dom_results = schedule_results
            best_dom_statistics = stats
            best_dom_episode_data = episode_data
            best_dom_env = env
            best_dom_sample_id = sample_id
            print(
                "   🏆 새로운 최고 dominance-best! "
                f"primary={best_dom_primary}, makespan={best_dom_makespan:.2f}h, raw={best_dom_raw}"
            )

    parallel_plan = _resolve_eval_parallel_plan(total_samples, device, settings)
    if parallel_plan.get("enabled"):
        ram_text = f"{parallel_plan.get('ram_gb'):.1f}GiB" if parallel_plan.get("ram_gb") is not None else "unknown"
        gpu_text = f"{parallel_plan.get('gpu_gb'):.1f}GiB" if parallel_plan.get("gpu_gb") is not None else "n/a"
        print(
            "[Eval RL 병렬] "
            f"workers={parallel_plan['workers']}, device={parallel_plan['worker_device']}, "
            f"worker_threads={parallel_plan['worker_threads']}, cpu={parallel_plan['cpu_count']}, "
            f"cpu_limit={parallel_plan.get('cpu_limit')}, ram={ram_text}, "
            f"ram_limit={parallel_plan.get('ram_limit')}, gpu_free={gpu_text}, "
            f"gpu_limit={parallel_plan.get('gpu_limit')}, tasks={total_samples}"
        )
        actor_state_cpu = {key: value.detach().cpu() for key, value in actor_state.items()}
        ctx = mp.get_context("spawn")
        with ProcessPoolExecutor(
            max_workers=int(parallel_plan["workers"]),
            mp_context=ctx,
            initializer=_init_eval_rl_worker,
            initargs=(
                copy.deepcopy(actor_params),
                actor_state_cpu,
                str(parallel_plan["worker_device"]),
                blocks,
                metadata,
                start_date,
                copy.deepcopy(get_runtime_config() or {}),
                int(_get_setting(settings, "assembly_max_days", 20)),
                int(_get_setting(settings, "assembly_date_offset", 0)),
                int(parallel_plan["worker_threads"]),
            ),
        ) as executor:
            future_to_task = {
                executor.submit(_run_parallel_eval_rl_task, task): task
                for task in eval_tasks
            }
            completed = 0
            for future in as_completed(future_to_task):
                completed += 1
                _consume_eval_result(future.result())
                if completed % progress_interval == 0 or completed == total_samples:
                    print(f"   [Eval RL 병렬] progress {completed}/{total_samples}")
    else:
        if total_samples >= 4:
            print(f"[Eval RL 병렬] disabled: {parallel_plan.get('reason')}")
        for task in eval_tasks:
            bias_profile = task.get("bias_profile")
            hard_profile = task.get("hard_profile")
            _seed_eval_task(int(task.get("seed", 0) or 0), device)
            schedule_results, stats, episode_data, env = _run_with_profile(
                bias_profile,
                hard_profile,
                lambda: run_rl_assembly_decoding_sequence_with_blocks(
                    blocks=blocks,
                    metadata=metadata,
                    rl_agent=model,
                    device=device,
                    start_date=start_date,
                    max_days=int(_get_setting(settings, "assembly_max_days", 20)),
                    date_offset=int(_get_setting(settings, "assembly_date_offset", 0)),
                    output_csv=(
                        os.path.join(result_folder, "rl_best_results.csv")
                        if result_folder
                        else "rl_best_results.csv"
                    ),
                    save_csv=False,
                    save_detailed=False,
                    training_mode=False,
                    collect_episode_data=False,
                    enable_grad=False,
                    collect_step_metrics=False,
                ),
            )
            result = {
                "ok": True,
                "sample_id": task.get("sample_id"),
                "profile_sample_id": task.get("profile_sample_id"),
                "bias_label": task.get("bias_label"),
                "hard_label": task.get("hard_label"),
                "schedule_results": schedule_results,
                "stats": stats or {},
                "episode_data": episode_data or [],
                "env": env,
                "seconds": 0.0,
            }
            _consume_eval_result(result)

    # [AGENT-ADD] 샘플 요약 CSV 저장 및 대표해 flag 확정.
    if result_folder and sample_records:
        for record in sample_records:
            sample_id = int(record["sample_id"])
            record["is_best_m"] = bool(best_m_sample_id is not None and sample_id == best_m_sample_id)
            record["is_best_pm"] = bool(best_pm_sample_id is not None and sample_id == best_pm_sample_id)
            record["is_best_dom"] = bool(best_dom_sample_id is not None and sample_id == best_dom_sample_id)
        samples_df = pd.DataFrame(sample_records)
        samples_csv = os.path.join(result_folder, "rl_samples_summary.csv")
        samples_df.to_csv(samples_csv, index=False, encoding='utf-8-sig')

    selected_results = best_results
    selected_statistics = best_statistics
    selected_episode_data = best_episode_data
    selected_env = best_env
    selected_kind = "best_m"
    if selection_mode == "best_dom_vs_lpt":
        if best_dom_results is not None:
            selected_results = best_dom_results
            selected_statistics = best_dom_statistics
            selected_episode_data = best_dom_episode_data
            selected_env = best_dom_env
            selected_kind = "best_dom"
        elif best_pm_results is not None:
            selected_results = best_pm_results
            selected_statistics = best_pm_statistics
            selected_episode_data = best_pm_episode_data
            selected_env = best_pm_env
            selected_kind = "best_pm_fallback"
    elif selection_mode == "best_pm" and best_pm_results is not None:
        selected_results = best_pm_results
        selected_statistics = best_pm_statistics
        selected_episode_data = best_pm_episode_data
        selected_env = best_pm_env
        selected_kind = "best_pm"

    # [AGENT-ADD] 대표해 3종과 최종 선택본을 모두 저장한다.
    selected_specific_csv_name = "rl_best_m_results.csv"
    if selected_kind == "best_dom":
        selected_specific_csv_name = "rl_best_dom_results.csv"
    elif selected_kind == "best_pm" or selected_kind == "best_pm_fallback":
        selected_specific_csv_name = "rl_best_pm_results.csv"
    selected_csv_name = "rl_best_results.csv"
    dom_proxy_results = best_dom_results or best_pm_results or best_results
    try:
        _save_schedule_rows("rl_best_m_results.csv", best_results)
        _save_schedule_rows("rl_best_pm_results.csv", best_pm_results)
        _save_schedule_rows("rl_best_dom_results.csv", dom_proxy_results)
        _save_schedule_rows(selected_csv_name, selected_results)
    except Exception as e:
        print(f"   ⚠️ RL 대표 결과 CSV 저장 실패: {e}")

    if isinstance(selected_statistics, dict):
        selected_statistics["profile_sampling"] = profile_enabled
        selected_statistics["profile_count"] = profile_count
        selected_statistics["samples_per_profile"] = int(num_samples)
        selected_statistics["profile_samples_evaluated"] = total_samples
        selected_statistics["selected_result_csv_name"] = selected_specific_csv_name
        selected_statistics["selected_alias_csv_name"] = selected_csv_name
        selected_statistics["selected_result_kind"] = selected_kind
        selected_statistics["best_m_result_csv_name"] = "rl_best_m_results.csv"
        selected_statistics["best_pm_result_csv_name"] = "rl_best_pm_results.csv"
        selected_statistics["best_dom_result_csv_name"] = "rl_best_dom_results.csv"
        selected_statistics["samples_summary_csv_name"] = "rl_samples_summary.csv"
        selected_statistics["best_dom_available"] = bool(best_dom_results is not None)
        selected_statistics["best_pm_sample_id"] = best_pm_sample_id
        selected_statistics["best_m_sample_id"] = best_m_sample_id
        selected_statistics["best_dom_sample_id"] = best_dom_sample_id

    _rebuild_detailed_for_sequence(selected_results)

    return selected_results or [], selected_statistics or {}, selected_episode_data or [], selected_env


__all__ = [
    "run_excel_heuristic",
    "run_actionmasking_heuristic",
    "run_spt_heuristic",
    "run_lpt_heuristic",
    "run_seam_min_heuristic",
    "run_ga_metaheuristic",
    "run_cp_sat_baseline",
    "run_rl_evaluation",
]
