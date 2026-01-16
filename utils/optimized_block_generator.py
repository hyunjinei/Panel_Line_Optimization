#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
최적화된 블록 생성기
- 실제 데이터 값들을 미리 저장 (길이, 폭, 최소두께)
- 매번 파일 읽기 없이 빠른 샘플링 가능
- 경험적 분포 + 고정확도 관계식 적용
- 상관관계 비교 및 검증 기능 통합
- 조립착수일 생성 (현실적 클러스터링)
- Tact Time 계산 (최적화된 공식 적용)
- P/S 쌍과 별판 생성 (현실적 비율 적용)
- 호선번호, 블록번호, 소조번호 생성
"""

import os
import pandas as pd
import numpy as np
import json
from pathlib import Path
import matplotlib.pyplot as plt
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional, Tuple
import random
import copy
import string
import math
from collections import defaultdict

# 허용된 조립 작업장 코드 (엑셀 구조와 동일)
ALLOWED_WORKSHOP_CODES = ['L_11', 'L_12', 'L_21', 'L_22'] + [f'F_{i}' for i in range(1, 11)]

# 🔧 디버깅 설정
VERBOSE_MODE = False  # 상세 출력 여부 (True/False로 제어)
QUIET_MODE = False    # �� 별판 디버깅을 위해 출력 활성화

# 🔧 생성 데이터 로깅 설정 (환경 변수로도 제어 가능)
_DEFAULT_SAVE_GENERATED_BLOCKS = True
_DEFAULT_USE_WEEKEND_FILTER = False

def _is_truthy(value) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    value_str = str(value).strip().lower()
    return value_str in {"1", "true", "yes", "on"}

SAVE_GENERATED_BLOCKS = _is_truthy(os.environ.get("SAVE_GENERATED_BLOCKS", str(_DEFAULT_SAVE_GENERATED_BLOCKS)))
SAVE_GENERATED_BLOCKS_PATH = os.environ.get("SAVE_GENERATED_BLOCKS_PATH", "generated_blocks_debug.csv")

# 🔧 주말 필터링 사용 여부 (True: weekend_ratio 적용, False: 주말도 평일과 동일)
USE_WEEKEND_FILTER = _is_truthy(os.environ.get("USE_WEEKEND_FILTER", str(_DEFAULT_USE_WEEKEND_FILTER)))

# 🔧 출력 제어 함수
def debug_print(*args, **kwargs):
    """디버깅 출력 제어"""
    if not QUIET_MODE and VERBOSE_MODE:
        print(*args, **kwargs)

def info_print(*args, **kwargs):
    """정보 출력 제어"""
    if not QUIET_MODE:
        print(*args, **kwargs)

# 한글 폰트 설정
plt.rcParams['font.family'] = ['Malgun Gothic', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

class OptimizedBlockGenerator:
    """최적화된 블록 생성기"""
    
    def __init__(self):
        """초기화 - 실제 데이터 값들 저장"""
        
        debug_print("🔧 최적화된 블록 생성기 초기화 중...")
        
        # 고정확도 관계식 계수들 (항상 초기화)
        self.equations = {
            'seam_count': {'slope': 0.2377, 'intercept': -0.0736},
            'seam_weld_length': {'slope': 36.0733, 'intercept': -0.9030},
            'longi_count': {'slope': 0.0455, 'intercept': 1.9696},
            'max_thickness': {'slope': 0.8873, 'intercept': 5.6008}
        }
        
        # C/SEAM 확률 분포 (항상 초기화)
        self.c_seam_dist = {
            'values': [0, 1, 2, 3, 4, 5],
            'probs': [0.6124, 0.2364, 0.1163, 0.0233, 0.0039, 0.0077]
        }
        
        # 조립착수일 생성 설정
        # [AGENT-EDIT] 날짜 범위를 SNU 수준으로 강하게 축소 (기준일 ±3일)
        self.assembly_date_config = {
            'year': 2025,
            'spread_days': 3,    # 기준일 ±3일 → 총 7일 이내
            'weekend_ratio': 0.1,
            'min_cluster_blocks': 6,
            'max_cluster_blocks': 24
        }
        
        # P/S 쌍과 별판 생성 설정 (실제 데이터 분석 결과 기반)
        self.block_type_config = {
            'ps_pair_ratio': 0.297,      # P/S 쌍 비율 (29.7%)
            'orphan_p_ratio': 0.176,     # 고아 P 비율 (17.6%)
            'orphan_s_ratio': 0.198,     # 고아 S 비율 (19.8%)
            'center_ratio': 0.181,       # Center 블록 비율 (18.1%)
            'subassembly_ratio': 0.181   # 별판 비율 (18.1%)
        }

        # ==== [AGENT-ADD BEGIN: SAW calibration based on real dataset (250618)] ====
        # 실적 대비 생성 SAW 시간이 과도하게 길어지는 문제를 완화하기 위한 선형 스케일/클램프 파라미터
        # 실측 샘플(72건)에 기반해 real ≈ slope * pred + offset 형태로 회귀
        self.saw_calibration = {
            'front': {'scale': 0.551, 'offset': 22.8},  # real_front ≈ 0.551*pred + 22.791
            'back': {'scale': 0.675, 'offset': 8.2},    # real_back  ≈ 0.675*pred + 8.181
        }
        # 실측 평균에 맞추기 위한 후속 스케일(포스트 스케일) - 샘플 비교 기반
        #   실데이터 대비 생성: 전면 76→58, 후면 69→44 목표로 0.757/0.635 적용
        self.saw_post_scale = {
            'front': 0.7568,
            'back': 0.6353
        }
        # 실적 분포(최소~최대)에 근거한 합리적 범위
        self.saw_bounds = {
            'front': (17.5, 190.0),  # 실측 min≈17.9, max≈185.8
            'back': (13.0, 165.0)    # 실측 min≈13.5, max≈160.9
        }
        # ==== [AGENT-ADD END] ====

        # 🎲 확률적 수량 결정 범위 (사용자 요청: 0~5)
        self.random_block_type_limits = {
            'ps_pairs': (0, 5),
            'subassembly_groups': (0, 5)
        }
        
        # 식별자 생성 설정 (분석 결과 기반)
        self.identifier_config = {
            'ship_number_range': (1, 15),
            'ship_letters': list(string.ascii_uppercase),
            'sub_numbers': [1, 2],
            'block_prefix': 'BLK_',
            'sub_prefix': 'TP_'
        }
        
        # 직접 real_block_values.json만 사용 (스냅샷/CSV 로딩 제거)
        # [AGENT-EDIT] 경로를 프로젝트 기준으로 탐색 (상대경로 안정화)
        real_values_path = None
        base_dir = Path(__file__).resolve().parents[1]  # repo root
        candidate_paths = [
            Path.cwd() / 'real_block_values.json',
            base_dir / 'PPO' / 'real_block_values.json',
            base_dir / 'real_block_values.json',
        ]
        for candidate in candidate_paths:
            if candidate.exists():
                real_values_path = candidate
                break

        try:
            if real_values_path is None:
                raise FileNotFoundError
            with open(real_values_path, 'r', encoding='utf-8') as f:
                self.real_values = json.load(f)
            debug_print(f"✅ real_block_values.json에서 기본 규격 로드 완료: {real_values_path}")
        except FileNotFoundError:
            raise FileNotFoundError(
                "real_block_values.json 파일이 없습니다. 기본 규격 데이터를 먼저 준비하세요.")

        # 면적별 경험분포 기본값 (CSV 없이 최소 동작)
        self.area_distributions = {
            '판넬 론지 용접장': {'small': [0], 'medium': [0], 'large': [0]},
            '평판 수': {'small': [1], 'medium': [1], 'large': [1]},
            '앵글 수': {'small': [0], 'medium': [0], 'large': [0]},
            'B/UP 수': {'small': [0], 'medium': [0], 'large': [0]}
        }

        # 조립 작업장 분포 기본값 (1/n 균등 분포)
        total_workshops = len(ALLOWED_WORKSHOP_CODES)
        uniform_weight = 1.0 / total_workshops if total_workshops > 0 else 0
        self.workshop_distribution = {
            code: uniform_weight for code in ALLOWED_WORKSHOP_CODES
        }

        # 상관관계 비교용 최소 데이터프레임 (길이/폭/최소두께만)
        self.real_data = pd.DataFrame({
            '길이': self.real_values.get('length', []),
            '폭': self.real_values.get('width', []),
            '최소두께': self.real_values.get('min_thickness', [])
        })
        if '길이' in self.real_data.columns and '폭' in self.real_data.columns:
            self.real_data['면적'] = self.real_data['길이'] * self.real_data['폭']

        debug_print(" \\초기화 완료!")
        if hasattr(self, 'real_values'):
            debug_print(f"   저장된 길이 값: {len(self.real_values.get('length', []))}개")
            debug_print(f"   저장된 폭 값: {len(self.real_values.get('width', []))}개") 
            debug_print(f"   저장된 최소두께 값: {len(self.real_values.get('min_thickness', []))}개")
    
    def _extract_real_values(self):
        """실제 데이터에서 기본 규격 값들 추출"""
        
        # 데이터 로드 (초기화 시 한 번만)
        df = pd.read_csv('enhanced_analysis/integrated_block_dataset.csv')
        
        real_values = {
            'length': df['길이'].dropna().values.tolist(),
            'width': df['폭'].dropna().values.tolist(),
            'min_thickness': df['최소두께'].dropna().values.tolist()
        }
        
        # JSON으로 저장 (선택사항)
        with open('real_block_values.json', 'w', encoding='utf-8') as f:
            json.dump(real_values, f, ensure_ascii=False, indent=2)
        
        print(" 실제 데이터 값들 저장 완료: real_block_values.json")
        
        return real_values
    
    def _extract_area_distributions(self):
        """면적별 경험적 분포 파라미터 추출"""
        
        df = pd.read_csv('enhanced_analysis/integrated_block_dataset.csv')
        df['면적'] = df['길이'] * df['폭']
        
        distributions = {}
        
        features = ['판넬 론지 용접장', '평판 수', '앵글 수', 'B/UP 수']
        
        for feature in features:
            if feature in df.columns:
                df_temp = df[[feature, '면적']].dropna()
                
                # 면적 구간별 실제 값들 저장
                small_values = df_temp[df_temp['면적'] < 200][feature].values.tolist()
                medium_values = df_temp[(df_temp['면적'] >= 200) & (df_temp['면적'] < 400)][feature].values.tolist()
                large_values = df_temp[df_temp['면적'] >= 400][feature].values.tolist()
                
                distributions[feature] = {
                    'small': small_values if len(small_values) > 0 else [0],
                    'medium': medium_values if len(medium_values) > 0 else small_values if len(small_values) > 0 else [0],
                    'large': large_values if len(large_values) > 0 else medium_values if len(medium_values) > 0 else [0]
                }
        
        return distributions

    def _sample_from_distribution(self, distribution: dict, default_value):
        if not distribution:
            return default_value
        items = list(distribution.items())
        values = [item[0] for item in items]
        weights = []
        for _, weight in items:
            try:
                weights.append(max(float(weight), 0.0))
            except (TypeError, ValueError):
                weights.append(0.0)
        total = sum(weights)
        if total <= 0:
            return default_value
        return random.choices(values, weights=weights, k=1)[0]

    def _sample_workshop_code(self) -> str:
        default_code = 'L_11'
        sampled = self._sample_from_distribution(getattr(self, 'workshop_distribution', {}), default_code)
        normalized = self._normalize_allowed_workshop_code(sampled) or default_code
        # [AGENT-EDIT] 고정:라인 ≈ 6:4 비율로 재조정
        if random.random() < 0.6:
            fixed_codes = [code for code in ALLOWED_WORKSHOP_CODES if code.startswith('F_')]
            if fixed_codes:
                normalized = random.choice(fixed_codes)
        else:
            line_codes = [code for code in ALLOWED_WORKSHOP_CODES if code.startswith('L_')]
            if line_codes:
                normalized = random.choice(line_codes)
        return normalized

    def _derive_line_group(self, workshop_code: str) -> str:
        if not workshop_code:
            return ''
        normalized = self._normalize_allowed_workshop_code(workshop_code)
        if normalized:
            return normalized
        return str(workshop_code).strip().upper()

    def _derive_assembly_type(self, workshop_code: str) -> str:
        if not workshop_code:
            return 'LINE'
        code = workshop_code.strip().upper()
        if code.startswith('F_'):
            return 'FIXED'
        return 'LINE'

    def _normalize_allowed_workshop_code(self, code: str) -> str:
        if not code:
            return None
        code = str(code).strip().upper()
        if code.startswith('F_'):
            digits = ''.join(ch for ch in code.split('_', 1)[1] if ch.isdigit()) if '_' in code else ''
            try:
                idx = int(digits) if digits else 1
            except ValueError:
                idx = 1
            idx = max(1, min(idx, 10))
            return f'F_{idx}'
        if code.startswith('L_'):
            digits = ''.join(ch for ch in code.split('_', 1)[1] if ch.isdigit()) if '_' in code else ''
            major = digits[0] if digits and digits[0] in {'1', '2'} else '1'
            minor = digits[1] if digits and len(digits) > 1 and digits[1] in {'1', '2'} else '1'
            normalized = f'L_{major}{minor}'
            return normalized if normalized in ALLOWED_WORKSHOP_CODES else f'L_{major}{minor}'
        return None if code not in ALLOWED_WORKSHOP_CODES else code

    def _get_block_base_and_suffix(self, block_name: str) -> Tuple[str, Optional[str]]:
        """블록명을 기본명과 접미사(P/S 등)로 분리"""
        if not isinstance(block_name, str) or not block_name:
            return "", None
        suffix = block_name[-1].upper()
        if suffix in {'P', 'S'}:
            return block_name[:-1], suffix
        return block_name, None

    def _compute_physical_key(self, block: Dict[str, Any]) -> Tuple:
        """별판/쌍 검증을 위한 물리 특성 키 생성"""
        def _round_or_zero(value):
            if value is None:
                return 0.0
            try:
                return round(float(value), 1)
            except (TypeError, ValueError):
                return 0.0

        seam_count = int(block.get('판넬 SEAM 수', 0) or 0)
        c_seam_count = int(block.get('판넬 C/SEAM 수', 0) or 0)
        longi_count = int(block.get('판넬 론지 수', 0) or 0)

        return (
            _round_or_zero(block.get('폭')),
            seam_count,
            c_seam_count > 0,
            longi_count,
            _round_or_zero(block.get('길이')),
            _round_or_zero(block.get('최소두께')),
            _round_or_zero(block.get('최대두께')),
        )

    def _get_assembly_date_token(self, block: Dict[str, Any]) -> Optional[str]:
        """조립 착수일을 비교 가능한 문자열 토큰으로 변환"""
        assembly_dt = block.get('assembly_start_date')
        if isinstance(assembly_dt, datetime):
            return assembly_dt.strftime('%Y%m%d')

        for key in ('조립착수일', '조립 착수일', '착수일'):
            date_value = block.get(key)
            if date_value:
                return str(date_value)
        return None

    def _load_full_data_json(self, json_path: str) -> bool:
        """Compatibility stub: real_block_data_full.json is no longer used."""
        return False
    
    
    @classmethod
    def load_from_saved_values(cls, json_path='real_block_values.json'):
        """저장된 값들로부터 생성기 로드"""
        
        generator = cls()
        return generator
    
    def generate_single_block(self):
        """단일 블록 생성"""
        
        block = {}
        
        # 1. 기본 규격 (저장된 실제 값들에서 샘플링)
        block['길이'] = float(np.random.choice(self.real_values['length']))
        block['폭'] = float(np.random.choice(self.real_values['width']))
        block['최소두께'] = float(np.random.choice(self.real_values['min_thickness']))
        
        # 2. 최대두께 (관계식 + 길이/폭 영향 추가)
        max_thickness_formula = (self.equations['max_thickness']['slope'] * block['최소두께'] + 
                               self.equations['max_thickness']['intercept'])
        # 길이와 폭의 영향 추가 (큰 블록일수록 두꺼움)
        size_effect = 0.02 * block['길이'] + 0.01 * block['폭'] - 0.5
        # 노이즈 조정
        noise = np.random.normal(0, max_thickness_formula * 0.2)
        # 추가 랜덤 요소
        random_addition = np.random.uniform(-2, 6)  
        block['최대두께'] = max(max_thickness_formula + size_effect + noise + random_addition, block['최소두께'] + 2)
        
        # 3. 면적, 부피
        block['면적'] = block['길이'] * block['폭']
        block['부피'] = block['면적'] * block['최소두께'] / 1000

        # 조립 작업장 및 라인 그룹 메타데이터
        workshop_code = self._sample_workshop_code()
        line_group = self._derive_line_group(workshop_code)
        assembly_type_meta = self._derive_assembly_type(workshop_code)

        # 4. 고정확도 관계식 (길이 영향 추가)
        # SEAM 수 (폭 주도 + 길이 보조)
        seam_formula = (self.equations['seam_count']['slope'] * block['폭'] + 
                       self.equations['seam_count']['intercept'] +
                       0.02 * block['길이'])  # 길이 영향 추가
        seam_noise = np.random.normal(0, 0.8)  
        seam_value = max(1, int(seam_formula + seam_noise))
        block['판넬 SEAM 수'] = min(seam_value, 4)
        
        # 4. SEAM 용접장 (최우선 관계 #2,#3: 면적 ↔ SEAM 용접장 0.881, SEAM 수 ↔ SEAM 용접장 0.853)
        # 면적 + SEAM 수 + 길이 영향 (문제 #1: 길이 영향 강화)
        weld_base = (0.45 * block['면적'] + 
                    30.0 * block['판넬 SEAM 수'] + 
                    4.8 * block['길이'] - 20.0)  # 길이 계수 강화: 2.5 → 4.8
        weld_noise = np.random.normal(0, weld_base * 0.12)  
        # 추가 오프셋 노이즈 감소
        weld_offset = np.random.uniform(-5, 8)  
        block['판넬 SEAM 용접장'] = max(0, weld_base + weld_noise + weld_offset)
        
        # 론지 수 (면적 기반 + 길이/SEAM수 영향)
        longi_formula = (self.equations['longi_count']['slope'] * block['면적'] + 
                        self.equations['longi_count']['intercept'] +
                        0.05 * block['길이'] +  # 길이 영향 추가
                        0.3 * block['판넬 SEAM 수'])  # SEAM수 영향 추가
        longi_noise = np.random.normal(0, 2.2)  # 노이즈 감소
        # 기본 공식에 랜덤 요소 감소
        random_factor = np.random.uniform(0.75, 1.25)  # 범위 감소
        # 면적별 조건부 조정 감소
        if block['면적'] < 150:
            longi_bonus = np.random.randint(0, 1)  # 보너스 감소
        else:
            longi_bonus = np.random.randint(-1, 1)  
        block['판넬 론지 수'] = max(1, int((longi_formula * random_factor) + longi_noise + longi_bonus))
        
        # 5. C/SEAM 수 (확률적)
        block['판넬 C/SEAM 수'] = 0
        
        # 6. 개선된 평판 수 (분석 결과 기반 대폭 수정)
        # 문제: 평균 과다(5.3→8.6), SEAM수 관계 약화, 최대두께 관계 반전, 길이 관계 과도
        plate_base = (0.003 * block['면적'] +        # 면적 계수 축소: 0.009 → 0.003
                     1.0 * block['판넬 SEAM 수'] +   # SEAM수 관계 강화: 0.4 → 1.0 
                     0.03 * block['판넬 론지 수'] +  # 론지 계수 축소: 0.06 → 0.03
                     0.04 * block['길이'] +          # 길이 영향 축소: 0.08 → 0.04
                     -0.03 * block['최소두께'] +     # 최소두께 영향 축소: -0.05 → -0.03
                     2.2)                            # 기본값 대폭 축소: 3.5 → 2.2
        plate_noise = np.random.normal(0, 1.0)       # 노이즈 축소: 1.6 → 1.0
        
        # 조건부 변동 단순화 (복잡한 로직 제거)
        if block['면적'] > 400:
            plate_multiplier = np.random.uniform(1.0, 1.2)  
        elif block['최소두께'] > 25:
            plate_multiplier = np.random.uniform(0.7, 0.9)  
        else:
            plate_multiplier = np.random.uniform(0.9, 1.1)  
            
        block['평판 수'] = max(1, int((plate_base * plate_multiplier) + plate_noise))
        # [AGENT-EDIT] 고주판 비율을 강하게 억제 (최대 10)
        if block['평판 수'] > 10:
            block['평판 수'] = 10
        
        # 7. 앵글 수와 B/UP 수 - 앵글을 거의 독립적으로 생성
        # 문제: 앵글이 다른 특성들과 간접적 연결로 여전히 과도한 관계
        # 실제 데이터에서 앵글은 대부분 약한 관계이므로 거의 독립적으로 생성
        # 면적/SEAM/론지/용접장과의 조건부 분기 제거, 최소두께만 약한 음의 영향 + 큰 노이즈 적용
        angle_mean = max(0.0, 8.0 - 0.12 * block['최소두께'])
        angle_sigma = 4.0
        final_angle = int(max(0, np.round(np.random.normal(angle_mean, angle_sigma))))
        
        # B/UP 수 기본 계산 (면적/SEAM/론지 등은 유지)
        bup_base = (0.025 * block['면적'] +
				   0.18 * block['판넬 SEAM 용접장'] / 10 +
				   0.25 * block['판넬 론지 수'] +
				   0.15 * block['판넬 SEAM 수'] +
				   -0.06 * block['최소두께'] +
				   -0.03 * block['평판 수'] +
                   1.0)
        bup_noise = np.random.normal(0, 2.5)
        initial_bup = max(0.0, bup_base + bup_noise)
        
        # 앵글과의 음의 상관만 유지: B/UP을 앵글 편차에 대해 선형 보정
        # 목표: corr(Angle, BUP) < 0, 다른 특성과 Angle 상관 ≈ 0
        gamma = 0.6  # 음의 상관 강도 (필요시 미세조정)
        bup_adjust_noise = np.random.normal(0, 1.5)
        final_bup = max(0.0, initial_bup - gamma * (final_angle - angle_mean) + bup_adjust_noise)
        
        block['B/UP 수'] = int(np.round(final_bup))
        block['앵글 수'] = int(final_angle)
        
        # 8. 면적별 경험적 분포 (론지 용접장만)
        area = block['면적']
        if area < 200:
            size_category = 'small'
        elif area < 400:
            size_category = 'medium'
        else:
            size_category = 'large'
        
        # 론지 용접장만 경험적 분포 사용
        if '판넬 론지 용접장' in self.area_distributions:
            available_values = self.area_distributions['판넬 론지 용접장'][size_category]
            # 기존 경험적 분포에 약간의 변동 추가
            base_value = float(np.random.choice(available_values))
            variation = np.random.normal(0, base_value * 0.2)  # ±20% 변동
            block['판넬 론지 용접장'] = max(0, base_value + variation)
        
        # 9. 기타 특성들
        block['곡판 수'] = int(np.random.choice([0, 1], p=[0.95, 0.05]))
        block['플랫드바 수'] = 0
        
        # 10. FAB 관련 (0으로 고정)
        block['FAB SEAM 수'] = 0
        block['FAB 론지 수'] = 0
        block['FAB SEAM 용접장'] = 0.0
        block['FAB 론지 용접장'] = 0.0
        
        # 11. 조립착수일 생성
        block['조립착수일'] = self.generate_assembly_start_date()
        
        # 12. Tact Time 계산
        tact_times = self.calculate_tact_times(block)
        for process, time_val in tact_times.items():
            if process != '총합':
                block[f'{process} Tact Time'] = round(time_val, 2)
        block['총 Tact Time'] = round(tact_times['총합'], 2)
        
        return block
    
    def generate_complete_learning_data(self, n_blocks=10000, save_filename='complete_learning_data.csv'):
        """완전한 학습데이터 생성 (블록 특성 + 조립착수일 + Tact Time)"""
        
        print(f"\n 완전한 학습데이터 생성 시작 (총 {n_blocks}개)")
        print("=" * 80)
        print(f" 조립착수일 설정:")
        print(f"   연도: {self.assembly_date_config['year']}")
        print(f"   클러스터 범위: 기준일 + 0~{self.assembly_date_config['spread_days']-1}일")
        print(f"   주말 비율: {self.assembly_date_config['weekend_ratio']*100:.1f}%")
        print(f" Tact Time: 최적화된 공식 적용")
        print("=" * 80)
        
        # 1. 블록 생성
        generated_data = self.generate_blocks(n_blocks)
        
        # 2. 조립착수일 분포 분석
        self._analyze_generated_assembly_dates(generated_data)
        
        # 3. 블록 저장
        self.save_generated_blocks(generated_data, save_filename)
        
        print(f"\n 완전한 학습데이터 생성 완료!")
        print(f"   생성된 블록: {len(generated_data)}개")
        print(f"   포함된 특성: 블록 물리 특성 + 조립착수일 + 8개 Tact Time")
        print(f"   저장 파일: {save_filename}")
        
        return generated_data
    
    def _analyze_generated_assembly_dates(self, df):
        """생성된 조립착수일 분포 분석"""
        
        print(f"\n📊 생성된 조립착수일 분포 분석:")
        print("-" * 50)
        
        # 날짜 변환
        assembly_dates = []
        for date_str in df['조립착수일']:
            try:
                date_obj = datetime.strptime(str(date_str), "%Y%m%d")
                assembly_dates.append(date_obj)
            except:
                continue
        
        if assembly_dates:
            # 기본 통계
            min_date = min(assembly_dates)
            max_date = max(assembly_dates)
            date_range = (max_date - min_date).days
            unique_dates = len(set(date_obj.date() for date_obj in assembly_dates))
            
            print(f"   날짜 범위: {min_date.strftime('%Y-%m-%d')} ~ {max_date.strftime('%Y-%m-%d')}")
            print(f"   총 기간: {date_range}일")
            print(f"   고유 날짜: {unique_dates}개")
            
            # 주말 vs 평일 비율
            weekend_count = sum(1 for date_obj in assembly_dates if date_obj.weekday() >= 5)
            weekday_count = len(assembly_dates) - weekend_count
            
            print(f"   평일: {weekday_count}개 ({weekday_count/len(assembly_dates)*100:.1f}%)")
            print(f"   주말: {weekend_count}개 ({weekend_count/len(assembly_dates)*100:.1f}%)")
            
            # 월별 분포
            from collections import Counter
            month_counts = Counter(date_obj.month for date_obj in assembly_dates)
            print(f"   월별 분포: {dict(sorted(month_counts.items()))}")
        
        # Tact Time 통계
        tact_columns = [col for col in df.columns if 'Tact Time' in col]
        if tact_columns:
            print(f"\n Tact Time 통계:")
            print("-" * 30)
            for col in ['판계 Tact Time', '전면SAW Tact Time', '후면SAW Tact Time', '론지용접 Tact Time', '총 Tact Time']:
                if col in df.columns:
                    mean_val = df[col].mean()
                    min_val = df[col].min()
                    max_val = df[col].max()
                    print(f"   {col:15s}: 평균 {mean_val:6.1f}분 (범위: {min_val:5.1f}~{max_val:6.1f})")
        
        return assembly_dates
    
    def generate_assembly_start_date(self):
        """현실적인 조립착수일 생성 (클러스터링 방식)"""
        
        # 1. 기준일 선택 (2025년 1-12월 중 랜덤)
        year = self.assembly_date_config['year']
        base_date = datetime(year, 1, 1) + timedelta(days=random.randint(0, 364))
        
        # 2. 확산 범위 설정 (기준일 + 0~7일)
        spread_days = self.assembly_date_config['spread_days']
        possible_dates = [base_date + timedelta(days=i) for i in range(spread_days)]
        
        # 3. 주말 필터링 (주말 비율 10-20%)
        weekend_ratio = self.assembly_date_config['weekend_ratio']
        filtered_dates = []
        
        for date in possible_dates:
            is_weekend = date.weekday() >= 5  # 0=월요일, 5=토요일, 6=일요일
            
            if is_weekend:
                # 주말은 낮은 확률로 포함
                if random.random() < weekend_ratio:
                    filtered_dates.append(date)
            else:
                # 평일은 높은 확률로 포함 (주말 제외한 나머지)
                weekday_prob = min(0.9, (1.0 - weekend_ratio * 2/7) * 7/5)  # 주말 비율 보정
                if random.random() < weekday_prob:
                    filtered_dates.append(date)
        
        # 4. 최종 날짜 선택 (필터링된 날짜가 없으면 기준일 사용)
        if filtered_dates:
            selected_date = random.choice(filtered_dates)
        else:
            selected_date = base_date
        
        # 5. 문자열 형태로 반환 (YYYYMMDD)
        return selected_date.strftime("%Y%m%d")
    
    def calculate_tact_times(self, block):
        """Tact Time 계산 (enhanced_environment/utils.py의 최적화된 공식 사용)"""
        
        # utils.py의 _generate_processing_times 함수를 직접 구현
        # (import 문제를 피하기 위해 최적화된 공식을 복사)
        
        # 기본 변수 추출
        seam_count = int(block['판넬 SEAM 수'])
        longi_count = int(block['판넬 론지 수'])
        main_plate_count = int(block['평판 수'])
        angle_count = int(block['앵글 수'])
        buildup_count = int(block['B/UP 수'])
        width = float(block['폭'])
        length = float(block['길이'])
        max_thickness = float(block['최대두께'])
        min_thickness = float(block['최소두께'])
        total_weight = float(block['길이']) * float(block['폭']) * float(block['최소두께']) * 0.00785 / 1000
        total_seam_length = float(block['판넬 SEAM 용접장'])
        longi_length = float(block['판넬 론지 용접장'])
        c_seam_count = int(block['판넬 C/SEAM 수'])
        
        # enhanced_environment/utils.py의 _generate_processing_times 로직 적용
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
                base_time = 45 + (seam_count - 6) * 7
            
            # 2. C/Seam 추가 시간 (최적화됨)
            c_seam_additional = 0
            if 1 <= seam_count <= 4:
                if c_seam_count == 1:
                    c_seam_additional = 8
                elif 2 <= c_seam_count <= 3:
                    c_seam_additional = 15
                elif c_seam_count > 3:
                    c_seam_additional = 15 + (c_seam_count - 3) * 6
            
            return base_time + c_seam_additional
        
        def calculate_front_saw_time():
            preparation_time = 8
            seam_welding_time = 0
            
            if seam_count > 0:
                avg_seam_length = total_seam_length / max(1, seam_count + c_seam_count)
                welding_passes = 1
                if seam_count >= 6:
                    welding_passes = 2
                elif seam_count >= 3:
                    welding_passes = 1
                if max_thickness >= 25:
                    welding_passes = 2
                seam_welding_time = avg_seam_length * welding_passes / 3.2
            
            c_seam_welding_time = 0
            if c_seam_count > 0:
                c_seam_preparation = c_seam_count * 5
                avg_c_seam_length = total_seam_length / max(1, seam_count + c_seam_count)
                c_seam_welding_time = c_seam_preparation + (avg_c_seam_length * c_seam_count / 0.4)
            
            return preparation_time + seam_welding_time + c_seam_welding_time
        
        def calculate_turnover_time():
            return 12
        
        def calculate_back_saw_time():
            preparation_time = 8
            seam_welding_time = 0
            
            if seam_count > 0:
                avg_seam_length = total_seam_length / max(1, seam_count + c_seam_count)
                welding_passes = 1
                if seam_count >= 6:
                    welding_passes = 2
                elif seam_count >= 3:
                    welding_passes = 1
                if max_thickness >= 25:
                    welding_passes = 2
                seam_welding_time = avg_seam_length * welding_passes / 0.55
            
            c_seam_welding_time = 0
            if c_seam_count > 0:
                c_seam_preparation = c_seam_count * 5
                avg_c_seam_length = total_seam_length / max(1, seam_count + c_seam_count)
                c_seam_welding_time = c_seam_preparation + (avg_c_seam_length * c_seam_count / 0.4)
            
            base_time = preparation_time + seam_welding_time + c_seam_welding_time
            base_time *= 0.55  # 전체 기본 시간 축소
            
            if c_seam_count > 0:
                exponential_factor = 1.05 ** c_seam_count
                base_time *= exponential_factor
            else:
                base_time *= 0.4
            
            return base_time
        
        def calculate_nc_marking_time():
            base_time = 13.0
            labeling_passes = 1
            if 1.6806 <= width <= 3.361:
                labeling_passes = 2
            elif width > 3.361:
                labeling_passes = max(2, int(width / 1.6805))
            
            labeling_time = (length * labeling_passes) / 20.0
            tap_piece_time = (seam_count + c_seam_count) * 5
            
            return base_time + labeling_time + tap_piece_time
        
        def calculate_longi_installation_time():
            preparation_time = 10
            installation_time = longi_count * 3.5
            return preparation_time + installation_time
        
        def calculate_longi_welding_time():
            move_time = 5
            
            # 론지 용접장 단위 보정
            if longi_length > 100:
                corrected_longi_length = longi_length / 1000.0
            else:
                corrected_longi_length = longi_length
            
            welding_time = 0
            if longi_count > 0:
                welding_passes = 1
                if width >= 8.25 or longi_count > 10:
                    welding_passes = 2
                
                welding_time = (corrected_longi_length * welding_passes) / 1.1
                cleaning_time = welding_passes * 4
                welding_time += cleaning_time
                
                # 데이터셋별 차별화 보정
                if longi_length > 100:  # mm 단위 (250618)
                    length_factor = min(1.5, 1.0 + (corrected_longi_length / 1000) * 0.2)
                    welding_time *= length_factor
                else:  # m 단위 (2507)
                    longi_correction = longi_count * 0.6
                    welding_time += longi_correction
            
            return move_time + welding_time
        
        def calculate_finishing_time():
            move_time = 5
            finishing_time = longi_count * 2.2
            lug_time = 0  # 간단화 (assembly_type="LINE"으로 가정)
            return move_time + finishing_time + lug_time
        
        # 최종 시간 계산
        processing_times = [
            calculate_panel_time(),
            calculate_front_saw_time(),
            calculate_turnover_time(),
            calculate_back_saw_time(),
            calculate_nc_marking_time(),
            calculate_longi_installation_time(),
            calculate_longi_welding_time(),
            calculate_finishing_time()
        ]
        
        # 결과를 딕셔너리로 변환
        process_names = ['판계', '전면SAW', 'TurnOver', '후면SAW', 'NC', '론지취부', '론지용접', '수정']
        tact_times = {}
        
        for i, process_name in enumerate(process_names):
            value = processing_times[i]
            if process_name == '전면SAW':
                value = self._calibrate_saw_time(value, 'front')
            elif process_name == '후면SAW':
                value = self._calibrate_saw_time(value, 'back')
            tact_times[process_name] = value
            processing_times[i] = value  # 총합 및 이후 사용을 위해 스케일된 값을 덮어씀
        
        # 총합 계산
        tact_times['총합'] = sum(processing_times)
        
        return tact_times

    # ==== [AGENT-ADD BEGIN: SAW calibration helper] ====
    def _calibrate_saw_time(self, raw_time: float, stage: str) -> float:
        """실적 기반 스케일/클램프로 SAW 시간을 조정한다."""
        params = self.saw_calibration.get(stage, {'scale': 1.0, 'offset': 0.0})
        min_bound, max_bound = self.saw_bounds.get(stage, (0.0, None))
        adjusted = raw_time * params.get('scale', 1.0) + params.get('offset', 0.0)
        # 2단계: 실측 평균에 맞추기 위한 후속 스케일
        post_scale = self.saw_post_scale.get(stage, 1.0)
        adjusted *= post_scale
        if max_bound is not None:
            adjusted = min(adjusted, max_bound)
        adjusted = max(min_bound, adjusted)
        return adjusted
    # ==== [AGENT-ADD END] ====
    
    def generate_blocks(self, n_blocks=1000):
        """여러 블록 생성"""
        
        print(f"🚀 {n_blocks}개 블록 생성 중...")
        
        blocks = []
        for i in range(n_blocks):
            block = self.generate_single_block()
            blocks.append(block)
            
            if (i + 1) % 100 == 0:
                print(f"   진행률: {i+1}/{n_blocks} ({(i+1)/n_blocks*100:.1f}%)")
        
        df = pd.DataFrame(blocks)
        
        print(f"✅ {n_blocks}개 블록 생성 완료!")
        print(f"   특성 수: {len(df.columns)}개")
        
        return df
    
    def generate_100_blocks_with_ps_pairs_correct(self):
        """
        기존 호환성을 위한 래퍼 함수 - 기본값으로 100개 블록 생성
        """
        return self.generate_blocks_with_ps_pairs_configurable(
            total_blocks=100,
            ps_pairs_count=14,
            subassembly_groups=9
        )

    def _sample_random_block_type_count(self, key: str) -> int:
        """지정된 키에 대한 랜덤 수량 샘플링"""
        low, high = self.random_block_type_limits.get(key, (0, 5))
        if low > high:
            low, high = high, low
        return random.randint(low, high)

    def _clamp_reserved_block_counts(self, total_blocks: int, ps_pairs: int, sub_groups: int) -> Tuple[int, int]:
        """총 블록 수를 초과하지 않도록 예약 블록 수량 보정"""
        max_reserved = max(0, total_blocks - 1)  # 최소 1개 기본 블록 확보
        reserved = ps_pairs + sub_groups
        if reserved > max_reserved:
            overflow = reserved - max_reserved
            if sub_groups > 0:
                reduce_sub = min(sub_groups, overflow)
                sub_groups -= reduce_sub
                overflow -= reduce_sub
            if overflow > 0 and ps_pairs > 0:
                reduce_ps = min(ps_pairs, overflow)
                ps_pairs -= reduce_ps
                overflow -= reduce_ps
        return max(0, ps_pairs), max(0, sub_groups)
    
    def generate_blocks_with_ps_pairs_configurable(self, 
                                                   total_blocks=100, 
                                                   ps_pairs_count=14, 
                                                   subassembly_groups=9):
        """
        완벽한 Flow: 기본 생성 → P/S 쌍 복사 → 별판 복사 → 초과분 제거
        
        Args:
            total_blocks (int): 총 생성할 블록 수 (기본: 100개)
            ps_pairs_count (int): P/S 쌍 개수 (기본: 14쌍) - None이면 0~5 사이 무작위
            subassembly_groups (int): 별판 그룹 수 (기본: 9그룹) - None이면 0~5 사이 무작위
        
        Returns:
            pandas.DataFrame: 생성된 블록 데이터
        """
        
        # info_print(f"\n🎯 완벽한 Flow로 {total_blocks}개 블록 생성")
        info_print("=" * 80)
        
        # 🎲 확률적 수량 결정
        randomized = False
        if ps_pairs_count is None or subassembly_groups is None:
            info_print(f" 확률적 수량 결정 중...")
        if ps_pairs_count is None:
            ps_pairs_count = self._sample_random_block_type_count('ps_pairs')
            randomized = True
        if subassembly_groups is None:
            subassembly_groups = self._sample_random_block_type_count('subassembly_groups')
            randomized = True

        if randomized:
            ps_pairs_count, subassembly_groups = self._clamp_reserved_block_counts(
                total_blocks,
                ps_pairs_count,
                subassembly_groups
            )
            ps_min, ps_max = self.random_block_type_limits['ps_pairs']
            sub_min, sub_max = self.random_block_type_limits['subassembly_groups']
            info_print(
                f"  확률적 결정 결과: P/S 쌍 {ps_pairs_count}쌍, 별판 {subassembly_groups}그룹 "
                f"(P/S 범위 {ps_min}~{ps_max}, 별판 범위 {sub_min}~{sub_max})"
            )
        
        # info_print(f" 최종 생성 설정:")
        # info_print(f"    총 블록 수: {total_blocks}개")
        # info_print(f"    P/S 쌍: {ps_pairs_count}쌍")
        # info_print(f"    별판: {subassembly_groups}그룹")
        
        # ===== 1단계: 정확한 기본 블록 수 계산하여 생성 =====
        #  애초에 생성할 때부터 정확히 50개가 되도록 계산
        basic_blocks_needed = total_blocks - ps_pairs_count - subassembly_groups
        if basic_blocks_needed < 0:
            basic_blocks_needed = 0
        
        # info_print(f"\n정확한 기본 블록 {basic_blocks_needed}개 생성")
        # info_print(f"    계산: 목표 {total_blocks}개 - P/S쌍 {ps_pairs_count}개 - 별판 {subassembly_groups}개 = {basic_blocks_needed}개")
    
        # 공통 기준일 선택
        base_date = self._generate_single_base_date_for_all()
        # info_print(f"   공통 기준일: {base_date.strftime('%Y-%m-%d')}")
        
        blocks = []
        for i in range(basic_blocks_needed):
            block_num = i + 1
            
            block = self.generate_single_block_without_date()
            block['호선번호'] = self.generate_ship_number()
            block['블록번호'] = f"BLK_{block_num}{random.choice(['P', 'S', 'C'])}"
            block['소조번호'] = self.generate_sub_assembly_number()
            block['block_id'] = block_num
            block['block_type'] = 'BASIC'
            block['pair_block_id'] = None
            block['subassembly_group'] = None
            block['조립착수일'] = self._generate_individual_date_from_base(base_date)
            block['착수일'] = block['조립착수일']
            blocks.append(block)
        
        # info_print(f"    기본 블록 {len(blocks)}개 생성 완료")
        
        # ===== 2단계: 생성된 블록들 중에서 P/S 쌍 생성 =====
        if ps_pairs_count > 0:
            # info_print(f"\n 생성된 블록들 중에서 P/S 쌍 {ps_pairs_count}쌍 생성")
            
            # P 블록들 찾기
            p_blocks = [block for block in blocks if block['블록번호'].endswith('P')]
            available_p_blocks = [block for block in p_blocks]  # 복사본 생성
            
            created_pairs = 0
            selected_p_blocks = []
            
            for _ in range(ps_pairs_count):
                if not available_p_blocks:
                    break
                
                # P 블록 랜덤 선택
                selected_p = random.choice(available_p_blocks)
                available_p_blocks.remove(selected_p)
                selected_p_blocks.append(selected_p)
                
                # S 블록 복사 생성
                s_block = copy.deepcopy(selected_p)
                s_block['block_id'] = max(b['block_id'] for b in blocks) + 1
                s_block['블록번호'] = selected_p['블록번호'].replace('P', 'S')  # BLK_5P → BLK_5S
                # 호선번호, 소조번호는 동일하게 유지
                
                # P/S 쌍 연결
                selected_p['block_type'] = 'P'
                selected_p['pair_block_id'] = s_block['block_id']
                s_block['block_type'] = 'S'
                s_block['pair_block_id'] = selected_p['block_id']
                
                blocks.append(s_block)
                created_pairs += 1
            
            # info_print(f"    P/S 쌍 {created_pairs}쌍 생성 완료 (+{created_pairs}개 블록)")
        else:
            # info_print(f"\n P/S 쌍: 0쌍 (건너뛰기)")
            selected_p_blocks = []
        
        # ===== 3단계: 나머지 블록들 중에서 별판 생성 =====
        if subassembly_groups > 0:
            # info_print(f"\n나머지 블록들 중에서 별판 {subassembly_groups}그룹 생성")
            
            # 2단계에서 선택되지 않은 블록들
            used_block_ids = {block['block_id'] for block in selected_p_blocks}
            available_blocks = [block for block in blocks if block['block_id'] not in used_block_ids and block['block_type'] == 'BASIC']
            
            created_subassemblies = 0
            selected_sub_blocks = []
            
            for _ in range(subassembly_groups):
                if not available_blocks:
                    break
                
                # 블록 랜덤 선택
                selected_block = random.choice(available_blocks)
                available_blocks.remove(selected_block)
                selected_sub_blocks.append(selected_block)
                
                # 별판 복사 생성
                sub_block = copy.deepcopy(selected_block)
                sub_block['block_id'] = max(b['block_id'] for b in blocks) + 1
                
                # 🔧 DataConverter 통합 조건 완벽 만족:
                # 1. 블록번호: 완전히 동일하게 유지
                # 2. 호선번호: 완전히 동일하게 유지  
                # 3. 소조번호: TP_ 뒤 숫자만 다르게 랜덤 설정
                
                #  물리적 특성 강제 동일화 (DataConverter 통합 보장)
                physical_features = [
                    '길이', '폭', '최소두께', '최대두께', '면적', '부피',
                    '판넬 SEAM 수', '판넬 SEAM 용접장', '판넬 론지 수', '판넬 C/SEAM 수',
                    '평판 수', 'B/UP 수', '앵글 수', '판넬 론지 용접장', '곡판 수', '플랫드바 수'
                ]
                for feature in physical_features:
                    if feature in selected_block:
                        sub_block[feature] = selected_block[feature]  # 강제 동일화
                
                # 조립착수일도 완전히 동일하게
                sub_block['조립착수일'] = selected_block['조립착수일']
                
                #  별판 생성 디버깅 출력
                # info_print(f"    별판 그룹 {created_subassemblies + 1} 생성:")
                # info_print(f"     원본 블록 ID: {selected_block['block_id']}, 블록번호: {selected_block['블록번호']}")
                # info_print(f"     별판 블록 ID: {sub_block['block_id']}, 블록번호: {sub_block['블록번호']}")
                # info_print(f"     원본 소조번호: {selected_block['소조번호']} → 별판 소조번호: {sub_block['소조번호']}")
                # info_print(f"     조립착수일: {selected_block['조립착수일']} (동일)")
                # info_print(f"     물리적 특성: 폭={selected_block['폭']}, SEAM={selected_block['판넬 SEAM 수']}, 론지={selected_block['판넬 론지 수']}")
                
                #  소조번호 변경 (디버깅 출력 전에 실행!)
                original_sub_number = selected_block['소조번호']  # 예: TP_1
                available_sub_numbers = ['TP_1', 'TP_2']
                if original_sub_number in available_sub_numbers:
                    available_sub_numbers.remove(original_sub_number)
                new_sub_number = random.choice(available_sub_numbers) if available_sub_numbers else 'TP_2'
                sub_block['소조번호'] = new_sub_number
                
                #  별판 생성 디버깅 출력 (소조번호 변경 후)
                # info_print(f"    별판 그룹 {created_subassemblies + 1} 생성:")
                # info_print(f"     원본 블록 ID: {selected_block['block_id']}, 블록번호: {selected_block['블록번호']}")
                # info_print(f"     별판 블록 ID: {sub_block['block_id']}, 블록번호: {sub_block['블록번호']}")
                # info_print(f"     원본 소조번호: {selected_block['소조번호']} → 별판 소조번호: {sub_block['소조번호']}")
                # info_print(f"     조립착수일: {selected_block['조립착수일']} (동일)")
                # info_print(f"     물리적 특성: 폭={selected_block['폭']}, SEAM={selected_block['판넬 SEAM 수']}, 론지={selected_block['판넬 론지 수']}")
                
                #  블록번호, 호선번호, 물리적 특성 모두 완전히 동일 (DataConverter 통합 조건 완벽 만족)
                
                # 별판 타입 설정
                selected_block['block_type'] = 'SUB'
                selected_block['subassembly_group'] = created_subassemblies + 1
                sub_block['block_type'] = 'SUB'
                sub_block['subassembly_group'] = created_subassemblies + 1
                
                blocks.append(sub_block)
                created_subassemblies += 1
            
        # info_print(f"    별판 {created_subassemblies}그룹 생성 완료 (+{created_subassemblies}개 블록)")
        else:
            # info_print(f"\n별판: 0그룹 (건너뛰기)")
            selected_sub_blocks = []
        
        # ===== 4단계: 나머지 블록들 타입 설정 (제거 단계 생략) =====
        # info_print(f"\n4️ 나머지 블록들 타입 설정")
        
        for block in blocks:
            if block['block_type'] == 'BASIC':
                if block['블록번호'].endswith('P'):
                    block['block_type'] = 'ORPHAN_P'
                elif block['블록번호'].endswith('S'):
                    block['block_type'] = 'ORPHAN_S'
                elif block['블록번호'].endswith('C'):
                    block['block_type'] = 'CENTER'

        # ===== 4단계: 조립 작업장 및 조립착수일 현실적 할당 =====
        self._assign_workshop_and_dates(blocks)
        self._synchronize_ps_pairs(blocks)
        self._synchronize_subassembly_groups(blocks)
        self._validate_pairing_integrity(blocks)
        self._log_workshop_summary(blocks)

        # ===== 결과 분석 =====
        df = pd.DataFrame(blocks)

        # 조립 작업장 & 조립착수일 기준 정렬 (실제 엑셀 구조와 동일하게)
        if 'assembly_workshop_code' in df.columns and '조립착수일' in df.columns:
            # 정렬을 위해 착수일을 숫자로 변환 시도
            temp_date = pd.to_numeric(df['조립착수일'], errors='coerce')
            df = df.assign(_sort_date=temp_date)
            df.sort_values(by=['assembly_workshop_code', '_sort_date', 'block_id'], inplace=True)
            df.drop(columns=['_sort_date'], inplace=True)
            df.reset_index(drop=True, inplace=True)

        if SAVE_GENERATED_BLOCKS:
            try:
                save_path = SAVE_GENERATED_BLOCKS_PATH
                df.to_csv(save_path, index=False, encoding='utf-8-sig')
                abs_path = os.path.abspath(save_path)
                print(f"[OptimizedBlockGenerator] Generated blocks saved to {abs_path}")
            except Exception as save_error:
                print(f"[OptimizedBlockGenerator] ⚠️ Failed to save generated blocks CSV: {save_error}")

        # info_print(f"\n 완벽한 Flow 블록 생성 완료!")
        # info_print(f"   총 블록수: {len(df)}")
        
        # 타입별 분포
        type_counts = df['block_type'].value_counts()
        # info_print(f"    블록 타입별 분포:")
        # for block_type, count in type_counts.items():
        #     info_print(f"     {block_type}: {count}개")
        
        # P/S 쌍 분석
        ps_pairs_actual = len(df[df['pair_block_id'].notna()]) // 2
        subassembly_count_actual = len(df[df['block_type'] == 'SUB']) // 2
        
        # info_print(f"    실제 P/S 쌍: {ps_pairs_actual}쌍 (목표: {ps_pairs_count}쌍)")
        # info_print(f"    실제 별판: {subassembly_count_actual}그룹 (목표: {subassembly_groups}그룹)")
        
        # 조립착수일 분포 분석
        self._analyze_final_assembly_dates(df, base_date)
        
        return df
    
    def _log_workshop_summary(self, blocks: List[dict]):
        """생성된 데이터의 작업장별 핵심 정보를 로그로 출력"""
        from collections import defaultdict
        workshop_map = defaultdict(list)
        for block in blocks:
            workshop_map[block['assembly_workshop_code']].append(block)

        # print("=== 생성 블록 작업장 요약 ===")
        # for code in sorted(workshop_map.keys()):
        #     items = sorted(workshop_map[code], key=lambda b: (b['조립착수일'], b['block_id']))
        #     head = items[0]
        #     seam = int(head.get('판넬 SEAM 수', 0))
        #     cseam = int(head.get('판넬 C/SEAM 수', 0))
        #     curved = head.get('곡판 수')
        #     block_type = head.get('block_type')
        #     pair_id = head.get('pair_block_id')
        #     print(f" - {code}: 첫 블록 ID {head['block_id']} / 날짜 {head['조립착수일']} / "
        #           f"SEAM {seam} / C-SEAM {cseam} / 곡판 {curved} / 타입 {block_type} / pair {pair_id}")
        # print("============================")
    
    def _generate_single_base_date_for_all(self):
        """기준일을 연중 임의일(365일)로 복원"""
        # [AGENT-EDIT] 연중 랜덤 기준일로 복원 (6월 중심 고정 해제)
        year = self.assembly_date_config['year']
        random_day = random.randint(1, 365)
        base_date = datetime(year, 1, 1) + timedelta(days=random_day - 1)
        return base_date
    
    def _generate_individual_date_from_base(self, base_date):
        """개별 블록용: 기준일에서 +0~7일 범위로 날짜 생성"""
        spread_days = self.assembly_date_config['spread_days']
        weekend_ratio = self.assembly_date_config['weekend_ratio']
        
        # 기준일 + 0~(spread_days-1)일 범위
        possible_dates = [base_date + timedelta(days=i) for i in range(spread_days)]
        
        # 주말 필터링 적용 여부
        if USE_WEEKEND_FILTER:
            filtered_dates = []
            for date in possible_dates:
                is_weekend = date.weekday() >= 5

                if is_weekend:
                    if random.random() < weekend_ratio:  # 15% 확률로 주말 포함
                        filtered_dates.append(date)
                else:
                    weekday_prob = min(0.9, (1.0 - weekend_ratio * 2/7) * 7/5)
                    if random.random() < weekday_prob:
                        filtered_dates.append(date)
        else:
            filtered_dates = possible_dates
        
        # 최종 날짜 선택
        if filtered_dates:
            selected_date = random.choice(filtered_dates)
        else:
            selected_date = base_date
        
        return selected_date.strftime("%Y%m%d")

    def _assign_workshop_and_dates(self, blocks: List[dict]):
        """생성된 블록들에 현실적인 작업장/조립착수일을 부여"""
        if not blocks:
            return

        groups = self._build_block_groups(blocks)
        if not groups:
            return

        # 그룹을 라인/고정 배정 (SUB/P/S 포함 모든 그룹 동일 처리) - 라인 비율을 약 40%로 낮춰 혼합 제약 완화
        ordered_groups = sorted(groups, key=lambda g: min(b['block_id'] for b in g['blocks']))
        total_groups = len(ordered_groups)
        target_line_total = max(1, math.ceil(total_groups * 0.4))

        line_groups = []
        fixed_groups = []
        for idx, group in enumerate(ordered_groups):
            if idx < target_line_total:
                group['assembly_type'] = 'line'
                line_groups.append(group)
            else:
                group['assembly_type'] = 'fixed'
                fixed_groups.append(group)

        line_codes = sorted(code for code in self.workshop_distribution if code.startswith('L_'))
        if not line_codes:
            line_codes = ['L_11', 'L_12', 'L_21', 'L_22']
        fixed_codes = sorted(code for code in self.workshop_distribution if code.startswith('F_'))
        if not fixed_codes:
            fixed_codes = ['F_1']

        def assign_codes_evenly(group_list, codes, is_line=True):
            if not group_list or not codes:
                return
            unique_codes = list(dict.fromkeys(codes))
            sorted_groups = sorted(group_list, key=lambda g: min(b['block_id'] for b in g['blocks']))
            for idx, group in enumerate(sorted_groups):
                code = unique_codes[idx % len(unique_codes)]
                group['workshop_code'] = code
                group['line_group'] = self._derive_line_group(code)

        assign_codes_evenly(line_groups, line_codes, is_line=True)
        assign_codes_evenly(fixed_groups, fixed_codes, is_line=False)

        # 각 작업장별로 날짜 할당
        base_date = self._generate_single_base_date_for_all()
        # [AGENT-EDIT] 모든 조립착수일을 기준일+7일 이내로 클램프 (창 밖 날짜 생성 방지)
        window_end = base_date + timedelta(days=7)

        def _clamp_date(dt: datetime) -> datetime:
            """창 상한(기준일+7일)으로 날짜를 클램프"""
            return dt if dt <= window_end else window_end

        workshop_groups = defaultdict(list)
        for group in groups:
            workshop_groups[group['workshop_code']].append(group)

        raw_workshop_order = sorted(workshop_groups.keys())
        line_queue = [code for code in raw_workshop_order if str(code).upper().startswith('L_')]
        fixed_queue = [code for code in raw_workshop_order if str(code).upper().startswith('F_')]
        other_queue = [code for code in raw_workshop_order if code not in line_queue and code not in fixed_queue]
        workshop_order = []
        while line_queue or fixed_queue:
            if line_queue:
                workshop_order.append(line_queue.pop(0))
            if fixed_queue:
                workshop_order.append(fixed_queue.pop(0))
        workshop_order.extend(other_queue)
        workshop_base_dates = {}
        rolling_offset = 0
        for code in workshop_order:
            jitter = random.randint(-2, 2)
            workshop_base = base_date + timedelta(days=jitter + rolling_offset)
            workshop_base = _clamp_date(workshop_base)
            workshop_base_dates[code] = workshop_base
            rolling_offset += random.randint(0, 2)
            # rolling_offset 누적이 창 상한을 넘지 않도록 제한
            rolling_offset = min(rolling_offset, max(0, (window_end - base_date).days))

        def soften_anchor_group(group):
            """초기 그룹을 제약 완화형 블록으로 조정하고 최소 한 개의 간단한 블록 확보."""
            blocks_in_group = sorted(group.get('blocks', []), key=lambda b: b['block_id'])
            if not blocks_in_group:
                return

            def convert_to_center(block):
                if not block:
                    return
                block_type = block.get('block_type')
                if block_type in {'P', 'S', 'SUB'}:
                    return
                if block.get('pair_block_id'):
                    return
                block['block_type'] = 'CENTER'
                name = block.get('블록번호', '')
                if isinstance(name, str):
                    if name.endswith(('P', 'S')):
                        block['블록번호'] = name[:-1] + 'C'
                    elif not name.endswith('C'):
                        block['블록번호'] = f"{name}C"

            for block in blocks_in_group:
                original_seam = int(block.get('판넬 SEAM 수', 1))
                limited_seam = max(1, min(original_seam, 4))
                block['판넬 SEAM 수'] = limited_seam
                block['판넬 C/SEAM 수'] = 0
                block['곡판 수'] = 0
                block['has_curved_plate'] = False

                original_longi = max(1, int(block.get('판넬 론지 수', 1)))
                target_longi = max(original_longi, 8)
                if target_longi != original_longi:
                    ratio = target_longi / original_longi
                    block['판넬 론지 수'] = target_longi
                    if '판넬 론지 용접장' in block and isinstance(block['판넬 론지 용접장'], (int, float)):
                        block['판넬 론지 용접장'] = round(block['판넬 론지 용접장'] * ratio, 2)
                    for tact_key in ['론지취부 Tact Time', '론지용접 Tact Time']:
                        if tact_key in block and isinstance(block[tact_key], (int, float)):
                            block[tact_key] = round(block[tact_key] * ratio, 2)
                total = 0.0
                tact_keys = [
                    '판계 Tact Time', '전면SAW Tact Time', 'TurnOver Tact Time',
                    '후면SAW Tact Time', 'NC Tact Time', '론지취부 Tact Time',
                    '론지용접 Tact Time', '수정 Tact Time'
                ]
                for key in tact_keys:
                    value = block.get(key)
                    if isinstance(value, (int, float)):
                        total += value
                block['총 Tact Time'] = round(total, 2)

            anchor_block = None
            for block in blocks_in_group:
                if block.get('block_type') not in ('S', 'SUB'):
                    anchor_block = block
                    break

            if anchor_block is None:
                anchor_block = blocks_in_group[0]

            pair_id = anchor_block.get('pair_block_id')
            if pair_id:
                anchor_block['pair_block_id'] = None
                for other in blocks_in_group:
                    if other.get('block_id') == pair_id:
                        other['pair_block_id'] = None
                        if other.get('block_type') == 'S':
                            convert_to_center(other)
                        break

            convert_to_center(anchor_block)

        for code, group_list in workshop_groups.items():
            group_list.sort(key=lambda g: min(b['block_id'] for b in g['blocks']))
            if group_list:
                soften_anchor_group(group_list[0])

        for workshop_code, group_list in workshop_groups.items():
            group_list.sort(key=lambda g: min(b['block_id'] for b in g['blocks']))
            # [AGENT-EDIT] C/SEAM·고주판 그룹을 분산 배치 (heavy-light 인터리브)
            heavy, light = [], []
            for g in group_list:
                has_cseam = any(int(b.get('판넬 C/SEAM 수', 0) or 0) > 0 for b in g['blocks'])
                high_plate = any(int(b.get('평판 수', 0) or 0) > 10 for b in g['blocks'])
                (heavy if (has_cseam or high_plate) else light).append(g)
            interleaved = []
            while heavy or light:
                if light:
                    interleaved.append(light.pop(0))
                if heavy:
                    interleaved.append(heavy.pop(0))
            group_list = interleaved

            current_date = workshop_base_dates.get(workshop_code, base_date)
            used_capacity = 0
            is_line = self._derive_assembly_type(workshop_code) == 'LINE'
            # 하루 처리량: SNU와 유사하게 너무 작지 않게 복원
            min_cap = 4 if is_line else 3
            max_cap = 8 if is_line else 6
            daily_capacity = random.randint(min_cap, max_cap)
            date_stats = defaultdict(lambda: {'cseam': 0, 'high_plate': 0})
            max_cseam_per_day = 2
            max_high_per_day = 1

            for group in group_list:
                group_size = len(group['blocks'])
                if used_capacity + group_size > daily_capacity:
                    advance_days = random.randint(1, 3)
                    current_date = _clamp_date(current_date + timedelta(days=advance_days))
                    used_capacity = 0
                    daily_capacity = random.randint(min_cap, max_cap)

                group_cseam = sum(int(b.get('판넬 C/SEAM 수', 0) or 0) > 0 for b in group['blocks'])
                group_high = sum(int(b.get('평판 수', 0) or 0) > 10 for b in group['blocks'])
                # [AGENT-EDIT] 그룹 자체가 일별 한도를 초과하면 그대로 배치하고 루프 종료(무한 증분 방지)
                if group_cseam > max_cseam_per_day or group_high > max_high_per_day:
                    pass  # 배치 허용, 아래에서 통계만 누적
                else:
                    while True:
                        stats = date_stats[current_date]
                        over_c = stats['cseam'] + group_cseam > max_cseam_per_day
                        over_h = stats['high_plate'] + group_high > max_high_per_day
                        if over_c or over_h:
                            next_date = _clamp_date(current_date + timedelta(days=random.randint(1, 2)))
                            # 창 상한에서 더 이동할 수 없으면 한도로 허용하고 탈출
                            if next_date == current_date:
                                break
                            current_date = next_date
                            used_capacity = 0
                            daily_capacity = random.randint(min_cap, max_cap)
                            continue
                        break

                group['assembly_date'] = _clamp_date(current_date)
                used_capacity += group_size
                date_stats[current_date]['cseam'] += group_cseam
                date_stats[current_date]['high_plate'] += group_high

        # 각 블록에 값 적용
        for group in groups:
            assembly_type = group['assembly_type']
            workshop_code = group['workshop_code']
            normalized_workshop = self._normalize_allowed_workshop_code(workshop_code) or workshop_code
            line_group = group['line_group']
            assembly_date = group.get('assembly_date', self._generate_single_base_date_for_all())
            if isinstance(assembly_date, datetime):
                assembly_date = _clamp_date(assembly_date)
            date_str = assembly_date.strftime('%Y%m%d')
            date_fmt = assembly_date.strftime('%Y-%m-%d')

            for block in group['blocks']:
                block['assembly_type'] = assembly_type
                block['assembly_type_meta'] = assembly_type
                block['line_group'] = line_group
                block['assembly_workshop_code'] = normalized_workshop
                block['assembly_workshop_code_raw'] = normalized_workshop
                block['조립 작업장'] = normalized_workshop
                block['조립착수일'] = date_str
                block['조립 착수일'] = date_str
                block['착수일'] = date_str
                block['block_assembly_date'] = date_fmt
                block['assembly_start_date'] = assembly_date

    def _synchronize_ps_pairs(self, blocks: List[dict]):
        """P/S 쌍의 조립 작업장과 조립착수일을 강제로 맞춘다"""
        block_lookup = {block['block_id']: block for block in blocks}
        processed = set()

        for block in blocks:
            pair_id = block.get('pair_block_id')
            if not pair_id:
                continue

            key = tuple(sorted((block['block_id'], pair_id)))
            if key in processed:
                continue

            partner = block_lookup.get(pair_id)
            if not partner:
                continue

            date_a = block.get('assembly_start_date')
            date_b = partner.get('assembly_start_date')

            if date_a and date_b:
                if date_a <= date_b:
                    anchor_date = date_a
                    anchor_workshop = block.get('assembly_workshop_code') or partner.get('assembly_workshop_code')
                else:
                    anchor_date = date_b
                    anchor_workshop = partner.get('assembly_workshop_code') or block.get('assembly_workshop_code')
            else:
                anchor_date = date_a or date_b
                anchor_workshop = block.get('assembly_workshop_code') or partner.get('assembly_workshop_code')

            if not anchor_workshop:
                anchor_workshop = self._sample_workshop_code()

            line_group = self._derive_line_group(anchor_workshop)

            for entry in (block, partner):
                entry['assembly_workshop_code'] = anchor_workshop
                entry['assembly_workshop_code_raw'] = anchor_workshop
                entry['조립 작업장'] = anchor_workshop
                entry['line_group'] = line_group
                if anchor_date:
                    entry['assembly_start_date'] = anchor_date
                    entry['block_assembly_date'] = anchor_date.strftime('%Y-%m-%d')
                    date_str = anchor_date.strftime('%Y%m%d')
                    entry['조립착수일'] = date_str
                    entry['조립 착수일'] = date_str
                    entry['착수일'] = date_str

            processed.add(key)

    def _synchronize_subassembly_groups(self, blocks: List[dict]):
        """별판 그룹 구성원이 동일한 조립 메타데이터를 갖도록 정렬"""
        sub_groups: Dict[Any, List[dict]] = defaultdict(list)
        for block in blocks:
            group_id = block.get('subassembly_group')
            if group_id:
                sub_groups[group_id].append(block)

        for group_blocks in sub_groups.values():
            if len(group_blocks) < 2:
                continue

            anchor = min(group_blocks, key=lambda b: b['block_id'])
            anchor_date = anchor.get('assembly_start_date')
            if not isinstance(anchor_date, datetime):
                token = self._get_assembly_date_token(anchor)
                if token and len(token) == 8 and token.isdigit():
                    try:
                        anchor_date = datetime.strptime(token, "%Y%m%d")
                    except ValueError:
                        anchor_date = None
            anchor_workshop = anchor.get('assembly_workshop_code') or anchor.get('조립 작업장')
            if not anchor_workshop:
                anchor_workshop = self._sample_workshop_code()

            line_group = self._derive_line_group(anchor_workshop) if anchor_workshop else anchor.get('line_group')

            for block in group_blocks:
                if anchor_workshop:
                    block['assembly_workshop_code'] = anchor_workshop
                    block['assembly_workshop_code_raw'] = anchor_workshop
                    block['조립 작업장'] = anchor_workshop
                    block['line_group'] = line_group
                if anchor_date:
                    block['assembly_start_date'] = anchor_date
                    block['block_assembly_date'] = anchor_date.strftime('%Y-%m-%d')
                    date_str = anchor_date.strftime('%Y%m%d')
                    block['조립착수일'] = date_str
                    block['조립 착수일'] = date_str
                    block['착수일'] = date_str

    def _validate_pairing_integrity(self, blocks: List[dict]):
        """별판 및 P/S 쌍이 정의한 규칙을 만족하는지 검증"""
        errors: List[str] = []
        block_lookup = {block['block_id']: block for block in blocks}

        processed_pairs = set()
        for block in blocks:
            pair_id = block.get('pair_block_id')
            if not pair_id:
                continue

            key = tuple(sorted((block['block_id'], pair_id)))
            if key in processed_pairs:
                continue
            processed_pairs.add(key)

            partner = block_lookup.get(pair_id)
            if not partner:
                errors.append(f"P/S 쌍 누락: 블록 {block['block_id']} - 상대 {pair_id} 없음")
                continue

            base_a, suffix_a = self._get_block_base_and_suffix(block.get('블록번호', ''))
            base_b, suffix_b = self._get_block_base_and_suffix(partner.get('블록번호', ''))
            if base_a != base_b or {suffix_a, suffix_b} != {'P', 'S'}:
                errors.append(f"P/S 블록명 불일치: {block['블록번호']} ↔ {partner.get('블록번호')}")

            if block.get('소조번호') != partner.get('소조번호'):
                errors.append(f"P/S 소조번호 불일치: {block['block_id']} ↔ {partner['block_id']}")

            if self._compute_physical_key(block) != self._compute_physical_key(partner):
                errors.append(f"P/S 물리 특성 불일치: {block['block_id']} ↔ {partner['block_id']}")

            token_a = self._get_assembly_date_token(block)
            token_b = self._get_assembly_date_token(partner)
            if token_a and token_b and token_a != token_b:
                errors.append(f"P/S 조립착수일 불일치: {block['block_id']}({token_a}) ↔ {partner['block_id']}({token_b})")

        sub_groups: Dict[Any, List[dict]] = defaultdict(list)
        for block in blocks:
            group_id = block.get('subassembly_group')
            if group_id:
                sub_groups[group_id].append(block)

        for group_id, group_blocks in sub_groups.items():
            if len(group_blocks) < 2:
                continue

            block_names = {blk.get('블록번호') for blk in group_blocks}
            if len(block_names) != 1:
                errors.append(f"별판 그룹 {group_id}: 블록번호 불일치 {block_names}")

            physical_keys = {self._compute_physical_key(blk) for blk in group_blocks}
            if len(physical_keys) != 1:
                errors.append(f"별판 그룹 {group_id}: 물리 특성 불일치")

            sub_numbers = [blk.get('소조번호') for blk in group_blocks]
            if len(sub_numbers) != len(set(sub_numbers)):
                errors.append(f"별판 그룹 {group_id}: 소조번호 중복 {sub_numbers}")

            tokens = {self._get_assembly_date_token(blk) for blk in group_blocks if self._get_assembly_date_token(blk)}
            if len(tokens) > 1:
                errors.append(f"별판 그룹 {group_id}: 조립착수일 불일치 {tokens}")

        if errors:
            raise ValueError("P/S 쌍 및 별판 생성 검증 실패:\n" + "\n".join(errors))


    def _build_block_groups(self, blocks: List[dict]) -> List[dict]:
        """P/S 쌍 및 별판 그룹을 묶어 처리"""
        if not blocks:
            return []

        id_map = {block['block_id']: block for block in blocks}
        groups: List[dict] = []
        visited = set()

        sorted_blocks = sorted(blocks, key=lambda b: b['block_id'])

        for block in sorted_blocks:
            bid = block['block_id']
            if bid in visited:
                continue

            group_blocks = []
            stack = [bid]

            while stack:
                current_id = stack.pop()
                if current_id in visited:
                    continue
                visited.add(current_id)
                current_block = id_map.get(current_id)
                if not current_block:
                    continue
                group_blocks.append(current_block)

                pair_id = current_block.get('pair_block_id')
                if pair_id and pair_id not in visited:
                    stack.append(pair_id)

                sub_group = current_block.get('subassembly_group')
                if sub_group:
                    for other in sorted_blocks:
                        if (
                            other.get('subassembly_group') == sub_group
                            and other['block_id'] not in visited
                        ):
                            stack.append(other['block_id'])

            groups.append({'blocks': group_blocks})

        return groups

    def _copy_block_characteristics(self, source_block, target_block):
        """소스 블록의 특성을 타겟 블록에 복사 (블록 특성, 조립착수일, Tact Time 포함)"""
        
        # 물리적 특성 복사
        physical_features = [
            '길이', '폭', '최소두께', '최대두께', '면적', '부피',
            '판넬 SEAM 수', '판넬 SEAM 용접장', '판넬 론지 수', '판넬 C/SEAM 수',
            '평판 수', 'B/UP 수', '앵글 수', '판넬 론지 용접장', '곡판 수', '플랫드바 수',
            'FAB SEAM 수', 'FAB 론지 수', 'FAB SEAM 용접장', 'FAB 론지 용접장'
        ]
        
        for feature in physical_features:
            if feature in source_block:
                target_block[feature] = source_block[feature]
        
        # Tact Time 복사
        tact_time_features = [
            '판계 Tact Time', '전면SAW Tact Time', 'TurnOver Tact Time', '후면SAW Tact Time',
            'NC Tact Time', '론지취부 Tact Time', '론지용접 Tact Time', '수정 Tact Time', '총 Tact Time'
        ]

        for feature in tact_time_features:
            if feature in source_block:
                target_block[feature] = source_block[feature]

        # 조립착수일 복사
        target_block['조립착수일'] = source_block['조립착수일']

        # 호선번호 복사
        target_block['호선번호'] = source_block['호선번호']

        # 조립 작업장 메타데이터 복사
        for meta_feature in ['조립 작업장', 'assembly_workshop_code', 'assembly_workshop_code_raw',
                             'line_group', 'assembly_type_meta', 'assembly_type']:
            if meta_feature in source_block:
                target_block[meta_feature] = source_block[meta_feature]

    def _analyze_final_assembly_dates(self, df, base_date):
        """최종 조립착수일 분석"""
        
        # info_print(f"\n 최종 조립착수일 분석:")
        # info_print("-" * 50)
        # info_print(f"    기준일+: {base_date.strftime('%Y-%m-%d')}")
        
        # 날짜 변환
        assembly_dates = []
        for date_str in df['조립착수일']:
            try:
                date_obj = datetime.strptime(str(date_str), "%Y%m%d")
                assembly_dates.append(date_obj)
            except:
                continue
        
        if assembly_dates:
            # 기본 통계
            min_date = min(assembly_dates)
            max_date = max(assembly_dates)
            date_range = (max_date - min_date).days
            unique_dates = len(set(date_obj.date() for date_obj in assembly_dates))
            
            # info_print(f"   실제 범위: {min_date.strftime('%Y-%m-%d')} ~ {max_date.strftime('%Y-%m-%d')}")
            # info_print(f"   총 기간: {date_range}일")
            # info_print(f"   고유 날짜: {unique_dates}개")
            
            # 기준일 대비 분포
            base_diff_days = [(date_obj - base_date).days for date_obj in assembly_dates]
            # info_print(f"   기준일 대비: {min(base_diff_days):+d}일 ~ {max(base_diff_days):+d}일")
            
            # 주말 vs 평일 비율
            weekend_count = sum(1 for date_obj in assembly_dates if date_obj.weekday() >= 5)
            weekday_count = len(assembly_dates) - weekend_count
            
            # info_print(f"   평일: {weekday_count}개 ({weekday_count/len(assembly_dates)*100:.1f}%)")
            # info_print(f"   주말: {weekend_count}개 ({weekend_count/len(assembly_dates)*100:.1f}%)")
            
            # info_print(f"    성공적인 개별 블록 날짜 분산: 기준일 +0~7일 내에 개별 분산됨")
    
    def compare_correlation_matrices(self, generated_data):
        """실제 데이터와 생성 데이터의 상관관계 비교 (final_block_generation_method.py에서 가져옴)"""
        
        print(f"\n" + "=" * 80)
        print(" 실제 데이터 vs 생성 데이터 상관관계 비교")
        print("=" * 80)
        
        # 공통 특성 추출
        common_features = ['길이', '폭', '최소두께', '최대두께', '면적', '판넬 SEAM 수', 
                          '판넬 론지 수', '판넬 SEAM 용접장', '평판 수', '앵글 수', 'B/UP 수']
        
        available_features = [f for f in common_features if f in self.real_data.columns and f in generated_data.columns]
        
        # 상관계수 행렬 계산
        real_corr = self.real_data[available_features].corr()
        generated_corr = generated_data[available_features].corr()
        
        print(f"\n 분석 대상 특성: {len(available_features)}개")
        print(f"   {available_features}")
        
        # 상관계수 차이 분석
        print(f"\n 주요 상관관계 비교:")
        
        important_pairs = [
            ('길이', '면적'), ('폭', '면적'), ('폭', '판넬 SEAM 수'),
            ('면적', '판넬 론지 수'), ('판넬 SEAM 수', '판넬 SEAM 용접장'),
            ('최소두께', '최대두께'), ('면적', '평판 수')
        ]
        
        total_diff = 0
        count = 0
        
        for feature1, feature2 in important_pairs:
            if feature1 in available_features and feature2 in available_features:
                real_corr_val = real_corr.loc[feature1, feature2]
                gen_corr_val = generated_corr.loc[feature1, feature2]
                diff = abs(real_corr_val - gen_corr_val)
                
                print(f"   {feature1} ↔ {feature2}:")
                print(f"     실제: {real_corr_val:6.3f}")
                print(f"     생성: {gen_corr_val:6.3f}")
                print(f"     차이: {diff:6.3f}")
                
                if diff < 0.1:
                    print(f"      매우 유사")
                elif diff < 0.2:
                    print(f"      약간 차이")
                else:
                    print(f"      큰 차이")
                
                total_diff += diff
                count += 1
                print()
        
        # 전체 평가
        avg_diff = total_diff / count if count > 0 else 0
        print(f" 전체 평가:")
        print(f"   평균 상관계수 차이: {avg_diff:.3f}")
        
        if avg_diff < 0.1:
            print(f"    결과: 매우 우수한 재현성!")
        elif avg_diff < 0.2:
            print(f"    결과: 양호한 재현성")
        else:
            print(f"   ⚠️ 결과: 개선 필요")
        
        return real_corr, generated_corr, avg_diff
    
    def create_correlation_heatmap(self, real_corr, generated_corr, filename='optimized_correlation_heatmap.png'):
        """상관관계 히트맵 생성 (final_block_generation_method.py에서 가져옴)"""
        
        print(f"\n📊 상관관계 히트맵 생성 중...")
        
        fig, axes = plt.subplots(1, 3, figsize=(24, 8))
        
        # 실제 데이터 상관관계
        im1 = axes[0].imshow(real_corr.values, cmap='RdBu_r', aspect='auto', vmin=-1, vmax=1)
        axes[0].set_title('실제 데이터 상관관계', fontsize=16, fontweight='bold')
        axes[0].set_xticks(range(len(real_corr.columns)))
        axes[0].set_yticks(range(len(real_corr.columns)))
        axes[0].set_xticklabels(real_corr.columns, rotation=45, ha='right')
        axes[0].set_yticklabels(real_corr.columns)
        
        # 생성 데이터 상관관계
        im2 = axes[1].imshow(generated_corr.values, cmap='RdBu_r', aspect='auto', vmin=-1, vmax=1)
        axes[1].set_title('생성 데이터 상관관계 (최적화됨)', fontsize=16, fontweight='bold')
        axes[1].set_xticks(range(len(generated_corr.columns)))
        axes[1].set_yticks(range(len(generated_corr.columns)))
        axes[1].set_xticklabels(generated_corr.columns, rotation=45, ha='right')
        axes[1].set_yticklabels(generated_corr.columns)
        
        # 차이
        diff_corr = abs(real_corr - generated_corr)
        im3 = axes[2].imshow(diff_corr.values, cmap='Reds', aspect='auto', vmin=0, vmax=0.5)
        axes[2].set_title('상관관계 차이 (절댓값)', fontsize=16, fontweight='bold')
        axes[2].set_xticks(range(len(diff_corr.columns)))
        axes[2].set_yticks(range(len(diff_corr.columns)))
        axes[2].set_xticklabels(diff_corr.columns, rotation=45, ha='right')
        axes[2].set_yticklabels(diff_corr.columns)
        
        # 컬러바 추가
        fig.colorbar(im1, ax=axes[0], fraction=0.046, pad=0.04)
        fig.colorbar(im2, ax=axes[1], fraction=0.046, pad=0.04)
        fig.colorbar(im3, ax=axes[2], fraction=0.046, pad=0.04)
        
        plt.tight_layout()
        plt.savefig(filename, dpi=300, bbox_inches='tight')
        print(f"   저장: {filename}")
        plt.close()
    
    def save_generated_blocks(self, df, filename='generated_blocks.csv'):
        """생성된 블록 저장"""
        
        df.to_csv(filename, index=False, encoding='utf-8-sig')
        print(f"생성된 블록 저장: {filename}")
        
        # 기본 통계 출력
        print(f"\n 생성된 블록 기본 통계:")
        key_features = ['길이', '폭', '최소두께', '면적', '판넬 SEAM 수']
        for feature in key_features:
            if feature in df.columns:
                print(f"   {feature}: 평균 {df[feature].mean():.2f}, 범위 {df[feature].min():.2f}~{df[feature].max():.2f}")
    
    def generate_and_validate(self, n_blocks=1000, save_filename='optimized_generated_blocks.csv'):
        """블록 생성 + 상관관계 검증 + 저장 (통합 메서드)"""
        
        print(f"\n 블록 생성 및 검증 시작 (총 {n_blocks}개)")
        print("=" * 80)
        
        # 1. 블록 생성
        generated_data = self.generate_blocks(n_blocks)
        
        # 2. 상관관계 비교
        real_corr, generated_corr, avg_diff = self.compare_correlation_matrices(generated_data)
        
        # 3. 히트맵 생성
        heatmap_filename = save_filename.replace('.csv', '_correlation_heatmap.png')
        self.create_correlation_heatmap(real_corr, generated_corr, heatmap_filename)
        
        # 4. 블록 저장
        self.save_generated_blocks(generated_data, save_filename)
        
        print(f"\n 완료 요약:")
        print(f"   생성된 블록: {len(generated_data)}개")
        print(f"   평균 상관계수 차이: {avg_diff:.3f}")
        print(f"   블록 파일: {save_filename}")
        print(f"   히트맵 파일: {heatmap_filename}")
        
        return generated_data, avg_diff

    def generate_ship_number(self):
        """호선번호 생성 (예: PROJ_1, PROJ_2, PROJ_15)"""
        config = self.identifier_config
        ship_num = random.randint(*config['ship_number_range'])
        return f"PROJ_{ship_num}"
    
    def generate_sub_assembly_number(self):
        """소조번호 생성 (예: TP_1, TP_2)"""
        config = self.identifier_config
        sub_num = random.choice(config['sub_numbers'])
        return f"{config['sub_prefix']}{sub_num}"
    
    def generate_block_name(self, base_number, block_type):
        """블록번호 생성 (예: BLK_1P, BLK_2S, BLK_3C)"""
        config = self.identifier_config
        return f"{config['block_prefix']}{base_number}{block_type}"
    
    def _generate_common_base_date(self):
        """전체 100개 블록을 위한 공통 기준일 생성"""
        year = self.assembly_date_config['year']
        base_date = datetime(year, 1, 1) + timedelta(days=random.randint(0, 364))
        return base_date
    
    def _generate_clustered_assembly_date(self, base_date):
        """공통 기준일 기반 클러스터링된 조립착수일 생성 (±7일 내)"""
        spread_days = self.assembly_date_config['spread_days']
        weekend_ratio = self.assembly_date_config['weekend_ratio']
        
        # 기준일 ± 7일 범위에서 날짜 선택
        possible_dates = [base_date + timedelta(days=i) for i in range(-7, spread_days)]
        
        # 주말 필터링 적용
        filtered_dates = []
        for date in possible_dates:
            is_weekend = date.weekday() >= 5
            
            if is_weekend:
                if random.random() < weekend_ratio:
                    filtered_dates.append(date)
            else:
                weekday_prob = min(0.9, (1.0 - weekend_ratio * 2/7) * 7/5)
                if random.random() < weekday_prob:
                    filtered_dates.append(date)
        
        # 최종 날짜 선택
        if filtered_dates:
            selected_date = random.choice(filtered_dates)
        else:
            selected_date = base_date
        
        return selected_date.strftime("%Y%m%d")
    
    def generate_single_block_without_date(self):
        """조립착수일 제외하고 단일 블록 생성 (기존 generate_single_block에서 날짜 부분만 제거)"""
        
        block = {}
        
        # 1. 기본 규격 (저장된 실제 값들에서 샘플링)
        block['길이'] = float(np.random.choice(self.real_values['length']))
        block['폭'] = float(np.random.choice(self.real_values['width']))
        block['최소두께'] = float(np.random.choice(self.real_values['min_thickness']))
        
        # 2. 최대두께 (관계식 + 길이/폭 영향 추가)
        max_thickness_formula = (self.equations['max_thickness']['slope'] * block['최소두께'] + 
                               self.equations['max_thickness']['intercept'])
        # 길이와 폭의 영향 추가 (큰 블록일수록 두꺼움)
        size_effect = 0.02 * block['길이'] + 0.01 * block['폭'] - 0.5
        # 노이즈 조정
        noise = np.random.normal(0, max_thickness_formula * 0.2)
        # 추가 랜덤 요소
        random_addition = np.random.uniform(-2, 6)  
        block['최대두께'] = max(max_thickness_formula + size_effect + noise + random_addition, block['최소두께'] + 2)
        
        # 3. 면적, 부피
        block['면적'] = block['길이'] * block['폭']
        block['부피'] = block['면적'] * block['최소두께'] / 1000

        # 조립 작업장 및 라인 그룹 메타데이터
        workshop_code = self._sample_workshop_code()
        line_group = self._derive_line_group(workshop_code)
        assembly_type_meta = self._derive_assembly_type(workshop_code)

        # 4. 고정확도 관계식 (길이 영향 추가)
        # SEAM 수 (폭 주도 + 길이 보조)
        seam_formula = (self.equations['seam_count']['slope'] * block['폭'] + 
                       self.equations['seam_count']['intercept'] +
                       0.02 * block['길이'])  # 길이 영향 추가
        seam_noise = np.random.normal(0, 0.8)  
        block['판넬 SEAM 수'] = max(1, int(seam_formula + seam_noise))
        
        # 4. SEAM 용접장 (최우선 관계 #2,#3: 면적 ↔ SEAM 용접장 0.881, SEAM 수 ↔ SEAM 용접장 0.853)
        # 면적 + SEAM 수 + 길이 영향 (문제 #1: 길이 영향 강화)
        weld_base = (0.45 * block['면적'] + 
                    30.0 * block['판넬 SEAM 수'] + 
                    4.8 * block['길이'] - 20.0)  # 길이 계수 강화: 2.5 → 4.8
        weld_noise = np.random.normal(0, weld_base * 0.12)  
        # 추가 오프셋 노이즈 감소
        weld_offset = np.random.uniform(-5, 8)  
        block['판넬 SEAM 용접장'] = max(0, weld_base + weld_noise + weld_offset)
        
        # 론지 수 (면적 기반 + 길이/SEAM수 영향)
        longi_formula = (self.equations['longi_count']['slope'] * block['면적'] + 
                        self.equations['longi_count']['intercept'] +
                        0.05 * block['길이'] +  # 길이 영향 추가
                        0.3 * block['판넬 SEAM 수'])  # SEAM수 영향 추가
        longi_noise = np.random.normal(0, 2.2)  # 노이즈 감소
        # 기본 공식에 랜덤 요소 감소
        random_factor = np.random.uniform(0.75, 1.25)  # 범위 감소
        # 면적별 조건부 조정 감소
        if block['면적'] < 150:
            longi_bonus = np.random.randint(0, 1)  # 보너스 감소
        else:
            longi_bonus = np.random.randint(-1, 1)  
        block['판넬 론지 수'] = max(1, int((longi_formula * random_factor) + longi_noise + longi_bonus))
        
        # 5. C/SEAM 수 (확률적)
        block['판넬 C/SEAM 수'] = int(np.random.choice(
            self.c_seam_dist['values'], 
            p=self.c_seam_dist['probs']
        ))
        
        # 6. 개선된 평판 수 (분석 결과 기반 대폭 수정)
        # 문제: 평균 과다(5.3→8.6), SEAM수 관계 약화, 최대두께 관계 반전, 길이 관계 과도
        plate_base = (0.003 * block['면적'] +        # 면적 계수 축소: 0.009 → 0.003
                     1.0 * block['판넬 SEAM 수'] +   # SEAM수 관계 강화: 0.4 → 1.0 
                     0.03 * block['판넬 론지 수'] +  # 론지 계수 축소: 0.06 → 0.03
                     0.04 * block['길이'] +          # 길이 영향 축소: 0.08 → 0.04
                     -0.03 * block['최소두께'] +     # 최소두께 영향 축소: -0.05 → -0.03
                     2.2)                            # 기본값 대폭 축소: 3.5 → 2.2
        plate_noise = np.random.normal(0, 1.0)       # 노이즈 축소: 1.6 → 1.0
        
        # 조건부 변동 단순화 (복잡한 로직 제거)
        if block['면적'] > 400:
            plate_multiplier = np.random.uniform(1.0, 1.2)  
        elif block['최소두께'] > 25:
            plate_multiplier = np.random.uniform(0.7, 0.9)  
        else:
            plate_multiplier = np.random.uniform(0.9, 1.1)  
            
        block['평판 수'] = max(1, int((plate_base * plate_multiplier) + plate_noise))
        
        # 7. 앵글 수와 B/UP 수 - 앵글을 거의 독립적으로 생성
        # 문제: 앵글이 다른 특성들과 간접적 연결로 여전히 과도한 관계
        # 실제 데이터에서 앵글은 대부분 약한 관계이므로 거의 독립적으로 생성
        # 면적/SEAM/론지/용접장과의 조건부 분기 제거, 최소두께만 약한 음의 영향 + 큰 노이즈 적용
        angle_mean = max(0.0, 8.0 - 0.12 * block['최소두께'])
        angle_sigma = 4.0
        final_angle = int(max(0, np.round(np.random.normal(angle_mean, angle_sigma))))
        
        # B/UP 수 기본 계산 (면적/SEAM/론지 등은 유지)
        bup_base = (0.025 * block['면적'] +
                   0.18 * block['판넬 SEAM 용접장'] / 10 +
                   0.25 * block['판넬 론지 수'] +
                   0.15 * block['판넬 SEAM 수'] +
                   -0.06 * block['최소두께'] +
                   -0.03 * block['평판 수'] +
                   1.0)
        bup_noise = np.random.normal(0, 2.5)
        initial_bup = max(0.0, bup_base + bup_noise)
        
        # 앵글과의 음의 상관만 유지: B/UP을 앵글 편차에 대해 선형 보정
        # 목표: corr(Angle, BUP) < 0, 다른 특성과 Angle 상관 ≈ 0
        gamma = 0.6  # 음의 상관 강도 (필요시 미세조정)
        bup_adjust_noise = np.random.normal(0, 1.5)
        final_bup = max(0.0, initial_bup - gamma * (final_angle - angle_mean) + bup_adjust_noise)
        
        block['B/UP 수'] = int(np.round(final_bup))
        block['앵글 수'] = int(final_angle)
        
        # 8. 면적별 경험적 분포 (론지 용접장만)
        area = block['면적']
        if area < 200:
            size_category = 'small'
        elif area < 400:
            size_category = 'medium'
        else:
            size_category = 'large'
        
        # 론지 용접장만 경험적 분포 사용
        if '판넬 론지 용접장' in self.area_distributions:
            available_values = self.area_distributions['판넬 론지 용접장'][size_category]
            # 기존 경험적 분포에 약간의 변동 추가
            base_value = float(np.random.choice(available_values))
            variation = np.random.normal(0, base_value * 0.2)  # ±20% 변동
            block['판넬 론지 용접장'] = max(0, base_value + variation)
        
        # 9. 기타 특성들
        block['곡판 수'] = 0 if area < 400 else int(np.random.choice([0, 0, 1], p=[0.7, 0.2, 0.1]))
        block['플랫드바 수'] = 0
        
        # 10. FAB 관련 (0으로 고정)
        block['FAB SEAM 수'] = 0
        block['FAB 론지 수'] = 0
        block['FAB SEAM 용접장'] = 0.0
        block['FAB 론지 용접장'] = 0.0

        # 11. Tact Time 계산 (조립착수일은 제외)
        tact_times = self.calculate_tact_times(block)
        for process, time_val in tact_times.items():
            if process != '총합':
                block[f'{process} Tact Time'] = round(time_val, 2)
        block['총 Tact Time'] = round(tact_times['총합'], 2)

        # 조립 메타데이터 기록 (엑셀 구조 호환)
        block['조립 작업장'] = workshop_code
        block['assembly_workshop_code'] = workshop_code
        block['assembly_workshop_code_raw'] = workshop_code
        block['line_group'] = line_group
        block['assembly_type_meta'] = assembly_type_meta
        block['assembly_type'] = assembly_type_meta
        block['착수일'] = None  # 생성 데이터에서는 조립착수일과 동일하게 나중에 설정

        return block

    def _analyze_clustered_assembly_dates(self, df, base_date):
        """클러스터링된 조립착수일 분석"""
        
        print(f"\n 클러스터링된 조립착수일 분석:")
        print("-" * 50)
        print(f"   기준일: {base_date.strftime('%Y-%m-%d')}")
        
        # 날짜 변환
        assembly_dates = []
        for date_str in df['조립착수일']:
            try:
                date_obj = datetime.strptime(str(date_str), "%Y%m%d")
                assembly_dates.append(date_obj)
            except:
                continue
        
        if assembly_dates:
            # 기본 통계
            min_date = min(assembly_dates)
            max_date = max(assembly_dates)
            date_range = (max_date - min_date).days
            unique_dates = len(set(date_obj.date() for date_obj in assembly_dates))
            
            print(f"   실제 범위: {min_date.strftime('%Y-%m-%d')} ~ {max_date.strftime('%Y-%m-%d')}")
            print(f"   총 기간: {date_range}일 (기준일 ± {date_range//2}일)")
            print(f"   고유 날짜: {unique_dates}개")
            
            # 기준일 대비 분포
            base_diff_days = [(date_obj - base_date).days for date_obj in assembly_dates]
            print(f"   기준일 대비: {min(base_diff_days):+d}일 ~ {max(base_diff_days):+d}일")
            
            # 주말 vs 평일 비율
            weekend_count = sum(1 for date_obj in assembly_dates if date_obj.weekday() >= 5)
            weekday_count = len(assembly_dates) - weekend_count
            
            print(f"   평일: {weekday_count}개 ({weekday_count/len(assembly_dates)*100:.1f}%)")
            print(f"   주말: {weekend_count}개 ({weekend_count/len(assembly_dates)*100:.1f}%)")
            
            print(f"   성공적인 클러스터링: 모든 블록이 기준일 주변에 집중됨")
        
        return assembly_dates
    
