# [AGENT-ADD] Split from constraint_managers.py to improve readability.

"""Capacity tracking for daily seam constraints."""

from datetime import datetime
from typing import Dict, List, Optional, Tuple

from enhanced_environment.models import EnhancedBlock, ConstraintViolation

class CapacityTracker:
    """
    P5#8,9,10,P5#15,16: 용량 관리 매니저 (심수 기준으로 단순화)

    구현된 제약조건:
    - P5#8: 평일 하루 물량은 75심까지 (심+C/S 합계)
    - P5#9: 72심 초과시 최소 17블록 체크 (사전 검증으로 처리)
    - P5#10: 주말 하루 물량은 45심까지  
    - P5#15: 명절 전날 야간 없음 (CalendarManager 연동)
    - P5#16: 혹서기 Capa 감소 (CalendarManager 연동)
    """

    @staticmethod
    def _seam_load(block: EnhancedBlock) -> int:
        """Compute combined SEAM and C/SEAM load for capacity checks."""
        return block.seam_count + getattr(block, 'c_seam_count', 0)

    def __init__(self, daily_structure=None, fab_interval_tracking=None, constraint_config=None, capacity_hyperparams=None):
        # 심수만 추적
        self.daily_seam_used = 0
        self.weekend_seam_used = 0
        
        # ✅ P5#9를 위한 블록 수 추적 추가
        self.daily_block_count = 0
        self.weekend_block_count = 0
        
        # ✅ 하이퍼파라미터 기반 용량 설정
        self.capacity_hyperparams = capacity_hyperparams or {}
        
        # 기본 일별 용량 (하이퍼파라미터로 조정 가능)
        self.weekday_seam_limit = self.capacity_hyperparams.get('weekday_seam_limit', 75)  # 평일 심수 한계
        self.weekend_seam_limit = self.capacity_hyperparams.get('weekend_seam_limit', 45)  # 주말 심수 한계
        
        # ✅ 명절전날 심수 제한 (하이퍼파라미터)
        self.holiday_eve_seam_limit = self.capacity_hyperparams.get('holiday_eve_seam_limit', 35)  # 명절전날 심수 한계
        self.holiday_eve_enabled = self.capacity_hyperparams.get('holiday_eve_seam_reduction_enabled', True)  # 명절전날 심수 제한 사용
        
        # ✅ 혹서기 심수 제한 (하이퍼파라미터)
        self.hot_season_seam_reduction = self.capacity_hyperparams.get('hot_season_seam_reduction', 6)  # 혹서기 심수 감소량
        self.hot_season_reduction_type = self.capacity_hyperparams.get('hot_season_reduction_type', 'absolute')  # 'absolute' 또는 'percentage'
        self.hot_season_percentage_reduction = self.capacity_hyperparams.get('hot_season_percentage_reduction', 15)  # 백분율 감소 (%)
        
        # ✅ 메타데이터 저장
        self.daily_structure = daily_structure or {}
        self.fab_interval_tracking = fab_interval_tracking or {}
        
        # ✅ 제약조건 설정 저장
        self.constraint_config = constraint_config
        # ✅ 날짜별 심수 용량 오버라이드
        self.daily_seam_cap_overrides = getattr(constraint_config, "daily_seam_cap_overrides", {}) if constraint_config else {}
        self.daily_seam_cap_scales = getattr(constraint_config, "daily_seam_cap_scales", {}) if constraint_config else {}
        
        # ✅ P5#6: FAB 간격 추적 상태
        self.fab_condition_blocks = set(self.fab_interval_tracking.get('condition_blocks', []))
        self.fab_interval_requirement = self.fab_interval_tracking.get('interval_requirement', 5)
        self.fab_current_counter = 0
        self.fab_last_condition_position = -1
        
        # ✅ 하이퍼파라미터 출력
        # print(f"   📊 용량 하이퍼파라미터:")
        # print(f"      평일: {self.weekday_seam_limit}심, 주말: {self.weekend_seam_limit}심")
        # if self.holiday_eve_enabled:
            # print(f"      명절전날: {self.holiday_eve_seam_limit}심 (심수 제한)")
        # else:
            # print(f"      명절전날: 시간 제한 방식")
        # if self.hot_season_reduction_type == 'absolute':
            # print(f"      혹서기: -{self.hot_season_seam_reduction}심 (절대값 감소)")
        # else:
            # print(f"      혹서기: -{self.hot_season_percentage_reduction}% (백분율 감소)")
    
    def _resolve_daily_seam_override(self, current_time: Optional[datetime]) -> Optional[int]:
        """특정 날짜 심수 용량 오버라이드 조회 (YYYYMMDD 기반)."""
        if not current_time:
            return None
        override_map = getattr(self.constraint_config, "daily_seam_cap_overrides", {}) or {}
        if not isinstance(override_map, dict):
            return None
        target_key = current_time.strftime("%Y%m%d")
        if target_key in override_map:
            return override_map[target_key]
        for raw_key, raw_limit in override_map.items():
            try:
                if isinstance(raw_key, datetime):
                    key_str = raw_key.strftime("%Y%m%d")
                else:
                    key_str = str(raw_key).replace("-", "").replace("/", "").strip()
                if key_str == target_key:
                    return raw_limit
            except Exception:
                continue
        return None

    def _resolve_daily_seam_scale(self, current_time: Optional[datetime]) -> Optional[float]:
        """특정 날짜 심수 배율 오버라이드 조회 (YYYYMMDD 기반)."""
        if not current_time:
            return None
        scale_map = getattr(self.constraint_config, "daily_seam_cap_scales", {}) or {}
        if not isinstance(scale_map, dict):
            return None
        target_key = current_time.strftime("%Y%m%d")
        if target_key in scale_map:
            return scale_map[target_key]
        for raw_key, raw_scale in scale_map.items():
            try:
                if isinstance(raw_key, datetime):
                    key_str = raw_key.strftime("%Y%m%d")
                else:
                    key_str = str(raw_key).replace("-", "").replace("/", "").strip()
                if key_str == target_key:
                    return raw_scale
            except Exception:
                continue
        return None

    def get_capacity_limits(
        self,
        is_weekend: bool,
        is_hot_season: bool = False,
        is_holiday_eve: bool = False,
        current_time: Optional[datetime] = None,
    ) -> int:
        """
        현재 심수 용량 한계 반환 (하이퍼파라미터 기반)
        
        Args:
            is_weekend: 주말 여부
            is_hot_season: 혹서기 여부 (P5#16)
            is_holiday_eve: 명절전날 여부 (P5#15)
            
        Returns:
            심수 한계
        """
        # 🎯 우선순위 0: 특정 날짜 심수 용량 오버라이드
        override_limit = self._resolve_daily_seam_override(current_time)
        if override_limit is not None:
            return max(1, int(override_limit))

        # 기본 용량 설정
        base_limit = self.weekend_seam_limit if is_weekend else self.weekday_seam_limit

        # 🎯 우선순위 1: 명절전날 심수 제한 (가장 엄격)
        if self.holiday_eve_enabled and is_holiday_eve:
            base_limit = self.holiday_eve_seam_limit
        
        # 🎯 우선순위 2: 혹서기 용량 감소 (P5#16 설정 체크)
        if (is_hot_season and not (self.holiday_eve_enabled and is_holiday_eve) and
            self.constraint_config and 
            self.constraint_config.is_constraint_enabled("P5#16")):
            if self.hot_season_reduction_type == 'absolute':
                # 절대값 감소 (예: 6심 감소)
                base_limit = max(1, int(base_limit - self.hot_season_seam_reduction))
            else:  # percentage
                # 백분율 감소 (예: 15% 감소)
                reduction_amount = int(base_limit * (self.hot_season_percentage_reduction / 100))
                base_limit = max(1, int(base_limit - reduction_amount))
            
        # 🎯 우선순위 3: 날짜별 배율 적용
        scale = self._resolve_daily_seam_scale(current_time)
        if scale is not None:
            try:
                base_limit = max(1, int(base_limit * float(scale)))
            except Exception:
                pass

        return base_limit
    
    def get_capacity_used(self, is_weekend: bool) -> int:
        """
        현재 사용된 심수 반환
        
        Returns:
            사용된 심수
        """
        if is_weekend:
            return self.weekend_seam_used
        else:
            return self.daily_seam_used
    
    def get_block_count(self, is_weekend: bool) -> int:
        """
        현재 처리된 블록 수 반환 (P5#9용)
        
        Args:
            is_weekend: 주말 여부
            
        Returns:
            처리된 블록 수
        """
        if is_weekend:
            return self.weekend_block_count
        else:
            return self.daily_block_count
    
    def can_add_block(
        self,
        block: EnhancedBlock,
        is_weekend: bool,
        is_hot_season: bool = False,
        is_holiday_eve: bool = False,
        current_time: Optional[datetime] = None,
    ) -> Tuple[bool, str]:
        """
        블록 추가 가능 여부 확인 (심수 + P5#9 블록 수 체크) - 하이퍼파라미터 기반
        
        Args:
            block: 추가할 블록
            is_weekend: 주말 여부
            is_hot_season: 혹서기 여부
            is_holiday_eve: 명절전날 여부
            
        Returns:
            (추가 가능 여부, 불가능한 경우 사유)
        """
        seam_limit = self.get_capacity_limits(is_weekend, is_hot_season, is_holiday_eve, current_time=current_time)
        used_seam = self.get_capacity_used(is_weekend)
        block_load = self._seam_load(block)
        predicted_seam = used_seam + block_load
        weekday_capacity_enabled = bool(
            self.constraint_config and self.constraint_config.is_constraint_enabled("P5#8")
        )
        weekend_capacity_enabled = bool(
            self.constraint_config and self.constraint_config.is_constraint_enabled("P5#10")
        )
        holiday_capacity_enabled = bool(
            self.constraint_config and self.constraint_config.is_constraint_enabled("P5#15")
        )
        hot_season_capacity_enabled = bool(
            self.constraint_config and self.constraint_config.is_constraint_enabled("P5#16")
        )
        
        # ✅ 명절전날 심수 제한 체크
        if self.holiday_eve_enabled and is_holiday_eve and holiday_capacity_enabled:
            if predicted_seam > seam_limit:
                return False, f"명절전날 심수 제한: {predicted_seam}/{seam_limit}심 (하이퍼파라미터 기반)"
        
        # ✅ P5#9: 평일 72심 초과 시 블록 수 17개 이상 필수
        if (not is_weekend and
            self.constraint_config and
            self.constraint_config.is_constraint_enabled("P5#9") and
            predicted_seam > 72):
            current_blocks = self.get_block_count(is_weekend)
            predicted_blocks = current_blocks + 1

            if predicted_blocks < 17:
                # [AGENT-EDIT] 72심 초과는 17블록 이상일 때만 허용
                return False, f"P5#9: 72심 초과 시 17블록 이상 필요 {predicted_seam}/72심 (블록수: {predicted_blocks})"
        
        # 일반 심수 용량 체크
        if predicted_seam > seam_limit:
            if is_holiday_eve and self.holiday_eve_enabled and not holiday_capacity_enabled:
                return True, f"P5#15 비활성: {predicted_seam}/{seam_limit}심"
            if is_hot_season and not hot_season_capacity_enabled:
                if (is_weekend and not weekend_capacity_enabled) or ((not is_weekend) and not weekday_capacity_enabled):
                    return True, f"용량 제약 비활성: {predicted_seam}/{seam_limit}심"
            if is_weekend and not weekend_capacity_enabled:
                return True, f"P5#10 비활성: {predicted_seam}/{seam_limit}심"
            if (not is_weekend) and not weekday_capacity_enabled:
                return True, f"P5#8 비활성: {predicted_seam}/{seam_limit}심"
            constraint_type = ""
            if is_holiday_eve and self.holiday_eve_enabled:
                constraint_type = " (명절전날)"
            elif (
                is_hot_season and
                self.constraint_config and
                self.constraint_config.is_constraint_enabled("P5#16")
            ):
                constraint_type = f" (혹서기-{self.hot_season_reduction_type})"
            elif is_weekend:
                constraint_type = " (주말)"
            else:
                constraint_type = " (평일)"
            
            return False, f"심수 용량 초과: {predicted_seam}/{seam_limit}심{constraint_type}"
        
        return True, ""
    
    def add_block(
        self,
        block: EnhancedBlock,
        is_weekend: bool,
        is_hot_season: bool = False,
        is_holiday_eve: bool = False,
        current_time: Optional[datetime] = None,
    ) -> List[ConstraintViolation]:
        """
        블록을 용량에 추가 및 제약조건 위반 검증 (심수만) - 하이퍼파라미터 기반
        
        Args:
            block: 추가할 블록
            is_weekend: 주말 여부
            is_hot_season: 혹서기 여부
            is_holiday_eve: 명절전날 여부
            
        Returns:
            제약조건 위반 리스트
        """
        violations = []
        block_load = self._seam_load(block)
        weekday_capacity_enabled = bool(
            self.constraint_config and self.constraint_config.is_constraint_enabled("P5#8")
        )
        weekend_capacity_enabled = bool(
            self.constraint_config and self.constraint_config.is_constraint_enabled("P5#10")
        )
        holiday_capacity_enabled = bool(
            self.constraint_config and self.constraint_config.is_constraint_enabled("P5#15")
        )
        hot_season_capacity_enabled = bool(
            self.constraint_config and self.constraint_config.is_constraint_enabled("P5#16")
        )
        
        # ✅ P5#8,10,15,16: 용량 초과 검증 (하이퍼파라미터 기반)
        can_add, reason = self.can_add_block(
            block, is_weekend, is_hot_season, is_holiday_eve, current_time=current_time
        )
        if not can_add:
            # 적절한 제약조건 ID 선택
            constraint_id = "P5#8"  # 기본값 (평일)
            if reason.startswith("P5#9"):
                constraint_id = "P5#9"
            elif is_holiday_eve and self.holiday_eve_enabled and holiday_capacity_enabled:
                constraint_id = "P5#15"  # 명절전날
            elif (
                is_hot_season and
                hot_season_capacity_enabled
            ):
                constraint_id = "P5#16"  # 혹서기
            elif is_weekend and weekend_capacity_enabled:
                constraint_id = "P5#10"  # 주말
            elif (not is_weekend) and not weekday_capacity_enabled:
                constraint_id = ""

            if constraint_id:
                violations.append(ConstraintViolation(
                    constraint_id=constraint_id,
                    message=f"{reason}" if constraint_id == "P5#9" else f"용량 초과: {reason}",
                    severity="ERROR",
                    block_id=block.block_id
                ))
        
        # ✅ P5#9: 72심 초과 시 블록 수 17개 이상 필수 (평일만)
        if not is_weekend and self.constraint_config and self.constraint_config.is_constraint_enabled("P5#9"):
            total_seam_after = self.daily_seam_used + block_load
            total_blocks_after = self.daily_block_count + 1
            seam_limit = self.get_capacity_limits(is_weekend, is_hot_season, is_holiday_eve, current_time=current_time)

            if total_seam_after > 72:
                if total_blocks_after < 17:
                    # [AGENT-EDIT] 72심 초과는 17블록 이상일 때만 허용
                    if not any(v.constraint_id == "P5#9" for v in violations):
                        violations.append(ConstraintViolation(
                            constraint_id="P5#9",
                            message=f"P5#9 위반: 72심 초과 시 17블록 이상 필요 {total_seam_after}/72심 (블록수: {total_blocks_after})",
                            severity="ERROR",
                            block_id=block.block_id
                        ))
                else:
                    # [AGENT-EDIT] 17블록 이상이면 72~75 구간 허용 (정보성 기록)
                    if total_seam_after <= seam_limit:
                        violations.append(ConstraintViolation(
                            constraint_id="P5#9",
                            message=f"P5#9 조건 충족: 72심 초과지만 {total_blocks_after}/17블록 → 허용 (현재 {total_seam_after}/{seam_limit}심)",
                            severity="INFO",
                            block_id=block.block_id
                        ))
            # else:
            #     # 72심 이하는 항상 허용
            #     if total_blocks_after > 10:  # 관심 구간에서만 로그
            #         print(f"✅ P5#9 정상: {total_seam_after}심 ≤ 72심, 블록수 {total_blocks_after}")
        
        # 실제 용량 추가
        if is_weekend:
            self.weekend_seam_used += block_load
            self.weekend_block_count += 1
        else:
            self.daily_seam_used += block_load
            self.daily_block_count += 1
                
        return violations
    
    def check_calendar_constraints(self, block: EnhancedBlock, current_time: datetime, calendar_manager: 'CalendarManager') -> List[ConstraintViolation]:
        """
        달력 기반 제약조건 검증 (하이퍼파라미터 기반)
        
        Args:
            block: 처리할 블록
            current_time: 현재 시간
            calendar_manager: 달력 매니저
            
        Returns:
            제약조건 위반 리스트
        """
        violations = []
        
        is_weekend = calendar_manager.is_weekend(current_time)
        is_hot_season = calendar_manager.is_hot_season(current_time)
        is_holiday_eve = calendar_manager.is_holiday_eve(current_time)
        block_load = self._seam_load(block)
        
        # ✅ P5#15: 명절 전날 심수 제한 (하이퍼파라미터 기반)
        if (self.constraint_config and self.constraint_config.is_constraint_enabled("P5#15") and
            self.holiday_eve_enabled and is_holiday_eve):
            
            # 심수 기반 제한 체크
            current_used = self.get_capacity_used(is_weekend)
            if current_used + block_load > self.holiday_eve_seam_limit:
                violations.append(ConstraintViolation(
                    constraint_id="P5#15",
                    message=f"명절전날 심수 제한: {current_used + block_load}/{self.holiday_eve_seam_limit}심 (하이퍼파라미터: {self.holiday_eve_seam_limit}심)",
                    severity="ERROR",
                    block_id=block.block_id
                ))
        
        # ✅ P5#16: 혹서기 심수 제한 (하이퍼파라미터 기반)
        if (self.constraint_config and self.constraint_config.is_constraint_enabled("P5#16") and is_hot_season):
            
            # 하이퍼파라미터 기반 혹서기 용량 계산
            base_limit = self.weekday_seam_limit if not is_weekend else self.weekend_seam_limit
            hot_season_limit = self.get_capacity_limits(is_weekend, is_hot_season, is_holiday_eve, current_time=current_time)
            
            current_used = self.get_capacity_used(is_weekend)
            if current_used + block_load > hot_season_limit:
                reduction_info = ""
                if self.hot_season_reduction_type == 'absolute':
                    reduction_info = f"{self.hot_season_seam_reduction}심 절대값 감소"
                else:
                    reduction_info = f"{self.hot_season_percentage_reduction}% 비율 감소"

                violations.append(ConstraintViolation(
                    constraint_id="P5#16",
                    message=f"혹서기 심수 제한: {current_used + block_load}/{hot_season_limit}심 (하이퍼파라미터: {reduction_info})",
                    severity="ERROR", 
                    block_id=block.block_id
                ))
        
        return violations
    
    def reset_daily(self):
        """일일 용량 리셋 (심수 + 블록 수)"""
        self.daily_seam_used = 0
        self.daily_block_count = 0
    
    def reset_weekend(self):
        """주말 용량 리셋 (심수 + 블록 수)"""
        self.weekend_seam_used = 0
        self.weekend_block_count = 0
    
    def get_status(
        self,
        is_weekend: bool,
        is_hot_season: bool = False,
        is_holiday_eve: bool = False,
        current_time: Optional[datetime] = None,
    ) -> Dict:
        """현재 용량 상태 반환 (심수 + 블록 수 + 하이퍼파라미터 포함)"""
        seam_limit = self.get_capacity_limits(is_weekend, is_hot_season, is_holiday_eve, current_time=current_time)
        seam_used = self.get_capacity_used(is_weekend)
        
        # ✅ 블록 수 정보 추가
        if is_weekend:
            block_count = self.weekend_block_count
        else:
            block_count = self.daily_block_count
        
        # ✅ 하이퍼파라미터 상태 정보
        capacity_type = "평일"
        if is_holiday_eve and self.holiday_eve_enabled:
            capacity_type = "명절전날"
        elif is_hot_season:
            capacity_type = f"혹서기({self.hot_season_reduction_type})"
        elif is_weekend:
            capacity_type = "주말"
        
        return {
            "seam_used": seam_used,
            "seam_limit": seam_limit,
            "seam_remaining": seam_limit - seam_used,
            "utilization_seam": seam_used / seam_limit if seam_limit > 0 else 0,
            "block_count": block_count,  # ✅ 블록 수 추가
            "p5_9_expansion_available": not is_weekend and seam_used > 72 and block_count < 17,  # ✅ P5#9 확장 가능 여부
            "capacity_type": capacity_type,  # ✅ 하이퍼파라미터 기반 용량 타입
            "is_weekend": is_weekend,
            "is_hot_season": is_hot_season,
            "is_holiday_eve": is_holiday_eve,
            # ✅ 하이퍼파라미터 정보
            "hyperparams": {
                "weekday_limit": self.weekday_seam_limit,
                "weekend_limit": self.weekend_seam_limit,
                "holiday_eve_enabled": self.holiday_eve_enabled,
                "holiday_eve_limit": self.holiday_eve_seam_limit if self.holiday_eve_enabled else None,
                "hot_season_reduction_type": self.hot_season_reduction_type,
                "hot_season_reduction": self.hot_season_seam_reduction if self.hot_season_reduction_type == 'absolute' else self.hot_season_percentage_reduction
            }
        }
