# makespan_calculator.py
# [AGENT-ADD] Split makespan calculation logic into bay/makespan/core.py.

import numpy as np
from typing import List, Dict, Tuple, Optional, Set
from enhanced_environment.models import BayType

AFTERNOON_THRESHOLD_SECONDS = 7 * 3600  # 08:00 기준 15:00까지 7시간


def _apply_afternoon_guard_shift(
    i: int,
    block,
    ct_common,
    ct_branch_a,
    ct_branch_b,
    assigned_bay: BayType,
    afternoon_guard_blocks: Set[int]
) -> None:
    """
    리드타임 강제 선택된 P6(15시 필요) 블록을 15:00 이후로 밀어주기 위한 보정.
    - 판계 완료시간(ct_common[i+1,1])이 15:00 이전이면 delta를 계산해 동일 블록 행을 밀고,
      이후 블록의 ESD 계산에 자연히 반영되도록 테이블 값을 직접 수정한다.
    """
    if not afternoon_guard_blocks or block.block_id not in afternoon_guard_blocks:
        return
    if not getattr(block, "needs_afternoon_start", lambda: False)():
        return

    panel_end = ct_common[i + 1, 1]
    if panel_end >= AFTERNOON_THRESHOLD_SECONDS:
        return

    delta = AFTERNOON_THRESHOLD_SECONDS - panel_end
    # 현재 블록의 공통/분기 완료 시간을 밀어줌 → 이후 블록 ESD가 자연히 따라감
    ct_common[i + 1, 1:] += delta
    if assigned_bay == BayType.BAY_35A:
        ct_branch_a[i + 1, :] += delta
    else:
        ct_branch_b[i + 1, :] += delta


def calculate_makespan(blocks_dict: dict, sequence: List[int], branch_assignments: Dict[int, BayType], 
                      previous_machine_state: Dict = None,
                      afternoon_guard_blocks: Optional[Set[int]] = None) -> Tuple[float, Dict]:
    """
    주어진 순서와 베이 할당에 대한 makespan 계산 (8개 공정 구조)
    
    Args:
        blocks_dict: 블록 ID를 키로 하는 블록 객체 딕셔너리
        sequence: 블록 처리 순서
        branch_assignments: 분기 베이 할당 {block_id: bay_type}
        previous_machine_state: 이전 날의 머신 완료 시간 상태 (옵션)
            {
                'common_times': [공정1, 공정2, 공정3, 공정4, 공정5],  # 공통 공정 완료 시간
                'branch_a_times': [공정6, 공정7, 공정8],  # 베이A 분기 공정 완료 시간
                'branch_b_times': [공정6, 공정7, 공정8],  # 베이B 분기 공정 완료 시간
                'day_start_offset': 8시간 오프셋 (초)  # 새로운 날의 시작 오프셋
            }
        
    Returns:
        (총 makespan (초), {
            'total_makespan': 총 makespan,
            'block_schedules': [개별 블록 스케줄 정보],
            'ct_tables': CT_table 정보 (필요시),
            'final_machine_state': 마지막 머신 상태 (다음 날로 전달용)
        })
    """
    afternoon_guard_blocks = afternoon_guard_blocks or set()
    # ✅ 디버깅: 입력 정보 출력
    # print(f"\n🔍 calculate_makespan 시작:")
    # print(f"   순서: {sequence[:3]}...")  # 첫 3개만 출력
    # print(f"   베이 할당: {dict(list(branch_assignments.items())[:3])}...")  # 첫 3개만 출력
    
    # 완료 시간 테이블 초기화
    num_blocks = len(sequence)
    ct_common = np.zeros((num_blocks + 1, 6))  # 공통 5개 공정
    ct_branch_a = np.zeros((num_blocks + 1, 4))  # 분기A 3개 공정
    ct_branch_b = np.zeros((num_blocks + 1, 4))  # 분기B 3개 공정
    
    # 🆕 이전 날의 머신 상태 반영
    if previous_machine_state:
        day_start_offset = previous_machine_state.get('day_start_offset', 0)
        
        # 공통 공정의 이전 완료 시간 설정 (새로운 날의 오프셋 적용)
        common_times = previous_machine_state.get('common_times', [0, 0, 0, 0, 0])
        for j in range(5):
            # 이전 날 완료 시간에서 새로운 날의 시작 시간을 뺀 값 (상대 시간)
            ct_common[0, j + 1] = max(0, common_times[j] - day_start_offset)
        
        # 분기 공정의 이전 완료 시간 설정
        branch_a_times = previous_machine_state.get('branch_a_times', [0, 0, 0])
        branch_b_times = previous_machine_state.get('branch_b_times', [0, 0, 0])
        for j in range(3):
            ct_branch_a[0, j] = max(0, branch_a_times[j] - day_start_offset)
            ct_branch_b[0, j] = max(0, branch_b_times[j] - day_start_offset)
        
        # print(f"🔄 이전 날 머신 상태 적용:")
        # print(f"   일자 오프셋: {day_start_offset/3600:.1f}시간")
        common_remaining = [max(0, t-day_start_offset)/60.0 for t in common_times]
        branch_a_remaining = [max(0, t-day_start_offset)/60.0 for t in branch_a_times]
        branch_b_remaining = [max(0, t-day_start_offset)/60.0 for t in branch_b_times]
        # print(f"   공통 공정 잔여시간: {common_remaining[:3]}분...")
        # print(f"   분기A 잔여시간: {branch_a_remaining}분")
        # print(f"   분기B 잔여시간: {branch_b_remaining}분")
    
    for i, block_id in enumerate(sequence[:3]):  # ✅ 첫 3개만 상세 출력
        if block_id not in blocks_dict:
            continue
            
        block = blocks_dict[block_id]
        # print(f"\n   📋 블록 {i+1} (ID: {block_id}) 처리:")
        # print(f"      공정시간: {[round(t, 1) for t in block.processing_times]}")
        
        # 공통 공정 (1~5: 판계, 전면SAW, TurnOver, 후면SAW, NC)
        for j in range(5):
            processing_time = block.processing_times[j] * 60  # 분 → 초
            
            # ESD 계산
            esd_1 = ct_common[i, j + 1]  # 이전 블록의 같은 공정
            esd_2 = ct_common[i + 1, j]  # 같은 블록의 이전 공정
            
            earliest_start = max(esd_1, esd_2)
            completion_time = earliest_start + processing_time
            
            ct_common[i + 1, j + 1] = completion_time
        
            # print(f"      공정 {j+1}: ESD1={esd_1/60:.1f}분, ESD2={esd_2/60:.1f}분 → 시작={earliest_start/60:.1f}분, 완료={completion_time/60:.1f}분")
        
        # 분기 공정 (6~8: 론지취부, 론지용접, 수정)
        assigned_bay = branch_assignments.get(block_id, BayType.BAY_35A)
        branch_table = ct_branch_a if assigned_bay == BayType.BAY_35A else ct_branch_b
        other_table = ct_branch_b if assigned_bay == BayType.BAY_35A else ct_branch_a
        
        # print(f"      할당 베이: {assigned_bay.value}")
        
        for j in range(3):  # 분기는 3개 공정
            processing_time = block.processing_times[j + 5] * 60  # 공정 6~8
            
            if j == 0:  # 첫 번째 분기 공정 (론지취부)
                # 공통 공정 완료 시간과 분기 가용 시간 중 최대값
                common_completion = ct_common[i + 1, 5]  # 공통 마지막 공정
                branch_available = branch_table[i, 0]
                earliest_start = max(common_completion, branch_available)
                completion_time = earliest_start + processing_time
                branch_table[i + 1, 0] = completion_time
                
                # print(f"      분기공정 {j+1}: 공통완료={common_completion/60:.1f}분, 분기가용={branch_available/60:.1f}분 → 시작={earliest_start/60:.1f}분, 완료={completion_time/60:.1f}분")
                
                # 사용하지 않는 분기는 이전 상태 유지
                other_table[i + 1, :] = other_table[i, :]
            else:
                # 분기 내부 공정
                esd_1 = branch_table[i, j]      # 이전 블록의 같은 공정
                esd_2 = branch_table[i + 1, j - 1]  # 같은 블록의 이전 공정
                earliest_start = max(esd_1, esd_2)
            
            completion_time = earliest_start + processing_time
            branch_table[i + 1, j] = completion_time
            
            # if j > 0:
            #     print(f"      분기공정 {j+1}: ESD1={esd_1/60:.1f}분, ESD2={esd_2/60:.1f}분 → 시작={earliest_start/60:.1f}분, 완료={completion_time/60:.1f}분")

        # 🆕 리드타임 강제+P6 대상은 15:00 이전이면 강제로 지연
        _apply_afternoon_guard_shift(
            i, block, ct_common, ct_branch_a, ct_branch_b, assigned_bay,
            afternoon_guard_blocks
        )
    
    # 처리되지 않은 나머지 블록들도 계산 (출력 없이)
    for i, block_id in enumerate(sequence[3:], start=3):
        if block_id not in blocks_dict:
            continue
            
        block = blocks_dict[block_id]
        
        # 공통 공정 (1~5: 판계, 전면SAW, TurnOver, 후면SAW, NC)
        for j in range(5):
            processing_time = block.processing_times[j] * 60  # 분 → 초
            
            # ESD 계산
            esd_1 = ct_common[i, j + 1]  # 이전 블록의 같은 공정
            esd_2 = ct_common[i + 1, j]  # 같은 블록의 이전 공정
            
            earliest_start = max(esd_1, esd_2)
            completion_time = earliest_start + processing_time
            
            ct_common[i + 1, j + 1] = completion_time
        
        # 분기 공정 (6~8: 론지취부, 론지용접, 수정)
        assigned_bay = branch_assignments.get(block_id, BayType.BAY_35A)
        branch_table = ct_branch_a if assigned_bay == BayType.BAY_35A else ct_branch_b
        other_table = ct_branch_b if assigned_bay == BayType.BAY_35A else ct_branch_a
        
        for j in range(3):  # 분기는 3개 공정
            processing_time = block.processing_times[j + 5] * 60  # 공정 6~8
            
            if j == 0:  # 첫 번째 분기 공정 (론지취부)
                # 공통 공정 완료 시간과 분기 가용 시간 중 최대값
                common_completion = ct_common[i + 1, 5]  # 공통 마지막 공정
                branch_available = branch_table[i, 0]
                earliest_start = max(common_completion, branch_available)
                completion_time = earliest_start + processing_time
                branch_table[i + 1, 0] = completion_time
                
                # 사용하지 않는 분기는 이전 상태 유지
                other_table[i + 1, :] = other_table[i, :]
            else:
                # 분기 내부 공정
                esd_1 = branch_table[i, j]      # 이전 블록의 같은 공정
                esd_2 = branch_table[i + 1, j - 1]  # 같은 블록의 이전 공정
                earliest_start = max(esd_1, esd_2)
            
            completion_time = earliest_start + processing_time
            branch_table[i + 1, j] = completion_time

        # 🆕 리드타임 강제+P6 대상은 15:00 이전이면 강제로 지연
        _apply_afternoon_guard_shift(
            i, block, ct_common, ct_branch_a, ct_branch_b, assigned_bay,
            afternoon_guard_blocks or set()
        )
    
    # 최종 makespan 계산
    max_completion_time = max(
        np.max(ct_common) if ct_common.size > 0 else 0,
        np.max(ct_branch_a) if ct_branch_a.size > 0 else 0,
        np.max(ct_branch_b) if ct_branch_b.size > 0 else 0
    )
    
    # print(f"\n   📊 최종 makespan: {max_completion_time/60:.1f}분")
    
    # ✅ 개별 블록 스케줄 정보 계산 (핵심 개선!)
    block_schedules = []
    
    # print(f"\n   ⏰ 개별 블록 스케줄 계산:")
    for i, block_id in enumerate(sequence[:3]):  # ✅ 첫 3개만 상세 출력
        # 블록 시작시간: 첫 번째 공정 시작 시간
        if i == 0:
            # 첫 번째 블록: 0초부터 시작
            block_start_seconds = 0
        else:
            # 첫 번째 공정이 시작 가능한 시간 = 이전 블록의 첫 번째 공정 완료 시간
            block_start_seconds = ct_common[i, 1]  # 이전 블록의 공정1 완료시간
        
        # 블록 종료시간: 마지막 공정 완료 시간
        assigned_bay = branch_assignments.get(block_id, BayType.BAY_35A)
        if assigned_bay == BayType.BAY_35A:
            block_end_seconds = ct_branch_a[i + 1, 2]  # ✅ 수정: 인덱스 2 (마지막 공정)
        else:
            block_end_seconds = ct_branch_b[i + 1, 2]  # ✅ 수정: 인덱스 2 (마지막 공정)
        
        # print(f"      블록 {i+1} (ID: {block_id}): 시작={block_start_seconds/60:.1f}분, 종료={block_end_seconds/60:.1f}분")
        
        block_schedules.append({
            'block_id': block_id,
            'sequence_index': i,
            'start_seconds': block_start_seconds,
            'end_seconds': block_end_seconds,
            'duration_seconds': block_end_seconds - block_start_seconds,
            'assigned_bay': assigned_bay.value
        })
    
    # 나머지 블록들도 계산 (출력 없이)
    for i, block_id in enumerate(sequence[3:], start=3):
        # 블록 시작시간: 첫 번째 공정 시작 시간
        if i == 0:
            # 첫 번째 블록: 0초부터 시작
            block_start_seconds = 0
        else:
            # 첫 번째 공정이 시작 가능한 시간 = 이전 블록의 첫 번째 공정 완료 시간
            block_start_seconds = ct_common[i, 1]  # 이전 블록의 공정1 완료시간
        
        # 블록 종료시간: 마지막 공정 완료 시간
        assigned_bay = branch_assignments.get(block_id, BayType.BAY_35A)
        if assigned_bay == BayType.BAY_35A:
            block_end_seconds = ct_branch_a[i + 1, 2]  # ✅ 수정: 인덱스 2 (마지막 공정)
        else:
            block_end_seconds = ct_branch_b[i + 1, 2]  # ✅ 수정: 인덱스 2 (마지막 공정)
        
        block_schedules.append({
            'block_id': block_id,
            'sequence_index': i,
            'start_seconds': block_start_seconds,
            'end_seconds': block_end_seconds,
            'duration_seconds': block_end_seconds - block_start_seconds,
            'assigned_bay': assigned_bay.value
        })
    
    # ✅ 모든 정보를 하나의 딕셔너리로 반환
    detailed_result = {
        'total_makespan': max_completion_time,
        'block_schedules': block_schedules,
        'sequence': sequence,
        'branch_assignments': branch_assignments,
        'num_blocks': num_blocks,
        # CT_table 정보 (디버깅용)
        'ct_tables': {
            'ct_common': ct_common,
            'ct_branch_a': ct_branch_a,
            'ct_branch_b': ct_branch_b
        },
        'final_machine_state': {
            'common_times': [ct_common[num_blocks, j] for j in range(1, 6)],  # 마지막 행의 공통 공정
            'branch_a_times': [ct_branch_a[num_blocks, j] for j in range(3)],  # 마지막 행의 분기A
            'branch_b_times': [ct_branch_b[num_blocks, j] for j in range(3)],  # 마지막 행의 분기B
            'total_completion_time': max_completion_time
        }
    }
    
    return max_completion_time, detailed_result
