"""Unified entrypoint for training/evaluation/heuristics."""

# [AGENT-ADD] Main orchestration entry for field users.
# [AGENT-EDIT] 상세 주석 추가: main.py 동작 흐름과 모듈 실행 방식을 명확히 설명.

import argparse
import os
import runpy
import sys
from typing import List

from runtime_config import load_runtime_config, set_runtime_config


def _normalize_method_name(name: str) -> str:
    if not name:
        return ""
    key = str(name).strip().lower()
    key = key.replace("-", "_").replace(" ", "")
    mapping = {
        "spt": "SPT",
        "spt휴리스틱": "SPT",
        "lpt": "LPT",
        "lpt휴리스틱": "LPT",
        "seam_min": "SEAM_MIN",
        "seammin": "SEAM_MIN",
        "seam": "SEAM_MIN",
        "rl": "RL",
        "강화학습": "RL",
        "excel": "EXCEL",
        "엑셀": "EXCEL",
        "엑셀순번": "EXCEL",
        "실적": "EXCEL",
        "실적데이터": "EXCEL",
        "actionmasking": "ACTIONMASKING",
        "action_masking": "ACTIONMASKING",
        "착수일": "ACTIONMASKING",
        "착수일기준": "ACTIONMASKING",
        "착수일휴리스틱": "ACTIONMASKING",
    }
    return mapping.get(key, key.upper())


def _get_selected_methods(config: dict) -> List[str]:
    # [AGENT-EDIT] evaluation이 비어있으면 eval을 보완값으로 병합
    evaluation_cfg = (config.get("evaluation") or {}) if isinstance(config, dict) else {}
    eval_fallback = (config.get("eval") or {}) if isinstance(config, dict) else {}
    if isinstance(evaluation_cfg, dict) and isinstance(eval_fallback, dict):
        evaluation_cfg = {**eval_fallback, **evaluation_cfg}
    methods = evaluation_cfg.get("methods") or evaluation_cfg.get("only_methods")
    if not methods:
        return []
    if isinstance(methods, str):
        methods = [m.strip() for m in methods.split(",") if m.strip()]
    return [_normalize_method_name(m) for m in methods]


def _normalize_relax_key(raw: str) -> str:
    if raw is None:
        return ""
    text = str(raw).strip().lower()
    return "".join(ch for ch in text if ch.isalnum() or "가" <= ch <= "힣")


def _allowed_relax_keys() -> set:
    # action_masking.py (masking/core.py)와 동일한 키 집합
    keys = {
        "3bay완화", "3bay", "3베이",
        "라인고정간격", "라인그룹", "라인연속",
        "라인혼합", "혼합", "p511",
        "cseam", "cseam완화",
        "곡판", "곡판간격",
        "고심수", "고심수간격",
        "p6", "p6시간",
        "p64",
        "p615", "p515",
        "용량", "용량완화",
    }
    return {_normalize_relax_key(k) for k in keys}


def _looks_like_constraint_id(text: str) -> bool:
    if not text:
        return False
    raw = str(text).strip()
    if "#" in raw:
        return True
    return raw.isupper()


def _summarize_list(values: List[str]) -> str:
    if not values:
        return "없음"
    return ", ".join(str(v) for v in values)


def validate_config(mode: str, config: dict) -> tuple[list, list, dict]:
    errors: list = []
    warnings: list = []
    summary: dict = {}

    data_cfg = config.get("data", {}) if isinstance(config, dict) else {}
    gantt_cfg = config.get("gantt", {}) if isinstance(config, dict) else {}
    excel_path = data_cfg.get("excel_path")
    sheet = data_cfg.get("sheet")

    # [AGENT-EDIT] evaluation이 비어있으면 eval을 보완값으로 병합
    eval_cfg = (config.get("evaluation") or {}) if isinstance(config, dict) else {}
    eval_fallback = (config.get("eval") or {}) if isinstance(config, dict) else {}
    if isinstance(eval_cfg, dict) and isinstance(eval_fallback, dict):
        eval_cfg = {**eval_fallback, **eval_cfg}
    eval_mode = eval_cfg.get("mode")
    try:
        eval_mode = int(eval_mode) if eval_mode is not None else None
    except Exception:
        warnings.append("evaluation.mode는 1 또는 2만 가능합니다. (현재 값 무시)")
        eval_mode = None

    selected_methods = _get_selected_methods(config)
    method_set = set(selected_methods)

    summary["mode"] = mode
    summary["eval_mode"] = eval_mode or "(기본값: 2)"
    summary["excel_path"] = excel_path or ""
    summary["sheet"] = sheet or ""
    summary["methods"] = selected_methods or ["(기본값 사용)"]
    summary["gantt_search_dir"] = gantt_cfg.get("search_dir") if isinstance(gantt_cfg, dict) else ""

    # 엑셀 경로 필요 조건
    needs_excel = False
    if mode in ("replay", "replay_start_date"):
        needs_excel = True
    if mode in ("heuristic",):
        entry = (config.get("heuristic") or {}).get("entry", "assembly_start")
        if entry in ("start_date", "performance_replay"):
            needs_excel = True
    if mode in ("eval", "compare"):
        if eval_mode in (None, 2):
            needs_excel = True
        if method_set & {"EXCEL", "ACTIONMASKING"}:
            needs_excel = True
        if eval_mode == 1 and method_set & {"EXCEL", "ACTIONMASKING"}:
            warnings.append("MODE 1에서는 엑셀/착수일은 실행되지 않습니다.")

    if mode != "gantt" and needs_excel:
        if not excel_path:
            errors.append("data.excel_path가 필요하지만 설정되지 않았습니다.")
        elif not os.path.exists(str(excel_path)):
            errors.append(f"data.excel_path 파일이 없습니다: {excel_path}")

    # RL 모델 경로 검증
    if "RL" in method_set or mode in ("eval", "compare"):
        model_cfg = config.get("model", {}) if isinstance(config, dict) else {}
        model_path = model_cfg.get("path")
        summary["model_path"] = model_path or ""
        if "RL" in method_set and model_path:
            if not os.path.exists(str(model_path)):
                errors.append(f"model.path 파일이 없습니다: {model_path}")
        elif "RL" in method_set and not model_path:
            warnings.append("model.path가 비어 있어 기본 RL 모델 경로를 사용합니다.")
        elif not method_set and mode in ("eval", "compare") and not model_path:
            warnings.append("평가 방법을 지정하지 않아 RL이 포함될 수 있습니다. model.path가 비어 있으면 기본 RL 경로를 사용합니다.")

    # 완화 순서 검증
    constraint_cfg = config.get("constraints", {}) if isinstance(config, dict) else {}
    relax_keys = _allowed_relax_keys()
    for key_name in ("relax_order_start_date", "relax_order_assembly"):
        items = constraint_cfg.get(key_name) or []
        summary[key_name] = items or []
        for raw in items:
            norm = _normalize_relax_key(raw)
            if not norm:
                continue
            if norm in relax_keys:
                continue
            if _looks_like_constraint_id(str(raw)):
                warnings.append(f"{key_name} 항목 '{raw}'는 제약 ID로 처리됩니다.")
            else:
                warnings.append(f"{key_name} 항목 '{raw}'는 인식되지 않는 키입니다.")

    summary["strict_rules"] = (constraint_cfg.get("strict_rules") or [])
    # [AGENT-EDIT] 날짜별 용량 오버라이드 활성 플래그 반영
    if constraint_cfg.get("enable_daily_block_cap_overrides"):
        summary["daily_block_cap_overrides"] = (constraint_cfg.get("daily_block_cap_overrides") or {})
    else:
        summary["daily_block_cap_overrides"] = {}
    if constraint_cfg.get("enable_daily_seam_cap_overrides"):
        summary["daily_seam_cap_overrides"] = (constraint_cfg.get("daily_seam_cap_overrides") or {})
    else:
        summary["daily_seam_cap_overrides"] = {}
    if constraint_cfg.get("enable_daily_seam_cap_scales"):
        summary["daily_seam_cap_scales"] = (constraint_cfg.get("daily_seam_cap_scales") or {})
    else:
        summary["daily_seam_cap_scales"] = {}

    calendar_cfg = config.get("calendar", {}) if isinstance(config, dict) else {}
    summary["holidays_off"] = calendar_cfg.get("holidays_off") or []
    summary["half_day_off"] = calendar_cfg.get("half_day_off") or []
    summary["enable_morning_shutdown"] = bool(calendar_cfg.get("enable_morning_shutdown"))
    summary["enable_half_day_off"] = bool(calendar_cfg.get("enable_half_day_off"))
    summary["enable_afternoon_shutdown"] = bool(calendar_cfg.get("enable_afternoon_shutdown"))
    summary["morning_shutdown_dates"] = calendar_cfg.get("morning_shutdown_dates") or []
    summary["morning_shutdown"] = calendar_cfg.get("morning_shutdown") if summary["enable_morning_shutdown"] else None
    summary["morning_shutdown_schedule"] = calendar_cfg.get("morning_shutdown_schedule") or {}
    summary["afternoon_shutdown"] = calendar_cfg.get("afternoon_shutdown") if (summary["enable_half_day_off"] or summary["enable_afternoon_shutdown"]) else None
    summary["afternoon_shutdown_schedule"] = calendar_cfg.get("afternoon_shutdown_schedule") or {}
    summary["lunch_break"] = calendar_cfg.get("lunch_break") if calendar_cfg.get("enable_lunch_break") else None

    return errors, warnings, summary


def print_summary(summary: dict) -> None:
    print("\n" + "=" * 60)
    print("[실행 요약]")
    print(f"- 모드: {summary.get('mode')}")
    if summary.get("mode") in ("eval", "compare"):
        print(f"- 평가 모드: {summary.get('eval_mode')}")
    if summary.get("excel_path"):
        print(f"- 데이터 경로: {summary.get('excel_path')} (시트: {summary.get('sheet') or '기본'})")
    if summary.get("methods"):
        print(f"- 평가 방법: {_summarize_list(summary.get('methods') or [])}")
    if summary.get("model_path"):
        print(f"- 모델 경로: {summary.get('model_path')}")
    if summary.get("gantt_search_dir"):
        print(f"- 간트차트 검색 경로: {summary.get('gantt_search_dir')}")
    print(f"- 완화 순서(착수일): {_summarize_list(summary.get('relax_order_start_date') or [])}")
    print(f"- 완화 순서(조립): {_summarize_list(summary.get('relax_order_assembly') or [])}")
    print(f"- 완화 금지: {_summarize_list(summary.get('strict_rules') or [])}")
    if summary.get("daily_block_cap_overrides"):
        print(f"- 일일 블록 제한: {summary.get('daily_block_cap_overrides')}")
    if summary.get("daily_seam_cap_overrides"):
        print(f"- 일일 심수 제한: {summary.get('daily_seam_cap_overrides')}")
    if summary.get("daily_seam_cap_scales"):
        print(f"- 일일 심수 배율: {summary.get('daily_seam_cap_scales')}")
    if summary.get("holidays_off"):
        print(f"- 휴무일: {_summarize_list(summary.get('holidays_off') or [])}")
    if summary.get("half_day_off"):
        print(f"- 반일 중지: {_summarize_list(summary.get('half_day_off') or [])}")
    if summary.get("enable_morning_shutdown") and summary.get("morning_shutdown"):
        print(f"- 오전 중지: {summary.get('morning_shutdown')} / 날짜: {_summarize_list(summary.get('morning_shutdown_dates') or [])}")
        if summary.get("morning_shutdown_schedule"):
            print(f"- 오전 날짜별 시간: {summary.get('morning_shutdown_schedule')}")
    if (summary.get("enable_half_day_off") or summary.get("enable_afternoon_shutdown")) and summary.get("afternoon_shutdown"):
        print(f"- 오후 중지: {summary.get('afternoon_shutdown')}")
        if summary.get("afternoon_shutdown_schedule"):
            print(f"- 오후 날짜별 시간: {summary.get('afternoon_shutdown_schedule')}")
    if summary.get("lunch_break"):
        print(f"- 점심 시간: {summary.get('lunch_break')}")
    print("=" * 60 + "\n")


def _run_module(module_name: str, extra_args: List[str]) -> None:
    """
    지정한 모듈을 __main__처럼 실행한다.

    - sys.argv 를 강제로 교체해서 "원본 스크립트가 직접 실행되는 것"처럼 보이게 만든다.
    - runpy.run_module(..., run_name="__main__") 로 실행하면,
      해당 모듈 안의 if __name__ == "__main__": 구문이 정상 동작한다.
    - 따라서 train_ppo_rollout.py / comprehensive_evaluation.py 의
      기존 CLI 파서를 그대로 활용할 수 있다.
    """
    original_argv = sys.argv[:]
    try:
        # 원본 스크립트가 받은 것처럼 argv를 세팅
        sys.argv = [module_name] + list(extra_args or [])
        # __main__으로 실행하여 내부 main() 로직을 그대로 수행
        runpy.run_module(module_name, run_name="__main__")
    finally:
        # 안전하게 원래 argv 복구 (다른 모듈 실행에 영향 방지)
        sys.argv = original_argv


def main() -> None:
    """
    현업용 통합 실행기.

    주요 포인트:
    - main.py 자체는 "모드 분기 + 설정 주입 + 원본 스크립트 실행"만 담당한다.
    - 실제 학습/평가/휴리스틱 로직은 기존 스크립트가 그대로 수행한다.
    - 설정(config.yaml/json)은 runtime_config에 저장되어
      캘린더 오버라이드, 완화 순서 등 환경 전역 옵션에 반영된다.
    """
    parser = argparse.ArgumentParser(description="PBS 통합 실행기")
    parser.add_argument(
        "mode",
        nargs="?",
        choices=["train", "eval", "compare", "heuristic", "replay", "replay_start_date", "gantt"],
        help="실행 모드",
    )
    parser.add_argument("--config", required=True, help="설정 파일 경로 (yaml/json)")
    parser.add_argument("--yes", action="store_true", help="확인 없이 바로 실행")
    # parse_known_args: main.py에서 모르는 옵션은 원본 스크립트로 그대로 넘긴다.
    args, unknown = parser.parse_known_args()
    # [AGENT-EDIT] "--" 구분자가 들어오면 내부 스크립트로 전달하지 않음
    if unknown and unknown[0] == "--":
        unknown = unknown[1:]

    # 1) 설정 파일 로드 (yaml/json)
    config = load_runtime_config(args.config)
    # 2) 런타임 설정 전역 저장 (Calendar/Relax 등 공통 옵션 반영)
    set_runtime_config(config)
    # [AGENT-ADD] config.yaml의 env 섹션을 환경변수로 반영
    if isinstance(config, dict):
        env_cfg = config.get("env") or {}
        if isinstance(env_cfg, dict):
            for key, value in env_cfg.items():
                if value is None:
                    continue
                os.environ[str(key)] = str(value)

    # 3) 실행 모드 결정
    #    - CLI mode가 우선
    #    - 없으면 config["mode"]
    #    - 둘 다 없으면 eval
    mode = args.mode or (config.get("mode") if isinstance(config, dict) else None) or "eval"

    # config 기반 추가 인자
    # - 예: config["train"]["cli_args"]에 ["--use_env_state"] 같은 값 넣기
    def _get_cli_args(section: str) -> List[str]:
        if not isinstance(config, dict):
            return []
        return [str(x) for x in (config.get(section, {}) or {}).get("cli_args", [])]

    # 4) 설정 검증 + 요약 출력 + 사용자 확인
    errors, warnings, summary = validate_config(mode, config if isinstance(config, dict) else {})
    print_summary(summary)
    if warnings:
        print("[경고]")
        for w in warnings:
            print(f"- {w}")
        print("")
    if errors:
        print("[오류] 아래 항목을 수정한 뒤 다시 실행하세요.")
        for e in errors:
            print(f"- {e}")
        sys.exit(1)

    confirm_flag = (config.get("confirm", True) if isinstance(config, dict) else True)
    if confirm_flag and not args.yes:
        try:
            answer = input("이대로 진행할까요? (y/n) ").strip().lower()
        except Exception:
            answer = "n"
        if answer not in ("y", "yes"):
            print("실행을 중단합니다.")
            return

    if mode == "train":
        # PPO 학습 실행
        _run_module("PPO.train.runner", _get_cli_args("train") + unknown)
        return

    if mode in ("eval", "compare"):
        # PPO 평가/비교 실행 (comprehensive_evaluation.py)
        # compare 모드도 현재는 동일 스크립트를 사용 (내부에서 비교 수행)
        _run_module("PPO.eval.runner", _get_cli_args("eval") + unknown)
        return

    if mode == "gantt":
        # 간트차트 생성 (최신 결과 폴더 또는 지정 경로)
        gantt_cfg = (config.get("gantt") if isinstance(config, dict) else {}) or {}
        search_dir = gantt_cfg.get("search_dir")
        # CLI로 search_dir를 넣고 싶으면 "--search_dir 경로" 형식 지원
        if unknown:
            if "--search_dir" in unknown:
                try:
                    idx = unknown.index("--search_dir")
                    search_dir = unknown[idx + 1]
                except Exception:
                    pass
            elif len(unknown) == 1:
                search_dir = unknown[0]
        from utils.gantt_chart_enhanced import generate_enhanced_gantt_charts
        generate_enhanced_gantt_charts(search_dir=search_dir)
        return

    if mode == "replay":
        # 실적 재현(엑셀 순번 기반)
        _run_module(
            "scheduling.performance_replay.excel_실적데이터_순번기반시퀀싱",
            _get_cli_args("replay") + unknown,
        )
        return

    if mode == "replay_start_date":
        # [AGENT-EDIT] 실적 재현 + 착수일 휴리스틱 연속 실행
        replay_cfg = (config.get("replay_start_date") if isinstance(config, dict) else {}) or {}
        excel_args = [str(x) for x in (replay_cfg.get("excel_cli_args") or _get_cli_args("replay"))]
        start_date_args = [str(x) for x in (replay_cfg.get("start_date_cli_args") or _get_cli_args("heuristic"))]
        _run_module(
            "scheduling.performance_replay.excel_실적데이터_순번기반시퀀싱",
            excel_args + unknown,
        )
        _run_module(
            "scheduling.start_date.action_sequence_착수일기준휴리스틱",
            start_date_args + unknown,
        )
        return

    if mode == "heuristic":
        # 휴리스틱만 실행 (조립착수일/착수일/실적재현 중 선택)
        heuristic_cfg = (config.get("heuristic") if isinstance(config, dict) else {}) or {}
        entry = heuristic_cfg.get("entry", "assembly_start")
        entry_map = {
            "assembly_start": "scheduling.assembly_start.action_sequence_조립착수일기준휴리스틱",
            "start_date": "scheduling.start_date.action_sequence_착수일기준휴리스틱",
            "performance_replay": "scheduling.performance_replay.excel_실적데이터_순번기반시퀀싱",
        }
        module_name = entry_map.get(entry, entry)
        _run_module(module_name, _get_cli_args("heuristic") + unknown)
        return

    raise ValueError(f"알 수 없는 mode: {mode}")


if __name__ == "__main__":
    # 직접 실행 시 main() 진입
    main()
