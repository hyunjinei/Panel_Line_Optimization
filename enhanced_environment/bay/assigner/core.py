# bay_assigner.py
# [AGENT-ADD] Split bay assignment logic into bay/assigner/core.py.

from enhanced_environment.models import BayType, PortStarboard, EnhancedBlock
from enhanced_environment.constraints import ConstraintConfig
from enhanced_environment.constraints.managers import BayStateTracker, PSBlockManager

def _finalize_analysis(analysis: dict, bay_tracker: BayStateTracker, blocks_dict: dict):
    """분석 정보 최종 정리"""
    # 베이 할당 후 상태 정보 추가
    analysis['bay_35a_worktime_after'] = bay_tracker.bay_35a_worktime
    analysis['bay_36b_worktime_after'] = bay_tracker.bay_36b_worktime
    analysis['bay_35a_count_after'] = bay_tracker.bay_35a_block_count
    analysis['bay_36b_count_after'] = bay_tracker.bay_36b_block_count
    analysis['bay_35a_longi_total_after'] = bay_tracker.bay_35a_longi_total
    analysis['bay_36b_longi_total_after'] = bay_tracker.bay_36b_longi_total
    
    # 시간 차이 계산
    processing_time_seconds = sum(blocks_dict[analysis['block_id']].processing_times) * 60
    analysis['processing_time_seconds'] = processing_time_seconds
    analysis['processing_time_hours'] = round(processing_time_seconds / 3600, 2)

def _update_bay_state_after_assignment(
    block: EnhancedBlock,
    assigned_bay: BayType,
    bay_tracker: BayStateTracker,
    logger,
    update_tracker: bool = True,
):
    """
    베이 할당 후 자동으로 bay_tracker 상태 업데이트
    
    Args:
        block: 할당된 블록
        assigned_bay: 할당된 베이
        bay_tracker: 베이 상태 트래커
        logger: 로거 객체
    """
    # 처리 시간 계산 (분 → 초)
    processing_time_seconds = sum(block.processing_times) * 60
    
    if not update_tracker:
        return

    # bay_tracker 상태 업데이트 (위반사항은 무시하고 상태만 업데이트)
    try:
        bay_tracker.assign_bay(block, assigned_bay, processing_time_seconds)
    except Exception as e:
        # 상태 업데이트 실패해도 베이 할당은 유지
        logger.warning(f"베이 상태 업데이트 실패 (블록 {block.block_id}): {e}")
        
        # 최소한 worktime과 count는 업데이트
        if assigned_bay == BayType.BAY_35A:
            bay_tracker.bay_35a_worktime += processing_time_seconds
            bay_tracker.bay_35a_block_count += 1
        else:
            bay_tracker.bay_36b_worktime += processing_time_seconds
            bay_tracker.bay_36b_block_count += 1


def assign_fixed_bay(
    block: EnhancedBlock,
    assigned_bay: BayType,
    bay_tracker: BayStateTracker,
    blocks_dict: dict,
    logger,
    return_analysis: bool = False,
    update_tracker: bool = True,
    final_reason: str = "MANUAL_FIXED_BAY",
):
    """
    # [AGENT-EDIT] Explicit bay assignment helper for interactive/manual scheduling.
    # 원본 자동 배정 로직은 유지하고, 외부 협상 결과를 env.step 경로에 안전하게 주입할 때만 사용한다.
    """
    analysis = {
        'block_id': block.block_id,
        'assembly_type': block.assembly_type.value,
        'port_starboard': block.port_starboard.value,
        'width_m': round(block.width, 1),
        'longi_count': block.longi_count,
        'seam_count': block.seam_count,
        'c_seam_count': getattr(block, 'c_seam_count', 0),
        'main_plate_count': block.main_plate_count,
        'has_curved_plate': getattr(block, 'has_curved_plate', False),
        'material_type': block.material_type.value,
        'is_fab': block.is_fab,
        'is_draft': block.is_draft,
        'is_cross_seam': block.is_cross_seam,
        'constraint_checks': {},
        'assigned_bay': assigned_bay.value,
        'final_reason': final_reason,
        'bay_35a_worktime_before': bay_tracker.bay_35a_worktime,
        'bay_36b_worktime_before': bay_tracker.bay_36b_worktime,
        'bay_35a_longi_total_before': bay_tracker.bay_35a_longi_total,
        'bay_36b_longi_total_before': bay_tracker.bay_36b_longi_total,
        'bay_35a_count_before': bay_tracker.bay_35a_block_count,
        'bay_36b_count_before': bay_tracker.bay_36b_block_count,
        'manual_assignment': True,
    }
    _update_bay_state_after_assignment(block, assigned_bay, bay_tracker, logger, update_tracker)
    if return_analysis:
        _finalize_analysis(analysis, bay_tracker, blocks_dict)
        return assigned_bay, analysis
    return assigned_bay

def auto_assign_bay(
    block: EnhancedBlock,
    constraint_config: ConstraintConfig,
    bay_tracker: BayStateTracker,
    ps_manager: PSBlockManager,
    blocks_dict: dict,
    logger,
    return_analysis: bool = False,
    update_tracker: bool = True,
):
    """
    P7 제약조건 기반 자동 베이 할당 (우선순위 순) + 자동 상태 업데이트
    
    우선순위:
    1. P7#2: 폭 21m 이상 → B베이 (최우선)
    2. P7#3,4: P/S 쌍(론d지<7) → 동일 베이 & Port 우선
    3. P7#7,8: 연속성 제약 확인
    4. P7#10: 론지 30+ → A베이, P7#11: 10번 블록 → B베이
    5. P7#1: 부하 균등 (최종 결정)
    
    ✅ 베이 할당 후 자동으로 bay_tracker 상태 업데이트
    
    Args:
        block: 베이 할당할 블록
        constraint_config: 제약조건 설정
        bay_tracker: 베이 상태 트래커
        ps_manager: P/S 매니저
        blocks_dict: 전체 블록 딕셔너리
        logger: 로거 객체
        return_analysis: True면 상세 분석 정보도 함께 반환
        
    Returns:
        return_analysis=False: BayType
        return_analysis=True: (BayType, analysis_dict)
    """
    violations = []
    
    # ✅ 상세 분석 정보 초기화
    analysis = {
        'block_id': block.block_id,
        'assembly_type': block.assembly_type.value,
        'port_starboard': block.port_starboard.value,
        'width_m': round(block.width, 1),
        'longi_count': block.longi_count,
        'seam_count': block.seam_count,
        'c_seam_count': getattr(block, 'c_seam_count', 0),
        'main_plate_count': block.main_plate_count,
        ################################################################################################################################################################################################
        # fix: Routing 상위 제약 (곡판/고심수 간격 & 후공정 착수 순서)
        ################################################################################################################################################################################################
        'has_curved_plate': getattr(block, 'has_curved_plate', False),
        'material_type': block.material_type.value,
        'is_fab': block.is_fab,
        'is_draft': block.is_draft,
        'is_cross_seam': block.is_cross_seam,
        'constraint_checks': {},
        'assigned_bay': None,
        'final_reason': None,
        'bay_35a_worktime_before': bay_tracker.bay_35a_worktime,
        'bay_36b_worktime_before': bay_tracker.bay_36b_worktime,
        'bay_35a_longi_total_before': bay_tracker.bay_35a_longi_total,
        'bay_36b_longi_total_before': bay_tracker.bay_36b_longi_total,
        'bay_35a_count_before': bay_tracker.bay_35a_block_count,
        'bay_36b_count_before': bay_tracker.bay_36b_block_count
    }

    # ✅ 1순위: P7#3,4 P/S 쌍 동일 베이 (조건: 둘 다 론지<7)
    ps_small_pair_condition = False
    required_bay = None
    
    # 🔥 핵심 수정: P/S small 쌍 정확한 감지 (둘 다 론지 < 7개)
    if blocks_dict:
        ps_small_pair_condition = block.is_ps_small_pair(blocks_dict)
        
        # 🔍 BLK_57 디버깅 (P와 S 둘 다)
        # if 'BLK_57' in str(block.block_name):
            # print(f"🔍 {block.block_name} 디버깅 - blocks_dict 방식:")
            # print(f"   is_ps_small_pair() 결과: {ps_small_pair_condition}")
            # if block.pair_block_id and block.pair_block_id in blocks_dict:
                # pair_block = blocks_dict[block.pair_block_id]
                # print(f"   {block.block_name} 론지: {block.longi_count} < 7 = {block.longi_count < 7}")
                # print(f"   {pair_block.block_name} 론지: {pair_block.longi_count} < 7 = {pair_block.longi_count < 7}")
                # print(f"   둘 다 < 7: {block.longi_count < 7 and pair_block.longi_count < 7}")
    
    if ps_small_pair_condition:
        # P 블록인 경우: P/S small 쌍은 무조건 B베이 우선
        if block.port_starboard == PortStarboard.PORT:
            # 🚨 제약조건 최우선: P/S small 쌍은 무조건 B베이
            required_bay = BayType.BAY_36B
            
            # P 블록 베이 할당 기록 (S 블록이 따라갈 수 있도록)
            # 🔧 동기화 문제 해결: ps_manager.bay_assignments에 저장
            if required_bay and update_tracker:
                ps_manager.bay_assignments[block.block_id] = required_bay
        
        # S 블록인 경우: P 블록과 동일한 베이로
        else:
            required_bay = ps_manager.get_required_bay_for_starboard(block)
            # 🚨 P블록이 없으면 B베이로 fallback (제약조건 준수)
            if not required_bay:
                required_bay = BayType.BAY_36B
    
    # 🔍 BLK_57 디버깅 (최종 결과)  
    # if 'BLK_57' in str(block.block_name):
        # print(f"🔍 {block.block_name} 디버깅 - 1순위 결과:")
        # print(f"   ps_small_pair_condition: {ps_small_pair_condition}")
        # print(f"   required_bay: {required_bay}")
        # if required_bay:
            # print(f"   → 1순위에서 {required_bay.value}베이 강제 할당됨!")
        # else:
            # print(f"   → 1순위 통과, 2순위로 이동")
    
    analysis['constraint_checks']['P7#3_4_ps_pair'] = {
        'enabled': constraint_config.is_constraint_enabled("P7#3"),
        'condition': f"P/S small쌍 (둘 다 론지<7): 현재블록 론지{block.longi_count}",
        'is_ps_pair': block.is_p_s_pair(),
        'is_ps_small_pair': ps_small_pair_condition,
        'longi_count': block.longi_count,
        'pair_block_id': block.pair_block_id,
        'required_bay': required_bay.value if required_bay else None,
        'result': 'FORCE_PAIR' if required_bay else 'PASS',
        'reason': f"P/S small 쌍 동일베이 → {required_bay.value}베이" if required_bay else "P/S small 쌍 조건 미충족"
    }

    # ==== [AGENT-EDIT BEGIN: enforce P7#3,#4 before soft bay preferences] ====
    if required_bay and (
        constraint_config.is_constraint_enabled("P7#3")
        or constraint_config.is_constraint_enabled("P7#4")
    ):
        assigned_bay = required_bay
        analysis['assigned_bay'] = assigned_bay.value
        analysis['final_reason'] = f"P7#3,#4 P/S small 쌍 동일베이 강제 → {assigned_bay.value}"
        _update_bay_state_after_assignment(block, assigned_bay, bay_tracker, logger, update_tracker)
        if return_analysis:
            _finalize_analysis(analysis, bay_tracker, blocks_dict)
            return assigned_bay, analysis
        return assigned_bay
    # ==== [AGENT-EDIT END] ====
    
    # ✅ 2순위: P7#2 물리적 제약 (21m 초과 → B베이)
    p7_2_check = constraint_config.is_constraint_enabled("P7#2") and block.width > 21.0
    analysis['constraint_checks']['P7#2_physical'] = {
        'enabled': constraint_config.is_constraint_enabled("P7#2"),
        'condition': f"폭 {block.width}m > 21m",
        'result': 'FORCE_B' if p7_2_check else 'PASS',
        'reason': f"21m 초과 → B베이 강제" if p7_2_check else f"폭 {block.width}m ≤ 21m"
    }
    
    if p7_2_check:
        # 21m 초과는 무조건 B베이
        assigned_bay = BayType.BAY_36B
        analysis['assigned_bay'] = assigned_bay.value
        analysis['final_reason'] = f"P7#2 물리적 제약: 폭 {block.width}m > 21m → B베이 강제"
        _update_bay_state_after_assignment(block, assigned_bay, bay_tracker, logger, update_tracker)
        
        # 🔍 BLK_57 디버깅 (2순위)
        # if 'BLK_57' in str(block.block_name):
            # print(f"🔍 {block.block_name} 디버깅 - 2순위에서 B베이 강제!")
        
        if return_analysis:
            _finalize_analysis(analysis, bay_tracker, blocks_dict)
            return assigned_bay, analysis
        return assigned_bay
    
    # 🔍 BLK_57 디버깅 (2순위 통과)
    # if 'BLK_57' in str(block.block_name):
        # print(f"🔍 {block.block_name} 디버깅 - 2순위 통과: 폭 {block.width}m ≤ 21m")
    
    
    # ✅ 3순위: P7#7,8 연속성 제약 확인
    # A베이 가능 여부 확인
    can_assign_35a, reason_35a = bay_tracker.can_assign_bay(block, BayType.BAY_35A)
    can_assign_36b, reason_36b = bay_tracker.can_assign_bay(block, BayType.BAY_36B)
    
    # 🔍 BLK_57 디버깅 (3순위 연속성 체크)
    # if 'BLK_57' in str(block.block_name):
        # print(f"🔍 {block.block_name} 디버깅 - 3순위 연속성 체크:")
        # print(f"   can_assign_35a: {can_assign_35a}, reason: {reason_35a}")
        # print(f"   can_assign_36b: {can_assign_36b}, reason: {reason_36b}")
    
    analysis['constraint_checks']['P7#7_8_consecutive'] = {
        'enabled': constraint_config.is_constraint_enabled("P7#7"),
        'can_assign_35a': can_assign_35a,
        'can_assign_36b': can_assign_36b,
        'reason_35a': reason_35a,
        'reason_36b': reason_36b,
        'result': 'PASS' if (can_assign_35a and can_assign_36b) else 'RESTRICTED',
        'restriction_reason': f"35A: {reason_35a}, 36B: {reason_36b}" if not (can_assign_35a and can_assign_36b) else "모든 베이 가능"
    }
    
    # 연속성 제약으로 선택지 제한되는 경우
    if not can_assign_35a and "P7#7" in reason_35a:
        if can_assign_36b:
            assigned_bay = BayType.BAY_36B
            analysis['assigned_bay'] = assigned_bay.value
            analysis['final_reason'] = f"P7#7 연속성 제약: A베이 연속 제한 → B베이 강제"
            _update_bay_state_after_assignment(block, assigned_bay, bay_tracker, logger, update_tracker)
            
            # 🔍 BLK_57 디버깅 (3순위 A베이 제한)
            # if 'BLK_57' in str(block.block_name):
                # print(f"🔍 {block.block_name} 디버깅 - 3순위에서 B베이 강제! (A베이 연속 제한)")
            
            if return_analysis:
                _finalize_analysis(analysis, bay_tracker, blocks_dict)
                return assigned_bay, analysis
            return assigned_bay
    
    if not can_assign_36b and "P7#7" in reason_36b:
        if can_assign_35a:
            assigned_bay = BayType.BAY_35A
            analysis['assigned_bay'] = assigned_bay.value
            analysis['final_reason'] = f"P7#7 연속성 제약: B베이 연속 제한 → A베이 강제"
            _update_bay_state_after_assignment(block, assigned_bay, bay_tracker, logger, update_tracker)
            
            # 🔍 BLK_57 디버깅 (3순위 B베이 제한)
            # if 'BLK_57' in str(block.block_name):
                # print(f"🔍 {block.block_name} 디버깅 - 3순위에서 A베이 강제! (B베이 연속 제한)")
            
            if return_analysis:
                _finalize_analysis(analysis, bay_tracker, blocks_dict)
                return assigned_bay, analysis
            return assigned_bay
    
    # 🔍 BLK_57 디버깅 (3순위 통과)
    # if 'BLK_57' in str(block.block_name):
        # print(f"🔍 {block.block_name} 디버깅 - 3순위 통과: 연속성 제약 없음")
    
    # ✅ 4순위: P7#10, P7#11 특별 제약
    # P7#11: 10번 블록 특별 처리
    p7_11_condition = (hasattr(block, 'block_number') and block.block_number == 10)
    analysis['constraint_checks']['P7#11_special_block'] = {
        'enabled': constraint_config.is_constraint_enabled("P7#11"),
        'condition': f"블록번호 = {getattr(block, 'block_number', 'N/A')}",
        'is_block_10': p7_11_condition,
        'result': 'FORCE_B' if (constraint_config.is_constraint_enabled("P7#11") and p7_11_condition and can_assign_36b) else 'PASS',
        'reason': "10번 블록 → B베이" if p7_11_condition else "10번 블록 아님"
    }
    
    if (constraint_config.is_constraint_enabled("P7#11") and p7_11_condition and can_assign_36b):
        assigned_bay = BayType.BAY_36B
        analysis['assigned_bay'] = assigned_bay.value
        analysis['final_reason'] = f"P7#11 특별 제약: 10번 블록 → B베이"
        _update_bay_state_after_assignment(block, assigned_bay, bay_tracker, logger, update_tracker)
        
        if return_analysis:
            _finalize_analysis(analysis, bay_tracker, blocks_dict)
            return assigned_bay, analysis
        return assigned_bay
    
    # P7#10: 론지 30+ → A베이 우선
    p7_10_condition = (block.longi_count >= 30)
    analysis['constraint_checks']['P7#10_longi_30plus'] = {
        'enabled': constraint_config.is_constraint_enabled("P7#10"),
        'condition': f"론지 {block.longi_count}개 ≥ 30개",
        'longi_count': block.longi_count,
        'result': 'PREFER_A' if (constraint_config.is_constraint_enabled("P7#10") and p7_10_condition) else 'PASS',
        'reason': f"론지 {block.longi_count}개 ≥ 30개 → A베이 우선" if p7_10_condition else f"론지 {block.longi_count}개 < 30개"
    }
    
    if (constraint_config.is_constraint_enabled("P7#10") and p7_10_condition):
        if can_assign_35a:
            assigned_bay = BayType.BAY_35A
            analysis['assigned_bay'] = assigned_bay.value
            analysis['final_reason'] = f"P7#10 론지 우선: 론지 {block.longi_count}개 ≥ 30개 → A베이 우선"
            _update_bay_state_after_assignment(block, assigned_bay, bay_tracker, logger, update_tracker)
            
            if return_analysis:
                _finalize_analysis(analysis, bay_tracker, blocks_dict)
                return assigned_bay, analysis
            return assigned_bay
        elif can_assign_36b:
            assigned_bay = BayType.BAY_36B
            analysis['assigned_bay'] = assigned_bay.value
            analysis['final_reason'] = f"P7#10 론지 우선: 론지 {block.longi_count}개 ≥ 30개인데 A베이 불가 → B베이"
            _update_bay_state_after_assignment(block, assigned_bay, bay_tracker, logger, update_tracker)
            
            if return_analysis:
                _finalize_analysis(analysis, bay_tracker, blocks_dict)
                return assigned_bay, analysis
            return assigned_bay
    
    # ✅ 5순위: P7#1 부하 균등 (Greedy 전략)
    use_longi_balance = getattr(constraint_config, "enable_longi_load_balance", False)

    if use_longi_balance:
        bay_35a_load = bay_tracker.bay_35a_longi_total
        bay_36b_load = bay_tracker.bay_36b_longi_total
        load_label = "론지"

        def _format_load(val: float) -> str:
            return f"{int(val)}ea"

        load_diff = abs(bay_35a_load - bay_36b_load)
    else:
        bay_35a_load = bay_tracker.bay_35a_worktime
        bay_36b_load = bay_tracker.bay_36b_worktime
        load_label = "작업시간"

        def _format_load(val: float) -> str:
            return f"{round(val / 3600, 2)}h"

        load_diff = abs(bay_35a_load - bay_36b_load)
    
    # 🔍 BLK_57 디버깅 (5순위 부하균등)
    # if 'BLK_57' in str(block.block_name):
        # print(f"🔍 {block.block_name} 디버깅 - 5순위 부하균등:")
        # print(f"   35A 작업시간: {round(bay_35a_worktime / 3600, 2)}h")
        # print(f"   36B 작업시간: {round(bay_36b_worktime / 3600, 2)}h")
        # print(f"   선호 베이: {'35A' if bay_35a_worktime <= bay_36b_worktime else '36B'}")
    
    worktime_diff_hours = round(abs(bay_tracker.bay_35a_worktime - bay_tracker.bay_36b_worktime) / 3600, 2)

    analysis['constraint_checks']['P7#1_load_balance'] = {
        'enabled': constraint_config.is_constraint_enabled("P7#1"),
        'load_metric': 'longi_count' if use_longi_balance else 'worktime',
        'bay_35a_longi_total': bay_tracker.bay_35a_longi_total,
        'bay_36b_longi_total': bay_tracker.bay_36b_longi_total,
        'bay_35a_worktime_hours': round(bay_tracker.bay_35a_worktime / 3600, 2),
        'bay_36b_worktime_hours': round(bay_tracker.bay_36b_worktime / 3600, 2),
        'worktime_diff_hours': worktime_diff_hours,
        'load_diff': load_diff,
        'load_diff_display': _format_load(load_diff),
        'prefer_bay': '35A' if bay_35a_load <= bay_36b_load else '36B',
        'result': 'BALANCE_DECISION',
        'reason': f"부하균등({load_label}): 35A({_format_load(bay_35a_load)}) vs 36B({_format_load(bay_36b_load)})"
    }

    if constraint_config.is_constraint_enabled("P7#1"):
        # 부하가 적은 베이 우선 (부하 균등)
        if bay_35a_load <= bay_36b_load:
            if can_assign_35a:
                assigned_bay = BayType.BAY_35A
                analysis['assigned_bay'] = assigned_bay.value
                analysis['final_reason'] = (
                    f"P7#1 부하균등({load_label}): 35A({_format_load(bay_35a_load)}) ≤ "
                    f"36B({_format_load(bay_36b_load)}) → A베이 선택"
                )
                _update_bay_state_after_assignment(block, assigned_bay, bay_tracker, logger, update_tracker)

                # 🔍 BLK_57 디버깅 (5순위 A베이 선택)
                # if 'BLK_57' in str(block.block_name):
                    # print(f"🔍 {block.block_name} 디버깅 - 5순위에서 A베이 선택! (부하균등)")
                
                if return_analysis:
                    _finalize_analysis(analysis, bay_tracker, blocks_dict)
                    return assigned_bay, analysis
                return assigned_bay
            elif can_assign_36b:
                assigned_bay = BayType.BAY_36B
                analysis['assigned_bay'] = assigned_bay.value
                analysis['final_reason'] = (
                    f"P7#1 부하균등({load_label}): A베이 선호하지만 불가 → B베이"
                )
                _update_bay_state_after_assignment(block, assigned_bay, bay_tracker, logger, update_tracker)
                
                # 🔍 BLK_57 디버깅 (5순위 A베이 불가로 B베이)
                # if 'BLK_57' in str(block.block_name):
                    # print(f"🔍 {block.block_name} 디버깅 - 5순위에서 B베이 선택! (A베이 선호하지만 불가)")
                
                if return_analysis:
                    _finalize_analysis(analysis, bay_tracker, blocks_dict)
                    return assigned_bay, analysis
                return assigned_bay
        else:
            if can_assign_36b:
                assigned_bay = BayType.BAY_36B
                analysis['assigned_bay'] = assigned_bay.value
                analysis['final_reason'] = (
                    f"P7#1 부하균등({load_label}): 36B({_format_load(bay_36b_load)}) < "
                    f"35A({_format_load(bay_35a_load)}) → B베이 선택"
                )
                _update_bay_state_after_assignment(block, assigned_bay, bay_tracker, logger, update_tracker)
                
                # 🔍 BLK_57 디버깅 (5순위 B베이 선호)
                # if 'BLK_57' in str(block.block_name):
                    # print(f"🔍 {block.block_name} 디버깅 - 5순위에서 B베이 선택! (B베이 작업시간 적음)")
                
                if return_analysis:
                    _finalize_analysis(analysis, bay_tracker, blocks_dict)
                    return assigned_bay, analysis
                return assigned_bay
            elif can_assign_35a:
                assigned_bay = BayType.BAY_35A
                analysis['assigned_bay'] = assigned_bay.value
                analysis['final_reason'] = (
                    f"P7#1 부하균등({load_label}): B베이 선호하지만 불가 → A베이"
                )
                _update_bay_state_after_assignment(block, assigned_bay, bay_tracker, logger, update_tracker)
                
                # 🔍 BLK_57 디버깅 (5순위 B베이 불가로 A베이)
                # if 'BLK_57' in str(block.block_name):
                    #  print(f"🔍 {block.block_name} 디버깅 - 5순위에서 A베이 선택! (B베이 선호하지만 불가)")
                
                if return_analysis:
                    _finalize_analysis(analysis, bay_tracker, blocks_dict)
                    return assigned_bay, analysis
                return assigned_bay
    
    # ✅ 최종 fallback: 가능한 베이 중 아무거나
    if can_assign_35a:
        assigned_bay = BayType.BAY_35A
        analysis['assigned_bay'] = assigned_bay.value
        analysis['final_reason'] = f"Fallback: A베이 가능 → A베이"
        _update_bay_state_after_assignment(block, assigned_bay, bay_tracker, logger, update_tracker)
        
        if return_analysis:
            _finalize_analysis(analysis, bay_tracker, blocks_dict)
            return assigned_bay, analysis
        return assigned_bay
    elif can_assign_36b:
        assigned_bay = BayType.BAY_36B
        analysis['assigned_bay'] = assigned_bay.value
        analysis['final_reason'] = f"Fallback: B베이만 가능 → B베이"
        _update_bay_state_after_assignment(block, assigned_bay, bay_tracker, logger, update_tracker)
        
        if return_analysis:
            _finalize_analysis(analysis, bay_tracker, blocks_dict)
            return assigned_bay, analysis
        return assigned_bay
    else:
        # 모든 베이 불가능: 강제로 B베이 할당 (시스템 안정성)
        assigned_bay = BayType.BAY_36B
        analysis['assigned_bay'] = assigned_bay.value
        analysis['final_reason'] = f"Emergency: 모든 베이 불가능 → B베이 강제"
        _update_bay_state_after_assignment(block, assigned_bay, bay_tracker, logger, update_tracker)
        
        if return_analysis:
            _finalize_analysis(analysis, bay_tracker, blocks_dict)
            return assigned_bay, analysis
        return assigned_bay


def preview_assign_bay(
    block: EnhancedBlock,
    constraint_config: ConstraintConfig,
    bay_tracker: BayStateTracker,
    ps_manager: PSBlockManager,
    blocks_dict: dict,
    logger,
    return_analysis: bool = False,
):
    """상태를 변경하지 않는 베이 배정 미리보기"""
    # [AGENT-ADD] RL 후보 평가 등에서 베이 상태를 보존하기 위한 무 side-effect 헬퍼
    return auto_assign_bay(
        block=block,
        constraint_config=constraint_config,
        bay_tracker=bay_tracker,
        ps_manager=ps_manager,
        blocks_dict=blocks_dict,
        logger=logger,
        return_analysis=return_analysis,
        update_tracker=False,
    )
