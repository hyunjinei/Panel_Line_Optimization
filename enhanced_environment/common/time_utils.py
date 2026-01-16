"""
Time utilities
"""

# [AGENT-ADD] Split from common/utils_core.py for readability.

import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date, time
from typing import List, Dict, Tuple, Optional, Any, Set
import logging
import re
import sys  # 🔧 sys import 추가
from enhanced_environment.common.settings import VERBOSE_CONVERSION, DEBUG_MODE, QUIET_MODE

class TimeUtils:
    """시간 관련 유틸리티 함수들"""
    
    @staticmethod
    def is_weekend(date: datetime) -> bool:
        """주말인지 확인 (토요일: 5, 일요일: 6)"""
        return date.weekday() >= 5
    
    @staticmethod
    def is_work_hour(time: datetime) -> bool:
        """작업 시간인지 확인 (오전 8시 ~ 오후 10시)"""
        hour = time.hour
        return 8 <= hour <= 22
    
    @staticmethod
    def is_afternoon(time: datetime) -> bool:
        """오후인지 확인 (오후 3시 이후)"""
        return time.hour >= 15
    
    @staticmethod
    def get_work_hours_between(start_time: datetime, end_time: datetime) -> float:
        """두 시간 사이의 작업 시간 계산 (시간 단위)"""
        if start_time >= end_time:
            return 0.0
        
        total_hours = 0.0
        current_time = start_time
        
        while current_time < end_time:
            # 다음 시간 (1시간 단위로 체크)
            next_hour = current_time.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            next_hour = min(next_hour, end_time)
            
            # 작업시간이면 추가
            if TimeUtils.is_work_hour(current_time) and not TimeUtils.is_weekend(current_time):
                hours_diff = (next_hour - current_time).total_seconds() / 3600
                total_hours += hours_diff
            
            current_time = next_hour
        
        return total_hours
    
    @staticmethod
    def add_work_hours(start_time: datetime, work_hours: float) -> datetime:
        """작업 시간만큼 시간 추가"""
        current_time = start_time
        remaining_hours = work_hours
        
        while remaining_hours > 0:
            if TimeUtils.is_work_hour(current_time) and not TimeUtils.is_weekend(current_time):
                # 작업 시간 중이면 시간 추가
                add_hours = min(remaining_hours, 1.0)  # 최대 1시간씩
                current_time += timedelta(hours=add_hours)
                remaining_hours -= add_hours
            else:
                # 비작업 시간이면 다음 작업 시간까지 점프
                current_time = TimeUtils._next_work_time(current_time)
        
        return current_time

    # [AGENT-ADD] CalendarManager 반영: 휴무/중지/점심시간을 고려한 작업 시간 계산
    @staticmethod
    def add_work_hours_with_calendar(start_time: datetime, work_hours: float, calendar_manager) -> datetime:
        """CalendarManager 규칙을 고려해 작업 시간을 더한다."""
        current_time = start_time
        remaining_hours = work_hours

        while remaining_hours > 0:
            # 휴무/중지 시간은 스킵 (주말/야간 제한은 적용하지 않음)
            if calendar_manager is not None and calendar_manager.is_closed_time(current_time):
                current_time = calendar_manager.next_open_time(current_time)
                continue

            add_hours = min(remaining_hours, 1.0)
            current_time += timedelta(hours=add_hours)
            remaining_hours -= add_hours

        return current_time
    
    @staticmethod
    def _next_work_time(current_time: datetime) -> datetime:
        """다음 작업 시간 반환"""
        # 주말이면 다음 월요일 8시
        if TimeUtils.is_weekend(current_time):
            days_to_monday = 7 - current_time.weekday()
            next_monday = current_time + timedelta(days=days_to_monday)
            return next_monday.replace(hour=8, minute=0, second=0, microsecond=0)
        
        # 평일이지만 작업 시간이 아니면
        if current_time.hour < 8:
            # 같은 날 8시
            return current_time.replace(hour=8, minute=0, second=0, microsecond=0)
        elif current_time.hour > 22:
            # 다음 날 8시
            next_day = current_time + timedelta(days=1)
            return next_day.replace(hour=8, minute=0, second=0, microsecond=0)
        else:
            # 작업 시간 중이면 그대로
            return current_time
    
    @staticmethod
    def format_duration(seconds: float) -> str:
        """초를 읽기 쉬운 형태로 변환"""
        if seconds < 60:
            return f"{seconds:.1f}초"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.1f}분"
        elif seconds < 86400:
            hours = seconds / 3600
            return f"{hours:.1f}시간"
        else:
            days = seconds / 86400
            return f"{days:.1f}일"
    
    @staticmethod
    def parse_time_string(time_str: str) -> datetime:
        """시간 문자열을 datetime으로 변환"""
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%H:%M:%S",
            "%H:%M"
        ]
        
        for fmt in formats:
            try:
                return datetime.strptime(time_str, fmt)
            except ValueError:
                continue
        
        raise ValueError(f"시간 형식을 파싱할 수 없음: {time_str}")
