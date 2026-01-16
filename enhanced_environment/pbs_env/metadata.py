"""
Environment metadata helpers
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

class MetadataMixin:
    def _process_metadata(self):
        """메타데이터를 내부 구조로 변환 및 저장"""
        self.daily_structure = self.metadata.get('daily_structure', {})
        self.ps_pair_complete_info = self.metadata.get('ps_pair_complete_info', {})
        self.subassembly_complete_info = self.metadata.get('subassembly_complete_info', {})
        self.constraint_application_map = self.metadata.get('constraint_application_map', {})
        self.bay_assignment_strategy = self.metadata.get('bay_assignment_strategy', {})
        self.fab_interval_tracking = self.metadata.get('fab_interval_tracking', {})
        self.assembly_mixing_control = self.metadata.get('assembly_mixing_control', {})
        self.cross_seam_mixing_control = self.metadata.get('cross_seam_mixing_control', {})
        self.block_10_special_rule = self.metadata.get('block_10_special_rule', {})
        self.constraint_conflicts = self.metadata.get('constraint_conflicts', {})
        self.dynamic_state_tracking = self.metadata.get('dynamic_state_tracking', {})
    
        # Logger가 초기화되기 전이므로 print 사용
        # print(f"메타데이터 처리 완료: 일별구조 {len(self.daily_structure)}일, P/S쌍 {len(self.ps_pair_complete_info)//2}쌍, 별판 {len(self.subassembly_complete_info)}개")
    
    # ======================================================================
    # [AGENT-ADD] Debug flag resolver (env에서도 action_masking과 동일 기준 사용)
    # ======================================================================

    def _initialize_constraint_managers_with_metadata(self):
        """제약조건 매니저들을 메타데이터와 함께 초기화"""
    
        # ✅ 용량 하이퍼파라미터를 constraint_config에서 가져오기
        capacity_hyperparams = self.constraint_config.get_capacity_hyperparams()
    
        # print(f"   📊 용량 하이퍼파라미터 (from constraint_config):")
        # print(f"      평일: {capacity_hyperparams['weekday_seam_limit']}심, 주말: {capacity_hyperparams['weekend_seam_limit']}심")
        # if capacity_hyperparams['holiday_eve_seam_reduction_enabled']:
            # print(f"      명절전날: {capacity_hyperparams['holiday_eve_seam_limit']}심 (심수 제한)")
        # else:
            # print(f"      명절전날: 시간 제한 방식")
        # if capacity_hyperparams['hot_season_reduction_type'] == 'absolute':
            # print(f"      혹서기: -{capacity_hyperparams['hot_season_seam_reduction']}심 (절대값 감소)")
        # else:
            # print(f"      혹서기: -{capacity_hyperparams['hot_season_percentage_reduction']}% (백분율 감소)")
    
        # CapacityTracker: 일별 구조와 FAB 간격 정보 + 제약조건 설정 + 하이퍼파라미터 전달
        self.capacity_tracker = CapacityTracker(
            daily_structure=self.daily_structure,
            fab_interval_tracking=self.fab_interval_tracking,
            constraint_config=self.constraint_config,
            capacity_hyperparams=capacity_hyperparams  # ✅ constraint_config에서 가져온 하이퍼파라미터
        )
    
        # PSBlockManager: P/S 완전 정보 전달
        self.ps_manager = PSBlockManager(
            ps_complete_info=self.ps_pair_complete_info,
            subassembly_info=self.subassembly_complete_info
        )
    
        # BayStateTracker: 베이 전략과 특별 규칙들 전달
        self.bay_tracker = BayStateTracker(
            subassembly_info=self.subassembly_complete_info,
            ps_pair_info=self.ps_pair_complete_info,
            bay_strategy=self.bay_assignment_strategy,
            block_10_rule=self.block_10_special_rule,
            dynamic_tracking=self.dynamic_state_tracking,
            constraint_config=self.constraint_config  # ✅ constraint_config 전달
        )
    
        # CalendarManager: 일별 구조 정보 전달
        self.calendar_manager = CalendarManager(
            daily_structure=self.daily_structure,
            calendar_overrides=self.calendar_overrides
        )
    

    def _is_subassembly(self, block_id: int) -> bool:
        """블록이 별판인지 확인 (메타데이터 기반)"""
        return block_id in self.subassembly_complete_info
    

    def _get_original_subassembly_blocks(self, block_id: int) -> List[int]:
        """별판의 원본 블록 ID들 반환"""
        if block_id in self.subassembly_complete_info:
            return self.subassembly_complete_info[block_id]['original_block_ids']
        return [block_id]
    

    def _is_ps_pair_from_metadata(self, block_id: int) -> bool:
        """블록이 P/S 쌍인지 메타데이터에서 확인"""
        return block_id in self.ps_pair_complete_info
    

    def get_expanded_schedule_results(self) -> List[Dict]:
        """
        메타데이터를 활용하여 별판 자동 확장된 스케줄 결과 반환
    
        Returns:
            별판이 확장된 완전한 스케줄 결과 리스트
        """
        expanded_results = []
    
        for step in self.completed_steps:
            block_id = step.block_id
    
            if self._is_subassembly(block_id):
                # 별판을 원본 블록들로 확장
                subassembly_data = self.subassembly_complete_info[block_id]
                original_block_ids = subassembly_data['original_block_ids']
    
                for orig_block_id in original_block_ids:
                    expanded_step = {
                        'block_id': orig_block_id,
                        'sequence': subassembly_data['sequence_number'],
                        'process_num': step.process_num,
                        'bay_type': step.bay_type.value,
                        'start_time': step.start_time.strftime('%Y-%m-%d %H:%M:%S'),
                        'end_time': step.end_time.strftime('%Y-%m-%d %H:%M:%S'),
                        'processing_time': step.processing_time,
                        'completion_time': step.completion_time,
                        'is_subassembly_expanded': True,
                        'unified_block_id': block_id,
                        'subassembly_group_size': len(original_block_ids)
                    }
                    expanded_results.append(expanded_step)
            else:
                # 일반 블록은 그대로 추가
                regular_step = {
                    'block_id': block_id,
                    'sequence': getattr(self.blocks_dict[block_id], 'sequence_number', 0),
                    'process_num': step.process_num,
                    'bay_type': step.bay_type.value,
                    'start_time': step.start_time.strftime('%Y-%m-%d %H:%M:%S'),
                    'end_time': step.end_time.strftime('%Y-%m-%d %H:%M:%S'),
                    'processing_time': step.processing_time,
                    'completion_time': step.completion_time,
                    'is_subassembly_expanded': False,
                    'unified_block_id': block_id,
                    'subassembly_group_size': 1
                }
                expanded_results.append(regular_step)
    
        return expanded_results
    

    def generate_final_schedule_with_metadata(self, sequence_results: List[Dict]) -> List[Dict]:
        """
        스케줄링 결과를 메타데이터 기반으로 최종 형태로 변환
    
        Args:
            sequence_results: 기본 스케줄링 결과
    
        Returns:
            별판 확장 및 메타데이터 정보가 포함된 최종 결과
        """
        final_results = []
    
        for result in sequence_results:
            block_id = result['block_id']
    
            if self._is_subassembly(block_id):
                # 별판 확장 처리
                subassembly_data = self.subassembly_complete_info[block_id]
                original_block_ids = subassembly_data['original_block_ids']
                original_block_names = subassembly_data['original_block_names']
    
                for i, orig_block_id in enumerate(original_block_ids):
                    expanded_result = result.copy()
                    expanded_result.update({
                        'block_id': orig_block_id,
                        'block_name': original_block_names[i],
                        'sequence': subassembly_data['sequence_number'],  # 동일 순번
                        'is_subassembly': True,
                        'subassembly_unified_id': block_id,
                        'subassembly_group_size': len(original_block_ids),
                        'subassembly_processing_method': 'UNIFIED_MAKESPAN',
                        'subassembly_result_method': 'SEPARATE_DISPLAY'
                    })
    
                    # P/S 쌍 정보 추가
                    if orig_block_id in self.ps_pair_complete_info:
                        ps_info = self.ps_pair_complete_info[orig_block_id]
                        expanded_result.update({
                            'is_ps_pair': True,
                            'ps_pair_id': ps_info['port_block_id'] if orig_block_id == ps_info['starboard_block_id'] else ps_info['starboard_block_id'],
                            'ps_role': 'PORT' if orig_block_id == ps_info['port_block_id'] else 'STARBOARD',
                            'ps_require_same_bay': ps_info['require_same_bay'],
                            'ps_require_continuous': ps_info['require_continuous_line'] or ps_info['require_continuous_fixed']
                        })
    
                    final_results.append(expanded_result)
            else:
                # 일반 블록 처리
                enhanced_result = result.copy()
    
                # P/S 쌍 정보 추가
                if block_id in self.ps_pair_complete_info:
                    ps_info = self.ps_pair_complete_info[block_id]
                    enhanced_result.update({
                        'is_ps_pair': True,
                        'ps_pair_id': ps_info['port_block_id'] if block_id == ps_info['starboard_block_id'] else ps_info['starboard_block_id'],
                        'ps_role': 'PORT' if block_id == ps_info['port_block_id'] else 'STARBOARD',
                        'ps_require_same_bay': ps_info['require_same_bay'],
                        'ps_require_continuous': ps_info['require_continuous_line'] or ps_info['require_continuous_fixed']
                    })
                else:
                    enhanced_result.update({
                        'is_ps_pair': False,
                        'ps_pair_id': None,
                        'ps_role': 'NONE',
                        'ps_require_same_bay': False,
                        'ps_require_continuous': False
                    })
    
                # 제약조건 프로파일 정보 추가
                bay_strategy = self.bay_assignment_strategy.get(block_id, {})
                enhanced_result.update({
                    'constraint_profile': bay_strategy.get('physical_constraints', []),
                    'constraint_priority': bay_strategy.get('priority_order', []),
                    'is_subassembly': False,
                    'subassembly_unified_id': block_id,
                    'subassembly_group_size': 1
                })
    
                final_results.append(enhanced_result)
    
        return final_results
    

    def get_metadata_summary(self) -> Dict[str, Any]:
        """메타데이터 요약 정보 반환"""
        summary = {
            'daily_structure_count': len(self.daily_structure),
            'ps_pair_count': len(self.ps_pair_complete_info) // 2,  # 양방향이므로 2로 나눔
            'subassembly_count': len(self.subassembly_complete_info),
            'total_blocks': len(self.original_blocks),
            'constraint_application_phases': list(self.constraint_application_map.keys()),
            'bay_strategy_blocks': len(self.bay_assignment_strategy),
            'fab_condition_blocks': len(self.fab_interval_tracking.get('condition_blocks', [])),
            'cross_seam_blocks': len(self.cross_seam_mixing_control.get('cross_seam_blocks', [])),
            'block_10_special_blocks': len(self.block_10_special_rule.get('block_10_ids', [])),
        }
    
        # 날짜별 상세 정보
        daily_details = {}
        for date_key, daily_info in self.daily_structure.items():
            daily_details[date_key] = {
                'total_blocks': daily_info['total_unified_blocks'],
                'subassembly_count': daily_info['subassembly_count'],
                'ps_pair_count': daily_info['ps_pair_count'],
                'seam_capacity': daily_info['seam_capacity'],
                'is_weekend': daily_info['is_weekend']
            }
    
        summary['daily_details'] = daily_details
    
        return summary
    

    def _register_ps_pairs(self):
        """P/S 블록 쌍들을 PSBlockManager에 등록"""
        port_blocks = {}
        starboard_blocks = {}
    
        # Port/Starboard 블록 분류
        for block in self.original_blocks:
            if block.port_starboard == PortStarboard.PORT:
                port_blocks[block.pair_block_id] = block
            elif block.port_starboard == PortStarboard.STARBOARD:
                starboard_blocks[block.block_id] = block
    
        # P/S 쌍 등록
        for port_block in port_blocks.values():
            if port_block.pair_block_id in starboard_blocks:
                starboard_block = starboard_blocks[port_block.pair_block_id]
                self.ps_manager.register_ps_pair(port_block, starboard_block)
                self.logger.debug(f"P/S 쌍 등록: P{port_block.block_id} ↔ S{starboard_block.block_id}")
                # print(f"🔗 P/S 쌍 등록: P{port_block.block_id}({getattr(port_block, 'block_name', 'Unknown')}) ↔ S{starboard_block.block_id}({getattr(starboard_block, 'block_name', 'Unknown')})")
