# [AGENT-ADD] Split from masking/core.py for readability.

from datetime import datetime, date, time
from typing import List, Tuple, Dict, Any, Optional
from enhanced_environment.models import (
    EnhancedBlock,
    ConstraintViolation,
    AssemblyType,
    BayType,
)

class ScoringMixin:
    def _evaluate_blocks_stage(
        self,
        blocks: List[EnhancedBlock],
        stage_label: str,
        selected_blocks: List[int],
        block_analysis: List[Dict[str, Any]],
        current_time: datetime,
        last_assembly_type: Optional[AssemblyType],
        all_blocks: List[EnhancedBlock],
        constraints_to_relax: Optional[List[str]] = None,
        violations: Optional[List[ConstraintViolation]] = None,
        previous_machine_state: Optional[Dict] = None,
        current_bay_assignments: Optional[Dict[int, BayType]] = None,
        current_day_selected_blocks: Optional[List[int]] = None,
        **kwargs: Any  # [AGENT-ADD] 호환성: 예상치 못한 키워드 인자 무시 (legacy 호출 방지)
    ) -> List[EnhancedBlock]:
        if not blocks:
            return []
    
        constraints_to_relax = constraints_to_relax or []
        valid_candidates: List[EnhancedBlock] = []
        for block in blocks:
            is_valid, reason = self._check_consecutive_3bay_prevention(block, selected_blocks)
            if is_valid:
                valid_candidates.append(block)
            else:
                analysis = next((info for info in block_analysis if info['block_id'] == block.block_id), None)
                if analysis:
                    analysis['is_available'] = False
                    analysis['exclusion_reason'] = reason
                    analysis['three_bay_check'] = 'FAIL'
    
        if not valid_candidates:
            return []
    
        constraint_order_assembly = [
            ("ROUTING_CURVED_SPACING", "곡판 간격"),
            ("ROUTING_HIGH_SEAM_SPACING", "고심수 간격"),
            ("P5#15", "명절 전날 차단"),
            ("P5#8,9,10,16", "용량"),
            ("P6#1,2,3", "SAW 시간"),
            ("ROUTING_C_SEAM_SPACING", "C/Seam 간격"),
            ("P5#11,12", "Assembly 타입"),
            ("P5#3,4", "P/S 순서")
        ]
    
        step_final_candidates: List[EnhancedBlock] = []
    
        for block in valid_candidates:
            passes_all = True
            failed_constraints: List[str] = []
            relaxed_failures: List[str] = []
    
            curved_pass, curved_reason = self._check_curved_plate_spacing(block, selected_blocks)
            high_seam_pass, high_seam_reason = self._check_high_seam_spacing(block, selected_blocks)
    
            if not curved_pass and "ROUTING_CURVED_SPACING" not in constraints_to_relax:
                passes_all = False
                failed_constraints.append('ROUTING_CURVED_SPACING')
            elif not curved_pass:
                relaxed_failures.append('ROUTING_CURVED_SPACING')
            elif "ROUTING_CURVED_SPACING" in constraints_to_relax:
                relaxed_failures.append('ROUTING_CURVED_SPACING')
            if not high_seam_pass and "ROUTING_HIGH_SEAM_SPACING" not in constraints_to_relax:
                passes_all = False
                failed_constraints.append('ROUTING_HIGH_SEAM_SPACING')
            elif not high_seam_pass:
                relaxed_failures.append('ROUTING_HIGH_SEAM_SPACING')
            elif "ROUTING_HIGH_SEAM_SPACING" in constraints_to_relax:
                relaxed_failures.append('ROUTING_HIGH_SEAM_SPACING')
    
            # [AGENT-ADD] 작업장 순서 하드 제약: 후보 평가 경로에서도 반드시 차단
            workshop_pass, workshop_reason = self._check_workshop_order_constraint(block, all_blocks, selected_blocks)
            if not workshop_pass:
                passes_all = False
                failed_constraints.append("ROUTING_WORKSHOP_ORDER")
    
            holiday_pass, holiday_reason = self._check_holiday_eve_constraint(block, current_time)
            if not holiday_pass and "P5#15" not in constraints_to_relax:
                passes_all = False
                failed_constraints.append("P5#15")
            elif not holiday_pass:
                relaxed_failures.append("P5#15")
            elif "P5#15" in constraints_to_relax:
                relaxed_failures.append("P5#15")
    
            capacity_pass, capacity_reason = self._check_integrated_capacity_constraints(block, current_time)
            if not capacity_pass and "P5#8" not in constraints_to_relax:
                passes_all = False
                failed_constraints.append("P5#8,9,10,16")
            elif not capacity_pass:
                relaxed_failures.append("P5#8,9,10,16")
            elif any(c in constraints_to_relax for c in ["P5#8","P5#9","P5#10","P5#16"]):
                relaxed_failures.append("P5#8,9,10,16")
    
            saw_pass, saw_reason = self._check_saw_time_constraint(
                block,
                current_time,
                previous_machine_state=previous_machine_state,
                current_bay_assignments=current_bay_assignments,
                current_day_selected_blocks=current_day_selected_blocks
            )
            if not saw_pass and not {'P6#1','P6#2','P6#3'}.intersection(set(constraints_to_relax or [])):
                passes_all = False
                failed_constraints.append("P6#1,2,3")
            elif not saw_pass:
                relaxed_failures.append("P6#1,2,3")
            elif {'P6#1','P6#2','P6#3'}.intersection(set(constraints_to_relax or [])):
                relaxed_failures.append("P6#1,2,3")
    
            c_seam_pass, c_seam_reason = self._check_c_seam_spacing(block, selected_blocks)
            if not c_seam_pass and "ROUTING_C_SEAM_SPACING" not in constraints_to_relax:
                passes_all = False
                failed_constraints.append("ROUTING_C_SEAM_SPACING")
            elif not c_seam_pass:
                relaxed_failures.append("ROUTING_C_SEAM_SPACING")
            elif "ROUTING_C_SEAM_SPACING" in constraints_to_relax:
                relaxed_failures.append("ROUTING_C_SEAM_SPACING")
    
            assembly_type_pass, assembly_type_reason = self._check_assembly_type_constraint(
                block, last_assembly_type, selected_blocks, blocks, constraints_to_relax
            ) if last_assembly_type else (True, "")
            if last_assembly_type and not assembly_type_pass:
                passes_all = False
                failed_constraints.append("P5#11,12")
            elif last_assembly_type and not assembly_type_pass:
                relaxed_failures.append("P5#11,12")
            elif last_assembly_type and any(c in constraints_to_relax for c in ["P5#11","P5#12"]):
                relaxed_failures.append("P5#11,12")
    
            ps_order_pass, ps_order_reason = self._check_ps_order_constraint(block, selected_blocks, blocks)
            if not ps_order_pass:
                passes_all = False
                failed_constraints.append("P5#3,4")
    
            if not self._check_cross_seam_constraint(block, selected_blocks, blocks)[0]:
                passes_all = False
                failed_constraints.append("P6#4")
    
            analysis = next((info for info in block_analysis if info['block_id'] == block.block_id), None)
            if analysis:
                if passes_all:
                    analysis['is_available'] = True
                    if analysis.get('inclusion_reason') in (None, '', '모든 제약조건 통과'):
                        analysis['inclusion_reason'] = '모든 제약조건 통과'
                    # [AGENT-ADD] 완화 단계 정보 기록 (실제 완화 대상이 있을 때만 기록)
                    if constraints_to_relax and relaxed_failures:
                        analysis['relax_stage'] = stage_label
                        analysis['relax_constraints'] = constraints_to_relax
                        # [AGENT-ADD] 완화로 통과한 실제 제약 수 기록
                        relaxed_targets = list(dict.fromkeys(relaxed_failures))
                        analysis['relax_targets'] = relaxed_targets
                        analysis['relax_target_count'] = len(relaxed_targets)
                        relax_msg = f"[RELAX] {stage_label}: {', '.join(relaxed_targets)}"
                        analysis['inclusion_reason'] = relax_msg
                        if violations is not None:
                            existing = {(v.constraint_id, v.message) for v in violations}
                            key = ('RELAX_STAGE', relax_msg)
                            if key not in existing:
                                violations.append(ConstraintViolation(
                                    constraint_id='RELAX_STAGE',
                                    message=relax_msg,
                                    severity='INFO',  # [AGENT-EDIT] 완화 사용은 정보용으로만 기록
                                    block_id=block.block_id
                                ))
                            # [AGENT-ADD] 상세 로그: 어떤 제약을 풀었는지 INFO로 추가
                            detail_msg = f"완화 적용으로 통과: {', '.join(relaxed_targets)}"
                            detail_key = ('RELAX_STAGE_DETAIL', detail_msg)
                            if detail_key not in existing:
                                violations.append(ConstraintViolation(
                                    constraint_id='RELAX_STAGE_DETAIL',
                                    message=detail_msg,
                                    severity='INFO',
                                    block_id=block.block_id
                                ))
                            # [AGENT-EDIT] 완화 통과는 INFO로만 기록 (카운트 제외)
                            for target in relaxed_targets:
                                tgt_key = (target, stage_label)
                                if tgt_key not in existing:
                                    violations.append(ConstraintViolation(
                                        constraint_id=target,
                                        message=f"{stage_label}: 완화 적용으로 통과",
                                        severity='INFO',
                                        block_id=block.block_id
                                    ))
                else:
                    analysis['is_available'] = False
                    analysis['exclusion_reason'] = f"{', '.join(failed_constraints)} 위반"
    
            if passes_all:
                step_final_candidates.append(block)
    
        if step_final_candidates and getattr(self.constraint_config, 'enable_debug_violations', True):
            self._debug_candidate_stage(True, f"{stage_label} - 최종 제약", step_final_candidates, None)
    
        return step_final_candidates
    
    # [AGENT-ADD] 최후 선택: 제약 위반 수 최소 블록 선택용 스코어 계산

    def _score_block_violations(
        self,
        block: EnhancedBlock,
        selected_blocks: List[int],
        all_blocks: List[EnhancedBlock],
        current_time: datetime,
        last_assembly_type: Optional[AssemblyType],
        previous_machine_state: Optional[Dict] = None,
        current_bay_assignments: Optional[Dict[int, BayType]] = None,
        current_day_selected_blocks: Optional[List[int]] = None
    ) -> Tuple[int, List[str]]:
        failed_constraints: List[str] = []
    
        # 3베이 연속 방지
        bay_ok, _ = self._check_consecutive_3bay_prevention(block, selected_blocks)
        if not bay_ok:
            failed_constraints.append("CONSECUTIVE_3BAY")
    
        curved_ok, _ = self._check_curved_plate_spacing(block, selected_blocks)
        if not curved_ok:
            failed_constraints.append("ROUTING_CURVED_SPACING")
    
        high_seam_ok, _ = self._check_high_seam_spacing(block, selected_blocks)
        if not high_seam_ok:
            failed_constraints.append("ROUTING_HIGH_SEAM_SPACING")
    
        holiday_ok, _ = self._check_holiday_eve_constraint(block, current_time)
        if not holiday_ok:
            failed_constraints.append("P5#15")
    
        capacity_ok, _ = self._check_integrated_capacity_constraints(block, current_time)
        if not capacity_ok:
            failed_constraints.append("P5#8,9,10,16")
    
        saw_ok, _ = self._check_saw_time_constraint(
            block,
            current_time,
            previous_machine_state=previous_machine_state,
            current_bay_assignments=current_bay_assignments,
            current_day_selected_blocks=current_day_selected_blocks
        )
        if not saw_ok:
            failed_constraints.append("P6#1,2,3")
    
        c_seam_ok, _ = self._check_c_seam_spacing(block, selected_blocks)
        if not c_seam_ok:
            failed_constraints.append("ROUTING_C_SEAM_SPACING")
    
        workshop_ok, _ = self._check_workshop_order_constraint(block, all_blocks, selected_blocks)
        if not workshop_ok:
            failed_constraints.append("ROUTING_WORKSHOP_ORDER")
    
        if last_assembly_type:
            # [AGENT-EDIT] _check_assembly_type_constraint는 relaxed_constraints 인자를 사용
            assembly_ok, _ = self._check_assembly_type_constraint(
                block, last_assembly_type, selected_blocks, all_blocks, []
            )
            if not assembly_ok:
                failed_constraints.append("P5#11,12")
    
        ps_ok, _ = self._check_ps_order_constraint(block, selected_blocks, all_blocks)
        if not ps_ok:
            failed_constraints.append("P5#3,4")
    
        cross_ok, _ = self._check_cross_seam_constraint(block, selected_blocks, all_blocks)
        if not cross_ok:
            failed_constraints.append("P6#4")
    
        return len(failed_constraints), failed_constraints
    

    def _pick_leadtime_guard_block(
        self,
        blocks: List[EnhancedBlock],
        current_panel_date: datetime.date,
        override_required_days: Optional[int] = None,
        *,
        prefer_time_feasible: bool = False,
        current_time: Optional[datetime] = None,
        previous_machine_state: Optional[Dict] = None,
        current_bay_assignments: Optional[Dict[int, BayType]] = None
    ) -> Optional[EnhancedBlock]:
        # [AGENT-EDIT] 리드타임 강제 선택 시 P6 오후 3시 충족 후보를 우선 선택 (옵션)
        min_lead = override_required_days
        if min_lead is None:
            min_lead = getattr(self.constraint_config, 'minimum_panel_lead_days', 0) if self.constraint_config else 0
        min_lead = max(0, min_lead)
        if min_lead <= 0:
            return None
        candidate: Optional[EnhancedBlock] = None
        time_feasible: Optional[EnhancedBlock] = None
        fallback: Optional[EnhancedBlock] = None
        for block in blocks:
            start_dt = getattr(block, 'assembly_start_date', None)
            if not isinstance(start_dt, datetime):
                continue
            delta = (start_dt.date() - current_panel_date).days
            if 0 <= delta <= min_lead:
                # 오전에는 P6 오후착수 블록을 서두르지 않음: 다른 후보가 있고 15시 전이면 스킵
                if (prefer_time_feasible and current_time and current_time.time() < time(15, 0)
                    and block.needs_afternoon_start() and len(blocks) > 1):
                    continue
                # 시간 제약(P6 15:00)까지 만족하는 후보를 우선 선택
                if prefer_time_feasible and current_time and block.needs_afternoon_start():
                    machine_start = self._calculate_actual_machine_2_start_time_action_masking(
                        block,
                        current_time,
                        previous_machine_state=previous_machine_state,
                        current_bay_assignments=current_bay_assignments
                    )
                    seq_date = current_time.date()
                    seq_3pm = datetime.combine(seq_date, time(15, 0))
                    # 다음날로 넘어가면 자동 만족
                    is_time_ok = (machine_start.date() > seq_date) or (machine_start >= datetime.combine(machine_start.date(), time(15, 0)))
                    if is_time_ok:
                        if time_feasible is None or start_dt < getattr(time_feasible, 'assembly_start_date', start_dt):
                            time_feasible = block
                    else:
                        if fallback is None or start_dt < getattr(fallback, 'assembly_start_date', start_dt):
                            fallback = block
                else:
                    if candidate is None or start_dt < getattr(candidate, 'assembly_start_date', start_dt):
                        candidate = block
    
        # 시간까지 만족하는 후보가 있으면 그것을 사용, 없으면 기존 로직 fallback
        if time_feasible:
            return time_feasible
        if candidate:
            return candidate
        return fallback
    
    ################################################################################################################################################################################################
    # [AGENT-ADD] Workshop bucket helpers for candidate loop
    ################################################################################################################################################################################################

    def _force_select_earliest_block(
        self,
        blocks: List[EnhancedBlock],
        selected_blocks: List[int]
    ) -> Tuple[Optional[EnhancedBlock], Optional[List[str]]]:
        """워크숍 우선으로 묶여 있는 경우 가장 빠른 착수일 블록을 강제로 선택."""
        remaining_blocks = [b for b in blocks if b.block_id not in selected_blocks]
        if not remaining_blocks:
            return None, None
    
        def _get_date(b: EnhancedBlock) -> datetime:
            date_attr = getattr(b, 'assembly_start_date', None)
            return date_attr if date_attr else datetime.max
    
        remaining_blocks.sort(key=_get_date)
        earliest_date = _get_date(remaining_blocks[0]).date()
        earliest_same_date = [b for b in remaining_blocks if _get_date(b).date() == earliest_date]
    
        # P/S 순서를 우선 만족하는 블록을 찾음
        for block in earliest_same_date:
            ps_ok, _ = self._check_ps_order_constraint(block, selected_blocks, blocks)
            if ps_ok:
                return block, None
    
        # 그래도 없으면 첫 번째 블록을 반환하고 P/S 위반을 알림
        fallback_block = earliest_same_date[0]
        return fallback_block, ["P5#3,4"]
    

    def _check_cross_seam_constraint(
        self, 
        block: EnhancedBlock, 
        selected_blocks: List[int],
        all_blocks: List[EnhancedBlock]
    ) -> Tuple[bool, str]:
        """P6#4: Cross seam 혼합 배치 제약 체크"""
        if not self.constraint_config.is_constraint_enabled("P6#4"):
            return True, "P6#4 제약조건 비활성화"
    
        # Cross seam 블록 확인
        cross_seam_blocks = set(self.metadata.get('cross_seam_mixing_control', {}).get('cross_seam_blocks', []))
        is_special_block = (
            block.block_id in cross_seam_blocks or
            block.is_cross_seam or 
            block.is_draft or 
            (block.main_plate_count > 10)
        )
    
        if not is_special_block:
            return True, "P6#4 해당없음: 일반 블록"
    
        # 최근 선택된 특수 블록 연속 개수 확인
    ################################################################################################################################################################################################
    # fix: C/Seam 연속 금지 (최소 1판 간격)
    ################################################################################################################################################################################################
        metadata_limit = self.metadata.get('cross_seam_mixing_control', {}).get('max_consecutive_special')
        if metadata_limit is None:
            max_consecutive = 1
        else:
            max_consecutive = max(1, metadata_limit)
        recent_special_count = 0
    
        # 최근 선택된 블록들 중 특수 블록 개수 확인 (최대 2개까지만)
        for recent_block_id in selected_blocks[-max_consecutive:]:
            recent_block = next((b for b in all_blocks if b.block_id == recent_block_id), None)
            if recent_block:
                recent_is_special = (
                    recent_block_id in cross_seam_blocks or
                    recent_block.is_cross_seam or 
                    recent_block.is_draft or 
                    (recent_block.main_plate_count > 10)
                )
                if recent_is_special:
                    recent_special_count += 1
                else:
                    break  # 일반 블록 나오면 연속 중단
    
        if recent_special_count >= max_consecutive:
            return False, f"P6#4 적용: 특수 블록 {recent_special_count}개 연속 (최대 {max_consecutive}개)"
        else:
            return True, f"P6#4 적용: 특수 블록 연속 {recent_special_count}/{max_consecutive} (허용)"
