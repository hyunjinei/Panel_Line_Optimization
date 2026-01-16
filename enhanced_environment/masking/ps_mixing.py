# [AGENT-ADD] Split from masking/core.py for readability.

from typing import List, Tuple, Optional
# [AGENT-EDIT] 타입 힌트 참조 해소를 위해 데이터 구조 타입을 명시적으로 임포트
from enhanced_environment.models import (
    EnhancedBlock,
    AssemblyType,
    PortStarboard,
    ConstraintViolation,
)

class PSMixingMixin:
    def _check_assembly_type_constraint(
        self, 
        block: EnhancedBlock, 
        last_assembly_type: AssemblyType, 
        selected_blocks: List[int],
        all_blocks: List[EnhancedBlock],
        relaxed_constraints: Optional[List[str]] = None
    ) -> Tuple[bool, str]:
        """P5#11,12: Assembly Type 혼합 배정 제약 체크"""
        relaxed_constraints = relaxed_constraints or []
        constraint_config = getattr(self, 'constraint_config', None)
        mixing_enabled = True
        line_group_enabled = False
        mixing_use_category = False
        mixing_max_consecutive = 1
        mixing_allow_exhaustion_relief = True
    
        if constraint_config:
            try:
                mixing_enabled = constraint_config.is_constraint_enabled("P5#11")
            except AttributeError:
                mixing_enabled = getattr(constraint_config, 'enable_p5_11_fixed_line_mixing', True)
            try:
                line_group_enabled = constraint_config.is_constraint_enabled("LINE_GROUP_CONSTRAINT")
            except AttributeError:
                line_group_enabled = getattr(constraint_config, 'enable_line_group_constraint', False)
            mixing_use_category = getattr(constraint_config, 'assembly_mixing_use_category', False)
            mixing_max_consecutive = getattr(constraint_config, 'assembly_mixing_max_consecutive', 1)
            mixing_allow_exhaustion_relief = getattr(constraint_config, 'assembly_mixing_allow_exhaustion_relief', True)
        else:
            mixing_enabled = True
            line_group_enabled = False
            mixing_use_category = False
            mixing_max_consecutive = 1
            mixing_allow_exhaustion_relief = True
    
        blocks_lookup = getattr(self, 'blocks_dict', None)
        if not blocks_lookup:
            blocks_lookup = {b.block_id: b for b in all_blocks}
    
        # 라인 작업장 연속 제한: 동일 라인 그룹이 허용 횟수를 넘으면 차단(완화 시 경고만 남김)
        if line_group_enabled and block.assembly_type == AssemblyType.LINE:
            limit = (
                constraint_config.line_group_soft_limit
                if getattr(constraint_config, 'line_group_use_soft_limit', False)
                else getattr(constraint_config, 'line_group_strict_limit', 0)
            )
            if limit > 0:
                line_group = getattr(block, 'line_group', None)
                if not line_group or not str(line_group).startswith('L'):
                    from enhanced_environment.common.utils_core import get_line_group_and_workshop_code
                    line_group, _ = get_line_group_and_workshop_code(block)
                if line_group and str(line_group).startswith('L'):
                    consecutive = 0
                    for prev_id in reversed(selected_blocks):
                        prev_block = blocks_lookup.get(prev_id)
                        if not prev_block or prev_block.assembly_type != AssemblyType.LINE:
                            continue
                        prev_group = getattr(prev_block, 'line_group', None)
                        if not prev_group or not str(prev_group).startswith('L'):
                            from enhanced_environment.common.utils_core import get_line_group_and_workshop_code
                            prev_group, _ = get_line_group_and_workshop_code(prev_block)
                        if prev_group != line_group:
                            break
                        consecutive += 1
                        if consecutive >= limit:
                            violation_msg = (
                                f"P5#11 적용: 라인 작업장 연속 제한 {line_group} {consecutive + 1}판 → 최대 {limit}판"
                            )
                            if "LINE_GROUP_CONSTRAINT" in relaxed_constraints:
                                return True, f"[RELAX] {violation_msg}"
                            return False, violation_msg
    
        if last_assembly_type is None:
            return True, "첫 번째 선택: 모든 assembly_type 허용"
    
        if not mixing_enabled:
            return True, "P5#11 제약조건 비활성화"
    
        remaining_blocks = [b for b in all_blocks if b.block_id not in selected_blocks]
    
        if mixing_use_category:
            limit = max(0, mixing_max_consecutive)
            if limit == 0:
                return True, "P5#11 카테고리 연속 제한 비활성화"
    
            current_category = self._resolve_assembly_category(block)
            if not current_category:
                return True, "P5#11 카테고리 정보 없음"
    
            consecutive = 0
            for prev_id in reversed(selected_blocks):
                prev_block = blocks_lookup.get(prev_id)
                if not prev_block:
                    continue
                prev_category = self._resolve_assembly_category(prev_block)
                if prev_category != current_category:
                    break
                consecutive += 1
    
            if consecutive >= limit:
                if (not mixing_allow_exhaustion_relief) or ("P5#11" not in relaxed_constraints and "P5#12" not in relaxed_constraints):
                    return False, (
                        f"P5#11 적용: {current_category} 카테고리 {consecutive + 1}판 연속 (최대 {limit}판)"
                    )
    
                other_available = any(
                    (self._resolve_assembly_category(rem_block) not in (None, current_category))
                    for rem_block in remaining_blocks
                )
                if other_available and mixing_allow_exhaustion_relief:
                    return False, (
                        f"P5#11 적용: {current_category} 카테고리 {consecutive + 1}판 연속 (최대 {limit}판)"
                    )
                elif other_available:
                    return False, (
                        f"P5#11 적용: {current_category} 카테고리 {consecutive + 1}판 연속 (최대 {limit}판)"
                    )
                else:
                    return True, f"P5#11 완화: 다른 카테고리 없음 ({current_category})"
    
            return True, (
                f"P5#11 적용: {current_category} 카테고리 연속 {consecutive + 1}/{limit}판"
            )
    
        # 기존 line/fixed 강제 교대 규칙
        assembly_counts = {
            'line': len([b for b in remaining_blocks if b.assembly_type == AssemblyType.LINE]),
            'fixed': len([b for b in remaining_blocks if b.assembly_type == AssemblyType.FIXED])
        }
    
        if last_assembly_type == AssemblyType.FIXED:
            if block.assembly_type == AssemblyType.LINE:
                return True, f"P5#11 적용: fixed 다음 → line 선택 (line {assembly_counts['line']}개 가용)"
            elif assembly_counts['line'] > 0:
                return False, f"P5#11 적용: fixed 다음인데 fixed 타입 (line {assembly_counts['line']}개 대기 중)"
            else:
                return True, "P5#11 완화: line 블록 부족으로 fixed 허용"
    
        elif last_assembly_type == AssemblyType.LINE:
            if block.assembly_type == AssemblyType.FIXED:
                return True, f"P5#11 적용: line 다음 → fixed 선택 (fixed {assembly_counts['fixed']}개 가용)"
            elif assembly_counts['fixed'] > 0:
                return False, f"P5#11 적용: line 다음인데 line 타입 (fixed {assembly_counts['fixed']}개 대기 중)"
            else:
                return True, "P5#11 완화: fixed 블록 부족으로 line 허용"
    
        return True, "기타 assembly_type"
    

    def _check_ps_order_constraint(
        self, 
        block: EnhancedBlock, 
        selected_blocks: List[int],
        all_blocks: List[EnhancedBlock]
    ) -> Tuple[bool, str]:
        """P5#3,4: P/S 순서 제약 체크"""
        if not self.constraint_config.is_constraint_enabled("P5#3"):
            return True, "P5#3,4 제약조건 비활성화"
    
        # S 블록인 경우에만 체크
        if block.port_starboard == PortStarboard.STARBOARD and block.pair_block_id:
            # 같은 쌍의 P 블록이 이미 선택되었는지 확인
            pair_p_id = block.pair_block_id
            if pair_p_id in selected_blocks:
                return True, f"P5#3,4 적용: S블록 {block.block_id}의 P블록 {pair_p_id} 이미 선택됨"
            else:
                return False, f"P5#3,4 적용: S블록 {block.block_id}의 P블록 {pair_p_id} 미선택 (P→S 순서 필수)"
    
        return True, "P/S 제약 해당없음 (P블록 또는 일반블록)"
    
    ################################################################################################################################################################################################
    # fix: Routing 전용 우선 제약 (곡판/고심수 간격 & 후공정 착수 순서)
    ################################################################################################################################################################################################

    def _is_ps_priority_override(self, block: EnhancedBlock, selected_blocks: List[int]) -> bool:
        """PS 순서 제약으로 인해 다른 제약보다 우선해야 하는지 확인"""
        if not self.constraint_config.is_constraint_enabled("P5#3"):
            return False
    
        if not block.is_p_s_pair():
            return False
    
        # S 블록이 직전 P 블록과 짝인 경우
        if block.port_starboard == PortStarboard.STARBOARD and selected_blocks:
            last_selected_id = selected_blocks[-1]
            if (self.ps_manager.is_port_block(last_selected_id) and
                    self.ps_manager.get_starboard_for_port(last_selected_id) == block.block_id):
                return True
    
        # PSBlockManager가 다음에 필수로 요구하는 블록인지 확인
        next_required_block_id = self.ps_manager.get_next_required_block()
        return next_required_block_id == block.block_id
    
    ################################################################################################################################################################################################
    # fix 2025_09_05_15_45: 조립 작업장별 착수일 우선순위 키 계산
    ################################################################################################################################################################################################
    ################################################################################################################################################################################################
    # fix 2025_09_05_17_10: 조립 작업장 루트 코드 정규화 (L_11 → L_1)
    ################################################################################################################################################################################################

    def _apply_ps_pair_masking(self, available_blocks: List[EnhancedBlock],
                              selected_blocks: List[int],
                              violations: List[ConstraintViolation],
                              all_blocks: List[EnhancedBlock]) -> List[EnhancedBlock]:
        """
        P5#3: P/S 쌍 마스킹 적용 (PSBlockManager 연동) + P 선택 후 S 강제 선택
    
        핵심 로직:
        1. P가 선택된 후 연속성이 필요한 경우 S를 바로 강제 선택 (최우선)
        2. S 블록이 P 블록보다 먼저 선택 못함 (기존 로직)
    
        Args:
            available_blocks: 기본 제약조건을 통과한 블록들
            selected_blocks: 이미 선택된 블록 ID 리스트
            violations: 위반 기록 리스트
            all_blocks: 전체 블록 리스트
    
        Returns:
            P/S 쌍 마스킹이 적용된 블록들 (강제 선택 블록만 반환하거나 빈 리스트)
        """
        if not self.constraint_config.is_constraint_enabled("P5#3"):
            return []  # P5#3이 비활성화되면 P/S 쌍 마스킹 없음
    
        # 1단계: P 선택 후 S 강제 선택 로직 (최우선)
        if len(selected_blocks) > 0:
            last_selected_id = selected_blocks[-1]
    
            # PSBlockManager를 통해 마지막 선택 블록이 P 블록인지 확인
            if self.ps_manager.is_port_block(last_selected_id):
                starboard_id = self.ps_manager.get_starboard_for_port(last_selected_id)
    
                if starboard_id and starboard_id not in selected_blocks:
                    # S 블록이 available_blocks에 있으면 우선 선택
                    for block in available_blocks:
                        if block.block_id == starboard_id:
                            ################################################################################################################################################################################################
                            # fix: Routing 상위 제약 (곡판/고심수 간격 & 후공정 착수 순서)
                            ################################################################################################################################################################################################
                            curved_pass, curved_reason = self._check_curved_plate_spacing(block, selected_blocks)
                            high_seam_pass, high_seam_reason = self._check_high_seam_spacing(block, selected_blocks)
    
                            if not curved_pass or not high_seam_pass:
                                priority_reason = curved_reason if not curved_pass else high_seam_reason
                                violations.append(ConstraintViolation(
                                    constraint_id='ROUTING_CURVED_SPACING' if not curved_pass else 'ROUTING_HIGH_SEAM_SPACING',
                                    message=priority_reason,
                                    block_id=starboard_id,
                                    severity='ERROR'
                                ))
                                return []
    
                            # print(f"P/S 강제 연속성: P{last_selected_id} 후 S{starboard_id} 강제 선택")
                            return [block]
    
                    # S 블록이 available_blocks에 없어도 전체 블록에서 찾아서 강제 선택
                    # 이 경우는 제약조건을 무시하고 S 블록을 선택해야 함
                    for s_block in all_blocks:
                        if s_block.block_id == starboard_id:
                            ################################################################################################################################################################################################
                            # fix: Routing 상위 제약 (곡판/고심수 간격 & 후공정 착수 순서)
                            ################################################################################################################################################################################################
                            curved_pass, curved_reason = self._check_curved_plate_spacing(s_block, selected_blocks)
                            high_seam_pass, high_seam_reason = self._check_high_seam_spacing(s_block, selected_blocks)
    
                            if not curved_pass or not high_seam_pass:
                                priority_reason = curved_reason if not curved_pass else high_seam_reason
                                violations.append(ConstraintViolation(
                                    constraint_id='ROUTING_CURVED_SPACING' if not curved_pass else 'ROUTING_HIGH_SEAM_SPACING',
                                    message=priority_reason,
                                    block_id=starboard_id,
                                    severity='ERROR'
                                ))
                                return []
    
                            # print(f"P/S 강제 연속성: P{last_selected_id} 후 S{starboard_id} 제약조건 무시하고 강제 선택")
                            violations.append(ConstraintViolation(
                                constraint_id="P5#3_FORCE",
                                message=f"P/S 연속성을 위해 S 블록 {starboard_id} 강제 선택 (다른 제약조건 무시)",
                                severity="INFO",
                                block_id=starboard_id
                            ))
                            return [s_block]
    
        # 2단계: 기존 로직 - S 블록이 P 블록보다 먼저 선택되지 않도록 필터링
        next_required_block_id = self.ps_manager.get_next_required_block()
    
        if next_required_block_id:
            # 강제 선택해야 할 블록이 available_blocks에 있는지 확인
            required_block = next((b for b in available_blocks if b.block_id == next_required_block_id), None)
    
            if required_block:
                # print(f"P/S 쌍 강제 선택: P 완료 후 S 블록 {next_required_block_id} 즉시 선택")
                return [required_block]
            else:
                # print(f"P/S 쌍 강제 선택 실패: S 블록 {next_required_block_id}가 선택 가능 목록에 없음")
                pass
    
        # 3단계: S 블록이 P 블록보다 먼저 선택되지 않도록 필터링
        ps_filtered_blocks = []
        current_position = len(selected_blocks)  # 현재 선택 위치
    
        for block in available_blocks:
            # P/S 쌍이 아닌 블록은 자유롭게 선택 가능
            if not block.is_p_s_pair():
                ps_filtered_blocks.append(block)
                continue
    
            # P/S 쌍 블록인 경우 순서 제약 확인
            can_process, reason = self.ps_manager.can_process_block_in_sequence(block, current_position)
    
            if can_process:
                ps_filtered_blocks.append(block)
            else:
                # P/S 순서 위반으로 차단된 경우 위반 기록
                violations.append(ConstraintViolation(
                    constraint_id="P5#3",
                    message=f"P/S 순서 제약: {reason}",
                    severity="INFO",
                    block_id=block.block_id
                ))
                self.violation_counts["P5#3"] += 1
                # print(f"P/S 순서 차단: 블록 {block.block_id} - {reason}")
    
        # 통계 업데이트
        self.constraint_checks["P5#3"] += 1
    
        # 우선순위 블록이 없고 필터링만 된 경우 빈 리스트 반환 (Assembly Type 마스킹으로 넘어감)
        if len(ps_filtered_blocks) == len(available_blocks):
            return []  # 필터링 효과가 없음
    
        # print(f"P/S 순서 필터링: {len(available_blocks)} → {len(ps_filtered_blocks)}개 블록")
        return ps_filtered_blocks
