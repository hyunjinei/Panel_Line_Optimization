# csv_save.py

import pandas as pd
from typing import List, Dict

def save_detailed_masking_info(date_key: str, daily_step_info: List[Dict], blocks_in_date: List):
    """
    날짜별 Action Masking 상세 분석 정보 CSV 저장
    
    Args:
        date_key: 날짜 키 (예: '20250609')
        daily_step_info: 스텝별 상세 정보 리스트
        blocks_in_date: 해당 날짜의 모든 블록들
    """
    filename = f"detailed_actionmasking_schedule_info_{date_key}.csv"
    
    # ✅ CSV 데이터 구성
    csv_data = []
    
    # 첫 번째 줄: 메타데이터
    csv_data.append({
        'type': 'METADATA',
        'date': date_key,
        'total_blocks': len(blocks_in_date),
        'total_steps': len(daily_step_info),
        'step': '',
        'block_id': '',
        'assembly_type': '',
        'port_starboard': '',
        'status': '',
        'reason': '',
        'P5#11_check': '',
        'P5#3_check': '',
        'P6#4_check': '',
        'P6#time_check': '',
        'selected_block': '',
        'available_count': '',
        'excluded_count': '',
        'primary_constraint': '',
        # [AGENT-ADD] 완화 단계/대상 요약 컬럼
        'relax_level': '',
        'relax_stage': '',
        'relax_constraints': '',
        'relax_target_count': '',
        'relax_targets': '',
        'relaxed_block_count': '',
        'relax_target_total': '',
        # [AGENT-ADD] 최후 선택(최소 위반) 기록
        'final_choice_flag': '',
        'final_violation_count': '',
        'final_violation_list': '',
        'seam_count': '',
        'c_seam_count': '',
        ######################################################################################################################################################################################
        #####                                                                 fix: 곡판 여부 CSV 기록                                                                                         #####
        ######################################################################################################################################################################################
        'has_curved_plate': ''
    })
    
    # 각 스텝별 상세 정보
    for step_info in daily_step_info:
        step = step_info['step']
        
        # 스텝 요약 정보
        available_types = {}
        excluded_types = {}
        
        for analysis in step_info['block_analysis']:
            assembly_type = analysis['assembly_type']
            if analysis['is_available']:
                available_types[assembly_type] = available_types.get(assembly_type, 0) + 1
            else:
                excluded_types[assembly_type] = excluded_types.get(assembly_type, 0) + 1

        # [AGENT-ADD] 완화 적용된 후보 요약
        relaxed_blocks = [
            analysis for analysis in step_info['block_analysis']
            if analysis.get('is_available') and analysis.get('relax_target_count', 0) > 0
        ]
        relaxed_block_count = len(relaxed_blocks)
        relax_target_total = sum(analysis.get('relax_target_count', 0) for analysis in relaxed_blocks)
        final_choice_count = sum(1 for analysis in step_info['block_analysis'] if analysis.get('final_choice_flag'))
        
        # 주요 제약조건 파악
        primary_constraints = []
        for analysis in step_info['block_analysis']:
            if not analysis['is_available'] and analysis['exclusion_reason']:
                if 'P5#11' in analysis['exclusion_reason']:
                    primary_constraints.append('P5#11')
                elif 'P5#3' in analysis['exclusion_reason']:
                    primary_constraints.append('P5#3,4')
                elif 'P6#4' in analysis['exclusion_reason']:
                    primary_constraints.append('P6#4')
        
        primary_constraint = ', '.join(set(primary_constraints)) or '없음'
        
        # 스텝 요약 줄
        csv_data.append({
            'type': 'STEP_SUMMARY',
            'date': date_key,
            'total_blocks': len(blocks_in_date),
            'total_steps': len(daily_step_info),
            'step': step,
            'block_id': f"선택: {step_info.get('selected_block_id', 'N/A')}",
            'assembly_type': step_info.get('selected_assembly_type', ''),
            'port_starboard': '',
            'status': f"가용: {step_info['available_count']}, 제외: {step_info['excluded_count']}",
            'reason': f"available_types: {available_types}, excluded_types: {excluded_types}",
            'P5#11_check': '',
            'P5#3_check': '',
            'P6#4_check': '',
            'P6#time_check': '',
            'selected_block': step_info.get('selected_block_id', ''),
            'available_count': step_info['available_count'],
            'excluded_count': step_info['excluded_count'],
            'primary_constraint': primary_constraint,
            'relax_level': '',
            'relax_stage': '',
            'relax_constraints': '',
            'relax_target_count': '',
            'relax_targets': '',
            'relaxed_block_count': relaxed_block_count,
            'relax_target_total': relax_target_total,
            'final_choice_flag': final_choice_count,
            'final_violation_count': '',
            'final_violation_list': '',
            'seam_count': '',
            'c_seam_count': '',
            'has_curved_plate': ''
        })
        
        # 각 블록별 상세 분석
        for analysis in step_info['block_analysis']:
            csv_data.append({
                'type': 'BLOCK_DETAIL',
                'date': date_key,
                'total_blocks': len(blocks_in_date),
                'total_steps': len(daily_step_info),
                'step': step,
                'block_id': analysis['block_id'],
                'assembly_type': analysis['assembly_type'],
                'port_starboard': analysis['port_starboard'],
                'status': 'AVAILABLE' if analysis['is_available'] else 'EXCLUDED',
                'reason': analysis['inclusion_reason'] if analysis['is_available'] else analysis['exclusion_reason'],
                'P5#11_check': analysis['constraint_checks'].get('P5#11_assembly_mixing', {}).get('result', ''),
                'P5#3_check': analysis['constraint_checks'].get('P5#3_ps_order', {}).get('result', ''),
                'P6#4_check': analysis['constraint_checks'].get('P6#4_cross_seam', {}).get('result', ''),
                'P6#time_check': analysis['constraint_checks'].get('P6#1_2_3_saw_time', {}).get('result', ''),
                'selected_block': '★' if analysis['block_id'] == step_info.get('selected_block_id') else '',
                'available_count': '',
                'excluded_count': '',
                'primary_constraint': '',
                'relax_level': analysis.get('relax_level', ''),
                'relax_stage': analysis.get('relax_stage', ''),
                'relax_constraints': ', '.join(analysis.get('relax_constraints', []) or []),
                'relax_target_count': analysis.get('relax_target_count', 0),
                'relax_targets': ', '.join(analysis.get('relax_targets', []) or []),
                'relaxed_block_count': '',
                'relax_target_total': '',
                'final_choice_flag': analysis.get('final_choice_flag', False),
                'final_violation_count': analysis.get('final_violation_count', 0),
                'final_violation_list': ', '.join(analysis.get('final_violation_list', []) or []),
                'seam_count': analysis.get('seam_count', ''),
                'c_seam_count': analysis.get('c_seam_count', ''),
                'has_curved_plate': analysis.get('has_curved_plate', False)
            })
    
    # ✅ CSV 저장
    df = pd.DataFrame(csv_data)
    df.to_csv(filename, index=False, encoding='utf-8-sig')
    
    # print(f"   📄 상세 분석 저장: {filename} ({len(csv_data)}개 레코드)")
    # print(f"      • 메타데이터: 1개")
    # print(f"      • 스텝 요약: {len(daily_step_info)}개")
    # print(f"      • 블록 분석: {sum(len(step['block_analysis']) for step in daily_step_info)}개")


def save_detailed_bay_selection_info(bay_analyses_by_date: Dict[str, List[Dict]]):
    """
    베이 선택 상세 분석 정보를 날짜별 CSV로 저장
    
    Args:
        bay_analyses_by_date: 날짜별 베이 분석 정보
    """
    
    for date_str, bay_analyses in bay_analyses_by_date.items():
        filename = f'detailed_actionmasking_bayselect_info_{date_str}.csv'
        
        all_data = []
        
        # ✅ 날짜별 메타데이터
        all_data.append({
            'type': 'METADATA',
            'date': date_str,
            'total_blocks': len(bay_analyses),
            'block_id': None,
            'width_m': None,
            'longi_count': None,
            'seam_count': None,
            'c_seam_count': None,
            'has_curved_plate': None,
            'assigned_bay': None,
            'final_reason': f"날짜별 베이 선택 분석: {len(bay_analyses)}개 블록"
        })
        
        # ✅ 블록별 베이 선택 상세 정보
        for analysis in bay_analyses:
            row_data = {
                'type': 'BLOCK_DETAIL',
                'date': date_str,
                'total_blocks': len(bay_analyses),
                'block_id': analysis['block_id'],
                'width_m': analysis['width_m'],
                'longi_count': analysis['longi_count'],
                'seam_count': analysis.get('seam_count', ''),
                'c_seam_count': analysis.get('c_seam_count', ''),
                'has_curved_plate': analysis.get('has_curved_plate', False),
                'material_type': analysis['material_type'],
                'assigned_bay': analysis['assigned_bay'],
                'final_reason': analysis['final_reason'],
                'P7#2_result': analysis['constraint_checks'].get('P7#2_physical', {}).get('result', ''),
                'P7#3_4_result': analysis['constraint_checks'].get('P7#3_4_ps_pair', {}).get('result', ''),
                'P7#7_8_result': analysis['constraint_checks'].get('P7#7_8_consecutive', {}).get('result', ''),
                'P7#10_result': analysis['constraint_checks'].get('P7#10_longi_30plus', {}).get('result', ''),
                'P7#11_result': analysis['constraint_checks'].get('P7#11_special_block', {}).get('result', ''),
                'P7#1_result': analysis['constraint_checks'].get('P7#1_load_balance', {}).get('result', ''),
                'bay_35a_worktime_before': round(analysis.get('bay_35a_worktime_before', 0) / 3600, 2),
                'bay_36b_worktime_before': round(analysis.get('bay_36b_worktime_before', 0) / 3600, 2),
                'bay_35a_worktime_after': round(analysis.get('bay_35a_worktime_after', 0) / 3600, 2),
                'bay_36b_worktime_after': round(analysis.get('bay_36b_worktime_after', 0) / 3600, 2)
            }
            all_data.append(row_data)
        
        # ✅ 날짜별 개별 CSV 저장
        if all_data:
            df = pd.DataFrame(all_data)
            df.to_csv(filename, index=False, encoding='utf-8-sig')
            # print(f"🏗️ {date_str} 베이 선택 상세 분석: {filename} ({len(bay_analyses)}개 블록)")
        else:
            print(f"❌ {date_str} 베이 선택 분석 데이터가 없습니다.")
    
    # print(f"✅ 총 {len(bay_analyses_by_date)}개 날짜의 베이 선택 상세 분석 완료")


def save_assembly_decoding_schedule_info(assembly_step_info: List[Dict], date_key: str, all_blocks: List = None):
    """
    Assembly Decoding 방식의 날짜별 상세 스케줄링 분석 정보 CSV 저장
    
    Args:
        date_key: 날짜 키 (예: '20250609')
        assembly_step_info: Assembly Decoding 스텝별 상세 정보 리스트
        all_blocks: 전체 블록들 (Assembly Decoding은 전체 풀에서 선택)
    """
    filename = f"detailed_assembly_schedule_info_{date_key}.csv"
    
    # ✅ CSV 데이터 구성
    csv_data = []
    
    # 첫 번째 줄: 메타데이터
    total_blocks = len(all_blocks) if all_blocks else len(assembly_step_info)
    csv_data.append({
        'type': 'METADATA',
        'date': date_key,
        'total_blocks': total_blocks,
        'total_steps': len(assembly_step_info),
        'step': '',
        'block_id': '',
        'assembly_start_date': '',
        'assembly_type': '',
        'port_starboard': '',
        'status': '',
        'reason': '',
        'assembly_masking_stage': '',
        'three_bay_check': '',
        'ps_forced': '',
        'selected_block': '',
        'available_count': '',
        'excluded_count': '',
        'emergency_mode': '',
        # [AGENT-ADD] 완화 단계/대상 요약 컬럼
        'relax_level': '',
        'relax_stage': '',
        'relax_constraints': '',
        'relax_target_count': '',
        'relax_targets': '',
        'relaxed_block_count': '',
        'relax_target_total': '',
        # [AGENT-ADD] 최후 선택(최소 위반) 기록
        'final_choice_flag': '',
        'final_violation_count': '',
        'final_violation_list': '',
        'seam_count': '',
        'c_seam_count': '',
        'has_curved_plate': ''
    })
    
    # 각 스텝별 상세 정보
    for step_info in assembly_step_info:
        step = step_info.get('step', 0)

        # [AGENT-ADD] 완화 적용된 후보 요약
        relaxed_blocks = [
            analysis for analysis in step_info.get('block_analysis', [])
            if analysis.get('is_available') and analysis.get('relax_target_count', 0) > 0
        ]
        relaxed_block_count = len(relaxed_blocks)
        relax_target_total = sum(analysis.get('relax_target_count', 0) for analysis in relaxed_blocks)
        final_choice_count = sum(1 for analysis in step_info.get('block_analysis', []) if analysis.get('final_choice_flag'))
        
        # 스텝 요약 줄
        csv_data.append({
            'type': 'STEP_SUMMARY',
            'date': date_key,
            'total_blocks': total_blocks,
            'total_steps': len(assembly_step_info),
            'step': step,
            'block_id': f"선택: {step_info.get('selected_block_id', 'N/A')}",
            'assembly_start_date': step_info.get('selected_assembly_date', ''),
            'assembly_type': step_info.get('selected_assembly_type', ''),
            'port_starboard': step_info.get('selected_port_starboard', ''),
            'status': f"가용: {step_info.get('available_count', 0)}, 제외: {step_info.get('excluded_count', 0)}",
            'reason': step_info.get('selection_summary', ''),
            'assembly_masking_stage': step_info.get('masking_stage_used', ''),
            'three_bay_check': '',
            'ps_forced': step_info.get('ps_forced', ''),
            'selected_block': step_info.get('selected_block_id', ''),
            'available_count': step_info.get('available_count', 0),
            'excluded_count': step_info.get('excluded_count', 0),
            'emergency_mode': step_info.get('emergency_mode', ''),
            'relax_level': '',
            'relax_stage': '',
            'relax_constraints': '',
            'relax_target_count': '',
            'relax_targets': '',
            'relaxed_block_count': relaxed_block_count,
            'relax_target_total': relax_target_total,
            'final_choice_flag': final_choice_count,
            'final_violation_count': '',
            'final_violation_list': '',
            'seam_count': '',
            'c_seam_count': '',
            'has_curved_plate': ''
        })
        
        # 각 블록별 상세 분석
        for analysis in step_info.get('block_analysis', []):
            is_available = analysis.get('is_available', False)
            inclusion_reason = analysis.get('inclusion_reason', '')
            exclusion_reason = analysis.get('exclusion_reason', '')
            csv_data.append({
                'type': 'BLOCK_DETAIL',
                'date': date_key,
                'total_blocks': total_blocks,
                'total_steps': len(assembly_step_info),
                'step': step,
                'block_id': analysis['block_id'],
                'assembly_start_date': analysis.get('assembly_start_date', ''),
                'assembly_type': analysis.get('assembly_type', ''),
                'port_starboard': analysis.get('port_starboard', ''),
                'status': 'AVAILABLE' if is_available else 'EXCLUDED',
                'reason': inclusion_reason if is_available else exclusion_reason,
                'assembly_masking_stage': analysis.get('masking_stage', ''),
                'three_bay_check': analysis.get('three_bay_check', ''),
                'ps_forced': analysis.get('ps_forced', ''),
                'selected_block': '★' if analysis['block_id'] == step_info.get('selected_block_id') else '',
                'available_count': '',
                'excluded_count': '',
                'emergency_mode': '',
                'relax_level': analysis.get('relax_level', ''),
                'relax_stage': analysis.get('relax_stage', ''),
                'relax_constraints': ', '.join(analysis.get('relax_constraints', []) or []),
                'relax_target_count': analysis.get('relax_target_count', 0),
                'relax_targets': ', '.join(analysis.get('relax_targets', []) or []),
                'relaxed_block_count': '',
                'relax_target_total': '',
                'final_choice_flag': analysis.get('final_choice_flag', False),
                'final_violation_count': analysis.get('final_violation_count', 0),
                'final_violation_list': ', '.join(analysis.get('final_violation_list', []) or []),
                'seam_count': analysis.get('seam_count', ''),
                'c_seam_count': analysis.get('c_seam_count', ''),
                'has_curved_plate': analysis.get('has_curved_plate', False)
            })
    
    # ✅ CSV 저장
    df = pd.DataFrame(csv_data)
    df.to_csv(filename, index=False, encoding='utf-8-sig')
    
    print(f"   📄 Assembly 상세 분석 저장: {filename} ({len(csv_data)}개 레코드)")
    return filename


def save_assembly_decoding_bay_info(bay_analyses: List[Dict], date_key: str):
    """
    Assembly Decoding 방식의 베이 선택 상세 분석 정보를 CSV로 저장
    
    Args:
        bay_analyses: 베이 분석 정보 리스트
        date_key: 날짜 키
    """
    filename = f'detailed_assembly_bayselect_info_{date_key}.csv'
    
    all_data = []
    
    # ✅ 날짜별 메타데이터
    all_data.append({
        'type': 'METADATA',
        'date': date_key,
        'total_blocks': len(bay_analyses),
        'block_id': None,
        'assembly_start_date': None,
        'width_m': None,
        'longi_count': None,
        'seam_count': None,
        'c_seam_count': None,
        'has_curved_plate': None,
        'assigned_bay': None,
        'final_reason': f"Assembly Decoding 베이 선택 분석: {len(bay_analyses)}개 블록",
        'material_type': None,
        'P7#2_result': None,
        'P7#3_4_result': None,
        'P7#7_8_result': None,
        'P7#10_result': None,
        'P7#11_result': None,
        'P7#1_result': None,
        'bay_35a_worktime_before': None,
        'bay_36b_worktime_before': None,
        'bay_35a_worktime_after': None,
        'bay_36b_worktime_after': None
    })
    
    # ✅ 블록별 베이 선택 상세 정보
    for analysis in bay_analyses:
        row_data = {
            'type': 'BLOCK_DETAIL',
            'date': date_key,
            'total_blocks': len(bay_analyses),
            'block_id': analysis['block_id'],
            'assembly_start_date': analysis.get('assembly_start_date', ''),
            'width_m': analysis['width_m'],
            'longi_count': analysis['longi_count'],
            'seam_count': analysis.get('seam_count', ''),
            'c_seam_count': analysis.get('c_seam_count', ''),
            'has_curved_plate': analysis.get('has_curved_plate', False),
            'material_type': analysis['material_type'],
            'assigned_bay': analysis['assigned_bay'],
            'final_reason': analysis['final_reason'],
            'P7#2_result': analysis['constraint_checks'].get('P7#2_physical', {}).get('result', ''),
            'P7#3_4_result': analysis['constraint_checks'].get('P7#3_4_ps_pair', {}).get('result', ''),
            'P7#7_8_result': analysis['constraint_checks'].get('P7#7_8_consecutive', {}).get('result', ''),
            'P7#10_result': analysis['constraint_checks'].get('P7#10_longi_30plus', {}).get('result', ''),
            'P7#11_result': analysis['constraint_checks'].get('P7#11_special_block', {}).get('result', ''),
            'P7#1_result': analysis['constraint_checks'].get('P7#1_load_balance', {}).get('result', ''),
            'bay_35a_worktime_before': round(analysis.get('bay_35a_worktime_before', 0) / 3600, 2),
            'bay_36b_worktime_before': round(analysis.get('bay_36b_worktime_before', 0) / 3600, 2),
            'bay_35a_worktime_after': round(analysis.get('bay_35a_worktime_after', 0) / 3600, 2),
            'bay_36b_worktime_after': round(analysis.get('bay_36b_worktime_after', 0) / 3600, 2)
        }
        all_data.append(row_data)
    
    # ✅ CSV 저장
    if all_data:
        df = pd.DataFrame(all_data)
        df.to_csv(filename, index=False, encoding='utf-8-sig')
        print(f"   📄 Assembly 베이 분석 저장: {filename} ({len(bay_analyses)}개 블록)")
    
    return filename
# csv_save.py

#################################################################################################################################################
###############                                            완화 모드                                                               ###############
#################################################################################################################################################
