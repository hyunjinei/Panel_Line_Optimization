"""
Environment debug helpers
"""

# [AGENT-ADD] Split from pbs_env/core.py for readability.

import os  # [AGENT-EDIT] PBS_FORCE_DEBUG 환경변수 조회에 필요
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

class EnvDebugMixin:
    def _resolve_debug_flag(self, config: Optional['ConstraintConfig']) -> bool:
        """
        verbose 디버그 출력 여부를 결정한다.
        우선순위:
          1) PBS_FORCE_DEBUG 환경변수
          2) ConstraintConfig.enable_debug_violations
          3) 기본값 True
        """
        # constraint_checker에 동일 로직이 있으면 위임
        if hasattr(self, 'constraint_checker') and hasattr(self.constraint_checker, '_resolve_debug_flag'):
            return self.constraint_checker._resolve_debug_flag(config)
        env_value = os.environ.get("PBS_FORCE_DEBUG", "")
        if env_value:
            normalized = env_value.strip().lower()
            if normalized in {"1", "true", "on", "yes"}:
                return True
            if normalized in {"0", "false", "off", "no"}:
                return False
        if config is not None:
            return getattr(config, 'enable_debug_violations', True)
        return True
