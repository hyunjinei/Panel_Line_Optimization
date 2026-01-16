# [AGENT-ADD] Split from masking/core.py for readability.

from datetime import datetime, timedelta, time, date
from typing import List, Tuple, Optional, Dict, Set
from enhanced_environment.models import EnhancedBlock, BayType

class SawTimeMixin:
    def _check_saw_time_constraint(
        self,
        block: EnhancedBlock,
        current_time: datetime,
        *,
        previous_machine_state: Optional[Dict] = None,
        current_bay_assignments: Optional[Dict[int, BayType]] = None,
        current_day_selected_blocks: Optional[List[int]] = None,
        sequencing_date: Optional[date] = None,
    ) -> Tuple[bool, str]:
        """P6#1,2,3: SAW 시간 제약 체크 (실제 머신 스케줄 기준)"""
        if not (self.constraint_config.is_constraint_enabled("P6#1") or 
                self.constraint_config.is_constraint_enabled("P6#2") or
                self.constraint_config.is_constraint_enabled("P6#3")):
            return True, "P6#1,2,3 제약조건 비활성화"
    
        if block.needs_afternoon_start():
            # 🆕 실제 머신 2번(전면SAW) 시작 시간 계산
            actual_machine_2_start_time = self._calculate_actual_machine_2_start_time_action_masking(
                block,
                current_time,
                afternoon_guard_blocks=None,
                previous_machine_state=previous_machine_state,
                current_bay_assignments=current_bay_assignments,
                current_day_selected_blocks=current_day_selected_blocks,
            )
    
            # [AGENT-DEBUG] P6 시각 추적: 주요 문제 블록(44C,47C,1P 등) 후보 로그
            # DEBUG 출력 제거
    
            # [AGENT-EDIT] 최종 검증 로직과 동일한 validator로 한 번 더 확인 (정밀 일치)
            try:
                if hasattr(self, 'env') and hasattr(self.env, 'validator'):
                    validator = self.env.validator
                    predicted_vios = validator.validate_saw_constraints_realtime(
                        block,
                        current_time,
                        actual_machine_2_start_time,
                    )
                    for vio in predicted_vios:
                        if vio.severity in {"ERROR", "WARNING"}:
                            return False, vio.message
            except Exception:
                # validator 실패 시 기존 로직으로 계속
                pass
    
            # 🆕 시퀀싱 날짜 기준 15:00 비교 (익일 새벽 착수 허용)  # [AGENT-EDIT]
            seq_date = sequencing_date or current_time.date()
            seq_date_3pm = datetime.combine(seq_date, time(15, 0))
    
            # 날짜가 넘어갔으면 제약조건 만족
            if actual_machine_2_start_time.date() > seq_date:
                return True, "P6#1,2,3 시퀀싱일 이후 착수 → 자동 만족"
    
            if actual_machine_2_start_time < seq_date_3pm:
                constraint_reasons = []
                if block.is_draft:
                    constraint_reasons.append("Draft 블록")
                if block.is_cross_seam:
                    constraint_reasons.append("Cross seam 블록")
                if block.main_plate_count > 10:
                    constraint_reasons.append(f"주판 {block.main_plate_count}장 (>10장)")
    
                reason_text = " + ".join(constraint_reasons)
                return False, f"{reason_text}: 실제머신2(SAW) 시작 {actual_machine_2_start_time.strftime('%Y-%m-%d %H:%M')} < 15:00 (오후 3시 착수 필요) [선점고려]"
    
        return True, "SAW 시간 제약 해당없음 또는 시간 조건 만족"
    
    

    def _calculate_actual_machine_2_start_time_action_masking(
        self,
        block: EnhancedBlock,
        current_time: datetime,
        afternoon_guard_blocks: Optional[Set[int]] = None,
        previous_machine_state: Optional[Dict] = None,
        current_bay_assignments: Optional[Dict[int, BayType]] = None,
        current_day_selected_blocks: Optional[List[int]] = None
    ) -> datetime:
        """
        Action Masking용 실제 머신 2번 시작 시간 계산 (CT 테이블 기반)
        """
        try:
            # 🎯 현재까지 선택된 블록들의 시퀀스 가져오기
            current_sequence = list(self.sequence_state.block_sequence) if hasattr(self, 'sequence_state') and self.sequence_state.block_sequence else []
            if current_day_selected_blocks is not None:
                # 날짜 리셋 반영: 당일 시퀀스만 사용
                current_sequence = list(current_day_selected_blocks)
    
            # 현재 블록을 임시로 추가한 시퀀스 생성
            test_sequence = current_sequence + [block.block_id]
    
            # 베이 할당 정보 생성 (확정값 우선 사용)
            test_bay_assignments: Dict[int, BayType] = {}
            if current_bay_assignments:
                test_bay_assignments.update(current_bay_assignments)
    
            for block_id in test_sequence:
                if block_id in test_bay_assignments:
                    continue
                if block_id in self.blocks_dict:
                    test_block = self.blocks_dict[block_id]
                    # [AGENT-EDIT] 실제 환경 베이 자동할당 사용 (기존 추정치보다 정확)
                    if hasattr(self, 'env') and hasattr(self.env, '_auto_assign_bay'):
                        assigned_bay, _ = self.env._auto_assign_bay(test_block, return_analysis=False)
                        test_bay_assignments[block_id] = assigned_bay
                    else:
                        from enhanced_environment.models import BayType
                        test_bay_assignments[block_id] = BayType.BAY_35A
    
            # 🎯 makespan_calculator를 활용한 실제 머신 2번 시작 시간 계산
            from enhanced_environment.bay.makespan import calculate_makespan
    
            # 현재 날짜 시작 시간 계산
            current_date = current_time.date()
            date_start_time = datetime.combine(current_date, datetime.min.time().replace(hour=8))
    
            # makespan 계산으로 CT 테이블 얻기
            makespan_sec, detailed = calculate_makespan(
                self.blocks_dict,
                test_sequence,
                test_bay_assignments,
                previous_machine_state or {},
                afternoon_guard_blocks=afternoon_guard_blocks or set()
            )
    
            # 현재 블록의 스케줄 정보 찾기
            block_schedule = None
            for bs in detailed['block_schedules']:
                if bs['block_id'] == block.block_id:
                    block_schedule = bs
                    break
    
            if block_schedule:
                # 🆕 CT 테이블에서 전면SAW 시작 시간 정확히 추출
                ct_common = detailed['ct_tables']['ct_common']
                block_seq_idx = test_sequence.index(block.block_id)
                saw_end_seconds = ct_common[block_seq_idx + 1, 2]    # 전면SAW 완료 시간
                saw_duration_seconds = block.processing_times[1] * 60 if len(block.processing_times) > 1 else 0
                machine_2_start_time = date_start_time + timedelta(seconds=saw_end_seconds - saw_duration_seconds)
    
                return machine_2_start_time
            else:
                # Fallback: 기존 방식
                return self._fallback_machine_2_time_calculation(block, current_time)
    
        except Exception as e:
            # 에러 발생 시 Fallback
            return self._fallback_machine_2_time_calculation(block, current_time)
    

    def _fallback_machine_2_time_calculation(self, block: EnhancedBlock, current_time: datetime) -> datetime:
        """Fallback 머신 2번 시간 계산"""
        # 기존 단순 계산 방식
        print(f'fallback_action_masking')
        simulated_time = current_time
    
        if hasattr(self, 'sequence_state') and self.sequence_state.block_sequence:
            for prev_block_id in self.sequence_state.block_sequence:
                if prev_block_id in self.blocks_dict:
                    prev_block = self.blocks_dict[prev_block_id]
                    machine_1_processing_time = prev_block.processing_times[0]
                    old_time = simulated_time
                    simulated_time = simulated_time + timedelta(minutes=machine_1_processing_time)
    
                    if simulated_time.date() > old_time.date() or simulated_time.hour >= 22:
                        next_date = simulated_time.date()
                        if simulated_time.hour >= 22 and simulated_time.date() == old_time.date():
                            next_date = old_time.date() + timedelta(days=1)
                        simulated_time = datetime.combine(next_date, datetime.min.time().replace(hour=8))
    
        machine_1_processing_time = block.processing_times[0]
        old_time = simulated_time
        machine_2_start_time = simulated_time + timedelta(minutes=machine_1_processing_time)
    
        if machine_2_start_time.date() > old_time.date() or machine_2_start_time.hour >= 22:
            next_date = machine_2_start_time.date()
            if machine_2_start_time.hour >= 22 and machine_2_start_time.date() == old_time.date():
                next_date = old_time.date() + timedelta(days=1)
            machine_2_start_time = datetime.combine(next_date, datetime.min.time().replace(hour=8))
    
        return machine_2_start_time
