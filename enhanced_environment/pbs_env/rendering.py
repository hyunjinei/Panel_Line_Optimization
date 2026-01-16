"""
Rendering and info helpers
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

class RenderingMixin:
    def get_info(self) -> Dict[str, Any]:
        """환경 정보 반환"""
        progress_info = self.sequence_state.get_progress_info() if self.sequence_state else {}
    
        return {
            "total_blocks": len(self.original_blocks),
            "current_step": self.current_step,
            "current_time": self.current_time,
            "makespan": self.makespan,
            "is_done": self.is_done,
            "violations": len(self.violation_history),
            "sequence_info": progress_info,
            "phase": self.sequence_state.current_phase.value if self.sequence_state else "UNKNOWN",
            "completion_tables": {
                name: table.tolist() for name, table in self.completion_tables.items()
            }
        }
    

    def render(self, mode: str = "human"):
        """환경 시각화"""
        if mode == "human":
            print(f"\n🎯 PFSP 환경 상태:")
            print(f"Step: {self.current_step}")
            print(f"Phase: {self.sequence_state.current_phase.value}")
            print(f"Time: {self.current_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
            if self.sequence_state.sequence_decided:
                progress = self.sequence_state.get_progress_info()
                print(f"Progress: {progress['progress_percentage']:.1f}%")
                print(f"Current Block: {progress['current_block_id']}")
                print(f"Current Process: {progress['current_process']}")
    
            print(f"Makespan: {self.makespan:.1f}s")
            print(f"Violations: {len(self.violation_history)}")
    
            if self.enable_visualization:
                self._render_gantt_chart()
    

    def _render_gantt_chart(self):
        """간소화된 간트 차트 출력"""
        if not self.completed_steps:
            return
    
        print(f"\n📊 처리 완료된 단계들:")
        print(f"{'블록':<8} {'공정':<8} {'베이':<8} {'시작':<12} {'완료':<12} {'시간(분)':<10}")
        print("-" * 70)
    
        for step in self.completed_steps[-10:]:  # 최근 10개만 표시
            duration_minutes = step.processing_time / 60
            print(f"{step.block_id:<8} {step.process_num:<8} {step.bay_type.value:<8} "
                  f"{step.start_time.strftime('%H:%M:%S'):<12} {step.end_time.strftime('%H:%M:%S'):<12} "
                  f"{duration_minutes:<10.1f}")
    

    def close(self):
        """환경 정리"""
        self.logger.info("PFSP 환경 종료")
        if hasattr(self, 'violation_history'):
            self.violation_history.clear()
        if hasattr(self, 'action_history'):
            self.action_history.clear()
        if hasattr(self, 'completed_steps'):
            self.completed_steps.clear()
    
    # 분리된 모듈 함수 호출
