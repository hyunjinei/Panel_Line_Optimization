# [AGENT-ADD] Split from constraint_managers.py to improve readability.

"""Calendar and shift manager."""

from datetime import datetime, timedelta, time, date
from typing import Dict, Optional, Set, Tuple

class CalendarManager:
    """
    P5#13,14,15,16: 달력 및 시간 관리 매니저
    
    구현된 제약조건:
    - P5#13: 야간 시간 처리 (15:00-08:00) - 현직자 정보 반영
    - P5#14: 일요일 Capa 제한
    - P5#15: 명절 전날 야간 없음 (15:00 이후)
    - P5#16: 혹서기 Capa 감소 (6-8월)
    
    시간 체계: 08:00-15:00 = 주간, 15:00-08:00 = 야간 (현직자 정보 반영)
    """
    
    def __init__(self, daily_structure=None, calendar_overrides: Optional[Dict] = None):  # ✅ 메타데이터 매개변수 추가
        # ✅ 메타데이터 저장
        self.daily_structure = daily_structure or {}
        overrides = calendar_overrides or {}
        
        # [AGENT-EDIT] 입력 데이터 연도 범위를 따라 공휴일 연도를 동적으로 구성한다.
        import holidays
        
        # ✅ 시간대 정의 수정 (현직자 정보 반영: 야간 = 15:00 이후)
        self.day_shift_start = time(8, 0)    # 08:00
        self.day_shift_end = time(15, 0)     # 15:00 (22:00에서 수정)
        self.night_shift_start = time(15, 0) # 15:00 (22:00에서 수정)
        self.night_shift_end = time(8, 0)    # 08:00 (다음날)
        
        # ✅ 혹서기 기간 수정 (6-8월)
        self.hot_season_months = [6, 7, 8]  # [7, 8]에서 6월 추가

        # [AGENT-EDIT] 공장 휴무/중지/점심시간 오버라이드 (기본: 비활성)
        self.enable_closed_dates = bool(overrides.get('enable_closed_dates', False))
        self.enable_afternoon_shutdown = bool(overrides.get('enable_afternoon_shutdown', False))
        self.enable_morning_shutdown = bool(overrides.get('enable_morning_shutdown', False))
        self.enable_lunch_break = bool(overrides.get('enable_lunch_break', False))

        def _parse_date(value):
            if isinstance(value, datetime):
                return value.date()
            if isinstance(value, date):
                return value
            if isinstance(value, (int, float)):
                if isinstance(value, float) and value != value:
                    return None
                cleaned = str(int(value))
                if cleaned.isdigit() and len(cleaned) == 8:
                    return datetime.strptime(cleaned, "%Y%m%d").date()
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned.isdigit() and len(cleaned) == 8:
                    return datetime.strptime(cleaned, "%Y%m%d").date()
                for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
                    try:
                        return datetime.strptime(cleaned, fmt).date()
                    except ValueError:
                        continue
            return None

        def _parse_time(value, default: time):
            if isinstance(value, time):
                return value
            if isinstance(value, str):
                cleaned = value.strip()
                for fmt in ("%H:%M", "%H:%M:%S"):
                    try:
                        return datetime.strptime(cleaned, fmt).time()
                    except ValueError:
                        continue
            return default

        def _collect_holiday_years() -> Set[int]:
            years: Set[int] = set()

            def _register_year(value) -> None:
                parsed = _parse_date(value)
                if parsed is not None:
                    years.add(parsed.year)

            if isinstance(self.daily_structure, dict):
                for day_key, day_value in self.daily_structure.items():
                    _register_year(day_key)
                    if isinstance(day_value, dict):
                        for nested_key in ("date", "start_date", "target_date"):
                            if nested_key in day_value:
                                _register_year(day_value.get(nested_key))

            for override_key in (
                "closed_dates",
                "work_dates",
                "afternoon_shutdown_dates",
                "morning_shutdown_dates",
            ):
                for item in overrides.get(override_key, []) or []:
                    _register_year(item)

            for schedule_key in ("afternoon_shutdown_schedule", "morning_shutdown_schedule"):
                schedule = overrides.get(schedule_key) or {}
                if isinstance(schedule, dict):
                    for day_key in schedule.keys():
                        _register_year(day_key)

            if not years:
                years.add(datetime.now().year)
            return years

        self.holiday_years = sorted(_collect_holiday_years())
        self.kr_holidays = holidays.KR(years=self.holiday_years)  # 한국 공휴일 자동 생성
        # print(f"✅ holidays 라이브러리 로드 성공: {len(self.kr_holidays)}개 공휴일 자동 생성")

        closed_dates_raw = overrides.get('closed_dates', []) or []
        self.closed_dates: Set[date] = set()
        for item in closed_dates_raw:
            parsed = _parse_date(item)
            if parsed:
                self.closed_dates.add(parsed)

        # [AGENT-ADD] GUI calendar: explicitly opened days override closed/partial shutdown dates.
        work_dates_raw = overrides.get('work_dates', []) or []
        self.work_dates: Set[date] = set()
        for item in work_dates_raw:
            parsed = _parse_date(item)
            if parsed:
                self.work_dates.add(parsed)

        afternoon_dates_raw = overrides.get('afternoon_shutdown_dates', []) or []
        self.afternoon_shutdown_dates: Set[date] = set()
        for item in afternoon_dates_raw:
            parsed = _parse_date(item)
            if parsed:
                self.afternoon_shutdown_dates.add(parsed)

        self.afternoon_shutdown_schedule = overrides.get('afternoon_shutdown_schedule') or {}

        morning_dates_raw = overrides.get('morning_shutdown_dates', []) or []
        self.morning_shutdown_dates: Set[date] = set()
        for item in morning_dates_raw:
            parsed = _parse_date(item)
            if parsed:
                self.morning_shutdown_dates.add(parsed)

        self.morning_shutdown_schedule = overrides.get('morning_shutdown_schedule') or {}

        self.morning_shutdown_start = _parse_time(overrides.get('morning_shutdown_start'), time(8, 0))
        self.morning_shutdown_end = _parse_time(overrides.get('morning_shutdown_end'), time(12, 0))
        self.afternoon_shutdown_start = _parse_time(overrides.get('afternoon_shutdown_start'), self.night_shift_start)
        self.afternoon_shutdown_end = _parse_time(overrides.get('afternoon_shutdown_end'), self.day_shift_start)

        self.lunch_break_start = _parse_time(overrides.get('lunch_break_start'), time(12, 0))
        self.lunch_break_end = _parse_time(overrides.get('lunch_break_end'), time(13, 0))
        
        # 현재 처리 중인 날짜
        self.current_date = None
    
    def is_holiday(self, target_date: datetime) -> bool:
        """해당 날짜가 휴일인지 확인 (holidays 라이브러리 사용)"""
        date_only = target_date.date()
        return date_only in self.kr_holidays

    # [AGENT-ADD] 하드코딩 휴무/중지 체크
    def is_closed_day(self, target_date: datetime) -> bool:
        """공장 휴무일 여부"""
        if target_date.date() in self.work_dates:
            return False
        if not self.enable_closed_dates:
            return False
        return target_date.date() in self.closed_dates

    def _is_closed_by_full_day(self, target_time: datetime) -> bool:
        """휴무일 08:00~익일 07:59 범위"""
        if not self.enable_closed_dates:
            return False
        date_only = target_time.date()
        if date_only in self.work_dates:
            return False
        # 휴무일 당일 08:00 이후
        if date_only in self.closed_dates and target_time.time() >= self.day_shift_start:
            return True
        # 휴무일 다음날 08:00 이전
        prev_day = date_only - timedelta(days=1)
        if prev_day in self.closed_dates and prev_day not in self.work_dates and target_time.time() < self.day_shift_start:
            return True
        return False

    def _is_closed_by_afternoon_shutdown(self, target_time: datetime) -> bool:
        """오후 가동 중지 (15:00~익일 07:59)"""
        if not self.enable_afternoon_shutdown:
            return False
        date_only = target_time.date()
        if date_only in self.work_dates:
            return False
        t = target_time.time()
        start, end = self._get_afternoon_shutdown_range(date_only)
        if date_only in self.afternoon_shutdown_dates and self._time_in_range(t, start, end):
            return True
        prev_day = date_only - timedelta(days=1)
        if prev_day in self.afternoon_shutdown_dates and prev_day not in self.work_dates:
            prev_start, prev_end = self._get_afternoon_shutdown_range(prev_day)
            if prev_start > prev_end and t < prev_end:
                return True
        return False

    def _is_closed_by_morning_shutdown(self, target_time: datetime) -> bool:
        """오전 가동 중지 (기본: 08:00~12:00)"""
        if not self.enable_morning_shutdown:
            return False
        date_only = target_time.date()
        if date_only in self.work_dates:
            return False
        if date_only not in self.morning_shutdown_dates:
            return False
        t = target_time.time()
        start, end = self._get_morning_shutdown_range(date_only)
        return self._time_in_range(t, start, end)

    def _is_closed_by_lunch_break(self, target_time: datetime) -> bool:
        if not self.enable_lunch_break:
            return False
        t = target_time.time()
        return self.lunch_break_start <= t < self.lunch_break_end

    def is_closed_time(self, target_time: datetime) -> bool:
        """공장 중지 시간 여부 (휴무일/부분중지/점심시간)"""
        if self._is_closed_by_full_day(target_time):
            return True
        if self._is_closed_by_afternoon_shutdown(target_time):
            return True
        if self._is_closed_by_morning_shutdown(target_time):
            return True
        if self._is_closed_by_lunch_break(target_time):
            return True
        return False

    def next_open_time(self, target_time: datetime) -> datetime:
        """다음 가동 가능 시각 반환 (하드코딩 규칙 반영)"""
        current = target_time
        while True:
            # 휴무일 08:00 이후면 다음날 08:00
            if self.enable_closed_dates and current.date() in self.closed_dates and current.date() not in self.work_dates and current.time() >= self.day_shift_start:
                current = datetime.combine(current.date() + timedelta(days=1), self.day_shift_start)
                continue
            # 휴무일 다음날 08:00 이전이면 오늘 08:00
            prev_date = current.date() - timedelta(days=1)
            if self.enable_closed_dates and prev_date in self.closed_dates and prev_date not in self.work_dates and current.time() < self.day_shift_start:
                current = datetime.combine(current.date(), self.day_shift_start)
                continue

            # 오후 중지 처리 (날짜별 시간 지원)
            if self.enable_afternoon_shutdown and current.date() in self.afternoon_shutdown_dates and current.date() not in self.work_dates:
                start, end = self._get_afternoon_shutdown_range(current.date())
                t = current.time()
                if self._time_in_range(t, start, end):
                    if start <= end:
                        current = datetime.combine(current.date(), end)
                    else:
                        if t >= start:
                            current = datetime.combine(current.date() + timedelta(days=1), end)
                        else:
                            current = datetime.combine(current.date(), end)
                    continue
            if self.enable_afternoon_shutdown and (current.date() - timedelta(days=1)) in self.afternoon_shutdown_dates:
                prev_day = current.date() - timedelta(days=1)
                if prev_day in self.work_dates:
                    prev_start, prev_end = self.day_shift_start, self.day_shift_start
                else:
                    prev_start, prev_end = self._get_afternoon_shutdown_range(prev_day)
                if prev_start > prev_end and current.time() < prev_end:
                    current = datetime.combine(current.date(), prev_end)
                    continue

            # 오전 중지: 해당일 오전 구간이면 종료 시각으로 이동
            if self.enable_morning_shutdown and current.date() in self.morning_shutdown_dates and current.date() not in self.work_dates:
                start, end = self._get_morning_shutdown_range(current.date())
                if self._time_in_range(current.time(), start, end):
                    current = datetime.combine(current.date(), end)
                    continue

            # 점심시간
            if self._is_closed_by_lunch_break(current):
                current = datetime.combine(current.date(), self.lunch_break_end)
                continue

            return current

    def _time_in_range(self, t: time, start: time, end: time) -> bool:
        if start <= end:
            return start <= t < end
        return t >= start or t < end

    def _get_morning_shutdown_range(self, target_date: date) -> Tuple[time, time]:
        raw = self.morning_shutdown_schedule.get(target_date)
        if raw:
            start = self._parse_time(raw.get("start")) if isinstance(raw, dict) else None
            end = self._parse_time(raw.get("end")) if isinstance(raw, dict) else None
            if start and end:
                return start, end
            if isinstance(raw, str):
                parsed = self._parse_time_range(raw)
                if parsed:
                    return parsed
        return self.morning_shutdown_start, self.morning_shutdown_end

    def _get_afternoon_shutdown_range(self, target_date: date) -> Tuple[time, time]:
        raw = self.afternoon_shutdown_schedule.get(target_date)
        if raw:
            start = self._parse_time(raw.get("start")) if isinstance(raw, dict) else None
            end = self._parse_time(raw.get("end")) if isinstance(raw, dict) else None
            if start and end:
                return start, end
            if isinstance(raw, str):
                parsed = self._parse_time_range(raw)
                if parsed:
                    return parsed
        return self.afternoon_shutdown_start, self.afternoon_shutdown_end

    def _parse_time(self, value: Optional[str]) -> Optional[time]:
        if not value:
            return None
        try:
            return datetime.strptime(str(value).strip(), "%H:%M").time()
        except Exception:
            try:
                return datetime.strptime(str(value).strip(), "%H:%M:%S").time()
            except Exception:
                return None

    def _parse_time_range(self, raw: str) -> Optional[Tuple[time, time]]:
        if not raw:
            return None
        text = str(raw)
        if "-" in text:
            start_str, end_str = [p.strip() for p in text.split("-", 1)]
        elif "~" in text:
            start_str, end_str = [p.strip() for p in text.split("~", 1)]
        else:
            return None
        start = self._parse_time(start_str)
        end = self._parse_time(end_str)
        if not start or not end:
            return None
        return start, end
    
    def is_holiday_eve(self, target_date: datetime) -> bool:
        """해당 날짜가 명절 전날인지 확인 (P5#15) - 설날, 추석 전날만 해당"""
        date_only = target_date.date()
        next_day = date_only + timedelta(days=1)
        
        # 다음날이 공휴일인지 확인
        if next_day not in self.kr_holidays:
            return False
        
        # 다음날의 공휴일 이름 확인
        holiday_name = str(self.kr_holidays.get(next_day, ""))

        # [AGENT-EDIT] holidays.KR가 영문 공휴일명을 반환하는 환경에서도
        # 명절 전날 판정을 일관되게 유지한다.
        major_holiday_markers = [
            "설날",
            "추석",
            "Korean New Year",
            "Chuseok",
        ]
        is_major_holiday = any(marker in holiday_name for marker in major_holiday_markers)
        
        # if is_major_holiday:
            # print(f"🎌 명절 전날 감지: {target_date.strftime('%Y-%m-%d')} (다음날: {holiday_name})")
        
        return is_major_holiday
    
    def is_hot_season(self, target_date: datetime) -> bool:
        """해당 날짜가 혹서기인지 확인 (P5#16) - 6-8월"""
        return target_date.month in self.hot_season_months
    
    def has_night_work(self, target_date: datetime) -> bool:
        """해당 날짜에 야간 작업이 가능한지 확인 (P5#15)"""
        # 메타데이터에서 확인
        date_key = target_date.strftime('%Y%m%d')
        if date_key in self.daily_structure:
            # 명절 전날이면 야간 작업 불가
            return not self.is_holiday_eve(target_date)
        
        # 기본적으로 명절 전날이 아니면 야간 작업 가능
        return not self.is_holiday_eve(target_date)
    
    def get_capacity_factor(self, target_date: datetime) -> float:
        """해당 날짜의 용량 배수 반환 (P5#16)"""
        if self.is_hot_season(target_date):
            return 0.85  # 혹서기 15% 감소
        return 1.0
    
    def add_holiday(self, holiday_date: date):
        """새로운 휴일 추가 (holidays 라이브러리 사용 중이므로 수동 추가 불가)"""
        print(f"⚠️ holidays 라이브러리 사용 중: 수동 공휴일 추가 불가 ({holiday_date})")
        print(f"   holidays 라이브러리가 자동으로 한국 공휴일을 관리합니다.")
    
    def is_weekend(self, target_date: datetime) -> bool:
        """주말 여부 확인"""
        return target_date.weekday() >= 5  # 토요일(5), 일요일(6)
    
    def is_sunday(self, target_date: datetime) -> bool:
        """일요일 여부 확인 (P5#14)"""
        return target_date.weekday() == 6
    
    def get_shift_type(self, current_time: datetime) -> str:
        """현재 시간의 근무 시간대 반환 (현직자 정보 반영)"""
        current_time_only = current_time.time()
        
        if self.day_shift_start <= current_time_only < self.day_shift_end:
            return "DAY"    # 08:00 ~ 15:00
        else:
            return "NIGHT"  # 15:00 ~ 08:00 (다음날)
    
    def get_status(self, date: datetime) -> Dict:
        """특정 날짜의 달력 상태 반환"""
        years_display = ",".join(str(year) for year in self.holiday_years)
        return {
            "date": date.strftime("%Y-%m-%d"),
            "is_holiday": self.is_holiday(date),
            "is_holiday_eve": self.is_holiday_eve(date),
            "is_hot_season": self.is_hot_season(date),
            "has_night_work": self.has_night_work(date),
            "capacity_adjustment": self.get_capacity_factor(date),
            "shift_boundary": "15:00 (현직자 정보 반영)",
            "library": f"holidays KR {years_display} ({len(self.kr_holidays)}개 공휴일)"
        }
