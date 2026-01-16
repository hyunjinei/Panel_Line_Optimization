"""
Data conversion utilities
"""

# [AGENT-ADD] Split from common/utils_core.py for readability.

import os
import pandas as pd
import numpy as np
from datetime import datetime, timedelta, date
from typing import List, Dict, Tuple, Optional, Any, Set
import logging
import re
import sys  # 🔧 sys import 추가
from enhanced_environment.common.settings import VERBOSE_CONVERSION, DEBUG_MODE, QUIET_MODE
# [AGENT-ADD] 런타임 캘린더/데이터 시트 오버라이드 반영
from runtime_config import build_calendar_overrides, get_runtime_config
# [AGENT-EDIT] 타입 힌트 참조 해소를 위해 데이터 구조 타입을 명시적으로 임포트
from enhanced_environment.models import EnhancedBlock, AssemblyType, PortStarboard, BayType

class DataConverter:
    """데이터 변환 유틸리티"""
    
    _last_resolved_data_sheet: Optional[str] = None
    
    @staticmethod
    def excel_to_blocks(excel_path: str, sheet_name: Optional[str] = None) -> List[EnhancedBlock]:
        """
        실제 데이터셋 엑셀 파일을 EnhancedBlock 리스트로 변환 (250618_SNU.xlsx 버전)
        
        엑셀 열 구조:
        A: 호선번호, B: 블록번호, C: 소조번호, D: 길이, E: 폭, F: 최소두께, G: 최대두께,
        H: 판넬 SEAM 수, I: 판넬 C/SEAM 수, J: 판넬 론지 수, K: 판넬 SEAM 용접장, L: 판넬 론지 용접장,
        M: FAB SEAM 수, N: FAB 론지 수, O: FAB SEAM 용접장, P: FAB 론지 용접장,
        Q: 평판 수, R: 곡판 수, S: 앵글수, T: B/UP 수, U: 플랫드바 수, 
        V: 순번, W: 론지 작업장, X: 착수일, Y: 종료일, Z: 조립 착수일, AA: 조립 작업장,
        AB: 판계 Tact Time, AC: 전면 SAW Tact Time, AD: Turn Over Tact Time, AE: 후면 SAW Tact Time, 
        AF: NC Tact Time, AG: 론지 취부 Tact Time, AH: 론지 용접 Tact Time, AI: 수정 Tact Time, AJ: 총 Tact Time
        """
        try:
            # [AGENT-EDIT] config.yaml의 data.sheet가 있으면 기본 시트로 사용
            if sheet_name is None:
                runtime_cfg = get_runtime_config() or {}
                if isinstance(runtime_cfg, dict):
                    data_cfg = runtime_cfg.get("data") or {}
                    sheet_name = data_cfg.get("sheet") or sheet_name
            workbook = pd.ExcelFile(excel_path)
            resolved_sheet = DataConverter._resolve_data_sheet_name(workbook, sheet_name)
            df = workbook.parse(resolved_sheet)
            df.columns = [str(col).strip() for col in df.columns]
            blocks = []
            
            # 1단계: 모든 블록 기본 정보 수집
            block_data_list = []
            assembly_meta = {}
            assembly_meta = {}
            for idx, row in df.iterrows():
                try:
                    # 기본 정보 추출
                    block_id = idx + 1  # 순서대로 ID 할당
                    block_name = str(row.get('블록번호', f'BLK_{block_id}'))
                    
                    # ✅ 소조번호 추출 (별판 식별용)
                    sub_assembly_number = str(row.get('소조번호', f'SUB_{block_id}'))
                    
                    # P/S/C 구분 (블록번호 마지막 문자)
                    port_starboard = DataConverter._parse_block_ps_type(block_name)
                    
                    # 물리적 특성
                    length = float(row.get('길이', 20.0))
                    width = float(row.get('폭', 15.0))
                    min_thickness = float(row.get('최소두께', 10.0))
                    max_thickness = float(row.get('최대두께', 30.0))
                    
                    # 론지 개수 및 용접장
                    longi_count = int(row.get('판넬 론지 수', 10))
                    
                    # P5#8: 심수 계산: 판넬 SEAM 수 + 판넬 C/SEAM 수 (P5#8 제약 반영)
                    panel_seam_count = int(row.get('판넬 SEAM 수', 1))
                    panel_cseam_count = int(row.get('판넬 C/SEAM 수', 0))
                    seam_count = panel_seam_count  # 순수 SEAM 수 (C/Seam 별도 관리)
                    
                    # 부재 개수
                    flat_plate_count = int(row.get('평판 수', 5))
                    curved_plate_count = int(row.get('곡판 수', 2))
                    angle_count = int(row.get('앵글수', 0))
                    buildup_count = int(row.get('B/UP 수', 0))
                    
                    # 주판 개수 계산 (P6#3: D/C Block 판정용)
                    main_plate_count = flat_plate_count + curved_plate_count
                    
                    # ✅ 순번 추출 (동일 착수일 기준 시퀀싱용)
                    sequence_number = int(row.get('순번', idx + 1))
                    
                    # 론지 작업장과 베이 연결
                    longi_workshop = row.get('론지 작업장', 1)
                    bay_hint = BayType.BAY_35A if longi_workshop == 1 else BayType.BAY_36B
                    
                    # 날짜 변환 (착수일, 조립 착수일)
                    start_date = DataConverter._parse_date_format(row.get('착수일'))
                    end_date = DataConverter._parse_date_format(row.get('종료일'))
                    assembly_start_date = DataConverter._parse_date_format(row.get('조립 착수일'))
                    
                    # ✅ 실제 착수일은 '착수일' 컬럼 사용 (사용자 요구사항)
                    actual_start_date = DataConverter._parse_date_format(row.get('착수일'))
                    
                    # ✅ 디버깅: 착수일 정보 출력
                    if idx < 5 and VERBOSE_CONVERSION:  # 처음 5개만 출력
                        print(f"🔍 블록 {block_id}: 원본 착수일={row.get('착수일')}, 조립착수일={row.get('조립 착수일')}")
                        print(f"    파싱된 착수일={actual_start_date.strftime('%Y%m%d')}, 파싱된 조립착수일={assembly_start_date.strftime('%Y%m%d')}")
                    
                    # 조립 작업장과 타입 매핑
                    assembly_workshop = str(row.get('조립 작업장', '')).strip()
                    if VERBOSE_CONVERSION and idx < 5:
                        print(f"raw assembly workshop ({block_id}): {assembly_workshop}")
                    assembly_workshop_raw = assembly_workshop
                    assembly_type = DataConverter._parse_assembly_workshop(assembly_workshop)

################################################################################################################################################################################################
# fix: 라인 그룹 및 원시 작업장 코드 보존
################################################################################################################################################################################################
                    assembly_workshop_code = assembly_workshop or ''  # 원본 문자열 유지 (예: L_11, F_16)
                    line_group = DataConverter._extract_line_group(assembly_workshop_raw)
                    if not line_group:
                        line_group = DataConverter._extract_line_group(assembly_workshop_code)

                    if not line_group:
                        line_group_hint = DataConverter._line_group_from_longi_workshop(longi_workshop)
                        if line_group_hint:
                            line_group = line_group_hint

                    if not line_group and str(assembly_type).lower() == 'line':
                        line_group = 'L_1'

                    if not assembly_workshop_code:
                        assembly_workshop_code = line_group if line_group else ''
                    elif assembly_workshop_code.lower() == 'line':
                        assembly_workshop_code = line_group if line_group else 'L_1'

                    # ✅ 착수일을 그대로 사용 (P5#1 제약조건 완전 제거)
                    panel_start_date = actual_start_date  # '착수일' 컬럼 그대로 사용
                    
                    # FAB 데이터
                    fab_seam_count = int(row.get('FAB SEAM 수', 0))
                    fab_longi_count = int(row.get('FAB 론지 수', 0))
                    is_fab_block = (fab_seam_count > 0 or fab_longi_count > 0)
                    
                    # ✅ 산출식 계산용 추가 데이터 추출
                    # 총 무게 추정 (길이 × 폭 × 평균두께 × 강재밀도)
                    avg_thickness = (min_thickness + max_thickness) / 2.0 / 1000.0  # mm → m 변환
                    steel_density = 7.85  # ton/m³
                    total_weight = length * width * avg_thickness * steel_density
                    
                    # 총 seam 용접장 (판넬 SEAM 용접장)
                    total_seam_length = float(row.get('판넬 SEAM 용접장', length * 2))  # 기본값: 길이의 2배
                    
                    # 론지 용접장 (판넬 론지 용접장)
                    longi_length = float(row.get('판넬 론지 용접장', width * longi_count))  # 기본값: 폭 × 론지 개수
                    
                    # 조립 타입 문자열 변환
                    assembly_type_str = assembly_type.value if hasattr(assembly_type, 'value') else "LINE"
                    
                    # 제약조건 속성 계산
                    is_draft = False  # 추후 데이터 확장 시 사용
                    # Cross seam 자동 추론 규칙 (내부 정의):
                    # 1) All Cross: seam_count == 0 and c_seam_count > 0
                    # 2) Double Cross: c_seam_count >= 2 * max(1, seam_count)
                    is_cross_seam = False
                    if seam_count == 0 and panel_cseam_count > 0:
                        is_cross_seam = True
                    elif panel_cseam_count >= 2 * max(1, seam_count):
                        is_cross_seam = True
                    
                    # 특수 블록 여부 판정
                    is_main_plate_only = (angle_count == 0 and buildup_count == 0)
                    
                    # ✅ 실제 Tact Time 데이터 추출
                    actual_tact_times = DataConverter._extract_actual_tact_times(row)
                    
                    if actual_tact_times:
                        # 실제 Tact Time 사용
                        processing_times = actual_tact_times
                        has_actual_tact_time = True
                        # print(f"   블록 {block_id}: 실제 Tact Time 사용")
                    else:
                        # 추정 처리시간 생성 (산출식.txt 기반)
                        processing_times = DataConverter._generate_processing_times(
                            seam_count=panel_seam_count,  # Seam 개수 (C/Seam 제외)
                            longi_count=longi_count,
                            main_plate_count=flat_plate_count + curved_plate_count,
                            angle_count=angle_count,
                            buildup_count=buildup_count,
                            width=width,
                            length=length,
                            max_thickness=max_thickness,
                            min_thickness=min_thickness,
                            total_weight=total_weight,
                            total_seam_length=total_seam_length,
                            longi_length=longi_length,
                            is_step_block=False,  # 엑셀에 단차블록 정보가 없으면 False
                            is_special_block=(flat_plate_count + curved_plate_count > 10),  # 주판수 10개 초과시 특수블록으로 추정
                            c_seam_count=panel_cseam_count,  # C/Seam 개수
                            assembly_type=assembly_type_str
                        )
                        has_actual_tact_time = False
                    
                    # 블록 데이터 임시 저장
                    line_group = DataConverter._normalize_line_group(line_group)

                    block_data = {
                        'block_id': block_id,
                        'block_name': block_name,
                        'sub_assembly_number': sub_assembly_number,  # ✅ 소조번호 추가
                        'sequence_number': sequence_number,  # ✅ 순번 추가
                        'processing_times': processing_times,
                        'has_actual_tact_time': has_actual_tact_time,  # ✅ 실제 Tact Time 여부
                        'max_start_date': panel_start_date,  # ✅ '착수일' 컬럼 사용
                        'assembly_type': assembly_type,
                        'port_starboard': port_starboard,
                        'pair_block_id': None,  # ✅ 나중에 P/S 매칭에서 설정
                        'assembly_start_date': assembly_start_date,  # 조립 착수일은 별도 보관
                        'is_fab': is_fab_block,
                        'seam_count': seam_count,
                        'width': width,
                        'length': length,                    # 🔥 누락된 length 추가
                        'min_thickness': min_thickness,      # 🔥 누락된 min_thickness 추가
                        'max_thickness': max_thickness,      # 🔥 누락된 max_thickness 추가
                        'longi_count': longi_count,
                        'is_draft': is_draft,
                        'is_cross_seam': is_cross_seam,
                        'main_plate_count': main_plate_count,
                        'angle_count': angle_count,
                        'buildup_count': buildup_count,
                        'is_main_plate_only': is_main_plate_only,
                        'assigned_bay': bay_hint,
                        'material_ready': True,
                        'c_seam_count': panel_cseam_count,
                        'assembly_workshop_code': assembly_workshop_code,
                        'assembly_workshop_code_raw': assembly_workshop_raw,
                        'line_group': line_group
                    }

                    block_data_list.append(block_data)
                    assembly_meta[block_id] = {
                        'assembly_workshop_code': assembly_workshop_code,
                        'assembly_workshop_code_raw': assembly_workshop_raw,
                        'line_group': line_group
                    }
                    assembly_meta[block_id] = {
                        'assembly_workshop_code': assembly_workshop_code,
                        'assembly_workshop_code_raw': assembly_workshop_raw,
                        'line_group': line_group
                    }
                    
                except Exception as e:
                    if VERBOSE_CONVERSION:
                        print(f"⚠️ 블록 {idx+1} 처리 실패, 스킵: {e}")
                    continue
            
            if VERBOSE_CONVERSION:
                print(f"📊 총 {len(block_data_list)}개 블록 데이터 수집 완료")
            
            # 2단계: P/S 쌍 자동 매칭
            ps_pairs = DataConverter._auto_match_ps_pairs(block_data_list)
            if VERBOSE_CONVERSION:
             print(f"🔗 P/S 쌍 매칭: {len(ps_pairs)//2}쌍")
            
            # ✅ block_data_list에 P/S 쌍 정보 업데이트
            for block_data in block_data_list:
                block_id = block_data['block_id']
                if block_id in ps_pairs:
                    block_data['pair_block_id'] = ps_pairs[block_id]
            
            # 3단계: EnhancedBlock 객체 생성 (P/S 쌍 정보 포함)
            for block_data in block_data_list:
                try:
                    block = EnhancedBlock(
                        block_id=block_data['block_id'],
                        processing_times=block_data['processing_times'],
                        max_start_date=block_data['max_start_date'],  # '착수일' 사용
                        assembly_type=block_data['assembly_type'],
                        port_starboard=block_data['port_starboard'],
                        pair_block_id=block_data['pair_block_id'],  # 자동 매칭된 쌍 ID
                        assembly_start_date=block_data['assembly_start_date'],  # '조립 착수일' 사용
                        is_fab=block_data['is_fab'],
                        seam_count=block_data['seam_count'],
                        width=block_data['width'],
                        length=block_data['length'],                    # 🔥 누락된 length 추가
                        min_thickness=block_data['min_thickness'],      # 🔥 누락된 min_thickness 추가
                        max_thickness=block_data['max_thickness'],      # 🔥 누락된 max_thickness 추가
                        longi_count=block_data['longi_count'],
                        is_draft=block_data['is_draft'],
                        is_cross_seam=block_data['is_cross_seam'],
                        main_plate_count=block_data['main_plate_count'],
                        angle_count=block_data['angle_count'],
                        buildup_count=block_data['buildup_count'],
                        is_main_plate_only=block_data['is_main_plate_only'],
                        block_number=block_data['block_id'],
                        assigned_bay=block_data['assigned_bay'],
                        material_ready=block_data['material_ready'],
                        c_seam_count=block_data.get('c_seam_count', 0)
                    )

                    # ✅ 추가 속성 설정
                    setattr(block, 'sub_assembly_number', block_data['sub_assembly_number'])
                    setattr(block, 'block_name', block_data['block_name'])
                    setattr(block, 'sequence_number', block_data['sequence_number'])
                    setattr(block, 'has_actual_tact_time', block_data['has_actual_tact_time'])
                    setattr(block, 'assembly_workshop_code', block_data['assembly_workshop_code'])
                    setattr(block, 'assembly_workshop_code_raw', block_data['assembly_workshop_code_raw'])
                    setattr(block, 'line_group', block_data['line_group'])
                    setattr(block, 'c_seam_count', block_data.get('c_seam_count', 0))

                    blocks.append(block)
                    
                except Exception as e:
                    if VERBOSE_CONVERSION:
                        print(f"⚠️ 블록 객체 생성 실패, 스킵: {e}")
                    continue
            
            if VERBOSE_CONVERSION:
                print(f"✅ 총 {len(blocks)}개 블록 객체 생성 완료")
            
            # 4단계: 별판 처리 (먼저 실행)
            blocks = DataConverter._process_subassembly_grouping(blocks)

            # 5단계: ✅ 순번열 기준 정렬 (별판 처리 후)
            blocks = DataConverter._sort_blocks_by_sequence_number(blocks)

            # 별판 통합으로 손실된 메타데이터 재적용
            for block in blocks:
                meta = assembly_meta.get(block.block_id)
                if not meta:
                    continue
                raw_code = meta.get('assembly_workshop_code_raw') or meta.get('assembly_workshop_code') or ''
                code = meta.get('assembly_workshop_code') or raw_code
                line_group = meta.get('line_group')

                if raw_code is not None:
                    setattr(block, 'assembly_workshop_code_raw', raw_code)
                if code is not None:
                    setattr(block, 'assembly_workshop_code', code)
                if line_group is not None:
                    setattr(block, 'line_group', line_group)

            # 6단계: P5#9 제약 사전 검증
            blocks = DataConverter._validate_p5_9_constraint_pre_check(blocks)
            
            return blocks
            
        except Exception as e:
            raise ValueError(f"엑셀 파일 변환 실패: {e}")
    
    @staticmethod
    def _extract_actual_tact_times(row) -> Optional[List[float]]:
        """
        실제 Tact Time 데이터 추출 (250618_SNU.xlsx 버전)
        
        Returns:
            8개 공정의 실제 처리시간 리스트 (분 단위), 데이터가 없으면 None
        """
        tact_time_columns = [
            '판계 Tact Time',           # 1. 판계
            '전면 SAW Tact Time',       # 2. 전면 SAW
            'Turn Over Tact Time',      # 3. Turn Over
            '후면 SAW Tact Time',       # 4. 후면 SAW
            'NC Tact Time',             # 5. NC
            '론지 취부 Tact Time',       # 6. 론지 취부
            '론지 용접 Tact Time',       # 7. 론지 용접
            '수정 Tact Time'            # 8. 수정
        ]
        
        tact_times = []
        valid_count = 0
        
        for col in tact_time_columns:
            tact_time = row.get(col, None)
            
            if pd.isna(tact_time) or tact_time is None:
                # 빈 값이면 0으로 설정 (일부 공정은 시간이 0일 수 있음)
                tact_times.append(0.0)
            else:
                try:
                    time_value = float(tact_time)
                    if time_value >= 0:  # 0 이상이면 유효
                        tact_times.append(time_value)
                        if time_value > 0:
                            valid_count += 1
                    else:
                        return None  # 음수 값이 있으면 추정값 사용
                except (ValueError, TypeError):
                    return None  # 변환 실패시 추정값 사용
        
        # 최소 5개 이상의 공정에 유효한 시간이 있어야 실제 데이터로 인정
        if valid_count >= 5 and len(tact_times) == 8:
            return tact_times
        else:
            return None
    
    @staticmethod
    def _sort_blocks_by_sequence_number(blocks: List[EnhancedBlock]) -> List[EnhancedBlock]:
        """
        순번열 기준으로 블록 정렬 (동일 조립 착수일 내에서)
        
        Args:
            blocks: 별판 처리 완료된 블록 리스트
            
        Returns:
            순번 기준 정렬된 블록 리스트
        """
        # ✅ 조립 착수일별로 그룹화
        date_groups: Dict[str, List[EnhancedBlock]] = {}
        for block in blocks:
            assembly_start = getattr(block, 'assembly_start_date', None)
            if isinstance(assembly_start, datetime):
                date_key = assembly_start.strftime('%Y%m%d')
            else:
                date_key = f"NO_ASM_{block.block_id}"
            date_groups.setdefault(date_key, []).append(block)
        
        # ✅ 디버깅: 날짜 그룹핑 결과 출력
        if VERBOSE_CONVERSION:
            print(f"🔍 _sort_blocks_by_sequence_number: 발견된 조립 착수일들")
            for date_key in sorted(date_groups.keys()):
                print(f"    날짜 {date_key}: {len(date_groups[date_key])}개 블록")
        
        # 각 날짜별로 순번 기준 정렬 및 연속 순번 재배정
        sorted_blocks = []
        for date_key in sorted(date_groups.keys()):
            blocks_in_date = date_groups[date_key]
            
            # 순번 기준 정렬 (sequence_number 속성 사용)
            blocks_in_date_sorted = sorted(
                blocks_in_date, 
                key=lambda b: getattr(b, 'sequence_number', 0)
            )
            
            # ✅ 날짜별로 연속 순번 재배정 (1부터 시작)
            for i, block in enumerate(blocks_in_date_sorted):
                setattr(block, 'sequence_number', i + 1)
            
            sorted_blocks.extend(blocks_in_date_sorted)
            
            # ✅ 재배정된 순번으로 출력
            if blocks_in_date_sorted:
                min_seq = 1
                max_seq = len(blocks_in_date_sorted)
                if VERBOSE_CONVERSION:
                    print(f"📋 {date_key}: 순번 {min_seq}~{max_seq} ({len(blocks_in_date_sorted)}개 블록)")
        
        return sorted_blocks
    
    @staticmethod
    def _format_assembly_date_key(date_value: Optional[datetime], fallback: str) -> str:
        """조립 착수일을 YYYYMMDD 문자열로 변환 (없으면 고유 fallback 사용)"""
        if isinstance(date_value, datetime):
            return date_value.strftime('%Y%m%d')
        return fallback

    @staticmethod
    def _get_block_base_and_suffix(block_name: str) -> Tuple[str, Optional[str]]:
        """
        블록명을 기본명과 접미사(P/S 등)로 분리.
        
        Returns:
            (기본 블록명, 접미사) 형태. 접미사가 없으면 None.
        """
        if not isinstance(block_name, str) or len(block_name) == 0:
            return "", None
        suffix = block_name[-1].upper()
        if suffix in ("P", "S"):
            return block_name[:-1], suffix
        return block_name, None

    @staticmethod
    def _physical_key_from_block_data(block_data: Dict[str, Any]) -> Tuple:
        """EnhancedBlock.get_physical_characteristics_key와 동일한 물리 키 생성"""
        def _round_or_zero(value):
            if value is None:
                return 0.0
            try:
                return round(float(value), 1)
            except (TypeError, ValueError):
                return 0.0

        seam_count = block_data.get('seam_count', 0) or 0
        longi_count = block_data.get('longi_count', 0) or 0
        is_cross_seam = bool(block_data.get('is_cross_seam', False))
        return (
            _round_or_zero(block_data.get('width')),
            int(seam_count),
            is_cross_seam,
            int(longi_count),
            _round_or_zero(block_data.get('length')),
            _round_or_zero(block_data.get('min_thickness')),
            _round_or_zero(block_data.get('max_thickness')),
        )

    @staticmethod
    def _auto_match_ps_pairs(block_data_list: List[Dict]) -> Dict[int, int]:
        """
        P/S 쌍 자동 매칭 (착수일 명시적 확인 추가)
        
        규칙: 
        1. 같은 조립 착수일 (assembly_start_date)
        2. 동일 물리 특성과 동일 소조번호
        3. base 블록명이 동일하고 접미사가 P/S로 다른 경우 매칭
        
        Returns:
            Dict[block_id, pair_block_id]: 쌍 매칭 결과
        """
        ps_pairs = {}
        
        # ✅ 조립 착수일별로 블록 그룹핑
        assembly_date_groups: Dict[str, List[Dict[str, Any]]] = {}
        for block_data in block_data_list:
            asm_key = DataConverter._format_assembly_date_key(
                block_data.get('assembly_start_date'),
                f"NO_ASM_{block_data['block_id']}"
            )
            assembly_date_groups.setdefault(asm_key, []).append(block_data)
        
        # 각 조립 착수일 그룹별로 P/S 매칭
        for _, blocks_in_group in assembly_date_groups.items():
            base_groups: Dict[str, List[Dict[str, Any]]] = {}
            for block_data in blocks_in_group:
                block_name = block_data['block_name']
                base_name, suffix = DataConverter._get_block_base_and_suffix(block_name)
                if suffix not in ("P", "S"):
                    continue  # P/S 블록만 대상
                base_groups.setdefault(base_name, []).append(block_data)
            
            for base_name, same_base_blocks in base_groups.items():
                # 소조번호 + 물리키별로 P/S 쌍 후보 구성
                pairing_slots: Dict[Tuple[Any, Tuple], Dict[str, Dict[str, Any]]] = {}
                for block_data in same_base_blocks:
                    _, suffix = DataConverter._get_block_base_and_suffix(block_data['block_name'])
                    if suffix not in ("P", "S"):
                        continue
                    sub_number = block_data.get('sub_assembly_number')
                    physical_key = DataConverter._physical_key_from_block_data(block_data)
                    slot_key = (sub_number, physical_key)
                    slot = pairing_slots.setdefault(slot_key, {})
                    slot[suffix] = block_data

                # 매칭 가능한 슬롯에서 P/S 쌍 연결
                for slot in pairing_slots.values():
                    port_block = slot.get("P")
                    starboard_block = slot.get("S")
                    if not port_block or not starboard_block:
                        continue

                    port_id = port_block['block_id']
                    starboard_id = starboard_block['block_id']
                    ps_pairs[port_id] = starboard_id
                    ps_pairs[starboard_id] = port_id
        
        return ps_pairs
    
    @staticmethod
    def _extract_block_number(block_name: str) -> Optional[str]:
        """블록명에서 숫자 부분 추출"""
        import re
        
        # BLK_55S, BLK_55P 같은 패턴에서 숫자 추출
        match = re.search(r'(\d+)[A-Z]?$', block_name)
        if match:
            return match.group(1)
        
        return None
    
    @staticmethod
    def _parse_block_ps_type(block_name: str) -> PortStarboard:
        """블록명에서 P/S/C 구분 추출"""
        if not isinstance(block_name, str) or len(block_name) == 0:
            return PortStarboard.NONE
            
        last_char = block_name[-1].upper()
        if last_char == 'P':
            return PortStarboard.PORT
        elif last_char == 'C':
            return PortStarboard.CENTER  # 새로 추가된 CENTER 타입
        elif last_char == 'S':
            return PortStarboard.STARBOARD
        else:
            return PortStarboard.NONE
    
    @staticmethod
    def _parse_date_format(date_value) -> datetime:
        """20240509 형식 날짜를 datetime으로 변환"""
        if pd.isna(date_value):
            return datetime.now() + timedelta(days=30)
            
        if isinstance(date_value, datetime):
            return date_value
        elif isinstance(date_value, str):
            # 20240509 형식 처리
            if len(date_value) == 8 and date_value.isdigit():
                year = int(date_value[:4])
                month = int(date_value[4:6])
                day = int(date_value[6:8])
                return datetime(year, month, day, 8, 0, 0)  # 08:00 시작
            else:
                return TimeUtils.parse_time_string(date_value)
        elif isinstance(date_value, (int, float)):
            # 20240509 숫자 형식
            date_str = str(int(date_value))
            if len(date_str) == 8:
                year = int(date_str[:4])
                month = int(date_str[4:6])
                day = int(date_str[6:8])
                return datetime(year, month, day, 8, 0, 0)
            else:
                return pd.to_datetime(date_value)
        else:
            return datetime.now() + timedelta(days=30)
    
    @staticmethod
    def _parse_assembly_workshop(workshop_str: str) -> AssemblyType:
        """조립 작업장 문자열을 AssemblyType으로 변환"""
        if not isinstance(workshop_str, str):
            return AssemblyType.LINE
        
        if workshop_str.startswith('F_'):
            return AssemblyType.FIXED  # 고정조립
        elif workshop_str.startswith('L_'):
            return AssemblyType.LINE   # 라인조립  
        else:
            return AssemblyType.LINE   # 기본값

################################################################################################################################################################################################
# fix: 라인 그룹 추출 유틸리티 (L11/L12 → L1, L21/L22 → L2 등)
################################################################################################################################################################################################
    @staticmethod
    def _normalize_line_group(value: Optional[str]) -> str:
        if not value:
            return ''

        text = str(value).strip().upper()
        if not text:
            return ''

        if text.startswith('F'):
            digits = ''.join(ch for ch in text if ch.isdigit())
            try:
                idx = int(digits) if digits else 1
            except ValueError:
                idx = 1
            idx = max(1, min(idx, 10))
            return f"F_{idx}"

        if text.startswith('L'):
            # [AGENT-EDIT] L_11/L_12 -> L_1, L_21/L_22 -> L_2 (라인 그룹 묶음)
            suffix = text.split('_', 1)[1] if '_' in text else text[1:]
            digits = ''.join(ch for ch in suffix if ch.isdigit())
            if digits:
                return f"L_{digits[0]}"
            return 'L_1'

        return text

    @staticmethod
    def _line_group_from_longi_workshop(workshop_value) -> str:
        if workshop_value is None:
            return ''

        text = str(workshop_value).strip().upper()
        if not text:
            return ''

        digits = ''.join(ch for ch in text if ch.isdigit())
        if digits:
            return DataConverter._normalize_line_group(f"L_{digits[0]}")

        if text in {'A', 'LINE', 'L'}:
            return 'L_1'
        if text in {'B', 'LINE2'}:
            return 'L_2'

        return ''

    @staticmethod
    def _extract_line_group(workshop_str: str) -> Optional[str]:
        if not isinstance(workshop_str, str):
            return None

        workshop_str = workshop_str.strip()
        if workshop_str.startswith('F_'):
            return DataConverter._normalize_line_group(workshop_str)

        if workshop_str.startswith('L_'):
            return DataConverter._normalize_line_group(workshop_str)

        return None

    @staticmethod
    def _generate_processing_times(seam_count: int, longi_count: int, 
                                 main_plate_count: int, angle_count: int, 
                                 buildup_count: int, width: float = 20.0, 
                                 length: float = 30.0, max_thickness: float = 20.0,
                                 min_thickness: float = 10.0, total_weight: float = 10.0,
                                 total_seam_length: float = 100.0, longi_length: float = 50.0,
                                 is_step_block: bool = False, is_special_block: bool = False,
                                 c_seam_count: int = 0, assembly_type: str = "LINE") -> List[float]:
        """
        산출식.txt 기반 8개 공정 처리시간 정확 계산
        
        Args:
            seam_count: Seam 개수
            longi_count: 론지 개수  
            main_plate_count: 주판 개수
            angle_count: 앵글 개수
            buildup_count: 빌드업 개수
            width: 폭 (m)
            length: 길이 (m)
            max_thickness: 최대 두께 (mm)
            min_thickness: 최소 두께 (mm)
            total_weight: 총 무게 (톤)
            total_seam_length: 총 seam 용접장 (m)
            longi_length: 론지 용접장 (m)
            is_step_block: 단차블록 여부
            is_special_block: 특수블록 여부
            c_seam_count: C/Seam 개수
            assembly_type: 조립타입 ("LINE" 또는 "FIXED")
            
        Returns:
            8개 공정 처리시간 리스트 (분 단위)
        """
        
        # =============================================================================
        # I. 판계 (공정 1)
        # =============================================================================
        def calculate_panel_time():
            # 1. Seam 수에 따른 기본시간
            if seam_count == 1:
                base_time = 12
            elif seam_count == 2:
                base_time = 20
            elif seam_count == 3:
                base_time = 25
            elif 4 <= seam_count <= 5:
                base_time = 30
            elif seam_count == 6:
                base_time = 45
            else:
                # 6개 초과시 추정 (6개 기준으로 비례)
                base_time = 45 + (seam_count - 6) * 7
            
            # 2. C/Seam 추가 시간 (조정: 약간 줄임)
            c_seam_additional = 0
            if 1 <= seam_count <= 4:  # 기본 조건 충족시만
                if c_seam_count == 1:
                    c_seam_additional = 8  # 10→8
                elif 2 <= c_seam_count <= 3:
                    c_seam_additional = 15  # 18→15
                elif c_seam_count > 3:
                    c_seam_additional = 15 + (c_seam_count - 3) * 6  # 8→6
            
            # 3. 특수블록 시간 (기본시간 대체)
            special_time = 0
            if is_special_block:
                # 블록1~4 구분은 별도 로직 필요 (현재는 주판수로 추정)
                if main_plate_count <= 5:
                    special_time = 30  # 블록1
                elif main_plate_count <= 8:
                    special_time = 40  # 블록2  
                elif main_plate_count <= 12:
                    special_time = 55  # 블록3
                elif main_plate_count >= 10:  # 평판수 ≥10장
                    special_time = 85  # 블록4
            
            # 특수블록이면 특수시간 사용, 아니면 기본+C/Seam
            panel_time = special_time if special_time > 0 else (base_time + c_seam_additional)
            
            # 4. 두께 조건 보정 (기본 2~4번 조건 충족 시)
            thickness_multiplier = 1.0
            thickness_additional = 0
            if 2 <= seam_count <= 4:  # 기본 조건 충족시만
                if min_thickness >= 400:  # 최소두께 ≥400
                    thickness_additional = 15
                if max_thickness >= 400:  # 최대두께 ≥400
                    thickness_multiplier = 1.9
            
            # 5. 단차블록 보정 (기본 3~4번 조건 충족 시)
            step_additional = 0
            if 3 <= seam_count <= 4 and is_step_block:  # 기본 조건 충족시만
                step_additional = 10
            
            # 최종 판계 시간 계산
            final_time = (panel_time + thickness_additional + step_additional) * thickness_multiplier
            return final_time
        
        # =============================================================================
        # II. 전면 SAW (공정 2)
        # =============================================================================
        def calculate_front_saw_time():
            # 이동·준비시간
            preparation_time = 8
            
            # Seam 용접 계산
            seam_welding_time = 0
            if seam_count > 0:
                # seam 용접장 = (총 seam 용접장) ÷ (Seam 개수 + C/Seam 개수)
                avg_seam_length = total_seam_length / max(1, seam_count + c_seam_count)
                
                # 용접 횟수 결정 (수정: 조건 완화)
                welding_passes = 1
                if seam_count >= 6:  # 46 → 6으로 변경
                    welding_passes = 2
                elif seam_count >= 3:  # 13 → 3으로 변경
                    welding_passes = 1
                
                if max_thickness >= 25:  # 최대두께 ≥ 25 →2회
                    welding_passes = 2
                
                # 용접시간 계산 (수정: 속도 1.0 → 3.2 m/min)
                seam_welding_time = avg_seam_length * welding_passes / 3.2
            
            # C/Seam 용접 계산
            c_seam_welding_time = 0
            if c_seam_count > 0:
                # 1회당 준비시간: 5분
                c_seam_preparation = c_seam_count * 5
                
                # C/Seam 용접시간 (속도: 1분당 0.4 m)
                avg_c_seam_length = total_seam_length / max(1, seam_count + c_seam_count)
                c_seam_welding_time = c_seam_preparation + (avg_c_seam_length * c_seam_count / 0.4)
            
            return preparation_time + seam_welding_time + c_seam_welding_time
        
        # =============================================================================
        # III. Turn over (공정 3)
        # =============================================================================
        def calculate_turnover_time():
            return 12  # 고정 Tact-Time
        
        # =============================================================================
        # IV. 후면 SAW (공정 4)
        # =============================================================================
        def calculate_back_saw_time():
            # 이동·준비시간
            preparation_time = 8
            
            # Seam 용접 계산 (후면은 속도가 다름: 1분당 0.55 m)
            seam_welding_time = 0
            if seam_count > 0:
                avg_seam_length = total_seam_length / max(1, seam_count + c_seam_count)
                
                # 용접 횟수 결정 (수정: 조건 완화)
                welding_passes = 1
                if seam_count >= 6:  # 46 → 6으로 변경
                    welding_passes = 2
                elif seam_count >= 3:  # 13 → 3으로 변경
                    welding_passes = 1
                
                if max_thickness >= 25:
                    welding_passes = 2
                
                # 용접시간 계산 (속도: 1분당 0.55 m)
                seam_welding_time = avg_seam_length * welding_passes / 0.55
            
            # C/Seam 용접 계산 (전면과 동일)
            c_seam_welding_time = 0
            if c_seam_count > 0:
                c_seam_preparation = c_seam_count * 5
                avg_c_seam_length = total_seam_length / max(1, seam_count + c_seam_count)
                c_seam_welding_time = c_seam_preparation + (avg_c_seam_length * c_seam_count / 0.4)
            
            base_time = preparation_time + seam_welding_time + c_seam_welding_time
            
            # 🚀 전체 기본 시간 축소 (과대계산 해결)
            base_time *= 0.55
            
            # 🚀 실적데이터 기반 C/SEAM 지수적 보정
            if c_seam_count > 0:
                # 지수적 증가 모델: base_time * (1.05 ^ c_seam_count) - 극도로 완만한 지수
                exponential_factor = 1.05 ** c_seam_count
                base_time *= exponential_factor
            else:
                # C/SEAM이 없을 때는 기본 시간을 더 많이 줄임
                base_time *= 0.4
            
            return base_time
        
        # =============================================================================
        # V. NC 마킹 (공정 5)
        # =============================================================================
        def calculate_nc_marking_time():
            # 이동·계측·데이터 전송 (수정: 15.5 → 13.0분)
            base_time = 13.0
            
            # 라벨링 (속도: 1분당 20 m, 길이 기준)
            labeling_passes = 1
            if 1.6806 <= width <= 3.361:
                labeling_passes = 2
            elif width > 3.361:
                labeling_passes = max(2, int(width / 1.6805))
            
            labeling_time = (length * labeling_passes) / 20.0
            
            # 탭피스 작업 ((Seam 수 + C/Seam 수) 1개당 → 5분)
            tap_piece_time = (seam_count + c_seam_count) * 5
            
            return base_time + labeling_time + tap_piece_time
        
        # =============================================================================
        # VI. 론지 취부 (공정 6)
        # =============================================================================
        def calculate_longi_installation_time():
            # 이동·체크리스트 점검
            preparation_time = 10
            
            # 취부 시간 (론지 수 1개당 3.5분)
            installation_time = longi_count * 3.5
            
            return preparation_time + installation_time
        
        # =============================================================================
        # VII. 론지 용접 (공정 7)
        # =============================================================================
        def calculate_longi_welding_time():
            # 이동시간
            move_time = 5
            
            # 🔧 론지 용접장 단위 보정 (핵심 수정!)
            # 250618 데이터셋은 mm 단위, 2507 데이터셋은 m 단위
            if longi_length > 100:  # 100m 이상이면 mm 단위로 간주
                corrected_longi_length = longi_length / 1000.0  # mm → m 변환
            else:
                corrected_longi_length = longi_length  # 이미 m 단위
            
            # 론지 용접
            welding_time = 0
            if longi_count > 0:
                # 용접 횟수 결정
                welding_passes = 1
                if width >= 8.25 or longi_count > 10:
                    welding_passes = 2
                
                # 용접시간 계산 (속도: 1분당 1.1 m)
                # 론지의 seam 용접장 = 론지 용접장 (단위 보정된 값 사용)
                welding_time = (corrected_longi_length * welding_passes) / 1.1
                
                # 1회 용접 후 청소시간
                cleaning_time = welding_passes * 4
                welding_time += cleaning_time
                
                # 🚀 데이터셋별 차별화 보정
                # 방법 1: 용접장 기반 보정 (250618 타입)
                if longi_length > 100:  # mm 단위 (250618) - 실적: 용접장 구간별 보정
                    length_factor = min(1.5, 1.0 + (corrected_longi_length / 1000) * 0.2)
                    welding_time *= length_factor
                    
                # 방법 2: 론지 수 기반 보정 (2507 타입)  
                else:  # m 단위 (2507)
                    # 실적: 론지당 0.6분 추가
                    longi_correction = longi_count * 0.6
                    welding_time += longi_correction
            
            return move_time + welding_time
        
        # =============================================================================
        # VIII. 수정·사상 (공정 8)
        # =============================================================================
        def calculate_finishing_time():
            # 이동시간
            move_time = 5
            
            # 수정 시간 (수정: 론지 1개당 1.6 → 2.2분)
            finishing_time = longi_count * 2.2
            
            # 러그 작업
            lug_time = 0
            if assembly_type != "LINE":  # 후공정 작업장이 라인일 경우, 러그 작업 생략
                # 러그 마킹
                lug_marking = 5
                
                # 러그 개수 계산 (총무게 8 톤당 1개)
                lug_count = max(1, int((total_weight + 7) / 8))  # 올림 처리
                
                # 설치시간: 러그 1개당 5분
                lug_installation = lug_count * 5
                
                lug_time = lug_marking + lug_installation
            
            return move_time + finishing_time + lug_time
        
        # =============================================================================
        # 최종 시간 계산
        # =============================================================================
        processing_times = [
            calculate_panel_time(),           # 1. 판계
            calculate_front_saw_time(),       # 2. 전면 SAW
            calculate_turnover_time(),        # 3. Turn over
            calculate_back_saw_time(),        # 4. 후면 SAW
            calculate_nc_marking_time(),      # 5. NC 마킹
            calculate_longi_installation_time(), # 6. 론지 취부
            calculate_longi_welding_time(),   # 7. 론지 용접
            calculate_finishing_time()        # 8. 수정·사상
        ]
        
        return processing_times
    
    @staticmethod
    def blocks_to_dataframe(blocks: List[EnhancedBlock]) -> pd.DataFrame:
        """EnhancedBlock 리스트를 DataFrame으로 변환"""
        data = []
        
        for block in blocks:
            row = {
                'block_id': block.block_id,
                'max_start_date': block.max_start_date,  # 착수일
                'assembly_type': block.assembly_type.value,
                'port_starboard': block.port_starboard.value,
                'pair_block_id': block.pair_block_id,
                'seam_count': block.seam_count,
                'width': block.width,
                'length': block.length,                    # 🔥 누락된 length 추가
                'min_thickness': block.min_thickness,      # 🔥 누락된 min_thickness 추가
                'max_thickness': block.max_thickness,      # 🔥 누락된 max_thickness 추가
                'longi_count': block.longi_count,
                'is_draft': block.is_draft,
                'is_cross_seam': block.is_cross_seam,
                'main_plate_count': block.main_plate_count,
                'material_ready': block.material_ready,
                'block_number': block.block_number,
                'material_type': block.material_type.value,
                'total_processing_time': sum(block.processing_times)
            }
            
            # 개별 공정 시간
            for i, proc_time in enumerate(block.processing_times):
                row[f'process_{i+1}_time'] = proc_time
            
            data.append(row)
        
        return pd.DataFrame(data)
    
    @staticmethod
    def normalize_processing_times(blocks: List[EnhancedBlock], 
                                 method: str = "minmax") -> List[EnhancedBlock]:
        """처리 시간 정규화"""
        all_times = []
        for block in blocks:
            all_times.extend(block.processing_times)
        
        if method == "minmax":
            min_time = min(all_times)
            max_time = max(all_times)
            time_range = max_time - min_time
            
            for block in blocks:
                normalized_times = []
                for time_val in block.processing_times:
                    normalized = (time_val - min_time) / time_range if time_range > 0 else 0.5
                    normalized_times.append(normalized * 1000 + 100)  # 100~1100 범위로 스케일링
                
                block.processing_times = normalized_times
        
        elif method == "zscore":
            mean_time = np.mean(all_times)
            std_time = np.std(all_times)
            
            for block in blocks:
                normalized_times = []
                for time_val in block.processing_times:
                    z_score = (time_val - mean_time) / std_time if std_time > 0 else 0
                    normalized = max(50, min(2000, z_score * 300 + 500))  # 50~2000 범위
                    normalized_times.append(normalized)
                
                block.processing_times = normalized_times
        
        return blocks
    
    @staticmethod
    def _process_subassembly_grouping(blocks: List[EnhancedBlock]) -> List[EnhancedBlock]:
        """
        P5#17: 별판 처리 및 순번 생성 (회의 내용 반영 + 물리적/구조적 특성 일치 조건 추가)
        
        회의 결정사항:
        - 같은 조립 착수일 + 같은 블록이름 + 다른 소조번호 = 별판
        - ✅ 추가 조건: 물리적/구조적 특성도 동일해야 함
          * 같은 폭 (width)
          * 같은 SEAM 수 (seam_count) 
          * 같은 C/SEAM 여부 (is_cross_seam)
          * 같은 론지 수 (longi_count)
          * 향후 추가 예정: 길이, 최소두께, 최대두께
        - 별판들은 **하나의 블록으로 물리적 통합** (makespan 계산도 하나로)
        - 최종 해에서만 [7,7]로 표현 (순번 동일)
        
        Args:
            blocks: 원본 블록 리스트
            
        Returns:
            별판이 통합된 블록 리스트 (별판은 하나의 대표 블록으로 합쳐짐)
        """
        # ✅ 조립 착수일별로 블록 그룹핑
        assembly_groups: Dict[str, List[EnhancedBlock]] = {}
        for block in blocks:
            assembly_start = getattr(block, 'assembly_start_date', None)
            if isinstance(assembly_start, datetime):
                date_key = assembly_start.strftime('%Y%m%d')
            else:
                # 조립 착수일이 없으면 고유 키로 분리하여 잘못된 통합 방지
                date_key = f"NO_ASM_{block.block_id}"
            assembly_groups.setdefault(date_key, []).append(block)
        
        # ✅ 디버깅: 날짜 그룹핑 결과 출력
        if VERBOSE_CONVERSION:
            print(f"🔍 _process_subassembly_grouping: 발견된 조립 착수일들")
            for date_key in sorted(assembly_groups.keys()):
                print(f"    날짜 {date_key}: {len(assembly_groups[date_key])}개 블록")
        
        processed_blocks = []
        
        for date_key in sorted(assembly_groups.keys()):
            blocks_in_date = assembly_groups[date_key]
            
            # ✅ 같은 날짜 내에서 물리적/구조적 특성별 그룹핑 (강화된 조건)
            characteristic_groups = {}
            
            for block in blocks_in_date:
                # 블록이름 추출 (예: BLK_37P)
                block_name = getattr(block, 'block_name', f'BLK_{block.block_id}')
                
                # ✅ 물리적/구조적 특성 키 생성 (별판 판단용)
                # EnhancedBlock의 새로운 메서드 사용 + 블록명 추가
                physical_key = block.get_physical_characteristics_key()
                characteristic_key = (block_name,) + physical_key
                
                if characteristic_key not in characteristic_groups:
                    characteristic_groups[characteristic_key] = []
                characteristic_groups[characteristic_key].append(block)
            
            # 별판 통합 처리 (강화된 조건)
            for characteristic_key, group_blocks in characteristic_groups.items():
                block_name = characteristic_key[0]  # 블록명은 첫 번째 요소
                
                if len(group_blocks) > 1:
                    # ✅ 물리적 특성이 같은 블록들 중에서 소조번호 확인
                    subassembly_numbers = []
                    for block in group_blocks:
                        # ✅ 실제 소조번호 사용 (예: TP_1, TP_5 등)
                        sub_number = getattr(block, 'sub_assembly_number', str(block.block_id))
                        subassembly_numbers.append(sub_number)
                    
                    unique_subassemblies = list(set(subassembly_numbers))
                    
                    if len(unique_subassemblies) > 1:
                        # ✅ 별판 확인됨 - 하나의 대표 블록으로 통합
                        representative_block = group_blocks[0]  # 첫 번째 블록을 대표로 선택
                        
                        # ✅ 별판 통합 디버깅 출력 (전체 특성 표시)
                        physical_key = representative_block.get_physical_characteristics_key()
                        width, seam_count, is_cross_seam, longi_count, length, min_thick, max_thick = physical_key
                        
                        if VERBOSE_CONVERSION:
                            print(f"    🔗 별판 통합: {block_name} ({len(group_blocks)}개)")
                            print(f"        기본 특성: 폭={width}m, SEAM={seam_count}, C/SEAM={is_cross_seam}, 론지={longi_count}")
                            if length or min_thick or max_thick:
                                print(f"        추가 특성: 길이={length}m, 최소두께={min_thick}mm, 최대두께={max_thick}mm")
                            print(f"        소조번호: {unique_subassemblies}")
                        
                        # 별판 관련 메타데이터 설정 (원본 순번 유지)
                        setattr(representative_block, 'is_subassembly', True)
                        setattr(representative_block, 'subassembly_group_size', len(group_blocks))
                        setattr(representative_block, 'subassembly_original_blocks', group_blocks)  # 원본 별판들 보존
                        setattr(representative_block, 'block_name', block_name)
                        setattr(representative_block, 'start_date_key', date_key)
                        
                        # ✅ 별판 특성 메타데이터 추가 (검증용)
                        setattr(representative_block, 'subassembly_characteristics', {
                            'unified_width': width,
                            'unified_seam_count': seam_count,
                            'unified_is_cross_seam': is_cross_seam,
                            'unified_longi_count': longi_count,
                            'unified_length': length,
                            'unified_min_thickness': min_thick,
                            'unified_max_thickness': max_thick,
                            'characteristic_key': characteristic_key,
                            'physical_key': physical_key
                        })
                        
                        # 🔄 별판 통합 처리: Processing Time은 동일하므로 그대로 유지
                        # (사용자 설명: "심지어 모든공정에서 processingtime도 같아서 동일 취급해도 아무문제없어")
                        
                        # 하나의 통합된 블록만 추가
                        processed_blocks.append(representative_block)
                    else:
                        # 같은 소조번호: 일반 블록들 개별 처리
                        for block in group_blocks:
                            setattr(block, 'is_subassembly', False)
                            setattr(block, 'subassembly_group_size', 1)
                            setattr(block, 'subassembly_original_blocks', [block])
                            setattr(block, 'block_name', block_name)
                            setattr(block, 'start_date_key', date_key)
                            processed_blocks.append(block)
                else:
                    # 단독 블록
                    block = group_blocks[0]
                    setattr(block, 'is_subassembly', False)
                    setattr(block, 'subassembly_group_size', 1)
                    setattr(block, 'subassembly_original_blocks', [block])
                    setattr(block, 'block_name', block_name)
                    setattr(block, 'start_date_key', date_key)
                    processed_blocks.append(block)
        
        return processed_blocks
    
    @staticmethod
    def expand_subassembly_results(sequence: List[int], blocks: List[EnhancedBlock]) -> List[Tuple[int, int]]:
        """
        별판 결과 확장: 통합된 블록 순서를 원본 별판들로 복원
        
        Args:
            sequence: 통합된 블록들의 순서
            blocks: 통합된 블록 리스트 (별판은 대표 블록 하나)
            
        Returns:
            (block_id, sequence_number) 튜플 리스트 (별판은 동일 순번으로 복원)
        """
        expanded_results = []
        block_dict = {block.block_id: block for block in blocks}
        
        for seq_pos, block_id in enumerate(sequence):
            if block_id not in block_dict:
                continue
                
            block = block_dict[block_id]
            sequence_number = getattr(block, 'sequence_number', seq_pos + 1)
            
            if getattr(block, 'is_subassembly', False):
                # 별판: 원본 블록들을 동일 순번으로 복원
                original_blocks = getattr(block, 'subassembly_original_blocks', [block])
                for original_block in original_blocks:
                    expanded_results.append((original_block.block_id, sequence_number))
            else:
                # 일반 블록
                expanded_results.append((block_id, sequence_number))
        
        return expanded_results

################################################################################################################################################################################################
# fix: CSV 행 확장 유틸리티 (별판 복수 블록을 개별 행으로 복제)
################################################################################################################################################################################################
    @staticmethod
    def expand_rows_with_subassembly(rows: List[Dict]) -> List[Dict]:
        expanded_rows: List[Dict] = []
        for row in rows:
            expansion = row.get('subassembly_expansion')
            base_row = row.copy()
            base_row.pop('subassembly_expansion', None)

            group_size = len(expansion) if isinstance(expansion, list) else 0
            if group_size <= 0:
                group_size = 1

            assembly_type = base_row.get('assembly_type')
            base_line_group = DataConverter.resolve_line_group_value(base_row.get('line_group'), assembly_type, base_row.get('assembly_workshop_code'))
            base_assembly_code = DataConverter.resolve_workshop_code_value(base_row.get('assembly_workshop_code'), base_line_group, assembly_type)
            base_row['line_group'] = base_line_group
            base_row['assembly_workshop_code'] = base_assembly_code
            base_row['subassembly_group_size'] = group_size
            base_row['subassembly_representative_id'] = base_row.get('subassembly_representative_id', base_row.get('block_id'))
            base_row['is_subassembly_member'] = group_size > 1
            base_row['is_subassembly_representative'] = True
            base_row['violations_representative'] = base_row.get('violations_representative', base_row.get('violations', 0))

            if expansion and isinstance(expansion, list) and len(expansion) > 1:
                for idx, sub in enumerate(expansion):
                    sub_line_group = DataConverter.resolve_line_group_value(
                        sub.get('line_group'),
                        sub.get('assembly_type', assembly_type),
                        sub.get('assembly_workshop_code', base_assembly_code)
                    )
                    sub_assembly_code = DataConverter.resolve_workshop_code_value(
                        sub.get('assembly_workshop_code'),
                        sub_line_group,
                        sub.get('assembly_type', assembly_type)
                    )
                    sub_row = base_row.copy()
                    sub_row.update({
                        'block_id': sub.get('block_id', sub_row.get('block_id')),
                        'block_name': sub.get('block_name', sub_row.get('block_name')),
                        'port_starboard': sub.get('port_starboard', sub_row.get('port_starboard')),
                        'assembly_type': sub.get('assembly_type', sub_row.get('assembly_type')),
                        'line_group': sub_line_group,
                        'assembly_workshop_code': sub_assembly_code,
                        'block_assembly_date': sub.get('block_assembly_date', sub_row.get('block_assembly_date')),
                        'width_m': sub.get('width_m', sub_row.get('width_m')),
                        'longi_count': sub.get('longi_count', sub_row.get('longi_count')),
                        'seam_count': sub.get('seam_count', sub_row.get('seam_count')),
                        'c_seam_count': sub.get('c_seam_count', sub_row.get('c_seam_count', 0)),
                        ################################################################################################################################################################################################
                        # fix: Routing 상위 제약 (곡판/고심수 간격 & 후공정 착수 순서)
                        ################################################################################################################################################################################################
                        'has_curved_plate': sub.get('has_curved_plate', sub_row.get('has_curved_plate', False)),
                        'main_plate_count': sub.get('main_plate_count', sub_row.get('main_plate_count')),
                        'subassembly_group_size': group_size,
                        'subassembly_representative_id': base_row.get('subassembly_representative_id', sub_row.get('block_id')),
                        'is_subassembly_member': True,
                        'is_subassembly_representative': idx == 0,
                        'violations_representative': base_row.get('violations_representative', sub_row.get('violations', 0))
                    })
                    if idx > 0:
                        sub_row['is_subassembly_representative'] = False
                    expanded_rows.append(sub_row)
            else:
                if expansion and isinstance(expansion, list) and len(expansion) == 1:
                    sub = expansion[0]
                    single_line_group = DataConverter.resolve_line_group_value(
                        sub.get('line_group'),
                        sub.get('assembly_type', assembly_type),
                        sub.get('assembly_workshop_code', base_assembly_code)
                    )
                    single_code = DataConverter.resolve_workshop_code_value(
                        sub.get('assembly_workshop_code'),
                        single_line_group,
                        sub.get('assembly_type', assembly_type)
                    )
                    base_row.update({
                        'block_id': sub.get('block_id', base_row.get('block_id')),
                        'block_name': sub.get('block_name', base_row.get('block_name')),
                        'port_starboard': sub.get('port_starboard', base_row.get('port_starboard')),
                        'assembly_type': sub.get('assembly_type', base_row.get('assembly_type')),
                        'line_group': single_line_group,
                        'assembly_workshop_code': single_code,
                        'block_assembly_date': sub.get('block_assembly_date', base_row.get('block_assembly_date')),
                        'width_m': sub.get('width_m', base_row.get('width_m')),
                        'longi_count': sub.get('longi_count', base_row.get('longi_count')),
                        'seam_count': sub.get('seam_count', base_row.get('seam_count')),
                        'c_seam_count': sub.get('c_seam_count', base_row.get('c_seam_count', 0)),
                        'has_curved_plate': sub.get('has_curved_plate', base_row.get('has_curved_plate', False)),
                        'main_plate_count': sub.get('main_plate_count', base_row.get('main_plate_count')),
                        'subassembly_group_size': group_size,
                        'subassembly_representative_id': base_row.get('subassembly_representative_id', base_row.get('block_id')),
                        'is_subassembly_member': group_size > 1,
                        'is_subassembly_representative': True,
                        'violations_representative': base_row.get('violations_representative', base_row.get('violations', 0))
                    })
                base_row['subassembly_group_size'] = group_size
                base_row['subassembly_representative_id'] = base_row.get('subassembly_representative_id', base_row.get('block_id'))
                base_row['is_subassembly_member'] = group_size > 1
                base_row['is_subassembly_representative'] = True
                base_row['violations_representative'] = base_row.get('violations_representative', base_row.get('violations', 0))
                expanded_rows.append(base_row)

        return expanded_rows

    @staticmethod
    def _validate_p5_9_constraint_pre_check(blocks: List[EnhancedBlock]) -> List[EnhancedBlock]:
        """
        P5#9: 제약 사전 검증 (시퀀싱 시작 전)

        제약조건: 72심이 넘을 때는 최소 블록 수를 17개 반영
        
        Args:
            blocks: 전체 블록 리스트
            
        Returns:
            검증 완료된 블록 리스트 (위반 시 경고만 출력)
        """
        # 착수일별로 블록들 그룹핑
        date_groups = {}
        for block in blocks:
            # 착수일 기준 (max_start_date 사용 - '착수일' 컬럼)
            date_key = block.max_start_date.strftime('%Y%m%d')
            if date_key not in date_groups:
                date_groups[date_key] = []
            date_groups[date_key].append(block)
        
        # 각 착수일별로 P5#9 제약 검증
        total_violations = 0
        
        for date_key in sorted(date_groups.keys()):
            blocks_in_date = date_groups[date_key]
            
            # 해당 날짜 총 심수 계산
            total_seam_count = sum(block.seam_count + getattr(block, 'c_seam_count', 0) for block in blocks_in_date)
            total_block_count = len(blocks_in_date)
            
            # 주말 여부 확인
            sample_date = blocks_in_date[0].max_start_date
            is_weekend = sample_date.weekday() >= 5  # 5=토요일, 6=일요일
            
            # P5#9 제약 검증 (평일만)
            if not is_weekend and total_seam_count > 72:
                if total_block_count < 17:
                    total_violations += 1
        
        # 검증 결과 체크
        if total_violations > 0:
            # 위반이 있어도 일단 진행 (실제 환경에서는 사전 조정 필요)
            pass
        
        return blocks

    @staticmethod
    def _calculate_panel_start_date(assembly_start_date: datetime, assembly_type: AssemblyType) -> datetime:
        """
        P5#1 제약조건: 조립 타입별 판넬 최대 착수일 계산
        
        제약조건:
        - 라인조립: 조립 착수일 3일 전 (Working day 기준)
        - 고정조립: 조립 착수일 1일 전 (Working day 기준)
        - 사외(M): 조립 착수일 3일 전 (Working day 기준)
        - 사내(A): 조립 착수일 1.5일 전 (Working day 기준)
        
        Args:
            assembly_start_date: 조립 착수일
            assembly_type: 조립 타입
            
        Returns:
            판넬 최대 착수일 (이 일자 이후로는 착수 불가)
        """
        if assembly_type == AssemblyType.LINE:
            # 라인조립: 3일 전
            days_before = 3
        elif assembly_type == AssemblyType.FIXED:
            # 고정조립: 1일 전
            days_before = 1
        elif assembly_type == AssemblyType.EXTERNAL_M:
            # 사외(M): 3일 전
            days_before = 3
        elif assembly_type == AssemblyType.INTERNAL_A:
            # 사내(A): 1.5일 전 (36시간)
            panel_start_date = assembly_start_date - timedelta(hours=36)
            return panel_start_date.replace(hour=22, minute=0, second=0, microsecond=0)  # 22시까지 착수 가능
        else:
            # 기본값: 2일 전
            days_before = 2
        
        # Working day 기준으로 계산 (단순화: 주말도 포함)
        panel_start_date = assembly_start_date - timedelta(days=days_before)
        
        # 해당 날짜 22시까지 착수 가능 (P5#1: "해당 일정 이후에 판넬 착수 할 수 없음")
        return panel_start_date.replace(hour=22, minute=0, second=0, microsecond=0)

    @staticmethod
    def excel_to_blocks_with_metadata(excel_path: str, sheet_name: Optional[str] = None) -> Tuple[List[EnhancedBlock], Dict]:
        """
        실제 데이터셋 엑셀 파일을 EnhancedBlock 리스트와 메타데이터로 변환
        
        Returns:
            (블록 리스트, 메타데이터 딕셔너리)
        """
        try:
            # 기존 블록 생성 로직 사용
            blocks = DataConverter.excel_to_blocks(excel_path, sheet_name)
            
            # 메타데이터 생성
            metadata = DataConverter._generate_complete_metadata(blocks)
            resolved_sheet = DataConverter._last_resolved_data_sheet
            if resolved_sheet:
                excel_source_info = metadata.setdefault("excel_source", {})
                if isinstance(excel_source_info, dict):
                    excel_source_info["data_sheet"] = resolved_sheet

            # [AGENT-ADD] 런타임 캘린더 오버라이드 적용 (main.py config)
            calendar_overrides = build_calendar_overrides()
            if calendar_overrides:
                metadata["calendar_overrides"] = calendar_overrides
            
            print(f"✅ 블록 생성 완료: {len(blocks)}개")
            print(f"✅ 메타데이터 생성 완료: {len(metadata.keys())}개 카테고리")
            if resolved_sheet:
                print(f"🗂️ 사용된 데이터 시트: {resolved_sheet}")
            
            return blocks, metadata
            
        except Exception as e:
            raise ValueError(f"엑셀 파일 메타데이터 변환 실패: {e}")

    @staticmethod
    def _resolve_data_sheet_name(workbook: pd.ExcelFile, preferred_sheet: Optional[str]) -> str:
        """엑셀 데이터 시트 결정 (환경 변수 / 기본 우선순위 지원)"""
        sheet_names = list(workbook.sheet_names)
        if not sheet_names:
            raise ValueError("엑셀 파일에 시트가 존재하지 않습니다.")

        if preferred_sheet:
            candidate = str(preferred_sheet).strip()
            if candidate not in sheet_names:
                available = ", ".join(sheet_names)
                raise ValueError(f"요청한 시트 '{candidate}'를 찾을 수 없습니다. 사용 가능한 시트: {available}")
            DataConverter._last_resolved_data_sheet = candidate
            return candidate

        env_priority = os.getenv("EXCEL_DATA_SHEET_PRIORITY")
        candidates: List[str] = []
        if env_priority:
            env_parts = [part.strip() for part in env_priority.split(",") if part.strip()]
            candidates.extend(env_parts)

        # 기본 우선순위
        candidates.extend(
            [
                "기존데이터",
                "블록데이터",
                "Sheet1",
                "데이터",
                "data",
            ]
        )

        # 시트 순회 시 중복 제거
        seen = set()
        ordered_candidates: List[str] = []
        for name in candidates + sheet_names:
            if not name or name in seen:
                continue
            seen.add(name)
            ordered_candidates.append(name)

        required_columns = {"블록번호", "소조번호"}

        for candidate in ordered_candidates:
            if candidate in sheet_names and DataConverter._sheet_has_required_columns(workbook, candidate, required_columns):
                DataConverter._last_resolved_data_sheet = candidate
                return candidate

        # 필요한 열이 없으면 첫 번째 시트를 반환 (마지막 수단)
        fallback_sheet = sheet_names[0]
        DataConverter._last_resolved_data_sheet = fallback_sheet
        return fallback_sheet

    @staticmethod
    def _sheet_has_required_columns(
        workbook: pd.ExcelFile,
        sheet_name: str,
        required_columns: Optional[Set[str]] = None,
    ) -> bool:
        """시트가 필요한 컬럼을 포함하는지 확인"""
        try:
            df_preview = workbook.parse(sheet_name, nrows=1)
        except Exception:
            return False

        columns = {str(col).strip() for col in df_preview.columns}
        if not required_columns:
            return True
        return required_columns.issubset(columns)

    @staticmethod
    def dataframe_to_blocks_with_metadata(df: pd.DataFrame) -> Tuple[List[EnhancedBlock], Dict]:
        """
        DataFrame을 EnhancedBlock 리스트와 메타데이터로 변환 (PPO용)
        
        Args:
            df: optimized_block_generator에서 생성된 DataFrame
            
        Returns:
            (블록 리스트, 메타데이터 딕셔너리)
        """
        try:
            blocks = []

            # 1단계: 모든 블록 기본 정보 수집
            block_data_list = []
            assembly_meta = {}
            for idx, row in df.iterrows():
                try:
                    # 기본 정보 추출
                    block_id = int(row.get('block_id', idx + 1))
                    block_name = str(row.get('블록번호', f'BLK_{block_id}'))
                    
                    # 소조번호 추출
                    sub_assembly_number = str(row.get('소조번호', f'TP_{block_id}'))
                    
                    # P/S/C 구분 (블록번호 마지막 문자 또는 block_type)
                    if 'block_type' in row and row['block_type'] in ['P', 'S', 'ORPHAN_P', 'ORPHAN_S']:
                        if row['block_type'] in ['P', 'ORPHAN_P']:
                            port_starboard = PortStarboard.PORT
                        else:
                            port_starboard = PortStarboard.STARBOARD
                    elif 'block_type' in row and row['block_type'] == 'CENTER':
                        port_starboard = PortStarboard.CENTER
                    else:
                        port_starboard = DataConverter._parse_block_ps_type(block_name)
                    
                    # 물리적 특성
                    length = float(row.get('길이', 20.0))
                    width = float(row.get('폭', 15.0))
                    min_thickness = float(row.get('최소두께', 10.0))
                    max_thickness = float(row.get('최대두께', 30.0))
                    
                    # 론지 개수
                    longi_count = int(row.get('판넬 론지 수', 10))
                    
                    # 심수 계산
                    panel_seam_count = int(row.get('판넬 SEAM 수', 1))
                    panel_cseam_count = int(row.get('판넬 C/SEAM 수', 0))
                    seam_count = panel_seam_count
                    
                    # 부재 개수
                    flat_plate_count = int(row.get('평판 수', 5))
                    curved_plate_count = int(row.get('곡판 수', 0))
                    angle_count = int(row.get('앵글 수', 0))
                    buildup_count = int(row.get('B/UP 수', 0))
                    
                    # 주판 개수 계산
                    main_plate_count = flat_plate_count + curved_plate_count
                    
                    # 순번 (기본값: block_id)
                    sequence_number = int(row.get('sequence_number', block_id))
                    
                    # 베이 힌트 (기본값: A베이)
                    bay_hint = BayType.BAY_35A
                    
                    # 날짜 변환
                    assembly_start_date_str = str(row.get('조립착수일', '20250101'))
                    if len(assembly_start_date_str) == 8 and assembly_start_date_str.isdigit():
                        year = int(assembly_start_date_str[:4])
                        month = int(assembly_start_date_str[4:6])
                        day = int(assembly_start_date_str[6:8])
                        assembly_start_date = datetime(year, month, day, 8, 0, 0)
                    else:
                        assembly_start_date = datetime.now() + timedelta(days=30)
                    
                    # 착수일은 조립착수일과 동일하게 설정 (생성된 데이터용)
                    panel_start_date = assembly_start_date
                    
                    # 조립 타입 (기본값: LINE)
                    assembly_type = AssemblyType.LINE
                    assembly_type_meta_value = str(row.get('assembly_type_meta', row.get('assembly_type', ''))).strip().lower()
                    if assembly_type_meta_value:
                        try:
                            assembly_type = AssemblyType(assembly_type_meta_value)
                        except ValueError:
                            assembly_type = AssemblyType.LINE

                    # FAB 데이터 (기본값: 0)
                    is_fab_block = False

                    # 조립 작업장/라인 그룹 메타데이터 기본값
                    assembly_workshop_code = str(row.get('assembly_workshop_code', '')).strip()
                    assembly_workshop_code_raw = assembly_workshop_code
                    line_group = str(row.get('line_group', '')).strip()

                    if not line_group and assembly_workshop_code:
                        line_group = DataConverter._extract_line_group(assembly_workshop_code) or ''

                    if not line_group:
                        line_group = DataConverter._line_group_from_longi_workshop(row.get('longi_workshop', 1)) or ''

                    if not line_group and assembly_type == AssemblyType.LINE:
                        line_group = 'L_1'

                    if not assembly_workshop_code:
                        assembly_workshop_code = line_group if line_group else 'L_1'
                        assembly_workshop_code_raw = assembly_workshop_code

                    # 총 무게 추정
                    avg_thickness = (min_thickness + max_thickness) / 2.0 / 1000.0
                    steel_density = 7.85
                    total_weight = length * width * avg_thickness * steel_density
                    
                    # 용접장 정보
                    total_seam_length = float(row.get('판넬 SEAM 용접장', length * 2))
                    longi_length = float(row.get('판넬 론지 용접장', width * longi_count))
                    
                    # 제약조건 속성
                    is_draft = False
                    is_cross_seam = False
                    is_main_plate_only = (angle_count == 0 and buildup_count == 0)
                    
                    # Tact Time 추출 또는 계산
                    tact_time_columns = ['판계 Tact Time', '전면SAW Tact Time', 'TurnOver Tact Time', 
                                       '후면SAW Tact Time', 'NC Tact Time', '론지취부 Tact Time', 
                                       '론지용접 Tact Time', '수정 Tact Time']
                    
                    processing_times = []
                    has_actual_tact_time = True
                    
                    for col in tact_time_columns:
                        if col in row and pd.notna(row[col]):
                            processing_times.append(float(row[col]))
                        else:
                            has_actual_tact_time = False
                            break
                    
                    if not has_actual_tact_time or len(processing_times) != 8:
                        # Tact Time 계산
                        processing_times = DataConverter._generate_processing_times(
                            seam_count=panel_seam_count,
                            longi_count=longi_count,
                            main_plate_count=main_plate_count,
                            angle_count=angle_count,
                            buildup_count=buildup_count,
                            width=width,
                            length=length,
                            max_thickness=max_thickness,
                            min_thickness=min_thickness,
                            total_weight=total_weight,
                            total_seam_length=total_seam_length,
                            longi_length=longi_length,
                            is_step_block=False,
                            is_special_block=(main_plate_count > 10),
                            c_seam_count=panel_cseam_count,
                            assembly_type="LINE"
                        )
                        has_actual_tact_time = False
                    
                    # 블록 데이터 저장
                    block_data = {
                        'block_id': block_id,
                        'block_name': block_name,
                        'sub_assembly_number': sub_assembly_number,
                        'sequence_number': sequence_number,
                        'processing_times': processing_times,
                        'has_actual_tact_time': has_actual_tact_time,
                        'max_start_date': panel_start_date,
                        'assembly_type': assembly_type,
                        'port_starboard': port_starboard,
                        'pair_block_id': int(row['pair_block_id']) if 'pair_block_id' in row and pd.notna(row['pair_block_id']) else None,
                        'assembly_start_date': assembly_start_date,
                        'is_fab': is_fab_block,
                        'seam_count': seam_count,
                        'c_seam_count': panel_cseam_count,
                        'width': width,
                        'length': length,                    # 🔥 누락된 length 추가
                        'min_thickness': min_thickness,      # 🔥 누락된 min_thickness 추가
                        'max_thickness': max_thickness,      # 🔥 누락된 max_thickness 추가
                        'longi_count': longi_count,
                        'is_draft': is_draft,
                        'is_cross_seam': is_cross_seam,
                        'main_plate_count': main_plate_count,
                        'angle_count': angle_count,
                        'buildup_count': buildup_count,
                        'is_main_plate_only': is_main_plate_only,
                        'assigned_bay': bay_hint,
                        'material_ready': True,
                        'assembly_workshop_code': assembly_workshop_code,
                        'assembly_workshop_code_raw': assembly_workshop_code_raw,
                        'line_group': line_group,
                        'curved_plate_count': curved_plate_count,
                        'has_curved_plate': curved_plate_count > 0,
                        'is_high_seam_block': seam_count >= 6,
                        'assembly_type_meta': assembly_type_meta_value.upper() if assembly_type_meta_value else assembly_type.value.upper()
                    }
                    
                    block_data_list.append(block_data)
                    assembly_meta[block_id] = {
                        'assembly_workshop_code': assembly_workshop_code,
                        'assembly_workshop_code_raw': assembly_workshop_code_raw,
                        'line_group': line_group,
                        'assembly_type_meta': assembly_type_meta_value.upper() if assembly_type_meta_value else assembly_type.value.upper()
                    }

                except Exception as e:
                    if VERBOSE_CONVERSION:
                        print(f"⚠️ 블록 {idx+1} 처리 실패, 스킵: {e}")
                    continue
            
            if VERBOSE_CONVERSION:
                print(f"📊 총 {len(block_data_list)}개 블록 데이터 수집 완료")
            
            # 2단계: P/S 쌍 정보 처리 (DataFrame의 pair_block_id 사용)
            ps_pairs = {}
            for block_data in block_data_list:
                if block_data['pair_block_id']:
                    ps_pairs[block_data['block_id']] = block_data['pair_block_id']
            
            if VERBOSE_CONVERSION:
                print(f"🔗 P/S 쌍 매칭: {len(ps_pairs)//2}쌍")
            
            # 3단계: EnhancedBlock 객체 생성
            for block_data in block_data_list:
                try:
                    block = EnhancedBlock(
                        block_id=block_data['block_id'],
                        processing_times=block_data['processing_times'],
                        max_start_date=block_data['max_start_date'],
                        assembly_type=block_data['assembly_type'],
                        port_starboard=block_data['port_starboard'],
                        pair_block_id=block_data['pair_block_id'],
                        assembly_start_date=block_data['assembly_start_date'],
                        is_fab=block_data['is_fab'],
                        seam_count=block_data['seam_count'],
                        width=block_data['width'],
                        length=block_data['length'],                    # 🔥 누락된 length 추가
                        min_thickness=block_data['min_thickness'],      # 🔥 누락된 min_thickness 추가
                        max_thickness=block_data['max_thickness'],      # 🔥 누락된 max_thickness 추가
                        longi_count=block_data['longi_count'],
                        is_draft=block_data['is_draft'],
                        is_cross_seam=block_data['is_cross_seam'],
                        main_plate_count=block_data['main_plate_count'],
                        angle_count=block_data['angle_count'],
                        buildup_count=block_data['buildup_count'],
                        is_main_plate_only=block_data['is_main_plate_only'],
                        block_number=block_data['block_id'],
                        assigned_bay=block_data['assigned_bay'],
                        material_ready=block_data['material_ready'],
                        assembly_workshop_code=block_data['assembly_workshop_code'],
                        line_group=block_data['line_group'],
                        curved_plate_count=block_data['curved_plate_count'],
                        has_curved_plate=block_data['has_curved_plate'],
                        is_high_seam_block=block_data['is_high_seam_block'],
                        c_seam_count=block_data.get('c_seam_count', 0)
                    )

                    # 추가 속성 설정
                    setattr(block, 'c_seam_count', block_data.get('c_seam_count', 0))
                    setattr(block, 'sub_assembly_number', block_data['sub_assembly_number'])
                    setattr(block, 'block_name', block_data['block_name'])
                    setattr(block, 'sequence_number', block_data['sequence_number'])
                    setattr(block, 'has_actual_tact_time', block_data['has_actual_tact_time'])
                    setattr(block, 'assembly_workshop_code', block_data['assembly_workshop_code'])  # 원본 작업장 코드 보존
                    setattr(block, 'assembly_workshop_code_raw', block_data.get('assembly_workshop_code_raw', block_data['assembly_workshop_code']))
                    setattr(block, 'line_group', block_data['line_group'])  # 라인 그룹 (L1/L2/FIX)
                    setattr(block, 'curved_plate_count', block_data['curved_plate_count'])  # 곡판 수
                    setattr(block, 'has_curved_plate', block_data['has_curved_plate'])  # 곡판 존재 여부
                    setattr(block, 'is_high_seam_block', block_data['is_high_seam_block'])  # SEAM ≥ 6 여부
                    setattr(block, 'assembly_type_meta', block_data.get('assembly_type_meta', 'LINE'))

                    blocks.append(block)
                    
                except Exception as e:
                    if VERBOSE_CONVERSION:
                        print(f"⚠️ 블록 객체 생성 실패, 스킵: {e}")
                    continue
            
            if VERBOSE_CONVERSION:
                print(f"✅ 총 {len(blocks)}개 블록 객체 생성 완료")
            
            # 별판 처리 및 상세 분석
            blocks = DataConverter._process_subassembly_grouping(blocks)
            
            # 순번 정렬
            blocks = DataConverter._sort_blocks_by_sequence_number(blocks)

            # 별판 처리 등으로 손실될 수 있는 조립 작업장 메타데이터 재적용
            for block in blocks:
                meta = assembly_meta.get(block.block_id)
                if not meta:
                    continue
                raw_code = meta.get('assembly_workshop_code_raw') or meta.get('assembly_workshop_code') or ''
                code = meta.get('assembly_workshop_code') or raw_code
                line_group = meta.get('line_group')
                assembly_type_meta_value = meta.get('assembly_type_meta')

                if raw_code is not None:
                    setattr(block, 'assembly_workshop_code_raw', raw_code)
                if code is not None:
                    setattr(block, 'assembly_workshop_code', code)
                if line_group is not None:
                    setattr(block, 'line_group', line_group)
                if assembly_type_meta_value:
                    try:
                        block.assembly_type = AssemblyType(assembly_type_meta_value.lower())
                    except ValueError:
                        pass
                    setattr(block, 'assembly_type_meta', assembly_type_meta_value)
                    setattr(block, 'assembly_type_meta', assembly_type_meta_value)

            # P5#9 제약 사전 검증
            blocks = DataConverter._validate_p5_9_constraint_pre_check(blocks)
            
            # 메타데이터 생성
            metadata = DataConverter._generate_complete_metadata(blocks)
            
            if VERBOSE_CONVERSION:
                print(f"✅ 블록 생성 완료: {len(blocks)}개")
                print(f"✅ 메타데이터 생성 완료: {len(metadata.keys())}개 카테고리")
            
            return blocks, metadata
            
        except Exception as e:
            raise ValueError(f"DataFrame 변환 실패: {e}")
    
    @staticmethod
    def _generate_complete_metadata(blocks: List[EnhancedBlock]) -> Dict:
        """완전한 메타데이터 생성"""
        metadata = {
            'daily_structure': DataConverter._extract_daily_structure(blocks),
            'ps_pair_complete_info': DataConverter._extract_ps_pair_complete_info(blocks),
            'subassembly_complete_info': DataConverter._extract_subassembly_complete_info(blocks),
            'constraint_application_map': DataConverter._extract_constraint_application_map(blocks),
            'bay_assignment_strategy': DataConverter._extract_bay_assignment_strategy(blocks),
            'fab_interval_tracking': DataConverter._extract_fab_interval_tracking(blocks),
            'assembly_mixing_control': DataConverter._extract_assembly_mixing_control(blocks),
            'cross_seam_mixing_control': DataConverter._extract_cross_seam_mixing_control(blocks),
            'block_10_special_rule': DataConverter._extract_block_10_special_rule(blocks),
            'constraint_conflicts': DataConverter._extract_constraint_conflicts(),
            'dynamic_state_tracking': DataConverter._initialize_dynamic_state_tracking()
        }
        
        return metadata
    
    @staticmethod
    def _extract_daily_structure(blocks: List[EnhancedBlock]) -> Dict:
        """일별 구조 정보 추출"""
        daily_structure = {}
        
        # 날짜별 그룹핑
        date_groups = {}
        for block in blocks:
            date_key = block.max_start_date.strftime('%Y%m%d')
            if date_key not in date_groups:
                date_groups[date_key] = []
            date_groups[date_key].append(block)
        
        for date_key, blocks_in_date in date_groups.items():
            # 날짜 파싱
            year, month, day = int(date_key[:4]), int(date_key[4:6]), int(date_key[6:8])
            date_obj = datetime(year, month, day)
            
            # 별판 및 P/S 쌍 카운트
            subassembly_count = sum(1 for b in blocks_in_date if getattr(b, 'is_subassembly', False))
            ps_pair_count = sum(1 for b in blocks_in_date if b.is_p_s_pair()) // 2
            
            # 순번 매핑 생성
            sequence_to_blocks = {}
            blocks_to_sequence = {}
            
            for block in blocks_in_date:
                seq_num = getattr(block, 'sequence_number', 0)
                
                if seq_num not in sequence_to_blocks:
                    sequence_to_blocks[seq_num] = []
                
                # 별판인 경우 원본 블록들 추가
                if getattr(block, 'is_subassembly', False):
                    original_blocks = getattr(block, 'subassembly_original_blocks', [block])
                    for orig_block in original_blocks:
                        sequence_to_blocks[seq_num].append(orig_block.block_id)
                        blocks_to_sequence[orig_block.block_id] = seq_num
                else:
                    sequence_to_blocks[seq_num].append(block.block_id)
                    blocks_to_sequence[block.block_id] = seq_num
            
            # 제약조건 프로파일
            afternoon_start_blocks = [b.block_id for b in blocks_in_date if b.needs_afternoon_start()]
            wide_blocks = [b.block_id for b in blocks_in_date if b.width > 21.0]
            high_longi_blocks = [b.block_id for b in blocks_in_date if b.longi_count >= 30]
            main_plate_only_blocks = [b.block_id for b in blocks_in_date if b.is_main_plate_only]
            
            daily_structure[date_key] = {
                'date': date_key,
                'date_obj': date_obj,
                'is_weekend': date_obj.weekday() >= 5,
                'is_holiday': False,  # 추후 캘린더 매니저에서 확인
                'is_hot_season': False,  # 추후 캘린더 매니저에서 확인
                'seam_capacity': 45 if date_obj.weekday() >= 5 else 75,
                'block_capacity': 17,
                'current_seam_used': 0,
                'current_block_count': 0,
                'total_original_blocks': len(blocks_in_date) + subassembly_count,  # 별판 원본 개수 포함
                'total_unified_blocks': len(blocks_in_date),
                'subassembly_count': subassembly_count,
                'ps_pair_count': ps_pair_count,
                'sequence_to_blocks': sequence_to_blocks,
                'blocks_to_sequence': blocks_to_sequence,
                'afternoon_start_blocks': afternoon_start_blocks,
                'wide_blocks': wide_blocks,
                'high_longi_blocks': high_longi_blocks,
                'main_plate_only_blocks': main_plate_only_blocks
            }
        
        return daily_structure
    
    @staticmethod
    def _extract_ps_pair_complete_info(blocks: List[EnhancedBlock]) -> Dict:
        """P/S 쌍 완전 정보 추출"""
        ps_complete_info = {}
        
        # P/S 쌍 매핑 생성
        ps_pairs = {}
        for block in blocks:
            if block.is_p_s_pair() and block.pair_block_id:
                ps_pairs[block.block_id] = block.pair_block_id
        
        # 각 P/S 쌍에 대한 완전한 정보 생성
        processed_pairs = set()
        
        for block in blocks:
            if (block.is_p_s_pair() and 
                block.port_starboard == PortStarboard.PORT and
                block.block_id not in processed_pairs):
                
                port_block = block
                starboard_id = block.pair_block_id
                starboard_block = next((b for b in blocks if b.block_id == starboard_id), None)
                
                if starboard_block:
                    # 날짜 차이 계산
                    date_diff = abs((port_block.assembly_start_date.date() - 
                                   starboard_block.assembly_start_date.date()).days)
                    
                    # 연속성 요구 사항
                    require_continuous_line = (port_block.assembly_type == AssemblyType.LINE)
                    require_continuous_fixed = (port_block.assembly_type == AssemblyType.FIXED and date_diff <= 1)
                    
                    # 동일 베이 요구 사항
                    require_same_bay = (port_block.longi_count < 7 and starboard_block.longi_count < 7)
                    
                    # 물리적 제약
                    any_width_over_21 = (port_block.width > 21.0 or starboard_block.width > 21.0)
                    port_longi_over_30 = (port_block.longi_count >= 30)
                    starboard_longi_over_30 = (starboard_block.longi_count >= 30)
                    
                    ps_info = {
                        'port_block_id': port_block.block_id,
                        'starboard_block_id': starboard_block.block_id,
                        'assembly_type': port_block.assembly_type.value,
                        'assembly_start_date_port': port_block.assembly_start_date.strftime('%Y%m%d'),
                        'assembly_start_date_starboard': starboard_block.assembly_start_date.strftime('%Y%m%d'),
                        'date_difference_days': date_diff,
                        'require_continuous_line': require_continuous_line,
                        'require_continuous_fixed': require_continuous_fixed,
                        'port_longi_count': port_block.longi_count,
                        'starboard_longi_count': starboard_block.longi_count,
                        'require_same_bay': require_same_bay,
                        'port_priority': True,
                        'port_width': port_block.width,
                        'starboard_width': starboard_block.width,
                        'any_width_over_21': any_width_over_21,
                        'port_longi_over_30': port_longi_over_30,
                        'starboard_longi_over_30': starboard_longi_over_30
                    }
                    
                    ps_complete_info[port_block.block_id] = ps_info
                    ps_complete_info[starboard_block.block_id] = ps_info  # 양방향 접근
                    
                    processed_pairs.add(port_block.block_id)
                    processed_pairs.add(starboard_block.block_id)
        
        return ps_complete_info
    
    @staticmethod
    def _extract_subassembly_complete_info(blocks: List[EnhancedBlock]) -> Dict:
        """별판 완전 정보 추출 (물리적/구조적 특성 포함)"""
        subassembly_info = {}
        
        for block in blocks:
            if getattr(block, 'is_subassembly', False):
                original_blocks = getattr(block, 'subassembly_original_blocks', [block])
                
                # P/S 쌍 여부 확인
                is_ps_pair = block.is_p_s_pair()
                port_id = None
                starboard_id = None
                
                if is_ps_pair and len(original_blocks) == 2:
                    for orig_block in original_blocks:
                        if orig_block.port_starboard == PortStarboard.PORT:
                            port_id = orig_block.block_id
                        elif orig_block.port_starboard == PortStarboard.STARBOARD:
                            starboard_id = orig_block.block_id
                
                # ✅ 물리적/구조적 특성 정보 추출
                subassembly_characteristics = getattr(block, 'subassembly_characteristics', {})
                
                subassembly_data = {
                    'unified_block_id': block.block_id,
                    'original_block_ids': [b.block_id for b in original_blocks],
                    'original_block_names': [getattr(b, 'block_name', f'BLK_{b.block_id}') for b in original_blocks],
                    'subassembly_numbers': [getattr(b, 'sub_assembly_number', f'SUB_{b.block_id}') for b in original_blocks],
                    'is_ps_pair': is_ps_pair,
                    'port_id': port_id,
                    'starboard_id': starboard_id,
                    'processing_method': 'UNIFIED',
                    'result_method': 'SEPARATE',
                    'processing_times_identical': True,
                    'sequence_number': getattr(block, 'sequence_number', 0),
                    'count_in_consecutive': 1,
                    'atomic_unit': True,
                    'split_point': 'BRANCH',
                    'common_process_unified': True,
                    'branch_process_separate': True,
                    
                    # ✅ 물리적/구조적 특성 정보 추가
                    'physical_characteristics': {
                        'unified_width': subassembly_characteristics.get('unified_width', block.width),
                        'unified_seam_count': subassembly_characteristics.get('unified_seam_count', block.seam_count),
                        'unified_is_cross_seam': subassembly_characteristics.get('unified_is_cross_seam', block.is_cross_seam),
                        'unified_longi_count': subassembly_characteristics.get('unified_longi_count', block.longi_count),
                        'unified_length': subassembly_characteristics.get('unified_length', block.length),
                        'unified_min_thickness': subassembly_characteristics.get('unified_min_thickness', block.min_thickness),
                        'unified_max_thickness': subassembly_characteristics.get('unified_max_thickness', block.max_thickness),
                        'characteristic_matching_criteria': 'width + seam_count + is_cross_seam + longi_count + length + min_thickness + max_thickness',
                        'physical_key': subassembly_characteristics.get('physical_key', block.get_physical_characteristics_key()),
                        'matching_method': 'EnhancedBlock.get_physical_characteristics_key()'
                    },
                    
                    # ✅ 별판 검증 정보 추가
                    'validation_info': {
                        'grouping_criteria': '착수일 + 블록명 + 물리적특성 완전일치 + 소조번호 다름',
                        'physical_criteria_implemented': 7,  # width, seam_count, is_cross_seam, longi_count, length, min_thickness, max_thickness
                        'criteria_details': {
                            'basic_criteria': ['width', 'seam_count', 'is_cross_seam', 'longi_count'],
                            'extended_criteria': ['length', 'min_thickness', 'max_thickness']
                        },
                        'characteristic_key': subassembly_characteristics.get('characteristic_key', 'N/A'),
                        'enhanced_validation': True
                    }
                }
                
                subassembly_info[block.block_id] = subassembly_data
        
        return subassembly_info
    
    @staticmethod
    def _extract_constraint_application_map(blocks: List[EnhancedBlock]) -> Dict:
        """제약조건 적용 맵 추출"""
        constraint_map = {
            'SEQUENCE_DECISION': {
                'P5#3_line_ps_continuous': [],
                'P5#4_fixed_ps_continuous': [],
                'P5#17_subassembly_unified': []
            },
            'BRANCH_SELECTION': {
                'P7#2_width_over_21': [],
                'P7#3_ps_same_bay': [],
                'P7#7_consecutive_limit': [],
                'P7#10_longi_over_30': [],
                'P7#11_block_10_special': []
            },
            'RESULT_EXPANSION': {
                'subassembly_expansion': [],
                'ps_pair_validation': []
            }
        }
        
        # 각 제약조건별 해당 블록들 수집
        for block in blocks:
            block_id = block.block_id
            
            # P5#3: 라인 P/S 연속
            if (block.is_p_s_pair() and block.assembly_type == AssemblyType.LINE):
                if block.port_starboard == PortStarboard.PORT:
                    constraint_map['SEQUENCE_DECISION']['P5#3_line_ps_continuous'].append(
                        (block_id, block.pair_block_id)
                    )
            
            # P5#4: 고정 P/S 연속 (날짜 차이 ≤ 1일)
            if (block.is_p_s_pair() and 
                block.assembly_type == AssemblyType.FIXED and 
                block.port_starboard == PortStarboard.PORT):
                
                starboard_block = next((b for b in blocks if b.block_id == block.pair_block_id), None)
                if starboard_block:
                    date_diff = abs((block.assembly_start_date.date() - 
                                   starboard_block.assembly_start_date.date()).days)
                    if date_diff <= 1:
                        constraint_map['SEQUENCE_DECISION']['P5#4_fixed_ps_continuous'].append(
                            (block_id, block.pair_block_id)
                        )
            
            # P5#17: 별판 통합
            if getattr(block, 'is_subassembly', False):
                constraint_map['SEQUENCE_DECISION']['P5#17_subassembly_unified'].append(block_id)
                constraint_map['RESULT_EXPANSION']['subassembly_expansion'].append(block_id)
            
            # P7#2: 21m 초과 → B베이
            if block.width > 21.0:
                constraint_map['BRANCH_SELECTION']['P7#2_width_over_21'].append(block_id)
            
            # P7#3: P/S 동일 베이
            if (block.is_p_s_pair() and block.longi_count < 7):
                constraint_map['BRANCH_SELECTION']['P7#3_ps_same_bay'].append(
                    (block_id, block.pair_block_id)
                )
            
            # P7#7: 연속 제한 (별판 1개 카운트)
            if getattr(block, 'is_subassembly', False):
                constraint_map['BRANCH_SELECTION']['P7#7_consecutive_limit'].append(block_id)
            
            # P7#10: 론지 ≥ 30 → A베이
            if block.longi_count >= 30:
                constraint_map['BRANCH_SELECTION']['P7#10_longi_over_30'].append(block_id)
            
            # P7#11: 10번 블록 특별 처리
            if hasattr(block, 'block_number') and block.block_number == 10:
                constraint_map['BRANCH_SELECTION']['P7#11_block_10_special'].append(block_id)
            
            # P/S 쌍 검증
            if block.is_p_s_pair():
                constraint_map['RESULT_EXPANSION']['ps_pair_validation'].append(block_id)
        
        return constraint_map
    
    @staticmethod
    def _extract_bay_assignment_strategy(blocks: List[EnhancedBlock]) -> Dict:
        """베이 할당 전략 추출"""
        bay_strategy = {}
        
        for block in blocks:
            constraints = []
            priority_order = []
            
            # P7#2: 21m 초과 → B베이 강제 (최우선)
            if block.width > 21.0:
                constraints.append('P7#2_width_21')
                priority_order.append('P7#2')
            
            # P7#3: P/S 동일 베이
            if block.is_p_s_pair() and block.longi_count < 7:
                constraints.append('P7#3_same_bay')
                priority_order.append('P7#3')
            
            # P7#10: 론지 ≥ 30 → A베이 우선
            if block.longi_count >= 30:
                constraints.append('P7#10_longi_30')
                priority_order.append('P7#10')
            
            # P7#7: 연속 제한
            constraints.append('P7#7_consecutive')
            priority_order.append('P7#7')
            
            # P7#8: 주판 Only 연속 제한
            if block.is_main_plate_only:
                constraints.append('P7#8_main_plate')
                priority_order.append('P7#8')
            
            # P7#11: 10번 블록 특별
            if hasattr(block, 'block_number') and block.block_number == 10:
                constraints.append('P7#11_block_10')
                priority_order.append('P7#11')
            
            bay_strategy[block.block_id] = {
                'physical_constraints': constraints,
                'priority_order': priority_order
            }
        
        return bay_strategy
    
    @staticmethod
    def _extract_fab_interval_tracking(blocks: List[EnhancedBlock]) -> Dict:
        """P5#6: FAB 간격 추적 정보 추출"""
        condition_blocks = []
        
        for block in blocks:
            # 라인 B + 3심 이상 + FAB 조건
            if (block.assembly_type == AssemblyType.LINE and
                block.seam_count >= 3 and
                block.is_fab):
                condition_blocks.append(block.block_id)
        
        return {
            'condition_blocks': condition_blocks,
            'interval_requirement': 5,
            'current_counter': 0,
            'last_condition_block_position': -1
        }
    
    @staticmethod
    def _extract_assembly_mixing_control(blocks: List[EnhancedBlock]) -> Dict:
        """P5#11,12: 혼합 배정 제어 정보 추출"""
        return {
            'last_assembly_type': None,
            'consecutive_count': 0,
            'max_consecutive_limit': 1,
            'mixing_pattern': "ALTERNATING"
        }
    
    @staticmethod
    def _extract_cross_seam_mixing_control(blocks: List[EnhancedBlock]) -> Dict:
        """P6#4: 혼합 배치 제어 정보 추출"""
        cross_seam_blocks = []
        normal_blocks = []
        
        for block in blocks:
            if (block.is_cross_seam or block.is_draft or block.main_plate_count > 10):
                cross_seam_blocks.append(block.block_id)
            elif 2 <= block.seam_count <= 4:
                normal_blocks.append(block.block_id)
        
        return {
            'cross_seam_blocks': cross_seam_blocks,
            'normal_blocks': normal_blocks,
            'consecutive_special_count': 0,
            'max_consecutive_special': 2
        }
    
    @staticmethod
    def _extract_block_10_special_rule(blocks: List[EnhancedBlock]) -> Dict:
        """P7#11: 10번 블록 특별 규칙 추출"""
        block_10_ids = []
        
        block_number_ids = []
        longi30_ids = []

        # constraint_config 인스턴스를 안전하게 조회
        constraint_config = None
        if blocks:
            example_block = blocks[0]
            constraint_config = getattr(example_block, 'constraint_config', None)

        treat_longi = False
        if constraint_config is not None:
            treat_longi = getattr(constraint_config, 'treat_longi30_as_block10', False)

        for block in blocks:
            if hasattr(block, 'block_number') and block.block_number == 10:
                block_number_ids.append(block.block_id)
            if treat_longi and block.longi_count >= 30:
                longi30_ids.append(block.block_id)

        return {
            'block_10_ids': block_number_ids,
            'longi30_block_ids': longi30_ids,
            'after_block_10_counter': 0,
            'required_b_bay_count': 2,
            'is_block_10_processed': False
        }
    
    @staticmethod
    def _extract_constraint_conflicts() -> Dict:
        """제약조건 충돌 처리 규칙"""
        return {
            "P7#2_vs_P7#10": "P7#2_PRIORITY",
            "P7#2_vs_P7#12": "P7#2_PRIORITY", 
            "P5#11_vs_no_choice": "MASKING_RELEASE",
            "P6#4_vs_no_choice": "MASKING_RELEASE"
        }

################################################################################################################################################################################################
# fix: Assembly 타입/라인 그룹 보정 유틸리티
################################################################################################################################################################################################
    @staticmethod
    def resolve_line_group_value(line_group, assembly_type, assembly_code) -> str:
        value = (line_group or '').strip()
        code = (assembly_code or '').strip()
        type_str = DataConverter._assembly_type_to_str(assembly_type)

        if not value:
            if code.startswith(('L_', 'F_')):
                value = code

        if not value and type_str.lower() == 'fixed':
            value = 'F_1'

        normalized = DataConverter._normalize_line_group(value)
        return normalized

    @staticmethod
    def resolve_workshop_code_value(assembly_code, line_group, assembly_type) -> str:
        code = (assembly_code or '').strip()
        type_str = DataConverter._assembly_type_to_str(assembly_type)
        line_group = (line_group or '').strip()

        if code:
            return code
        if line_group:
            return line_group
        if type_str:
            return 'F_1' if type_str.lower() == 'fixed' else type_str
        return code

    @staticmethod
    def resolve_line_group_for_block(block) -> str:
        line_group = getattr(block, 'line_group', None)
        assembly_type = getattr(block, 'assembly_type', None)
        assembly_code = getattr(block, 'assembly_workshop_code', None)
        return DataConverter.resolve_line_group_value(line_group, assembly_type, assembly_code)

    @staticmethod
    def get_line_group_and_workshop_code(block):
        """라인 그룹 요약과 원본 조립 작업장 코드를 동시에 반환"""
        # 원본 코드 우선 보존
        raw_attr = getattr(block, 'assembly_workshop_code_raw', None)
        raw_code = str(raw_attr).strip() if raw_attr else ''

        current_code = getattr(block, 'assembly_workshop_code', None)
        current_code = str(current_code).strip() if current_code else ''

        if not raw_code:
            raw_code = current_code

        if not raw_code:
            raw_code = DataConverter.resolve_workshop_code_for_block(block)

        line_group_source = raw_code if raw_code else current_code
        line_group = DataConverter._normalize_line_group(line_group_source)
        if not line_group:
            existing = getattr(block, 'line_group', None)
            line_group = DataConverter._normalize_line_group(existing)
        if not line_group:
            resolved = DataConverter.resolve_line_group_for_block(block)
            line_group = DataConverter._normalize_line_group(resolved)

        if not line_group and str(getattr(block, 'assembly_type', '')).lower().endswith('line'):
            line_group = 'L_1'

        if not raw_code:
            raw_code = line_group if line_group else ''
        elif raw_code.lower() == 'line':
            raw_code = line_group if line_group else 'L_1'

        return line_group or '', raw_code or ''

    @staticmethod
    def resolve_workshop_code_for_block(block) -> str:
        assembly_code = getattr(block, 'assembly_workshop_code', None)
        line_group = DataConverter.resolve_line_group_for_block(block)
        assembly_type = getattr(block, 'assembly_type', None)
        return DataConverter.resolve_workshop_code_value(assembly_code, line_group, assembly_type)

    @staticmethod
    def _assembly_type_to_str(assembly_type) -> str:
        if assembly_type is None:
            return ''
        if hasattr(assembly_type, 'value'):
            return str(assembly_type.value)
        return str(assembly_type)

    @staticmethod
    def _initialize_dynamic_state_tracking() -> Dict:
        """동적 상태 추적 초기화"""
        return {
            'bay_consecutive_history': [],
            'main_plate_consecutive_history': [],
            'assembly_type_history': [],
            'cross_seam_consecutive_history': [],
            'fab_interval_history': []
        }


################################################################################################################################################################################################
# fix: DataConverter staticmethod 외부 노출 (별판 CSV 복제용 헬퍼)
################################################################################################################################################################################################
expand_rows_with_subassembly = DataConverter.expand_rows_with_subassembly
resolve_line_group_for_block = DataConverter.resolve_line_group_for_block
resolve_workshop_code_for_block = DataConverter.resolve_workshop_code_for_block
get_line_group_and_workshop_code = DataConverter.get_line_group_and_workshop_code
