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
import pandas as pd
from datetime import datetime
from typing import List, Dict, Any, Optional, Tuple

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PPO.train.assembly_rollout import AssemblyPPORollout
from utils.optimized_block_generator import OptimizedBlockGenerator
from enhanced_environment.common.utils_core import DataConverter
from PPO.models.single_step_actor import (
    ENV_STATE_DIM,
    BLOCK_FEATURE_DIM,
    REDUCED_ENV_STATE_DIM,
    REDUCED_BLOCK_FEATURE_DIM,
    CONSTRAINT_ENV_STATE_DIM,
    CONSTRAINT_BLOCK_FEATURE_DIM
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
    # 시드 설정
    set_seed(args.seed)
    
    # 디바이스 설정
    device = torch.device('cuda' if torch.cuda.is_available() and not args.cpu else 'cpu')
    print(f"🖥️ Device: {device}")
    
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
    session_folder_name = f"{timestamp}_{args.optimizer}_{env_state_suffix}"
    session_model_dir = os.path.join(result_dir, 'models', session_folder_name)
    os.makedirs(session_model_dir, exist_ok=True)
    
    # 🆕 실행별 평가 폴더 생성 (evaluation/실행날짜_time/)
    session_eval_dir = os.path.join(eval_dir, timestamp)
    os.makedirs(session_eval_dir, exist_ok=True)
    
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
        violation_penalty_weight=args.violation_penalty_weight,
        longi_balance_weight=args.longi_balance_weight,
        fixed_norm_stats=bool(args.fixed_norm_stats),
        fixed_norm_warmup_episodes=args.fixed_norm_warmup_episodes
    )
    
    # 모델 로드 (있으면)
    start_episode = 0
    if args.load_model and os.path.exists(args.load_model):
        start_episode = trainer.load_model(args.load_model)
        print(f"📂 모델 로드 완료: {args.load_model} (Episode {start_episode}부터 시작)")
    
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
        # [AGENT-ADD] 마스킹 디버그 환경변수 상태 출력
        print(f"   - Masking Debug: {os.environ.get('PBS_FORCE_DEBUG', '')} (verbose={os.environ.get('PBS_DEBUG_VERBOSE', '')})")
        print(f"   - Model Folder: {session_folder_name}")
        print("="*60)
        
        best_makespan = float('inf')
        all_makespans = []  # 🔥 전체 makespan 기록
        all_advantages = []  # 🔥 전체 advantage 기록
        all_entropy_losses = []  # 🔥 전체 엔트로피 Loss 기록
        
        for episode in range(start_episode, args.episodes):
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
                    f"[DEBUG] Episode {episode}: blocks={total_blocks}, bucket={util_bucket}, util={util_target:.2f} "
                    f"(range {util_range[0]:.2f}-{util_range[1]:.2f}), spread_days={spread_days}, "
                    f"ps_ratio={ps_ratio_target:.2f}, sub_ratio={sub_ratio_target:.2f}, "
                    f"scales(seam/tact/len/width/thick)={seam_scale:.2f}/{tact_time_scale:.2f}/"
                    f"{length_scale:.2f}/{width_scale:.2f}/{thickness_scale:.2f}"
                )
            print(f"[DEBUG] Episode {episode}: generated dataframe rows = {len(blocks_data)}")
            blocks, metadata = DataConverter.dataframe_to_blocks_with_metadata(blocks_data)
            print(f"[DEBUG] Episode {episode}: converted blocks = {len(blocks)}")

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
            if args.mode == "self_label":
                print(f"[DEBUG] Episode {episode}: invoking train_episode_self_label with {len(blocks)} blocks (samples={args.self_label_samples}, mode={args.self_label_mode})")
                stats = trainer.train_episode_self_label(blocks, metadata)
            else:
                print(f"[DEBUG] Episode {episode}: invoking train_episode_ppo with {len(blocks)} blocks")
                stats = trainer.train_episode_ppo(blocks, metadata)
            
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
            if episode % args.log_interval == 0:
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

                print(f"\n Episode {episode}/{args.episodes}")
                print(f"  Advantage: {stats['advantage']:.2f}")
                print(f"  Actor Loss: {stats['actor_loss']:.4f}")
                print(f"  Entropy Loss: {stats['entropy_loss']:.4f}")  # 🔥 엔트로피 Loss 출력
                print(f"  Violations: {stats['violations']}")
                
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
            if episode % args.baseline_update_interval == 0 and episode > 0:
                updated = trainer.should_update_baseline(
                    baseline_test_blocks, 
                    baseline_test_metadata,
                    update_csv_path=csv_paths['update']  # 🔥 CSV 로깅 경로 전달
                )
                if updated:
                    print(f"🔄 Baseline 업데이트 #{trainer.baseline_update_count}")
            
            # 평가
            if episode % args.eval_interval == 0 and episode > 0:
                from PPO.eval.train_evaluation import comprehensive_evaluation
                
                # 🆕 에포크별 평가 폴더 생성 (evaluation/실행날짜_time/에포크번호/)
                episode_eval_dir = os.path.join(session_eval_dir, str(episode))
                os.makedirs(episode_eval_dir, exist_ok=True)
                
                # 종합 평가 실행
                eval_results = comprehensive_evaluation(
                    trainer=trainer,
                    device=device,
                    generator=generator,
                    episode=episode,
                    csv_path=csv_paths['evaluation'],
                    eval_dir=episode_eval_dir  # 🆕 에포크별 폴더로 변경
                )
                
                # 베스트 모델 저장
                if eval_results['avg_rl'] < best_makespan:
                    best_makespan = eval_results['avg_rl']
                    save_path = os.path.join(session_model_dir, f"best_ppo_rollout_ep{episode}.pth")
                    trainer.save_model(save_path)
                    print(f"🏆 새로운 베스트 RL: {best_makespan:.2f}h")
            
            # 정기 저장
            if episode % args.save_interval == 0 and episode > 0:
                save_path = os.path.join(session_model_dir, f"ppo_rollout_ep{episode}.pth")
                trainer.save_model(save_path)
        
        print("\n" + "="*60)
        print("✅ PPO Rollout 학습 완료!")
        print(f"   Total Episodes: {args.episodes}")
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
    parser.add_argument('--feature_mode', choices=['full', 'reduced', 'constraint'], default='constraint',
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
    parser.add_argument('--mode', choices=['ppo', 'self_label', 'self-label'], default='self-label',
                        help='학습 모드 (ppo/self_label)')
    parser.add_argument('--self_label_samples', type=int, default=64, help='self-label 모드 샘플 수')
    parser.add_argument('--self_label_mode', type=int, choices=[1, 2], default=1,
                        help='self-label 선택 기준: 1=score 우선, 2=score 후 위반 우선')
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
    parser.add_argument('--save_interval', type=int, default=100, help='모델 저장 주기')
    parser.add_argument('--violation_print_limit', type=int, default=0,
                        help='학습 로그에 출력할 제약 위반 최대 개수 (0이면 전체 출력)')
    
    # 기타
    parser.add_argument('--seed', type=int, default=42, help='랜덤 시드')
    parser.add_argument('--cpu', action='store_true', help='CPU 사용')
    parser.add_argument('--load_model', type=str, help='로드할 모델 경로')
    
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

    # [AGENT-ADD] mode 별칭 정규화 (self-label -> self_label)
    if args.mode == 'self-label':
        args.mode = 'self_label'
    
    # 기본값 설정
    if not args.train and not args.eval:
        args.train = True
    
    main(args)
