"""
Environment setup helpers
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

class SetupMixin:
    def _setup_action_observation_spaces(self):
        """PFSP용 액션/관찰 공간 설정"""
        num_blocks = len(self.original_blocks)
    
        # 액션 공간: 단계별로 다름
        # Phase 1 (SEQUENCE_DECISION): 순서 선택 - Discrete(매우 큰 수)
        # Phase 2 (BRANCH_SELECTION): 베이 선택 - Discrete(2)
    
        # 실제로는 get_sequence_mask()에서 유효한 순서들만 제공하므로
        # 단순히 선택 인덱스만 필요
        max_sequence_choices = 1000  # 최대 1000개 순서 중 선택
    
        self.action_space = gym.spaces.Discrete(max_sequence_choices)
    
        # 관찰 공간: PFSP 상태 정보 포함
        obs_dim = self._calculate_observation_dimension()
        self.observation_space = gym.spaces.Box(
            low=-np.inf, high=np.inf, 
            shape=(obs_dim,), dtype=np.float32
        )
    

    def _calculate_observation_dimension(self) -> int:
        """관찰 공간 차원 계산"""
        # 기본 환경 정보: 8개 (시간, 단계, 진행률 등)
        # 순서 정보: 10개 (현재 순서, 진행 상황 등)
        # 용량 정보: 8개
        # 베이 정보: 6개
        # 블록별 특성: 15개 * 블록수 (간소화)
    
        base_dim = 8 + 10 + 8 + 6  # 32개
        block_features = 15  # 블록당 특성 수 (간소화)
        max_blocks = len(self.original_blocks)
    
        return base_dim + (block_features * max_blocks)
