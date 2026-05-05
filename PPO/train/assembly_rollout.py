#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Assembly PPO with Rollout Baseline
===================================

agent/ppo_rollout_self.py 방식을 Assembly RL에 적용
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
import numpy as np
from scipy import stats as scipy_stats
from concurrent.futures import ProcessPoolExecutor, as_completed
import multiprocessing as mp
import contextlib
import os
import random
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, List, Dict, Tuple, Optional
import copy

import sys

# [AGENT-EDIT] Direct script execution needs the repository root for absolute
# package imports such as `PPO.models...`, `utils...`, and `scheduling...`.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PPO.models.single_step_actor import SingleStepPtrNet
from utils.optimized_block_generator import OptimizedBlockGenerator
from enhanced_environment.common.utils_core import DataConverter
from scheduling.assembly_start.rl_assembly_scheduler import run_rl_assembly_decoding_sequence_with_blocks
# [AGENT-EDIT] Heuristic baseline support (LPT / SEAM_MIN)
from scheduling.assembly_start.action_sequence_조립착수일기준휴리스틱 import run_assembly_decoding_sequence_with_blocks
# [AGENT-EDIT] 분리된 rollout metric 유틸 사용
from PPO.train.rollout_metrics import (
    calculate_entropy,
    get_violation_count,
    compute_longi_balance_details,
    compute_longi_balance_ratio,
    compute_score,
)
from runtime_config import get_runtime_config, set_runtime_config  # [AGENT-ADD] self-label teacher/profile runtime override

# Ranger-AdaBelief optimizer (선택적)
try:
    from ranger_adabelief import RangerAdaBelief
    RANGER_AVAILABLE = True
except ImportError:
    RANGER_AVAILABLE = False
    print("⚠️ RangerAdaBelief not available, using Adam instead")


_SELF_LABEL_WORKER_ACTOR = None
_SELF_LABEL_WORKER_DEVICE = None
_SELF_LABEL_WORKER_BLOCKS = None
_SELF_LABEL_WORKER_METADATA = None
_SELF_LABEL_WORKER_START_DATE = None

# [AGENT-ADD] Persistent pool state — avoids respawning 44 processes + GPU model reload every episode.
_PERSISTENT_POOL: Optional['ProcessPoolExecutor'] = None
_PERSISTENT_POOL_WORKERS: int = 0
_PERSISTENT_POOL_DEVICE: str = ""


def _get_total_memory_gb() -> Optional[float]:
    """[AGENT-ADD] Return total system RAM in GiB without requiring psutil."""
    try:
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return float(pages * page_size) / (1024 ** 3)
    except Exception:
        return None


def _seed_worker_task(seed: int, device: torch.device) -> None:
    """[AGENT-ADD] Seed one self-label worker task."""
    seed = int(seed or 0) % (2 ** 31 - 1)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if device.type == "cuda" and torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


@contextlib.contextmanager
def _worker_output_context():
    """[AGENT-ADD] Suppress noisy worker logs unless explicitly requested."""
    keep_logs = os.environ.get("PBS_SELF_LABEL_WORKER_LOG", "").strip().lower() in {"1", "true", "on", "yes"}
    force_debug = os.environ.get("PBS_FORCE_DEBUG", "").strip().lower() in {"1", "true", "on", "yes"}
    verbose_debug = os.environ.get("PBS_DEBUG_VERBOSE", "").strip().lower() in {"1", "true", "on", "yes"}
    if keep_logs or force_debug or verbose_debug:
        yield
        return
    with open(os.devnull, "w") as devnull:
        with contextlib.redirect_stdout(devnull), contextlib.redirect_stderr(devnull):
            yield


def _init_self_label_worker(
    actor_params: Dict[str, Any],
    actor_state_dict: Dict[str, torch.Tensor],
    device_str: str,
    blocks: List,
    metadata: Dict,
    start_date: str,
    base_runtime_cfg: Dict,
    worker_torch_threads: int,
) -> None:
    """[AGENT-ADD] Initialize one process for parallel self-label candidate generation."""
    global _SELF_LABEL_WORKER_ACTOR
    global _SELF_LABEL_WORKER_DEVICE
    global _SELF_LABEL_WORKER_BLOCKS
    global _SELF_LABEL_WORKER_METADATA
    global _SELF_LABEL_WORKER_START_DATE

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
    actor.load_state_dict(actor_state_dict, strict=True)
    actor.default_decode_type = "sampling"
    actor.eval()

    _SELF_LABEL_WORKER_ACTOR = actor
    _SELF_LABEL_WORKER_DEVICE = requested_device
    _SELF_LABEL_WORKER_BLOCKS = blocks
    _SELF_LABEL_WORKER_METADATA = metadata
    _SELF_LABEL_WORKER_START_DATE = start_date


def _pool_sync_task(
    actor_state_dict: Dict[str, Any],
    blocks: List,
    metadata: Dict,
    start_date: str,
    base_runtime_cfg: Dict,
) -> bool:
    """[AGENT-ADD] Update worker globals between episodes without respawning processes.

    Called once per worker per episode via the persistent pool, so spawn cost is paid
    only on the very first episode (or when the pool configuration changes).
    """
    global _SELF_LABEL_WORKER_ACTOR, _SELF_LABEL_WORKER_BLOCKS
    global _SELF_LABEL_WORKER_METADATA, _SELF_LABEL_WORKER_START_DATE
    try:
        if _SELF_LABEL_WORKER_ACTOR is not None:
            _SELF_LABEL_WORKER_ACTOR.load_state_dict(actor_state_dict, strict=True)
        _SELF_LABEL_WORKER_BLOCKS = blocks
        _SELF_LABEL_WORKER_METADATA = metadata
        _SELF_LABEL_WORKER_START_DATE = start_date
        set_runtime_config(copy.deepcopy(base_runtime_cfg or {}))
        return True
    except Exception:
        return False


def _shutdown_persistent_pool() -> None:
    """[AGENT-ADD] Gracefully shut down the persistent worker pool."""
    global _PERSISTENT_POOL, _PERSISTENT_POOL_WORKERS, _PERSISTENT_POOL_DEVICE
    pool = _PERSISTENT_POOL
    _PERSISTENT_POOL = None
    _PERSISTENT_POOL_WORKERS = 0
    _PERSISTENT_POOL_DEVICE = ""
    if pool is not None:
        try:
            pool.shutdown(wait=False, cancel_futures=True)
        except Exception:
            try:
                pool.shutdown(wait=False)
            except Exception:
                pass


def _run_parallel_self_label_task(task: Dict[str, Any]) -> Dict[str, Any]:
    """[AGENT-ADD] Worker entrypoint for one self-label candidate."""
    global _SELF_LABEL_WORKER_ACTOR
    global _SELF_LABEL_WORKER_DEVICE
    global _SELF_LABEL_WORKER_BLOCKS
    global _SELF_LABEL_WORKER_METADATA
    global _SELF_LABEL_WORKER_START_DATE

    events: List[Dict[str, Any]] = []
    task_t0 = time.perf_counter()
    tag = task.get("tag", "candidate")
    try:
        if _SELF_LABEL_WORKER_ACTOR is None or _SELF_LABEL_WORKER_DEVICE is None:
            raise RuntimeError("self-label worker actor is not initialized")

        _seed_worker_task(int(task.get("seed", 0)), _SELF_LABEL_WORKER_DEVICE)
        set_runtime_config(copy.deepcopy(task.get("runtime_cfg") or {}))
        # [AGENT-ADD] 후보 간 상태 오염을 막기 위해 task 단위로 블록/메타데이터를 분리한다.
        blocks = copy.deepcopy(_SELF_LABEL_WORKER_BLOCKS)
        metadata = copy.deepcopy(_SELF_LABEL_WORKER_METADATA)
        start_date = str(task.get("start_date") or _SELF_LABEL_WORKER_START_DATE)

        if task.get("kind") == "rl":
            with _worker_output_context():
                schedule_results, stats, episode_data, _ = run_rl_assembly_decoding_sequence_with_blocks(
                    blocks=blocks,
                    metadata=metadata,
                    rl_agent=_SELF_LABEL_WORKER_ACTOR,
                    device=_SELF_LABEL_WORKER_DEVICE,
                    max_days=20,
                    start_date=start_date,
                    training_mode=True,
                    save_csv=False,
                    save_detailed=False,
                    forced_sequence=None,
                    collect_episode_data=True,
                    enable_grad=False,
                    collect_step_metrics=False,
                )
            events.append({
                "kind": "rl_schedule",
                "tag": tag,
                "seconds": time.perf_counter() - task_t0,
                "steps": len(episode_data or []),
                "makespan": (stats or {}).get("makespan_hours"),
                "violations": get_violation_count(stats or {}),
            })
            return {
                "ok": True,
                "record": {
                    "schedule": schedule_results,
                    "stats": stats or {},
                    "episode_data": episode_data or [],
                    "env": None,
                    "tag": tag,
                },
                "perf_events": events,
            }

        if task.get("kind") == "heuristic":
            method = str(task.get("method") or "lpt")
            heuristic_t0 = time.perf_counter()
            with _worker_output_context():
                heuristic_results, heuristic_stats = run_assembly_decoding_sequence_with_blocks(
                    blocks=blocks,
                    metadata=metadata,
                    decoding_type="assembly",
                    selection_method=method,
                    max_days=20,
                    start_date=start_date,
                    date_offset=0,
                    output_csv="",
                    save_csv=False,
                    save_detailed=False,
                    forced_sequence=None,
                    expand_rows=False,
                )
            events.append({
                "kind": "heuristic_sequence",
                "tag": tag,
                "seconds": time.perf_counter() - heuristic_t0,
                "steps": len(heuristic_results or []),
                "makespan": (heuristic_stats or {}).get("makespan_hours"),
                "violations": get_violation_count(heuristic_stats or {}),
            })
            forced_sequence = [
                r.get("block_id") for r in (heuristic_results or [])
                if r.get("block_id") is not None
            ]
            if not forced_sequence:
                return {
                    "ok": True,
                    "record": None,
                    "heuristic_results": heuristic_results,
                    "heuristic_stats": heuristic_stats or {},
                    "perf_events": events,
                }

            forced_t0 = time.perf_counter()
            with _worker_output_context():
                schedule_results, stats, episode_data, _ = run_rl_assembly_decoding_sequence_with_blocks(
                    blocks=copy.deepcopy(_SELF_LABEL_WORKER_BLOCKS),
                    metadata=copy.deepcopy(_SELF_LABEL_WORKER_METADATA),
                    rl_agent=_SELF_LABEL_WORKER_ACTOR,
                    device=_SELF_LABEL_WORKER_DEVICE,
                    max_days=20,
                    start_date=start_date,
                    training_mode=True,
                    save_csv=False,
                    save_detailed=False,
                    forced_sequence=forced_sequence,
                    collect_episode_data=True,
                    enable_grad=False,
                    collect_step_metrics=False,
                )
            events.append({
                "kind": "forced_schedule",
                "tag": tag,
                "seconds": time.perf_counter() - forced_t0,
                "steps": len(episode_data or []),
                "makespan": (stats or {}).get("makespan_hours"),
                "violations": get_violation_count(stats or {}),
            })
            return {
                "ok": True,
                "record": {
                    "schedule": schedule_results,
                    "stats": stats or {},
                    "episode_data": episode_data or [],
                    "env": None,
                    "tag": tag,
                },
                "heuristic_results": heuristic_results,
                "heuristic_stats": heuristic_stats or {},
                "perf_events": events,
            }

        raise ValueError(f"unknown self-label task kind: {task.get('kind')}")
    except Exception:
        return {
            "ok": False,
            "tag": tag,
            "kind": task.get("kind"),
            "error": traceback.format_exc(),
            "perf_events": events,
            "seconds": time.perf_counter() - task_t0,
        }


class AssemblyPPORollout:
    """PPO with Rollout Baseline for Assembly Scheduling + Self-Label mode switch"""
    
    def __init__(self, 
                 actor_params: dict,
                 device: torch.device,
                 lr: float = 1e-4,
                 epsilon: float = 0.2,  # PPO clipping
                 ppo_iterations: int = 2,
                 baseline_update_interval: int = 10,
                 statistical_alpha: float = 0.05,
                 grad_clip: float = 1.0,
                 use_env_state: bool = False,  # 🆕 환경 상태 사용 여부
                 fixed_norm_stats: bool = False,  # [AGENT-EDIT] 고정 통계 정규화 기본 비활성화
                 fixed_norm_warmup_episodes: int = 5,  # [AGENT-ADD] 고정 통계 워밍업 에피소드
                 start_date_change_interval: int = 1,  # 🆕 기준일 변경 주기
                 start_date_offset_days: int = 3,
                 # 🔥 엔트로피 파라미터 추가
                 entropy_coeff: float = 0.01,
                 entropy_decay: float = 0.995,
                 min_entropy_coeff: float = 0.001,
                 entropy_decay_interval: int = 100,
                 initial_entropy_coeff: float = 0.03,
                 entropy_warmup_episodes: int = 50,
                 baseline_improvement_margin: float = 1.0,
                 baseline_eval_samples: int = 5,
                 top_k_ratio_start: float = 1.0,
                 top_k_ratio_end: float = 0.25,
                 top_k_ratio_decay: float = 0.95,
                 top_k_warmup_episodes: int = 50,
                 top_k_decay_interval: int = 150,
                 # 🔥 옵티마이저 선택 추가
                 optimizer_type: str = 'AdamW',
                 enable_optimizer: bool = True,  # [AGENT-ADD] 평가용이면 False
                 use_block_generator: bool = True,  # [AGENT-ADD] 평가용이면 False
                 block_generator: Optional[OptimizedBlockGenerator] = None,  # [AGENT-ADD] 외부 주입 가능
                 mode: str = "ppo",              # 🆕 "ppo" 또는 "self_label"
                 self_label_samples: int = 4,    # [AGENT-EDIT] SLIM/RL 후보 기본 샘플 수
                 self_label_mode: int = 1,       # 🆕 legacy score tie-breaker mode
                 violation_penalty_weight: float = 1.0,  # 🆕 위반 1건당 가산(시간) 가중치
                 longi_balance_weight: float = 0.1,  # [AGENT-ADD] 론지 불균형 가중치 (비율형)
                 self_label_selection_strategy: str = "primary_first",  # [AGENT-EDIT] 기본 teacher 선택은 제약 우선 사전식
                 self_label_lpt_teacher_policy: str = "candidate_only",  # [AGENT-EDIT] LPT는 후보군에서만 경쟁
                 self_label_lpt_warmup_episodes: int = 0,  # [AGENT-ADD] warmup 동안만 LPT teacher 허용
                 self_label_lpt_teacher_bias_on: bool = False,  # [AGENT-ADD] teacher LPT만 bias on override
                 self_label_profile_expansion: bool = True,  # [AGENT-ADD] RL 후보: 8 bias x 8 hard profiles
                 self_label_heuristic_profile_expansion: bool = True,  # [AGENT-ADD] LPT/SEAM_MIN 후보: 8 bias profiles
                 self_label_print_best_updates: bool = True,  # [AGENT-ADD] 후보군 best 갱신 로그
                 perf_timing: bool = False,  # [AGENT-ADD] 후보/업데이트 구간별 wall-clock timing
                 perf_top_k: int = 8,  # [AGENT-ADD] timing 로그에서 느린 후보 표시 개수
                 self_label_parallel: str = "auto",  # [AGENT-ADD] auto/on/off parallel candidate generation
                 self_label_parallel_workers: int = 0,  # [AGENT-ADD] 0이면 CPU/GPU/RAM 기준 자동
                 self_label_parallel_device: str = "auto",  # [AGENT-ADD] auto/cuda/cpu
                 self_label_worker_threads: int = 0,  # [AGENT-ADD] worker별 torch thread 수
                 self_label_parallel_min_candidates: int = 8,  # [AGENT-ADD] 이 수 이상일 때 병렬화
                 resume_metadata: Optional[Dict[str, Any]] = None):
        """
        Args:
            actor_params: Actor 네트워크 파라미터
            device: 연산 디바이스
            lr: 학습률
            epsilon: PPO clipping epsilon
            ppo_iterations: PPO 업데이트 반복 횟수
            baseline_update_interval: Baseline 업데이트 체크 주기
            statistical_alpha: 통계적 유의수준 (p-value)
            grad_clip: 그래디언트 클리핑
            use_env_state: 🆕 환경 상태 벡터 사용 여부
            start_date_change_interval: 기준일 변경 주기 (legacy)
            start_date_offset_days: 최소 착수일에서 앞당길 일수
        """
        self.device = device
        self.lr = lr
        self.epsilon = epsilon
        self.ppo_iterations = ppo_iterations
        self.baseline_update_interval = baseline_update_interval
        self.statistical_alpha = statistical_alpha
        self.grad_clip = grad_clip
        self.use_env_state = use_env_state  # 🆕
        # [AGENT-ADD] 고정 통계 정규화 설정
        self.fixed_norm_stats = fixed_norm_stats
        self.fixed_norm_warmup_episodes = max(0, fixed_norm_warmup_episodes)
        self._fixed_norm_frozen = False
        
        # 🔥 엔트로피 설정
        self.entropy_coeff = entropy_coeff  # 목표 엔트로피 계수
        self.entropy_decay = entropy_decay
        self.min_entropy_coeff = min_entropy_coeff
        self.entropy_decay_interval = entropy_decay_interval
        self.initial_entropy_coeff = initial_entropy_coeff
        self.entropy_warmup_episodes = entropy_warmup_episodes
        self.current_entropy_coeff = initial_entropy_coeff  # 현재 엔트로피 계수 (워밍업 시작값)
        self.baseline_improvement_margin = baseline_improvement_margin
        self.baseline_eval_samples = baseline_eval_samples
        self.top_k_ratio_start = top_k_ratio_start
        self.top_k_ratio_end = top_k_ratio_end
        self.top_k_ratio_decay = top_k_ratio_decay
        self.top_k_warmup_episodes = top_k_warmup_episodes
        self.top_k_decay_interval = top_k_decay_interval
        self.current_top_k_ratio = top_k_ratio_start

        # 🆕 기준일 관리
        self.start_date_change_interval = start_date_change_interval
        self.start_date_offset_days = max(0, start_date_offset_days)
        self.current_start_date: Optional[str] = None
        self.start_date_episode_count = 0
        self.mode = mode.lower().strip()
        self.self_label_samples = max(1, self_label_samples)
        self.self_label_mode = 1 if self_label_mode != 2 else 2
        # [AGENT-EDIT] 현재 학습 기본값은 primary violation -> makespan -> longi balance 사전식 선택이다.
        strategy = str(self_label_selection_strategy or "primary_first").strip().lower()
        strategy_alias = {
            "legacy": "legacy_score",
            "legacy_score": "legacy_score",
            "score": "legacy_score",
            "primary_first": "primary_first",
            "violation_first": "primary_first",
            "feasible_first": "feasible_first",
            "makespan_first": "makespan_first",
        }
        self.self_label_selection_strategy = strategy_alias.get(strategy, "primary_first")
        teacher_policy = str(self_label_lpt_teacher_policy or "candidate_only").strip().lower()
        teacher_policy_alias = {
            "candidate": "candidate_only",
            "candidate_only": "candidate_only",
            "candidate_pool": "candidate_only",
            "always": "legacy",
            "legacy": "legacy",
            "warmup_only": "warmup_only",
            "disabled": "disabled",
            "off": "disabled",
        }
        self.self_label_lpt_teacher_policy = teacher_policy_alias.get(teacher_policy, "candidate_only")
        self.self_label_lpt_warmup_episodes = max(0, int(self_label_lpt_warmup_episodes or 0))
        self.self_label_lpt_teacher_bias_on = bool(self_label_lpt_teacher_bias_on)
        # [AGENT-ADD] Profile-expanded self-label generation.
        # RL/Self-label: 8 bias profiles x 8 hard profiles x self_label_samples.
        # Heuristics: LPT/SEAM_MIN x 8 bias profiles. Capacity hard constraints are never disabled here.
        self.self_label_profile_expansion = bool(self_label_profile_expansion)
        self.self_label_heuristic_profile_expansion = bool(self_label_heuristic_profile_expansion)
        self.self_label_print_best_updates = bool(self_label_print_best_updates)
        self.self_label_last_candidate_count = 0
        # [AGENT-ADD] Performance diagnostics are opt-in so default training output/behavior stays unchanged.
        self.perf_timing = bool(perf_timing)
        self.perf_top_k = max(1, int(perf_top_k or 8))
        self._active_perf_events: List[Dict[str, Any]] = []
        # [AGENT-ADD] Parallel self-label resource controls.
        parallel_mode = str(self_label_parallel or "auto").strip().lower()
        self.self_label_parallel = parallel_mode if parallel_mode in {"auto", "on", "off"} else "auto"
        self.self_label_parallel_workers = max(0, int(self_label_parallel_workers or 0))
        parallel_device = str(self_label_parallel_device or "auto").strip().lower()
        self.self_label_parallel_device = parallel_device if parallel_device in {"auto", "cuda", "cpu"} else "auto"
        self.self_label_worker_threads = max(0, int(self_label_worker_threads or 0))
        self.self_label_parallel_min_candidates = max(1, int(self_label_parallel_min_candidates or 8))
        # [AGENT-EDIT] makespan + 위반 페널티 통합 스코어 가중치
        self.violation_penalty_weight = max(0.0, float(violation_penalty_weight))
        # [AGENT-ADD] 론지 불균형 가중치 (비율형)
        self.longi_balance_weight = max(0.0, float(longi_balance_weight))
        # [AGENT-EDIT] value loss 제거
        
        print(f"📅 PPO 기준일 관리 설정:")
        print(f"   - 기준일 오프셋: {self.start_date_offset_days}일")
        if self.mode == "self_label":
            print("🧪 Self-label teacher 설정:")
            print(f"   - 선택 기준: {self.self_label_selection_strategy}")
            print(f"   - SLIM/RL 후보 샘플 수: {self.self_label_samples}")
            print(f"   - LPT teacher 정책: {self.self_label_lpt_teacher_policy}")
            print(f"   - LPT warmup 에피소드: {self.self_label_lpt_warmup_episodes}")
            print(f"   - LPT teacher bias on override: {self.self_label_lpt_teacher_bias_on}")
            print(f"   - profile expansion: RL={self.self_label_profile_expansion}, heuristic={self.self_label_heuristic_profile_expansion}")
            print(f"   - best 갱신 로그: {self.self_label_print_best_updates}")
            print(
                "   - parallel candidates: "
                f"mode={self.self_label_parallel}, workers={self.self_label_parallel_workers or 'auto'}, "
                f"device={self.self_label_parallel_device}, worker_threads={self.self_label_worker_threads or 'auto'}"
            )
        
        # 🆕 Actor 파라미터에 use_env_state 추가
        # [AGENT-EDIT] actor_params가 모델 인스턴스로 들어올 수 있어 안전 처리
        if isinstance(actor_params, SingleStepPtrNet):
            self.actor = actor_params.to(device)
            self.actor_params_for_resume = {}
        else:
            actor_params_with_state = actor_params.copy()
            actor_params_with_state['use_env_state'] = use_env_state
            self.actor_params_for_resume = copy.deepcopy(actor_params_with_state)
            # Actor 모델 (학습용)
            self.actor = SingleStepPtrNet(**actor_params_with_state).to(device)
        self.resume_metadata = copy.deepcopy(resume_metadata or {})
        
        # [AGENT-EDIT] Baseline actor 제거: 휴리스틱 baseline 사용
        self.baseline_actor = None
        
        # 🔥 옵티마이저 선택 로직 (평가에서는 비활성화 가능)
        self.optimizer_type = optimizer_type
        self.optimizer = None
        if enable_optimizer:
            print(f"🔧 옵티마이저: {optimizer_type}")
            if optimizer_type == 'AdamW':
                self.optimizer = optim.AdamW(
                    self.actor.parameters(),
                    lr=lr,
                    betas=(0.9, 0.999),
                    eps=1e-8,
                    weight_decay=0.01
                )
            elif optimizer_type == 'Adam':
                self.optimizer = optim.Adam(
                    self.actor.parameters(),
                    lr=lr,
                    betas=(0.9, 0.999),
                    eps=1e-8,
                    weight_decay=0.01
                )
            elif optimizer_type == 'ranger_adabelief':
                if RANGER_AVAILABLE:
                    self.optimizer = RangerAdaBelief(
                        self.actor.parameters(),
                        lr=lr,
                        betas=(0.9, 0.999),
                        eps=1e-8,
                        weight_decay=0.01
                    )
                else:
                    print("⚠️ RangerAdaBelief 사용 불가, AdamW로 대체")
                    self.optimizer = optim.AdamW(
                        self.actor.parameters(),
                        lr=lr,
                        betas=(0.9, 0.999),
                        eps=1e-8,
                        weight_decay=0.01
                    )
            else:
                raise ValueError(f"지원하지 않는 옵티마이저: {optimizer_type}")
        
        # 통계
        self.episode_count = 0
        self.baseline_update_count = 0
        self.training_stats = []
        
        # 블록 생성기 (평가에서는 스킵 가능)
        if block_generator is not None:
            self.block_generator = block_generator
        elif use_block_generator:
            self.block_generator = OptimizedBlockGenerator.load_from_saved_values()
        else:
            self.block_generator = None
        self.actor.set_sampling_top_k_ratio(self.current_top_k_ratio)
    
    def _should_change_start_date(self) -> bool:
        """기준일 변경 여부 확인 (REINFORCE와 동일)"""
        self.start_date_episode_count += 1
        if self.start_date_episode_count >= self.start_date_change_interval:
            self.start_date_episode_count = 0
            return True
        return False
    
    def update_entropy_coeff(self):
        """🔥 엔트로피 계수 스케줄링 (점진적 감소)"""
        if self.episode_count <= self.entropy_warmup_episodes:
            self.current_entropy_coeff = self.initial_entropy_coeff
            return

        old_coeff = self.current_entropy_coeff
        if self.current_entropy_coeff > self.entropy_coeff:
            new_coeff = max(self.entropy_coeff, self.current_entropy_coeff * self.entropy_decay)
        else:
            new_coeff = max(self.min_entropy_coeff, self.current_entropy_coeff * self.entropy_decay)
        self.current_entropy_coeff = new_coeff

        if old_coeff != self.current_entropy_coeff:
            print(f"🔥 엔트로피 계수 업데이트: {old_coeff:.6f} → {self.current_entropy_coeff:.6f}")

    def update_sampling_top_k_ratio(self):
        """[AGENT-EDIT] 샘플링 시 상위 후보 비율을 점진적으로 축소"""
        if self.episode_count <= self.top_k_warmup_episodes:
            target_ratio = self.top_k_ratio_start
        else:
            steps_since = self.episode_count - self.top_k_warmup_episodes
            target_ratio = self.current_top_k_ratio
            if steps_since > 0 and steps_since % max(1, self.top_k_decay_interval) == 0:
                target_ratio = max(
                    self.top_k_ratio_end,
                    self.current_top_k_ratio * self.top_k_ratio_decay
                )
        if abs(target_ratio - self.current_top_k_ratio) > 1e-6:
            print(f"🎯 Top-k ratio 업데이트: {self.current_top_k_ratio:.3f} → {target_ratio:.3f}")
            self.current_top_k_ratio = target_ratio
        self.actor.set_sampling_top_k_ratio(self.current_top_k_ratio)
    
    def _evaluate_heuristic_baseline(self,
                                     blocks: List,
                                     metadata: Dict,
                                     start_date: str,
                                     method: str,
                                     trials: int = 10) -> Tuple[float, List[Dict], Dict]:
        best_makespan = float('inf')
        best_stats: Dict = {}
        best_schedule: List[Dict] = []
        best_violations = float('inf')
        best_score = float('inf')

        for _ in range(max(1, trials)):
            results, stats = run_assembly_decoding_sequence_with_blocks(
                blocks=blocks,
                metadata=metadata,
                decoding_type="assembly",
                selection_method=method,
                max_days=20,
                start_date=start_date,
                date_offset=0,
                output_csv="",
                save_csv=False,
                save_detailed=False,
                forced_sequence=None
            )
            makespan = stats.get('makespan_hours', float('inf'))
            violations = get_violation_count(stats)
            # [AGENT-EDIT] 통합 스코어 기준으로 best-of-K 선택
            longi_balance = compute_longi_balance_ratio(results)
            score = compute_score(
                makespan,
                violations,
                longi_balance,
                self.violation_penalty_weight,
                self.longi_balance_weight,
            )
            if (score, makespan, violations) < (best_score, best_makespan, best_violations):
                best_makespan = makespan
                best_stats = stats
                best_schedule = results
                best_violations = violations
                best_score = score

        return best_makespan, best_schedule, best_stats

    def get_rollout_baseline(self, 
                            blocks: List,
                            metadata: Dict,
                            start_date: str,
                            return_details: bool = False):
        """
        🔥 휴리스틱 Baseline(LPT)으로 성능 및 (옵션) 상세 정보를 반환
        """
        # [AGENT-EDIT] Heuristic baseline: LPT only (best-of-K)
        # [AGENT-EDIT] 단일 LPT baseline (best-of-K 제거)
        lpt_makespan, lpt_schedule, lpt_stats = self._evaluate_heuristic_baseline(
            blocks, metadata, start_date, method="lpt", trials=1
        )
        makespan = lpt_makespan
        schedule_results = lpt_schedule
        stats = lpt_stats
        baseline_method = "LPT"

        if return_details:
            return makespan, {
                'schedule_results': schedule_results,
                'statistics': stats,
                'episode_data': [],
                'env': None,
                'baseline_method': baseline_method,
                'baseline_trials': 1
            }

        return makespan
    
    def train_episode(self,
                      blocks: List,
                      metadata: Dict) -> Dict:
        """모드에 따라 PPO 또는 Self-Label 에피소드 학습"""
        if self.mode == "self_label":
            return self.train_episode_self_label(blocks, metadata)
        return self.train_episode_ppo(blocks, metadata)

    # ==== [AGENT-ADD] Fixed norm stats freeze ====
    def _maybe_freeze_norm_stats(self) -> None:
        if not self.fixed_norm_stats or self._fixed_norm_frozen:
            return
        if self.episode_count < self.fixed_norm_warmup_episodes:
            return
        self.actor.enable_fixed_norm_stats(from_running=True)
        # [AGENT-EDIT] baseline_actor 제거됨
        self._fixed_norm_frozen = True
        print(f"✅ 고정 통계 정규화 활성화 (warmup={self.fixed_norm_warmup_episodes} episodes)")

    def train_episode_ppo(self, 
                          blocks: List,
                          metadata: Dict) -> Dict:
        """
        PPO 방식으로 한 에피소드 학습 (기준일 관리 포함)
        """
        self.episode_count += 1
        
        # 🔥 엔트로피 계수 스케줄링 (워밍업 + 디케이)
        if self.episode_count <= self.entropy_warmup_episodes:
            self.current_entropy_coeff = self.initial_entropy_coeff
        elif self.episode_count == self.entropy_warmup_episodes + 1:
            self.current_entropy_coeff = self.entropy_coeff
        elif (self.episode_count - self.entropy_warmup_episodes) % self.entropy_decay_interval == 0:
            self.update_entropy_coeff()
        self.update_sampling_top_k_ratio()

        # 🆕 데이터 기반 기준일 계산
        start_date = self._compute_episode_start_date(blocks)
        if start_date != self.current_start_date:
            print(f"   📅 PPO 기준일 설정: {start_date}")
            self.current_start_date = start_date
        self.start_date_episode_count = 0
        
        # 1. 현재 정책으로 trajectory 생성
        import time
        self.actor.train()  # 🔥 학습용이므로 train 모드 유지
        
        t1 = time.time()
        print(f"[1] Actor 스케줄링 시작...")
        print(f"[DEBUG] train_episode_ppo: received {len(blocks)} blocks")
        schedule_results, stats, episode_data, actor_env = run_rl_assembly_decoding_sequence_with_blocks(
            blocks=blocks,
            metadata=metadata,
            rl_agent=self.actor,
            device=self.device,
            max_days=20,
            start_date=start_date,
            training_mode=True,
            save_csv=False,
            save_detailed=False,  # 🔥 학습 중에는 상세 분석 CSV 비활성화
            collect_episode_data=True,
            # [AGENT-ADD] PPO rollout의 old log prob는 float로 저장하고 업데이트 때 재계산하므로 grad graph가 필요 없다.
            enable_grad=False,
            collect_step_metrics=False,
        )
        
        real_makespan = stats.get('makespan_hours', float('inf'))
        actor_violations = get_violation_count(stats)
        print(f"[1] 완료: {time.time()-t1:.1f}초")
        
        # 2. Baseline rollout으로 baseline makespan 계산
        t2 = time.time()
        print(f"[2] Baseline 스케줄링 시작...")
        baseline_makespan, baseline_details = self.get_rollout_baseline(
            blocks,
            metadata,
            start_date,
            return_details=True
        )
        print(f"[2] 완료: {time.time()-t2:.1f}초")
        
        # [AGENT-EDIT] 통합 스코어 기반 Advantage (score = makespan + w * violations)
        baseline_stats = baseline_details.get('statistics', {}) if baseline_details else {}
        baseline_violations = get_violation_count(baseline_stats)
        # [AGENT-ADD] 론지 균형 상세 (비율 + 베이별 합계)
        actor_longi_balance, actor_longi_a, actor_longi_b, actor_longi_total = compute_longi_balance_details(schedule_results)
        baseline_schedule = baseline_details.get('schedule_results') if baseline_details else []
        baseline_longi_balance, baseline_longi_a, baseline_longi_b, baseline_longi_total = compute_longi_balance_details(baseline_schedule)
        actor_score = compute_score(
            real_makespan,
            actor_violations,
            actor_longi_balance,
            self.violation_penalty_weight,
            self.longi_balance_weight,
        )
        baseline_score = compute_score(
            baseline_makespan,
            baseline_violations,
            baseline_longi_balance,
            self.violation_penalty_weight,
            self.longi_balance_weight,
        )
        # [AGENT-EDIT] actor가 baseline보다 좋을수록(낮을수록) Advantage가 양수
        # [AGENT-EDIT] 음수도 그대로 사용하여 '나쁜 선택'에 패널티를 부여
        advantage = float(baseline_score - actor_score)
        
        # 4. PPO 업데이트
        t3 = time.time()
        print(f"[3] PPO 업데이트 시작...")
        # [AGENT-EDIT] 후보 2개 이상 스텝만 업데이트에 사용
        eligible_episode_data = self._filter_episode_data_for_update(episode_data)
        # [AGENT-EDIT] 업데이트는 항상 수행 (baseline 대비 열세도 학습 신호로 사용)
        update_allowed = bool(eligible_episode_data)
        update_applied = bool(update_allowed)
        if update_applied:
            actor_loss, entropy_loss = self._ppo_update(eligible_episode_data, advantage)  # 🔥 엔트로피 Loss
        else:
            # [AGENT-EDIT] 데이터가 없으면 업데이트 불가
            actor_loss = 0.0
            entropy_loss = 0.0  # 🔥 기본값
        print(f"[3] 완료: {time.time()-t3:.1f}초")
        
        diagnostics = self._build_episode_diagnostics(
            actor_env=actor_env,
            actor_schedule=schedule_results,
            actor_stats=stats,
            baseline_details=baseline_details,
            baseline_makespan=baseline_makespan
        )

        # [AGENT-ADD] 고정 통계 전환 체크
        self._maybe_freeze_norm_stats()

        # episode_data를 반환에 포함
        return {
            'real_makespan': real_makespan,
            'baseline_makespan': baseline_makespan,
            'advantage': advantage,
            'actor_loss': actor_loss,
            'entropy_loss': entropy_loss,  # 🔥 엔트로피 Loss 추가
            'violations': actor_violations,
            'baseline_violations': baseline_violations,
            'actor_longi_balance': actor_longi_balance,
            'baseline_longi_balance': baseline_longi_balance,
            'actor_longi_a': actor_longi_a,
            'actor_longi_b': actor_longi_b,
            'actor_longi_total': actor_longi_total,
            'baseline_longi_a': baseline_longi_a,
            'baseline_longi_b': baseline_longi_b,
            'baseline_longi_total': baseline_longi_total,
            'actor_score': actor_score,
            'baseline_score': baseline_score,
            'update_applied': update_applied,
            'episode_data': episode_data,  # 추가
            'diagnostics': diagnostics,
            'update_skipped': not update_allowed
        }

    # ==== [AGENT-ADD] PPO 업데이트용 스텝 필터 ====
    def _filter_episode_data_for_update(self, episode_data: List[Dict]) -> List[Dict]:
        """
        후보 수가 2개 이상인 스텝만 업데이트 대상으로 사용.
        """
        if not episode_data:
            return []
        filtered = []
        for step_data in episode_data:
            available_indices = step_data.get('available_indices')
            if available_indices is None:
                available_features = step_data.get('available_features')
                candidate_count = len(available_features) if available_features is not None else 0
            else:
                candidate_count = len(available_indices)
            if candidate_count >= 2:
                filtered.append(step_data)
        return filtered

    # =============================
    # Profile-expanded self-label generation
    # =============================
    def _bias_profile_specs(self, expanded: bool = True) -> List[Dict]:
        """[AGENT-ADD] Return bias profiles for candidate generation.

        Bias components are candidate-reduction mechanisms, not audit constraints:
        1=workshop head, 2=leadtime layers, 3=date window.
        """
        if not expanded:
            return [{"components": None, "label": "current", "display": "현재설정"}]

        profiles: List[Dict] = []
        for mask in range(8):
            components = [idx + 1 for idx in range(3) if mask & (1 << idx)]
            label = ",".join(str(x) for x in components) if components else "off"
            display_parts = []
            if 1 in components:
                display_parts.append("작업장head")
            if 2 in components:
                display_parts.append("리드타임층")
            if 3 in components:
                display_parts.append("날짜창")
            profiles.append({
                "components": components,
                "label": label,
                "display": "+".join(display_parts) if display_parts else "off",
            })
        return profiles

    def _hard_profile_specs(self, expanded: bool = True) -> List[Dict]:
        """[AGENT-ADD] Return hard constraint generation profiles.

        Capacity constraints are intentionally absent. The 8 profiles are:
        all-on + each one of the 7 non-capacity relaxation targets off.
        """
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

        profile_defs = [
            ("line_group_off", "라인고정간격 off", ["LINE_GROUP_CONSTRAINT"], {"enable_line_group_constraint": False}),
            ("mixing_off", "라인혼합 off", ["P5#11", "P5#12"], {
                "enable_p5_11_fixed_line_mixing": False,
                "enable_p5_12_internal_external_mixing": False,
            }),
            ("p6_4_off", "P6#4 off", ["P6#4"], {"enable_p6_4_cross_seam_mixing": False}),
            ("p5_15_off", "P5#15 off", ["P5#15"], {"enable_p5_15_holiday_shift": False}),
            ("c_seam_off", "C-Seam off", ["ROUTING_C_SEAM_SPACING"], {"enable_c_seam_spacing": False}),
            ("curved_off", "곡판 off", ["ROUTING_CURVED_SPACING"], {"enable_routing_curved_spacing": False}),
            ("high_seam_off", "고심수 off", ["ROUTING_HIGH_SEAM_SPACING"], {"enable_routing_high_seam_spacing": False}),
        ]
        for label, display, off_ids, off_flags in profile_defs:
            profiles.append({
                "label": label,
                "display": display,
                "off_ids": off_ids,
                "off_flags": off_flags,
                "base_flags": base_flags,
            })
        return profiles

    def _build_profile_runtime_config(self, bias_profile: Dict, hard_profile: Optional[Dict] = None) -> Dict:
        """[AGENT-ADD] Build runtime config for one self-label candidate profile."""
        runtime_cfg = copy.deepcopy(get_runtime_config() or {})
        constraints = runtime_cfg.setdefault('constraints', {}) if isinstance(runtime_cfg, dict) else {}
        if not isinstance(constraints, dict):
            constraints = {}
            runtime_cfg['constraints'] = constraints

        components = (bias_profile or {}).get("components")
        if components is not None:
            constraints['bias_components'] = list(components)

        hard_profile = hard_profile or self._hard_profile_specs(expanded=False)[0]
        target_ids: List[str] = []
        for flag_name, flag_value in (hard_profile.get("base_flags") or {}).items():
            constraints[flag_name] = bool(flag_value)
        for off_id in hard_profile.get("off_ids") or []:
            target_ids.append(str(off_id))
        for flag_name, flag_value in (hard_profile.get("off_flags") or {}).items():
            constraints[flag_name] = bool(flag_value)

        # [AGENT-ADD] If an enabled-list is used, make all hard-profile targets visible first,
        # then remove the one relaxed target. Capacity IDs are never modified here.
        hard_target_ids = [
            "LINE_GROUP_CONSTRAINT", "P5#11", "P5#12", "P6#4", "P5#15",
            "ROUTING_C_SEAM_SPACING", "ROUTING_CURVED_SPACING", "ROUTING_HIGH_SEAM_SPACING",
        ]
        enabled = list(constraints.get("enabled_constraints_assembly") or [])
        if enabled:
            seen = set(enabled)
            for cid in hard_target_ids:
                if cid not in seen:
                    enabled.append(cid)
                    seen.add(cid)
            off_set = set(target_ids)
            enabled = [cid for cid in enabled if cid not in off_set]
            constraints["enabled_constraints_assembly"] = enabled

        for list_key in ("hard_constraints_assembly", "strict_rules"):
            values = list(constraints.get(list_key) or [])
            if values and target_ids:
                off_set = set(target_ids)
                constraints[list_key] = [value for value in values if value not in off_set]

        return runtime_cfg

    def _run_with_runtime_config(self, runtime_cfg: Optional[Dict], callback):
        """[AGENT-ADD] Temporarily swap runtime config for one candidate generation."""
        if runtime_cfg is None:
            return callback()
        original_runtime_cfg = copy.deepcopy(get_runtime_config() or {})
        try:
            set_runtime_config(runtime_cfg)
            return callback()
        finally:
            set_runtime_config(original_runtime_cfg)

    def _record_perf_event(self, kind: str, tag: str, seconds: float, **extra: Any) -> None:
        """[AGENT-ADD] Store opt-in performance timing events for one training episode."""
        if not self.perf_timing:
            return
        event = {
            "kind": str(kind),
            "tag": str(tag),
            "seconds": float(seconds),
        }
        event.update(extra)
        self._active_perf_events.append(event)

    def _summarize_perf_events(self, total_seconds: float) -> Dict[str, Any]:
        """[AGENT-ADD] Build a compact timing summary for logs and diagnostics."""
        if not self._active_perf_events:
            return {"total_seconds": float(total_seconds), "events": 0, "by_kind": {}, "slowest": []}

        by_kind: Dict[str, Dict[str, float]] = {}
        for event in self._active_perf_events:
            kind = event.get("kind", "unknown")
            sec = float(event.get("seconds", 0.0) or 0.0)
            bucket = by_kind.setdefault(kind, {"count": 0, "total_seconds": 0.0, "max_seconds": 0.0})
            bucket["count"] += 1
            bucket["total_seconds"] += sec
            bucket["max_seconds"] = max(bucket["max_seconds"], sec)

        for bucket in by_kind.values():
            count = max(1, int(bucket["count"]))
            bucket["avg_seconds"] = bucket["total_seconds"] / count

        slowest = sorted(
            self._active_perf_events,
            key=lambda item: float(item.get("seconds", 0.0) or 0.0),
            reverse=True,
        )[: self.perf_top_k]
        return {
            "total_seconds": float(total_seconds),
            "events": len(self._active_perf_events),
            "by_kind": by_kind,
            "slowest": slowest,
        }

    def _print_perf_summary(self, summary: Dict[str, Any]) -> None:
        """[AGENT-ADD] Print timing summary only when --perf_timing is enabled."""
        if not self.perf_timing:
            return
        print(
            f"[Perf] self-label total={summary.get('total_seconds', 0.0):.2f}s "
            f"events={summary.get('events', 0)}"
        )
        for kind, values in sorted((summary.get("by_kind") or {}).items()):
            print(
                f"[Perf] {kind}: count={int(values.get('count', 0))} "
                f"total={float(values.get('total_seconds', 0.0)):.2f}s "
                f"avg={float(values.get('avg_seconds', 0.0)):.3f}s "
                f"max={float(values.get('max_seconds', 0.0)):.3f}s"
            )
        for event in summary.get("slowest") or []:
            extra = []
            if event.get("makespan") is not None:
                extra.append(f"makespan={float(event.get('makespan')):.2f}h")
            if event.get("violations") is not None:
                extra.append(f"violations={event.get('violations')}")
            if event.get("steps") is not None:
                extra.append(f"steps={event.get('steps')}")
            extra_text = " " + " ".join(extra) if extra else ""
            print(
                f"[Perf] slow {event.get('kind')} {event.get('tag')}: "
                f"{float(event.get('seconds', 0.0)):.3f}s{extra_text}"
            )

    def _resolve_self_label_parallel_plan(self, candidate_count: int) -> Dict[str, Any]:
        """[AGENT-ADD] Pick worker count/device from CPU cores, GPU memory, and RAM."""
        if self.self_label_parallel == "off":
            return {"enabled": False, "reason": "parallel_off"}
        if candidate_count < self.self_label_parallel_min_candidates:
            return {"enabled": False, "reason": "too_few_candidates"}

        cpu_count = os.cpu_count() or 1
        ram_gb = _get_total_memory_gb()
        worker_device = self.self_label_parallel_device
        if worker_device == "auto":
            worker_device = "cuda" if self.device.type == "cuda" and torch.cuda.is_available() else "cpu"
        if worker_device == "cuda" and not torch.cuda.is_available():
            worker_device = "cpu"

        # [AGENT-EDIT] Server training target is throughput, so reserve only a small control margin.
        reserve_cores = max(2, cpu_count // 16)
        max_by_cpu = max(1, cpu_count - reserve_cores)

        max_by_ram = max_by_cpu
        if ram_gb is not None:
            ram_per_worker_gb = float(os.environ.get("PBS_SELF_LABEL_RAM_GB_PER_WORKER", "1.25"))
            max_by_ram = max(1, int((ram_gb * 0.80) // max(0.25, ram_per_worker_gb)))

        max_by_gpu = max_by_cpu
        gpu_gb = None
        if worker_device == "cuda":
            try:
                props = torch.cuda.get_device_properties(self.device)
                gpu_gb = float(props.total_memory) / (1024 ** 3)
                # [AGENT-EDIT] A6000 실측 worker당 ~0.18 GiB; 기본값을 0.35로 낮춰 더 많은 worker 허용.
                gpu_per_worker_gb = float(os.environ.get("PBS_SELF_LABEL_GPU_GB_PER_WORKER", "0.35"))
                max_by_gpu = max(1, int((gpu_gb * 0.82) // max(0.20, gpu_per_worker_gb)))
            except Exception:
                max_by_gpu = max_by_cpu
            max_gpu_workers = int(os.environ.get("PBS_SELF_LABEL_MAX_GPU_WORKERS", "52"))
            max_by_gpu = min(max_by_gpu, max(1, max_gpu_workers))

        auto_workers = min(candidate_count, max_by_cpu, max_by_ram, max_by_gpu)
        if self.self_label_parallel_workers > 0:
            workers = min(candidate_count, self.self_label_parallel_workers)
        else:
            workers = auto_workers

        if workers < 2:
            return {
                "enabled": False,
                "reason": "single_worker",
                "cpu_count": cpu_count,
                "ram_gb": ram_gb,
                "gpu_gb": gpu_gb,
            }

        worker_threads = self.self_label_worker_threads
        if worker_threads <= 0:
            # [AGENT-ADD] 후보 병렬화에서는 프로세스 수로 CPU를 채우므로 worker 내부 torch thread는 낮게 둔다.
            worker_threads = 1

        return {
            "enabled": True,
            "workers": int(workers),
            "worker_device": worker_device,
            "worker_threads": int(worker_threads),
            "cpu_count": cpu_count,
            "ram_gb": ram_gb,
            "gpu_gb": gpu_gb,
            "max_by_cpu": max_by_cpu,
            "max_by_ram": max_by_ram,
            "max_by_gpu": max_by_gpu,
            "candidate_count": candidate_count,
        }

    def _format_candidate_profile(self, record: Dict) -> str:
        source = record.get("source", record.get("tag", "candidate"))
        bias = record.get("bias_display", record.get("bias_label", "current"))
        hard = record.get("hard_display", record.get("hard_label", "all-on"))
        sample = record.get("sample_label")
        if sample:
            return f"{source}: bias [{bias}] hard [{hard}] {sample}"
        return f"{source}: bias [{bias}] hard [{hard}]"

    def _append_candidate_record(
        self,
        candidate_records: List[Dict],
        record: Optional[Dict],
        *,
        source: str,
        bias_profile: Dict,
        hard_profile: Optional[Dict] = None,
        sample_idx: Optional[int] = None,
        total_samples: Optional[int] = None,
    ) -> Optional[Dict]:
        """[AGENT-ADD] Append candidate and print whenever it becomes the current best."""
        if not record:
            return None
        previous_best = self._select_best_record(candidate_records) if candidate_records else None
        hard_profile = hard_profile or self._hard_profile_specs(expanded=False)[0]
        record["source"] = source
        record["bias_label"] = (bias_profile or {}).get("label", "current")
        record["bias_display"] = (bias_profile or {}).get("display", record["bias_label"])
        record["bias_components"] = (bias_profile or {}).get("components")
        record["hard_label"] = hard_profile.get("label", "all_on")
        record["hard_display"] = hard_profile.get("display", record["hard_label"])
        record["hard_off_ids"] = list(hard_profile.get("off_ids") or [])
        if sample_idx is not None and total_samples is not None:
            record["sample_label"] = f"sample {sample_idx}/{total_samples}"
        candidate_records.append(record)

        if self.self_label_print_best_updates and (
            previous_best is None or self._is_record_better(record, previous_best)
        ):
            metrics = self._record_metrics(record)
            sequence_preview = [row.get("block_id") for row in (record.get("schedule") or [])[:10]]
            print(
                "[시퀀스 갱신] "
                f"{self._format_candidate_profile(record)} -> "
                f"primary={metrics['violations']}, "
                f"makespan={metrics['makespan']:.2f}h, "
                f"longi={metrics['longi_balance']:.3f}, "
                f"seq_head={sequence_preview}"
            )
        return record

    def _build_parallel_self_label_tasks(
        self,
        *,
        rl_bias_profiles: List[Dict],
        rl_hard_profiles: List[Dict],
        heuristic_bias_profiles: List[Dict],
        all_on_hard_profile: Dict,
        start_date: str,
    ) -> List[Dict[str, Any]]:
        """[AGENT-ADD] Build self-label candidate tasks for multiprocessing workers."""
        tasks: List[Dict[str, Any]] = []
        for bias_profile in rl_bias_profiles:
            for hard_profile in rl_hard_profiles:
                runtime_cfg = self._build_profile_runtime_config(bias_profile, hard_profile)
                for sample_idx in range(1, self.self_label_samples + 1):
                    tag = (
                        f"selflabel_rl_b{bias_profile.get('label', 'current')}"
                        f"_h{hard_profile.get('label', 'all_on')}_s{sample_idx}"
                    )
                    tasks.append({
                        "kind": "rl",
                        "tag": tag,
                        "runtime_cfg": runtime_cfg,
                        "source": "RL",
                        "bias_profile": bias_profile,
                        "hard_profile": hard_profile,
                        "sample_idx": sample_idx,
                        "total_samples": self.self_label_samples,
                        "start_date": start_date,
                        "seed": random.randint(1, 2 ** 31 - 2),
                    })

        for method, source_name in (("lpt", "LPT"), ("seam_min", "SEAM_MIN")):
            for bias_profile in heuristic_bias_profiles:
                runtime_cfg = self._build_profile_runtime_config(bias_profile, all_on_hard_profile)
                tasks.append({
                    "kind": "heuristic",
                    "method": method,
                    "tag": f"selflabel_{method}_b{bias_profile.get('label', 'current')}",
                    "runtime_cfg": runtime_cfg,
                    "source": source_name,
                    "bias_profile": bias_profile,
                    "hard_profile": all_on_hard_profile,
                    "start_date": start_date,
                    "seed": random.randint(1, 2 ** 31 - 2),
                })
        return tasks

    def _generate_self_label_candidates_parallel(
        self,
        *,
        tasks: List[Dict[str, Any]],
        blocks: List,
        metadata: Dict,
        start_date: str,
        plan: Dict[str, Any],
    ) -> Tuple[List[Dict], Optional[Dict], Optional[List], Optional[Dict]]:
        """[AGENT-ADD] Generate self-label candidates concurrently across CPU/GPU worker processes."""
        candidate_records: List[Dict] = []
        lpt_record = None
        lpt_fallback_results = None
        lpt_fallback_stats = None

        actor_state_dict = {
            key: value.detach().cpu()
            for key, value in self.actor.state_dict().items()
        }
        actor_params = copy.deepcopy(self.actor_params_for_resume)
        if not actor_params:
            raise RuntimeError("parallel self-label requires actor_params_for_resume")

        worker_device = plan["worker_device"]
        workers = int(plan["workers"])
        worker_threads = int(plan["worker_threads"])
        if self.perf_timing or self.self_label_print_best_updates:
            ram_text = f"{plan.get('ram_gb'):.1f}GiB" if plan.get("ram_gb") is not None else "unknown"
            gpu_text = f"{plan.get('gpu_gb'):.1f}GiB" if plan.get("gpu_gb") is not None else "n/a"
            print(
                "[Self-label 병렬] "
                f"workers={workers}, device={worker_device}, worker_threads={worker_threads}, "
                f"cpu={plan.get('cpu_count')}, ram={ram_text}, gpu_mem={gpu_text}, tasks={len(tasks)}"
            )

        # [AGENT-EDIT] Persistent pool: spawn once, reuse across episodes.
        # On the first episode the pool is created via initializer (same as before).
        # On subsequent episodes, _pool_sync_task() updates weights + data in-place,
        # avoiding the per-episode cost of spawning 44+ processes and loading the GPU model.
        global _PERSISTENT_POOL, _PERSISTENT_POOL_WORKERS, _PERSISTENT_POOL_DEVICE
        need_new_pool = (
            _PERSISTENT_POOL is None
            or _PERSISTENT_POOL_WORKERS != workers
            or _PERSISTENT_POOL_DEVICE != worker_device
        )
        if need_new_pool:
            _shutdown_persistent_pool()
            ctx = mp.get_context("spawn")
            _PERSISTENT_POOL = ProcessPoolExecutor(
                max_workers=workers,
                mp_context=ctx,
                initializer=_init_self_label_worker,
                initargs=(
                    actor_params,
                    actor_state_dict,
                    worker_device,
                    blocks,
                    metadata,
                    start_date,
                    copy.deepcopy(get_runtime_config() or {}),
                    worker_threads,
                ),
            )
            _PERSISTENT_POOL_WORKERS = workers
            _PERSISTENT_POOL_DEVICE = worker_device
            # Warm up all workers so they're initialized before the first task.
            warmup_futures = [_PERSISTENT_POOL.submit(_pool_sync_task, actor_state_dict, blocks, metadata, start_date, get_runtime_config() or {}) for _ in range(workers)]
            for f in warmup_futures:
                f.result()
        else:
            # Sync weights and episode data into existing workers.
            sync_futures = [_PERSISTENT_POOL.submit(_pool_sync_task, actor_state_dict, blocks, metadata, start_date, get_runtime_config() or {}) for _ in range(workers)]
            for f in sync_futures:
                f.result()

        executor = _PERSISTENT_POOL
        future_to_task = {
            executor.submit(_run_parallel_self_label_task, task): task
            for task in tasks
        }
        for future in as_completed(future_to_task):
            task = future_to_task[future]
            result = future.result()
            for event in result.get("perf_events") or []:
                self._record_perf_event(
                    event.get("kind", task.get("kind", "candidate")),
                    event.get("tag", task.get("tag", "candidate")),
                    float(event.get("seconds", 0.0) or 0.0),
                    steps=event.get("steps"),
                    makespan=event.get("makespan"),
                    violations=event.get("violations"),
                )
            if not result.get("ok"):
                raise RuntimeError(
                    f"parallel self-label 후보 생성 실패: tag={result.get('tag')}, kind={result.get('kind')}\n"
                    f"{result.get('error')}"
                )

            record = result.get("record")
            source_name = task.get("source", "RL")
            if source_name == "LPT" and lpt_fallback_results is None:
                lpt_fallback_results = result.get("heuristic_results")
                lpt_fallback_stats = result.get("heuristic_stats")

            appended = self._append_candidate_record(
                candidate_records,
                record,
                source=source_name,
                bias_profile=task.get("bias_profile") or {},
                hard_profile=task.get("hard_profile"),
                sample_idx=task.get("sample_idx"),
                total_samples=task.get("total_samples"),
            )
            if source_name == "LPT" and appended:
                if lpt_record is None or self._is_record_better(appended, lpt_record):
                    lpt_record = appended
                    lpt_fallback_results = result.get("heuristic_results")
                    lpt_fallback_stats = result.get("heuristic_stats")

        return candidate_records, lpt_record, lpt_fallback_results, lpt_fallback_stats

    def _generate_self_label_heuristic_results(self, blocks: List, metadata: Dict, start_date: str, method: str):
        """[AGENT-ADD] Generate heuristic teacher sequence under the active runtime profile."""
        return run_assembly_decoding_sequence_with_blocks(
            blocks=blocks,
            metadata=metadata,
            decoding_type="assembly",
            selection_method=method,
            max_days=20,
            start_date=start_date,
            date_offset=0,
            output_csv="",
            save_csv=False,
            save_detailed=False,
            forced_sequence=None,
            expand_rows=False
        )

    def _build_lpt_teacher_runtime_config(self) -> Dict:
        """[AGENT-ADD] teacher LPT만 bias on으로 돌릴 때 쓸 runtime config를 만든다."""
        runtime_cfg = copy.deepcopy(get_runtime_config() or {})
        constraints = runtime_cfg.setdefault('constraints', {}) if isinstance(runtime_cfg, dict) else {}
        if not isinstance(constraints, dict):
            constraints = {}
            runtime_cfg['constraints'] = constraints
        constraints['enable_workshop_head_masking'] = True
        constraints['enable_assembly_start_leadtime_layers'] = True
        constraints['enable_assembly_start_window_filter'] = True
        return runtime_cfg

    def _generate_self_label_lpt_results(self, blocks: List, metadata: Dict, start_date: str):
        """[AGENT-ADD] 필요 시 teacher LPT만 bias on으로 override하여 기준 시퀀스를 생성한다."""
        original_runtime_cfg = copy.deepcopy(get_runtime_config() or {})
        try:
            if self.self_label_lpt_teacher_bias_on:
                set_runtime_config(self._build_lpt_teacher_runtime_config())
            return run_assembly_decoding_sequence_with_blocks(
                blocks=blocks,
                metadata=metadata,
                decoding_type="assembly",
                selection_method="lpt",
                max_days=20,
                start_date=start_date,
                date_offset=0,
                output_csv="",
                save_csv=False,
                save_detailed=False,
                forced_sequence=None,
                expand_rows=False
            )
        finally:
            set_runtime_config(original_runtime_cfg)

    # ------------------------------------------------------------------
    # Self-Label (best-of-K + teacher forcing) 모드
    # ------------------------------------------------------------------
    def train_episode_self_label(self, blocks: List, metadata: Dict) -> Dict:
        """Self-label 방식으로 한 에피소드 학습"""
        self.actor.train()
        # [AGENT-ADD] Reset per-episode performance timing storage.
        episode_t0 = time.perf_counter()
        self._active_perf_events = []
        start_date = self._compute_episode_start_date(blocks)

        candidate_records = []
        lpt_record = None
        lpt_fallback_results = None
        lpt_fallback_stats = None

        rl_bias_profiles = self._bias_profile_specs(expanded=self.self_label_profile_expansion)
        rl_hard_profiles = self._hard_profile_specs(expanded=self.self_label_profile_expansion)
        heuristic_bias_profiles = self._bias_profile_specs(expanded=self.self_label_heuristic_profile_expansion)
        all_on_hard_profile = self._hard_profile_specs(expanded=False)[0]

        expected_rl_candidates = len(rl_bias_profiles) * len(rl_hard_profiles) * self.self_label_samples
        expected_heuristic_candidates = len(heuristic_bias_profiles) * 2
        if self.self_label_print_best_updates:
            print(
                "[Self-label 후보 생성] "
                f"RL={len(rl_bias_profiles)} bias x {len(rl_hard_profiles)} hard x {self.self_label_samples} samples "
                f"= {expected_rl_candidates}, "
                f"LPT={len(heuristic_bias_profiles)}, SEAM_MIN={len(heuristic_bias_profiles)}, "
                f"expected_total={expected_rl_candidates + expected_heuristic_candidates}"
            )

        parallel_tasks = self._build_parallel_self_label_tasks(
            rl_bias_profiles=rl_bias_profiles,
            rl_hard_profiles=rl_hard_profiles,
            heuristic_bias_profiles=heuristic_bias_profiles,
            all_on_hard_profile=all_on_hard_profile,
            start_date=start_date,
        )
        parallel_plan = self._resolve_self_label_parallel_plan(len(parallel_tasks))

        if parallel_plan.get("enabled"):
            candidate_records, lpt_record, lpt_fallback_results, lpt_fallback_stats = (
                self._generate_self_label_candidates_parallel(
                    tasks=parallel_tasks,
                    blocks=blocks,
                    metadata=metadata,
                    start_date=start_date,
                    plan=parallel_plan,
                )
            )
        else:
            if self.perf_timing:
                print(f"[Self-label 병렬] disabled: {parallel_plan.get('reason')}")
            # [AGENT-ADD] RL/Self-label stochastic candidates: 8 bias x 8 hard profiles x K samples.
            for bias_profile in rl_bias_profiles:
                for hard_profile in rl_hard_profiles:
                    runtime_cfg = self._build_profile_runtime_config(bias_profile, hard_profile)
                    for sample_idx in range(1, self.self_label_samples + 1):
                        tag = (
                            f"selflabel_rl_b{bias_profile.get('label', 'current')}"
                            f"_h{hard_profile.get('label', 'all_on')}_s{sample_idx}"
                        )
                        record = self._run_with_runtime_config(
                            runtime_cfg,
                            lambda tag=tag: self._sample_schedule(
                                model=self.actor,
                                blocks=blocks,
                                metadata=metadata,
                                start_date=start_date,
                                training_mode=True,
                                tag=tag,
                            )
                        )
                        self._append_candidate_record(
                            candidate_records,
                            record,
                            source="RL",
                            bias_profile=bias_profile,
                            hard_profile=hard_profile,
                            sample_idx=sample_idx,
                            total_samples=self.self_label_samples,
                        )

            # [AGENT-ADD] Heuristic teacher candidates: LPT and SEAM_MIN under the 8 bias profiles.
            # Hard profiles are not crossed here by design; capacity remains untouched and hard constraints stay all-on.
            for method, source_name in (("lpt", "LPT"), ("seam_min", "SEAM_MIN")):
                for bias_profile in heuristic_bias_profiles:
                    runtime_cfg = self._build_profile_runtime_config(bias_profile, all_on_hard_profile)
                    try:
                        heuristic_t0 = time.perf_counter()
                        heuristic_results, heuristic_stats = self._run_with_runtime_config(
                            runtime_cfg,
                            lambda method=method: self._generate_self_label_heuristic_results(
                                blocks=blocks,
                                metadata=metadata,
                                start_date=start_date,
                                method=method,
                            )
                        )
                        self._record_perf_event(
                            "heuristic_sequence",
                            f"{method}_b{bias_profile.get('label', 'current')}",
                            time.perf_counter() - heuristic_t0,
                            steps=len(heuristic_results or []),
                            makespan=(heuristic_stats or {}).get("makespan_hours"),
                            violations=get_violation_count(heuristic_stats or {}),
                        )
                        forced_sequence = [
                            r.get('block_id') for r in (heuristic_results or [])
                            if r.get('block_id') is not None
                        ]
                        if source_name == "LPT" and lpt_fallback_results is None:
                            lpt_fallback_results = heuristic_results
                            lpt_fallback_stats = heuristic_stats
                        if not forced_sequence:
                            continue
                        forced_tag = f"selflabel_{method}_b{bias_profile.get('label', 'current')}"
                        forced_record = self._run_with_runtime_config(
                            runtime_cfg,
                            lambda forced_tag=forced_tag, forced_sequence=forced_sequence: self._sample_schedule(
                                model=self.actor,
                                blocks=blocks,
                                metadata=metadata,
                                start_date=start_date,
                                training_mode=True,
                                tag=forced_tag,
                                forced_sequence=forced_sequence,
                            )
                        )
                        appended = self._append_candidate_record(
                            candidate_records,
                            forced_record,
                            source=source_name,
                            bias_profile=bias_profile,
                            hard_profile=all_on_hard_profile,
                        )
                        if source_name == "LPT" and appended:
                            if lpt_record is None or self._is_record_better(appended, lpt_record):
                                lpt_record = appended
                                lpt_fallback_results = heuristic_results
                                lpt_fallback_stats = heuristic_stats
                    except Exception as exc:
                        raise RuntimeError(f"self-label {source_name} 후보 생성 실패: bias={bias_profile.get('label')}") from exc

        self.self_label_last_candidate_count = len(candidate_records)

        if not candidate_records:
            return {
                'real_makespan': float('inf'),
                'baseline_makespan': float('inf'),
                'advantage': 0.0,
                'actor_loss': 0.0,
                'entropy_loss': 0.0,
                'violations': float('inf'),
                'episode_data': [],
                'diagnostics': {},
                # [AGENT-ADD] self-label 업데이트 소스 추적 (후보 없음)
                'update_source': 'none'
            }

        best_record = self._select_best_record(candidate_records)
        best_source = str(best_record.get('source', 'RL')).lower()
        update_source = "self_label"
        # [AGENT-EDIT] candidate_only에서는 LPT가 이미 후보군에 있으므로 별도 override를 하지 않는다.
        if lpt_record and self._should_use_lpt_teacher(best_record, lpt_record):
            best_record = lpt_record
            best_source = "lpt"
            update_source = "lpt"
        elif best_source == "lpt" or str(best_record.get('tag', '')).startswith("selflabel_lpt"):
            update_source = "lpt"
        elif best_source == "seam_min" or str(best_record.get('tag', '')).startswith("selflabel_seam_min"):
            update_source = "seam_min"
        update_t0 = time.perf_counter()
        actor_loss = self._teacher_forcing_update(best_record['episode_data'])
        self._record_perf_event(
            "teacher_update",
            update_source,
            time.perf_counter() - update_t0,
            steps=len(best_record.get('episode_data') or []),
        )

        real_makespan = best_record['stats'].get('makespan_hours', float('inf'))
        violations = get_violation_count(best_record.get('stats', {}))
        # [AGENT-ADD] Self-label 론지 균형 상세
        actor_longi_balance, actor_longi_a, actor_longi_b, actor_longi_total = compute_longi_balance_details(best_record.get('schedule'))
        actor_score = compute_score(
            real_makespan,
            violations,
            actor_longi_balance,
            self.violation_penalty_weight,
            self.longi_balance_weight,
        )

        # [AGENT-ADD] Self-label도 LPT 기준으로 baseline 로그 통일
        baseline_details = None
        baseline_makespan = real_makespan
        baseline_violations = violations
        baseline_longi_balance = actor_longi_balance
        baseline_longi_a = actor_longi_a
        baseline_longi_b = actor_longi_b
        baseline_longi_total = actor_longi_total
        if lpt_record:
            baseline_details = {
                'schedule_results': lpt_record.get('schedule'),
                'statistics': lpt_record.get('stats', {}),
                'env': lpt_record.get('env')
            }
            baseline_makespan = lpt_record.get('stats', {}).get('makespan_hours', float('inf'))
            baseline_violations = get_violation_count(lpt_record.get('stats', {}))
            (baseline_longi_balance,
             baseline_longi_a,
             baseline_longi_b,
             baseline_longi_total) = compute_longi_balance_details(lpt_record.get('schedule'))
        elif lpt_fallback_results is not None:
            baseline_details = {
                'schedule_results': lpt_fallback_results,
                'statistics': lpt_fallback_stats or {},
                'env': None
            }
            baseline_makespan = (lpt_fallback_stats or {}).get('makespan_hours', float('inf'))
            baseline_violations = get_violation_count(lpt_fallback_stats or {})
            (baseline_longi_balance,
             baseline_longi_a,
             baseline_longi_b,
             baseline_longi_total) = compute_longi_balance_details(lpt_fallback_results)

        baseline_score = compute_score(
            baseline_makespan,
            baseline_violations,
            baseline_longi_balance,
            self.violation_penalty_weight,
            self.longi_balance_weight,
        )
        advantage = baseline_score - actor_score

        diagnostics = self._build_episode_diagnostics(
            actor_env=best_record['env'],
            actor_schedule=best_record['schedule'],
            actor_stats=best_record['stats'],
            baseline_details=baseline_details,
            baseline_makespan=baseline_makespan
        )
        diagnostics['candidate_count'] = len(candidate_records)
        diagnostics['best_candidate_profile'] = self._format_candidate_profile(best_record)
        diagnostics['profile_expansion'] = {
            'rl_bias_profiles': len(rl_bias_profiles),
            'rl_hard_profiles': len(rl_hard_profiles),
            'rl_samples': self.self_label_samples,
            'heuristic_bias_profiles': len(heuristic_bias_profiles),
            'expected_total': expected_rl_candidates + expected_heuristic_candidates,
        }
        perf_summary = self._summarize_perf_events(time.perf_counter() - episode_t0)
        diagnostics['perf_timing'] = perf_summary
        self._print_perf_summary(perf_summary)

        self.episode_count += 1

        # [AGENT-ADD] 고정 통계 전환 체크
        self._maybe_freeze_norm_stats()

        return {
            'real_makespan': real_makespan,
            'baseline_makespan': baseline_makespan,
            'advantage': advantage,
            'actor_loss': actor_loss,
            'entropy_loss': 0.0,
            'violations': violations,
            'baseline_violations': baseline_violations,
            'actor_longi_balance': actor_longi_balance,
            'baseline_longi_balance': baseline_longi_balance,
            'actor_longi_a': actor_longi_a,
            'actor_longi_b': actor_longi_b,
            'actor_longi_total': actor_longi_total,
            'baseline_longi_a': baseline_longi_a,
            'baseline_longi_b': baseline_longi_b,
            'baseline_longi_total': baseline_longi_total,
            'actor_score': actor_score,
            'baseline_score': baseline_score,
            'update_applied': bool(best_record.get('episode_data')),
            # [AGENT-ADD] self-label 업데이트 소스 추적 (lpt/self_label)
            'update_source': update_source,
            'episode_data': best_record['episode_data'],
            'diagnostics': diagnostics
        }

    # =============================
    # Self-label helper functions
    # =============================
    def _sample_schedule(self,
                         model,
                         blocks: List,
                         metadata: Dict,
                         start_date: str,
                         training_mode: bool,
                         tag: str,
                         forced_sequence: Optional[List[int]] = None):  # [AGENT-ADD] block_id 강제 시퀀스
        """
        모델로 한 번 스케줄을 샘플링하여 기록을 반환.
        """
        model.default_decode_type = "sampling"
        sample_t0 = time.perf_counter()
        try:
            schedule_results, stats, episode_data, env = run_rl_assembly_decoding_sequence_with_blocks(
                blocks=blocks,
                metadata=metadata,
                rl_agent=model,
                device=self.device,
                max_days=20,
                start_date=start_date,
                training_mode=training_mode,
                save_csv=False,
                save_detailed=False,
                forced_sequence=forced_sequence,
                collect_episode_data=training_mode,
                # [AGENT-ADD] Rollout 후보는 업데이트 때 log prob를 재계산하므로 autograd 그래프를 만들지 않는다.
                enable_grad=False,
                # [AGENT-ADD] Self-label teacher forcing에는 step별 부분 makespan이 필요 없어서 후보 생성 병목을 줄인다.
                collect_step_metrics=False,
            )
        except Exception:
            self._record_perf_event(
                "failed_schedule",
                tag,
                time.perf_counter() - sample_t0,
                steps=0,
            )
            raise
        self._record_perf_event(
            "forced_schedule" if forced_sequence else "rl_schedule",
            tag,
            time.perf_counter() - sample_t0,
            steps=len(episode_data or []),
            makespan=(stats or {}).get("makespan_hours"),
            violations=get_violation_count(stats or {}),
        )
        return {
            'schedule': schedule_results,
            'stats': stats or {},
            'episode_data': episode_data or [],
            'env': env,
            'tag': tag
        }

    def _record_metrics(self, rec: Dict) -> Dict[str, float]:
        """[AGENT-ADD] self-label 후보 비교용 메트릭을 공통 추출한다."""
        st = rec.get('stats', {}) or {}
        ms = st.get('makespan_hours')
        vio = get_violation_count(st)
        ms = float(ms) if ms is not None else float('inf')
        vio = int(vio) if vio is not None else 10**9
        longi_balance = compute_longi_balance_ratio(rec.get('schedule'))
        score = compute_score(
            ms,
            vio,
            longi_balance,
            self.violation_penalty_weight,
            self.longi_balance_weight,
        )
        return {
            'makespan': ms,
            'violations': vio,
            'longi_balance': longi_balance,
            'score': score,
        }

    def _record_sort_key(self, rec: Dict):
        """[AGENT-ADD] self-label teacher 선택 기준을 설정으로 바꾼다."""
        metrics = self._record_metrics(rec)
        ms = metrics['makespan']
        vio = metrics['violations']
        longi_balance = metrics['longi_balance']
        score = metrics['score']
        if self.self_label_selection_strategy == 'primary_first':
            return (vio, ms, longi_balance)
        if self.self_label_selection_strategy == 'feasible_first':
            if vio == 0:
                return (0, ms, longi_balance, 0.0)
            return (1, vio, ms, longi_balance)
        if self.self_label_selection_strategy == 'makespan_first':
            return (ms, vio, longi_balance)
        if self.self_label_mode == 2:
            return (score, vio, ms, longi_balance)
        return (score, ms, vio, longi_balance)

    def _is_record_better(self, candidate: Dict, reference: Dict) -> bool:
        return self._record_sort_key(candidate) < self._record_sort_key(reference)

    def _should_use_lpt_teacher(self, best_record: Dict, lpt_record: Dict) -> bool:
        """[AGENT-EDIT] candidate_only는 후보군 정렬만 사용하고 별도 LPT override를 막는다."""
        policy = self.self_label_lpt_teacher_policy
        if policy == 'candidate_only':
            return False
        if policy == 'disabled':
            return False
        if policy == 'legacy':
            best_metrics = self._record_metrics(best_record)
            lpt_metrics = self._record_metrics(lpt_record)
            return lpt_metrics['score'] <= best_metrics['score']
        if policy == 'warmup_only' and self.episode_count >= self.self_label_lpt_warmup_episodes:
            return False
        return self._is_record_better(lpt_record, best_record)

    def _select_best_record(self, records: List[Dict]) -> Dict:
        """[AGENT-EDIT] legacy score / 제약 우선 / feasible-first teacher 선택을 모두 지원한다."""
        return min(records, key=self._record_sort_key)

    def _teacher_forcing_update(self, episode_data: List[Dict]) -> float:
        """
        선택된 최적 시퀀스를 그대로 따라가도록 NLL 기반 학습.
        """
        if not episode_data:
            return 0.0
        self.actor.train()
        total_logprob = []
        for step in episode_data:
            feats = step.get('available_features')
            idx = step.get('selected_action_idx')
            avail_indices = step.get('available_indices')
            env_state_vec = step.get('env_state_vector', None)
            if feats is None or idx is None:
                continue
            log_prob = self.actor.get_action_log_prob(
                available_block_features=feats,
                selected_local_idx=idx,
                device=self.device,
                env_state=env_state_vec,
                available_indices=avail_indices
            )
            total_logprob.append(log_prob)

        if not total_logprob:
            return 0.0

        loss = -torch.stack(total_logprob).mean()
        self.optimizer.zero_grad()
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.actor.parameters(), self.grad_clip)
        self.optimizer.step()
        return float(loss.item())

    def _build_episode_diagnostics(self,
                                   actor_env,
                                   actor_schedule: Optional[List[Dict]],
                                   actor_stats: Dict,
                                   baseline_details: Optional[Dict],
                                   baseline_makespan: float) -> Dict:
        """로그용 진단 정보 구성"""
        actor_sequence = self._extract_sequence(actor_env, actor_schedule)
        actor_daily, actor_violations = self._summarize_schedule_results(actor_schedule)
        # [AGENT-ADD] 론지 균형 상세
        actor_longi_balance, actor_longi_a, actor_longi_b, actor_longi_total = compute_longi_balance_details(actor_schedule)

        baseline_schedule = baseline_details.get('schedule_results') if baseline_details else None
        baseline_env = baseline_details.get('env') if baseline_details else None
        baseline_statistics = baseline_details.get('statistics', {}) if baseline_details else {}
        baseline_sequence = self._extract_sequence(baseline_env, baseline_schedule)
        baseline_daily, baseline_violations = self._summarize_schedule_results(baseline_schedule)
        baseline_longi_balance, baseline_longi_a, baseline_longi_b, baseline_longi_total = compute_longi_balance_details(baseline_schedule)

        return {
            'actor': {
                'sequence': actor_sequence,
                'daily_makespans': actor_daily,
                'violation_details': actor_violations,
                'statistics': actor_stats or {},
                'longi_balance': actor_longi_balance,
                'longi_a': actor_longi_a,
                'longi_b': actor_longi_b,
                'longi_total': actor_longi_total
            },
            'baseline': {
                'sequence': baseline_sequence,
                'daily_makespans': baseline_daily,
                'violation_details': baseline_violations,
                'statistics': baseline_statistics,
                'makespan': baseline_makespan,
                'longi_balance': baseline_longi_balance,
                'longi_a': baseline_longi_a,
                'longi_b': baseline_longi_b,
                'longi_total': baseline_longi_total
            }
        }

    def _extract_sequence(self, env, schedule_results: Optional[List[Dict]]) -> List[int]:
        """환경/결과에서 실행된 시퀀스 추출"""
        if env is not None and hasattr(env, 'sequence_state'):
            seq_state = getattr(env, 'sequence_state', None)
            if seq_state is not None:
                block_sequence = getattr(seq_state, 'block_sequence', None)
                if block_sequence:
                    return list(block_sequence)
                selected_blocks = getattr(seq_state, 'selected_blocks', None)
                if selected_blocks:
                    return list(selected_blocks)

        if schedule_results:
            sequence = []
            seen = set()
            for result in schedule_results:
                block_id = result.get('subassembly_representative_id') or result.get('block_id')
                if block_id is None:
                    continue
                # 서브어셈블리 확장으로 인한 중복을 방지
                if block_id in seen:
                    continue
                seen.add(block_id)
                sequence.append(int(block_id))
            if sequence:
                return sequence

        return []

    def _summarize_schedule_results(self, schedule_results: Optional[List[Dict]]) -> Tuple[List[Tuple[str, float]], List[Dict]]:
        """일별 makespan 및 제약 위반 요약"""
        if not schedule_results:
            return [], []

        daily_totals: Dict[str, float] = {}
        sort_keys: Dict[str, object] = {}
        violation_entries: List[Dict] = []

        for result in schedule_results:
            date_label, sort_key = self._normalize_date_label(result.get('date'))
            hours = result.get('makespan_hours')
            try:
                hours_value = float(hours)
            except (TypeError, ValueError):
                hours_value = None

            if date_label and hours_value is not None:
                previous = daily_totals.get(date_label, 0.0)
                daily_totals[date_label] = max(previous, hours_value)
                sort_keys[date_label] = sort_key

            constraint_ids = result.get('constraint_ids') or []
            severities = result.get('violation_severity') or []
            messages = result.get('violation_details') or []
            block_id = result.get('block_id')

            for cid, severity, message in zip(constraint_ids, severities, messages):
                sev = (severity or '').upper()
                if sev not in ('WARNING', 'ERROR'):
                    continue
                violation_entries.append({
                    'constraint': cid,
                    'severity': sev,
                    'message': message,
                    'block_id': block_id
                })

        ordered_dates = sorted(
            daily_totals.keys(),
            key=lambda label: sort_keys.get(label) or label
        )
        daily_list = [(label, round(daily_totals[label], 2)) for label in ordered_dates]

        return daily_list, violation_entries

    @staticmethod
    def _normalize_date_label(raw_date: Optional[str]) -> Tuple[Optional[str], Optional[object]]:
        if raw_date is None:
            return None, None
        raw_str = str(raw_date)
        try:
            dt = datetime.strptime(raw_str, "%Y%m%d")
            return dt.strftime("%Y-%m-%d"), dt
        except ValueError:
            return raw_str, raw_str
    
    def _ppo_update(self, 
                    episode_data: List[Dict],
                    global_advantage: float) -> Tuple[float, float]:
        """
        PPO 업데이트 수행
        """
        if not episode_data:
            return 0.0
        
        self.actor.train()
        
        # Old log probabilities 저장
        old_log_probs = []
        for step_data in episode_data:
            log_prob = step_data.get('log_prob')
            if isinstance(log_prob, torch.Tensor):
                old_log_probs.append(log_prob.detach())
            else:
                old_log_probs.append(torch.tensor(log_prob, device=self.device))
        
        old_log_probs = torch.stack(old_log_probs)
        
        # ==== [AGENT-EDIT BEGIN: unify PPO training objective with final audit score] ====
        # 학습 신호는 휴리스틱/평가와 동일한 최종 audit objective 하나만 사용한다.
        # step별 runtime 위반 shaping을 섞으면 최종 제약 카운트와 학습 목표가 어긋난다.
        advantages = torch.full(
            (len(episode_data),),
            float(global_advantage),
            device=self.device,
            dtype=torch.float32,
        )
        # ==== [AGENT-EDIT END] ====
        
        # PPO iterations
        total_loss = 0.0
        total_entropy_loss = 0.0  # 🔥 엔트로피 Loss 누적
        for _ in range(self.ppo_iterations):
            # New log probabilities 계산
            new_log_probs = []
            
            for step_data in episode_data:
                # 액션 재계산
                available_features = step_data['available_features']
                selected_action_idx = step_data['selected_action_idx']
                
                # Forward pass로 새로운 log prob 계산
                if isinstance(available_features, torch.Tensor):
                    features_list = available_features.cpu().numpy().tolist()
                else:
                    features_list = available_features
                
                # 🆕 환경 상태 벡터 가져오기
                env_state_vector = step_data.get('env_state_vector', None)
                
                log_prob = self.actor.get_action_log_prob(
                    available_block_features=features_list,
                    selected_local_idx=selected_action_idx,
                    device=self.device,
                    env_state=env_state_vector,  # 🆕 환경 상태 전달
                    available_indices=step_data.get('available_indices')
                )
                # log_prob는 이미 tensor이므로 직접 추가
                new_log_probs.append(log_prob)

            new_log_probs = torch.stack(new_log_probs)
            
            # Ratio 계산
            ratio = torch.exp(new_log_probs - old_log_probs)
            
            # Advantage 정규화
            # normalized_advantage = (advantage_tensor - advantage_tensor.mean()) / (advantage_tensor.std() + 1e-8)
            
            # PPO Clipped objective
            surr1 = ratio * advantages
            surr2 = torch.clamp(ratio, 1 - self.epsilon, 1 + self.epsilon) * advantages
            
            # [AGENT-EDIT] PPO 목적함수 부호 수정 (advantage 증가 방향)
            actor_loss = -torch.min(surr1, surr2).mean()
            
            # 🔥 엔트로피 Loss 계산 (새로운 방식)
            entropy_loss = calculate_entropy(new_log_probs)
            total_entropy_loss += entropy_loss.item()  # 🔥 누적
            
            # 🔥 Total Loss (Policy Loss - 엔트로피 보너스)
            loss = actor_loss - self.current_entropy_coeff * entropy_loss
            
            # 역전파
            self.optimizer.zero_grad()
            loss.backward()
            
            # 그래디언트 클리핑
            torch.nn.utils.clip_grad_norm_(self.actor.parameters(), self.grad_clip)
            
            # 파라미터 업데이트
            self.optimizer.step()
            
            total_loss += loss.item()
        
        # 🔥 평균 Loss 반환 (Policy Loss, Entropy Loss)
        avg_policy_loss = total_loss / self.ppo_iterations
        avg_entropy_loss = total_entropy_loss / self.ppo_iterations
        
        return avg_policy_loss, avg_entropy_loss
    
    def should_update_baseline(self, 
                               test_blocks: List[List],
                               test_metadata: List[Dict],
                               num_tests: int = 3,
                               update_csv_path: str = None) -> bool:  # 🔥 CSV 로깅 추가
        """
        통계적 검정으로 baseline 업데이트 여부 결정
        """
        # [AGENT-EDIT] Heuristic baseline is fixed; no actor baseline updates.
        print(f"\n{'='*50}")
        print("🔬 Baseline Update Check (Heuristic baseline fixed)")
        print(f"{'='*50}")
        if update_csv_path:
            import csv
            with open(update_csv_path, 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    self.episode_count,           # episode
                    True,                         # check_triggered
                    "",                           # current_mean
                    "",                           # current_std
                    "",                           # baseline_mean
                    "",                           # baseline_std
                    "",                           # t_statistic
                    "",                           # p_value
                    f"{self.statistical_alpha:.3f}", # statistical_alpha
                    False,                        # is_significant
                    False,                        # is_better
                    False,                        # updated
                    self.baseline_update_count,   # total_updates
                    "heuristic_baseline_fixed"    # update_reason
                ])
        print(f"{'='*50}\n")
        return False
    
    def _compute_episode_start_date(self, blocks: List) -> str:
        """
        블록 데이터의 최소 조립 착수일을 기반으로 기준일 계산
        """
        dates = []
        for block in blocks:
            date = getattr(block, 'assembly_start_date', None)
            if date is None:
                date = getattr(block, 'max_start_date', None)
            if isinstance(date, datetime):
                dates.append(date)
            elif isinstance(date, str):
                try:
                    dates.append(datetime.strptime(date, "%Y-%m-%d"))
                except ValueError:
                    continue
        if not dates:
            return self._generate_random_start_date(datetime.now().year)
        earliest = min(dates)
        start_dt = earliest - timedelta(days=self.start_date_offset_days)
        return start_dt.strftime("%Y-%m-%d")

    def compute_start_date(self, blocks: List) -> str:
        """외부 호출용 기준일 계산"""
        return self._compute_episode_start_date(blocks)

    def _generate_random_start_date(self, year: int) -> str:
        """랜덤 시작 날짜 생성"""
        start = datetime(year, 1, 1)
        end = datetime(year, 12, 31)
        random_date = start + timedelta(
            days=random.randint(0, (end - start).days)
        )
        return random_date.strftime("%Y-%m-%d")
    
    def save_model(self, path: str):
        """모델 저장"""
        checkpoint = {
            'actor_state_dict': self.actor.state_dict(),
            'optimizer_state_dict': self.optimizer.state_dict() if self.optimizer is not None else None,
            'episode': self.episode_count,
            'baseline_updates': self.baseline_update_count,
            'optimizer_type': self.optimizer_type,
            'resume_metadata': copy.deepcopy(self.resume_metadata),
        }
        if self.actor_params_for_resume:
            checkpoint['actor_params'] = copy.deepcopy(self.actor_params_for_resume)
        torch.save(checkpoint, path)
        print(f"✅ 모델 저장: {path}")
    
    def load_model(self, path: str):
        """모델 로드"""
        checkpoint = torch.load(path, map_location=self.device)
        self.actor.load_state_dict(checkpoint['actor_state_dict'])
        optimizer_state = checkpoint.get('optimizer_state_dict')
        if self.optimizer is not None and optimizer_state is not None:
            self.optimizer.load_state_dict(optimizer_state)
        self.episode_count = checkpoint.get('episode', 0)
        self.baseline_update_count = checkpoint.get('baseline_updates', 0)
        self.resume_metadata = copy.deepcopy(checkpoint.get('resume_metadata', self.resume_metadata))
        actor_params = checkpoint.get('actor_params')
        if isinstance(actor_params, dict) and actor_params:
            self.actor_params_for_resume = copy.deepcopy(actor_params)
        print(f"✅ 모델 로드: {path}")
        return self.episode_count
