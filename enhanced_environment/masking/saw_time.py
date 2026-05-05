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
        """[AGENT-EDIT] P6#1,2,3 시간 제약 제거: legacy 호출은 항상 통과."""
        _ = (
            block,
            current_time,
            previous_machine_state,
            current_bay_assignments,
            current_day_selected_blocks,
            sequencing_date,
        )
        return True, "P6#1,2,3 시간 제약 제거됨"
    
    

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
            if block_id not in self.blocks_dict:
                raise RuntimeError(f"P6 예측 대상 블록 누락: block_id={block_id}")
            test_block = self.blocks_dict[block_id]
            # [AGENT-EDIT] 예측 경로에서는 preview_assign_bay만 사용한다.
            # _auto_assign_bay는 실제 bay_tracker/ps_manager를 갱신하므로
            # action masking용 teacher/preview 점수 계산을 오염시킬 수 있다.
            if not (hasattr(self, 'env') and hasattr(self.env, '_preview_assign_bay')):
                raise RuntimeError(
                    f"P6 예측용 preview_assign_bay 사용 불가: block_id={block_id}"
                )
            test_bay_assignments[block_id] = self.env._preview_assign_bay(
                test_block,
                return_analysis=False,
            )

        # 🎯 makespan_calculator를 활용한 실제 머신 2번 시작 시간 계산
        from enhanced_environment.bay.makespan import calculate_makespan

        # 현재 날짜 시작 시간 계산
        current_date = current_time.date()
        date_start_time = datetime.combine(current_date, datetime.min.time().replace(hour=8))

        # makespan 계산으로 CT 테이블 얻기
        _, detailed = calculate_makespan(
            self.blocks_dict,
            test_sequence,
            test_bay_assignments,
            previous_machine_state or {},
            afternoon_guard_blocks=afternoon_guard_blocks or set()
        )

        block_schedule = next(
            (bs for bs in detailed['block_schedules'] if bs['block_id'] == block.block_id),
            None,
        )
        if block_schedule is None:
            raise RuntimeError(f"P6 예측 스케줄 누락: block_id={block.block_id}")

        # 🆕 CT 테이블에서 전면SAW 시작 시간 정확히 추출
        ct_common = detailed['ct_tables']['ct_common']
        block_seq_idx = test_sequence.index(block.block_id)
        saw_end_seconds = ct_common[block_seq_idx + 1, 2]    # 전면SAW 완료 시간
        saw_duration_seconds = block.processing_times[1] * 60 if len(block.processing_times) > 1 else 0
        machine_2_start_time = date_start_time + timedelta(seconds=saw_end_seconds - saw_duration_seconds)

        return machine_2_start_time
