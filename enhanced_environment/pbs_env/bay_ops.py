"""
Bay assignment and makespan helpers
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

class BayOpsMixin:
    def _auto_assign_bay(self, block: EnhancedBlock, return_analysis: bool = False):
        # 분리된 모듈의 함수를 호출
        return auto_assign_bay(
            block,
            self.constraint_config,
            self.bay_tracker,
            self.ps_manager,
            self.blocks_dict,
            self.logger,
            return_analysis
        )
    

    def _preview_assign_bay(self, block: EnhancedBlock, return_analysis: bool = False):
        """상태를 변경하지 않는 베이 배정"""
        # [AGENT-ADD] RL 후보 평가용 미리보기 (bay_tracker, ps_manager 보존)
        return preview_assign_bay(
            block,
            self.constraint_config,
            self.bay_tracker,
            self.ps_manager,
            self.blocks_dict,
            self.logger,
            return_analysis,
        )
    

    def calculate_makespan(self, sequence: List[int], branch_assignments: Dict[int, BayType], 
                          previous_machine_state: Dict = None,
                          afternoon_guard_blocks: Optional[Set[int]] = None) -> Tuple[float, Dict]:
        return calculate_makespan(self.blocks_dict, sequence, branch_assignments, previous_machine_state, afternoon_guard_blocks)
