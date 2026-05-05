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
from concurrent.futures import ProcessPoolExecutor, as_completed
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import torch

from runtime_config import get_runtime_config, set_runtime_config
from enhanced_environment.constraints import ConstraintConfig
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
    "run_rl_evaluation",
]
