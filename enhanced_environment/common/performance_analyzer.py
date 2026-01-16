"""
Performance analysis utilities
"""

# [AGENT-ADD] Split from common/utils_core.py for readability.

import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional, Any, Set
import logging
import re
import sys  # 🔧 sys import 추가
from enhanced_environment.common.settings import VERBOSE_CONVERSION, DEBUG_MODE, QUIET_MODE

class PerformanceAnalyzer:
    """성능 분석 유틸리티"""
    
    @staticmethod
    def analyze_makespan(action_history: List[Dict]) -> Dict[str, float]:
        """makespan 분석"""
        if not action_history:
            return {}
        
        total_time = sum(action['action_result'].processing_time for action in action_history)
        max_time = max(action['action_result'].processing_time for action in action_history)
        min_time = min(action['action_result'].processing_time for action in action_history)
        avg_time = total_time / len(action_history)
        
        return {
            'total_makespan': total_time,
            'average_processing_time': avg_time,
            'max_processing_time': max_time,
            'min_processing_time': min_time,
            'time_variance': np.var([action['action_result'].processing_time for action in action_history])
        }
    
    @staticmethod
    def analyze_constraint_violations(violation_history: List) -> Dict[str, Any]:
        """제약조건 위반 분석"""
        if not violation_history:
            return {'total_violations': 0, 'violation_by_type': {}}
        
        violation_by_type = {}
        for violation in violation_history:
            constraint_id = violation.constraint_id
            if constraint_id not in violation_by_type:
                violation_by_type[constraint_id] = 0
            violation_by_type[constraint_id] += 1
        
        return {
            'total_violations': len(violation_history),
            'violation_by_type': violation_by_type,
            'most_violated_constraint': max(violation_by_type.items(), key=lambda x: x[1])[0] if violation_by_type else None
        }
    
    @staticmethod
    def generate_performance_report(env_info: Dict[str, Any]) -> str:
        """성능 리포트 생성"""
        report = []
        report.append("=== Enhanced PBS 성능 리포트 ===\n")
        
        # 기본 정보
        report.append(f"총 블록 수: {env_info.get('total_blocks', 0)}")
        report.append(f"완료 블록 수: {env_info.get('completed_blocks', 0)}")
        report.append(f"진행률: {env_info.get('performance_metrics', {}).get('progress', 0):.1%}")
        report.append(f"총 스텝 수: {env_info.get('current_step', 0)}")
        
        # 성능 메트릭
        metrics = env_info.get('performance_metrics', {})
        if metrics:
            report.append(f"\n=== 성능 메트릭 ===")
            report.append(f"제약조건 위반률: {metrics.get('violation_rate', 0):.3f}")
            report.append(f"베이 부하 균형: {metrics.get('load_balance_score', 0):.3f}")
            report.append(f"용량 활용률: {metrics.get('capacity_utilization', 0):.3f}")
        
        # 용량 상태
        capacity = env_info.get('capacity_status', {})
        if capacity:
            report.append(f"\n=== 용량 상태 ===")
            report.append(f"심수 사용률: {capacity.get('utilization_seam', 0):.1%}")
            report.append(f"블록 사용률: {capacity.get('utilization_block', 0):.1%}")
        
        # 베이 상태
        bay_status = env_info.get('bay_status', {})
        if bay_status:
            report.append(f"\n=== 베이 상태 ===")
            report.append(f"35A 베이: {bay_status.get('bay_35a_blocks', 0)}개 블록, {bay_status.get('bay_35a_worktime', 0):.1f}초")
            report.append(f"36B 베이: {bay_status.get('bay_36b_blocks', 0)}개 블록, {bay_status.get('bay_36b_worktime', 0):.1f}초")
            report.append(f"부하 균형: {bay_status.get('load_balance_score', 0):.3f}")
        
        return "\n".join(report)
