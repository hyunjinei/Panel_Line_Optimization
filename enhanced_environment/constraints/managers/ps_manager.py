# [AGENT-ADD] Split from constraint_managers.py to improve readability.

"""P/S pairing manager."""

from datetime import datetime
from typing import Dict, List, Optional, Set, Tuple

# [AGENT-EDIT] 타입 힌트 참조 해소를 위해 데이터 구조 타입을 명시적으로 임포트
from enhanced_environment.models import (
    PSBlockPair,
    BayType,
    EnhancedBlock,
    PortStarboard,
    ConstraintViolation,
)

class PSBlockManager:
    """
    P5#3,4,P7#3,4: P/S 블록 쌍 관리 매니저 (PFSP 방식)
    
    구현된 제약조건:
    - P5#3: 라인 조립 물량은 Port, Starboard 블록을 연속으로 태운다 (Port 우선)
    - P5#4: 고정 조립 물량은 조립 착수일 차이가 1일 이내일 경우 연속 배정
    - P7#3,4: P/S Block 중 각 블록의 론지 수가 7개 미만일 경우, 동일 베이 배정 & Port 우선
    
    PFSP에서는 블록 순서가 미리 정해지므로 P/S 제약이 대폭 단순화됨:
    - 순서에서 P 다음에 S가 오면 자동으로 연속 처리
    - 분기점에서도 같은 순서 유지로 역전 불가능
    """
    
    def __init__(self, ps_complete_info=None, subassembly_info=None):  # ✅ 메타데이터 매개변수 추가
        # P/S 쌍 정보 저장
        self.ps_pairs: Dict[int, PSBlockPair] = {}  # {port_block_id: PSBlockPair}
        
        # 블록 순서 추적 (PFSP용)
        self.block_sequence: List[int] = []
        self.sequence_positions: Dict[int, int] = {}  # {block_id: position}
        
        # 완료된 블록들
        self.completed_blocks: Set[int] = set()
        
        # 베이 할당 히스토리 (P7#3,4용)
        self.bay_assignments: Dict[int, BayType] = {}  # {block_id: bay}
        
        # ✅ 메타데이터 저장 및 처리
        self.ps_complete_info = ps_complete_info or {}
        self.subassembly_info = subassembly_info or {}
        
        # ✅ P/S 쌍 고급 매핑
        self.ps_pair_requirements = {}  # {pair_id: requirements}
        self._process_ps_complete_info()
        
        # ✅ 별판 P/S 쌍 추적
        self.subassembly_ps_pairs = {}  # {unified_block_id: ps_info}
        self._process_subassembly_ps_info()
    
    def _process_ps_complete_info(self):
        """완전한 P/S 정보를 내부 구조로 변환"""
        processed_pairs = set()
        
        for block_id, ps_info in self.ps_complete_info.items():
            port_id = ps_info['port_block_id']
            starboard_id = ps_info['starboard_block_id']
            
            # 이미 처리된 쌍이면 건너뛰기
            pair_key = f"{port_id}_{starboard_id}"
            if pair_key in processed_pairs:
                continue
            
            # P/S 쌍 요구사항 저장
            self.ps_pair_requirements[pair_key] = {
                'require_continuous_line': ps_info['require_continuous_line'],
                'require_continuous_fixed': ps_info['require_continuous_fixed'],
                'require_same_bay': ps_info['require_same_bay'],
                'port_priority': ps_info['port_priority'],
                'assembly_type': ps_info['assembly_type'],
                'date_difference_days': ps_info['date_difference_days'],
                'physical_constraints': {
                    'any_width_over_21': ps_info['any_width_over_21'],
                    'port_longi_over_30': ps_info['port_longi_over_30'],
                    'starboard_longi_over_30': ps_info['starboard_longi_over_30']
                }
            }
            
            processed_pairs.add(pair_key)
    
    def _process_subassembly_ps_info(self):
        """별판 중 P/S 쌍 정보 처리"""
        for unified_id, subassembly_data in self.subassembly_info.items():
            if subassembly_data['is_ps_pair']:
                port_id = subassembly_data.get('port_id')
                starboard_id = subassembly_data.get('starboard_id')
                
                if port_id and starboard_id:
                    self.subassembly_ps_pairs[unified_id] = {
                        'port_id': port_id,
                        'starboard_id': starboard_id,
                        'atomic_unit': subassembly_data['atomic_unit'],
                        'count_in_consecutive': subassembly_data['count_in_consecutive']
                    }
    
    def is_ps_pair_from_metadata(self, block_id: int) -> bool:
        """메타데이터 기반 P/S 쌍 확인"""
        return block_id in self.ps_complete_info
    
    def get_ps_pair_requirements(self, port_id: int, starboard_id: int) -> Dict:
        """P/S 쌍 요구사항 반환"""
        pair_key = f"{port_id}_{starboard_id}"
        return self.ps_pair_requirements.get(pair_key, {})
    
    def is_subassembly_ps_pair(self, unified_block_id: int) -> bool:
        """통합 블록이 별판 P/S 쌍인지 확인"""
        return unified_block_id in self.subassembly_ps_pairs
    
    def is_port_block(self, block_id: int) -> bool:
        """블록이 P(Port) 블록인지 확인"""
        if block_id in self.ps_pairs:
            return True  # ps_pairs의 키는 port_block_id
        return False
    
    def get_starboard_for_port(self, port_block_id: int) -> Optional[int]:
        """P 블록에 대응하는 S 블록 ID 반환"""
        if port_block_id in self.ps_pairs:
            ps_pair = self.ps_pairs[port_block_id]
            return ps_pair.starboard_block_id
        return None
    
    def is_ps_pair_continuous_required(self, port_block_id: int) -> bool:
        """P/S 쌍이 연속성을 요구하는지 확인"""
        if port_block_id in self.ps_pairs:
            ps_pair = self.ps_pairs[port_block_id]
            return ps_pair.is_continuous_required()
        return False
    
    def set_block_sequence(self, sequence: List[int]):
        """
        PFSP 블록 순서 설정
        
        Args:
            sequence: 에이전트가 결정한 블록 순서
        """
        self.block_sequence = sequence.copy()
        self.sequence_positions = {block_id: idx for idx, block_id in enumerate(sequence)}
        
    def register_ps_pair(self, port_block: EnhancedBlock, starboard_block: EnhancedBlock):
        """
        P/S 블록 쌍 등록
        
        Args:
            port_block: Port 블록
            starboard_block: Starboard 블록
        """
        if port_block.port_starboard != PortStarboard.PORT:
            raise ValueError(f"첫 번째 블록은 Port여야 함: {port_block.port_starboard}")
        
        if starboard_block.port_starboard != PortStarboard.STARBOARD:
            raise ValueError(f"두 번째 블록은 Starboard여야 함: {starboard_block.port_starboard}")
        
        ps_pair = PSBlockPair(
            port_block_id=port_block.block_id,
            starboard_block_id=starboard_block.block_id,
            assembly_type=port_block.assembly_type,
            assembly_start_date=port_block.assembly_start_date,
            longi_count_port=port_block.longi_count,
            longi_count_starboard=starboard_block.longi_count
        )
        
        self.ps_pairs[port_block.block_id] = ps_pair
        
    def can_process_block_in_sequence(self, block: EnhancedBlock, current_position: int) -> Tuple[bool, str]:
        """
        PFSP 순서에서 블록 처리 가능 여부 확인 (대폭 단순화)
        
        Args:
            block: 처리할 블록
            current_position: 현재 순서 위치
            
        Returns:
            (처리 가능 여부, 불가능한 경우 사유)
        """
        # 이미 완료된 블록 체크
        if block.block_id in self.completed_blocks:
            return False, "이미 완료된 블록"
        
        # P/S 쌍이 아닌 경우 자유롭게 처리 가능
        if not block.is_p_s_pair():
            return True, ""
        
        # ✅ Step-by-Step 방식: 블록 순서가 미리 설정되지 않은 경우 간단한 P/S 제약만 적용
        if not self.block_sequence or not self.sequence_positions:
            # S 블록인 경우, 쌍인 P 블록이 먼저 완료되었는지만 확인
            if block.port_starboard == PortStarboard.STARBOARD:
                pair_p_id = block.pair_block_id
                if pair_p_id and pair_p_id not in self.completed_blocks:
                    return False, f"P5#3 위반: S 블록({block.block_id})보다 P 블록({pair_p_id})이 먼저 선택되어야 함"
            
            # P 블록이나 순서 제약을 통과한 S 블록은 자유롭게 선택 가능
            return True, ""
        
        # ✅ PFSP 방식: 순서가 미리 정해진 경우의 기존 로직
        if block.port_starboard == PortStarboard.STARBOARD:
            pair_p_id = block.pair_block_id
            if not pair_p_id:
                return True, ""  # 쌍 정보가 없으면 단독 블록으로 처리
            
            # 순서에서 P 블록이 먼저 오는지 확인
            if (pair_p_id in self.sequence_positions and 
                block.block_id in self.sequence_positions):
                
                p_position = self.sequence_positions[pair_p_id]
                s_position = self.sequence_positions[block.block_id]
                
                if p_position >= s_position:
                    return False, f"PFSP 순서 위반: P 블록({pair_p_id})이 S 블록({block.block_id})보다 뒤에 배치됨"
                
                # P 블록이 아직 완료되지 않았으면 대기
                if pair_p_id not in self.completed_blocks:
                    return False, f"P5#3,4 위반: 같은 쌍의 P 블록({pair_p_id})이 먼저 처리되어야 함"
            
            return True, ""
        
        # ✅ P 블록(PORT)인 경우: 자유롭게 선택 가능 (P는 항상 먼저 올 수 있음)
        elif block.port_starboard == PortStarboard.PORT:
            return True, ""
        
        # ✅ 기타 경우 (CENTER 등): 자유롭게 선택 가능
            return True, ""
    
    def process_block(self, block: EnhancedBlock, assigned_bay: BayType, current_time: datetime) -> List[ConstraintViolation]:
        """
        블록 처리 및 상태 업데이트 (PFSP 방식) + 실시간 제약조건 검증
        
        Args:
            block: 처리할 블록
            assigned_bay: 할당된 베이
            current_time: 현재 시간
            
        Returns:
            제약조건 위반 리스트
        """
        violations = []
        
        # ✅ P5#11,12: 혼합 배정 제약 실시간 검증
        mixing_violations = self._check_assembly_mixing_constraints(block, current_time)
        violations.extend(mixing_violations)
        
        # 블록 완료 처리
        self.completed_blocks.add(block.block_id)
        self.bay_assignments[block.block_id] = assigned_bay
        
        # P/S 쌍 베이 할당 검증 (P7#3,4)
        if block.is_p_s_pair() and block.longi_count < 7:
            if block.port_starboard == PortStarboard.STARBOARD:
                pair_p_id = block.pair_block_id
                if (pair_p_id and 
                    pair_p_id in self.bay_assignments and 
                    self.bay_assignments[pair_p_id] != assigned_bay):
                    
                    violations.append(ConstraintViolation(
                        constraint_id="P7#3,4",
                        message=f"P/S 블록 동일 베이 위반: P({self.bay_assignments[pair_p_id].value}) ≠ S({assigned_bay.value})",
                        block_id=block.block_id
                    ))
        
        # ✅ P5#3,4: P/S 연속성 제약 실시간 검증
        ps_continuity_violations = self._check_ps_continuity_constraints(block)
        violations.extend(ps_continuity_violations)
        
        return violations
    
    def _check_assembly_mixing_constraints(self, block: EnhancedBlock, current_time: datetime) -> List[ConstraintViolation]:
        """
        P5#11,12: 혼합 배정 제약 실시간 검증
        
        Args:
            block: 처리 중인 블록
            current_time: 현재 시간
            
        Returns:
            혼합 배정 제약 위반 리스트
        """
        violations = []
        
        # 완료된 블록들의 조립 타입 히스토리 생성
        completed_sequence = []
        for block_id in self.block_sequence:
            if block_id in self.completed_blocks:
                completed_sequence.append(block_id)
        
        # 현재 블록 추가
        completed_sequence.append(block.block_id)
        
        # 혼합 배정 제약 체크
        last_assembly_type = None
        consecutive_same_type = 0
        max_consecutive_limit = 1  # 3 → 1으로 변경 (강제 교체)
        
        # 메타데이터에서 설정 확인
        assembly_mixing_control = self.ps_complete_info.get('assembly_mixing_control', {})
        if assembly_mixing_control:
            max_consecutive_limit = assembly_mixing_control.get('max_consecutive_limit', 1)  # 기본값도 1로 변경
        
        for block_id in completed_sequence:
            # 블록 정보 가져오기 (실제 환경에서는 blocks_dict 접근 필요)
            current_assembly_type = block.assembly_type if block_id == block.block_id else None
            
            if current_assembly_type is None:
                continue
            
            if last_assembly_type is None:
                last_assembly_type = current_assembly_type
                consecutive_same_type = 1
                continue
            
            if current_assembly_type == last_assembly_type:
                consecutive_same_type += 1
                
                if consecutive_same_type > max_consecutive_limit:
                    violations.append(ConstraintViolation(
                        constraint_id="P5#11,12",
                        message=f"혼합 배정 위반: {current_assembly_type.value} 타입 {consecutive_same_type}개 연속 (최대 {max_consecutive_limit}개)",
                        severity="WARNING",
                        block_id=block.block_id
                    ))
                    break
            else:
                consecutive_same_type = 1
                last_assembly_type = current_assembly_type
        
        return violations
    
    def _check_ps_continuity_constraints(self, block: EnhancedBlock) -> List[ConstraintViolation]:
        """
        P5#3,4: P/S 연속성 제약 실시간 검증
        
        Args:
            block: 처리 중인 블록
            
        Returns:
            P/S 연속성 제약 위반 리스트
        """
        violations = []
        
        # P/S 쌍이 아니면 검증하지 않음
        if not block.is_p_s_pair():
            return violations
        
        # S 블록인 경우, P 블록이 먼저 완료되었는지 확인
        if block.port_starboard == PortStarboard.STARBOARD:
            pair_p_id = block.pair_block_id
            if pair_p_id and pair_p_id not in self.completed_blocks:
                violations.append(ConstraintViolation(
                    constraint_id="P5#3,4",
                    message=f"P/S 연속성 위반: S 블록({block.block_id})보다 P 블록({pair_p_id})이 먼저 완료되어야 함",
                    severity="ERROR",
                    block_id=block.block_id
                ))
        
        # P 블록인 경우, 연속성 요구 조건 확인
        elif block.port_starboard == PortStarboard.PORT:
            if block.block_id in self.ps_complete_info:
                ps_info = self.ps_complete_info[block.block_id]
                
                # 라인 조립은 항상 연속성 필요
                if (ps_info.get('require_continuous_line', False) or 
                    ps_info.get('require_continuous_fixed', False)):
                    
                    # 다음 블록이 S 블록인지 확인은 순서 단계에서 이미 보장됨
                    # 실시간에서는 완료 기록만 함
                    pass
        
        return violations
    
    def get_required_bay_for_starboard(self, starboard_block: EnhancedBlock) -> Optional[BayType]:
        """
        Starboard 블록에 필요한 베이 반환 (P7#3,4 강화)
        
        ✅ 강화된 로직:
        1. P/S 쌍 + 론지<7 → 동일 베이 할당 (기본)
        2. 연속송선 필요 시 → B베이 우선 (추가)
        3. P 블록 베이 할당이 이미 있으면 따라감 (기존)
        
        Args:
            starboard_block: Starboard 블록
            
        Returns:
            필요한 베이 (동일 베이 할당이 필요한 경우)
        """
        # 기본 조건 체크
        if (starboard_block.port_starboard != PortStarboard.STARBOARD or 
            starboard_block.longi_count >= 7 or
            not starboard_block.pair_block_id):
            return None
        
        pair_p_id = starboard_block.pair_block_id
        
        # ✅ 강화 1: P 블록 베이 할당이 이미 있으면 동일 베이 반환 (기존 로직)
        if pair_p_id in self.bay_assignments:
            return self.bay_assignments[pair_p_id]
        
        # ✅ 강화 2: 연속송선 필요 시 B베이 우선 (신규 로직)
        if self._is_ps_pair_requires_continuous_sending(starboard_block):
            return BayType.BAY_36B  # 연속송선 → B베이 우선
        
        # ✅ 강화 3: P/S 쌍 특성에 따른 베이 선택 (신규 로직)
        preferred_bay = self._get_preferred_bay_for_ps_pair(starboard_block)
        if preferred_bay:
            return preferred_bay
        
        return None
    
    def _is_ps_pair_requires_continuous_sending(self, starboard_block: EnhancedBlock) -> bool:
        """
        P/S 쌍이 연속송선을 요구하는지 확인
        
        ✅ 연속송선 조건:
        - P/S 쌍이고 론지 < 7개
        - 블록 크기나 특성상 연속 처리가 필요한 경우
        - 폭이 큰 경우 (21m 근처)
        
        Args:
            starboard_block: S 블록
            
        Returns:
            연속송선 필요 여부
        """
        # 기본 P/S 쌍 + 론지<7 조건
        if not (starboard_block.is_p_s_pair() and starboard_block.longi_count < 7):
            return False
        
        # 연속송선 조건들 체크
        continuous_conditions = [
            # 조건 1: 폭이 18m 이상 (큰 블록)
            starboard_block.width >= 18.0,
            
            # 조건 2: 심수가 4개 이상 (복잡한 블록)
            starboard_block.seam_count >= 4,
            
            # 조건 3: 론지가 5개 이상 (복잡한 론지 작업)
            starboard_block.longi_count >= 5,
            
            # 조건 4: 메인플레이트가 8장 이상 (큰 규모)
            starboard_block.main_plate_count >= 8
        ]
        
        # 연속송선 조건 중 하나라도 만족하면 B베이 우선
        if any(continuous_conditions):
            return True
        
        return False
    
    def _get_preferred_bay_for_ps_pair(self, starboard_block: EnhancedBlock) -> Optional[BayType]:
        """
        P/S 쌍에 대한 선호 베이 결정
        
        ✅ 선호 베이 로직:
        - 큰 블록 (폭 > 20m) → B베이
        - 복잡한 블록 (론지 많음) → B베이  
        - 일반 블록 → A베이 (부하균등 고려)
        
        Args:
            starboard_block: S 블록
            
        Returns:
            선호하는 베이 (있는 경우)
        """
        # 큰 블록은 B베이 선호
        if starboard_block.width > 20.0:
            return BayType.BAY_36B
            
        # 론지가 많은 복잡한 블록도 B베이 선호
        if starboard_block.longi_count >= 6:
            return BayType.BAY_36B
            
        # 심수가 많은 블록도 B베이 선호
        if starboard_block.seam_count >= 5:
            return BayType.BAY_36B
        
        # 일반적인 작은 P/S 쌍은 A베이 선호 (부하균등)
        # return BayType.BAY_35A
        return BayType.BAY_36B
    
    def get_next_required_block(self) -> Optional[int]:
        """
        PFSP 순서에서 다음에 처리해야 할 필수 블록 반환
        (P/S 쌍에서 P가 완료되면 S를 바로 처리해야 하는 경우)
        """
        for block_id in self.block_sequence:
            if block_id in self.completed_blocks:
                continue
                
            # P 블록이 완료되었는데 S 블록이 대기 중인 경우
            if block_id in self.ps_pairs:
                ps_pair = self.ps_pairs[block_id]
                s_block_id = ps_pair.starboard_block_id
                
                if (s_block_id in self.sequence_positions and 
                    s_block_id not in self.completed_blocks and
                ps_pair.is_continuous_required()):
                    return s_block_id
        
            # 첫 번째 미완료 블록 반환
            return block_id
        
        return None
    
    def _is_block_completed(self, block_id: int) -> bool:
        """블록 완료 여부 확인"""
        return block_id in self.completed_blocks
    
    def reset(self):
        """상태 리셋"""
        self.block_sequence.clear()
        self.sequence_positions.clear()
        self.completed_blocks.clear()
        self.bay_assignments.clear()
    
    def get_status(self) -> Dict:
        """현재 P/S 관리 상태 반환"""
        return {
            "total_pairs": len(self.ps_pairs),
            "block_sequence": self.block_sequence,
            "completed_blocks": len(self.completed_blocks),
            "sequence_progress": f"{len(self.completed_blocks)}/{len(self.block_sequence)}"
        }
