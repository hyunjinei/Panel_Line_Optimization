#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
PPO with Rollout Baseline 학습 스크립트
======================================
"""

import argparse
import torch
import numpy as np
import random
import os
from pathlib import Path
import math
import time
import pandas as pd
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

import sys

# [AGENT-EDIT] Direct script execution needs the repository root for absolute
# package imports such as `PPO.train...`, `utils...`, and `enhanced_environment...`.
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from PPO.train.assembly_rollout import AssemblyPPORollout
from utils.optimized_block_generator import OptimizedBlockGenerator
from enhanced_environment.common.utils_core import DataConverter
from PPO.models.single_step_actor import (
    ENV_STATE_DIM,
    BLOCK_FEATURE_DIM,
    REDUCED_ENV_STATE_DIM,
    REDUCED_BLOCK_FEATURE_DIM,
    CONSTRAINT_ENV_STATE_DIM,
    CONSTRAINT_BLOCK_FEATURE_DIM,
    DIFF_ENV_STATE_DIM,
    DIFF_BLOCK_FEATURE_DIM,
)
from runtime_config import get_runtime_config  # [AGENT-ADD] config.yaml 기반 학습 파라미터 반영
# [AGENT-EDIT] 분리된 유틸 모듈 import
from PPO.train.helpers import (
    _format_sequence_preview,
    _safe_value,
    _compare_sequences,
    _format_mismatch_summary,
    _format_daily_lines,
    _format_violation_lines,
    set_seed,
)
from PPO.train.data_utils import (
    _sample_util_bucket,
    _compute_spread_days,
    _adjust_reserved_counts,
    _apply_generated_data_variant,
)

# [AGENT-EDIT] Training distribution: 완화된 난이도 + 블록수 50~90에 맞춘 범위
TRAIN_UTIL_BUCKETS = [
    {"name": "normal", "ratio": 0.70, "util_range": (0.90, 1.05)},
    {"name": "overload", "ratio": 0.25, "util_range": (1.05, 1.20)},
    {"name": "heavy_overload", "ratio": 0.05, "util_range": (1.20, 1.30)},
]
TRAIN_PS_RATIO_RANGE = (0.0, 0.35)
TRAIN_SUB_RATIO_RANGE = (0.0, 0.35)
TRAIN_SEAM_SCALE_RANGE = (1.0, 1.15)
TRAIN_TACT_TIME_SCALE_RANGE = (1.0, 1.15)
TRAIN_LENGTH_SCALE_RANGE = (0.95, 1.05)
TRAIN_WIDTH_SCALE_RANGE = (0.95, 1.05)
TRAIN_THICKNESS_SCALE_RANGE = (0.95, 1.10)
def get_actor_params(args):
    """Actor 파라미터 설정 (args에서 가져오기)"""
    feature_mode = (args.feature_mode or "full").lower().strip()
    if feature_mode == "reduced":
        feature_dim = REDUCED_BLOCK_FEATURE_DIM
        env_state_dim = REDUCED_ENV_STATE_DIM
    elif feature_mode == "constraint":
        feature_dim = CONSTRAINT_BLOCK_FEATURE_DIM
        env_state_dim = CONSTRAINT_ENV_STATE_DIM
    elif feature_mode == "diff":
        feature_dim = DIFF_BLOCK_FEATURE_DIM
        env_state_dim = DIFF_ENV_STATE_DIM
    else:
        feature_dim = BLOCK_FEATURE_DIM
        env_state_dim = ENV_STATE_DIM
    return {
        'embedding_dim': args.embedding_dim,
        'hidden_dim': args.hidden_dim,
        'n_layers': args.n_layers,
        'n_heads': args.n_heads,
        'dropout': args.dropout,
        'use_logit_clipping': True,
        'C': 10.0,
        'T': args.temperature,
        # [AGENT-EDIT] block/env feature dimension must match feature_mode.
        'feature_dim': feature_dim,
        'feature_mode': feature_mode,
        'use_positional_encoding': bool(args.use_positional_encoding),
        'env_state_dim': env_state_dim,
        'use_norm': args.use_norm
    }


RESUME_RESTORE_KEYS = [
    'mode', 'embedding_dim', 'hidden_dim', 'n_layers', 'n_heads', 'use_norm',
    'dropout', 'temperature', 'optimizer', 'lr', 'epsilon', 'ppo_iterations',
    'grad_clip', 'baseline_update_interval', 'statistical_alpha', 'entropy_coeff',
    'entropy_decay', 'min_entropy_coeff', 'entropy_decay_interval',
    'initial_entropy_coeff', 'entropy_warmup_episodes', 'num_blocks',
    'train_distribution', 'num_blocks_min', 'num_blocks_max', 'max_daily_blocks',
    'min_spread_days', 'use_snu', 'use_env_state', 'feature_mode',
    'use_positional_encoding', 'fixed_norm_stats', 'fixed_norm_warmup_episodes',
    'self_label_samples', 'self_label_mode', 'self_label_selection_strategy',
    'self_label_lpt_teacher_policy', 'self_label_lpt_warmup_episodes',
    'self_label_lpt_teacher_bias_on', 'self_label_profile_expansion',
    'self_label_heuristic_profile_expansion', 'self_label_print_best_updates',
    'violation_penalty_weight', 'longi_balance_weight', 'start_date_offset'
]


def _extract_actor_state_dict_from_checkpoint(checkpoint: Dict[str, Any]) -> Dict[str, Any]:
    if isinstance(checkpoint, dict):
        if 'actor_state_dict' in checkpoint:
            return checkpoint['actor_state_dict']
        if 'model_state_dict' in checkpoint:
            return checkpoint['model_state_dict']
    return checkpoint if isinstance(checkpoint, dict) else {}


def _infer_feature_mode_from_dim(dim: Optional[int]) -> str:
    if dim == REDUCED_BLOCK_FEATURE_DIM:
        return 'reduced'
    if dim == CONSTRAINT_BLOCK_FEATURE_DIM:
        return 'constraint'
    if dim == DIFF_BLOCK_FEATURE_DIM:
        return 'diff'
    if dim == BLOCK_FEATURE_DIM:
        return 'full'
    return 'constraint'


def _infer_resume_args_from_actor_state(state_dict: Dict[str, Any]) -> Dict[str, Any]:
    inferred: Dict[str, Any] = {}
    embed_w = state_dict.get('embedding.0.weight')
    embed2_w = state_dict.get('embedding.2.weight')
    if embed_w is not None:
        inferred['embedding_dim'] = int(embed_w.shape[0])
        inferred['feature_dim'] = int(embed_w.shape[1])
    if embed2_w is not None:
        inferred['hidden_dim'] = int(embed2_w.shape[0])
    inferred['use_env_state'] = 'env_state_mean' in state_dict
    inferred['use_positional_encoding'] = 'positional_encoding' in state_dict
    if 'feature_dim' in inferred:
        inferred['feature_mode'] = _infer_feature_mode_from_dim(inferred['feature_dim'])
    return inferred


def _collect_explicit_cli_dests(parser: argparse.ArgumentParser, cli_argv: List[str]) -> set:
    explicit = set()
    option_map = getattr(parser, '_option_string_actions', {})
    for token in cli_argv:
        if not isinstance(token, str) or not token.startswith('--'):
            continue
        option = token.split('=', 1)[0]
        action = option_map.get(option)
        if action is not None:
            explicit.add(action.dest)
    return explicit


def _load_resume_defaults_from_checkpoint(path_str: str) -> Tuple[Dict[str, Any], str, int]:
    checkpoint = torch.load(path_str, map_location='cpu')
    restored: Dict[str, Any] = {}
    source = 'checkpoint_inferred'
    metadata = checkpoint.get('resume_metadata') if isinstance(checkpoint, dict) else None
    if isinstance(metadata, dict) and metadata:
        source = 'checkpoint_metadata'
        meta_args = metadata.get('args') or {}
        if isinstance(meta_args, dict):
            restored.update(meta_args)
        actor_meta = metadata.get('actor_params') or {}
        if isinstance(actor_meta, dict):
            restored.setdefault('embedding_dim', actor_meta.get('embedding_dim'))
            restored.setdefault('hidden_dim', actor_meta.get('hidden_dim'))
            restored.setdefault('n_layers', actor_meta.get('n_layers'))
            restored.setdefault('n_heads', actor_meta.get('n_heads'))
            restored.setdefault('dropout', actor_meta.get('dropout'))
            restored.setdefault('temperature', actor_meta.get('T'))
            restored.setdefault('feature_mode', actor_meta.get('feature_mode'))
            restored.setdefault('use_positional_encoding', actor_meta.get('use_positional_encoding'))
            restored.setdefault('use_env_state', actor_meta.get('use_env_state'))
            restored.setdefault('use_norm', actor_meta.get('use_norm'))
    if not restored:
        actor_state = _extract_actor_state_dict_from_checkpoint(checkpoint)
        inferred = _infer_resume_args_from_actor_state(actor_state)
        restored.update({
            'embedding_dim': inferred.get('embedding_dim'),
            'hidden_dim': inferred.get('hidden_dim'),
            'feature_mode': inferred.get('feature_mode'),
            'use_env_state': inferred.get('use_env_state'),
            'use_positional_encoding': inferred.get('use_positional_encoding'),
        })
    if isinstance(checkpoint, dict) and checkpoint.get('optimizer_type'):
        restored.setdefault('optimizer', checkpoint.get('optimizer_type'))
    saved_episode = int(checkpoint.get('episode', 0)) if isinstance(checkpoint, dict) else 0
    return restored, source, saved_episode


def _apply_resume_defaults_from_checkpoint(parsed_args, parser: argparse.ArgumentParser, cli_argv: List[str]) -> None:
    load_model = str(getattr(parsed_args, 'load_model', '') or '').strip()
    if not load_model or not os.path.exists(load_model):
        return
    try:
        restored, source, saved_episode = _load_resume_defaults_from_checkpoint(load_model)
    except Exception as exc:
        print(f"⚠️ 이어학습 메타데이터 로드 실패: {exc}")
        return

    explicit_dests = _collect_explicit_cli_dests(parser, cli_argv)
    restored_keys: List[str] = []
    for key, value in restored.items():
        if value is None or not hasattr(parsed_args, key):
            continue
        if key in explicit_dests:
            continue
        if key == 'mode' and value == 'self-label':
            value = 'self_label'
        setattr(parsed_args, key, value)
        restored_keys.append(key)

    parsed_args._resume_source = source
    parsed_args._resume_saved_episode = saved_episode
    if restored_keys:
        print(f"📦 이어학습 설정 복원: {load_model}")
        print(f"   - source: {source}")
        print(f"   - saved episode: {saved_episode}")
        print(f"   - restored: {', '.join(restored_keys)}")
        if source != 'checkpoint_metadata':
            print("   - note: 구형 체크포인트라 일부 값은 추론으로 복원했습니다.")


def _build_resume_metadata(args, actor_params: Dict[str, Any]) -> Dict[str, Any]:
    arg_snapshot: Dict[str, Any] = {}
    for key in RESUME_RESTORE_KEYS:
        if hasattr(args, key):
            arg_snapshot[key] = getattr(args, key)
    actor_snapshot = dict(actor_params or {})
    actor_snapshot['use_env_state'] = bool(getattr(args, 'use_env_state', False))
    actor_snapshot['use_norm'] = bool(getattr(args, 'use_norm', False))
    return {
        'version': 1,
        'saved_at': datetime.now().isoformat(timespec='seconds'),
        'command': ' '.join(sys.argv),
        'args': arg_snapshot,
        'actor_params': actor_snapshot,
    }


def _extract_assembly_date_range(df: Optional[pd.DataFrame]) -> Tuple[Optional[str], Optional[str]]:
    """[AGENT-ADD] Extract min/max assembly date tokens for distribution logging."""
    if df is None or '조립착수일' not in df.columns:
        return None, None
    series = pd.to_numeric(df['조립착수일'], errors='coerce').dropna()
    if series.empty:
        return None, None
    min_date = int(series.min())
    max_date = int(series.max())
    return str(min_date), str(max_date)

def _resolve_excel_path(path_str: Optional[str]) -> Path:
    """[AGENT-ADD] Resolve excel path relative to repo root."""
    base_dir = Path(__file__).resolve().parents[2]  # repo root
    if not path_str:
        return base_dir / 'environment' / '판넬 블록 데이터셋_250618_SNU.xlsx'
    candidate = Path(path_str)
    if not candidate.is_absolute():
        candidate = base_dir / candidate
    return candidate


def generate_test_datasets(generator, num_datasets=20, use_snu=True):
    """테스트용 데이터셋 미리 생성 (평가용이므로 SNU 데이터 사용)"""
    test_blocks = []
    test_metadata = []

    if use_snu:
        cfg = get_runtime_config() or {}
        data_cfg = cfg.get("data", {}) if isinstance(cfg, dict) else {}
        sheet_name = data_cfg.get("sheet") if isinstance(data_cfg, dict) else None
        excel_path = _resolve_excel_path(
            data_cfg.get("excel_path") if isinstance(data_cfg, dict) else None
        )
        # SNU 데이터는 동일하므로 한 번만 로드하고 복사
        snu_blocks, snu_metadata = DataConverter.excel_to_blocks_with_metadata(
            str(excel_path),
            sheet_name=sheet_name
        )
        for _ in range(num_datasets):
            test_blocks.append(snu_blocks)
            test_metadata.append(snu_metadata)
    else:
        for _ in range(num_datasets):
            blocks_data = generator.generate_blocks_with_ps_pairs_configurable(
                total_blocks=50,
                ps_pairs_count=None,
                subassembly_groups=None
            )
            blocks, metadata = DataConverter.dataframe_to_blocks_with_metadata(blocks_data)
            test_blocks.append(blocks)
            test_metadata.append(metadata)
    
    return test_blocks, test_metadata

def generate_baseline_test_datasets(generator):
    """🔥 Baseline 검정용 3문제 데이터셋 생성"""
    test_blocks = []
    test_metadata = []

    cfg = get_runtime_config() or {}
    data_cfg = cfg.get("data", {}) if isinstance(cfg, dict) else {}
    sheet_name = data_cfg.get("sheet") if isinstance(data_cfg, dict) else None
    snu_excel_path = _resolve_excel_path(
        data_cfg.get("excel_path") if isinstance(data_cfg, dict) else None
    )

    # 1️⃣ SNU 데이터 (250618)
    snu_blocks_1, snu_metadata_1 = DataConverter.excel_to_blocks_with_metadata(
        str(snu_excel_path),
        sheet_name=sheet_name
    )
    test_blocks.append(snu_blocks_1)
    test_metadata.append(snu_metadata_1)

    # 2️⃣ 2차년도 데이터
    second_year_path = _resolve_excel_path('environment/판넬 블록 데이터셋_2차년도예시.xlsx')
    if second_year_path.exists():
        snu_blocks_2, snu_metadata_2 = DataConverter.excel_to_blocks_with_metadata(
            str(second_year_path),
            sheet_name=sheet_name
        )
        test_blocks.append(snu_blocks_2)
        test_metadata.append(snu_metadata_2)
    else:
        print(f"⚠️ 2차년도 데이터셋이 없어 제외합니다: {second_year_path}")
    
    # 3️⃣ 생성 데이터 (1개)
    blocks_data = generator.generate_blocks_with_ps_pairs_configurable(
        total_blocks=50,
        ps_pairs_count=None,
        subassembly_groups=None
    )
    gen_blocks, gen_metadata = DataConverter.dataframe_to_blocks_with_metadata(blocks_data)
    test_blocks.append(gen_blocks)
    test_metadata.append(gen_metadata)
    
    print("🔥 Baseline 검정용 데이터셋 구성:")
    print("   1️⃣ SNU 데이터 (250618)")
    print("   2️⃣ 2차년도 데이터")  
    print("   3️⃣ 생성 데이터 (1개)")
    
    return test_blocks, test_metadata

def main(args):
    # [AGENT-EDIT] Step-level RL 로그 기본 활성화 (환경변수로 끌 수 있음)
    os.environ.setdefault("PBS_RL_STEP_LOG", "0")
    # [AGENT-ADD] 마스킹 디버그 플래그를 CLI에서 제어
    if getattr(args, "masking_debug", False):
        os.environ["PBS_FORCE_DEBUG"] = "1"
    if getattr(args, "masking_debug_verbose", False):
        os.environ["PBS_DEBUG_VERBOSE"] = "1"
        os.environ.setdefault("PBS_FORCE_DEBUG", "1")
    # [AGENT-ADD] Torch CPU thread controls for server experiments. Candidate parallelism uses
    # process workers, so the parent PyTorch thread count is auto-capped to avoid oversubscription.
    cpu_count = os.cpu_count() or 1
    if getattr(args, "torch_num_threads", 0):
        try:
            torch.set_num_threads(max(1, int(args.torch_num_threads)))
        except Exception as exc:
            print(f"⚠️ torch_num_threads 설정 실패: {exc}")
    elif bool(getattr(args, "resource_auto", 1)):
        try:
            torch.set_num_threads(max(1, min(8, cpu_count // 4 or 1)))
        except Exception as exc:
            print(f"⚠️ torch_num_threads 자동 설정 실패: {exc}")
    if getattr(args, "torch_num_interop_threads", 0):
        try:
            torch.set_num_interop_threads(max(1, int(args.torch_num_interop_threads)))
        except Exception as exc:
            print(f"⚠️ torch_num_interop_threads 설정 실패: {exc}")
    elif bool(getattr(args, "resource_auto", 1)):
        try:
            torch.set_num_interop_threads(max(1, min(4, cpu_count // 12 or 1)))
        except Exception as exc:
            print(f"⚠️ torch_num_interop_threads 자동 설정 실패: {exc}")
    # 시드 설정
    set_seed(args.seed)
    
    # 디바이스 설정
    device = torch.device('cuda' if torch.cuda.is_available() and not args.cpu else 'cpu')
    print(f"🖥️ Device: {device}")
    print(
        f"🧵 CPU/Torch threads: os_cpu={os.cpu_count()}, "
        f"torch={torch.get_num_threads()}, interop={torch.get_num_interop_threads()}"
    )
    if torch.cuda.is_available() and not args.cpu:
        try:
            print(f"🎮 CUDA device: {torch.cuda.get_device_name(0)}")
        except Exception:
            pass
    
    # 결과 폴더 생성
    from datetime import datetime
    import csv
    
    result_dir = os.path.join(os.path.dirname(__file__), 'result')
    os.makedirs(result_dir, exist_ok=True)
    log_dir = os.path.join(result_dir, 'log', 'ppo')
    os.makedirs(log_dir, exist_ok=True)
    eval_dir = os.path.join(result_dir, 'evaluation')
    os.makedirs(eval_dir, exist_ok=True)
    
    # 타임스탬프 생성
    timestamp = datetime.now().strftime('%m%d_%H_%M')
    
    # 🆕 실행별 모델 폴더 생성 (models/실행날짜_time_옵티마이저_env_state/)
    env_state_suffix = "envTrue" if args.use_env_state else "envFalse"
    # [AGENT-EDIT] feature_mode를 모델 폴더명에 포함해 full/diff 새 학습 결과를 혼동하지 않게 한다.
    session_folder_name = f"{timestamp}_{args.optimizer}_{args.feature_mode}_{env_state_suffix}"
    session_model_dir = os.path.join(result_dir, 'models', session_folder_name)
    os.makedirs(session_model_dir, exist_ok=True)
    
    # 🆕 실행별 평가 폴더 생성 (evaluation/실행날짜_time/)
    session_eval_dir = os.path.join(eval_dir, timestamp)
    os.makedirs(session_eval_dir, exist_ok=True)
    profiler_dir = args.torch_profile_dir or os.path.join(result_dir, 'profiler', timestamp)
    if args.torch_profile_episodes > 0:
        os.makedirs(profiler_dir, exist_ok=True)
    
    # CSV 파일 경로
    csv_paths = {
        'train': os.path.join(log_dir, f'{timestamp}_ppo_train.csv'),
        'detail': os.path.join(log_dir, f'{timestamp}_ppo_detail.csv'),
        'evaluation': os.path.join(log_dir, f'{timestamp}_ppo_evaluation.csv'),
        'update': os.path.join(log_dir, f'{timestamp}_ppo_update.csv'),  # 🔥 Baseline 업데이트 추적
        'distribution': os.path.join(log_dir, f'{timestamp}_ppo_distribution.csv')
    }
    
    # CSV 헤더 작성
    with open(csv_paths['train'], 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            'episode',
            'real_makespan',
            'baseline_makespan',
            'advantage',
            'actor_loss',
            'entropy_loss',
            'violations',
            'baseline_violations',
            'actor_longi_balance',
            'baseline_longi_balance',
            'actor_longi_a',
            'actor_longi_b',
            'actor_longi_total',
            'baseline_longi_a',
            'baseline_longi_b',
            'baseline_longi_total',
            'delta_makespan',
            'delta_violations',
            'delta_longi_balance',
            'update_applied',
            # [AGENT-ADD] self-label/LPT 업데이트 소스 표시
            'update_source'
        ])  # [AGENT-EDIT] 론지/개선지표/업데이트 로그 추가
    
    with open(csv_paths['detail'], 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['episode', 'step', 'block_id', 'confidence', 'log_prob', 'advantage'])
    
    with open(csv_paths['evaluation'], 'w', newline='') as f:
        writer = csv.writer(f)
        # [AGENT-EDIT] train_evaluation.csv 출력 컬럼과 정렬 (RL/Random/SPT/LPT/SEAM)
        writer.writerow([
            'episode',
            'rl_mean', 'rl_std', 'rl_min', 'rl_max',
            'random_mean', 'random_std',
            'spt_mean', 'spt_std',
            'lpt_mean', 'lpt_std',
            'seam_mean', 'seam_std',
            'rl_wins', 'random_wins', 'spt_wins', 'lpt_wins', 'seam_wins',
            'improvement_vs_random', 'improvement_vs_spt',
            'improvement_vs_lpt', 'improvement_vs_seam'
        ])
    
    # 🔥 Baseline 업데이트 추적 CSV 헤더
    with open(csv_paths['update'], 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow(['episode', 'check_triggered', 'current_mean', 'current_std', 
                        'baseline_mean', 'baseline_std', 't_statistic', 'p_value', 
                        'statistical_alpha', 'is_significant', 'is_better', 'updated', 
                        'total_updates', 'update_reason'])

    # [AGENT-ADD] Training distribution logging
    with open(csv_paths['distribution'], 'w', newline='') as f:
        writer = csv.writer(f)
        writer.writerow([
            'episode', 'train_distribution', 'total_blocks', 'converted_blocks',
            'util_bucket', 'util_target', 'util_min', 'util_max', 'spread_days',
            'ps_ratio_target', 'sub_ratio_target', 'ps_pairs', 'sub_groups',
            'seam_scale', 'tact_time_scale', 'length_scale', 'width_scale', 'thickness_scale',
            'assembly_date_min', 'assembly_date_max'
        ])
    
    # Actor 파라미터
    actor_params = get_actor_params(args)
    
    # PPO Rollout Trainer 초기화
    trainer = AssemblyPPORollout(
        actor_params=actor_params,
        device=device,
        lr=args.lr,
        epsilon=args.epsilon,
        ppo_iterations=args.ppo_iterations,
        baseline_update_interval=args.baseline_update_interval,
        statistical_alpha=args.statistical_alpha,
        grad_clip=args.grad_clip,
        use_env_state=args.use_env_state,  # 🆕 환경 상태 사용 여부
        start_date_change_interval=1,  # 🆕 기준일 변경 주기 (REINFORCE와 동일)
        # 🔥 엔트로피 파라미터 추가
        entropy_coeff=args.entropy_coeff,
        entropy_decay=args.entropy_decay,
        min_entropy_coeff=args.min_entropy_coeff,
        entropy_decay_interval=args.entropy_decay_interval,
        initial_entropy_coeff=args.initial_entropy_coeff,
        entropy_warmup_episodes=args.entropy_warmup_episodes,
        # 🔥 옵티마이저 선택 추가
        optimizer_type=args.optimizer,
        start_date_offset_days=args.start_date_offset,
        mode=args.mode,
        self_label_samples=args.self_label_samples,
        self_label_mode=args.self_label_mode,
        self_label_selection_strategy=args.self_label_selection_strategy,
        self_label_lpt_teacher_policy=args.self_label_lpt_teacher_policy,
        self_label_lpt_warmup_episodes=args.self_label_lpt_warmup_episodes,
        self_label_lpt_teacher_bias_on=bool(args.self_label_lpt_teacher_bias_on),
        self_label_profile_expansion=bool(args.self_label_profile_expansion),
        self_label_heuristic_profile_expansion=bool(args.self_label_heuristic_profile_expansion),
        self_label_print_best_updates=bool(args.self_label_print_best_updates),
        violation_penalty_weight=args.violation_penalty_weight,
        longi_balance_weight=args.longi_balance_weight,
        fixed_norm_stats=bool(args.fixed_norm_stats),
        fixed_norm_warmup_episodes=args.fixed_norm_warmup_episodes,
        perf_timing=bool(args.perf_timing),
        perf_top_k=args.perf_top_k,
        self_label_parallel=args.self_label_parallel,
        self_label_parallel_workers=args.self_label_parallel_workers,
        self_label_parallel_device=args.self_label_parallel_device,
        self_label_worker_threads=args.self_label_worker_threads,
        self_label_parallel_min_candidates=args.self_label_parallel_min_candidates,
        resume_metadata=_build_resume_metadata(args, actor_params)
    )
    
    # 모델 로드 (있으면)
    start_episode = 0
    if args.load_model and os.path.exists(args.load_model):
        start_episode = trainer.load_model(args.load_model)
        print(f"📂 모델 로드 완료: {args.load_model} (Episode {start_episode}부터 시작)")
    # [AGENT-EDIT] 이어학습에서 --episodes는 기본적으로 "추가 학습 횟수"로 해석한다.
    # 예: ep2600 로드 + --episodes 5000 => 최종 ep7600까지 학습.
    resume_episode_mode = str(getattr(args, "resume_episode_mode", "additional")).lower()
    if args.load_model and resume_episode_mode == "additional":
        end_episode = start_episode + max(0, int(args.episodes))
    else:
        end_episode = int(args.episodes)
    if args.train and end_episode <= start_episode:
        print(
            f"⚠️ 학습 목표 에피소드가 시작점보다 작거나 같습니다: "
            f"start={start_episode}, end={end_episode}"
        )
    
    # 블록 생성기
    generator = OptimizedBlockGenerator.load_from_saved_values()
    
    # 테스트 데이터셋 미리 생성 (평가용이므로 항상 SNU 사용)
    print("📊 평가용 SNU 데이터셋 생성 중...")
    test_blocks, test_metadata = generate_test_datasets(
        generator, 
        num_datasets=20, 
        use_snu=True  # 평가는 항상 SNU 데이터
    )
    print(f"✅ SNU 테스트 데이터셋 {len(test_blocks)}개 준비 완료")
    
    # 🔥 Baseline 검정용 데이터셋 생성 (3문제)
    print("🔬 Baseline 검정용 데이터셋 생성 중...")
    baseline_test_blocks, baseline_test_metadata = generate_baseline_test_datasets(generator)
    print(f"✅ Baseline 검정용 데이터셋 {len(baseline_test_blocks)}개 준비 완료")
    
    # 학습 루프
    if args.train:
        print("\n" + "="*60)
        print("🚀 PPO Rollout 학습 시작")
        print("="*60)
        print(f"📋 설정:")
        print(f"   - Learning Rate: {args.lr}")
        print(f"   - PPO Epsilon: {args.epsilon}")
        print(f"   - PPO Iterations: {args.ppo_iterations}")
        print(f"   - Baseline Update Interval: {args.baseline_update_interval}")
        print(f"   - Statistical Alpha: {args.statistical_alpha}")
        print(f"   - Optimizer: {args.optimizer}")
        print(f"   - Use Env State: {args.use_env_state}")
        print(f"   - Feature Mode: {args.feature_mode}")
        print(f"   - Positional Encoding: {args.use_positional_encoding}")
        print(f"   - Episode Range: {start_episode + 1} -> {end_episode} (mode={resume_episode_mode})")
        # [AGENT-ADD] 마스킹 디버그 환경변수 상태 출력
        print(f"   - Masking Debug: {os.environ.get('PBS_FORCE_DEBUG', '')} (verbose={os.environ.get('PBS_DEBUG_VERBOSE', '')})")
        print(f"   - Model Folder: {session_folder_name}")
        print("="*60)
        
        best_makespan = float('inf')
        all_makespans = []  # 🔥 전체 makespan 기록
        all_advantages = []  # 🔥 전체 advantage 기록
        all_entropy_losses = []  # 🔥 전체 엔트로피 Loss 기록
        
        for episode in range(start_episode, end_episode):
            # [AGENT-ADD] 사용자 출력/평가 주기는 1-based episode 기준으로 통일한다.
            display_episode = episode + 1
            # 학습 시에는 항상 랜덤 블록 생성
            util_bucket = None
            util_target = None
            util_range = (None, None)
            spread_days = None
            ps_ratio_target = None
            sub_ratio_target = None
            ps_pairs = None
            sub_groups = None
            seam_scale = None
            tact_time_scale = None
            length_scale = None
            width_scale = None
            thickness_scale = None

            if args.train_distribution == "eval_like":
                total_blocks = random.randint(args.num_blocks_min, args.num_blocks_max)
                util_bucket, util_target, util_range = _sample_util_bucket(TRAIN_UTIL_BUCKETS)
                spread_days = _compute_spread_days(
                    total_blocks,
                    util_target,
                    args.max_daily_blocks,
                    args.min_spread_days
                )
                ps_ratio_target = random.uniform(TRAIN_PS_RATIO_RANGE[0], TRAIN_PS_RATIO_RANGE[1])
                sub_ratio_target = random.uniform(TRAIN_SUB_RATIO_RANGE[0], TRAIN_SUB_RATIO_RANGE[1])
                ps_pairs = int(round(total_blocks * ps_ratio_target / 2))
                sub_groups = int(round(total_blocks * sub_ratio_target / 2))
                ps_pairs, sub_groups = _adjust_reserved_counts(total_blocks, ps_pairs, sub_groups, min_basic_blocks=1)
                seam_scale = random.uniform(TRAIN_SEAM_SCALE_RANGE[0], TRAIN_SEAM_SCALE_RANGE[1])
                tact_time_scale = random.uniform(TRAIN_TACT_TIME_SCALE_RANGE[0], TRAIN_TACT_TIME_SCALE_RANGE[1])
                length_scale = random.uniform(TRAIN_LENGTH_SCALE_RANGE[0], TRAIN_LENGTH_SCALE_RANGE[1])
                width_scale = random.uniform(TRAIN_WIDTH_SCALE_RANGE[0], TRAIN_WIDTH_SCALE_RANGE[1])
                thickness_scale = random.uniform(TRAIN_THICKNESS_SCALE_RANGE[0], TRAIN_THICKNESS_SCALE_RANGE[1])
                generator.assembly_date_config['spread_days'] = spread_days
                blocks_data = generator.generate_blocks_with_ps_pairs_configurable(
                    total_blocks=total_blocks,
                    ps_pairs_count=ps_pairs,
                    subassembly_groups=sub_groups
                )
                blocks_data = _apply_generated_data_variant(
                    blocks_data,
                    seam_scale=seam_scale,
                    tact_time_scale=tact_time_scale,
                    length_scale=length_scale,
                    width_scale=width_scale,
                    thickness_scale=thickness_scale
                )
            else:
                blocks_data = generator.generate_blocks_with_ps_pairs_configurable(
                    total_blocks=args.num_blocks,
                    ps_pairs_count=None,
                    subassembly_groups=None
                )
                total_blocks = args.num_blocks
                spread_days = generator.assembly_date_config.get('spread_days')
            if args.train_distribution == "eval_like":
                print(
                    f"[DEBUG] Episode {display_episode}: blocks={total_blocks}, bucket={util_bucket}, util={util_target:.2f} "
                    f"(range {util_range[0]:.2f}-{util_range[1]:.2f}), spread_days={spread_days}, "
                    f"ps_ratio={ps_ratio_target:.2f}, sub_ratio={sub_ratio_target:.2f}, "
                    f"scales(seam/tact/len/width/thick)={seam_scale:.2f}/{tact_time_scale:.2f}/"
                    f"{length_scale:.2f}/{width_scale:.2f}/{thickness_scale:.2f}"
                )
            print(f"[DEBUG] Episode {display_episode}: generated dataframe rows = {len(blocks_data)}")
            blocks, metadata = DataConverter.dataframe_to_blocks_with_metadata(blocks_data)
            print(f"[DEBUG] Episode {display_episode}: converted blocks = {len(blocks)}")

            # [AGENT-ADD] Distribution logging
            assembly_min, assembly_max = _extract_assembly_date_range(blocks_data)
            with open(csv_paths['distribution'], 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    episode,
                    args.train_distribution,
                    total_blocks,
                    len(blocks),
                    util_bucket,
                    util_target,
                    util_range[0] if util_range[0] is not None else None,
                    util_range[1] if util_range[1] is not None else None,
                    spread_days,
                    ps_ratio_target,
                    sub_ratio_target,
                    ps_pairs,
                    sub_groups,
                    seam_scale,
                    tact_time_scale,
                    length_scale,
                    width_scale,
                    thickness_scale,
                    assembly_min,
                    assembly_max
                ])
            
            # 에피소드 학습 (mode에 따라 PPO / self-label 분기)
            # [AGENT-ADD] Optional wall-clock and torch profiler diagnostics around the whole training episode.
            episode_wall_t0 = time.perf_counter()

            def _run_train_episode():
                if args.mode == "self_label":
                    print(f"[DEBUG] Episode {display_episode}: invoking train_episode_self_label with {len(blocks)} blocks (samples={args.self_label_samples}, mode={args.self_label_mode})")
                    return trainer.train_episode_self_label(blocks, metadata)
                print(f"[DEBUG] Episode {display_episode}: invoking train_episode_ppo with {len(blocks)} blocks")
                return trainer.train_episode_ppo(blocks, metadata)

            profile_this_episode = bool(args.torch_profile_episodes > 0 and len(all_makespans) < args.torch_profile_episodes)
            if profile_this_episode:
                from torch.profiler import ProfilerActivity, profile
                activities = [ProfilerActivity.CPU]
                if torch.cuda.is_available() and not args.cpu:
                    activities.append(ProfilerActivity.CUDA)
                with profile(
                    activities=activities,
                    record_shapes=True,
                    profile_memory=True,
                    with_stack=False,
                ) as prof:
                    stats = _run_train_episode()
                if torch.cuda.is_available() and not args.cpu:
                    torch.cuda.synchronize()
                trace_path = os.path.join(profiler_dir, f"episode_{display_episode}.json")
                prof.export_chrome_trace(trace_path)
                sort_key = "self_cuda_time_total" if torch.cuda.is_available() and not args.cpu else "self_cpu_time_total"
                print(f"[Profiler] trace saved: {trace_path}")
                print(prof.key_averages().table(sort_by=sort_key, row_limit=20))
            else:
                stats = _run_train_episode()
                if torch.cuda.is_available() and not args.cpu:
                    torch.cuda.synchronize()

            if args.perf_timing:
                print(f"[Perf] episode_wall={time.perf_counter() - episode_wall_t0:.2f}s")
            
            # CSV 저장 - train
            # [AGENT-ADD] LPT 대비 개선치/론지 로그 계산
            baseline_violations = stats.get('baseline_violations', stats.get('violations', 0))
            actor_longi_balance = stats.get('actor_longi_balance', 0.0) or 0.0
            baseline_longi_balance = stats.get('baseline_longi_balance', actor_longi_balance) or 0.0
            actor_longi_a = stats.get('actor_longi_a', 0)
            actor_longi_b = stats.get('actor_longi_b', 0)
            actor_longi_total = stats.get('actor_longi_total', 0)
            baseline_longi_a = stats.get('baseline_longi_a', 0)
            baseline_longi_b = stats.get('baseline_longi_b', 0)
            baseline_longi_total = stats.get('baseline_longi_total', 0)
            try:
                delta_makespan = float(stats['baseline_makespan']) - float(stats['real_makespan'])
            except (TypeError, ValueError):
                delta_makespan = 0.0
            try:
                delta_violations = int(baseline_violations) - int(stats['violations'])
            except (TypeError, ValueError):
                delta_violations = 0
            try:
                delta_longi_balance = float(baseline_longi_balance) - float(actor_longi_balance)
            except (TypeError, ValueError):
                delta_longi_balance = 0.0
            update_applied = bool(stats.get('update_applied', False))
            if args.mode == "self_label":
                update_source = stats.get('update_source', 'self_label')
            else:
                update_source = stats.get('update_source', 'ppo')

            with open(csv_paths['train'], 'a', newline='') as f:
                writer = csv.writer(f)
                writer.writerow([
                    episode,
                    stats['real_makespan'],
                    stats['baseline_makespan'],
                    stats['advantage'],
                    stats['actor_loss'],
                    stats['entropy_loss'],  # 🔥 엔트로피 Loss 추가
                    stats['violations'],
                    baseline_violations,
                    actor_longi_balance,
                    baseline_longi_balance,
                    actor_longi_a,
                    actor_longi_b,
                    actor_longi_total,
                    baseline_longi_a,
                    baseline_longi_b,
                    baseline_longi_total,
                    delta_makespan,
                    delta_violations,
                    delta_longi_balance,
                    update_applied,
                    update_source
                ])
            
            # CSV 저장 - detail (episode_data가 있으면)
            if 'episode_data' in stats and stats['episode_data']:
                with open(csv_paths['detail'], 'a', newline='') as f:
                    writer = csv.writer(f)
                    for step_idx, step_data in enumerate(stats['episode_data']):
                        if 'selected_block_id' in step_data:
                            writer.writerow([
                                episode,
                                step_idx,
                                step_data.get('selected_block_id', -1),
                                step_data.get('confidence', 0.0),
                                step_data.get('log_prob', 0.0) if hasattr(step_data.get('log_prob'), 'item') else step_data.get('log_prob', 0.0),
                                stats['advantage']  # 전체 advantage 사용
                            ])
            
            # 🔥 전체 통계 업데이트
            all_makespans.append(stats['real_makespan'])
            all_advantages.append(stats['advantage'])
            all_entropy_losses.append(stats['entropy_loss'])  # 🔥 엔트로피 Loss 누적
            
            # 로깅
            if episode == start_episode or display_episode % args.log_interval == 0:
                diagnostics = stats.get('diagnostics') or {}
                actor_diag = diagnostics.get('actor') or {}
                baseline_diag = diagnostics.get('baseline') or {}

                actor_seq = actor_diag.get('sequence') or []
                baseline_seq = baseline_diag.get('sequence') or []
                seq_diff = _compare_sequences(actor_seq, baseline_seq, top_k=5)
                mismatch_summary = _format_mismatch_summary(seq_diff['top_mismatches'])
                first_mismatch = seq_diff['first_mismatch']

                actor_sequence_preview = _format_sequence_preview(actor_seq)
                baseline_sequence_preview = _format_sequence_preview(baseline_seq)

                actor_daily_lines = _format_daily_lines(actor_diag.get('daily_makespans') or [])
                baseline_daily_lines = _format_daily_lines(baseline_diag.get('daily_makespans') or [])

                actor_violation_lines = _format_violation_lines(
                    actor_diag.get('violation_details') or [],
                    limit=args.violation_print_limit
                )
                baseline_violation_lines = _format_violation_lines(
                    baseline_diag.get('violation_details') or [],
                    limit=args.violation_print_limit
                )

                baseline_stats = baseline_diag.get('statistics') or {}
                baseline_violation_count = baseline_stats.get('total_violations', 0)
                baseline_final_makespan = baseline_diag.get('makespan', stats['baseline_makespan'])
                # [AGENT-ADD] 론지 균형 상세
                actor_longi_balance = actor_diag.get('longi_balance', 0.0)
                actor_longi_a = actor_diag.get('longi_a', 0)
                actor_longi_b = actor_diag.get('longi_b', 0)
                actor_longi_total = actor_diag.get('longi_total', 0)
                baseline_longi_balance = baseline_diag.get('longi_balance', 0.0)
                baseline_longi_a = baseline_diag.get('longi_a', 0)
                baseline_longi_b = baseline_diag.get('longi_b', 0)
                baseline_longi_total = baseline_diag.get('longi_total', 0)
                try:
                    delta_makespan_log = float(stats['baseline_makespan']) - float(stats['real_makespan'])
                except (TypeError, ValueError):
                    delta_makespan_log = 0.0
                try:
                    delta_violations_log = int(baseline_violations) - int(stats['violations'])
                except (TypeError, ValueError):
                    delta_violations_log = 0
                try:
                    delta_longi_log = float(baseline_longi_balance) - float(actor_longi_balance)
                except (TypeError, ValueError):
                    delta_longi_log = 0.0

                print(f"\n Episode {display_episode}/{end_episode}")
                print(f"  Advantage: {stats['advantage']:.2f}")
                print(f"  Actor Loss: {stats['actor_loss']:.4f}")
                print(f"  Entropy Loss: {stats['entropy_loss']:.4f}")  # 🔥 엔트로피 Loss 출력
                print(f"  Violations: {stats['violations']}")
                # [AGENT-ADD] 단일 라인 요약을 추가해 학습 추이를 빠르게 비교한다.
                print(
                    f"[SingleTrain] ep={display_episode} actor_score={float(stats.get('actor_score', 0.0)):.3f} "
                    f"baseline_score={float(stats.get('baseline_score', 0.0)):.3f} "
                    f"makespan={float(stats.get('real_makespan', 0.0)):.2f}h "
                    f"primary={int(stats.get('violations', 0))}"
                )
                
                # 🔥 전체 평균 출력
                if len(all_makespans) >= 1:
                    print(f"  Total Avg Makespan: {np.mean(all_makespans):.2f}h")
                    print(f"  Total Avg Advantage: {np.mean(all_advantages):.2f}")
                    print(f"  Total Avg Entropy Loss: {np.mean(all_entropy_losses):.4f}")  # 🔥 평균 엔트로피 Loss
                    print(f"  Episodes Count: {len(all_makespans)}")

                print("")
                print(f"  시퀀스 길이: actor={len(actor_seq)}, baseline={len(baseline_seq)}, 동일길이={seq_diff['same_length']}")
                if first_mismatch is None:
                    print("  첫 불일치 index=None (완전 일치)")
                else:
                    print(
                        f"  첫 불일치 index={first_mismatch['index']} (actor={_safe_value(first_mismatch['actor'])}, "
                        f"baseline={_safe_value(first_mismatch['baseline'])})"
                    )
                print(f"  불일치 상위 5개 → {mismatch_summary}")

                print("  [Current Actor]")
                print(f"    Sequence (first 30): {actor_sequence_preview}")
                print(f"    Final Makespan: {stats['real_makespan']:.2f}h")
                print("    Daily Makespan (hours):")
                for line in actor_daily_lines:
                    print(line)
                print(f"    Violations: {stats['violations']}")
                print("    Violations Detail:")
                for line in actor_violation_lines:
                    print(line)
                print(f"    Longi Balance: ratio={actor_longi_balance:.4f} (A={actor_longi_a}, B={actor_longi_b}, total={actor_longi_total})")

                print("\n  [Baseline Actor]")
                print(f"    Sequence (first 30): {baseline_sequence_preview}")
                print(f"    Final Makespan: {baseline_final_makespan:.2f}h")
                print("    Daily Makespan (hours):")
                for line in baseline_daily_lines:
                    print(line)
                print(f"    Violations: {baseline_violation_count}")
                print("    Violations Detail:")
                for line in baseline_violation_lines:
                    print(line)
                print(f"    Longi Balance: ratio={baseline_longi_balance:.4f} (A={baseline_longi_a}, B={baseline_longi_b}, total={baseline_longi_total})")
                print(f"\n  Δ vs LPT: makespan {delta_makespan_log:+.2f}h, violations {delta_violations_log:+d}, longi {delta_longi_log:+.4f}")
                print(f"  PPO Update Applied: {update_applied}")
            
            # Baseline 업데이트 체크
            if display_episode % args.baseline_update_interval == 0:
                updated = trainer.should_update_baseline(
                    baseline_test_blocks, 
                    baseline_test_metadata,
                    update_csv_path=csv_paths['update']  # 🔥 CSV 로깅 경로 전달
                )
                if updated:
                    print(f"🔄 Baseline 업데이트 #{trainer.baseline_update_count}")
            
            # [AGENT-EDIT] single-agent의 comprehensive evaluation은 매우 무겁다.
            # 첫 에피소드부터 full evaluation을 돌리면 학습 시작이 과도하게 느려지므로,
            # 기본적으로 eval_interval 시점과 마지막 에피소드에서만 full eval을 수행한다.
            should_eval = (
                not bool(getattr(args, "disable_eval", 0))
                and ((display_episode % args.eval_interval == 0) or (display_episode == end_episode))
            )
            if should_eval:
                from PPO.eval.train_evaluation import comprehensive_evaluation
                
                # 🆕 에포크별 평가 폴더 생성 (evaluation/실행날짜_time/에포크번호/)
                episode_eval_dir = os.path.join(session_eval_dir, str(display_episode))
                os.makedirs(episode_eval_dir, exist_ok=True)
                
                # 종합 평가 실행
                eval_results = comprehensive_evaluation(
                    trainer=trainer,
                    device=device,
                    generator=generator,
                    episode=display_episode,
                    csv_path=csv_paths['evaluation'],
                    eval_dir=episode_eval_dir  # 🆕 에포크별 폴더로 변경
                )
                
                # [AGENT-ADD] RL/LPT/SPT/SEAM 기준 한 줄 요약
                print(
                    f"[SingleEval] ep={display_episode} rl={float(eval_results['avg_rl']):.2f}h "
                    f"lpt={float(eval_results['avg_lpt']):.2f}h "
                    f"spt={float(eval_results['avg_spt']):.2f}h "
                    f"seam={float(eval_results['avg_seam']):.2f}h "
                    f"random={float(eval_results['avg_random']):.2f}h"
                )

                # 베스트 모델 저장
                if eval_results['avg_rl'] < best_makespan:
                    best_makespan = eval_results['avg_rl']
                    save_path = os.path.join(session_model_dir, f"best_ppo_rollout_ep{display_episode}.pth")
                    trainer.save_model(save_path)
                    print(f"🏆 새로운 베스트 RL: {best_makespan:.2f}h")
            
            # 정기 저장
            if display_episode % args.save_interval == 0:
                save_path = os.path.join(session_model_dir, f"ppo_rollout_ep{display_episode}.pth")
                trainer.save_model(save_path)
        
        print("\n" + "="*60)
        print("✅ PPO Rollout 학습 완료!")
        print(f"   Start Episode: {start_episode}")
        print(f"   End Episode: {end_episode}")
        print(f"   New Episodes This Run: {max(0, end_episode - start_episode)}")
        print(f"   Best Makespan: {best_makespan:.2f}h")
        print(f"   Baseline Updates: {trainer.baseline_update_count}")
        print("="*60)
    
    # 평가 모드
    if args.eval:
        print("\n" + "="*60)
        print("📊 평가 모드")
        print("="*60)
        
        eval_results = []
        baseline_results = []
        
        for i in range(len(test_blocks)):
            # 현재 모델 평가
            trainer.actor.eval()
            with torch.no_grad():
                from scheduling.assembly_start.rl_assembly_scheduler import run_rl_assembly_decoding_sequence_with_blocks
                start_date = trainer.compute_start_date(test_blocks[i])
                _, eval_stats, _, _ = run_rl_assembly_decoding_sequence_with_blocks(
                    blocks=test_blocks[i],
                    metadata=test_metadata[i],
                    rl_agent=trainer.actor,
                    device=device,
                    max_days=20,
                    start_date=start_date,
                    training_mode=False,
                    save_csv=False,
                    save_detailed=False  # 🔥 평가 모드에서도 상세 분석 CSV 비활성화
                )
            eval_results.append(eval_stats.get('makespan_hours', float('inf')))
            
            # Baseline 평가
            baseline_makespan = trainer.get_rollout_baseline(
                test_blocks[i], 
                test_metadata[i],
                start_date
            )
            baseline_results.append(baseline_makespan)
        
        # 🔥 평가 완료 후 train 모드로 복구
        trainer.actor.train()
        
        print(f"\n최종 평가 결과 ({len(test_blocks)}개 테스트):")
        print(f"  Actor Model: {np.mean(eval_results):.2f}h (±{np.std(eval_results):.2f})")
        print(f"  Baseline Model: {np.mean(baseline_results):.2f}h (±{np.std(baseline_results):.2f})")
        print(f"  평균 개선: {np.mean(baseline_results) - np.mean(eval_results):.2f}h")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='PPO with Rollout Baseline Training')
    
    # 모드
    parser.add_argument('--train', action='store_true', help='학습 모드')
    parser.add_argument('--eval', action='store_true', help='평가 모드')
    # [AGENT-ADD] main.py가 runtime config 경로를 전달해도 single-agent runner가 실패하지 않도록 수용한다.
    parser.add_argument('--config', type=str, help='런타임 설정 파일 경로 (main.py 전달용)')
    
    # 학습 파라미터
    parser.add_argument('--episodes', type=int, default=150001, help='학습 에피소드 수')
    parser.add_argument('--lr', type=float, default=1e-4, help='학습률')  # 🔥 10배 증가
    parser.add_argument('--epsilon', type=float, default=0.2, help='PPO clipping epsilon')
    parser.add_argument('--ppo_iterations', type=int, default=2, help='PPO 업데이트 반복 횟수')
    # parser.add_argument('--grad_clip', type=float, default=0.5, help='그래디언트 클리핑')
    parser.add_argument('--grad_clip', type=float, default=1.0, help='그래디언트 클리핑')
    
    # 🔥 네트워크 아키텍처 파라미터
    parser.add_argument('--embedding_dim', type=int, default=256, help='임베딩 차원')
    parser.add_argument('--hidden_dim', type=int, default=256, help='히든 차원')
    parser.add_argument('--n_layers', type=int, default=1, help='어텐션 레이어 수 (포지셔널 인코딩으로 2개면 충분)')
    parser.add_argument('--n_heads', type=int, default=4, help='어텐션 헤드 수 (과도한 평균화 방지)')
    parser.add_argument('--use_norm', action='store_true', help='블록 피처 스케일링 사용')
    parser.add_argument('--dropout', type=float, default=0.1, help='드롭아웃 비율')
    parser.add_argument('--temperature', type=float, default=1.0, help='샘플링 온도 (T)')
    
    # 🔥 옵티마이저 설정
    parser.add_argument('--optimizer', type=str, default='ranger_adabelief', 
                       choices=['Adam', 'AdamW', 'ranger_adabelief'],
                       help='옵티마이저 선택 (Adam, AdamW, ranger_adabelief)')
    
    # Baseline 설정
    parser.add_argument('--baseline_update_interval', type=int, default=10, help='Baseline 업데이트 체크 주기')  # 🔥 더 자주 업데이트
    # parser.add_argument('--statistical_alpha', type=float, default=0.95, help='통계적 유의수준')  # 🔥 더 관대하게 (0.8→0.95)
    parser.add_argument('--statistical_alpha', type=float, default=0.7, help='통계적 유의수준')  # 🔥 더 관대하게 (0.8→0.95)
    
    # 🔥 엔트로피 설정
    parser.add_argument('--entropy_coeff', type=float, default=0.01, help='엔트로피 계수 (워밍업 이후 목표 값)')
    parser.add_argument('--entropy_decay', type=float, default=0.995, help='엔트로피 계수 감소율')
    parser.add_argument('--min_entropy_coeff', type=float, default=0.0005, help='최소 엔트로피 계수')
    parser.add_argument('--entropy_decay_interval', type=int, default=50, help='엔트로피 감소 주기 (에피소드)')
    parser.add_argument('--initial_entropy_coeff', type=float, default=0.03, help='엔트로피 워밍업 시작 값')
    parser.add_argument('--entropy_warmup_episodes', type=int, default=50, help='엔트로피 워밍업 에피소드 수')
    # [AGENT-EDIT] value loss 제거
    
    # 환경 설정
    parser.add_argument('--num_blocks', type=int, default=100, help='블록 수')
    # [AGENT-ADD] Training distribution controls (eval-like but smaller size).
    parser.add_argument('--train_distribution', choices=['fixed', 'eval_like'], default='eval_like',
                        help='학습 분포: fixed(고정 num_blocks) / eval_like(평가와 동일 분포)')
    # [AGENT-EDIT] 블록 수 범위 고정 (50~90)
    parser.add_argument('--num_blocks_min', type=int, default=50, help='eval_like 최소 블록 수')
    parser.add_argument('--num_blocks_max', type=int, default=90, help='eval_like 최대 블록 수')
    parser.add_argument('--max_daily_blocks', type=int, default=17, help='하루 최대 블록 수')
    # [AGENT-EDIT] 몰림 완화
    parser.add_argument('--min_spread_days', type=int, default=5, help='최소 spread_days')
    parser.add_argument('--use_snu', action='store_true', help='SNU 데이터셋 사용')
    parser.add_argument('--use_env_state', action='store_true', help='🆕 환경 상태 벡터 사용 (full=31 / reduced=4)')
    # [AGENT-ADD] Feature mode switch (full vs reduced)
    parser.add_argument('--feature_mode', choices=['full', 'reduced', 'constraint', 'diff'], default='constraint',
                        help='피처 모드: full(기존 34/31) / reduced(총 10개) / constraint(제약 연동)')
    parser.add_argument('--use_positional_encoding', action='store_true',
                        help='포지셔널 인코딩 사용 (기본: 사용 안 함)')
    # [AGENT-ADD] action masking 디버그 출력 제어
    parser.add_argument('--masking_debug', action='store_true',
                        help='Action masking 디버그 출력 활성화 (PBS_FORCE_DEBUG=1)')
    parser.add_argument('--masking_debug_verbose', action='store_true',
                        help='Action masking 상세 디버그 출력 활성화 (PBS_DEBUG_VERBOSE=1)')
    # [AGENT-ADD] 고정 통계 정규화 설정
    parser.add_argument('--fixed_norm_stats', type=int, default=0, help='고정 통계 정규화 사용 여부 (1/0)')
    parser.add_argument('--fixed_norm_warmup_episodes', type=int, default=5, help='고정 통계 워밍업 에피소드 수')
    # [AGENT-ADD] 서버 병목 진단/프로파일링 옵션
    parser.add_argument('--perf_timing', action='store_true',
                        help='에피소드, self-label 후보, teacher update wall-clock timing 출력')
    parser.add_argument('--perf_top_k', type=int, default=8,
                        help='--perf_timing 사용 시 느린 후보를 몇 개 출력할지')
    parser.add_argument('--torch_profile_episodes', type=int, default=0,
                        help='처음 N개 학습 에피소드에 torch profiler trace 저장')
    parser.add_argument('--torch_profile_dir', type=str, default='',
                        help='torch profiler trace 저장 디렉토리 (기본: PPO/train/result/profiler/<timestamp>)')
    parser.add_argument('--resource_auto', type=int, choices=[0, 1], default=1,
                        help='CPU/GPU/RAM 기준 자동 리소스 설정 사용 여부 (1/0)')
    parser.add_argument('--torch_num_threads', type=int, default=0,
                        help='torch.set_num_threads 값 (0이면 PyTorch 기본값)')
    parser.add_argument('--torch_num_interop_threads', type=int, default=0,
                        help='torch.set_num_interop_threads 값 (0이면 PyTorch 기본값)')
    parser.add_argument('--mode', choices=['ppo', 'self_label', 'self-label'], default='self-label',
                        help='학습 모드 (ppo/self_label)')
    parser.add_argument('--self_label_samples', type=int, default=4, help='self-label 모드 SLIM/RL 후보 샘플 수')
    parser.add_argument('--self_label_mode', type=int, choices=[1, 2], default=1,
                        help='legacy self-label tie-breaker: 1=score 후 makespan, 2=score 후 위반')
    parser.add_argument('--self_label_selection_strategy',
                        choices=['legacy_score', 'primary_first', 'feasible_first', 'makespan_first'],
                        default='primary_first',
                        help='self-label teacher 선택 기준: legacy_score / primary_first / feasible_first / makespan_first')
    parser.add_argument('--self_label_lpt_teacher_policy',
                        choices=['candidate_only', 'legacy', 'warmup_only', 'disabled'],
                        default='candidate_only',
                        help='LPT teacher 사용 정책: candidate_only(후보군 경쟁만) / legacy / warmup_only / disabled')
    parser.add_argument('--self_label_lpt_warmup_episodes', type=int, default=0,
                        help='warmup_only일 때 LPT teacher를 허용할 에피소드 수')
    parser.add_argument('--self_label_lpt_teacher_bias_on', type=int, choices=[0, 1], default=0,
                        help='teacher LPT만 bias on override 적용 여부 (1/0)')
    parser.add_argument('--self_label_profile_expansion', type=int, choices=[0, 1], default=1,
                        help='self-label RL 후보 profile 확장 여부: 8 bias x 8 hard profiles (1/0)')
    parser.add_argument('--self_label_heuristic_profile_expansion', type=int, choices=[0, 1], default=1,
                        help='self-label LPT/SEAM_MIN 후보 bias profile 확장 여부: 각 8개 (1/0)')
    parser.add_argument('--self_label_print_best_updates', type=int, choices=[0, 1], default=1,
                        help='후보군 best 갱신 시 [시퀀스 갱신] 로그 출력 여부 (1/0)')
    parser.add_argument('--self_label_parallel', choices=['auto', 'on', 'off'], default='auto',
                        help='self-label 후보 스케줄 병렬 생성: auto/on/off')
    parser.add_argument('--self_label_parallel_workers', type=int, default=0,
                        help='self-label 후보 생성 worker 수 (0이면 CPU/GPU/RAM 기준 자동)')
    parser.add_argument('--self_label_parallel_device', choices=['auto', 'cuda', 'cpu'], default='auto',
                        help='self-label worker의 actor forward 장치: auto/cuda/cpu')
    parser.add_argument('--self_label_worker_threads', type=int, default=0,
                        help='self-label worker별 torch thread 수 (0이면 자동, 병렬 후보에서는 보통 1)')
    parser.add_argument('--self_label_parallel_min_candidates', type=int, default=8,
                        help='self-label 후보 수가 이 값 이상일 때 병렬화')
    # [AGENT-ADD] 통합 스코어 가중치 (시간 단위)
    parser.add_argument('--violation_penalty_weight', type=float, default=1.0,
                        help='통합 스코어에서 위반 1건당 가산(시간) 가중치')
    parser.add_argument('--longi_balance_weight', type=float, default=0.1,
                        help='론지 불균형(비율형) 가중치')
    parser.add_argument('--start_date_offset', type=int, default=3,
                        help='기준일을 최소 착수일에서 며칠 앞당길지 (예: 3이면 최소 착수일 -3일)')
    
    # 로깅
    parser.add_argument('--log_interval', type=int, default=1, help='로그 출력 주기')
    parser.add_argument('--eval_interval', type=int, default=100, help='평가 주기')
    parser.add_argument('--disable_eval', type=int, choices=[0, 1], default=0,
                        help='학습 중 종합 평가를 비활성화 (빠른 학습/스모크 테스트용)')
    parser.add_argument('--save_interval', type=int, default=100, help='모델 저장 주기')
    parser.add_argument('--violation_print_limit', type=int, default=0,
                        help='학습 로그에 출력할 제약 위반 최대 개수 (0이면 전체 출력)')
    
    # 기타
    parser.add_argument('--seed', type=int, default=42, help='랜덤 시드')
    parser.add_argument('--cpu', action='store_true', help='CPU 사용')
    parser.add_argument('--load_model', type=str, help='로드할 모델 경로')
    parser.add_argument('--resume_episode_mode', choices=['additional', 'total'], default='additional',
                        help='이어학습 episodes 해석: additional=추가 학습 횟수, total=최종 에피소드 번호')
    
    args = parser.parse_args()

    # [AGENT-ADD] runtime_config 기반 기본값 오버라이드 (CLI가 우선)
    def _apply_config_defaults(parsed_args, cfg: dict):
        if not isinstance(cfg, dict):
            return
        defaults = {a.dest: parser.get_default(a.dest) for a in parser._actions}
        for key, value in cfg.items():
            if key == "cli_args":
                continue
            if key not in defaults:
                continue
            if value is None:
                continue
            # CLI에서 명시하지 않은 경우에만 적용
            if getattr(parsed_args, key) == defaults.get(key):
                setattr(parsed_args, key, value)

    _runtime_cfg = get_runtime_config() or {}
    if isinstance(_runtime_cfg, dict):
        train_cfg = _runtime_cfg.get("training") or _runtime_cfg.get("train") or {}
        if isinstance(train_cfg, dict):
            # [AGENT-EDIT] top-level seed/device를 학습 기본값에 반영 (main.py config.yaml 연동)
            merged_cfg = dict(train_cfg)
            if "seed" not in merged_cfg and _runtime_cfg.get("seed") is not None:
                merged_cfg["seed"] = _runtime_cfg.get("seed")
            if "device" not in merged_cfg and _runtime_cfg.get("device"):
                dev = str(_runtime_cfg.get("device")).strip().lower()
                if dev == "cpu":
                    merged_cfg["cpu"] = True
                elif dev.startswith("cuda"):
                    merged_cfg["cpu"] = False
            _apply_config_defaults(args, merged_cfg)

    # [AGENT-ADD] 이어학습은 checkpoint 메타데이터를 우선 복원한다.
    _apply_resume_defaults_from_checkpoint(args, parser, sys.argv[1:])

    # [AGENT-ADD] mode 별칭 정규화 (self-label -> self_label)
    if args.mode == 'self-label':
        args.mode = 'self_label'
    
    # 기본값 설정
    if not args.train and not args.eval:
        args.train = True
    
    main(args)
