# [AGENT-ADD] Split from masking/core.py for readability.

import logging
from datetime import date
from typing import List, Tuple, Optional
from enhanced_environment.models import EnhancedBlock, BayType, PortStarboard  # [AGENT-EDIT] 타입 힌트용 명시 임포트
from enhanced_environment.bay.assigner import preview_assign_bay

class RoutingMixin:
    def _check_consecutive_3bay_prevention(self, candidate_block: EnhancedBlock, selected_blocks: List[int]) -> Tuple[bool, str]:
        """
        연속 3판 B베이 방지 제약조건 확인
    
        Args:
            candidate_block: 후보 블록
            selected_blocks: 이미 선택된 블록 ID 리스트
    
        Returns:
            (선택 가능 여부, 불가능한 경우 사유)
        """
        # [AGENT-EDIT] config 기반 동적 토글 (enabled_constraints 반영)
        try:
            if not self.constraint_config.is_constraint_enabled("CONSECUTIVE_3BAY"):
                return True, "연속 3베이 제약 비활성화"
        except Exception:
            if not getattr(self.constraint_config, "enable_consecutive_3bay_prevention", True):
                return True, "연속 3베이 제약 비활성화"
    
        # 최근 베이 기록 기반으로 실제 연속도를 확인
        recent_b_count = 0
        try:
            if hasattr(self, "bay_tracker"):
                recent_b_count = self.bay_tracker.get_consecutive_count(BayType.BAY_36B)
        except Exception:
            recent_b_count = 0
    
        # 베이 미지정 상태에서 예측이 필요할 때 preview_assign_bay 사용 (side-effect 없음)
        def _predict_bay(block: EnhancedBlock) -> Optional[BayType]:
            logger = getattr(self, "logger", None) or logging.getLogger("ConstraintCheckerPreview")
            try:
                return preview_assign_bay(
                    block,
                    self.constraint_config,
                    self.bay_tracker,
                    self.ps_manager,
                    getattr(self, "blocks_dict", {}),
                    logger,
                    return_analysis=False,
                )
            except Exception:
                return None
    
        predicted_bay = _predict_bay(candidate_block)
    
        # 예측 실패 시에는 보수적으로 통과시키되, 이미 2연속 B면 차단
        if predicted_bay is None:
            if recent_b_count >= 2:
                return False, "최근 B베이 2연속 상태에서 후보 베이 예측 불가 → 안전 차단"
            return True, "베이 예측 불가 - 통과"
    
        # 실제 연속 카운트가 2 이상이고 이번에도 B이면 차단
        if recent_b_count >= 2 and predicted_bay == BayType.BAY_36B:
            return False, f"연속 B베이 3판 방지: 최근 {recent_b_count}판 B, 후보도 B({predicted_bay.value})"
    
        # assignment_history가 있는 경우, 즉시 이전 두 건이 모두 B인 상황도 확인
        try:
            history = getattr(self.bay_tracker, "assignment_history", [])
            last_two = [h[1] for h in history[-2:]]
            if len(last_two) == 2 and all(b == BayType.BAY_36B for b in last_two) and predicted_bay == BayType.BAY_36B:
                return False, "직전 2판 B베이, 후보도 B베이 → 3연속 방지"
        except Exception:
            pass
    
        return True, f"B연속 {recent_b_count}판 후 후보 {predicted_bay.value}"
    

    def _check_curved_plate_spacing(self, block: EnhancedBlock, selected_blocks: List[int]) -> Tuple[bool, str]:
        # [AGENT-EDIT] Routing 곡판 간격 개별 토글
        if not self.constraint_config.is_constraint_enabled("ROUTING_CURVED_SPACING"):
            return True, "ROUTING_CURVED_SPACING 비활성화"
        return self._check_spacing_rule(
            block,
            selected_blocks,
            lambda b: getattr(b, 'has_curved_plate', False),
            min_gap=3,
            rule_name="곡판 블록 간 3판 간격"
        )
    

    def _check_high_seam_spacing(self, block: EnhancedBlock, selected_blocks: List[int]) -> Tuple[bool, str]:
        # [AGENT-EDIT] Routing 고심수 간격 개별 토글
        if not self.constraint_config.is_constraint_enabled("ROUTING_HIGH_SEAM_SPACING"):
            return True, "ROUTING_HIGH_SEAM_SPACING 비활성화"
        return self._check_spacing_rule(
            block,
            selected_blocks,
            lambda b: getattr(b, 'is_high_seam_block', False),
            min_gap=3,
            rule_name="고심수(6+) 블록 간 3판 간격"
        )
    

    def _check_c_seam_spacing(self, block: EnhancedBlock, selected_blocks: List[int]) -> Tuple[bool, str]:
        # [AGENT-EDIT] config 기반 동적 토글 (enabled_constraints 반영)
        try:
            if not self.constraint_config.is_constraint_enabled("ROUTING_C_SEAM_SPACING"):
                return True, "C/Seam 간격 제약 비활성화"
        except Exception:
            if not getattr(self.constraint_config, 'enable_c_seam_spacing', True):
                return True, "C/Seam 간격 제약 비활성화"
    
        if getattr(block, 'c_seam_count', 0) <= 0:
            return True, "C/Seam 제약 해당 없음"
    
        if not selected_blocks:
            return True, "C/Seam 제약: 선행 블록 없음"
    
        blocks_dict = getattr(self, 'blocks_dict', {})
        last_block = blocks_dict.get(selected_blocks[-1])
        if not last_block:
            return True, "C/Seam 제약: 선행 블록 정보 없음"
    
        if getattr(last_block, 'c_seam_count', 0) <= 0:
            return True, "C/Seam 제약: 직전 블록 일반"
    
        # PS 우선순위: S 블록이 직전 P 블록과 짝인 경우 허용
        if (block.port_starboard == PortStarboard.STARBOARD and
                block.pair_block_id == selected_blocks[-1]):
            return True, "C/Seam 제약 완화: P/S 연속 처리 우선"
    
        return False, "C/Seam 블록 연속 배치 불가"
    

    def _check_spacing_rule(
        self,
        block: EnhancedBlock,
        selected_blocks: List[int],
        predicate,
        min_gap: int,
        rule_name: str
    ) -> Tuple[bool, str]:
        if not selected_blocks:
            return True, f"{rule_name}: 선행 블록 없음"
    
        if not predicate(block):
            return True, f"{rule_name}: 현재 블록 해당 없음"
    
        blocks_dict = getattr(self, 'blocks_dict', {})
        for offset, prev_block_id in enumerate(reversed(selected_blocks), start=1):
            prev_block = blocks_dict.get(prev_block_id)
            if prev_block is None:
                continue
            if predicate(prev_block):
                if offset < min_gap:
                    return (
                        False,
                        f"{rule_name} 위반: 직전 동일 특성 블록과 {offset}판 간격 (최소 {min_gap}판 간격 필요)"
                    )
                return (
                    True,
                    f"{rule_name}: 직전 동일 특성 블록과 {offset}판 간격"
                )
    
        return True, f"{rule_name}: 선행 동일 특성 블록 없음"
    

    def _check_workshop_order_constraint(
        self,
        block: EnhancedBlock,
        candidate_blocks: List[EnhancedBlock],
        selected_blocks: List[int]
    ) -> Tuple[bool, str]:
        # [AGENT-EDIT] Routing 작업장 순서 개별 토글
        if not self.constraint_config.is_constraint_enabled("ROUTING_WORKSHOP_ORDER"):
            return True, "ROUTING_WORKSHOP_ORDER 비활성화"
        from enhanced_environment.common.utils_core import get_line_group_and_workshop_code
    
        line_group, workshop_code = get_line_group_and_workshop_code(block)
        workshop_key = workshop_code or line_group
        block_date = getattr(block, 'assembly_start_date', None)
        if not workshop_key or not block_date:
            return True, "후공정 착수 순서 제약 해당 없음"
    
        # 조립 착수일이 빠른 동일 작업장 블록이 아직 남아있는지 확인
        # [AGENT-EDIT] 후보 집합 우선 사용하여 외부 데이터로 인한 오탐 방지
        remaining_source = candidate_blocks or []
        if not remaining_source and getattr(self, 'all_blocks', None):
            remaining_source = self.all_blocks
        block_date_value = block_date.date() if hasattr(block_date, "date") else block_date
        for other_block in remaining_source:
            if other_block.block_id == block.block_id:
                continue
            if other_block.block_id in selected_blocks:
                continue
            other_line_group, other_code = get_line_group_and_workshop_code(other_block)
            other_key = other_code or other_line_group
            if not other_key or other_key != workshop_key:
                continue
            other_date = getattr(other_block, 'assembly_start_date', None)
            if not other_date:
                continue
            other_date_value = other_date.date() if hasattr(other_date, "date") else other_date
            if other_date_value < block_date_value:
                return (
                    False,
                    f"후공정 착수 순서 위반: 동일 작업장({workshop_key})의 {other_block.block_id}가 먼저 착수해야 함"
                )
    
        return True, f"후공정 착수 순서 준수: 작업장 {workshop_key}"
