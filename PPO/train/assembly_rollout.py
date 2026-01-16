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
import os
import random
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional
import copy

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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

# Ranger-AdaBelief optimizer (선택적)
try:
    from ranger_adabelief import RangerAdaBelief
    RANGER_AVAILABLE = True
except ImportError:
    RANGER_AVAILABLE = False
    print("⚠️ RangerAdaBelief not available, using Adam instead")


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
                 self_label_samples: int = 8,    # 🆕 self-label 샘플 수
                 self_label_mode: int = 1,       # 🆕 1: score 우선, 2: 위반 우선(동점 tie-breaker)
                 violation_penalty_weight: float = 1.0,  # 🆕 위반 1건당 가산(시간) 가중치
                 longi_balance_weight: float = 0.1):  # [AGENT-ADD] 론지 불균형 가중치 (비율형)
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
        self.self_label_samples = max(2, self_label_samples)
        self.self_label_mode = 1 if self_label_mode != 2 else 2
        # [AGENT-EDIT] makespan + 위반 페널티 통합 스코어 가중치
        self.violation_penalty_weight = max(0.0, float(violation_penalty_weight))
        # [AGENT-ADD] 론지 불균형 가중치 (비율형)
        self.longi_balance_weight = max(0.0, float(longi_balance_weight))
        # [AGENT-EDIT] value loss 제거
        
        print(f"📅 PPO 기준일 관리 설정:")
        print(f"   - 기준일 오프셋: {self.start_date_offset_days}일")
        
        # 🆕 Actor 파라미터에 use_env_state 추가
        # [AGENT-EDIT] actor_params가 모델 인스턴스로 들어올 수 있어 안전 처리
        if isinstance(actor_params, SingleStepPtrNet):
            self.actor = actor_params.to(device)
        else:
            actor_params_with_state = actor_params.copy()
            actor_params_with_state['use_env_state'] = use_env_state
            # Actor 모델 (학습용)
            self.actor = SingleStepPtrNet(**actor_params_with_state).to(device)
        
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
        lpt_makespan, lpt_schedule, lpt_stats = self._evaluate_heuristic_baseline(
            blocks, metadata, start_date, method="lpt", trials=10
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
                'baseline_trials': 10
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
            save_detailed=False  # 🔥 학습 중에는 상세 분석 CSV 비활성화
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
        # actor가 baseline보다 좋을수록(낮을수록) Advantage가 양수
        advantage = baseline_score - actor_score
        
        # 4. PPO 업데이트
        t3 = time.time()
        print(f"[3] PPO 업데이트 시작...")
        # [AGENT-EDIT] 동점도 업데이트 허용 (baseline과 동일 score일 때도 학습)
        update_allowed = actor_score <= baseline_score
        update_applied = bool(update_allowed and episode_data)
        if update_applied:
            # [AGENT-EDIT] LPT보다 좋은 경우에만 업데이트 (항상 LPT 이상 목표)
            actor_loss, entropy_loss = self._ppo_update(episode_data, advantage)  # 🔥 엔트로피 Loss
        else:
            # [AGENT-EDIT] LPT보다 나쁘면 업데이트 스킵
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

    # ------------------------------------------------------------------
    # Self-Label (best-of-K + teacher forcing) 모드
    # ------------------------------------------------------------------
    def train_episode_self_label(self, blocks: List, metadata: Dict) -> Dict:
        """Self-label 방식으로 한 에피소드 학습"""
        self.actor.train()
        start_date = self._compute_episode_start_date(blocks)

        candidate_records = []
        lpt_record = None
        lpt_fallback_results = None
        lpt_fallback_stats = None
        for sample_idx in range(self.self_label_samples):
            record = self._sample_schedule(
                model=self.actor,
                blocks=blocks,
                metadata=metadata,
                start_date=start_date,
                training_mode=True,  # 샘플은 스토캐스틱하게 뽑되 grad는 이후 teacher forcing에서만 사용
                tag=f"selflabel_{sample_idx}"
            )
            if record:
                candidate_records.append(record)

        # [AGENT-ADD] LPT 휴리스틱 시퀀스를 self-label 후보에 추가
        try:
            lpt_results, _lpt_stats = run_assembly_decoding_sequence_with_blocks(
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
                expand_rows=False  # 강제 시퀀스용 원본 순서 유지
            )
            # [AGENT-ADD] fallback 저장 (강제 시퀀스 생성 실패 시 사용)
            lpt_fallback_results = lpt_results
            lpt_fallback_stats = _lpt_stats
            forced_sequence = [
                r.get('block_id') for r in (lpt_results or [])
                if r.get('block_id') is not None
            ]
            if forced_sequence:
                forced_record = self._sample_schedule(
                    model=self.actor,
                    blocks=blocks,
                    metadata=metadata,
                    start_date=start_date,
                    training_mode=True,
                    tag="selflabel_lpt",
                    forced_sequence=forced_sequence
                )
                if forced_record:
                    lpt_record = forced_record
                    candidate_records.append(forced_record)
        except Exception:
            pass

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
        update_source = "self_label"
        # [AGENT-EDIT] LPT보다 나쁘면 LPT 강제 학습
        if lpt_record:
            best_ms = best_record['stats'].get('makespan_hours', float('inf'))
            best_vio = get_violation_count(best_record.get('stats', {}))
            lpt_ms = lpt_record['stats'].get('makespan_hours', float('inf'))
            lpt_vio = get_violation_count(lpt_record.get('stats', {}))
            best_longi = compute_longi_balance_ratio(best_record.get('schedule'))
            lpt_longi = compute_longi_balance_ratio(lpt_record.get('schedule'))
            best_score = compute_score(
                best_ms,
                best_vio,
                best_longi,
                self.violation_penalty_weight,
                self.longi_balance_weight,
            )
            lpt_score = compute_score(
                lpt_ms,
                lpt_vio,
                lpt_longi,
                self.violation_penalty_weight,
                self.longi_balance_weight,
            )
            if best_score >= lpt_score:
                best_record = lpt_record
                update_source = "lpt"
        if best_record.get('tag') == "selflabel_lpt":
            update_source = "lpt"
        actor_loss = self._teacher_forcing_update(best_record['episode_data'])

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
            forced_sequence=forced_sequence
        )
        return {
            'schedule': schedule_results,
            'stats': stats or {},
            'episode_data': episode_data or [],
            'env': env,
            'tag': tag
        }

    def _select_best_record(self, records: List[Dict]) -> Dict:
        """
        self_label_mode 기준으로 best-of-K 샘플 선택.
        mode 1: score(makespan + w*violations) → makespan → violations
        mode 2: score(makespan + w*violations) → violations → makespan
        """
        def safe_stats(rec):
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
            return ms, vio, score

        if self.self_label_mode == 2:
            # [AGENT-EDIT] score 우선 + 위반 tie-breaker
            key_fn = lambda r: (safe_stats(r)[2], safe_stats(r)[1], safe_stats(r)[0])
        else:
            # [AGENT-EDIT] score 우선 + makespan tie-breaker
            key_fn = lambda r: (safe_stats(r)[2], safe_stats(r)[0], safe_stats(r)[1])
        return min(records, key=key_fn)

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
        
        # [AGENT-EDIT] step_reward 제거: 글로벌 advantage만 사용
        advantages = torch.full(
            (len(episode_data),),
            float(global_advantage),
            device=self.device,
            dtype=torch.float32
        )
        if advantages.numel() > 1:
            advantages = (advantages - advantages.mean()) / (advantages.std(unbiased=False) + 1e-8)
        else:
            advantages = advantages - advantages
        
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
        torch.save({
            'actor_state_dict': self.actor.state_dict(),
            'optimizer_state_dict': self.optimizer.state_dict(),
            'episode': self.episode_count,
            'baseline_updates': self.baseline_update_count
        }, path)
        print(f"✅ 모델 저장: {path}")
    
    def load_model(self, path: str):
        """모델 로드"""
        checkpoint = torch.load(path, map_location=self.device)
        self.actor.load_state_dict(checkpoint['actor_state_dict'])
        self.optimizer.load_state_dict(checkpoint['optimizer_state_dict'])
        self.episode_count = checkpoint.get('episode', 0)
        self.baseline_update_count = checkpoint.get('baseline_updates', 0)
        print(f"✅ 모델 로드: {path}")
        return self.episode_count
