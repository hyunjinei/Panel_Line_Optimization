"""
Validation and reward helpers
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

class ValidationRewardMixin:
    def _validate_all_constraints_realtime(self, block: EnhancedBlock, assigned_bay: BayType, current_time: datetime) -> List[ConstraintViolation]:
        return self.validator.validate_all_constraints_realtime(block, assigned_bay, current_time)
    

    def _validate_all_constraints_realtime_action(
        self,
        block: EnhancedBlock,
        assigned_bay: BayType,
        current_time: datetime,
        actual_machine_2_start_time: datetime = None,
        capacity_time_override: datetime = None,
        current_in_history: bool = False,
    ) -> List[ConstraintViolation]:
        return self.validator.validate_all_constraints_realtime_action(
            block,
            assigned_bay,
            current_time,
            actual_machine_2_start_time,
            capacity_time_override,
            current_in_history
        )



    def _validate_and_commit_constraints_action(
        self,
        block: EnhancedBlock,
        assigned_bay: BayType,
        current_time: datetime,
        *,
        actual_machine_2_start_time: datetime = None,
        capacity_time_override: datetime = None,
        processing_time_seconds: float = 0.0,
        step_start_time: datetime = None,
        step_end_time: datetime = None,
        current_in_history: bool = False,
    ) -> List[ConstraintViolation]:
        """[AGENT-ADD] canonical runtime 검사 + 상태 반영을 한 곳에서 수행한다."""
        violations = self._validate_all_constraints_realtime_action(
            block,
            assigned_bay,
            current_time,
            actual_machine_2_start_time=actual_machine_2_start_time,
            capacity_time_override=capacity_time_override,
            current_in_history=current_in_history,
        )
        violations.extend(self.bay_tracker.assign_bay(block, assigned_bay, processing_time_seconds))
        violations.extend(self.ps_manager.process_block(block, assigned_bay, current_time))
        if step_start_time is not None and step_end_time is not None:
            self.completed_steps.append(
                ProcessStep(
                    block_id=block.block_id,
                    process_num=1,
                    bay_type=assigned_bay,
                    start_time=step_start_time,
                    end_time=step_end_time,
                    processing_time=processing_time_seconds,
                    completion_time=processing_time_seconds,
                )
            )
        return violations

    def _validate_saw_constraints_realtime(self, block: EnhancedBlock, current_time: datetime,
                                          actual_machine_2_start_time: datetime = None) -> List[ConstraintViolation]:
        return self.validator.validate_saw_constraints_realtime(block, current_time, actual_machine_2_start_time)
    
    # ... 다른 validate 함수들도 마찬가지로 validator 객체를 통해 호출 (코드가 너무 길어져 생략) ...
    

    def _calculate_reward(self, step_info: Dict, violations: List[ConstraintViolation]) -> float:
        """
        보상 계산 (제약조건 위반 반영)
    
        Args:
            step_info: 단계 정보
            violations: 제약조건 위반 리스트
    
        Returns:
            계산된 보상
        """
        base_reward = 5.0  # 기본 보상
    
        # 제약조건 위반 페널티
        violation_penalty = 0.0
        for violation in violations:
            if violation.severity == "ERROR":
                violation_penalty += 5.0
            elif violation.severity == "WARNING":
                violation_penalty += 2.0
            elif violation.severity == "INFO":
                violation_penalty += 0.0  # 정보성은 페널티 없음
    
        # 환경 자동 베이 할당 보너스
        if step_info.get('auto_bay_assignment', False):
            base_reward += 2.0
    
        # 실시간 검증 완료 보너스
        if step_info.get('realtime_validation', False):
            base_reward += 1.0
    
        # 최종 완료 보너스
        if self.is_done:
            base_reward += 10.0
    
        final_reward = base_reward - violation_penalty
        return max(final_reward, -10.0)  # 최소 보상 제한
    

    def _check_completion(self):
        """완료 조건 확인"""
        # PFSP 완료 조건: 모든 공정이 완료됨
        if self.constraint_checker.is_completed():
            self.is_done = True
            # Makespan 계산
            max_completion_time = 0
            for table in self.completion_tables.values():
                if table.size > 0:
                    max_completion_time = max(max_completion_time, np.max(table))
    
            self.makespan = max_completion_time
            self.logger.info(f"PFSP 완료! 총 {self.current_step} 스텝, Makespan: {self.makespan:.1f}초")
            return
    
        # 최대 스텝 초과
        if self.current_step >= self.max_steps:
            self.is_done = True
            self.logger.warning(f"최대 스텝({self.max_steps}) 초과로 종료")
            return
    
        # 순서 결정 단계에서 유효한 순서가 없는 경우
        if self.sequence_state.current_phase == ProcessPhase.SEQUENCE_DECISION:
            valid_sequences, _ = self.constraint_checker.get_sequence_mask(
                self.original_blocks, self.current_time
            )
    
            if not valid_sequences:
                self.is_done = True
                self.logger.error("유효한 순서가 없어 진행 불가능")
                return
