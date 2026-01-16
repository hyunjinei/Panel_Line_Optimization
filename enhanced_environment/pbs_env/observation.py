"""
Observation encoding helpers
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

class ObservationMixin:
    def _get_observation(self) -> np.ndarray:
        """
        현재 관찰 상태 반환
    
        Returns:
            관찰 상태 벡터
        """
        obs_parts = []
    
        # 시간 정보
        time_features = [
            self.current_time.hour / 24.0,  # 정규화된 시간
            self.current_time.weekday() / 6.0,  # 정규화된 요일
            float(TimeUtils.is_weekend(self.current_time)),
            float(self.calendar_manager.is_hot_season(self.current_time))
        ]
        obs_parts.extend(time_features)
    
        # 순서 정보
        sequence_features = self._encode_sequence_state()
        obs_parts.extend(sequence_features)
    
        # 용량 정보
        capacity_features = self._encode_capacity_state()
        obs_parts.extend(capacity_features)
    
        # 베이 정보
        bay_features = self._encode_bay_state()
        obs_parts.extend(bay_features)
    
        # 블록 정보 (간소화)
        block_features = self._encode_block_state()
        obs_parts.extend(block_features)
    
        return np.array(obs_parts, dtype=np.float32)
    

    def _encode_sequence_state(self) -> List[float]:
        """순서 상태를 특성 벡터로 인코딩"""
        total_blocks = len(self.original_blocks)
        max_processes = 8  # 8개 공정으로 변경
    
        sequence_features = [
            # 진행 상황
            self.sequence_state.current_block_index / max(1, total_blocks),
            self.sequence_state.current_process / max_processes,
            float(self.sequence_state.sequence_decided),
            len(self.sequence_state.block_sequence) / max(1, total_blocks),
    
            # 단계 정보 (one-hot encoding)
            float(self.sequence_state.current_phase == ProcessPhase.SEQUENCE_DECISION),
            float(self.sequence_state.current_phase == ProcessPhase.PROCESS_EXECUTION),
            float(self.sequence_state.current_phase == ProcessPhase.BRANCH_SELECTION),
            float(self.sequence_state.current_phase == ProcessPhase.COMPLETED),
    
            # 공정 타입 (8개 공정 구조)
            float(self.sequence_state.current_process in self.sequence_state.common_processes),
            float(self.sequence_state.current_process in self.sequence_state.branch_processes),
        ]
        return sequence_features
    

    def _encode_capacity_state(self) -> List[float]:
        """용량 상태를 특성 벡터로 인코딩 (심수 기준으로 단순화)"""
        is_weekend = TimeUtils.is_weekend(self.current_time)
        is_hot_season = self.calendar_manager.is_hot_season(self.current_time)
        capacity_status = self.capacity_tracker.get_status(is_weekend, is_hot_season, current_time=self.current_time)
    
        capacity_features = [
            capacity_status["seam_used"] / max(1, capacity_status["seam_limit"]),
            capacity_status["seam_remaining"] / max(1, capacity_status["seam_limit"]),
            capacity_status["utilization_seam"],
            float(is_weekend),
            float(is_hot_season)
        ]
        return capacity_features
    

    def _encode_bay_state(self) -> List[float]:
        """베이 상태를 특성 벡터로 인코딩"""
        bay_status = self.bay_tracker.get_status()
        total_worktime = bay_status["bay_35a_worktime"] + bay_status["bay_36b_worktime"]
        total_blocks = max(1, len(self.completed_steps))
    
        bay_features = [
            bay_status["bay_35a_worktime"] / max(1, total_worktime),
            bay_status["bay_36b_worktime"] / max(1, total_worktime),
            bay_status["bay_35a_blocks"] / total_blocks,
            bay_status["bay_36b_blocks"] / total_blocks,
            bay_status["load_balance_score"],
            bay_status["bay_35a_consecutive"] / 5.0  # 최대 5로 정규화
        ]
        return bay_features
    

    def _encode_block_state(self) -> List[float]:
        """블록 상태를 간소화하여 인코딩"""
        max_blocks = len(self.original_blocks)
        block_feature_dim = 15  # 간소화된 특성 수
    
        features = [0.0] * (max_blocks * block_feature_dim)
    
        # 현재 처리할 블록만 인코딩
        current_block_id = self.sequence_state.get_next_block_to_process()
        if current_block_id and current_block_id in self.blocks_dict:
            block = self.blocks_dict[current_block_id]
            base_idx = 0  # 첫 번째 슬롯에만 인코딩
    
            # 블록 기본 정보
            features[base_idx] = block.block_id / max_blocks
            features[base_idx + 1] = sum(block.processing_times) / 10000
            features[base_idx + 2] = block.seam_count / 5.0
    
            # 납기 정보
            time_to_deadline = (block.max_start_date - self.current_time).total_seconds()
            features[base_idx + 3] = max(0, min(1, time_to_deadline / (24 * 3600)))
    
            # P/S 정보
            features[base_idx + 4] = float(block.port_starboard == PortStarboard.PORT)
            features[base_idx + 5] = float(block.port_starboard == PortStarboard.STARBOARD)
            features[base_idx + 6] = float(block.is_p_s_pair())
    
            # 조립 타입
            features[base_idx + 7] = float(block.assembly_type == AssemblyType.LINE)
            features[base_idx + 8] = float(block.assembly_type == AssemblyType.FIXED)
    
            # 물리적 특성
            features[base_idx + 9] = block.width / 25.0
            features[base_idx + 10] = block.longi_count / 50.0
            features[base_idx + 11] = float(block.width > 21.0)  # P7#2
            features[base_idx + 12] = float(block.longi_count >= 30)  # P7#10
    
            # 시간 제약
            features[base_idx + 13] = float(block.needs_afternoon_start())
            features[base_idx + 14] = float(block.material_ready)
    
        return features
