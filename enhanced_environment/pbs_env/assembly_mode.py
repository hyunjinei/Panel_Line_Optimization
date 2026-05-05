"""
Assembly decoding helpers
"""

# [AGENT-ADD] Split from pbs_env/core.py for readability.

import gymnasium as gym
import numpy as np
from datetime import datetime, timedelta, time, date
from typing import List, Dict, Tuple, Optional, Any, Union, Set
import logging
from collections import defaultdict

# 데이터 구조 및 유틸리티
from enhanced_environment.models import (
    EnhancedBlock, EnvironmentState, ActionResult, ConstraintViolation,
    BayType, AssemblyType, PortStarboard, ActionMask, BlockSequence,
    ProcessPhase, SequenceState, ProcessStep
)
from enhanced_environment.common.utils_core import TimeUtils, DataConverter, Logger
from enhanced_environment.constraints import ConstraintConfig

# 제약조건 매니저
from enhanced_environment.constraints.managers import (
    CapacityTracker, PSBlockManager, BayStateTracker, CalendarManager
)
from enhanced_environment.masking import ConstraintChecker

# 분리된 모듈 임포트
from enhanced_environment.bay.assigner import auto_assign_bay, preview_assign_bay
from enhanced_environment.bay.makespan import calculate_makespan
from enhanced_environment.bay.validator import ConstraintValidator

class AssemblyModeMixin:
    def _initialize_assembly_decoding_mode(self) -> None:
        """Assembly Decoding 모드 기본 세팅"""
        self.constraint_checker.set_decoding_type("assembly")
        self.constraint_checker.set_assembly_blocks(self.original_blocks)
        self.constraint_checker.set_blocks_dict(self.blocks_dict)
        self._reset_assembly_state()
    

    def _reset_assembly_state(self) -> None:
        """Assembly Decoding 전용 상태 초기화"""
        self.assembly_selected_blocks: List[int] = []
        self.assembly_last_assembly_type: Optional[AssemblyType] = None
        self.assembly_day_counter: int = 1
        self.assembly_current_date = self.start_time.date()
        self.assembly_current_datetime = datetime.combine(
            self.assembly_current_date, time(8, 0)
        )
        # [AGENT-EDIT] 공장 휴무/중지 시간 반영 (하드코딩 캘린더)
        if hasattr(self, "calendar_manager") and self.calendar_manager is not None:
            self.assembly_current_datetime = self.calendar_manager.next_open_time(self.assembly_current_datetime)
            self.assembly_current_date = self.assembly_current_datetime.date()
        self.current_time = self.assembly_current_datetime
        self.assembly_date_start_time = self.assembly_current_datetime
        self.assembly_daily_sequence: List[int] = []
        self.assembly_total_sequence: List[int] = []
        self.assembly_current_bay_assignments: Dict[int, BayType] = {}
        self.assembly_all_bay_assignments: Dict[int, BayType] = {}
        self.assembly_previous_machine_state: Dict[str, Any] = {}
        self.assembly_afternoon_guard_blocks_day: Set[int] = set()
        self.assembly_afternoon_guard_blocks_all: Set[int] = set()
        self._assembly_last_available_ids: List[int] = []
        self._assembly_last_block_analysis: List[Dict] = []
        self._assembly_last_violations: List[ConstraintViolation] = []
        self._assembly_step_index: int = 0
    

    def _advance_assembly_day(self, reason: str = "") -> None:
        """Assembly Decoding 날짜 전환 및 머신 상태 갱신"""
        do_update_state = False
        if reason == "capacity_limit" and self.assembly_update_state_on_capacity:
            do_update_state = True
        if reason == "no_candidates" and self.assembly_update_state_on_empty:
            do_update_state = True
    
        if do_update_state and self.assembly_daily_sequence and self.assembly_current_bay_assignments:
            try:
                makespan_sec, detailed = self.calculate_makespan(
                    self.assembly_daily_sequence,
                    self.assembly_current_bay_assignments,
                    self.assembly_previous_machine_state,
                    afternoon_guard_blocks=self.assembly_afternoon_guard_blocks_day
                )
                final_state = detailed.get('final_machine_state', {}) or {}
                day_duration_seconds = 24 * 3600
                prev_offset = self.assembly_previous_machine_state.get('day_start_offset', 0) if isinstance(self.assembly_previous_machine_state, dict) else 0
                new_offset = prev_offset + day_duration_seconds
    
                def _shift(lst):
                    return [t + prev_offset for t in lst] if isinstance(lst, list) else []
    
                self.assembly_previous_machine_state = {
                    'common_times': _shift(final_state.get('common_times', [])),
                    'branch_a_times': _shift(final_state.get('branch_a_times', [])),
                    'branch_b_times': _shift(final_state.get('branch_b_times', [])),
                    'day_start_offset': new_offset
                }
            except Exception as exc:
                # [AGENT-EDIT] 일자 전환용 makespan 실패는 이후 날짜 상태를 오염시키므로 즉시 중단한다.
                raise RuntimeError(
                    f"assembly 일자 전환 makespan 계산 실패: date={self.assembly_current_date}, "
                    f"reason={reason}, blocks={len(self.assembly_daily_sequence)}"
                ) from exc
    
        self.assembly_current_date += timedelta(days=1)
        self.assembly_current_datetime = datetime.combine(
            self.assembly_current_date, time(8, 0)
        )
        # [AGENT-EDIT] 공장 휴무/중지 날짜는 건너뜀
        if hasattr(self, "calendar_manager") and self.calendar_manager is not None:
            # 휴무일이면 다음 가동일로 이동
            while self.calendar_manager.is_closed_day(self.assembly_current_datetime):
                self.assembly_current_date += timedelta(days=1)
                self.assembly_current_datetime = datetime.combine(
                    self.assembly_current_date, time(8, 0)
                )
        self.current_time = self.assembly_current_datetime
        self.assembly_day_counter += 1
        self.assembly_date_start_time = self.assembly_current_datetime
        self.assembly_daily_sequence = []
        if not (reason == "capacity_limit" and self.assembly_keep_bay_assignments_on_capacity):
            self.assembly_current_bay_assignments = {}
        self.assembly_afternoon_guard_blocks_day = set()
    
        # 용량 리셋
        is_weekend = self.assembly_current_datetime.weekday() >= 5
        if is_weekend:
            self.capacity_tracker.reset_weekend()
        else:
            self.capacity_tracker.reset_daily()
    

    def get_available_actions_assembly(self) -> Tuple[List[int], List[ConstraintViolation], List[Dict]]:
        """Assembly Decoding에서 선택 가능한 블록 목록 반환 (상태 내부 관리)"""
        if self.decoding_mode != "assembly":
            return [], [], []
    
        # 날짜 전환/용량 체크를 포함해 후보를 보장
        while True:
            # [AGENT-EDIT] 공장 휴무/중지 시간 처리
            if hasattr(self, "calendar_manager") and self.calendar_manager is not None:
                # 휴무일이면 다음날로 이동
                if self.calendar_manager.is_closed_day(self.assembly_current_datetime):
                    self._advance_assembly_day("calendar_closed")
                    continue
                # 중지 시간(점심/부분 중지)이면 다음 가동 시각으로 이동
                if self.calendar_manager.is_closed_time(self.assembly_current_datetime):
                    next_open = self.calendar_manager.next_open_time(self.assembly_current_datetime)
                    # 날짜가 바뀌면 일자 전환 처리
                    if next_open.date() != self.assembly_current_date:
                        while self.assembly_current_date < next_open.date():
                            self._advance_assembly_day("calendar_closed")
                        # next_open은 새 날짜 기준이므로 시간도 업데이트
                        self.assembly_current_datetime = next_open
                        self.current_time = next_open
                        self.assembly_date_start_time = datetime.combine(
                            self.assembly_current_date, time(8, 0)
                        )
                    else:
                        self.assembly_current_datetime = next_open
                        self.current_time = next_open
    
            if self.assembly_day_counter > self.assembly_max_days:
                self.is_done = True
                return [], [], []
    
            is_weekend = self.assembly_current_datetime.weekday() >= 5
            is_hot_season = self.calendar_manager.is_hot_season(self.assembly_current_datetime)
            is_holiday_eve = self.calendar_manager.is_holiday_eve(self.assembly_current_datetime)
            current_capacity_used = self.capacity_tracker.get_capacity_used(is_weekend)
            capacity_time = self.assembly_date_start_time if hasattr(self, "assembly_date_start_time") else self.assembly_current_datetime
            actual_capacity_limit = self.capacity_tracker.get_capacity_limits(
                is_weekend, is_hot_season, is_holiday_eve, current_time=capacity_time
            )
    
            capacity_bypass = False
            if self.assembly_capacity_bypass:
                # [AGENT-ADD] 리드타임 임박 블록이 있으면 용량 제약을 임시 완화
                min_lead_days = getattr(self.constraint_config, 'minimum_panel_lead_days', 2)
                urgent_exists = False
                for blk in self.original_blocks:
                    if blk.block_id in self.assembly_selected_blocks:
                        continue
                    start_dt = getattr(blk, 'assembly_start_date', None)
                    if not isinstance(start_dt, datetime):
                        continue
                    slack_days = (start_dt.date() - self.assembly_current_date).days
                    if slack_days <= min_lead_days:
                        urgent_exists = True
                        break
    
                if current_capacity_used >= actual_capacity_limit and not urgent_exists:
                    self._advance_assembly_day("capacity_limit")
                    continue
                elif current_capacity_used >= actual_capacity_limit and urgent_exists:
                    if getattr(self.constraint_config, 'enable_assembly_urgent_capacity_bypass', True):
                        capacity_bypass = True
                    else:
                        self._advance_assembly_day("capacity_limit")
                        continue
            else:
                if current_capacity_used >= actual_capacity_limit:
                    self._advance_assembly_day("capacity_limit")
                    continue
    
            self.constraint_checker.set_sequence(self.assembly_selected_blocks)
            if capacity_bypass and getattr(self.constraint_config, 'enable_assembly_urgent_capacity_bypass', True):
                cfg = self.constraint_config
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
                available_ids, violations, block_analysis = self.constraint_checker.get_next_available_blocks_assembly(
                    self.original_blocks,
                    self.assembly_current_datetime,
                    self.assembly_selected_blocks,
                    self.assembly_last_assembly_type,
                    panel_date=self.assembly_current_date,
                    previous_machine_state=self.assembly_previous_machine_state,
                    current_bay_assignments=self.assembly_current_bay_assignments,
                    current_day_selected_blocks=self.assembly_daily_sequence
                )
            finally:
                if capacity_bypass and getattr(self.constraint_config, 'enable_assembly_urgent_capacity_bypass', True):
                    cfg.enable_p5_8_weekday_capacity = orig_flags['enable_p5_8_weekday_capacity']
                    cfg.enable_p5_9_block_count_check = orig_flags['enable_p5_9_block_count_check']
                    cfg.enable_p5_10_weekend_capacity = orig_flags['enable_p5_10_weekend_capacity']
                    cfg.enable_p5_16_hot_season_capacity = orig_flags['enable_p5_16_hot_season_capacity']
    
            # [AGENT-EDIT] 계획일 기준 하드 용량 필터 (마스킹/검증 불일치 방지)
            if available_ids:
                capacity_time = self.assembly_date_start_time
                is_weekend = self.calendar_manager.is_weekend(capacity_time)
                is_hot_season = self.calendar_manager.is_hot_season(capacity_time)
                is_holiday_eve = self.calendar_manager.is_holiday_eve(capacity_time)
                filtered_ids = []
                for bid in available_ids:
                    blk = self.blocks_dict.get(bid)
                    if not blk:
                        continue
                    can_add, reason = self.capacity_tracker.can_add_block(
                        blk, is_weekend, is_hot_season, is_holiday_eve, current_time=capacity_time
                    )
                    if can_add:
                        filtered_ids.append(bid)
                    else:
                        # 후보 분석 정보에 기록 (선택 불가 사유만 남김)
                        for analysis in block_analysis:
                            if analysis.get('block_id') == bid:
                                analysis['is_available'] = False
                                analysis['exclusion_reason'] = f"용량 제약 위반: {reason}"
                                analysis.setdefault('constraint_checks', {})['P5#8_9_10_16_capacity'] = {
                                    'result': 'FAIL',
                                    'reason': reason
                                }
                                break
                if not filtered_ids:
                    if self._resolve_debug_flag(self.constraint_config):
                        print("⚠️ 용량 제약으로 당일 선택 불가 → 다음날로 전환")
                    self._advance_assembly_day("capacity_limit")
                    continue
                available_ids = filtered_ids
    
            self._assembly_last_available_ids = list(available_ids)
            self._assembly_last_block_analysis = list(block_analysis)
            self._assembly_last_violations = list(violations)
            # [AGENT-ADD] env_state 계산용 마지막 후보 저장
            self._last_available_ids = list(available_ids)
    
            if available_ids:
                return available_ids, violations, block_analysis
    
            # 후보가 없으면 다음날로 이동 후 재시도
            self._advance_assembly_day("no_candidates")
    
    # ==== [AGENT-ADD END: Assembly Decoding 전용 상태/유틸] ====
