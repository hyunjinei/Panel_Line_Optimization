"""
Logging utilities
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

class Logger:
    """로깅 유틸리티"""
    
    @staticmethod
    def setup_logger(name: str, 
                    level: int = logging.INFO,
                    log_file: Optional[str] = None,
                    format_str: Optional[str] = None) -> logging.Logger:
        """로거 설정"""
        logger = logging.getLogger(name)
        logger.setLevel(level)
        
        # 기존 핸들러 제거
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
        
        # 포맷터 설정
        if format_str is None:
            format_str = '[%(asctime)s] %(name)s - %(levelname)s - %(message)s'
        
        formatter = logging.Formatter(format_str)
        
        # 콘솔 핸들러
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)
        
        # 파일 핸들러 (옵션)
        if log_file:
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setLevel(level)
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        
        return logger
    
    @staticmethod
    def log_constraint_violation(logger: logging.Logger, 
                               constraint_id: str,
                               message: str,
                               block_id: Optional[int] = None,
                               severity: str = "WARNING"):
        """제약조건 위반 로그"""
        if block_id:
            log_msg = f"[{constraint_id}] 블록 {block_id}: {message}"
        else:
            log_msg = f"[{constraint_id}] {message}"
        
        if severity == "ERROR":
            logger.error(log_msg)
        elif severity == "WARNING":
            logger.warning(log_msg)
        else:
            logger.info(log_msg)
    
    @staticmethod
    def log_performance_metrics(logger: logging.Logger, 
                              metrics: Dict[str, float],
                              step: int):
        """성능 메트릭 로그"""
        metrics_str = ", ".join([f"{k}={v:.3f}" for k, v in metrics.items()])
        logger.info(f"Step {step} 성능: {metrics_str}")
