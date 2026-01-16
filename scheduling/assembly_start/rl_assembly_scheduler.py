# rl_assembly_scheduler.py
import os  # [AGENT-EDIT] PBS_FORCE_DEBUG 기반 디버그 출력 제어
import math  # [AGENT-EDIT] env_state 자동 스케일링에 사용
import torch
import random
import numpy as np
from collections import defaultdict
from typing import List, Dict, Tuple, Optional, Set
from datetime import datetime, timedelta

from scheduling.assembly_start.action_sequence_조립착수일기준휴리스틱 import (
    _run_assembly_decoding_core,
    get_line_group_and_workshop_code,
    create_block_result,
)  # [AGENT-EDIT] scheduling 경로로 직접 참조 (shim의 _ 접두어 노출 문제 회피)
from enhanced_environment.common.utils_core import dedup_violations
from enhanced_environment.pbs_env import EnhancedPanelBlockShop
from enhanced_environment.constraints import ConstraintConfig
from enhanced_environment.models import ConstraintViolation, BayType, PortStarboard, ProcessStep
from enhanced_environment.common.utils_core import expand_rows_with_subassembly
from PPO.models.single_step_actor import (
    extract_block_features_single,
    ENV_STATE_DIM,
    REDUCED_ENV_STATE_DIM,
    CONSTRAINT_ENV_STATE_DIM
)


class RLAssemblyScheduler:
    """RL Agent를 사용한 조립 스케줄링 (단순화)"""
    
    def __init__(self, rl_agent, device):
        self.rl_agent = rl_agent
        self.device = device
        self.episode_data = []  # 전체 에피소드 데이터 저장
        self.use_env_state = getattr(rl_agent, 'use_env_state', False)  # 🆕 env_state 사용 여부
        self.feature_mode = getattr(rl_agent, 'feature_mode', 'full')
        if self.feature_mode == "reduced":
            default_env_dim = REDUCED_ENV_STATE_DIM
        elif self.feature_mode == "constraint":
            default_env_dim = CONSTRAINT_ENV_STATE_DIM
        else:
            default_env_dim = ENV_STATE_DIM
        self.env_state_dim = getattr(rl_agent, 'env_state_dim', default_env_dim)
        ########################################################################
        # [FIX] 베이별 누적 블록 수 추적 (35A/36B 정확히 관리)
        ########################################################################
        self.branch_usage_counts = {'35A': 0, '36B': 0}
        self._env_state_debug_prints = 0
        self._masking_stage_lookup: Dict[str, int] = {}
        self._masking_stage_bucket = 32.0
        self.stage_definitions = [
            ("LINE_GROUP_CONSTRAINT", ["LINE_GROUP_CONSTRAINT"]),
            ("P5#11_12", ["P5#11_12_assembly_mixing"]),
            ("P5#8_9_10_16", ["P5#8_9_10_16_capacity"]),
            ("P6#4", ["P6#4_cross_seam"]),
            ("P5#15", ["P5#15_holiday_eve"]),
            ("ROUTING_C_SEAM", ["ROUTING_C_SEAM_SPACING"]),
            ("ROUTING_CURVED", ["ROUTING_CURVED_SPACING"]),
            ("ROUTING_HIGH_SEAM", ["ROUTING_HIGH_SEAM_SPACING"]),
            ("ROUTING_WORKSHOP_ORDER", ["ROUTING_WORKSHOP_ORDER"]),
            ("P6#1_2_3", ["P6#1_2_3_saw_time"]),
            ("CONSECUTIVE_3BAY", ["CONSECUTIVE_3BAY"])
        ]
        # ==== [AGENT-ADD BEGIN: Constraint-focused scaling constants] ====
        self.daily_block_reference = 20.0  # 일일 처리 가능한 블록 수 기준치
        self.constraint_debt_window = 24   # 최근 제약 위반 윈도우
        # [AGENT-ADD] env_state 자동 스케일링 (에피소드/학습 분포 변화 대응)
        self.env_state_max_total_blocks = 1
        self.env_state_max_abs_slack = 1.0
        # ==== [AGENT-ADD END] ====

    ############
    # 추가했음 #
    ############

    ###########
    # 수정했음 #
    ###########

    def _normalize_line_key(self, value) -> str:
        if value is None:
            return 'UNKNOWN'
        key = str(value).upper()
        if '35A' in key:
            return '35A'
        if '36B' in key:
            return '36B'
        return key

    def _normalize_workshop_key(self, value) -> str:
        if value is None:
            return 'UNKNOWN'
        return str(value).upper()

    def _compute_bay_balance_delta(self) -> float:
        """35A-36B 누적 편차를 -1~1 범위로 정규화."""
        count_35 = float(self.branch_usage_counts.get('35A', 0))
        count_36 = float(self.branch_usage_counts.get('36B', 0))
        total = max(1.0, count_35 + count_36)
        delta = (count_35 - count_36) / total
        return max(-1.0, min(1.0, delta))

    @staticmethod
    def _safe_ratio(value: float, base: float) -> float:
        if base <= 0:
            return 0.0
        return max(0.0, min(1.0, value / base))

    def _compute_ps_pair_ratio(self, blocks_dict: Dict[int, object], selected_block_ids: List[int]) -> float:
        remaining = [
            block for block_id, block in blocks_dict.items()
            if block_id not in selected_block_ids
        ]
        if not remaining:
            return 0.0
        ps_blocks = 0
        for block in remaining:
            port_state = getattr(block, 'port_starboard', PortStarboard.NONE)
            if port_state != PortStarboard.NONE:
                ps_blocks += 1
        return self._safe_ratio(ps_blocks, len(remaining))

    def _compute_capacity_snapshot(self, env, current_time: datetime) -> Dict[str, float]:
        """현재 용량 사용률 정보 요약."""
        is_weekend = current_time.weekday() >= 5
        is_hot = env.calendar_manager.is_hot_season(current_time)
        is_holiday_eve = env.calendar_manager.is_holiday_eve(current_time)

        used_seam = float(env.capacity_tracker.get_capacity_used(is_weekend))
        block_count = float(env.capacity_tracker.get_block_count(is_weekend))
        capacity_limit = float(env.capacity_tracker.get_capacity_limits(is_weekend, is_hot, is_holiday_eve))
        seam_ratio = self._safe_ratio(used_seam, max(1.0, capacity_limit))
        remaining_ratio = self._safe_ratio(max(0.0, capacity_limit - used_seam), max(1.0, capacity_limit))
        block_ratio = self._safe_ratio(block_count, self.daily_block_reference)

        return {
            'daily_seam_usage_ratio': seam_ratio,
            'daily_block_usage_ratio': block_ratio,
            'remaining_capacity_ratio': remaining_ratio,
            'capacity_limit': capacity_limit,
            'used_seam': used_seam
        }

    def _compute_constraint_debt(self, env) -> Tuple[float, float]:
        """최근 위반 내역을 기반으로 경고/오류 비율 계산."""
        history = getattr(env, 'violation_history', [])
        if not history:
            return 0.0, 0.0
        recent = history[-self.constraint_debt_window:]
        warnings = sum(1 for v in recent if getattr(v, 'severity', '').upper() == 'WARNING')
        errors = sum(1 for v in recent if getattr(v, 'severity', '').upper() == 'ERROR')
        warning_ratio = self._safe_ratio(warnings, self.constraint_debt_window)
        error_ratio = self._safe_ratio(errors, max(1.0, self.constraint_debt_window / 2))
        return warning_ratio, error_ratio

    def _classify_line_flags(self, block) -> Tuple[float, float]:
        workshop_code = self._normalize_workshop_key(getattr(block, 'assembly_workshop_code', None))
        line_is_fixed = 1.0 if workshop_code.startswith('F_') else 0.0
        line_is_line = 1.0 if workshop_code.startswith('L_') else 0.0
        return line_is_fixed, line_is_line

    @staticmethod
    def _compute_deadline_urgency(slack_days: float) -> float:
        if slack_days <= 0:
            return 1.0
        return 1.0 / (1.0 + slack_days)

    def _resolve_line_root(self, block) -> Optional[str]:
        line_group = getattr(block, 'line_group', None)
        if isinstance(line_group, str) and line_group.upper().startswith('L_'):
            digits = ''.join(ch for ch in line_group.split('_', 1)[1] if ch.isdigit())
            if digits:
                return f"L_{digits[0]}"
        workshop_code = getattr(block, 'assembly_workshop_code', None)
        if isinstance(workshop_code, str) and workshop_code.upper().startswith('L_'):
            digits = ''.join(ch for ch in workshop_code.split('_', 1)[1] if ch.isdigit())
            if digits:
                return f"L_{digits[0]}"
        return None

    def _resolve_fixed_code(self, block) -> Optional[str]:
        workshop_code = getattr(block, 'assembly_workshop_code', None)
        if not isinstance(workshop_code, str):
            return None
        code = workshop_code.upper()
        if not code.startswith('F_'):
            return None
        suffix = ''.join(ch for ch in code.split('_', 1)[1] if ch.isdigit()) or '1'
        try:
            idx = max(1, min(10, int(suffix)))
        except ValueError:
            idx = 1
        return f"F_{idx}"

    def _compute_branch_counts(self, blocks_dict: Dict[int, object], selected_block_ids: List[int]) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for block_id in selected_block_ids:
            block = blocks_dict.get(block_id)
            if not block:
                continue
            line_key = self._normalize_line_key(getattr(block, 'line_group', None))
            counts[line_key] = counts.get(line_key, 0) + 1
        return counts

    def _compute_backlogs(self, blocks_dict: Dict[int, object], selected_block_ids: List[int]) -> Tuple[Dict[str, int], Dict[str, int]]:
        remaining_ids = [bid for bid in blocks_dict.keys() if bid not in selected_block_ids]
        workshop_counts: Dict[str, int] = {}
        line_counts: Dict[str, int] = {}
        for block_id in remaining_ids:
            block = blocks_dict.get(block_id)
            if not block:
                continue
            workshop_key = self._normalize_workshop_key(getattr(block, 'assembly_workshop_code', None))
            workshop_counts[workshop_key] = workshop_counts.get(workshop_key, 0) + 1
            line_key = self._normalize_line_key(getattr(block, 'line_group', None))
            line_counts[line_key] = line_counts.get(line_key, 0) + 1
        return workshop_counts, line_counts

    def _compute_slack_days(self, block, current_time: datetime) -> float:
        slack = 0.0
        max_date = getattr(block, 'max_start_date', None)
        if isinstance(max_date, datetime):
            slack = (max_date - current_time).total_seconds() / 86400.0
        return float(slack)

    def _encode_masking_stage(self, stage_label: Optional[str]) -> float:
        if not stage_label:
            return 0.0
        key = stage_label.strip().upper()
        if not key:
            return 0.0
        if key not in self._masking_stage_lookup:
            self._masking_stage_lookup[key] = len(self._masking_stage_lookup) + 1
        stage_id = self._masking_stage_lookup[key]
        return min(stage_id / self._masking_stage_bucket, 1.0)

    ########################################################################
    # [FIX] 베이 별 누적 카운트 업데이트 유틸
    ########################################################################
    def _update_branch_usage(self, block, assigned_bay: Optional[BayType]):
        bay_key = None
        if assigned_bay == BayType.BAY_35A:
            bay_key = '35A'
        elif assigned_bay == BayType.BAY_36B:
            bay_key = '36B'
        else:
            line_key = self._normalize_line_key(getattr(block, 'line_group', None))
            if line_key in ('35A', '36B'):
                bay_key = line_key
        if bay_key is None:
            # 균형 유지용 기본 분배
            if self.branch_usage_counts.get('35A', 0) <= self.branch_usage_counts.get('36B', 0):
                bay_key = '35A'
            else:
                bay_key = '36B'
        self.branch_usage_counts[bay_key] = self.branch_usage_counts.get(bay_key, 0) + 1

    # 블록 특성만 사용

    #  환경 스냅샷 생성 제거 (추정치/가정 사용 금지 정책에 따라 불필요)
    
    def _extract_environment_state(self, env, selected_blocks: List[int], blocks_dict: Dict, block_analysis: Optional[List[Dict]] = None) -> List[float]:
        """환경 상태 벡터 추출 (ENV_STATE_DIM 차원)"""
        # [AGENT-EDIT] env_state를 전역 맥락 + 남은 블록 분포 + 후보 품질 요약으로 확장.
        state: List[float] = []

        current_time = env.current_time
        is_weekend = env.calendar_manager.is_weekend(current_time)
        is_holiday_eve = env.calendar_manager.is_holiday_eve(current_time)
        is_hot_season = env.calendar_manager.is_hot_season(current_time)

        day_of_week_norm = current_time.weekday() / 6.0 if current_time.weekday() >= 0 else 0.0
        hour_of_day = current_time.hour + (current_time.minute / 60.0)
        hour_of_day_norm = hour_of_day / 23.0 if hour_of_day > 0 else 0.0

        state.extend([
            1.0 if is_weekend else 0.0,
            1.0 if is_holiday_eve else 0.0,
            1.0 if is_hot_season else 0.0,
            max(0.0, min(1.0, day_of_week_norm)),
            max(0.0, min(1.0, hour_of_day_norm))
        ])

        capacity_snapshot = self._compute_capacity_snapshot(env, current_time)
        # ==== [AGENT-EDIT] Reduced env_state (4 dims) ====
        if self.feature_mode == "reduced":
            remaining_blocks = [block for block_id, block in blocks_dict.items() if block_id not in selected_blocks]
            total_blocks = max(1, len(blocks_dict))
            remaining_ratio = len(remaining_blocks) / total_blocks
            bay_balance_delta = self._compute_bay_balance_delta()

            reduced_state = [
                capacity_snapshot['remaining_capacity_ratio'],
                max(0.0, min(1.0, remaining_ratio)),
                1.0 if is_hot_season else 0.0,
                bay_balance_delta
            ]
            if len(reduced_state) != self.env_state_dim:
                reduced_state = reduced_state[:self.env_state_dim]
                if len(reduced_state) < self.env_state_dim:
                    reduced_state.extend([0.0] * (self.env_state_dim - len(reduced_state)))
            return reduced_state
        # ==== [AGENT-EDIT] Constraint-aligned env_state (9 dims) ====
        if self.feature_mode == "constraint":
            remaining_blocks = [block for block_id, block in blocks_dict.items() if block_id not in selected_blocks]
            total_blocks = max(1, len(blocks_dict))
            remaining_ratio = len(remaining_blocks) / total_blocks
            bay_balance_delta = self._compute_bay_balance_delta()
            hour_of_day = current_time.hour + (current_time.minute / 60.0)
            hour_of_day_norm = hour_of_day / 23.0 if hour_of_day > 0 else 0.0

            constraint_state = [
                capacity_snapshot['remaining_capacity_ratio'],
                capacity_snapshot['daily_seam_usage_ratio'],
                capacity_snapshot['daily_block_usage_ratio'],
                1.0 if is_hot_season else 0.0,
                1.0 if is_holiday_eve else 0.0,
                1.0 if is_weekend else 0.0,
                bay_balance_delta,
                max(0.0, min(1.0, remaining_ratio)),
                max(0.0, min(1.0, hour_of_day_norm))
            ]
            if len(constraint_state) != self.env_state_dim:
                constraint_state = constraint_state[:self.env_state_dim]
                if len(constraint_state) < self.env_state_dim:
                    constraint_state.extend([0.0] * (self.env_state_dim - len(constraint_state)))
            return constraint_state
        state.extend([
            capacity_snapshot['daily_seam_usage_ratio'],
            capacity_snapshot['daily_block_usage_ratio'],
            capacity_snapshot['remaining_capacity_ratio']
        ])

        remaining_blocks = [block for block_id, block in blocks_dict.items() if block_id not in selected_blocks]
        total_blocks = max(1, len(blocks_dict))
        remaining_count = len(remaining_blocks)
        remaining_ratio = remaining_count / total_blocks

        # [AGENT-EDIT] 블록 수는 log 스케일 + 자동 최대치 기반 정규화
        self.env_state_max_total_blocks = max(self.env_state_max_total_blocks, total_blocks)
        max_total_log = math.log1p(self.env_state_max_total_blocks)
        total_blocks_norm = (math.log1p(total_blocks) / max_total_log) if max_total_log > 0 else 0.0
        remaining_blocks_norm = (math.log1p(remaining_count) / max_total_log) if max_total_log > 0 else 0.0
        state.extend([total_blocks_norm, remaining_blocks_norm, remaining_ratio])

        ps_blocks = 0
        line_count = 0
        fixed_count = 0
        subassembly_count = 0
        needs_pm_count = 0
        curved_count = 0
        high_seam_count = 0
        overdue_count = 0
        slack_values: List[float] = []

        for block in remaining_blocks:
            port_state = getattr(block, 'port_starboard', PortStarboard.NONE)
            if port_state != PortStarboard.NONE:
                ps_blocks += 1

            assembly_type = getattr(block, 'assembly_type', None)
            assembly_value = getattr(assembly_type, 'value', str(assembly_type)).lower()
            if 'line' in assembly_value:
                line_count += 1
            elif 'fixed' in assembly_value:
                fixed_count += 1

            if getattr(block, 'is_subassembly', False):
                subassembly_count += 1

            try:
                if block.needs_afternoon_start():
                    needs_pm_count += 1
            except Exception:
                pass

            if getattr(block, 'has_curved_plate', False):
                curved_count += 1
            if getattr(block, 'is_high_seam_block', False):
                high_seam_count += 1

            slack = self._compute_slack_days(block, current_time)
            slack_values.append(slack)
            if slack < 0:
                overdue_count += 1

        ps_pair_ratio = self._safe_ratio(ps_blocks, remaining_count)
        state.extend([
            ps_pair_ratio,
            self._safe_ratio(line_count, remaining_count),
            self._safe_ratio(fixed_count, remaining_count),
            self._safe_ratio(subassembly_count, remaining_count),
            self._safe_ratio(needs_pm_count, remaining_count),
            self._safe_ratio(curved_count, remaining_count),
            self._safe_ratio(high_seam_count, remaining_count)
        ])

        count_35 = float(self.branch_usage_counts.get('35A', 0))
        count_36 = float(self.branch_usage_counts.get('36B', 0))
        branch_total = max(1.0, count_35 + count_36)
        bay_balance_delta = self._compute_bay_balance_delta()
        state.extend([
            bay_balance_delta,
            self._safe_ratio(count_35, branch_total),
            self._safe_ratio(count_36, branch_total)
        ])

        warning_ratio, error_ratio = self._compute_constraint_debt(env)
        state.extend([warning_ratio, error_ratio])

        available_ids = getattr(env, '_last_available_ids', []) or []
        available_ratio = self._safe_ratio(len(available_ids), remaining_count)
        diversity = 0.0
        if available_ids:
            line_keys = []
            for bid in available_ids:
                blk = blocks_dict.get(bid)
                if blk:
                    line_keys.append(self._normalize_line_key(getattr(blk, 'line_group', None)))
            if line_keys:
                diversity = len(set(line_keys)) / len(line_keys)

        available_ps_forced_ratio = 0.0
        available_masking_stage_mean = 0.0
        if block_analysis:
            available_entries = [a for a in block_analysis if a.get('is_available')]
            if available_entries:
                ps_forced_count = sum(1 for a in available_entries if a.get('ps_forced'))
                available_ps_forced_ratio = self._safe_ratio(ps_forced_count, len(available_entries))
                stage_values = [self._encode_masking_stage(a.get('masking_stage')) for a in available_entries]
                if stage_values:
                    available_masking_stage_mean = float(sum(stage_values) / len(stage_values))

        state.extend([
            available_ratio,
            diversity,
            available_ps_forced_ratio,
            available_masking_stage_mean
        ])

        overdue_ratio = self._safe_ratio(overdue_count, remaining_count)
        if slack_values:
            avg_slack = sum(slack_values) / len(slack_values)
            min_slack = min(slack_values)
            max_slack = max(slack_values)
            max_abs_slack = max(abs(min_slack), abs(max_slack))
        else:
            avg_slack = 0.0
            min_slack = 0.0
            max_slack = 0.0
            max_abs_slack = 0.0

        # [AGENT-EDIT] slack도 자동 최대치 기반 정규화
        if max_abs_slack > 0:
            self.env_state_max_abs_slack = max(self.env_state_max_abs_slack, max_abs_slack)
        slack_scale = max(1e-6, self.env_state_max_abs_slack)
        def _scale_slack(value: float) -> float:
            clipped = max(-slack_scale, min(slack_scale, value))
            return clipped / slack_scale

        state.extend([
            overdue_ratio,
            _scale_slack(avg_slack),
            _scale_slack(min_slack),
            _scale_slack(max_slack)
        ])

        if len(state) != self.env_state_dim:
            # [AGENT-EDIT] 안전장치: 차원 불일치 시 패딩/절단
            state = state[:self.env_state_dim]
            if len(state) < self.env_state_dim:
                state.extend([0.0] * (self.env_state_dim - len(state)))

        if self._env_state_debug_prints < 5:
            preview = np.round(state[:10], 3).tolist()
            # print(f"[EnvState] sample#{self._env_state_debug_prints+1}: first10={preview} ... len={len(state)}")
            self._env_state_debug_prints += 1

        return state

    def _rl_block_selection(self, 
                           available_blocks: List, 
                           blocks_dict: Dict, 
                           selected_blocks: List[int], 
                           current_time,
                           env,
                           training_mode: bool = False,
                           env_state_vector: Optional[List[float]] = None,
                           analysis_map: Optional[Dict[int, Dict]] = None,
                           forced_block_id: Optional[int] = None) -> Tuple[int, float, torch.Tensor]:
        """
        hope.txt 방식: 통합 상태 벡터를 사용한 RL 블록 선택
        """
        if not available_blocks:
            raise ValueError("선택 가능한 블록이 없습니다")
        
        # 상태 벡터 제거 - 블록 특성만 사용
        
        # 🔥 2. 선택 가능한 블록들의 개별 특성 (처리시간 위주로 단순화)
        from PPO.models.single_step_actor import extract_block_features_single
        
        ############
        # 수정했음 #
        ############
        current_branch_counts = dict(self.branch_usage_counts)
        workshop_backlog_counts, line_backlog_counts = self._compute_backlogs(blocks_dict, selected_blocks)
        ps_pair_ratio = self._compute_ps_pair_ratio(blocks_dict, selected_blocks)
        capacity_snapshot = self._compute_capacity_snapshot(env, current_time)
        bay_balance_delta = self._compute_bay_balance_delta()
        total_remaining = max(1, len(blocks_dict) - len(selected_blocks))

        available_features_list = []
        available_indices = []

        for block_id in available_blocks:
            block = blocks_dict[block_id]
            slack_days = self._compute_slack_days(block, current_time)
            workshop_code = self._normalize_workshop_key(getattr(block, 'assembly_workshop_code', None))
            line_key = self._normalize_line_key(getattr(block, 'line_group', None))
            workshop_backlog = max(0.0, workshop_backlog_counts.get(workshop_code, 0) - 1)
            workshop_backlog_ratio = self._safe_ratio(workshop_backlog, total_remaining)
            line_backlog = max(0.0, line_backlog_counts.get(line_key, 0) - 1)
            line_backlog_ratio = self._safe_ratio(line_backlog, total_remaining)
            count_35a = float(current_branch_counts.get('35A', 0))
            count_36b = float(current_branch_counts.get('36B', 0))
            analysis = analysis_map.get(block_id) if analysis_map else None
            inclusion_reason = (analysis.get('inclusion_reason') if analysis else '') or ''
            mask_stage_score = self._encode_masking_stage((analysis.get('masking_stage') if analysis else None))
            three_bay_state = 0.0
            if analysis:
                bay_state = (analysis.get('three_bay_check') or '').upper()
                if 'FAIL' in bay_state:
                    three_bay_state = 1.0
                elif 'RELAX' in bay_state:
                    three_bay_state = 0.5
            ps_forced_flag = 1.0 if (analysis and analysis.get('ps_forced')) else 0.0

            candidate_branch_35a = 0.0
            candidate_branch_36b = 0.0
            try:
                candidate_bay = env._preview_assign_bay(block)
                if candidate_bay == BayType.BAY_35A:
                    candidate_branch_35a = 1.0
                elif candidate_bay == BayType.BAY_36B:
                    candidate_branch_36b = 1.0
            except Exception:
                candidate_bay = None

            ########################################################################
            # [FIX] 최소 한 개의 후보 플래그는 1이 되도록 보정
            ########################################################################
            if candidate_branch_35a == 0.0 and candidate_branch_36b == 0.0:
                line_key = self._normalize_line_key(getattr(block, 'line_group', None))
                if line_key == '35A':
                    candidate_branch_35a = 1.0
                elif line_key == '36B':
                    candidate_branch_36b = 1.0
                else:
                    if current_branch_counts.get('35A', 0) <= current_branch_counts.get('36B', 0):
                        candidate_branch_35a = 1.0
                    else:
                        candidate_branch_36b = 1.0

            if candidate_branch_35a == 1.0 and candidate_branch_36b == 1.0:
                if current_branch_counts.get('35A', 0) > current_branch_counts.get('36B', 0):
                    candidate_branch_35a = 0.0
                else:
                    candidate_branch_36b = 0.0

            line_is_fixed, line_is_line = self._classify_line_flags(block)
            deadline_urgency = self._compute_deadline_urgency(slack_days)

            block_features = extract_block_features_single(
                block,
                slack_days=slack_days,
                feature_mode=self.feature_mode,
                branch_count_35a=count_35a,
                branch_count_36b=count_36b,
                workshop_backlog_ratio=workshop_backlog_ratio,
                line_backlog_ratio=line_backlog_ratio,
                ps_pair_remaining_ratio=ps_pair_ratio,
                daily_seam_usage_ratio=capacity_snapshot['daily_seam_usage_ratio'],
                daily_block_usage_ratio=capacity_snapshot['daily_block_usage_ratio'],
                remaining_capacity_ratio=capacity_snapshot['remaining_capacity_ratio'],
                candidate_branch_35a=candidate_branch_35a,
                candidate_branch_36b=candidate_branch_36b,
                masking_stage_value=mask_stage_score,
                three_bay_state=three_bay_state,
                ps_forced_flag=ps_forced_flag,
                bay_balance_delta=bay_balance_delta,
                deadline_urgency=deadline_urgency,
                line_is_fixed=line_is_fixed,
                line_is_line=line_is_line,
            )
            available_features_list.append(block_features)
            available_indices.append(block_id)
        
        # 리스트로 유지 (Actor가 numpy array로 변환함)
        available_features = available_features_list  # List of features
        
        env_state = env_state_vector if self.use_env_state else None
        
        # RL Agent로 블록 선택 (hope.txt 방식: 상태 벡터 전달)
        if training_mode:
            self.rl_agent.train()
        else:
            self.rl_agent.eval()
        
        decode_type = getattr(self.rl_agent, 'default_decode_type', 'sampling')
        
        with torch.set_grad_enabled(training_mode):
            # agent/actor.py 방식: 블록 특성만 전달 (+ 선택적 env_state)
            selected_original_index, confidence, action_logits = self.rl_agent.forward_single_step(
                available_block_features=available_features,
                available_indices=available_indices,
                device=self.device,
                decode_type=decode_type,
                env_state=env_state
            )

        # ==== [AGENT-EDIT BEGIN: RL-Step 로그 비활성화] ====
        # [AGENT-ADD] Step log: candidate count + top logits (opt-in)
        # log_flag = os.environ.get("PBS_RL_STEP_LOG", "").strip().lower() in {"1", "true", "on", "yes"}
        # if log_flag:
        #     try:
        #         log_probs = action_logits
        #         if isinstance(log_probs, torch.Tensor):
        #             top_k = min(3, log_probs.numel())
        #             top_vals, top_idx = torch.topk(log_probs, top_k)
        #             top_items = []
        #             for v, i in zip(top_vals.tolist(), top_idx.tolist()):
        #                 block_id = available_indices[i]
        #                 top_items.append(f"{block_id}:{v:+.3f}")
        #             top_str = ", ".join(top_items)
        #         else:
        #             # list/array fallback
        #             scores = list(log_probs)
        #             top_k = min(3, len(scores))
        #             ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        #             top_items = [f"{available_indices[i]}:{scores[i]:+.3f}" for i in ranked]
        #             top_str = ", ".join(top_items)
        #         print(f"[RL-Step] candidates={len(available_indices)} sel={selected_original_index} conf={confidence:.3f} top_logp={top_str}")
        #     except Exception:
        #         print(f"[RL-Step] candidates={len(available_indices)} sel={selected_original_index} conf={confidence:.3f}")
        # ==== [AGENT-EDIT END] ====

        # [AGENT-ADD] 강제 시퀀스가 주어진 경우 해당 블록을 선택 (가능하면)
        forced_used = False
        if forced_block_id is not None and forced_block_id in available_indices:
            selected_original_index = forced_block_id
            forced_used = True
            try:
                forced_action_idx = available_indices.index(selected_original_index)
                if isinstance(action_logits, torch.Tensor):
                    forced_log_prob = action_logits[forced_action_idx]
                    confidence = float(torch.exp(forced_log_prob).detach().cpu().item())
                else:
                    forced_log_prob = action_logits[forced_action_idx]
                    confidence = float(np.exp(forced_log_prob))
            except Exception:
                confidence = 1.0
        
        selected_analysis = analysis_map.get(selected_original_index) if analysis_map else None

        if training_mode:
            selected_action_idx = available_indices.index(selected_original_index)
            
            # gradient 유지를 위해 no_grad 제거
            ########################################################################
            # FIX: forward_single_step already returns log probabilities.
            log_probs = action_logits
            selected_log_prob = log_probs[selected_action_idx]
            ########################################################################
            
            immediate_reward = 0.0
            current_makespan = 0.0
            previous_makespan = 0.0
            
            step_index = len(self.episode_data)
            # 수정: 현재 선택 후 상태를 current로, 선택 전 상태를 previous로
            current_selected_blocks = selected_blocks + [selected_original_index]  # 선택 후
            previous_selected_blocks = selected_blocks  # 선택 전
            try:
                current_bay_assignments = {}
                for block_id in current_selected_blocks:
                    if block_id in blocks_dict:
                        block = blocks_dict[block_id]
                        assigned_bay = env._preview_assign_bay(block)
                        current_bay_assignments[block_id] = assigned_bay
                
                if current_selected_blocks:
                    current_makespan_sec, _ = env.calculate_makespan(
                        current_selected_blocks, 
                        current_bay_assignments, 
                        {}
                    )
                    actual_current_makespan = current_makespan_sec / 3600.0
                    
                    #  디버깅: 매 스텝 부분 makespan 확인
                    step_idx = len(self.episode_data)
                    # if step_idx < 5 or step_idx % 20 == 0 or step_idx >= len(blocks_dict) - 2:
                        # print(f"  Step {step_idx}: 선택된 블록 {len(current_selected_blocks)}개 → Makespan: {actual_current_makespan:.2f}h")
                else:
                    actual_current_makespan = 0.0
                
                # 이전 makespan 계산
                if previous_selected_blocks:  # 블록이 있으면
                    previous_bay_assignments = {}
                    for block_id in previous_selected_blocks:
                        if block_id in blocks_dict:
                            block = blocks_dict[block_id]
                            assigned_bay = env._preview_assign_bay(block)
                            previous_bay_assignments[block_id] = assigned_bay
                    
                    previous_makespan_sec, _ = env.calculate_makespan(
                        previous_selected_blocks, 
                        previous_bay_assignments, 
                        {}
                    )
                    actual_previous_makespan = previous_makespan_sec / 3600.0
                    
                    #  디버깅: 이전 makespan도 확인
                    # if step_idx < 5:
                        # print(f"    → 이전 {len(previous_selected_blocks)}개 블록 Makespan: {actual_previous_makespan:.2f}h")
                        # print(f"    → 차이 (보상): {actual_previous_makespan - actual_current_makespan:.2f}h")
                else:
                    actual_previous_makespan = 0.0  # 첫 스텝은 이전이 0
                    
            except Exception as makespan_err:
                actual_current_makespan = 0.0
                actual_previous_makespan = 0.0

            ########################################################################
            # [AGENT-EDIT] step_reward shaping 제거 (global advantage만 사용)
            ########################################################################
            env_state_payload = None
            if isinstance(env_state, list):
                env_state_payload = env_state.copy()
            elif isinstance(env_state, np.ndarray):
                env_state_payload = env_state.tolist()
            elif env_state is not None:
                env_state_payload = list(env_state)

            step_data = {
                'step_number': len(self.episode_data),
                'available_features': available_features.copy() if isinstance(available_features, list) else available_features.clone(),
                'available_indices': available_indices.copy(),
                'selected_block_id': selected_original_index,
                'selected_action_idx': selected_action_idx,
                'action_logits': action_logits.detach().cpu().tolist() if isinstance(action_logits, torch.Tensor) else action_logits,
                'log_prob': float(selected_log_prob.detach().cpu().item()) if isinstance(selected_log_prob, torch.Tensor) else float(selected_log_prob),
                'confidence': confidence,
                'selected_analysis_reason': selected_analysis.get('exclusion_reason') if selected_analysis else None,
                'selected_masking_stage': selected_analysis.get('masking_stage') if selected_analysis else None,
                'selected_three_bay_check': selected_analysis.get('three_bay_check') if selected_analysis else None,
                # [AGENT-ADD] 강제 시퀀스 여부 기록
                'forced_action': bool(forced_used),
                
                # 실제 계산된 makespan만 저장 (추정치 금지)
                'actual_makespan_hours': actual_current_makespan,  # 실제 계산된 현재 makespan
                'previous_makespan_hours': actual_previous_makespan,  # 실제 이전 makespan
                'actual_makespan_sec': current_makespan_sec if 'current_makespan_sec' in locals() else 0.0,
                
                # 블록 정보 (실제 선택된 것만)
                'current_selected_blocks': current_selected_blocks.copy(),
                'current_bay_assignments': current_bay_assignments.copy() if 'current_bay_assignments' in locals() else {},
                
                # 🆕 환경 상태 벡터 저장
                'env_state_vector': env_state_payload,
                
                # 환경 상태 (실제 상태만)
                'env_state': {
                    'current_time': current_time,
                    'step_count': len(self.episode_data),
                    'total_blocks': len(blocks_dict)
                }
            }
            self.episode_data.append(step_data)
        
        return selected_original_index, confidence, action_logits

    def _get_current_makespan(self, env, remaining_blocks: List) -> float:
        """
        현재 시점에서의 실제 makespan 계산
        """
        try:
            # 🔥 실제 선택된 블록들의 수 기반으로 정확한 현재 makespan 계산
            total_blocks = len(env.blocks_dict) if hasattr(env, 'blocks_dict') else 50
            completed_blocks = total_blocks - len(remaining_blocks)
            
            # 완료된 블록들의 실제 처리 시간 합계
            if hasattr(env, 'blocks_dict') and completed_blocks > 0:
                # 실제 선택된 블록들의 처리 시간 계산
                selected_block_ids = []
                for block_id, block in env.blocks_dict.items():
                    if block_id not in remaining_blocks:
                        selected_block_ids.append(block_id)
                
                # 선택된 블록들의 평균 처리 시간
                if selected_block_ids:
                    total_processing_time = 0
                    for block_id in selected_block_ids:
                        block = env.blocks_dict[block_id]
                        total_processing_time += sum(block.processing_times)
                    
                    # 병렬 처리를 고려한 추정 (실제 makespan은 순차 합보다 작음)
                    estimated_makespan = total_processing_time / 60.0 * 0.7  # 70% 효율 가정
                    return estimated_makespan
            
            # Fallback: 스텝 기반 추정
            return len(self.episode_data) * 2.5  # 스텝당 평균 2.5시간 가정
            
        except Exception as e:
            # 에러 발생 시 기본값 반환
            return len(self.episode_data) * 2.0  # 스텝당 2시간 가정


################################################################################################
# [AGENT-ADD] Assembly Decoding 결과 요약 공통 처리
################################################################################################
def _finalize_rl_results(
    schedule_results: List[Dict],
    all_violations: List[ConstraintViolation],
    env: EnhancedPanelBlockShop,
    blocks: List,
    rl_scheduler: RLAssemblyScheduler,
    training_mode: bool,
    afternoon_guard_blocks_all: Set[int]
) -> Tuple[List[Dict], Dict, List[Dict]]:
    # 8. 통계 계산
    total_blocks_processed = len(schedule_results)
    total_time_hours = sum(r.get('total_time_min', 0) for r in schedule_results) / 60.0
    # 휴리스틱과 동일: primary 기반 위반 합산
    total_violations = sum(r.get('violations', 0) for r in schedule_results)
    # 학습용 별도 조정 불필요
    total_violations_train = total_violations

    # 실제 makespan 계산 (간트차트와 동일한 방식: 실제 시작~끝 시간 차이)
    try:
        import pandas as pd
        start_times = []
        end_times = []

        for result in schedule_results:
            if 'start_time' in result and result['start_time']:
                try:
                    start_dt = pd.to_datetime(result['start_time'])
                    start_times.append(start_dt)
                except Exception:
                    pass
            if 'end_time' in result and result['end_time']:
                try:
                    end_dt = pd.to_datetime(result['end_time'])
                    end_times.append(end_dt)
                except Exception:
                    pass

        if start_times and end_times:
            min_start_time = min(start_times)
            max_end_time = max(end_times)
            actual_makespan_hours = (max_end_time - min_start_time).total_seconds() / 3600.0
        else:
            all_block_ids = [r['block_id'] for r in schedule_results]
            final_bay_assignments = {}
            for r in schedule_results:
                final_bay_assignments[r['block_id']] = BayType(r['assigned_bay'])

            final_makespan_sec, final_detailed = env.calculate_makespan(
                all_block_ids,
                final_bay_assignments,
                {},  # 첫날부터 시작
                afternoon_guard_blocks=afternoon_guard_blocks_all
            )
            actual_makespan_hours = final_makespan_sec / 3600.0
    except Exception:
        actual_makespan_hours = total_time_hours

    # RL 성능 통계
    rl_confidence_scores = [r.get('rl_confidence', 0) for r in schedule_results]
    avg_confidence = sum(rl_confidence_scores) / len(rl_confidence_scores) if rl_confidence_scores else 0

    # 별판 통합 행 풀기 (휴리스틱과 동일 출력 형식 유지)
    schedule_results = expand_rows_with_subassembly(schedule_results)
    total_blocks_processed = len(schedule_results)

    # [AGENT-ADD] C/Seam 위반 카운트 기본값 보강 (정의 누락 방지)
    cseam_ids = {"ROUTING_C_SEAM_SPACING", "C_SEAM_SPACING"}
    total_cseam_violations = sum(
        1
        for v in all_violations
        if getattr(v, "constraint_id", "") in cseam_ids
        and getattr(v, "severity", "").upper() in {"ERROR", "WARNING"}
    )

    statistics = {
        'total_blocks_processed': total_blocks_processed,
        'total_blocks_expected': len(blocks),
        'success_rate': (total_blocks_processed / len(blocks)) * 100 if blocks else 0.0,
        'total_time_hours': total_time_hours,
        'makespan_hours': actual_makespan_hours,
        'total_violations': total_violations,
        'total_violations_train': total_violations_train,
        'total_cseam_violations': total_cseam_violations,
        'ps_success_rate': 0,
        'ps_pairs_total': 0,
        'ps_pairs_successful': 0,
        'daily_analyses_generated': 0,
        'step_analyses_generated': 0,
        'rl_confidence_avg': avg_confidence,
        'rl_mode': 'training' if training_mode else 'evaluation'
    }

    step_data = rl_scheduler.episode_data if hasattr(rl_scheduler, 'episode_data') else []
    return schedule_results, statistics, step_data


def run_rl_assembly_decoding_sequence_with_blocks(
    blocks: List,
    metadata: Dict,
    rl_agent,
    device: torch.device,
    decoding_type: str = "assembly",
    max_days: int = 10,
    start_date: str = "2025-01-01",
    date_offset: int = 0,
    output_csv: str = "rl_assembly_results.csv",
    save_csv: bool = False,
    save_detailed: bool = False,
    training_mode: bool = False,
    use_env_step: bool = True,
    forced_sequence: Optional[List[int]] = None  # [AGENT-ADD] block_id 강제 시퀀스
) -> Tuple[List[Dict], Dict, List[Dict], 'EnhancedPanelBlockShop']:
    """
    RL Agent를 사용한 Assembly Decoding 실행
    
    Args:
        training_mode: True이면 학습 데이터 수집, False이면 평가만
        
    Returns:
        (results, statistics, episode_data, env)
    """
    
    # print(f" RL Assembly Decoding 시작 ({'학습모드' if training_mode else '평가모드'})")
    
    # 1. 시작 날짜 설정
    start_datetime = datetime.strptime(start_date, "%Y-%m-%d")
    if date_offset != 0:
        adjusted_start_datetime = start_datetime + timedelta(days=date_offset)
        actual_start_time = adjusted_start_datetime.replace(hour=8, minute=0, second=0, microsecond=0)
    else:
        actual_start_time = start_datetime.replace(hour=8, minute=0, second=0, microsecond=0)
    
    # 2. 환경 초기화
    constraint_config = ConstraintConfig()
    env = EnhancedPanelBlockShop(
        blocks=blocks,
        start_time=actual_start_time,
        constraint_config=constraint_config,
        metadata=metadata,
        decoding_mode="assembly",
        assembly_max_days=max_days,
        assembly_capacity_bypass=False,  # [AGENT-EDIT] 용량 우회 비활성화 (하드 제약 준수)
        assembly_update_state_on_capacity=True,
        assembly_update_state_on_empty=False,
        assembly_keep_bay_assignments_on_capacity=True
    )
    
    # 3. Assembly Decoding 설정
    env.constraint_checker.set_decoding_type("assembly")
    env.constraint_checker.set_assembly_blocks(blocks)
    
    # 🆕 Assembly 확장 모드는 이제 ConstraintConfig에서 설정됨
    # constraint_config에서 assembly_expansion_* 설정을 통해 제어
    
    blocks_dict = {block.block_id: block for block in blocks}
    env.constraint_checker.set_blocks_dict(blocks_dict)
    
    # 4. RL Scheduler 초기화
    rl_scheduler = RLAssemblyScheduler(rl_agent, device)
    ########################################################################
    # [FIX] 에피소드 시작 시 누적 베이 카운트 리셋
    ########################################################################
    rl_scheduler.branch_usage_counts = {'35A': 0, '36B': 0}

    ########################################################################
    # [AGENT-ADD] Assembly Decoding: env.step 기반 정석 루트
    ########################################################################
    if use_env_step:
        schedule_results: List[Dict] = []
        all_violations: List[ConstraintViolation] = []
        blocks_dict = {block.block_id: block for block in blocks}
        assembly_sequence = 1
        # [AGENT-ADD] 강제 시퀀스 포인터 (block_id 기준)
        forced_idx = 0
        forced_sequence = forced_sequence or []

        # 메인 루프 (환경이 상태를 관리)
        while not env.is_done and len(env.assembly_selected_blocks) < len(blocks):
            available_ids, violations, block_analysis = env.get_available_actions_assembly()
            if env.is_done or not available_ids:
                break

            current_datetime = env.assembly_current_datetime
            selected_blocks = env.assembly_selected_blocks.copy()

            env_state_vector = None
            if rl_scheduler.use_env_state:
                env_state_vector = rl_scheduler._extract_environment_state(
                    env, selected_blocks, blocks_dict, block_analysis
                )

            analysis_map = {analysis.get('block_id'): analysis for analysis in block_analysis}

            # [AGENT-ADD] 강제 시퀀스가 있으면 가능한 블록을 우선 사용
            forced_block_id = None
            if forced_sequence:
                while forced_idx < len(forced_sequence):
                    candidate_id = forced_sequence[forced_idx]
                    forced_idx += 1
                    if candidate_id in available_ids:
                        forced_block_id = candidate_id
                        break

            selected_block_id, confidence, action_logits = rl_scheduler._rl_block_selection(
                available_ids,
                blocks_dict,
                selected_blocks,
                current_datetime,
                env,
                training_mode,
                env_state_vector=env_state_vector,
                analysis_map=analysis_map,
                forced_block_id=forced_block_id
            )

            if selected_block_id not in available_ids:
                # 안전장치: 예측이 유효하지 않으면 첫 후보로 대체
                selected_block_id = available_ids[0]

            action_idx = available_ids.index(selected_block_id)
            _, _, done, step_info = env.step(action_idx)

            selected_block = blocks_dict[selected_block_id]
            assigned_bay = step_info.get('assigned_bay')
            bay_analysis = step_info.get('bay_analysis', {})
            block_start_time = step_info.get('block_start_time')
            block_end_time = step_info.get('block_end_time')
            makespan_sec = step_info.get('makespan_sec')
            makespan_minutes = (makespan_sec / 60.0) if makespan_sec is not None else None
            makespan_hours = (makespan_sec / 3600.0) if makespan_sec is not None else None
            total_completion_time = None
            if makespan_sec is not None:
                total_completion_time = env.assembly_date_start_time + timedelta(seconds=makespan_sec)

            # 위반 복원 (step_info 기반)
            reconstructed_violations: List[ConstraintViolation] = []
            constraint_ids = step_info.get('constraint_ids', []) or []
            violation_details = step_info.get('violation_details', []) or []
            violation_severity = step_info.get('violation_severity', []) or []
            for cid, msg, sev in zip(constraint_ids, violation_details, violation_severity):
                reconstructed_violations.append(
                    ConstraintViolation(
                        constraint_id=cid,
                        message=msg,
                        severity=sev,
                        block_id=selected_block_id
                    )
                )

            for mv in step_info.get('masking_violations', []) or []:
                reconstructed_violations.append(mv)

            # dedup 후 primary/info 분리
            primary_violations, info_violations = dedup_violations(
                reconstructed_violations, keep_info=True, include_guard=False
            )
            all_violations.extend(primary_violations)

            # [AGENT-EDIT] 원본 위반 리스트를 전달해 완화/메타 정보를 CSV에 반영
            result = create_block_result(
                selected_block,
                BayType(assigned_bay) if isinstance(assigned_bay, str) else assigned_bay,
                assembly_sequence,
                bay_analysis,
                reconstructed_violations,
                date_str=step_info.get('date_key', ''),
                start_time=block_start_time,
                end_time=block_end_time,
                makespan_minutes=makespan_minutes,
                makespan_hours=makespan_hours,
                total_completion_time=total_completion_time,
                date_start_time=env.assembly_date_start_time,
                actual_machine_2_start_time=step_info.get('actual_machine_2_start_time')
            )

            result['info_count'] = len(info_violations)
            result['info_details'] = [v.message for v in info_violations]
            result['info_constraint_ids'] = [v.constraint_id for v in info_violations]
            result['info_severity'] = [v.severity for v in info_violations]
            result['method'] = 'RL_ASSEMBLY'
            result['rl_confidence'] = round(confidence, 3)
            result['rl_available_count'] = len(available_ids)

            schedule_results.append(result)
            assembly_sequence += 1

            # 베이 사용량 누적
            if isinstance(assigned_bay, str):
                assigned_label = assigned_bay
            else:
                assigned_label = assigned_bay.value
            rl_scheduler._update_branch_usage(selected_block, BayType(assigned_label) if isinstance(assigned_label, str) else assigned_label)

        # env 기준 최종 요약 반환
        schedule_results, statistics, step_data = _finalize_rl_results(
            schedule_results,
            all_violations,
            env,
            blocks,
            rl_scheduler,
            training_mode,
            env.assembly_afternoon_guard_blocks_all
        )
        # ==== [AGENT-ADD BEGIN: env.step 경로에서도 RL 상세 CSV 생성] ====
        if save_detailed and schedule_results:
            try:
                from scheduling.assembly_start.action_sequence_조립착수일기준휴리스틱 import save_detailed_process_schedule_assembly
                from utils.csv_save import save_assembly_decoding_schedule_info, save_assembly_decoding_bay_info

                results_by_date = {}
                for result in schedule_results:
                    date_key = result.get('date')
                    if not date_key:
                        continue
                    results_by_date.setdefault(date_key, []).append(result)

                for date_key, date_results in results_by_date.items():
                    try:
                        # ==== [AGENT-EDIT BEGIN: 기존 상세 파일 삭제 로그] ====
                        for prefix in [
                            "detailed_assembly_schedule_info_",
                            "detailed_assembly_bayselect_info_",
                            "detailed_assembly_decoding_schedule_processes_",
                        ]:
                            filename = f"{prefix}{date_key}.csv"
                            if os.path.exists(filename):
                                print(f"    기존 파일 삭제: {filename}")
                                try:
                                    os.remove(filename)
                                except Exception:
                                    pass
                        # ==== [AGENT-EDIT END] ====

                        date_sequence = []
                        date_bay_assignments = {}
                        date_blocks_dict = {}

                        for r in date_results:
                            raw_block_id = r.get('subassembly_representative_id', r.get('block_id'))
                            if raw_block_id is None:
                                continue
                            try:
                                base_block_id = int(raw_block_id)
                            except (TypeError, ValueError):
                                base_block_id = raw_block_id

                            block_obj = blocks_dict.get(base_block_id)
                            if block_obj is None:
                                continue

                            if base_block_id not in date_blocks_dict:
                                date_blocks_dict[base_block_id] = block_obj

                            if base_block_id not in date_bay_assignments:
                                assigned_bay_value = r.get('assigned_bay', BayType.BAY_35A.value)
                                date_bay_assignments[base_block_id] = BayType._value2member_map_.get(
                                    assigned_bay_value,
                                    BayType.BAY_35A
                                )
                                date_sequence.append(base_block_id)

                        if not date_sequence:
                            continue

                        date_obj = datetime.strptime(date_key, '%Y%m%d')
                        date_start_time = date_obj.replace(hour=8, minute=0, second=0, microsecond=0)

                        makespan_sec, detailed = env.calculate_makespan(date_sequence, date_bay_assignments, {})

                        save_detailed_process_schedule_assembly(
                            date_key=date_key,
                            sequence=date_sequence,
                            bay_assignments=date_bay_assignments,
                            ct_tables=detailed['ct_tables'],
                            blocks_dict=date_blocks_dict,
                            date_start_time=date_start_time
                        )
                        print(f"    RL Process CSV 생성: detailed_assembly_decoding_schedule_processes_{date_key}.csv")

                        assembly_step_info = []
                        for i, block_id in enumerate(date_sequence):
                            block = blocks_dict[block_id]
                            block_line_group, block_workshop_code = get_line_group_and_workshop_code(block)
                            step_info = {
                                'step': i,
                                'selected_block_id': block_id,
                                'selected_line_group': block_line_group,
                                'selected_workshop_code': block_workshop_code,
                                'available_blocks': [block_id],
                                'block_analysis': [{
                                    'block_id': block_id,
                                    'assembly_type': block.assembly_type.value,
                                    'line_group': block_line_group,
                                    'assembly_workshop_code': block_workshop_code,
                                    'port_starboard': block.port_starboard.value,
                                    'is_available': True,
                                    'inclusion_reason': 'RL_SELECTED',
                                    'constraint_checks': {}
                                }]
                            }
                            assembly_step_info.append(step_info)

                        save_assembly_decoding_schedule_info(assembly_step_info, date_key, blocks)

                        bay_analyses = []
                        for block_id in date_sequence:
                            block = blocks_dict[block_id]
                            block_line_group, block_workshop_code = get_line_group_and_workshop_code(block)
                            bay_analyses.append({
                                'block_id': block_id,
                                'assigned_bay': date_bay_assignments[block_id].value,
                                'width_m': round(block.width, 1),
                                'longi_count': block.longi_count,
                                'line_group': block_line_group,
                                'assembly_workshop_code': block_workshop_code,
                                'material_type': block.material_type.value,
                                'final_reason': 'RL_AUTO_ASSIGNED',
                                'constraint_checks': {},
                                'bay_analysis': {
                                    'selected_bay': date_bay_assignments[block_id].value,
                                    'reason': 'RL_AUTO_ASSIGNED'
                                }
                            })
                        # csv_save.py 시그니처: (bay_analyses, date_key)
                        save_assembly_decoding_bay_info(bay_analyses, date_key)
                    except Exception as date_err:
                        print(f"   ⚠️ RL 상세 CSV 생성 실패 ({date_key}): {date_err}")
                        import traceback
                        traceback.print_exc()
            except Exception as detailed_err:
                print(f"   ⚠️ RL 상세 CSV 생성 전체 실패: {detailed_err}")
        # ==== [AGENT-ADD END] ====
        return schedule_results, statistics, step_data, env
    
    # 5. 메인 스케줄링 루프 (핵심 수정: RL 기반 선택)
    schedule_results = []
    selected_blocks = []
    all_bay_assignments = {}  # 🆕 베이 할당 정보 저장
    
    current_date = start_datetime.date()
    current_datetime = datetime.combine(current_date, datetime.min.time().replace(hour=8))
    assembly_sequence = 1
    last_assembly_type = None
    day_counter = 1
    
    # 시간 추적 변수들
    date_start_time = actual_start_time
    total_sequence = []  #  전체 블록 시퀀스 (makespan 계산용)
    daily_sequence = []  #  현재 날짜의 시퀀스 (용량 체크용)
    current_bay_assignments = {}
    previous_machine_state = {}
    afternoon_guard_blocks_day: Set[int] = set()  # [AGENT-ADD] 리드타임 강제+P6 대상
    afternoon_guard_blocks_all: Set[int] = set()
    
    # 제약조건 위반 추적
    all_violations = []
    
    #  학습 데이터 수집용
    step_data = []
    
    # 메인 시퀀싱 루프: 전체 블록을 하나씩 선택
    loop_iteration = 0  # 🔍 디버그: 루프 반복 횟수 추적
    while len(selected_blocks) < len(blocks):
        # ==== [AGENT-EDIT BEGIN: per-step 초기화] ====
        # panel_end_seconds 등이 한 번 설정되면 이후 반복에서도 남아 잘못 재사용되므로 매 반복마다 초기화
        panel_end_seconds = None
        block_start_time = None
        block_end_time = None
        total_completion_time = None
        makespan_minutes = None
        makespan_hours = None
        # ==== [AGENT-EDIT END] ====
        loop_iteration += 1
        if day_counter > max_days:
            break
            
        # 현재 날짜 계산
        date_key = current_date.strftime('%Y%m%d')
        
        # 용량 체크를 블록 선택 전에 먼저 수행
        is_weekend = current_datetime.weekday() >= 5
        current_capacity_used = env.capacity_tracker.get_capacity_used(is_weekend)
        
        #  용량 한계 계산
        is_hot_season = env.calendar_manager.is_hot_season(current_datetime)
        is_holiday_eve = env.calendar_manager.is_holiday_eve(current_datetime)
        actual_capacity_limit = env.capacity_tracker.get_capacity_limits(is_weekend, is_hot_season, is_holiday_eve)
        
        #  디버깅: 용량 상태 출력 (휴리스틱과 동일)
        # print(f"    [RL] 용량 상태: {current_capacity_used}/{actual_capacity_limit}심 (혹서기: {is_hot_season}, 주말: {is_weekend}, 명절전날: {is_holiday_eve})")

        # 🔥 리드타임 초과/임박 블록이 있으면 용량이 차도 하루를 넘기지 않고 선택 시도
        min_lead_days = getattr(env.constraint_config, 'minimum_panel_lead_days', 2)
        overdue_exists = False
        urgent_exists = False
        for blk in blocks:
            if blk.block_id in selected_blocks:
                continue
            start_dt = getattr(blk, 'assembly_start_date', None)
            if not isinstance(start_dt, datetime):
                continue
            slack_days = (start_dt.date() - current_date).days
            if slack_days <= min_lead_days:
                urgent_exists = True
            if current_date > (start_dt.date() + timedelta(days=min_lead_days)):
                overdue_exists = True
                urgent_exists = True
                break

        capacity_bypass = False
        if current_capacity_used >= actual_capacity_limit and not urgent_exists:
            # 용량 한계지만 리드타임 급한 블록이 없으면 다음날로 이동
            if daily_sequence and current_bay_assignments:
                try:
                    makespan_sec, detailed = env.calculate_makespan(
                        daily_sequence, current_bay_assignments, previous_machine_state,
                        afternoon_guard_blocks=afternoon_guard_blocks_day
                    )
                    
                    final_state = detailed.get('final_machine_state', {}) or {}
                    day_duration_seconds = 24 * 3600
                    prev_offset = previous_machine_state.get('day_start_offset', 0) if isinstance(previous_machine_state, dict) else 0
                    new_offset = prev_offset + day_duration_seconds
                    def _shift(lst):
                        return [t + prev_offset for t in lst] if isinstance(lst, list) else []
                    previous_machine_state = {
                        'common_times': _shift(final_state.get('common_times', [])),
                        'branch_a_times': _shift(final_state.get('branch_a_times', [])),
                        'branch_b_times': _shift(final_state.get('branch_b_times', [])),
                        'day_start_offset': new_offset
                    }
                except Exception as e:
                    pass

            current_date += timedelta(days=1)
            current_datetime = datetime.combine(current_date, datetime.min.time().replace(hour=8))
            day_counter += 1
            
            date_start_time = current_datetime
            daily_sequence = []  #  새로운 날의 시퀀스 시작 (용량 체크용)
            afternoon_guard_blocks_day = set()
            # total_sequence와 current_bay_assignments는 유지!
            
            # 용량 리셋
            is_weekend = current_datetime.weekday() >= 5
            if is_weekend:
                env.capacity_tracker.reset_weekend()
            else:
                env.capacity_tracker.reset_daily()
            
            if day_counter > max_days:
                break
            
            #  용량 한계로 다음날로 넘어간 경우 continue
            continue
        elif current_capacity_used >= actual_capacity_limit and urgent_exists:
            # 리드타임 임박/초과 블록이 있으므로 용량 제약을 임시 완화하여 선택 시도
            capacity_bypass = True
        
        # 선택 가능한 블록 찾기 (용량에 여유가 있을 때만)
        env.constraint_checker.set_sequence(selected_blocks)
        if capacity_bypass:
            cfg = env.constraint_config
            orig_flags = {
                'enable_p5_8_weekday_capacity': cfg.enable_p5_8_weekday_capacity,
                'enable_p5_9_block_count_check': cfg.enable_p5_9_block_count_check,
                'enable_p5_10_weekend_capacity': cfg.enable_p5_10_weekend_capacity,
                'enable_p5_16_hot_season_capacity': cfg.enable_p5_16_hot_season_capacity,
            }
            cfg.enable_p5_8_weekday_capacity = False
            cfg.enable_p5_9_block_count_check = False
            cfg.enable_p5_10_weekend_capacity = False
            cfg.enable_p5_16_hot_season_capacity = False
        try:
            available_ids, violations, block_analysis = env.constraint_checker.get_next_available_blocks_assembly(
                blocks, current_datetime, selected_blocks, last_assembly_type,
                include_relax_candidates=False,  # [AGENT-EDIT] RL/self-label 경로는 기본 후보만 사용
                panel_date=current_date,
                previous_machine_state=previous_machine_state,
                current_bay_assignments=current_bay_assignments,
                current_day_selected_blocks=daily_sequence
            )
        finally:
            if capacity_bypass:
                cfg.enable_p5_8_weekday_capacity = orig_flags['enable_p5_8_weekday_capacity']
                cfg.enable_p5_9_block_count_check = orig_flags['enable_p5_9_block_count_check']
                cfg.enable_p5_10_weekend_capacity = orig_flags['enable_p5_10_weekend_capacity']
                cfg.enable_p5_16_hot_season_capacity = orig_flags['enable_p5_16_hot_season_capacity']
        # 저장하여 state에서 available_ratio/diversity에 활용
        env._last_available_ids = available_ids.copy()

        masking_violation_map: Dict[Optional[int], List[ConstraintViolation]] = defaultdict(list)
        for violation in violations:
            block_id = getattr(violation, 'block_id', None)
            if block_id is None:
                continue
            masking_violation_map[block_id].append(violation)

        
        if not available_ids:

            current_date += timedelta(days=1)
            current_datetime = datetime.combine(current_date, datetime.min.time().replace(hour=8))
            day_counter += 1
            
            # 공장 연속 가동 고려
            date_start_time = current_datetime
            final_sequence = []
            current_bay_assignments = {}
            # 히스토리 리셋은 하지 않음: completed_steps를 유지해 워크숍 순서/라우팅이 전일 처리 블록을 인식하도록 함
            
            # 용량 리셋
            is_weekend = current_datetime.weekday() >= 5
            if is_weekend:
                env.capacity_tracker.reset_weekend()
            else:
                env.capacity_tracker.reset_daily()
            
            if day_counter > max_days:
                break
            continue
        
        env_state_vector = None
        if rl_scheduler.use_env_state:
            env_state_vector = rl_scheduler._extract_environment_state(env, selected_blocks, blocks_dict, block_analysis)
        analysis_map = {analysis.get('block_id'): analysis for analysis in block_analysis}
        
        # 🤖 핵심: RL Agent를 사용한 블록 선택 (523행 교체)
        selected_block_id, confidence, action_logits = rl_scheduler._rl_block_selection(
            available_ids, blocks_dict, selected_blocks, current_datetime, env, training_mode,
            env_state_vector=env_state_vector,
            analysis_map=analysis_map
        )
        
        selected_block = blocks_dict[selected_block_id]
        selected_analysis = next((analysis for analysis in block_analysis if analysis.get('block_id') == selected_block_id), None)
        is_afternoon_guard = False
        if selected_analysis:
            inc = (selected_analysis.get('inclusion_reason') or '').strip()
            if inc.startswith('[LEADTIME-GUARD]') and selected_block.needs_afternoon_start():
                is_afternoon_guard = True
                afternoon_guard_blocks_day.add(selected_block_id)
                afternoon_guard_blocks_all.add(selected_block_id)
        selected_block_load = selected_block.seam_count + getattr(selected_block, 'c_seam_count', 0)

        #  추가 안전장치: 선택된 블록이 용량을 초과하는지 재확인
        # (이미 위에서 체크했지만, 혹시 모를 상황을 대비)
        # print(f"    [RL] 블록 선택 후 용량 체크: {current_capacity_used} + {selected_block_load} = {current_capacity_used + selected_block_load} vs {actual_capacity_limit}")
        if current_capacity_used + selected_block_load > actual_capacity_limit:
            # print(f"    [RL] 선택 후 용량 초과 감지: {current_capacity_used + selected_block_load} > {actual_capacity_limit}")
            # print(f"    [RL] 다음날로 이동 (선택된 블록: {selected_block.block_id}) - 블록 처리하지 않음")
            
            #  날짜 전환 전에 이전 날의 머신 상태 저장
            if daily_sequence and current_bay_assignments:
                try:
                    # 현재 날짜의 makespan 계산
                    makespan_sec, detailed = env.calculate_makespan(
                        daily_sequence, current_bay_assignments, previous_machine_state,
                        afternoon_guard_blocks=afternoon_guard_blocks_day
                    )
                    
                    # 다음 날로 전달할 머신 상태 저장
                    previous_machine_state = detailed.get('final_machine_state', {})
                    # 다음 날 시작 시간 오프셋 계산 (24시간 = 86400초)
                    day_duration_seconds = 24 * 3600
                    previous_machine_state['day_start_offset'] = day_duration_seconds
                except Exception as e:
                    pass

            current_date += timedelta(days=1)
            current_datetime = datetime.combine(current_date, datetime.min.time().replace(hour=8))
            day_counter += 1
            
            date_start_time = current_datetime
            daily_sequence = []  # 새로운 날의 시퀀스 시작 (용량 체크용)
            afternoon_guard_blocks_day = set()
            # total_sequence와 current_bay_assignments는 유지!
            
            # 용량 리셋
            is_weekend = current_datetime.weekday() >= 5
            if is_weekend:
                env.capacity_tracker.reset_weekend()
            else:
                env.capacity_tracker.reset_daily()
            
            if day_counter > max_days:
                break
            
            # 중요: 용량 초과로 다음날 이동 시 선택된 블록을 처리하지 않고 continue
            continue
        
        # 블록 처리
        # print(f"   ✅ [RL] 블록 처리 진행: {selected_block.block_id} ({selected_block.seam_count}심)")
        assigned_bay, bay_analysis = env._auto_assign_bay(selected_block, return_analysis=True)
        current_bay_assignments[selected_block_id] = assigned_bay
        all_bay_assignments[selected_block_id] = assigned_bay  # 전역 베이 할당 저장
        ########################################################################
        # [FIX] 누적 베이 사용량 업데이트
        ########################################################################
        rl_scheduler._update_branch_usage(selected_block, assigned_bay)
        
        # makespan 계산
        total_sequence.append(selected_block_id)  # 전체 시퀀스에 추가
        daily_sequence.append(selected_block_id)  # 날짜별 시퀀스에도 추가
        try:
            # 현재 날짜의 makespan 계산 (머신 상태 포함)
            makespan_sec, detailed = env.calculate_makespan(
                daily_sequence, current_bay_assignments, previous_machine_state,
                afternoon_guard_blocks=afternoon_guard_blocks_day
            )
            bs = next((b for b in detailed['block_schedules'] if b['block_id'] == selected_block_id), None)
            if bs:
                block_start_time = date_start_time + timedelta(seconds=bs['start_seconds'])
                block_end_time = date_start_time + timedelta(seconds=bs['end_seconds'])
                total_completion_time = date_start_time + timedelta(seconds=makespan_sec)
                makespan_minutes = makespan_sec / 60.0
                makespan_hours = makespan_minutes / 60.0
            else:
                # Fallback 계산
                block_total_time = sum(selected_block.processing_times)
                block_start_time = date_start_time + timedelta(minutes=sum(sum(blocks_dict[bid].processing_times) for bid in final_sequence[:-1]))
                block_end_time = block_start_time + timedelta(minutes=block_total_time)
                makespan_minutes = (block_end_time - date_start_time).total_seconds() / 60.0
                makespan_hours = makespan_minutes / 60.0
                total_completion_time = block_end_time
        except Exception as makespan_err:
            block_total_time = sum(selected_block.processing_times)
            block_start_time = date_start_time + timedelta(minutes=sum(sum(blocks_dict[bid].processing_times) for bid in final_sequence[:-1]))
            block_end_time = block_start_time + timedelta(minutes=block_total_time)
            makespan_minutes = (block_end_time - date_start_time).total_seconds() / 60.0
            makespan_hours = makespan_minutes / 60.0
            total_completion_time = block_end_time
        
        # 실제 시작 시간으로 제약 검증 (실제 처리된 블록이므로 모든 제약조건 검사)
        # 🆕 실제 머신 2번(전면SAW) 시작 시간 계산 (makespan 계산 결과에서 추출)
        actual_machine_2_start_time = None
        if bs and 'detailed' in locals():
            # CT 테이블에서 머신 2번(전면SAW) 시작 시간 직접 계산
            ct_common = detailed['ct_tables']['ct_common']
            block_seq_idx = daily_sequence.index(selected_block_id)
            # 머신 2번(전면SAW) 시작 시간 = 전면SAW 시작 시간 (CT 테이블은 1-based 인덱스)
            # ct_common의 컬럼: [0: 시작, 1: 판계완료, 2: 전면SAW완료, 3: TurnOver완료, 4: 후면SAW완료, 5: NC완료]
            panel_end_seconds = ct_common[block_seq_idx + 1, 1]  # 판계 완료 시간
            saw_end_seconds = ct_common[block_seq_idx + 1, 2]    # 전면SAW 완료 시간
            
            # 전면SAW 시작 시간 = 전면SAW 완료 시간 - 전면SAW 처리 시간
            saw_duration_minutes = selected_block.processing_times[1]  # 전면SAW 처리 시간 (분)
            saw_duration_seconds = saw_duration_minutes * 60
            machine_2_start_seconds = saw_end_seconds - saw_duration_seconds
            
            actual_machine_2_start_time = date_start_time + timedelta(seconds=machine_2_start_seconds)
            
            # 디버깅: 실제 계산된 머신 2번 시작 시간 출력
            # print(f"   🔧 BLK_{selected_block.block_id}: 머신2번 시작 시간 = {actual_machine_2_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
            # print(f"      📊 CT 테이블 디버깅: seq_idx={block_seq_idx}, ct_row={block_seq_idx + 1}, machine_2_seconds={machine_2_start_seconds}")
        
        # ✅ 휴리스틱과 동일하게: 검증 전에 completed_steps를 “현재까지 실제 스케줄”로 재구성
        try:
            env.completed_steps = []
            # 1) 이미 확정된 schedule_results를 기반으로 반영
            for rec in schedule_results:
                try:
                    st = pd.to_datetime(rec.get('start_time')) if rec.get('start_time') else None
                    et = pd.to_datetime(rec.get('end_time')) if rec.get('end_time') else st
                    bay_val = rec.get('assigned_bay', '35A')
                    bay_type = BayType.BAY_36B if str(bay_val) == '36B' else BayType.BAY_35A
                    pid = rec.get('block_id')
                    if st is None or pid is None:
                        continue
                    ps = ProcessStep(
                        block_id=int(pid),
                        process_num=1,
                        bay_type=bay_type,
                        start_time=st.to_pydatetime(),
                        end_time=et.to_pydatetime() if et is not None else st.to_pydatetime(),
                        processing_time=(et - st).total_seconds() if et is not None else 0.0,
                        completion_time=(et - st).total_seconds() if et is not None else 0.0,
                    )
                    env.completed_steps.append(ps)
                except Exception:
                    continue
            # 2) 이번에 선택한 블록을 추가
            ps_step = ProcessStep(
                block_id=selected_block.block_id,
                process_num=1,
                bay_type=assigned_bay,
                start_time=block_start_time if block_start_time else current_datetime,
                end_time=block_end_time if block_end_time else (block_start_time if block_start_time else current_datetime),
                processing_time=sum(selected_block.processing_times) * 60,
                completion_time=sum(selected_block.processing_times) * 60,
            )
            env.completed_steps.append(ps_step)
        except Exception:
            pass

        block_violations = env._validate_all_constraints_realtime_action(
            selected_block,
            assigned_bay,
            block_start_time if block_start_time is not None else current_datetime,
            actual_machine_2_start_time=actual_machine_2_start_time
        )

        # [AGENT-EDIT] 후공정 착수 순서 제약은 기록 유지 (오탐 방지 로직은 action_masking 쪽에서 보정)
        
        # 용량 상태 정보 수정: 실제 처리 후 상태로 업데이트
        # 제약조건 검사에서 나온 용량 정보가 틀렸을 수 있으므로, 실제 상태로 교체
        corrected_violations = []
        for violation in block_violations:
            if violation.constraint_id in ['P5#8', 'P5#9', 'P5#10', 'P5#16']:
                # 용량 관련 제약조건은 실제 처리 후 상태로 교체
                actual_capacity_after = env.capacity_tracker.get_capacity_used(is_weekend)
                
                # 새로운 메시지 생성 (실제 상태 기반)
                if '초과' in violation.message:
                    # ERROR: 초과 상황 (이미 다음날 이동으로 처리했으므로 제외)
                    continue
                else:
                    # INFO: 정보성 메시지는 실제 상태로 업데이트
                    corrected_message = f"혹서기 심수 제한: {actual_capacity_after}/{actual_capacity_limit}심 (하이퍼파라미터: 6심 절대값 감소)"
                    
                    # 새로운 violation 객체 생성 (실제 상태 반영)
                    corrected_violation = ConstraintViolation(
                        constraint_id=violation.constraint_id,
                        message=corrected_message,
                        severity='INFO',  # 실제 처리된 블록이므로 INFO로
                        block_id=selected_block.block_id
                    )
                    corrected_violations.append(corrected_violation)
            else:
                # 용량 외 제약조건은 그대로 유지
                corrected_violations.append(violation)
        
        block_violations = corrected_violations

        # 🆕 혹서기 시즌에는 휴리스틱과 동일하게 P5#16 INFO를 보강 기록
        if is_hot_season:
            actual_capacity_after = env.capacity_tracker.get_capacity_used(is_weekend)
            has_p5_16 = any(v.constraint_id == 'P5#16' for v in block_violations)
            if not has_p5_16:
                info_violation = ConstraintViolation(
                    constraint_id='P5#16',
                    message=f"혹서기 심수 제한: {actual_capacity_after}/{actual_capacity_limit}심 (하이퍼파라미터: {env.capacity_tracker.hot_season_seam_reduction}심 절대값 감소)",
                    severity='INFO',
                    block_id=selected_block.block_id
                )
                block_violations.append(info_violation)

        selected_analysis = analysis_map.get(selected_block_id) if analysis_map else None
        existing_violation_keys = {
            (violation.constraint_id, violation.message) for violation in block_violations
        }
        existing_messages = {violation.message for violation in block_violations}
        if selected_analysis:
            inclusion_reason = (selected_analysis.get('inclusion_reason') or '').strip()
            # [AGENT-ADD] 리드타임 강제+P6 대상 트래킹 → 15시 이후 강제 지연 적용
            if inclusion_reason.startswith('[LEADTIME-GUARD]') and selected_block.needs_afternoon_start():
                afternoon_guard_blocks_day.add(selected_block_id)
                afternoon_guard_blocks_all.add(selected_block_id)
            if inclusion_reason:
                if inclusion_reason.startswith('[RELAX]'):
                    relax_violation = ConstraintViolation(
                        constraint_id='RELAX_STAGE',
                        message=inclusion_reason,
                        severity='INFO',  # 완화 메시지는 정보로만 남김
                        block_id=selected_block.block_id
                    )
                    key = (relax_violation.constraint_id, relax_violation.message)
                    if key not in existing_violation_keys and relax_violation.message not in existing_messages:
                        block_violations.append(relax_violation)
                        existing_violation_keys.add(key)
                        existing_messages.add(relax_violation.message)
                if '비상 모드' in inclusion_reason:
                    emergency_violation = ConstraintViolation(
                        constraint_id='EMERGENCY_MODE',
                        message=f"비상 모드 적용: {inclusion_reason}",
                        # [AGENT-EDIT] Treat emergency mode as INFO to avoid double counting.
                        severity='INFO',
                        block_id=selected_block.block_id
                    )
                    key = (emergency_violation.constraint_id, emergency_violation.message)
                    if key not in existing_violation_keys and emergency_violation.message not in existing_messages:
                        block_violations.append(emergency_violation)
                        existing_violation_keys.add(key)
                        existing_messages.add(emergency_violation.message)

        for masking_violation in masking_violation_map.get(selected_block_id, []):
            key = (masking_violation.constraint_id, masking_violation.message)
            if key in existing_violation_keys or masking_violation.message in existing_messages:
                continue
            block_violations.append(masking_violation)
            existing_violation_keys.add(key)
            existing_messages.add(masking_violation.message)

        # [AGENT-EDIT] 후공정 착수 순서 제약은 기록 유지 (오탐 방지 로직은 action_masking 쪽에서 보정)

        # ✅ 완료 스텝 기록: 이후 블록의 라우팅/워크숍 순서 검증이 앞선 블록을 인식하도록 유지
        try:
            ps_step = ProcessStep(
                block_id=selected_block.block_id,
                process_num=1,
                bay_type=assigned_bay,
                start_time=block_start_time,
                end_time=block_end_time,
                processing_time=sum(selected_block.processing_times) * 60,
                completion_time=sum(selected_block.processing_times) * 60,
            )
            env.completed_steps.append(ps_step)
        except Exception:
            pass

        # 🧹 휴리스틱과 동일: 위반 dedup 후 ERROR/WARNING만 카운트
        primary_violations, info_violations = dedup_violations(
            block_violations, keep_info=True, include_guard=False
        )
        violation_count = len([v for v in primary_violations if v.severity in ['ERROR', 'WARNING']])

        # 🆕 제약조건 위반 추적 (primary만 집계용)
        all_violations.extend(primary_violations)
        
        # [AGENT-EDIT] 원본 위반 리스트를 전달해 완화/메타 정보를 CSV에 반영
        result = create_block_result(
            selected_block,
            assigned_bay,
            assembly_sequence,
            bay_analysis,
            block_violations,  # 원본 위반 전달
            date_str=current_date.strftime('%Y%m%d'),
            start_time=block_start_time,
            end_time=block_end_time,
            makespan_minutes=makespan_minutes,
            makespan_hours=makespan_hours,
            total_completion_time=total_completion_time,
            date_start_time=date_start_time,
            actual_machine_2_start_time=actual_machine_2_start_time
        )

        # INFO/메타 메시지를 별도 필드로 기록
        result['info_count'] = len(info_violations)
        result['info_details'] = [v.message for v in info_violations]
        result['info_constraint_ids'] = [v.constraint_id for v in info_violations]
        result['info_severity'] = [v.severity for v in info_violations]

        # RL 전용 필드 업데이트
        result['method'] = 'RL_ASSEMBLY'
        result['rl_confidence'] = round(confidence, 3)
        result['rl_available_count'] = len(available_ids)

        schedule_results.append(result)
        selected_blocks.append(selected_block_id)
    
        
 
        # ✅ 시간 진행: 판계 완료 시각(공정1 종료) 기준으로 다음 선택 시간 이동
        # panel_end_seconds가 계산되지 않은 경우에도 최소 판계 시간만큼은 진행하도록 보완
        if panel_end_seconds is not None:
            panel_end_time = date_start_time + timedelta(seconds=panel_end_seconds)
        elif block_start_time is not None:
            panel_end_time = block_start_time + timedelta(minutes=selected_block.processing_times[0])
        else:
            panel_end_time = current_datetime + timedelta(minutes=selected_block.processing_times[0])

        # [AGENT-EDIT] P6 시각 추적: PBS_FORCE_DEBUG가 켜진 경우에만 출력
        debug_force = os.environ.get("PBS_FORCE_DEBUG", "").strip().lower() in {"1", "true", "on", "yes"}
        if debug_force and selected_block.block_id in {12, 39, 50}:
            debug_msg = f"[DEBUG] BLK_{selected_block.block_id} 선택"
            debug_msg += f" | current_datetime={current_datetime.strftime('%Y-%m-%d %H:%M')}"
            debug_msg += f" | panel_end_time={panel_end_time.strftime('%Y-%m-%d %H:%M')}"
            if actual_machine_2_start_time:
                debug_msg += f" | machine2_start={actual_machine_2_start_time.strftime('%Y-%m-%d %H:%M')}"
            print(debug_msg)

        current_datetime = panel_end_time
        env.current_time = current_datetime
        
        # 상태 업데이트
        env.constraint_checker.mark_block_selected(selected_block_id)
        
        #  용량 업데이트 후 상태 출력
        updated_capacity_used = env.capacity_tracker.get_capacity_used(is_weekend)
        # print(f"   [RL] 용량 업데이트: {current_capacity_used} → {updated_capacity_used}심 (추가: {selected_block.seam_count}심)")
        
        assembly_sequence += 1
        last_assembly_type = selected_block.assembly_type
    
    # 6. 결과 저장
    if save_csv:
        import pandas as pd
        df = pd.DataFrame(schedule_results)
        df.to_csv(output_csv, index=False, encoding='utf-8-sig')
        # print(f"  RL 결과 저장 완료: {output_csv}")
    
    # 상세 CSV 생성 (action_sequence_조립착수일기준휴리스틱.py와 동일)
    if save_detailed and schedule_results:
        try:
            from scheduling.assembly_start.action_sequence_조립착수일기준휴리스틱 import save_detailed_process_schedule_assembly
            from utils.csv_save import save_assembly_decoding_schedule_info, save_assembly_decoding_bay_info
            
            # 날짜별로 결과 그룹화
            results_by_date = {}
            for result in schedule_results:
                date_key = result['date']
                if date_key not in results_by_date:
                    results_by_date[date_key] = []
                results_by_date[date_key].append(result)
            
            # 각 날짜별로 상세 CSV 생성
            for date_key, date_results in results_by_date.items():
                try:
                    # 해당 날짜의 시퀀스를 대표 블록 기준으로 재구성 (서브어셈블리 확장 대응)
                    date_sequence = []
                    date_bay_assignments = {}
                    date_blocks_dict = {}
                    
                    for r in date_results:
                        raw_block_id = r.get('subassembly_representative_id', r.get('block_id'))
                        if raw_block_id is None:
                            continue
                        try:
                            base_block_id = int(raw_block_id)
                        except (TypeError, ValueError):
                            base_block_id = raw_block_id
                        
                        block_obj = blocks_dict.get(base_block_id)
                        if block_obj is None:
                            continue
                        
                        if base_block_id not in date_blocks_dict:
                            date_blocks_dict[base_block_id] = block_obj
                        
                        if base_block_id not in date_bay_assignments:
                            assigned_bay_value = r.get('assigned_bay', BayType.BAY_35A.value)
                            date_bay_assignments[base_block_id] = BayType._value2member_map_.get(
                                assigned_bay_value,
                                BayType.BAY_35A
                            )
                            date_sequence.append(base_block_id)
                    
                    if not date_sequence:
                        print(f"   ⚠️ RL 상세 공정 CSV 생성 스킵 ({date_key}): 유효한 블록 없음")
                        continue
                    
                    # 날짜 시작 시간 계산
                    date_obj = datetime.strptime(date_key, '%Y%m%d')
                    date_start_time = date_obj.replace(hour=8, minute=0, second=0, microsecond=0)
                    
                    # 해당 날짜의 makespan 계산
                    makespan_sec, detailed = env.calculate_makespan(date_sequence, date_bay_assignments, {})
                    
                    # 상세 공정별 스케줄 CSV 생성
                    try:
                        save_detailed_process_schedule_assembly(
                            date_key=date_key,
                            sequence=date_sequence,
                            bay_assignments=date_bay_assignments,
                            ct_tables=detailed['ct_tables'],
                            blocks_dict=date_blocks_dict,
                            date_start_time=date_start_time
                        )
                        # print(f"    Assembly 상세 공정 스케줄: detailed_assembly_decoding_schedule_processes_{date_key}.csv")
                    except Exception as process_err:
                        print(f"   ⚠️ RL 상세 공정 CSV 생성 실패 ({date_key}): {process_err}")
                        import traceback
                        traceback.print_exc()
                    
                    # 상세 스케줄링 정보 CSV 생성 (더미 데이터로)
                    assembly_step_info = []
                    for i, block_id in enumerate(date_sequence):
                        block = blocks_dict[block_id]
                        block_line_group, block_workshop_code = get_line_group_and_workshop_code(block)

                        step_info = {
                            'step': i,
                            'selected_block_id': block_id,
                            'selected_line_group': block_line_group,
                            'selected_workshop_code': block_workshop_code,
                            'available_blocks': [block_id],  # 단순화
                            'block_analysis': [{
                                'block_id': block_id,
                                'assembly_type': block.assembly_type.value,
                                'line_group': block_line_group,
                                'assembly_workshop_code': block_workshop_code,
                                'port_starboard': block.port_starboard.value,
                                'is_available': True,
                                'inclusion_reason': 'RL_SELECTED',
                                'constraint_checks': {}
                            }]
                        }
                        assembly_step_info.append(step_info)
                    
                    # ==== [AGENT-EDIT BEGIN: 상세 스케줄링 CSV 인자 순서 수정] ====
                    # 상세 스케줄링 정보 CSV 생성
                    try:
                        # csv_save.py 시그니처: (assembly_step_info, date_key, all_blocks)
                        save_assembly_decoding_schedule_info(assembly_step_info, date_key, blocks)
                        # print(f"    Assembly 상세 분석 저장: detailed_assembly_schedule_info_{date_key}.csv ({len(assembly_step_info)}개 레코드)")
                    except Exception as schedule_err:
                        print(f"   ⚠️ RL 스케줄 정보 CSV 생성 실패 ({date_key}): {schedule_err}")
                        import traceback
                        traceback.print_exc()
                    # ==== [AGENT-EDIT END] ====
                    
                    # 베이 선택 정보 CSV 생성 (csv_save.py 요구 형식에 맞게)
                    try:
                        bay_analyses = []
                        for block_id in date_sequence:
                            block = blocks_dict[block_id]
                            block_line_group, block_workshop_code = get_line_group_and_workshop_code(block)
                            bay_analysis = {
                                'block_id': block_id,
                                'assigned_bay': date_bay_assignments[block_id].value,
                                'width_m': round(block.width, 1),  # 필수 필드 추가
                                'longi_count': block.longi_count,  #  필수 필드 추가
                                'line_group': block_line_group,
                                'assembly_workshop_code': block_workshop_code,
                                'material_type': block.material_type.value,  #  필수 필드 추가
                                'final_reason': 'RL_AUTO_ASSIGNED',  #  필수 필드 추가
                                'constraint_checks': {},  #  필수 필드 추가
                                'bay_analysis': {
                                    'selected_bay': date_bay_assignments[block_id].value,
                                    'reason': 'RL_AUTO_ASSIGNED'
                                }
                            }
                            bay_analyses.append(bay_analysis)
                        
                        # csv_save.py 시그니처: (bay_analyses, date_key)
                        save_assembly_decoding_bay_info(bay_analyses, date_key)
                        # print(f"  Assembly 베이 분석 저장: detailed_assembly_bayselect_info_{date_key}.csv ({len(bay_analyses)}개 블록)")
                    except Exception as bay_err:
                        print(f"   ⚠️ RL 베이 선택 CSV 생성 실패 ({date_key}): {bay_err}")
                        import traceback
                        traceback.print_exc()
                    
                except Exception as date_err:
                    print(f"   ⚠️ RL 상세 CSV 생성 실패 ({date_key}): {date_err}")
                    import traceback
                    traceback.print_exc()
                    
        except Exception as detailed_err:
            print(f"   ⚠️ RL 상세 CSV 생성 전체 실패: {detailed_err}")
    
    schedule_results, statistics, step_data = _finalize_rl_results(
        schedule_results,
        all_violations,
        env,
        blocks,
        rl_scheduler,
        training_mode,
        afternoon_guard_blocks_all
    )
    return schedule_results, statistics, step_data, env 
