# rl_assembly_scheduler.py
import json  # [AGENT-ADD] decision trace JSON cells
import os  # [AGENT-EDIT] PBS_FORCE_DEBUG 기반 디버그 출력 제어
import math  # [AGENT-EDIT] env_state 자동 스케일링에 사용
import torch
import random
import numpy as np
import pandas as pd  # [AGENT-EDIT] 결과 시각 파싱과 completed_steps 복원에 사용
from collections import defaultdict
from typing import List, Dict, Tuple, Optional, Set
from datetime import datetime, timedelta

from scheduling.assembly_start.action_sequence_조립착수일기준휴리스틱 import (
    _run_assembly_decoding_core,
    get_line_group_and_workshop_code,
    create_block_result,
)  # [AGENT-EDIT] scheduling 경로로 직접 참조 (shim의 _ 접두어 노출 문제 회피)
from scheduling.common.interactive_override import (
    apply_manual_bay_override_if_requested,
    filter_available_ids_by_precedence,
    prime_forced_override_cache,
    resolve_forced_prefix_block,
)
from enhanced_environment.common.utils_core import (
    dedup_violations,
    summarize_violations,
    count_expanded_block_units,
    compute_schedule_span_hours,
)
from scheduling.common.independent_constraint_audit import sync_runtime_results_to_canonical_constraints
from runtime_config import get_runtime_config
from enhanced_environment.pbs_env import EnhancedPanelBlockShop
from enhanced_environment.constraints import ConstraintConfig
from enhanced_environment.models import ConstraintViolation, BayType, PortStarboard, ProcessStep
from enhanced_environment.common.utils_core import expand_rows_with_subassembly
from PPO.models.single_step_actor import (
    extract_block_features_single,
    ENV_STATE_DIM,
    REDUCED_ENV_STATE_DIM,
    CONSTRAINT_ENV_STATE_DIM,
    DIFF_ENV_STATE_DIM,
)


class RLAssemblyScheduler:
    """RL Agent를 사용한 조립 스케줄링 (단순화)"""
    
    def __init__(self, rl_agent, device):
        self.rl_agent = rl_agent
        self.device = device
        self.episode_data = []  # 전체 에피소드 데이터 저장
        self.last_decision_snapshot = None  # [AGENT-ADD] evaluation/test decision trace export
        self.use_env_state = getattr(rl_agent, 'use_env_state', False)  # 🆕 env_state 사용 여부
        self.feature_mode = getattr(rl_agent, 'feature_mode', 'full')
        if self.feature_mode == "reduced":
            default_env_dim = REDUCED_ENV_STATE_DIM
        elif self.feature_mode == "constraint":
            default_env_dim = CONSTRAINT_ENV_STATE_DIM
        elif self.feature_mode == "diff":
            default_env_dim = DIFF_ENV_STATE_DIM
        else:
            default_env_dim = ENV_STATE_DIM
        self.env_state_dim = getattr(rl_agent, 'env_state_dim', default_env_dim)
        ########################################################################
        # [FIX] 베이별 누적 블록 수 추적 (35A/36B 정확히 관리)
        ########################################################################
        self.branch_usage_counts = {'35A': 0, '36B': 0}
        # [AGENT-ADD] diff state: 베이별 누적 부하(심수/론지/작업시간)를 추적한다.
        self.branch_loads = {
            '35A': {'seam': 0.0, 'longi': 0.0, 'processing': 0.0},
            '36B': {'seam': 0.0, 'longi': 0.0, 'processing': 0.0},
        }
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
            ("CONSECUTIVE_3BAY", ["CONSECUTIVE_3BAY"])
        ]
        # ==== [AGENT-ADD BEGIN: Constraint-focused scaling constants] ====
        self.daily_block_reference = 20.0  # 일일 처리 가능한 블록 수 기준치
        self.constraint_debt_window = 24   # 최근 제약 위반 윈도우
        # [AGENT-ADD] env_state 자동 스케일링 (에피소드/학습 분포 변화 대응)
        self.env_state_max_total_blocks = 1
        self.env_state_max_abs_slack = 1.0
        self.env_state_max_seam_remaining = 1.0
        self.env_state_max_block_slots = 1.0
        # [AGENT-ADD] diff state: 남은 난이도/미래 용량 자동 스케일링 기준.
        self.env_state_max_seam_value = 1.0
        self.env_state_max_longi_value = 1.0
        self.env_state_max_future_seam_capacity = 1.0
        # [AGENT-EDIT] consecutive_bay_count를 러닝 max 기반 auto-scale로 통일
        self.env_state_max_consecutive_bay = 1.0
        self.last_assigned_bay: Optional[BayType] = None
        self.last_block_flags = {'c_seam': 0.0, 'curved': 0.0, 'high_seam': 0.0}
        self.consecutive_bay_streak = 0
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

    @staticmethod
    def _signed_delta_ratio(left: float, right: float) -> float:
        total = max(1.0, abs(left) + abs(right))
        return max(-1.0, min(1.0, (left - right) / total))

    @staticmethod
    def _processing_total(block) -> float:
        processing_times = getattr(block, 'processing_times', None)
        return float(sum(processing_times)) if processing_times else 0.0

    @staticmethod
    def _capacity_consumption_ratio(amount: float, remaining: float) -> float:
        amount = max(0.0, float(amount or 0.0))
        remaining = float(remaining or 0.0)
        if amount <= 0.0:
            return 0.0
        if remaining <= 0.0:
            return 1.0
        return max(0.0, min(1.0, amount / remaining))

    def _compute_last_pattern_distances(self, selected_blocks: List[int], blocks_dict: Dict[int, object]) -> Tuple[float, float, float]:
        # [AGENT-ADD] diff state: 직전 여부 대신 마지막 등장 이후 거리로 간격 제약 상태를 표현한다.
        total = max(1, len(blocks_dict))
        def distance_for(attr_name: str) -> float:
            if not selected_blocks:
                return 1.0
            for distance, block_id in enumerate(reversed(selected_blocks)):
                block = blocks_dict.get(block_id)
                if block is not None and bool(getattr(block, attr_name, False)):
                    return max(0.0, min(1.0, float(distance) / total))
            return 1.0
        return (
            distance_for('is_cross_seam'),
            distance_for('has_curved_plate'),
            distance_for('is_high_seam_block'),
        )

    def _compute_remaining_difficulty(self, remaining_blocks: List[object]) -> Tuple[float, float, float, float, float]:
        # [AGENT-ADD] diff state: 남은 블록의 심수/론지/대형 폭 분포를 요약한다.
        if not remaining_blocks:
            return 0.0, 0.0, 0.0, 0.0, 0.0
        seam_values = [float(getattr(block, 'seam_count', 0.0) or 0.0) for block in remaining_blocks]
        longi_values = [float(getattr(block, 'longi_count', 0.0) or 0.0) for block in remaining_blocks]
        avg_seam = sum(seam_values) / len(seam_values)
        max_seam = max(seam_values) if seam_values else 0.0
        avg_longi = sum(longi_values) / len(longi_values)
        max_longi = max(longi_values) if longi_values else 0.0
        self.env_state_max_seam_value = max(self.env_state_max_seam_value, avg_seam, max_seam)
        self.env_state_max_longi_value = max(self.env_state_max_longi_value, avg_longi, max_longi)
        wide_count = sum(1 for block in remaining_blocks if float(getattr(block, 'width', 0.0) or 0.0) > 21.0)
        return (
            self._safe_ratio(avg_seam, self.env_state_max_seam_value),
            self._safe_ratio(max_seam, self.env_state_max_seam_value),
            self._safe_ratio(avg_longi, self.env_state_max_longi_value),
            self._safe_ratio(max_longi, self.env_state_max_longi_value),
            self._safe_ratio(wide_count, len(remaining_blocks)),
        )

    def _compute_bay_load_deltas(self) -> Tuple[float, float, float]:
        # [AGENT-ADD] diff state: 베이별 누적 부하 평준화 정보를 count가 아니라 실제 부하로 표현한다.
        load_35 = self.branch_loads.get('35A', {})
        load_36 = self.branch_loads.get('36B', {})
        return (
            self._signed_delta_ratio(float(load_35.get('seam', 0.0)), float(load_36.get('seam', 0.0))),
            self._signed_delta_ratio(float(load_35.get('longi', 0.0)), float(load_36.get('longi', 0.0))),
            self._signed_delta_ratio(float(load_35.get('processing', 0.0)), float(load_36.get('processing', 0.0))),
        )

    def _compute_future_capacity_structure(self, env, current_time: datetime, horizon_days: int = 3) -> Tuple[float, float, float]:
        # [AGENT-ADD] diff state: 오늘 이후 며칠의 가동 가능성과 용량 구조를 요약한다.
        workday_count = 0
        seam_caps: List[float] = []
        block_caps: List[float] = []
        for offset in range(1, horizon_days + 1):
            target_dt = datetime.combine((current_time + timedelta(days=offset)).date(), datetime.min.time().replace(hour=8))
            is_closed = bool(getattr(env.calendar_manager, 'is_closed_day', lambda _: False)(target_dt))
            if is_closed:
                seam_caps.append(0.0)
                block_caps.append(0.0)
                continue
            workday_count += 1
            is_weekend = env.calendar_manager.is_weekend(target_dt)
            is_hot = env.calendar_manager.is_hot_season(target_dt)
            is_holiday_eve = env.calendar_manager.is_holiday_eve(target_dt)
            seam_cap = float(env.capacity_tracker.get_capacity_limits(is_weekend, is_hot, is_holiday_eve))
            date_key = target_dt.strftime('%Y%m%d')
            override_limit = float((getattr(env.constraint_config, 'daily_block_cap_overrides', {}) or {}).get(date_key, 0) or 0)
            block_cap = override_limit if override_limit > 0 else self.daily_block_reference
            seam_caps.append(seam_cap)
            block_caps.append(block_cap)
        avg_seam_cap = sum(seam_caps) / max(1, len(seam_caps))
        self.env_state_max_future_seam_capacity = max(self.env_state_max_future_seam_capacity, avg_seam_cap, *(seam_caps or [0.0]))
        min_block_cap = min(block_caps) if block_caps else 0.0
        return (
            self._safe_ratio(workday_count, horizon_days),
            self._safe_ratio(avg_seam_cap, self.env_state_max_future_seam_capacity),
            self._safe_ratio(min_block_cap, self.daily_block_reference),
        )

    def _compute_workshop_order_features(self, blocks_dict: Dict[int, object], selected_block_ids: List[int]) -> Dict[int, Tuple[float, float]]:
        # [AGENT-ADD] diff block feature: 작업장 내 상대 순위와 head와의 조립착수일 차이를 제공한다.
        selected_set = set(selected_block_ids)
        buckets: Dict[str, List[Tuple[int, datetime]]] = defaultdict(list)
        for block_id, block in blocks_dict.items():
            if block_id in selected_set:
                continue
            start_dt = getattr(block, 'assembly_start_date', None)
            if not isinstance(start_dt, datetime):
                continue
            workshop_key = self._normalize_workshop_key(getattr(block, 'assembly_workshop_code', None))
            buckets[workshop_key].append((block_id, start_dt))

        features: Dict[int, Tuple[float, float]] = {}
        for items in buckets.values():
            items.sort(key=lambda item: (item[1], item[0]))
            denom = max(1, len(items) - 1)
            head_dt = items[0][1]
            for rank, (block_id, start_dt) in enumerate(items):
                rank_norm = float(rank / denom) if denom > 0 else 0.0
                gap_days = max(0.0, (start_dt - head_dt).total_seconds() / 86400.0)
                features[block_id] = (rank_norm, gap_days)
        return features

    def _compute_slack_rank_map(self, slack_values_by_block: Dict[int, float]) -> Dict[int, float]:
        # [AGENT-ADD] diff block feature: 현재 후보 중 deadline/slack이 얼마나 급한지 상대 순위로 제공한다.
        if not slack_values_by_block:
            return {}
        ranked = sorted(slack_values_by_block.items(), key=lambda item: (float(item[1]), item[0]))
        denom = max(1, len(ranked) - 1)
        return {block_id: float(rank / denom) for rank, (block_id, _) in enumerate(ranked)}

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

    def _compute_effective_block_limit(self, env, current_time: datetime, capacity_snapshot: Dict[str, float]) -> float:
        is_weekend = current_time.weekday() >= 5
        block_count = float(env.capacity_tracker.get_block_count(is_weekend))
        date_key = current_time.strftime('%Y%m%d')
        override_limit = float((getattr(env.constraint_config, 'daily_block_cap_overrides', {}) or {}).get(date_key, 0) or 0)
        if override_limit > 0:
            return override_limit
        if not is_weekend and capacity_snapshot['used_seam'] > 72.0 and block_count < 17.0:
            return 17.0
        return float(self.daily_block_reference)

    def _compute_diff_bay_feasibility(self, block, blocks_dict: Dict[int, object]) -> Tuple[float, float]:
        width = float(getattr(block, 'width', 0.0) or 0.0)
        try:
            is_small_ps_pair = bool(getattr(block, 'is_ps_small_pair')(blocks_dict))
        except Exception as exc:
            raise RuntimeError(
                f"diff bay feasibility 계산 실패: block_id={getattr(block, 'block_id', 'unknown')}"
            ) from exc
        if width > 21.0 or is_small_ps_pair:
            return 0.0, 1.0
        return 1.0, 1.0

    def _compute_workshop_head_flags(self, available_block_ids: List[int], blocks_dict: Dict[int, object]) -> Dict[int, float]:
        groups: Dict[str, List[Tuple[int, datetime]]] = defaultdict(list)
        for block_id in available_block_ids:
            block = blocks_dict.get(block_id)
            if block is None:
                continue
            workshop_code = self._normalize_workshop_key(getattr(block, 'assembly_workshop_code', None))
            start_dt = getattr(block, 'assembly_start_date', None)
            if not isinstance(start_dt, datetime):
                continue
            groups[workshop_code].append((block_id, start_dt))

        flags = {block_id: 0.0 for block_id in available_block_ids}
        for items in groups.values():
            if not items:
                continue
            min_start = min(start_dt for _, start_dt in items)
            for block_id, start_dt in items:
                if start_dt == min_start:
                    flags[block_id] = 1.0
        return flags

    def _compute_desc_rank_map(self, values_by_block: Dict[int, float]) -> Dict[int, float]:
        if not values_by_block:
            return {}
        ranked = sorted(values_by_block.items(), key=lambda item: (-float(item[1]), item[0]))
        denom = max(1, len(ranked) - 1)
        return {block_id: float(rank / denom) for rank, (block_id, _) in enumerate(ranked)}

    def _reset_state_tracking(self):
        self.branch_usage_counts = {'35A': 0, '36B': 0}
        # [AGENT-EDIT] diff state 누적 부하도 에피소드마다 초기화한다.
        self.branch_loads = {
            '35A': {'seam': 0.0, 'longi': 0.0, 'processing': 0.0},
            '36B': {'seam': 0.0, 'longi': 0.0, 'processing': 0.0},
        }
        self.last_assigned_bay = None
        self.last_block_flags = {'c_seam': 0.0, 'curved': 0.0, 'high_seam': 0.0}
        self.consecutive_bay_streak = 0

    ########################################################################
    # [FIX] 베이 별 누적 카운트 업데이트 유틸
    ########################################################################
    def _update_branch_usage(self, block, assigned_bay: Optional[BayType]):
        bay_key = None
        resolved_bay = assigned_bay
        if assigned_bay == BayType.BAY_35A:
            bay_key = '35A'
        elif assigned_bay == BayType.BAY_36B:
            bay_key = '36B'
        else:
            line_key = self._normalize_line_key(getattr(block, 'line_group', None))
            if line_key == '35A':
                bay_key = '35A'
                resolved_bay = BayType.BAY_35A
            elif line_key == '36B':
                bay_key = '36B'
                resolved_bay = BayType.BAY_36B
        if bay_key is None:
            if self.branch_usage_counts.get('35A', 0) <= self.branch_usage_counts.get('36B', 0):
                bay_key = '35A'
                resolved_bay = BayType.BAY_35A
            else:
                bay_key = '36B'
                resolved_bay = BayType.BAY_36B
        self.branch_usage_counts[bay_key] = self.branch_usage_counts.get(bay_key, 0) + 1
        # [AGENT-ADD] diff state: 베이별 실제 부하를 누적한다.
        load_entry = self.branch_loads.setdefault(bay_key, {'seam': 0.0, 'longi': 0.0, 'processing': 0.0})
        load_entry['seam'] = float(load_entry.get('seam', 0.0)) + float(getattr(block, 'seam_count', 0.0) or 0.0)
        load_entry['longi'] = float(load_entry.get('longi', 0.0)) + float(getattr(block, 'longi_count', 0.0) or 0.0)
        load_entry['processing'] = float(load_entry.get('processing', 0.0)) + self._processing_total(block)

        if self.last_assigned_bay == resolved_bay:
            self.consecutive_bay_streak += 1
        else:
            self.consecutive_bay_streak = 1 if resolved_bay is not None else 0
        self.last_assigned_bay = resolved_bay
        self.last_block_flags = {
            'c_seam': 1.0 if getattr(block, 'is_cross_seam', False) else 0.0,
            'curved': 1.0 if getattr(block, 'has_curved_plate', False) else 0.0,
            'high_seam': 1.0 if getattr(block, 'is_high_seam_block', False) else 0.0,
        }

    # 블록 특성만 사용

    #  환경 스냅샷 생성 제거 (추정치/가정 사용 금지 정책에 따라 불필요)

    def _extract_diff_environment_state(
        self,
        env,
        selected_blocks: List[int],
        blocks_dict: Dict,
        current_time: datetime,
        capacity_snapshot: Dict[str, float],
        is_weekend: bool,
        is_holiday_eve: bool,
        is_hot_season: bool,
    ) -> List[float]:
        # [AGENT-ADD] diff env state는 마스킹 결과/최근 위반 로그를 제외하고 실제 상태와 도메인 부하만 담는다.
        state: List[float] = [
            1.0 if is_weekend else 0.0,
            1.0 if is_holiday_eve else 0.0,
            1.0 if is_hot_season else 0.0,
            capacity_snapshot['daily_seam_usage_ratio'],
            capacity_snapshot['daily_block_usage_ratio'],
            capacity_snapshot['remaining_capacity_ratio'],
        ]

        remaining_blocks = [block for block_id, block in blocks_dict.items() if block_id not in selected_blocks]
        total_blocks = max(1, len(blocks_dict))
        remaining_count = len(remaining_blocks)
        remaining_ratio = self._safe_ratio(remaining_count, total_blocks)
        max_total_log = math.log1p(max(1, total_blocks))
        remaining_blocks_norm = (math.log1p(remaining_count) / max_total_log) if max_total_log > 0 else 0.0
        state.extend([remaining_blocks_norm, remaining_ratio])

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
            except Exception as exc:
                raise RuntimeError(
                    f"RL diff env_state P6 피처 계산 실패: block_id={getattr(block, 'block_id', 'unknown')}"
                ) from exc

            if getattr(block, 'has_curved_plate', False):
                curved_count += 1
            if getattr(block, 'is_high_seam_block', False):
                high_seam_count += 1

            slack = self._compute_slack_days(block, current_time)
            slack_values.append(slack)
            if slack < 0:
                overdue_count += 1

        state.extend([
            self._safe_ratio(ps_blocks, remaining_count),
            self._safe_ratio(line_count, remaining_count),
            self._safe_ratio(fixed_count, remaining_count),
            self._safe_ratio(subassembly_count, remaining_count),
            self._safe_ratio(needs_pm_count, remaining_count),
            self._safe_ratio(curved_count, remaining_count),
            self._safe_ratio(high_seam_count, remaining_count),
            self._compute_bay_balance_delta(),
        ])

        overdue_ratio = self._safe_ratio(overdue_count, remaining_count)
        if slack_values:
            avg_slack = sum(slack_values) / len(slack_values)
            min_slack = min(slack_values)
            max_slack = max(slack_values)
            max_abs_slack = max(abs(min_slack), abs(max_slack))
        else:
            avg_slack = min_slack = max_slack = max_abs_slack = 0.0
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
            _scale_slack(max_slack),
        ])

        seam_remaining_abs = max(0.0, capacity_snapshot['capacity_limit'] - capacity_snapshot['used_seam'])
        self.env_state_max_seam_remaining = max(
            self.env_state_max_seam_remaining,
            seam_remaining_abs,
            capacity_snapshot['capacity_limit'],
        )
        seam_remaining_norm = self._safe_ratio(seam_remaining_abs, self.env_state_max_seam_remaining)

        block_count = float(env.capacity_tracker.get_block_count(is_weekend))
        effective_block_limit = self._compute_effective_block_limit(env, current_time, capacity_snapshot)
        block_slots_remaining_abs = max(0.0, effective_block_limit - block_count)
        self.env_state_max_block_slots = max(
            self.env_state_max_block_slots,
            block_slots_remaining_abs,
            effective_block_limit,
        )
        block_slots_remaining_norm = self._safe_ratio(block_slots_remaining_abs, self.env_state_max_block_slots)

        prev_bay_is_35a = 1.0 if self.last_assigned_bay == BayType.BAY_35A else 0.0
        prev_bay_is_36b = 1.0 if self.last_assigned_bay == BayType.BAY_36B else 0.0
        self.env_state_max_consecutive_bay = max(
            self.env_state_max_consecutive_bay,
            float(self.consecutive_bay_streak),
        )
        consecutive_bay_count = min(float(self.consecutive_bay_streak) / max(1.0, self.env_state_max_consecutive_bay), 1.0)

        state.extend([
            seam_remaining_norm,
            block_slots_remaining_norm,
            prev_bay_is_35a,
            prev_bay_is_36b,
            consecutive_bay_count,
        ])

        state.extend(self._compute_last_pattern_distances(selected_blocks, blocks_dict))
        state.extend(self._compute_bay_load_deltas())
        state.extend(self._compute_remaining_difficulty(remaining_blocks))
        state.extend(self._compute_future_capacity_structure(env, current_time))

        if len(state) != self.env_state_dim:
            state = state[:self.env_state_dim]
            if len(state) < self.env_state_dim:
                state.extend([0.0] * (self.env_state_dim - len(state)))
        return state

    def _extract_environment_state(self, env, selected_blocks: List[int], blocks_dict: Dict, block_analysis: Optional[List[Dict]] = None) -> List[float]:
        """환경 상태 벡터 추출 (ENV_STATE_DIM 차원)"""
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

        if self.feature_mode == "constraint":
            remaining_blocks = [block for block_id, block in blocks_dict.items() if block_id not in selected_blocks]
            total_blocks = max(1, len(blocks_dict))
            remaining_ratio = len(remaining_blocks) / total_blocks
            bay_balance_delta = self._compute_bay_balance_delta()

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

        if self.feature_mode == "diff":
            return self._extract_diff_environment_state(
                env,
                selected_blocks,
                blocks_dict,
                current_time,
                capacity_snapshot,
                is_weekend,
                is_holiday_eve,
                is_hot_season,
            )

        state.extend([
            capacity_snapshot['daily_seam_usage_ratio'],
            capacity_snapshot['daily_block_usage_ratio'],
            capacity_snapshot['remaining_capacity_ratio']
        ])

        remaining_blocks = [block for block_id, block in blocks_dict.items() if block_id not in selected_blocks]
        total_blocks = max(1, len(blocks_dict))
        remaining_count = len(remaining_blocks)
        remaining_ratio = remaining_count / total_blocks

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
            except Exception as exc:
                raise RuntimeError(
                    f"RL env_state P6 피처 계산 실패: block_id={getattr(block, 'block_id', 'unknown')}"
                ) from exc

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

        if self.feature_mode == "diff":
            state.extend([available_ratio, diversity])
        else:
            available_ps_forced_ratio = 0.0
            available_masking_stage_mean = 0.0
            if block_analysis:
                available_entries = [a for a in block_analysis if a.get('is_available')]
                if available_entries:
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

        if self.feature_mode == "diff":
            seam_remaining_abs = max(0.0, capacity_snapshot['capacity_limit'] - capacity_snapshot['used_seam'])
            self.env_state_max_seam_remaining = max(
                self.env_state_max_seam_remaining,
                seam_remaining_abs,
                capacity_snapshot['capacity_limit'],
            )
            seam_remaining_norm = self._safe_ratio(seam_remaining_abs, self.env_state_max_seam_remaining)

            block_count = float(env.capacity_tracker.get_block_count(is_weekend))
            effective_block_limit = self._compute_effective_block_limit(env, current_time, capacity_snapshot)
            block_slots_remaining_abs = max(0.0, effective_block_limit - block_count)
            self.env_state_max_block_slots = max(
                self.env_state_max_block_slots,
                block_slots_remaining_abs,
                effective_block_limit,
            )
            block_slots_remaining_norm = self._safe_ratio(block_slots_remaining_abs, self.env_state_max_block_slots)

            prev_bay_is_35a = 1.0 if self.last_assigned_bay == BayType.BAY_35A else 0.0
            prev_bay_is_36b = 1.0 if self.last_assigned_bay == BayType.BAY_36B else 0.0
            # [AGENT-EDIT] 고정 분모(4)로 인한 saturation을 러닝 max auto-scale로 교체
            self.env_state_max_consecutive_bay = max(
                self.env_state_max_consecutive_bay,
                float(self.consecutive_bay_streak),
            )
            consecutive_bay_scale = max(1.0, self.env_state_max_consecutive_bay)
            consecutive_bay_count = min(float(self.consecutive_bay_streak) / consecutive_bay_scale, 1.0)

            state.extend([
                seam_remaining_norm,
                block_slots_remaining_norm,
                prev_bay_is_35a,
                prev_bay_is_36b,
                consecutive_bay_count,
                float(self.last_block_flags.get('c_seam', 0.0) or 0.0),
                float(self.last_block_flags.get('curved', 0.0) or 0.0),
                float(self.last_block_flags.get('high_seam', 0.0) or 0.0),
            ])

        if len(state) != self.env_state_dim:
            state = state[:self.env_state_dim]
            if len(state) < self.env_state_dim:
                state.extend([0.0] * (self.env_state_dim - len(state)))

        if self._env_state_debug_prints < 5:
            preview = np.round(state[:10], 3).tolist()
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
                           forced_block_id: Optional[int] = None,
                           collect_episode_data: Optional[bool] = None,
                           enable_grad: Optional[bool] = None,
                           collect_step_metrics: Optional[bool] = None) -> Tuple[int, float, torch.Tensor]:
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
        candidate_rows = []
        processing_time_values: Dict[int, float] = {}
        seam_values: Dict[int, float] = {}
        slack_values: Dict[int, float] = {}
        selected_set = set(selected_blocks)
        available_set = set(available_blocks)
        remaining_set = set(blocks_dict.keys()) - selected_set
        workshop_head_flags = self._compute_workshop_head_flags(available_blocks, blocks_dict) if self.feature_mode == 'diff' else {}
        workshop_order_features = self._compute_workshop_order_features(blocks_dict, selected_blocks) if self.feature_mode == 'diff' else {}

        is_current_weekend = env.calendar_manager.is_weekend(current_time) if hasattr(env, 'calendar_manager') else current_time.weekday() >= 5
        seam_remaining_abs = max(0.0, capacity_snapshot['capacity_limit'] - capacity_snapshot['used_seam'])
        current_block_count = float(env.capacity_tracker.get_block_count(is_current_weekend))
        effective_block_limit = self._compute_effective_block_limit(env, current_time, capacity_snapshot)
        block_slots_remaining_abs = max(0.0, effective_block_limit - current_block_count)

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
            mask_stage_score = self._encode_masking_stage((analysis.get('masking_stage') if analysis else None))
            three_bay_state = 0.0
            if analysis:
                bay_state = (analysis.get('three_bay_check') or '').upper()
                if 'FAIL' in bay_state:
                    three_bay_state = 1.0
                elif 'RELAX' in bay_state:
                    three_bay_state = 0.5
            ps_forced_flag = 0.0
            line_is_fixed, line_is_line = self._classify_line_flags(block)
            deadline_urgency = self._compute_deadline_urgency(slack_days)
            processing_times = getattr(block, 'processing_times', None)
            processing_time_values[block_id] = float(sum(processing_times)) if processing_times else 0.0
            seam_values[block_id] = float(getattr(block, 'seam_count', 0.0) or 0.0)
            slack_values[block_id] = slack_days

            candidate_row = {
                'block_id': block_id,
                'block': block,
                'slack_days': slack_days,
                'count_35a': count_35a,
                'count_36b': count_36b,
                'workshop_backlog_ratio': workshop_backlog_ratio,
                'line_backlog_ratio': line_backlog_ratio,
                'deadline_urgency': deadline_urgency,
                'line_is_fixed': line_is_fixed,
                'line_is_line': line_is_line,
                'masking_stage_value': mask_stage_score,
                'three_bay_state': three_bay_state,
                'ps_forced_flag': ps_forced_flag,
            }

            if self.feature_mode == 'diff':
                bay_35a_feasible, bay_36b_feasible = self._compute_diff_bay_feasibility(block, blocks_dict)
                pair_id = getattr(block, 'pair_block_id', None)
                workshop_rank, workshop_head_gap_days = workshop_order_features.get(block_id, (0.0, 0.0))
                candidate_row.update({
                    'bay_35a_feasible': bay_35a_feasible,
                    'bay_36b_feasible': bay_36b_feasible,
                    'is_workshop_head': workshop_head_flags.get(block_id, 0.0),
                    'pair_already_selected': 1.0 if pair_id in selected_set else 0.0,
                    'pair_remaining': 1.0 if pair_id in remaining_set else 0.0,
                    'pair_available': 1.0 if pair_id in available_set else 0.0,
                    'workshop_rank': workshop_rank,
                    'workshop_head_gap_days': workshop_head_gap_days,
                    'candidate_seam_capacity_ratio': self._capacity_consumption_ratio(
                        float(getattr(block, 'seam_count', 0.0) or 0.0),
                        seam_remaining_abs,
                    ),
                    'candidate_block_slot_ratio': self._capacity_consumption_ratio(1.0, block_slots_remaining_abs),
                })
            else:
                candidate_branch_35a = 0.0
                candidate_branch_36b = 0.0
                try:
                    candidate_bay = env._preview_assign_bay(block)
                except Exception as exc:
                    raise RuntimeError(
                        f"RL candidate preview bay 계산 실패: block_id={block.block_id}"
                    ) from exc
                if candidate_bay == BayType.BAY_35A:
                    candidate_branch_35a = 1.0
                elif candidate_bay == BayType.BAY_36B:
                    candidate_branch_36b = 1.0
                else:
                    raise RuntimeError(
                        f"RL candidate preview bay 미확정: block_id={block.block_id}, bay={candidate_bay}"
                    )

                if candidate_branch_35a == 1.0 and candidate_branch_36b == 1.0:
                    if current_branch_counts.get('35A', 0) > current_branch_counts.get('36B', 0):
                        candidate_branch_35a = 0.0
                    else:
                        candidate_branch_36b = 0.0
                candidate_row.update({
                    'candidate_branch_35a': candidate_branch_35a,
                    'candidate_branch_36b': candidate_branch_36b,
                })
            candidate_rows.append(candidate_row)

        processing_time_rank_map = self._compute_desc_rank_map(processing_time_values) if self.feature_mode == 'diff' else {}
        seam_rank_map = self._compute_desc_rank_map(seam_values) if self.feature_mode == 'diff' else {}
        slack_rank_map = self._compute_slack_rank_map(slack_values) if self.feature_mode == 'diff' else {}

        for candidate_row in candidate_rows:
            block_id = candidate_row['block_id']
            block = candidate_row['block']
            feature_kwargs = dict(
                branch_count_35a=candidate_row['count_35a'],
                branch_count_36b=candidate_row['count_36b'],
                workshop_backlog_ratio=candidate_row['workshop_backlog_ratio'],
                line_backlog_ratio=candidate_row['line_backlog_ratio'],
                ps_pair_remaining_ratio=ps_pair_ratio,
                daily_seam_usage_ratio=capacity_snapshot['daily_seam_usage_ratio'],
                daily_block_usage_ratio=capacity_snapshot['daily_block_usage_ratio'],
                remaining_capacity_ratio=capacity_snapshot['remaining_capacity_ratio'],
                bay_balance_delta=bay_balance_delta,
                deadline_urgency=candidate_row['deadline_urgency'],
                line_is_fixed=candidate_row['line_is_fixed'],
                line_is_line=candidate_row['line_is_line'],
            )
            if self.feature_mode == 'diff':
                feature_kwargs.update({
                    'bay_35a_feasible': candidate_row['bay_35a_feasible'],
                    'bay_36b_feasible': candidate_row['bay_36b_feasible'],
                    'is_workshop_head': candidate_row['is_workshop_head'],
                    'pair_already_selected': candidate_row['pair_already_selected'],
                    'pair_remaining': candidate_row['pair_remaining'],
                    'pair_available': candidate_row['pair_available'],
                    'processing_time_rank': processing_time_rank_map.get(block_id, 0.0),
                    'seam_rank': seam_rank_map.get(block_id, 0.0),
                    'workshop_rank': candidate_row['workshop_rank'],
                    'workshop_head_gap_days': candidate_row['workshop_head_gap_days'],
                    'candidate_seam_capacity_ratio': candidate_row['candidate_seam_capacity_ratio'],
                    'candidate_block_slot_ratio': candidate_row['candidate_block_slot_ratio'],
                    'slack_rank': slack_rank_map.get(block_id, 0.0),
                })
            else:
                feature_kwargs.update({
                    'candidate_branch_35a': candidate_row['candidate_branch_35a'],
                    'candidate_branch_36b': candidate_row['candidate_branch_36b'],
                    'masking_stage_value': candidate_row['masking_stage_value'],
                    'three_bay_state': candidate_row['three_bay_state'],
                    'ps_forced_flag': candidate_row['ps_forced_flag'],
                })

            block_features = extract_block_features_single(
                block,
                slack_days=candidate_row['slack_days'],
                feature_mode=self.feature_mode,
                **feature_kwargs,
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
        # [AGENT-ADD] Rollout 후보 생성은 teacher/PPO 업데이트에서 log prob를 다시 계산하므로
        # episode_data 수집과 autograd 사용 여부를 분리한다.
        collect_episode_data = training_mode if collect_episode_data is None else bool(collect_episode_data)
        enable_grad = training_mode if enable_grad is None else bool(enable_grad)
        collect_step_metrics = collect_episode_data if collect_step_metrics is None else bool(collect_step_metrics)
        
        with torch.set_grad_enabled(enable_grad):
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
            except Exception as exc:
                # [AGENT-EDIT] forced action confidence 실패를 기본값으로 덮지 않는다.
                raise RuntimeError(
                    f"RL forced action confidence 계산 실패: block_id={selected_original_index}"
                ) from exc
        
        selected_analysis = analysis_map.get(selected_original_index) if analysis_map else None

        self.last_decision_snapshot = {
            'selected_block_id': selected_original_index,
            'available_indices': available_indices.copy(),
            'available_count': len(available_indices),
            'selected_action_idx': available_indices.index(selected_original_index),
            'confidence': float(confidence),
            'action_logits': action_logits.detach().cpu().tolist() if isinstance(action_logits, torch.Tensor) else list(action_logits) if action_logits is not None else None,
            'selected_analysis_reason': selected_analysis.get('exclusion_reason') if selected_analysis else None,
            'selected_masking_stage': selected_analysis.get('masking_stage') if selected_analysis else None,
            'selected_three_bay_check': selected_analysis.get('three_bay_check') if selected_analysis else None,
            'forced_action': bool(forced_used),
        }

        if collect_episode_data:
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
            current_bay_assignments = {}
            current_makespan_sec = 0.0
            actual_current_makespan = 0.0
            actual_previous_makespan = 0.0
            if collect_step_metrics:
                try:
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
                        
                except Exception as makespan_err:
                    # [AGENT-EDIT] rollout 기록용 makespan 실패를 0으로 덮지 않는다.
                    raise RuntimeError(
                        f"RL rollout makespan 기록 실패: step={len(self.episode_data)}, "
                        f"selected={selected_original_index}"
                    ) from makespan_err

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
        # [AGENT-EDIT] 추정 기반 makespan fallback 제거.
        raise RuntimeError("RL 추정 makespan helper는 제거되었고 더 이상 사용하면 안 된다.")


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
    afternoon_guard_blocks_all: Set[int],
    decision_trace: Optional[List[Dict]] = None,
) -> Tuple[List[Dict], Dict, List[Dict]]:
    # 8. 통계 계산
    total_blocks_processed = len(schedule_results)
    total_blocks_expected = count_expanded_block_units(blocks)
    total_time_hours = sum(r.get('total_time_min', 0) for r in schedule_results) / 60.0
    # [AGENT-EDIT] raw / primary / meta를 공통 기준으로 재집계한다.
    violation_summary = summarize_violations(
        all_violations,
        keep_info=True,
        include_guard=False,
    )
    total_violations = int(violation_summary.get('violations_primary_count', 0))
    total_violations_train = total_violations

    # [AGENT-EDIT] RL도 실제 결과 row의 wall-clock span을 canonical makespan으로 사용한다.
    actual_makespan_hours = compute_schedule_span_hours(schedule_results)

    # RL 성능 통계
    rl_confidence_scores = [r.get('rl_confidence', 0) for r in schedule_results]
    avg_confidence = sum(rl_confidence_scores) / len(rl_confidence_scores) if rl_confidence_scores else 0

    # ==== [AGENT-EDIT BEGIN: canonical runtime replay sync] ====
    runtime_cfg = get_runtime_config() or {}
    schedule_results, audit_stats, _ = sync_runtime_results_to_canonical_constraints(
        schedule_results=schedule_results,
        blocks=blocks,
        metadata=getattr(env, "metadata", {}) or {},
        runtime_cfg=runtime_cfg,
        case_label="runtime_rl",
    )

    # 별판 통합 행 풀기 (휴리스틱과 동일 출력 형식 유지)
    schedule_results = expand_rows_with_subassembly(schedule_results)
    total_blocks_processed = len(schedule_results)

    total_cseam_violations = sum(
        int(row.get("violations_primary_count", row.get("violations", 0)) or 0)
        for row in schedule_results
        if "ROUTING_C_SEAM_SPACING" in list(row.get("constraint_ids") or [])
    )

    statistics = {
        'total_blocks_processed': total_blocks_processed,
        'total_blocks_expected': total_blocks_expected,
        'success_rate': (total_blocks_processed / total_blocks_expected) * 100 if total_blocks_expected else 0.0,
        'total_time_hours': total_time_hours,
        'makespan_hours': actual_makespan_hours,
        'total_violations': int(audit_stats.get('total_violations', total_violations)),
        'total_violations_primary': int(audit_stats.get('total_violations_primary', total_violations)),
        'total_violations_raw': int(audit_stats.get('total_violations_raw', violation_summary.get('violations_raw_count', 0))),
        'total_violations_meta': int(audit_stats.get('total_violations_meta', violation_summary.get('violations_meta_count', 0))),
        'total_violations_info': int(audit_stats.get('total_violations_info', violation_summary.get('violations_info_count', 0))),
        'total_violations_train': int(audit_stats.get('total_violations_primary', total_violations_train)),
        'total_violations_primary_train': int(audit_stats.get('total_violations_primary', total_violations_train)),
        'total_cseam_violations': total_cseam_violations,
        'ps_success_rate': 0,
        'ps_pairs_total': 0,
        'ps_pairs_successful': 0,
        'daily_analyses_generated': 0,
        'step_analyses_generated': 0,
        'rl_confidence_avg': avg_confidence,
        'rl_mode': 'training' if training_mode else 'evaluation',
        'audit_workshop_order_inversions': int(audit_stats.get('audit_workshop_order_inversions', 0)),
        'audit_constraint_ids': list(audit_stats.get('audit_constraint_ids', [])),
        'audit_constraint_families': list(audit_stats.get('audit_constraint_families', [])),
    }
    # ==== [AGENT-EDIT END] ====

    if not training_mode and decision_trace is not None:
        step_data = decision_trace
    else:
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
    forced_sequence: Optional[List[int]] = None,  # [AGENT-ADD] block_id 강제 시퀀스
    allow_forced_prefix_override: bool = False,  # [AGENT-ADD] masking 밖 강제 선택 허용
    precedence_rules: Optional[List[Tuple[int, int]]] = None,  # [AGENT-ADD] interactive precedence
    manual_bay_assignments: Optional[Dict[int, str]] = None,  # [AGENT-ADD] interactive bay override
    collect_episode_data: Optional[bool] = None,  # [AGENT-ADD] collect rollout records without requiring autograd
    enable_grad: Optional[bool] = None,  # [AGENT-ADD] rollout forward autograd toggle
    collect_step_metrics: Optional[bool] = None,  # [AGENT-ADD] expensive per-step partial makespan metrics
) -> Tuple[List[Dict], Dict, List[Dict], 'EnhancedPanelBlockShop']:
    """
    RL Agent를 사용한 Assembly Decoding 실행
    
    Args:
        training_mode: True이면 학습 데이터 수집, False이면 평가만
        
    Returns:
        (results, statistics, episode_data, env)
    """
    # [AGENT-ADD] Rollout 기록 수집과 autograd 사용 여부를 함수 진입부에서 확정한다.
    collect_episode_data = training_mode if collect_episode_data is None else bool(collect_episode_data)
    enable_grad = training_mode if enable_grad is None else bool(enable_grad)
    collect_step_metrics = collect_episode_data if collect_step_metrics is None else bool(collect_step_metrics)
    
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
    rl_scheduler._reset_state_tracking()

    ########################################################################
    # [AGENT-ADD] Assembly Decoding: env.step 기반 정석 루트
    ########################################################################
    if use_env_step:
        schedule_results: List[Dict] = []
        all_violations: List[ConstraintViolation] = []
        decision_trace: List[Dict] = []  # [AGENT-ADD] evaluation/test decision trace
        blocks_dict = {block.block_id: block for block in blocks}
        assembly_sequence = 1
        # [AGENT-ADD] 강제 시퀀스 포인터 (block_id 기준)
        forced_idx = 0
        forced_sequence = forced_sequence or []

        # 메인 루프 (환경이 상태를 관리)
        while not env.is_done and len(env.assembly_selected_blocks) < len(blocks):
            available_ids, violations, block_analysis = env.get_available_actions_assembly()
            if env.is_done:
                break

            current_datetime = env.assembly_current_datetime
            selected_blocks = env.assembly_selected_blocks.copy()

            env_state_vector = None
            if rl_scheduler.use_env_state:
                env_state_vector = rl_scheduler._extract_environment_state(
                    env, selected_blocks, blocks_dict, block_analysis
                )

            analysis_map = {analysis.get('block_id'): analysis for analysis in block_analysis}

            forced_block_id, forced_idx = resolve_forced_prefix_block(
                forced_sequence,
                forced_idx,
                env.assembly_selected_blocks,
            )
            filtered_available_ids, precedence_blocked_ids, precedence_override_block_id = filter_available_ids_by_precedence(
                available_ids=available_ids,
                selected_block_ids=env.assembly_selected_blocks,
                precedence_rules=precedence_rules,
            )
            forced_override_active = False

            if not filtered_available_ids and forced_block_id is None and precedence_override_block_id is None:
                break

            if forced_block_id is not None and forced_block_id not in available_ids and allow_forced_prefix_override:
                selected_block_id = forced_block_id
                confidence = 1.0
                action_logits = None
                forced_override_active = True
                prime_forced_override_cache(
                    env=env,
                    forced_block_id=forced_block_id,
                    masking_violations=violations,
                    block_analysis=block_analysis,
                    blocks_dict=blocks_dict,
                    note="interactive prefix override",
                )
                rl_scheduler.last_decision_snapshot = {
                    'selected_block_id': selected_block_id,
                    'available_indices': list(filtered_available_ids),
                    'available_count': len(filtered_available_ids),
                    'selected_action_idx': 0,
                    'confidence': 1.0,
                    'action_logits': None,
                    'selected_analysis_reason': 'interactive prefix override',
                    'selected_masking_stage': 'forced_prefix_override',
                    'selected_three_bay_check': None,
                    'forced_action': True,
                }
                action_idx = 0
            elif precedence_override_block_id is not None and allow_forced_prefix_override:
                selected_block_id = precedence_override_block_id
                confidence = 1.0
                action_logits = None
                forced_override_active = True
                prime_forced_override_cache(
                    env=env,
                    forced_block_id=precedence_override_block_id,
                    masking_violations=violations,
                    block_analysis=block_analysis,
                    blocks_dict=blocks_dict,
                    note="interactive precedence override",
                )
                rl_scheduler.last_decision_snapshot = {
                    'selected_block_id': selected_block_id,
                    'available_indices': list(filtered_available_ids),
                    'available_count': len(filtered_available_ids),
                    'selected_action_idx': 0,
                    'confidence': 1.0,
                    'action_logits': None,
                    'selected_analysis_reason': 'interactive precedence override',
                    'selected_masking_stage': 'precedence_override',
                    'selected_three_bay_check': None,
                    'forced_action': True,
                }
                action_idx = 0
            else:
                selected_block_id, confidence, action_logits = rl_scheduler._rl_block_selection(
                    filtered_available_ids,
                    blocks_dict,
                    selected_blocks,
                    current_datetime,
                    env,
                    training_mode,
                    env_state_vector=env_state_vector,
                    analysis_map=analysis_map,
                    forced_block_id=forced_block_id,
                    collect_episode_data=collect_episode_data,
                    enable_grad=enable_grad,
                    collect_step_metrics=collect_step_metrics,
                )

                if selected_block_id not in available_ids:
                    # 안전장치: 예측이 유효하지 않으면 첫 후보로 대체
                    selected_block_id = filtered_available_ids[0]

                action_idx = available_ids.index(selected_block_id)

            manual_bay_override = apply_manual_bay_override_if_requested(
                env=env,
                selected_block_id=selected_block_id,
                manual_bay_assignments=manual_bay_assignments,
                final_reason="INTERACTIVE_USER_FIXED_BAY",
            )

            _, _, done, step_info = env.step(action_idx)

            selected_block = blocks_dict[selected_block_id]
            selected_analysis = analysis_map.get(selected_block_id) if analysis_map else None
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
            all_violations.extend(reconstructed_violations)

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
            result['forced_override'] = forced_override_active
            result['precedence_blocked_ids'] = ','.join(str(block_id) for block_id in precedence_blocked_ids)
            result['interactive_precedence_blocked_ids'] = ','.join(str(block_id) for block_id in precedence_blocked_ids)
            result['interactive_manual_bay_override'] = manual_bay_override or ''

            schedule_results.append(result)

            decision_snapshot = getattr(rl_scheduler, 'last_decision_snapshot', None) or {}
            decision_trace.append({
                'step': int(assembly_sequence),
                'selected_block_id': selected_block_id,
                'selected_action_idx': decision_snapshot.get('selected_action_idx'),
                'available_count': decision_snapshot.get('available_count', len(filtered_available_ids)),
                'available_indices_json': json.dumps(decision_snapshot.get('available_indices', filtered_available_ids), ensure_ascii=False),
                'current_date': env.assembly_current_date.strftime('%Y-%m-%d'),
                'current_datetime': current_datetime.strftime('%Y-%m-%d %H:%M'),
                'selection_method': 'rl',
                'selection_reason': decision_snapshot.get('selected_analysis_reason') or (selected_analysis.get('inclusion_reason') if selected_analysis else ''),
                'masking_stage_used': decision_snapshot.get('selected_masking_stage') or (selected_analysis.get('masking_stage') if selected_analysis else ''),
                'selected_three_bay_check': decision_snapshot.get('selected_three_bay_check') or (selected_analysis.get('three_bay_check') if selected_analysis else ''),
                'forced_override': bool(forced_override_active or decision_snapshot.get('forced_action')),
                'precedence_blocked_ids': ','.join(str(block_id) for block_id in precedence_blocked_ids),
                'manual_bay_override': manual_bay_override or '',
                'assigned_bay': assigned_bay if isinstance(assigned_bay, str) else assigned_bay.value,
                'confidence': decision_snapshot.get('confidence', confidence),
                'rl_log_prob_json': json.dumps(decision_snapshot.get('action_logits'), ensure_ascii=False),
            })

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
            env.assembly_afternoon_guard_blocks_all,
            decision_trace=decision_trace,
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
    afternoon_guard_blocks_day: Set[int] = set()  # [AGENT-EDIT] legacy no-op
    afternoon_guard_blocks_all: Set[int] = set()  # [AGENT-EDIT] legacy no-op
    
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
                except Exception as exc:
                    # [AGENT-EDIT] 일자 전환 상태 계산 실패는 숨기지 않는다.
                    raise RuntimeError(
                        f"RL 일자 전환 makespan 계산 실패: date={current_date}, blocks={len(daily_sequence)}"
                    ) from exc

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
            # [AGENT-EDIT] RL 경로의 긴급 용량 우회도 config 토글로 제어한다.
            if getattr(env.constraint_config, 'enable_rl_urgent_capacity_bypass', True):
                capacity_bypass = True
            else:
                current_date += timedelta(days=1)
                current_datetime = datetime.combine(current_date, datetime.min.time().replace(hour=8))
                day_counter += 1
                date_start_time = current_datetime
                daily_sequence = []
                afternoon_guard_blocks_day = set()
                is_weekend = current_datetime.weekday() >= 5
                if is_weekend:
                    env.capacity_tracker.reset_weekend()
                else:
                    env.capacity_tracker.reset_daily()
                if day_counter > max_days:
                    break
                continue
        
        # 선택 가능한 블록 찾기 (용량에 여유가 있을 때만)
        env.constraint_checker.set_sequence(selected_blocks)
        if capacity_bypass and getattr(env.constraint_config, 'enable_rl_urgent_capacity_bypass', True):
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
            if capacity_bypass and getattr(env.constraint_config, 'enable_rl_urgent_capacity_bypass', True):
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
            analysis_map=analysis_map,
            collect_episode_data=collect_episode_data,
            enable_grad=enable_grad,
            collect_step_metrics=collect_step_metrics,
        )
        
        selected_block = blocks_dict[selected_block_id]
        selected_analysis = next((analysis for analysis in block_analysis if analysis.get('block_id') == selected_block_id), None)
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
                except Exception as exc:
                    # [AGENT-EDIT] 용량 초과 시점의 상태 계산 실패는 숨기지 않는다.
                    raise RuntimeError(
                        f"RL 용량 초과 일자 전환 makespan 계산 실패: date={current_date}, blocks={len(daily_sequence)}"
                    ) from exc

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
        bs = None
        detailed = None
        try:
            # 현재 날짜의 makespan 계산 (머신 상태 포함)
            makespan_sec, detailed = env.calculate_makespan(
                daily_sequence, current_bay_assignments, previous_machine_state,
                afternoon_guard_blocks=afternoon_guard_blocks_day
            )
            bs = next((b for b in detailed['block_schedules'] if b['block_id'] == selected_block_id), None)
            if bs is None:
                raise RuntimeError(f"선택 블록 스케줄을 찾지 못함: block_id={selected_block_id}")
            block_start_time = date_start_time + timedelta(seconds=bs['start_seconds'])
            block_end_time = date_start_time + timedelta(seconds=bs['end_seconds'])
            total_completion_time = date_start_time + timedelta(seconds=makespan_sec)
            makespan_minutes = makespan_sec / 60.0
            makespan_hours = makespan_minutes / 60.0
        except Exception as makespan_err:
            # [AGENT-EDIT] RL 핵심 경로의 makespan 실패를 단순 합산으로 숨기지 않는다.
            raise RuntimeError(
                f"RL 블록 makespan 계산 실패: block_id={selected_block_id}, date={current_date}"
            ) from makespan_err
        
        # 실제 시작 시간으로 제약 검증 (실제 처리된 블록이므로 모든 제약조건 검사)
        # 🆕 실제 머신 2번(전면SAW) 시작 시간 계산 (makespan 계산 결과에서 추출)
        actual_machine_2_start_time = None
        if bs is not None and detailed is not None:
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
            except Exception as exc:
                # [AGENT-EDIT] validator 입력 복원 실패는 숨기지 않는다.
                raise RuntimeError(
                    f"RL completed_steps 복원 실패: record_block_id={rec.get('block_id')}"
                ) from exc
        block_violations = env._validate_and_commit_constraints_action(
            selected_block,
            assigned_bay,
            block_start_time if block_start_time is not None else current_datetime,
            actual_machine_2_start_time=actual_machine_2_start_time,
            processing_time_seconds=sum(selected_block.processing_times) * 60,
            step_start_time=block_start_time if block_start_time else current_datetime,
            step_end_time=block_end_time if block_end_time else (block_start_time if block_start_time else current_datetime),
            current_in_history=False,
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
        except Exception as exc:
            # [AGENT-EDIT] validator 히스토리 입력 실패는 숨기지 않는다.
            raise RuntimeError(
                f"RL completed_steps 기록 실패: block_id={selected_block.block_id}"
            ) from exc

        # 🧹 휴리스틱과 동일: 위반 dedup 후 ERROR/WARNING만 카운트
        primary_violations, info_violations = dedup_violations(
            block_violations, keep_info=True, include_guard=False
        )
        violation_count = len([v for v in primary_violations if v.severity in ['ERROR', 'WARNING']])

        # [AGENT-EDIT] 총괄 metric은 raw 기준으로 집계하고 create_block_result에서 primary/meta로 다시 분리한다.
        all_violations.extend(block_violations)
        
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

        # ==== [AGENT-ADD] PPO용 스텝 보상 입력(실제 시간/위반) ====
        if collect_episode_data and hasattr(rl_scheduler, "episode_data") and rl_scheduler.episode_data:
            try:
                last_step = rl_scheduler.episode_data[-1]
                # 마지막 스텝이 현재 선택 블록인지 확인
                if last_step.get('selected_block_id') == selected_block_id:
                    if block_start_time and block_end_time:
                        step_duration_hours = (block_end_time - block_start_time).total_seconds() / 3600.0
                    else:
                        step_duration_hours = 0.0
                    last_step['step_duration_hours'] = step_duration_hours
                    last_step['step_violation_count'] = int(violation_count)
            except Exception as exc:
                # [AGENT-EDIT] PPO 학습용 step metric 기록 실패는 숨기지 않는다.
                raise RuntimeError(
                    f"RL step metric 기록 실패: block_id={selected_block_id}"
                ) from exc
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
