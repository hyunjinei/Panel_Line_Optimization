# enhanced_pbs_env.py
import sys
import os

# 현재 파일의 상위 디렉토리 경로를 sys.path에 추가
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
"""
Enhanced Panel Block Shop Environment - PFSP Version
===================================================

PFSP(Permutation Flow Shop) 방식을 적용한 패널 블록 생산 환경

주요 변경사항:
- Job Shop → PFSP 방식 변경
- 블록 순서 결정 + 공정별 처리 두 단계
- P/S 제약이 순서 기반으로 단순화
- calculate_makespan 로직을 step별로 분할

제약조건 구현:
- 판계 작업: 순서 결정 단계에서 적용
- SAW 공정: 순서 결정 단계에서 적용  
- 론지 취부: 분기 선택 단계에서 적용
"""

# [AGENT-ADD] enhanced_pbs_env.py moved to pbs_env/core.py.

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



from enhanced_environment.pbs_env.debug import EnvDebugMixin
from enhanced_environment.pbs_env.metadata import MetadataMixin
from enhanced_environment.pbs_env.setup import SetupMixin
from enhanced_environment.pbs_env.assembly_mode import AssemblyModeMixin
from enhanced_environment.pbs_env.step_logic import StepLogicMixin
from enhanced_environment.pbs_env.bay_ops import BayOpsMixin
from enhanced_environment.pbs_env.validation import ValidationRewardMixin
from enhanced_environment.pbs_env.observation import ObservationMixin
from enhanced_environment.pbs_env.rendering import RenderingMixin

class EnhancedPanelBlockShop(EnvDebugMixin, MetadataMixin, SetupMixin, AssemblyModeMixin, StepLogicMixin, BayOpsMixin, ValidationRewardMixin, ObservationMixin, RenderingMixin):
    """
    PFSP 방식 패널 블록 샵 환경
    
    두 단계 처리:
    1. SEQUENCE_DECISION: 전체 블록 순서 결정 (한번만)
    2. PROCESS_EXECUTION: 공정별 순차 처리 (순서대로)
    3. BRANCH_SELECTION: 분기점에서 베이 선택
    
    Attributes:
        blocks: 처리할 블록들
        sequence_state: PFSP 순서 상태
        current_time: 현재 시간
        completion_tables: calculate_makespan 방식의 완료 시간 테이블
        constraint_managers: 제약조건 매니저들
        constraint_checker: 액션 마스킹 체커
        validator: 분리된 제약조건 검증기
    """
    def __init__(self,
                 blocks: List[EnhancedBlock],
                 start_time: datetime = None,
                 max_steps: int = 1000,
                 logging_level: int = logging.INFO,
                 constraint_config: 'ConstraintConfig' = None,
                 enable_visualization: bool = False,
                 metadata: Dict = None,
                 decoding_mode: str = "pfsp",
                 assembly_max_days: int = 10,
                 assembly_capacity_bypass: bool = False,
                 assembly_update_state_on_capacity: bool = True,
                 assembly_update_state_on_empty: bool = True,
                 assembly_keep_bay_assignments_on_capacity: bool = False,
                 calendar_overrides: Optional[Dict] = None):
        """
        PFSP 환경 초기화
    
        Args:
            blocks: 처리할 블록들
            start_time: 시작 시간 (None이면 현재 날짜 08:00 사용)
            max_steps: 최대 스텝 수
            logging_level: 로깅 레벨
            constraint_config: 제약조건 설정 (None이면 기본 설정 사용)
            enable_visualization: 시각화 활성화 여부
            metadata: 데이터 구조 메타데이터 (None이면 기본 처리)
        """
        super().__init__()
    
        # 기본 설정
        self.original_blocks = blocks.copy()
        self.blocks_dict = {block.block_id: block for block in blocks}  # 빠른 검색용
    
        # 메타데이터 저장 및 처리
        self.metadata = metadata or {}
        # [AGENT-EDIT] 캘린더 오버라이드 (하드코딩 입력용 + 런타임 설정)
        config_calendar_overrides = {}
        if constraint_config and getattr(constraint_config, "calendar_overrides", None):
            config_calendar_overrides = constraint_config.calendar_overrides
        self.calendar_overrides = (
            calendar_overrides
            or self.metadata.get('calendar_overrides')
            or config_calendar_overrides
            or {}
        )
        self._process_metadata()
    
        # 현재 날짜의 08:00로 시작 시간 설정
        if start_time is None:
            current_date = datetime.now().date()
            self.start_time = datetime.combine(current_date, time(8, 0))
        else:
            self.start_time = start_time
    
        self.max_steps = max_steps
        self.enable_visualization = enable_visualization
        # [AGENT-ADD] 디코딩 모드 (PFSP 기본 / Assembly Decoding 전용)
        self.decoding_mode = decoding_mode
        self.assembly_max_days = assembly_max_days
        # [AGENT-ADD] Assembly Decoding 파라미터 (기존 동작 보존용)
        self.assembly_capacity_bypass = assembly_capacity_bypass
        self.assembly_update_state_on_capacity = assembly_update_state_on_capacity
        self.assembly_update_state_on_empty = assembly_update_state_on_empty
        self.assembly_keep_bay_assignments_on_capacity = assembly_keep_bay_assignments_on_capacity
    
        # 로깅 설정
        self.logger = Logger.setup_logger("EnhancedPBS_PFSP", logging_level)
    
        # PFSP 상태 변수 초기화
        self.sequence_state = SequenceState()
        self.current_time = self.start_time
        self.current_step = 0
        self.makespan = 0.0
        self.is_done = False
    
        # 완료 시간 테이블 (calculate_makespan 방식)
        self.completion_tables = {
            'COMMON': np.zeros((len(blocks) + 1, 6)),    # [블록+1, 공정+1] 공통 5개 공정 (판계, 전면SAW, TurnOver, 후면SAW, NC)
            'BRANCH_A': np.zeros((len(blocks) + 1, 4)),  # 분기A 3개 공정 (론지취부, 론지용접, 수정)
            'BRANCH_B': np.zeros((len(blocks) + 1, 4))   # 분기B 3개 공정 (론지취부, 론지용접, 수정)
        }
    
        # 처리 완료된 단계들
        self.completed_steps = []  # ProcessStep 리스트
    
        # 제약조건 설정 저장 (매니저 초기화 전에 먼저 설정)
        self.constraint_config = constraint_config if constraint_config else ConstraintConfig()
    
        # [AGENT-EDIT] 데이터 폭에 맞춰 작업장 창 상한을 동적으로 조정
        #  - 블록들의 조립착수일이 start_time 이후 몇 일까지 퍼져 있는지 계산
        #  - 기존 설정(workshop_priority_max_window_days)보다 넓으면 상한을 확장
        try:
            base_date = self.start_time.date()
            offsets = []
            for blk in blocks:
                start_dt = getattr(blk, 'assembly_start_date', None)
                if isinstance(start_dt, datetime):
                    offsets.append((start_dt.date() - base_date).days)
            if offsets:
                max_offset = max(offsets)
                min_offset = min(offsets)
                span = max_offset  # 현재일 대비 가장 먼 리드 일수
                cfg_max = getattr(self.constraint_config, 'workshop_priority_max_window_days', 5)
                cfg_init = getattr(self.constraint_config, 'workshop_priority_initial_window_days', 2)
                new_max = max(cfg_init, cfg_max, span)
                # 음수/너무 작은 값 보호
                new_max = max(0, int(new_max))
                self.constraint_config.workshop_priority_max_window_days = new_max
        except Exception:
            # 실패 시 기존 설정 유지
            pass
    
        # 제약조건 매니저들 초기화 (메타데이터 포함)
        self._initialize_constraint_managers_with_metadata()
    
        # 액션 마스킹 체커 초기화 (메타데이터 포함)
        self.constraint_checker = ConstraintChecker(
            capacity_tracker=self.capacity_tracker,
            ps_manager=self.ps_manager,
            bay_tracker=self.bay_tracker,
            calendar_manager=self.calendar_manager,
            constraint_config=self.constraint_config,
            metadata=self.metadata  # 메타데이터 전달
        )
    
        # ✅ 연속 3판 B베이 방지를 위한 blocks_dict 설정
        self.constraint_checker.set_blocks_dict(self.blocks_dict)
    
        # [AGENT-ADD] Assembly Decoding 모드 초기화
        if self.decoding_mode == "assembly":
            self._initialize_assembly_decoding_mode()
    
        # ✅ 제약조건 검증기 초기화
        self.validator = ConstraintValidator(
            capacity_tracker=self.capacity_tracker,
            calendar_manager=self.calendar_manager,
            bay_tracker=self.bay_tracker,
            ps_manager=self.ps_manager,
            subassembly_complete_info=self.subassembly_complete_info,
            cross_seam_mixing_control=self.cross_seam_mixing_control,
            completed_steps=self.completed_steps,
            blocks_dict=self.blocks_dict
        )
        self.validator.set_environment(self)
    
        # 환경 상태
        self.env_state = EnvironmentState(
            current_time=self.current_time,
            current_date=self.current_time.date()
        )
    
        # 통계 추적
        self.violation_history = []
        self.action_history = []
        self.performance_metrics = defaultdict(list)
    
        # P/S 쌍 등록
        self._register_ps_pairs()
    
        # Gym 공간 정의
        self._setup_action_observation_spaces()
    
        # self.logger.info(f"Enhanced PBS PFSP Environment 초기화 완료: {len(blocks)}개 블록, 메타데이터: {len(self.metadata.keys())}개 카테고리")
    

    def copy(self):
        """
        환경 복사 (reinforce_train.py의 copy.deepcopy 대체)
        현재 상태를 그대로 복사한 새로운 환경 인스턴스 생성
    
        Returns:
            copied_env: 복사된 환경 인스턴스
        """
        import copy
    
        # 🔥 새로운 환경 인스턴스 생성 (동일한 초기화 파라미터)
        copied_env = EnhancedPanelBlockShop(
            blocks=copy.deepcopy(self.original_blocks),
            start_time=self.start_time,
            max_steps=self.max_steps,
            logging_level=logging.INFO,
            constraint_config=self.constraint_config,
            enable_visualization=False,
            metadata=copy.deepcopy(self.metadata),
            decoding_mode=self.decoding_mode,
            assembly_max_days=self.assembly_max_days,
            assembly_capacity_bypass=self.assembly_capacity_bypass,
            assembly_update_state_on_capacity=self.assembly_update_state_on_capacity,
            assembly_update_state_on_empty=self.assembly_update_state_on_empty,
            assembly_keep_bay_assignments_on_capacity=self.assembly_keep_bay_assignments_on_capacity
        )
    
        # 🔥 현재 상태 복사
        copied_env.current_time = self.current_time
        copied_env.env_state.current_time = self.current_time
        copied_env.env_state.current_date = self.current_time.date()
    
        # 🔥 제약조건 매니저 상태 복사
        copied_env.capacity_tracker = copy.deepcopy(self.capacity_tracker)
        copied_env.ps_manager = copy.deepcopy(self.ps_manager)
        copied_env.bay_tracker = copy.deepcopy(self.bay_tracker)
        copied_env.calendar_manager = copy.deepcopy(self.calendar_manager)
    
        # 🔥 constraint_checker 상태 복사
        copied_env.constraint_checker.selected_blocks_set = self.constraint_checker.selected_blocks_set.copy()
        copied_env.constraint_checker.relaxation_level = self.constraint_checker.relaxation_level
        copied_env.constraint_checker.last_assembly_type = self.constraint_checker.last_assembly_type
        copied_env.constraint_checker.cross_seam_consecutive_count = self.constraint_checker.cross_seam_consecutive_count
    
        # 🔥 기타 상태 복사
        copied_env.violation_history = copy.deepcopy(self.violation_history)
        copied_env.action_history = copy.deepcopy(self.action_history)
        copied_env.performance_metrics = copy.deepcopy(self.performance_metrics)
    
        # [AGENT-ADD] Assembly Decoding 상태 복사
        if self.decoding_mode == "assembly":
            copied_env._reset_assembly_state()
            copied_env.assembly_selected_blocks = copy.deepcopy(self.assembly_selected_blocks)
            copied_env.assembly_last_assembly_type = self.assembly_last_assembly_type
            copied_env.assembly_day_counter = self.assembly_day_counter
            copied_env.assembly_current_date = self.assembly_current_date
            copied_env.assembly_current_datetime = self.assembly_current_datetime
            copied_env.assembly_date_start_time = self.assembly_date_start_time
            copied_env.assembly_daily_sequence = copy.deepcopy(self.assembly_daily_sequence)
            copied_env.assembly_total_sequence = copy.deepcopy(self.assembly_total_sequence)
            copied_env.assembly_current_bay_assignments = copy.deepcopy(self.assembly_current_bay_assignments)
            copied_env.assembly_all_bay_assignments = copy.deepcopy(self.assembly_all_bay_assignments)
            copied_env.assembly_previous_machine_state = copy.deepcopy(self.assembly_previous_machine_state)
            copied_env.assembly_afternoon_guard_blocks_day = copy.deepcopy(self.assembly_afternoon_guard_blocks_day)
            copied_env.assembly_afternoon_guard_blocks_all = copy.deepcopy(self.assembly_afternoon_guard_blocks_all)
            copied_env._assembly_last_available_ids = copy.deepcopy(self._assembly_last_available_ids)
            copied_env._assembly_last_block_analysis = copy.deepcopy(self._assembly_last_block_analysis)
            copied_env._assembly_last_violations = copy.deepcopy(self._assembly_last_violations)
            copied_env._assembly_step_index = self._assembly_step_index
            copied_env.current_time = copied_env.assembly_current_datetime
    
        return copied_env
    

    def reset(self) -> np.ndarray:
        """
        PFSP 환경 리셋
    
        Returns:
            초기 관찰 상태
        """
        self.logger.info("PFSP 환경 리셋 시작")
    
        # 상태 변수 리셋
        self.sequence_state = SequenceState()
        self.current_time = self.start_time
        self.current_step = 0
        self.makespan = 0.0
        self.is_done = False
    
        # 완료 시간 테이블 리셋
        num_blocks = len(self.original_blocks)
        self.completion_tables = {  
            'COMMON': np.zeros((num_blocks + 1, 6)),
            'BRANCH_A': np.zeros((num_blocks + 1, 4)),
            'BRANCH_B': np.zeros((num_blocks + 1, 4))
        }
    
        self.completed_steps.clear()
    
        # 매니저들 리셋
        self.capacity_tracker.reset_daily()
        self.capacity_tracker.reset_weekend()
        self.ps_manager.reset()
        self.bay_tracker.reset()
        self.constraint_checker.reset()
    
        # 환경 상태 리셋
        self.env_state = EnvironmentState(
            current_time=self.current_time,
            current_date=self.current_time.date(),
            is_weekend=TimeUtils.is_weekend(self.current_time),
            is_hot_season=self.calendar_manager.is_hot_season(self.current_time)
        )
    
        # 통계 리셋
        self.violation_history.clear()
        self.action_history.clear()
        for key in self.performance_metrics:
            self.performance_metrics[key].clear()
    
        self.logger.info(f"PFSP 환경 리셋 완료: {len(self.original_blocks)}개 블록")
    
        # [AGENT-ADD] Assembly Decoding 모드 초기화
        if self.decoding_mode == "assembly":
            self._reset_assembly_state()
    
        return self._get_observation()
    
    # ==== [AGENT-ADD BEGIN: Assembly Decoding 전용 상태/유틸] ====
