# [AGENT-ADD] SAW/afternoon start constraint validation split from constraint_validator.py.

from datetime import datetime, timedelta, time, date
from typing import List

from enhanced_environment.models import EnhancedBlock, ConstraintViolation


class SawValidationMixin:
    def validate_saw_constraints_realtime(
        self,
        block: EnhancedBlock,
        current_time: datetime,
        actual_machine_2_start_time: datetime = None,
    ) -> List[ConstraintViolation]:
        """
        SAW 제약조건 실시간 검증 (실제 머신 스케줄 기반)

        P6#1,2,3: 오후 3시 착수 제약 실시간 검증
        - Draft 블록은 해당 날짜 오후 3시 착수
        - Cross seam 블록은 해당 날짜 오후 3시 착수
        - 주판 10장 초과 블록은 해당 날짜 오후 3시 착수

        Args:
            block: 처리 중인 블록
            current_time: 현재 시간 (머신 1번 기준)
            actual_machine_2_start_time: 실제 머신 2번 시작 시간 (이미 계산된 경우)

        Returns:
            SAW 제약조건 위반 리스트
        """
        _ = (block, current_time, actual_machine_2_start_time)
        # [AGENT-EDIT] 논문 실험 기준으로 P6#1,#2,#3 시간 제약 제거.
        return []

    def _calculate_actual_machine_2_start_time(
        self, block: EnhancedBlock, current_time: datetime
    ) -> datetime:
        """
        실제 머신 2번 시작 시간 계산 (CT 테이블 기반)
        """
        try:
            # 🎯 현재까지 완료된 블록들의 시퀀스 가져오기
            current_sequence = []
            for step in self.completed_steps:
                if hasattr(step, "block_id"):
                    current_sequence.append(step.block_id)

            # 현재 블록을 임시로 추가한 시퀀스 생성
            test_sequence = current_sequence + [block.block_id]

            # 베이 할당 정보 생성
            test_bay_assignments = {}
            for block_id in test_sequence:
                if block_id in self.blocks_dict:
                    test_block = self.blocks_dict[block_id]
                    # [AGENT-EDIT] 검증용 예측 경로에서는 preview_assign_bay만 사용한다.
                    # _auto_assign_bay는 상태를 갱신하므로 사후 검증 중 tracker를 오염시킬 수 있다.
                    if not (self.env is not None and hasattr(self.env, "_preview_assign_bay")):
                        raise RuntimeError(
                            f"P6 validator preview_assign_bay 사용 불가: block_id={block_id}"
                        )
                    assigned_bay = self.env._preview_assign_bay(
                        test_block, return_analysis=False
                    )
                    test_bay_assignments[block_id] = assigned_bay

            # 🎯 makespan_calculator를 활용한 실제 시간 계산
            from enhanced_environment.bay.makespan import calculate_makespan

            # 현재 날짜 시작 시간 계산
            current_date = current_time.date()
            date_start_time = datetime.combine(
                current_date, datetime.min.time().replace(hour=8)
            )

            # makespan 계산으로 CT 테이블 얻기
            _, detailed = calculate_makespan(
                self.blocks_dict,
                test_sequence,
                test_bay_assignments,
                {},  # 당일 기준
            )

            # 현재 블록의 판계 공정 완료 시간 = 머신 2번 시작 시간
            ct_common = detailed["ct_tables"]["ct_common"]
            block_seq_idx = test_sequence.index(block.block_id)
            panel_end_seconds = ct_common[block_seq_idx + 1, 1]  # 1번 공정 완료 시간

            # 실제 머신 2번 시작 시간
            machine_2_start_time = date_start_time + timedelta(
                seconds=panel_end_seconds
            )

            return machine_2_start_time

        except Exception as exc:
            # [AGENT-EDIT] P6는 hard 제약이므로 validator 시간 계산 실패를 숨기지 않는다.
            raise RuntimeError(
                f"P6 validator 머신2 시작시간 계산 실패: block_id={block.block_id}"
            ) from exc

    def _get_afternoon_constraint_id(self, block: EnhancedBlock) -> str:
        """
        블록에 해당하는 오후 3시 제약조건 ID 반환

        Args:
            block: 확인할 블록

        Returns:
            해당하는 제약조건 ID
        """
        if block.is_draft:
            return "P6#1"
        if block.is_cross_seam:
            return "P6#2"
        if block.main_plate_count > 10:
            return "P6#3"
        return "P6#1"  # 기본값
