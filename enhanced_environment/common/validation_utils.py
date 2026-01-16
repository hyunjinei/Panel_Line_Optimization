"""
Validation utilities
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
# [AGENT-EDIT] 타입 힌트 참조 해소를 위해 데이터 구조 타입을 명시적으로 임포트
from enhanced_environment.models import EnhancedBlock, PortStarboard

class ValidationUtils:
    """검증 유틸리티"""
    
    @staticmethod
    def validate_blocks(blocks: List[EnhancedBlock], start_time: datetime = None) -> Tuple[bool, List[str]]:
        """
        블록 리스트 검증
        
        Args:
            blocks: 검증할 블록 리스트
            start_time: 환경 시작 시간 (None이면 현재 시간 사용)
        """
        errors = []
        
        if not blocks:
            errors.append("블록 리스트가 비어있음")
            return False, errors
        
        # 기준 시간 설정
        reference_time = start_time if start_time else datetime.now()
        
        # 블록 ID 중복 체크
        block_ids = [block.block_id for block in blocks]
        if len(block_ids) != len(set(block_ids)):
            errors.append("중복된 블록 ID 존재")
        
        # 개별 블록 검증
        for i, block in enumerate(blocks):
            block_errors = ValidationUtils._validate_single_block(block, reference_time)
            for error in block_errors:
                errors.append(f"블록 {i} ({block.block_id}): {error}")
        
        # P/S 쌍 검증
        ps_errors = ValidationUtils._validate_ps_pairs(blocks)
        errors.extend(ps_errors)
        
        return len(errors) == 0, errors
    
    @staticmethod
    def _validate_single_block(block: EnhancedBlock, reference_time: datetime) -> List[str]:
        """
        단일 블록 검증
        
        Args:
            block: 검증할 블록
            reference_time: 기준 시간 (환경 시작 시간 또는 현재 시간)
        """
        errors = []
        
        # 처리 시간 검증
        if len(block.processing_times) != 8:
            errors.append("처리 시간이 8개가 아님")
        
        if any(t <= 0 for t in block.processing_times):
            errors.append("0 이하의 처리 시간 존재")
        
        # 물리적 특성 검증
        if block.width <= 0:
            errors.append("블록 폭이 0 이하")
        
        if block.longi_count < 0:
            errors.append("론지 개수가 음수")
        
        if block.seam_count <= 0:
            errors.append("심수가 0 이하")
        
        # 날짜 검증 (기준 시간과 비교)
        if block.max_start_date < reference_time:
            errors.append(f"착수일이 기준시간보다 과거: {block.max_start_date} < {reference_time}")
        
        # P/S 쌍 검증
        if block.is_p_s_pair() and not block.pair_block_id:
            errors.append("P/S 블록인데 쌍 ID가 없음")
        
        return errors
    
    @staticmethod
    def _validate_ps_pairs(blocks: List[EnhancedBlock]) -> List[str]:
        """P/S 쌍 검증"""
        errors = []
        
        port_blocks = {}
        starboard_blocks = {}
        
        # P/S 블록 분류
        for block in blocks:
            if block.port_starboard == PortStarboard.PORT:
                port_blocks[block.block_id] = block
            elif block.port_starboard == PortStarboard.STARBOARD:
                starboard_blocks[block.block_id] = block
        
        # 쌍 매칭 검증
        for port_block in port_blocks.values():
            if port_block.pair_block_id:
                if port_block.pair_block_id not in starboard_blocks:
                    errors.append(f"Port 블록 {port_block.block_id}의 쌍 Starboard 블록 {port_block.pair_block_id}를 찾을 수 없음")
        
        for starboard_block in starboard_blocks.values():
            if starboard_block.pair_block_id:
                if starboard_block.pair_block_id not in port_blocks:
                    errors.append(f"Starboard 블록 {starboard_block.block_id}의 쌍 Port 블록 {starboard_block.pair_block_id}를 찾을 수 없음")
        
        return errors
    
    @staticmethod
    def validate_environment_config(config: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """환경 설정 검증"""
        errors = []
        
        required_keys = ['blocks', 'start_time', 'max_steps']
        for key in required_keys:
            if key not in config:
                errors.append(f"필수 설정 키 누락: {key}")
        
        # 블록 검증
        if 'blocks' in config:
            if not isinstance(config['blocks'], list):
                errors.append("blocks는 리스트여야 함")
            elif len(config['blocks']) == 0:
                errors.append("최소 1개 이상의 블록이 필요")
        
        # 시간 검증
        if 'start_time' in config:
            if not isinstance(config['start_time'], datetime):
                errors.append("start_time은 datetime 객체여야 함")
        
        # 스텝 검증
        if 'max_steps' in config:
            if not isinstance(config['max_steps'], int) or config['max_steps'] <= 0:
                errors.append("max_steps는 양의 정수여야 함")
        
        return len(errors) == 0, errors
