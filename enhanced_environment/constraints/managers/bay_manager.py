# [AGENT-ADD] Split from constraint_managers.py to improve readability.

"""Bay state tracking manager."""

from typing import Dict, List, Optional, Tuple

# [AGENT-EDIT] 타입 힌트 참조 해소를 위해 데이터 구조 타입을 명시적으로 임포트
from enhanced_environment.models import EnhancedBlock, BayType, ConstraintViolation
from enhanced_environment.constraints import ConstraintConfig

class BayStateTracker:
    """
    P7#1,7,8,11: 베이 상태 추적 매니저
    
    구현된 제약조건:
    - P7#1: 론지 베이(A/B) 부하 균등 배정
    - P7#7: 연속 송선 시 → B 베이 (A베이 연속 불가)
    - P7#8: 주판 Only 연속 제한 (3판 제외)
    - P7#11: 10번 블록일 경우 B베이로 연속 2판 지정
    
    ⭐ P7#3,4 제약: P/S 쌍(론지<7)을 1개 블록으로 취급
    """
    
    def __init__(self, subassembly_info=None, ps_pair_info=None, bay_strategy=None, 
                 block_10_rule=None, dynamic_tracking=None, constraint_config=None):  # ✅ constraint_config 추가
        # 베이별 누적 작업시간 (P7#1)
        self.bay_35a_worktime = 0.0
        self.bay_36b_worktime = 0.0
        
        # 베이별 마지막 완료 시간 (증분적 스케줄링용)
        self.bay_35a_last_completion = None
        self.bay_36b_last_completion = None
        
        # 베이별 블록 수
        self.bay_35a_block_count = 0
        self.bay_36b_block_count = 0
        
        # ⭐ P/S 쌍 1개 블록 취급을 위한 연속성 추적 (P7#7)
        self.last_bay_assignment = None
        self.bay_35a_consecutive_count = 0
        
        # ✅ 36B 베이 연속 제약 추가 (최대 2개까지)
        self.bay_36b_consecutive_count = 0
        
        # ⭐ P/S 쌍 추적용 - 연속성 계산에서 P/S 쌍을 1개로 카운트
        self.ps_pair_atomic_assignments = []  # [(block_id, bay, is_ps_atomic_unit)]
        self.current_ps_atomic_unit = None    # 현재 처리 중인 P/S 원자 단위
        
        # 주판 Only 연속 추적 (P7#8)
        self.bay_35a_main_plate_consecutive = 0
        self.bay_36b_main_plate_consecutive = 0
        
        # 10번 블록 B베이 연속 추적 (P7#11)
        self.block_10_b_bay_consecutive = 0
        self.last_block_10_processed = False
        
        # 베이 할당 히스토리
        self.assignment_history: List[Tuple[int, BayType, float]] = []  # (block_id, bay, worktime)
        
        # ⭐ P/S 쌍 베이 할당 추적 (P7#3 + P7#7 제약용)
        self.ps_pair_bay_assignments: Dict[int, BayType] = {}  # {block_id: assigned_bay}
        self.ps_pair_order_tracking: Dict[int, int] = {}  # {pair_id: order} (P 먼저, S 나중 순서 체크)
        
        # ✅ 메타데이터 저장 및 처리
        self.subassembly_info = subassembly_info or {}
        self.ps_pair_info = ps_pair_info or {}
        self.bay_strategy = bay_strategy or {}
        self.block_10_rule = block_10_rule or {}
        self.dynamic_tracking = dynamic_tracking or {}
        
        # ✅ 제약조건 설정 저장
        self.constraint_config = constraint_config

        # ✅ 메타데이터 기반 고급 추적
        self._initialize_metadata_tracking()

################################################################################################################################################################################################
# fix: 라인 그룹/고심수 론지 연속 추적 상태
################################################################################################################################################################################################
        self.last_line_group: Optional[str] = None  # 마지막 처리된 라인 그룹 (L1/L2 등)
        self.line_group_streak: int = 0             # 동일 라인 그룹 연속 배정 카운터
        self.line_assignment_history: List[str] = []  # 라인 작업장 배정 이력 (Fixed는 기록하지 않음)
        self.last_high_longi_assignment = {
            BayType.BAY_35A: False,  # 직전에 고론지 블록을 처리했는지 여부 (A 베이)
            BayType.BAY_36B: False   # 직전에 고론지 블록을 처리했는지 여부 (B 베이)
        }
    
    def _initialize_metadata_tracking(self):
        """메타데이터 기반 추적 시스템 초기화"""
        # ✅ 별판 원자 단위 매핑
        self.subassembly_atomic_units = {}  # {unified_block_id: atomic_info}
        for unified_id, subassembly_data in self.subassembly_info.items():
            self.subassembly_atomic_units[unified_id] = {
                'original_block_ids': subassembly_data['original_block_ids'],
                'is_ps_pair': subassembly_data['is_ps_pair'],
                'count_in_consecutive': subassembly_data['count_in_consecutive'],
                'atomic_unit': subassembly_data['atomic_unit']
            }
        
        # ✅ P/S 쌍 원자 단위 매핑
        self.ps_atomic_units = {}  # {block_id: atomic_info}
        for block_id, ps_info in self.ps_pair_info.items():
            if ps_info['require_same_bay']:  # 론지 < 7개인 경우만
                self.ps_atomic_units[block_id] = {
                    'pair_id': ps_info['port_block_id'] if block_id == ps_info['starboard_block_id'] else ps_info['starboard_block_id'],
                    'is_port': block_id == ps_info['port_block_id'],
                    'require_same_bay': True,
                    'atomic_unit': True  # P/S 쌍도 1개 원자 단위로 취급
                }
        
        # ✅ 10번 블록 특별 규칙 (config 기반 확장)
        base_block10_ids = set(self.block_10_rule.get('block_10_ids', []))
        self.block_10_longi_ids = set(self.block_10_rule.get('longi30_block_ids', []))
        if self.constraint_config and getattr(self.constraint_config, 'treat_longi30_as_block10', False):
            base_block10_ids.update(self.block_10_longi_ids)
        self.block_10_ids = base_block10_ids
        self.block_10_required_b_count = self.block_10_rule.get('required_b_bay_count', 2)

        # ✅ 연속성 계산용 고급 추적
        self.atomic_assignment_history = []  # 원자 단위 기반 할당 히스토리
        self.bay_35a_longi_total = 0
        self.bay_36b_longi_total = 0

    #################################################################
    # [AGENT-ADD] 연속성 관련 상태만 리셋 (일자 경계용)
    #################################################################
    def reset_consecutive_counters(self):
        """연속 배치·원자 히스토리만 초기화 (작업시간/누적부하는 유지)."""
        self.last_bay_assignment = None
        self.bay_35a_consecutive_count = 0
        self.bay_36b_consecutive_count = 0
        self.bay_35a_main_plate_consecutive = 0
        self.bay_36b_main_plate_consecutive = 0
        self.block_10_b_bay_consecutive = 0
        self.last_block_10_processed = False
        self.last_line_group = None
        self.line_group_streak = 0
        self.last_high_longi_assignment = {
            BayType.BAY_35A: False,
            BayType.BAY_36B: False,
        }
        self.atomic_assignment_history.clear()
        self.assignment_history.clear()
        self.ps_pair_atomic_assignments.clear()
        self.current_ps_atomic_unit = None
    
    def _is_subassembly_atomic_unit(self, block_id: int) -> bool:
        """블록이 별판 원자 단위인지 확인"""
        return block_id in self.subassembly_atomic_units
    
    def _is_ps_atomic_unit(self, block_id: int) -> bool:
        """블록이 P/S 원자 단위인지 확인 (론지 < 7개)"""
        return block_id in self.ps_atomic_units
    
    def _get_atomic_unit_count(self, block_id: int) -> int:
        """원자 단위의 연속성 카운트 값 반환"""
        # 🔥 수정: 연속성 제약에서는 모든 블록을 1개로 취급
        # 별판이라도 베이 할당 관점에서는 1개 블록으로 처리
        # (부하균형 등 다른 제약에서는 별도 메타데이터 사용)
        return 1
    
    def _get_consecutive_count_without_current_block(self, target_bay: BayType) -> int:
        """
        현재 블록을 제외하고 연속성 계산 (Action Masking P7#7 중복 체크 해결용)

        ✅ Action Masking에서 이미 상태가 업데이트된 후에 호출되므로,
        가장 최근 할당(현재 블록)을 제외하고 그 이전까지의 연속 카운트를 계산
        
        Args:
            target_bay: 체크할 베이 타입
            
        Returns:
            현재 블록 제외한 연속 카운트
        """
        history_len = len(self.atomic_assignment_history)
        if history_len <= 1:
            return 0

        consecutive_count = 0

        # 최신(현재) 할당을 제외하고 역순으로 확인
        for atomic_assignment in reversed(self.atomic_assignment_history[:-1]):
            bay = atomic_assignment['bay']
            atomic_count = atomic_assignment['atomic_count']
            if bay == target_bay:
                consecutive_count += atomic_count
            else:
                break

        return consecutive_count
    
    def _get_consecutive_count_with_metadata(self, target_bay: BayType) -> int:
        """
        메타데이터 기반 정확한 연속성 계산 (P7#7용)
        
        ✅ 수정: 바로 직전 베이부터 연속으로 같은 베이가 나온 개수만 계산
            
        별판과 P/S 쌍을 1개 원자 단위로 올바르게 카운트
        """
        consecutive_count = 0
        
        # 역순으로 확인하여 바로 직전부터 연속으로 같은 베이인 것만 카운트
        for atomic_assignment in reversed(self.atomic_assignment_history):
            bay = atomic_assignment['bay']
            atomic_count = atomic_assignment['atomic_count']
            
            if bay == target_bay:
                consecutive_count += atomic_count
            else:
                # ✅ 다른 베이가 나오면 연속성 중단 → 카운트 중지
                break
        
        return consecutive_count
    
    def _record_atomic_assignment(self, block_id: int, bay: BayType, processing_time: float):
        """원자 단위 기반 할당 기록"""
        atomic_count = self._get_atomic_unit_count(block_id)
        
        atomic_assignment = {
            'block_id': block_id,
            'bay': bay,
            'processing_time': processing_time,
            'atomic_count': atomic_count,
            'is_subassembly': self._is_subassembly_atomic_unit(block_id),
            'is_ps_pair': self._is_ps_atomic_unit(block_id),
            'timestamp': len(self.atomic_assignment_history)
        }
        
        self.atomic_assignment_history.append(atomic_assignment)
    
    # [AGENT-ADD] Route bay hard-rule checks through config so ablation can toggle them cleanly.
    def _is_enabled(self, constraint_id: str, default: bool = True) -> bool:
        if self.constraint_config is None:
            return default
        try:
            return bool(self.constraint_config.is_constraint_enabled(constraint_id))
        except Exception:
            return default

    def can_assign_bay(self, block: EnhancedBlock, target_bay: BayType) -> Tuple[bool, str]:
        """
        특정 베이 할당 가능 여부 확인 (모든 P7 제약조건 검증)
        
        ⭐ P7#3,4: P/S 쌍(론지<7)을 1개 블록으로 취급하여 P7#7 연속 제약 계산
        
        Args:
            block: 처리할 블록
            target_bay: 목표 베이
            
        Returns:
            (할당 가능 여부, 불가능한 경우 사유)
        """
        # 🔥 P7#3,4 제약은 enhanced_pbs_env.py에서 처리하므로 여기서는 제거
        # enhanced_pbs_env.py의 _auto_assign_bay()에서 정확한 P/S small 쌍 감지 후 처리됨
        
        # ⭐ P7#7: 베이 연속 배치 제한 (수정된 로직)
        # ✅ 수정: 히스토리가 비어있으면 연속성 체크 스킵 (첫 번째 블록)
        if not self.atomic_assignment_history:
            # 첫 번째 블록은 연속성 제약 없음
            pass
        else:
            # 연속성 체크 수행
            consecutive_count = self._get_consecutive_count_with_metadata(target_bay)
            
            # A베이: 이미 1개 있으면 다음 A는 위반 (1개까지만 연속 가능)
            if self._is_enabled("P7#7") and target_bay == BayType.BAY_35A and consecutive_count >= 1:
                return False, "P7#7 위반: A베이 2개 연속 배치 불가 (개별 블록 카운트)"
            
            # B베이: 이미 1개 있으면 다음 B는 위반 (1개까지 연속 가능) - config 제어
            if (self._is_enabled("P7#7") and target_bay == BayType.BAY_36B and consecutive_count >= 1 and
                self._is_enabled("CONSECUTIVE_B_BAY")):
                return False, "P7#7 위반: B베이 2개 연속 배치 불가 (최대 1개까지) - 연속 3베이 방지 활성화"
            
            # B베이: 기본 제한 (2개까지 연속 가능) - config 비활성화 시
            elif (self._is_enabled("P7#7") and target_bay == BayType.BAY_36B and consecutive_count >= 2):
                return False, "P7#7 위반: B베이 3개 연속 배치 불가 (최대 2개까지) - 기본 제한"
        
        # P7#8: 주판 Only 연속 제한 (3판 연속 불가)
        if self._is_enabled("P7#8") and block.is_main_plate_only:
            if target_bay == BayType.BAY_35A and self.bay_35a_main_plate_consecutive >= 2:
                return False, "P7#8 위반: A베이 주판 Only 3판 연속 불가"
            elif target_bay == BayType.BAY_36B and self.bay_36b_main_plate_consecutive >= 2:
                return False, "P7#8 위반: B베이 주판 Only 3판 연속 불가"

################################################################################################################################################################################################
# fix: 론지 24개 이상 연속 송선 금지
################################################################################################################################################################################################
        # [AGENT-EDIT] config 기반 동적 토글 (enabled_constraints 반영)
        high_longi_enabled = False
        if self.constraint_config:
            try:
                high_longi_enabled = self.constraint_config.is_constraint_enabled("HIGH_LONGI_SPLIT")
            except Exception:
                high_longi_enabled = getattr(self.constraint_config, "enable_high_longi_split", False)
        if (high_longi_enabled and
                self.constraint_config and
                block.longi_count >= self.constraint_config.high_longi_threshold and
                self.last_high_longi_assignment.get(target_bay, False)):
            return False, f"고론지({block.longi_count}개) 연속 송선 불가: 이전 {target_bay.value}도 고론지 처리"

        # P7#11: 10번 블록 특별 처리 (B베이 연속 2판)
        if (self._is_enabled("P7#11") and block.block_number == 10 and 
            target_bay == BayType.BAY_35A and
            self.last_block_10_processed and
            self.block_10_b_bay_consecutive < 2):
            return False, "P7#11 위반: 10번 블록 후 B베이 연속 2판 필요"
        
        return True, ""
    
    def assign_bay(self, block: EnhancedBlock, target_bay: BayType, processing_time: float) -> List[ConstraintViolation]:
        """
        베이 할당 및 상태 업데이트 (물리적 제약 위반 기록 포함)
        
        Args:
            block: 처리할 블록
            target_bay: 할당할 베이
            processing_time: 처리시간 (초)
            
        Returns:
            제약조건 위반 리스트
        """
        violations = []
        
        # ✅ 물리적 제약 위반 검증 및 기록
        # P7#2: 21m 초과 → B베이 권장 (위반 기록)
        if self._is_enabled("P7#2") and block.width > 21.0 and target_bay == BayType.BAY_35A:
            violations.append(ConstraintViolation(
                constraint_id="P7#2",
                message=f"폭 {block.width:.1f}m > 21m인데 A베이 할당됨 (B베이 권장)",
                severity="WARNING",
                block_id=block.block_id
            ))
        
        # P7#10: 론지 30개 이상 → A베이 권장 (위반 기록)
        if self._is_enabled("P7#10") and block.longi_count >= 30 and target_bay == BayType.BAY_36B:
            violations.append(ConstraintViolation(
                constraint_id="P7#10",
                message=f"론지 {block.longi_count}개 ≥ 30개인데 B베이 할당됨 (A베이 권장)",
                severity="WARNING",
                block_id=block.block_id
            ))
        
        # P7#12: LT강재 → A베이 권장 (위반 기록)
        if self._is_enabled("P7#12") and hasattr(block, 'material_type') and block.material_type.value == 'LT' and target_bay == BayType.BAY_36B:
            violations.append(ConstraintViolation(
                constraint_id="P7#12",
                message=f"LT강재인데 B베이 할당됨 (A베이 권장)",
                severity="WARNING",
                block_id=block.block_id
            ))
        
        # ✅ 메타데이터 기반 원자 단위 할당 기록
        self._record_atomic_assignment(block.block_id, target_bay, processing_time)
        
        # 베이 할당 실행
        if target_bay == BayType.BAY_35A:
            self.bay_35a_worktime += processing_time
            self.bay_35a_block_count += 1
        else:
            self.bay_36b_worktime += processing_time
            self.bay_36b_block_count += 1
        
        # ✅ 연속성 카운터 업데이트 (메타데이터 기반)
        if target_bay == BayType.BAY_35A:
            if self.last_bay_assignment == BayType.BAY_35A:
                # 메타데이터 기반 정확한 연속성 계산
                atomic_consecutive = self._get_consecutive_count_with_metadata(BayType.BAY_35A)
                self.bay_35a_consecutive_count = atomic_consecutive
            else:
                # 별판이나 P/S 쌍의 원자 단위 카운트
                atomic_count = self._get_atomic_unit_count(block.block_id)
                self.bay_35a_consecutive_count = atomic_count
            self.bay_35a_longi_total += block.longi_count
        else:
            self.bay_35a_consecutive_count = 0
            self.bay_36b_longi_total += block.longi_count

        # ✅ 36B 베이 연속성 카운터 업데이트
        if target_bay == BayType.BAY_36B:
            if self.last_bay_assignment == BayType.BAY_36B:
                # 메타데이터 기반 정확한 연속성 계산
                atomic_consecutive = self._get_consecutive_count_with_metadata(BayType.BAY_36B)
                self.bay_36b_consecutive_count = atomic_consecutive
            else:
                # 별판이나 P/S 쌍의 원자 단위 카운트
                atomic_count = self._get_atomic_unit_count(block.block_id)
                self.bay_36b_consecutive_count = atomic_count
        else:
            self.bay_36b_consecutive_count = 0
        
        # 주판 Only 연속 카운터 업데이트 (P7#8)
        if block.is_main_plate_only:
            if target_bay == BayType.BAY_35A:
                self.bay_35a_main_plate_consecutive += 1
                self.bay_36b_main_plate_consecutive = 0
            else:
                self.bay_36b_main_plate_consecutive += 1
                self.bay_35a_main_plate_consecutive = 0
        else:
            self.bay_35a_main_plate_consecutive = 0
            self.bay_36b_main_plate_consecutive = 0
        
        # ✅ 10번 블록 연속 카운터 업데이트 (메타데이터 기반)
        if block.block_id in self.block_10_ids:
            self.last_block_10_processed = True
            self.block_10_b_bay_consecutive = 0  # 리셋
        else:
            if self.last_block_10_processed and target_bay == BayType.BAY_36B:
                self.block_10_b_bay_consecutive += 1
                if self.block_10_b_bay_consecutive >= self.block_10_required_b_count:
                    self.last_block_10_processed = False  # 조건 완료

################################################################################################################################################################################################
# fix: 라인 그룹/고론지 상태 업데이트
################################################################################################################################################################################################
        # [AGENT-EDIT] config 기반 동적 토글 (LINE_GROUP_CONSTRAINT)
        line_group_enabled = False
        if self.constraint_config:
            try:
                line_group_enabled = self.constraint_config.is_constraint_enabled("LINE_GROUP_CONSTRAINT")
            except Exception:
                line_group_enabled = getattr(self.constraint_config, "enable_line_group_constraint", False)
        if line_group_enabled:
            line_group = getattr(block, 'line_group', None)
            if line_group and line_group.startswith('L'):
                self.line_assignment_history.append(line_group)
                if self.last_line_group == line_group:
                    self.line_group_streak += 1
                else:
                    self.last_line_group = line_group
                    self.line_group_streak = 1
            else:
                self.last_line_group = None
                self.line_group_streak = 0
        else:
            self.last_line_group = None
            self.line_group_streak = 0

        threshold = 24  # 론지 연속 제한 기본 임계값
        if self.constraint_config:
            threshold = self.constraint_config.high_longi_threshold
        self.last_high_longi_assignment[target_bay] = block.longi_count >= threshold

        # 상태 업데이트
        self.last_bay_assignment = target_bay
        self.assignment_history.append((block.block_id, target_bay, processing_time))
        
        # ✅ P/S 쌍 베이 할당 추적 업데이트 (메타데이터 기반)
        if block.block_id in self.ps_pair_info:
            self.ps_pair_bay_assignments[block.block_id] = target_bay
            
            # 할당 순서 기록 (P: 1, S: 2)
            ps_info = self.ps_pair_info[block.block_id]
            if block.block_id == ps_info['port_block_id']:
                pair_key = f"{block.block_id}_{ps_info['starboard_block_id']}"
                self.ps_pair_order_tracking[pair_key] = 1  # P 먼저
            elif block.block_id == ps_info['starboard_block_id']:
                pair_key = f"{ps_info['port_block_id']}_{block.block_id}"
                self.ps_pair_order_tracking[pair_key] = 2  # S 나중
        
        return violations
    
    def get_load_balance_score(self) -> float:
        """베이 간 부하 균형 점수 (0~1, 1이 완벽한 균형)"""
        total_work = self.bay_35a_worktime + self.bay_36b_worktime
        if total_work == 0:
            return 1.0

        balance_diff = abs(self.bay_35a_worktime - self.bay_36b_worktime)
        time_score = 1.0 - (balance_diff / total_work)

        if self.constraint_config and getattr(self.constraint_config, 'enable_longi_load_balance', False):
            total_longi = self.bay_35a_longi_total + self.bay_36b_longi_total
            if total_longi > 0:
                longi_diff = abs(self.bay_35a_longi_total - self.bay_36b_longi_total)
                longi_score = 1.0 - (longi_diff / total_longi)
            else:
                longi_score = 1.0
            return max(0.0, min(1.0, (time_score + longi_score) / 2.0))

        return max(0.0, min(1.0, time_score))
    
    def reset(self):
        """상태 리셋"""
        print(f"🔄 BayStateTracker 리셋: atomic_history 길이 {len(self.atomic_assignment_history)} → 0")
        # 🔥 Critical Fix: worktime 리셋 주석 해제
        self.bay_35a_worktime = 0.0
        self.bay_36b_worktime = 0.0
        self.bay_35a_last_completion = None
        self.bay_36b_last_completion = None
        self.bay_35a_block_count = 0
        self.bay_36b_block_count = 0
        self.last_bay_assignment = None
        self.bay_35a_consecutive_count = 0
        self.bay_35a_main_plate_consecutive = 0
        self.bay_36b_consecutive_count = 0
        self.block_10_b_bay_consecutive = 0
        self.last_block_10_processed = False
        self.assignment_history.clear()
        self.atomic_assignment_history.clear()
        self.last_line_group = None
        self.line_group_streak = 0
        self.line_assignment_history.clear()
        self.last_high_longi_assignment[BayType.BAY_35A] = False
        self.last_high_longi_assignment[BayType.BAY_36B] = False
        self.bay_35a_longi_total = 0
        self.bay_36b_longi_total = 0
        print(f"✅ BayStateTracker 리셋 완료: atomic_history 길이 {len(self.atomic_assignment_history)}")

    def get_status(self) -> Dict:
        """현재 베이 상태 반환"""
        return {
            "bay_35a_worktime": self.bay_35a_worktime,
            "bay_36b_worktime": self.bay_36b_worktime,
            "bay_35a_blocks": self.bay_35a_block_count,
            "bay_36b_blocks": self.bay_36b_block_count,
            "bay_35a_longi_total": self.bay_35a_longi_total,
            "bay_36b_longi_total": self.bay_36b_longi_total,
            "load_balance_score": self.get_load_balance_score(),
            "bay_35a_consecutive": self.bay_35a_consecutive_count,
            "main_plate_consecutive_35a": self.bay_35a_main_plate_consecutive,
            "main_plate_consecutive_36b": self.bay_36b_main_plate_consecutive,
            "bay_36b_consecutive": self.bay_36b_consecutive_count,
            "block_10_b_consecutive": self.block_10_b_bay_consecutive,
            "last_block_10_processed": self.last_block_10_processed
        }
    
    # =================================================================
    # action_masking.py에서 사용하는 추가 메서드들
    # =================================================================
    
    def get_consecutive_count(self, bay_type: BayType) -> int:
        """특정 베이의 연속 배치 카운트 반환 (P7#7용)"""
        if bay_type == BayType.BAY_35A:
            return self.bay_35a_consecutive_count
        elif bay_type == BayType.BAY_36B:
            # ✅ 36B 베이 연속 카운트 반환
            return self.bay_36b_consecutive_count
        else:
            return 0
    
    def get_main_plate_consecutive_count(self, bay_type: BayType) -> int:
        """특정 베이의 주판 Only 연속 카운트 반환 (P7#8용)"""
        if bay_type == BayType.BAY_35A:
            return self.bay_35a_main_plate_consecutive
        else:
            return self.bay_36b_main_plate_consecutive
    
    def should_force_bay_for_block_10(self, block: EnhancedBlock) -> bool:
        """10번 블록 처리 후 B베이 강제 할당 여부 (P7#11용)"""
        # 10번 블록이 처리된 후이고, 아직 B베이 연속 2판이 완료되지 않은 경우
        return (self.last_block_10_processed and 
                self.block_10_b_bay_consecutive < 2 and
                hasattr(block, 'block_number') and 
                block.block_number != 10)  # 10번 블록 자체는 제외
    
    def get_required_bay_for_block_10(self) -> Optional[BayType]:
        """10번 블록 관련 필수 베이 반환 (P7#11용)"""
        if self.last_block_10_processed and self.block_10_b_bay_consecutive < 2:
            return BayType.BAY_36B  # B베이 강제
        return None
