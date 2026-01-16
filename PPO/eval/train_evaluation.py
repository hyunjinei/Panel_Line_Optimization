#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
종합 평가 함수: RL vs Random vs SPT/LPT/SEAM 비교
"""

import numpy as np
import torch
import os
import sys
import random
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scheduling.assembly_start.rl_assembly_scheduler import run_rl_assembly_decoding_sequence_with_blocks
from scheduling.assembly_start.action_sequence_조립착수일기준휴리스틱 import run_assembly_decoding_sequence_with_blocks
from enhanced_environment.common.utils_core import DataConverter
from utils.optimized_block_generator import OptimizedBlockGenerator

def comprehensive_evaluation(trainer, device, generator, episode, csv_path=None, eval_dir=None):
    """
    RL vs Random vs SPT 종합 평가
    
    Args:
        eval_dir: 평가 결과를 저장할 기본 디렉토리
    
    Returns:
        dict: 평가 결과 통계
    """
    print(f"\n📊 종합 평가 (Episode {episode})")
    
    # 🔥 Random Seed 고정으로 재현성 보장
    evaluation_seed = episode * 1000  # 에피소드별 고유 시드
    random.seed(evaluation_seed)
    np.random.seed(evaluation_seed)
    torch.manual_seed(evaluation_seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed(evaluation_seed)
    print("="*60)
    
    # 평가 세션별 폴더 생성
    import json
    if eval_dir:
        session_dir = os.path.join(eval_dir, str(episode))
        os.makedirs(session_dir, exist_ok=True)
    
    eval_results = []
    rl_wins = 0
    random_wins = 0
    spt_wins = 0
    lpt_wins = 0
    seam_wins = 0
    
    # 1. SNU 데이터 평가 (1개)
    print("📌 SNU 데이터셋 평가")
    snu_blocks, snu_metadata = DataConverter.excel_to_blocks_with_metadata(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 
                    'environment', '판넬 블록 데이터셋_250618_SNU.xlsx')
    )
    
    # 🔥 동적 시작일 계산 (integrated_learning_and_scheduling.py와 동일)
    min_assembly_date = min(block.max_start_date for block in snu_blocks)
    snu_start_date = min_assembly_date.strftime("%Y-%m-%d")
    print(f"   📅 SNU 동적 시작일: {snu_start_date}")
    
    # RL 평가 - Sampling 10번
    trainer.actor.eval()
    trainer.actor.default_decode_type = "sampling"  # Sampling 모드 설정
    
    print("  🎲 RL Sampling (10회): ", end="", flush=True)
    rl_makespans = []
    rl_stats_list = []
    for sample_idx in range(10):
        with torch.no_grad():
            _, sample_stats, _, _ = run_rl_assembly_decoding_sequence_with_blocks(
                blocks=snu_blocks,
                metadata=snu_metadata,
                rl_agent=trainer.actor,
                device=device,
                max_days=20,
                start_date=snu_start_date,
                training_mode=False,
                save_csv=False
            )
        rl_makespans.append(sample_stats.get('makespan_hours', float('inf')))
        rl_stats_list.append(sample_stats)
        print(".", end="", flush=True)
    print(" 완료!")

    # Best (Min) 선택
    best_rl_idx = int(np.argmin(rl_makespans))
    rl_makespan = rl_makespans[best_rl_idx]
    rl_best_stats = rl_stats_list[best_rl_idx]
    rl_best_viol = rl_best_stats.get('total_violations_train', rl_best_stats.get('total_violations', 0))
    rl_stats = {
        'makespan_hours': rl_makespan,
        'samples': rl_makespans,
        'violation_samples': [s.get('total_violations', 0) for s in rl_stats_list],
        'violation_train_samples': [s.get('total_violations_train', s.get('total_violations', 0)) for s in rl_stats_list],
        'min_violations': rl_best_viol,
        'total_violations': rl_best_stats.get('total_violations', 0),
        'total_cseam_violations': rl_best_stats.get('total_cseam_violations', 0),
        'total_violations_train': rl_best_viol
    }
    
    # Random 휴리스틱 - Sampling 10번
    print("  🎲 Random Sampling (10회): ", end="", flush=True)
    random_makespans = []
    random_stats_list = []
    for sample_idx in range(10):
        _, random_sample_stats = run_assembly_decoding_sequence_with_blocks(
            blocks=snu_blocks,
            metadata=snu_metadata,
            decoding_type="assembly",
            selection_method="random",  # heuristic_type 대신 selection_method
            max_days=20,
            start_date=snu_start_date,
            save_csv=False,
            save_detailed=False
        )
        random_makespans.append(random_sample_stats.get('makespan_hours', float('inf')))
        random_stats_list.append(random_sample_stats)
        print(".", end="", flush=True)
    print(" 완료!")
    
    # Best (Min) 선택
    best_random_idx = int(np.argmin(random_makespans))
    random_makespan = random_makespans[best_random_idx]
    random_best_stats = random_stats_list[best_random_idx]
    random_best_viol = random_best_stats.get('total_violations_train', random_best_stats.get('total_violations', 0))
    random_stats = {
        'makespan_hours': random_makespan,
        'samples': random_makespans,
        'violation_samples': [s.get('total_violations', 0) for s in random_stats_list],
        'violation_train_samples': [s.get('total_violations_train', s.get('total_violations', 0)) for s in random_stats_list],
        'min_violations': random_best_viol,
        'total_violations': random_best_stats.get('total_violations', 0),
        'total_cseam_violations': random_best_stats.get('total_cseam_violations', 0),
        'total_violations_train': random_best_viol
    }
    
    # SPT 휴리스틱 (deterministic)
    _, spt_stats = run_assembly_decoding_sequence_with_blocks(
        blocks=snu_blocks,
        metadata=snu_metadata,
        decoding_type="assembly",
        selection_method="spt",
        max_days=20,
        start_date=snu_start_date,
        save_csv=False,
        save_detailed=False
    )
    spt_makespan = spt_stats.get('makespan_hours', float('inf'))
    spt_makespans = [spt_makespan]

    # LPT 휴리스틱 (deterministic)
    _, lpt_stats = run_assembly_decoding_sequence_with_blocks(
        blocks=snu_blocks,
        metadata=snu_metadata,
        decoding_type="assembly",
        selection_method="lpt",
        max_days=20,
        start_date=snu_start_date,
        save_csv=False,
        save_detailed=False
    )
    lpt_makespan = lpt_stats.get('makespan_hours', float('inf'))
    lpt_makespans = [lpt_makespan]

    # SEAM_MIN 휴리스틱 (deterministic)
    _, seam_stats = run_assembly_decoding_sequence_with_blocks(
        blocks=snu_blocks,
        metadata=snu_metadata,
        decoding_type="assembly",
        selection_method="seam_min",
        max_days=20,
        start_date=snu_start_date,
        save_csv=False,
        save_detailed=False
    )
    seam_makespan = seam_stats.get('makespan_hours', float('inf'))
    seam_makespans = [seam_makespan]
    
    # 샘플링 통계
    rl_avg = np.mean(rl_makespans)
    rl_std = np.std(rl_makespans)
    random_avg = np.mean(random_makespans)
    random_std = np.std(random_makespans)
    spt_avg = np.mean(spt_makespans)
    spt_std = np.std(spt_makespans)
    lpt_avg = np.mean(lpt_makespans)
    lpt_std = np.std(lpt_makespans)
    seam_avg = np.mean(seam_makespans)
    seam_std = np.std(seam_makespans)
    
    def format_result(makespan, stats):
        # stats가 dict가 아닐 수도 있으므로 방어적으로 처리
        if isinstance(stats, dict):
            total = stats.get('total_violations', 0)
            cseam = stats.get('total_cseam_violations', 0)
            train_v = stats.get('total_violations_train', total - cseam)
        else:
            total = stats if stats is not None else 0
            cseam = 0
            train_v = total
        return f"{makespan:.1f}h/{train_v}v(train) total={total} cseam={cseam}"

    spt_viols_stats = {
        'total_violations': spt_stats.get('total_violations', 0),
        'total_cseam_violations': spt_stats.get('total_cseam_violations', 0),
        'total_violations_train': spt_stats.get('total_violations_train', spt_stats.get('total_violations', 0) - spt_stats.get('total_cseam_violations', 0))
    }
    lpt_viols_stats = {
        'total_violations': lpt_stats.get('total_violations', 0),
        'total_cseam_violations': lpt_stats.get('total_cseam_violations', 0),
        'total_violations_train': lpt_stats.get('total_violations_train', lpt_stats.get('total_violations', 0) - lpt_stats.get('total_cseam_violations', 0))
    }
    seam_viols_stats = {
        'total_violations': seam_stats.get('total_violations', 0),
        'total_cseam_violations': seam_stats.get('total_cseam_violations', 0),
        'total_violations_train': seam_stats.get('total_violations_train', seam_stats.get('total_violations', 0) - seam_stats.get('total_cseam_violations', 0))
    }

    print(
        f"  SNU: RL={format_result(rl_makespan, rl_stats)} (avg:{rl_avg:.1f}±{rl_std:.1f}), "
        f"Random={format_result(random_makespan, random_stats)} (avg:{random_avg:.1f}±{random_std:.1f}), "
        f"SPT={format_result(spt_makespan, spt_viols_stats)}, "
        f"LPT={format_result(lpt_makespan, lpt_viols_stats)}, "
        f"SEAM={format_result(seam_makespan, seam_viols_stats)}"
    )
    
    # [AGENT-EDIT] Winner 판정: makespan 동률이면 위반 적은 쪽 우선
    candidates = {
        'RL': (rl_makespan, rl_stats.get('total_violations', 0)),
        'Random': (random_makespan, random_stats.get('total_violations', 0)),
        'SPT': (spt_makespan, spt_viols_stats.get('total_violations', 0)),
        'LPT': (lpt_makespan, lpt_viols_stats.get('total_violations', 0)),
        'SEAM': (seam_makespan, seam_viols_stats.get('total_violations', 0))
    }
    # 정렬: makespan 오름차순, violations 오름차순
    sorted_candidates = sorted(candidates.items(), key=lambda x: (x[1][0], x[1][1]))
    winner = sorted_candidates[0][0] if sorted_candidates else ""
    if winner == "RL":
        rl_wins += 1
        print(f"  ✅ RL Win!")
    elif winner == "Random":
        random_wins += 1
        print(f"  🎲 Random Win!")
    elif winner == "SPT":
        spt_wins += 1
        print(f"  ⏱️ SPT Win!")
    elif winner == "LPT":
        lpt_wins += 1
        print(f"  🧮 LPT Win!")
    elif winner == "SEAM":
        seam_wins += 1
        print(f"  🌊 SEAM Win!")
    
    snu_result = {
        'problem': 'SNU',
        'rl': rl_makespan,
        'random': random_makespan,
        'spt': spt_makespan,
        'lpt': lpt_makespan,
        'seam': seam_makespan,
        'winner': winner,
        'num_blocks': len(snu_blocks)
    }
    eval_results.append(snu_result)
    
    # SNU 결과를 폴더에 저장
    if eval_dir:
        snu_dir = os.path.join(session_dir, 'snu')
        os.makedirs(snu_dir, exist_ok=True)
        
        # 결과 JSON 저장
        with open(os.path.join(snu_dir, 'result.json'), 'w') as f:
            json.dump({
                'episode': episode,
                'problem_type': 'SNU',
                'num_blocks': len(snu_blocks),
                'results': {
                    'rl_makespan': rl_makespan,
                    'random_makespan': random_makespan,
                    'spt_makespan': spt_makespan,
                    'lpt_makespan': lpt_makespan,
                    'seam_makespan': seam_makespan,
                    'winner': winner
                },
                'rl_stats': rl_stats,
                'random_stats': random_stats,
                'spt_stats': spt_stats,
                'lpt_stats': lpt_stats,
                'seam_stats': seam_stats
            }, f, indent=2)
    
    # [AGENT-EDIT] 랜덤 생성 데이터 평가 개수 상향
    num_random_problems = 10
    print(f"\n📌 랜덤 생성 데이터셋 평가 ({num_random_problems}개)")
    for i in range(num_random_problems):
        # 랜덤 블록 생성
        test_blocks_data = generator.generate_blocks_with_ps_pairs_configurable(
            total_blocks=70,
            ps_pairs_count=None,
            subassembly_groups=None
        )
        test_blocks, test_metadata = DataConverter.dataframe_to_blocks_with_metadata(test_blocks_data)
        
        # 🔥 동적 시작일 계산 (각 랜덤 데이터별로)
        test_min_assembly_date = min(block.max_start_date for block in test_blocks)
        test_start_date = test_min_assembly_date.strftime("%Y-%m-%d")
        
        # RL 평가 - Sampling 10번
        print(f"    [Test{i+1}] RL Sampling: ", end="", flush=True)
        rl_makespans_test = []
        rl_violations_test = []
        for sample_idx in range(10):
            with torch.no_grad():
                _, sample_stats, _, _ = run_rl_assembly_decoding_sequence_with_blocks(
                    blocks=test_blocks,
                    metadata=test_metadata,
                    rl_agent=trainer.actor,
                    device=device,
                    max_days=10,
                    start_date=test_start_date,
                    training_mode=False,
                    save_csv=False
                )
            rl_makespans_test.append(sample_stats.get('makespan_hours', float('inf')))
            rl_violations_test.append(sample_stats.get('total_violations', 0))
            print(".", end="", flush=True)
        print(" ", end="")
        
        # Best (Min) 선택
        best_rl_idx = int(np.argmin(rl_makespans_test))
        rl_makespan = rl_makespans_test[best_rl_idx]
        rl_viols_best = rl_violations_test[best_rl_idx]
        rl_stats = {
            'makespan_hours': rl_makespan,
            'samples': rl_makespans_test,
            'violation_samples': rl_violations_test,
            'min_violations': rl_viols_best
        }
        
        # Random 휴리스틱 (10번 샘플링으로 공정하게)
        random_makespans_test = []
        random_violations_test = []
        for sample_idx in range(10):
            _, random_sample_stats = run_assembly_decoding_sequence_with_blocks(
                blocks=test_blocks,
                metadata=test_metadata,
                decoding_type="assembly",
                selection_method="random",
                max_days=20,
                start_date=test_start_date,  # 🔥 RL과 동일한 start_date 사용
                save_csv=False,
                save_detailed=False
            )
            random_makespans_test.append(random_sample_stats.get('makespan_hours', float('inf')))
            random_violations_test.append(random_sample_stats.get('total_violations', 0))
        
        # Best (Min) 선택 - RL과 동일한 조건
        best_rand_idx = int(np.argmin(random_makespans_test))
        random_makespan = random_makespans_test[best_rand_idx]
        random_viols_best = random_violations_test[best_rand_idx]
        random_stats = {
            'makespan_hours': random_makespan,
            'samples': random_makespans_test,
            'violation_samples': random_violations_test,
            'min_violations': random_viols_best
        }
        
        # SPT 휴리스틱 (deterministic이지만 동일한 start_date 사용)
        _, spt_stats = run_assembly_decoding_sequence_with_blocks(
            blocks=test_blocks,
            metadata=test_metadata,
            decoding_type="assembly",
            selection_method="spt",
            max_days=10,
            start_date=test_start_date,  # 🔥 RL과 동일한 start_date 사용
            save_csv=False,
            save_detailed=False
        )
        spt_makespan = spt_stats.get('makespan_hours', float('inf'))

        # LPT 휴리스틱
        _, lpt_stats = run_assembly_decoding_sequence_with_blocks(
            blocks=test_blocks,
            metadata=test_metadata,
            decoding_type="assembly",
            selection_method="lpt",
            max_days=10,
            start_date=test_start_date,
            save_csv=False,
            save_detailed=False
        )
        lpt_makespan = lpt_stats.get('makespan_hours', float('inf'))

        # SEAM_MIN 휴리스틱
        _, seam_stats = run_assembly_decoding_sequence_with_blocks(
            blocks=test_blocks,
            metadata=test_metadata,
            decoding_type="assembly",
            selection_method="seam_min",
            max_days=10,
            start_date=test_start_date,
            save_csv=False,
            save_detailed=False
        )
        seam_makespan = seam_stats.get('makespan_hours', float('inf'))
        
        # 샘플링 통계 (Random도 포함)
        rl_test_avg = np.mean(rl_makespans_test)
        rl_test_std = np.std(rl_makespans_test)
        random_test_avg = np.mean(random_makespans_test)
        random_test_std = np.std(random_makespans_test)
        spt_viols = spt_stats.get('total_violations', 0)
        lpt_viols = lpt_stats.get('total_violations', 0)
        seam_viols = seam_stats.get('total_violations', 0)
        print(
            f"RL={format_result(rl_makespan, rl_viols_best)} (avg:{rl_test_avg:.1f}±{rl_test_std:.1f}), "
            f"Random={format_result(random_makespan, random_viols_best)} (avg:{random_test_avg:.1f}±{random_test_std:.1f}), "
            f"SPT={format_result(spt_makespan, spt_viols)}, "
            f"LPT={format_result(lpt_makespan, lpt_viols)}, "
            f"SEAM={format_result(seam_makespan, seam_viols)}",
            end=""
        )
        
        # [AGENT-EDIT] Winner 판정: makespan 동률이면 위반 적은 쪽 우선
        candidates = {
            'RL': (rl_makespan, rl_viols_best),
            'Random': (random_makespan, random_viols_best),
            'SPT': (spt_makespan, spt_viols),
            'LPT': (lpt_makespan, lpt_viols),
            'SEAM': (seam_makespan, seam_viols)
        }
        sorted_candidates = sorted(candidates.items(), key=lambda x: (x[1][0], x[1][1]))
        winner = sorted_candidates[0][0] if sorted_candidates else ""
        if winner == "RL":
            rl_wins += 1
            print(f" ✅ RL Win!")
        elif winner == "Random":
            random_wins += 1
            print(f" 🎲 Random Win!")
        elif winner == "SPT":
            spt_wins += 1
            print(f" ⏱️ SPT Win!")
        elif winner == "LPT":
            lpt_wins += 1
            print(f" 🧮 LPT Win!")
        elif winner == "SEAM":
            seam_wins += 1
            print(f" 🌊 SEAM Win!")
        else:
            print()
        
        problem_result = {
            'problem': f'Random{i+1}',
            'rl': rl_makespan,
            'random': random_makespan,
            'spt': spt_makespan,
            'lpt': lpt_makespan,
            'seam': seam_makespan,
            'winner': winner,
            'num_blocks': len(test_blocks)
        }
        eval_results.append(problem_result)
        
        # 각 문제 결과를 폴더에 저장
        if eval_dir:
            problem_dir = os.path.join(session_dir, f'problem{i+1}')
            os.makedirs(problem_dir, exist_ok=True)
            
            # 결과 JSON 저장
            with open(os.path.join(problem_dir, 'result.json'), 'w') as f:
                json.dump({
                    'episode': episode,
                    'problem_type': f'Random{i+1}',
                    'num_blocks': len(test_blocks),
                    'results': {
                        'rl_makespan': rl_makespan,
                        'random_makespan': random_makespan,
                        'spt_makespan': spt_makespan,
                        'lpt_makespan': lpt_makespan,
                        'seam_makespan': seam_makespan,
                        'winner': winner
                    },
                    'rl_stats': rl_stats,
                    'random_stats': random_stats,
                    'spt_stats': spt_stats,
                    'lpt_stats': lpt_stats,
                    'seam_stats': seam_stats
                }, f, indent=2)
            
            # 블록 데이터도 저장 (재현 가능하도록)
            test_blocks_data.to_csv(os.path.join(problem_dir, 'blocks.csv'), index=False)
    
    # 통계 계산
    rl_makespans = [r['rl'] for r in eval_results]
    random_makespans = [r['random'] for r in eval_results]
    spt_makespans = [r['spt'] for r in eval_results]
    lpt_makespans = [r['lpt'] for r in eval_results]
    seam_makespans = [r['seam'] for r in eval_results]

    avg_rl = np.mean(rl_makespans)
    avg_random = np.mean(random_makespans)
    avg_spt = np.mean(spt_makespans)
    avg_lpt = np.mean(lpt_makespans)
    avg_seam = np.mean(seam_makespans)
    
    print("\n" + "="*60)
    print(f"📈 평가 요약 (10개 문제)")
    print(f"  평균 Makespan:")
    print(f"    - RL:     {avg_rl:.2f}h (±{np.std(rl_makespans):.2f})")
    print(f"    - Random: {avg_random:.2f}h (±{np.std(random_makespans):.2f})")
    print(f"    - SPT:    {avg_spt:.2f}h (±{np.std(spt_makespans):.2f})")
    print(f"    - LPT:    {avg_lpt:.2f}h (±{np.std(lpt_makespans):.2f})")
    print(f"    - SEAM:   {avg_seam:.2f}h (±{np.std(seam_makespans):.2f})")
    print(f"\n  🏆 Win Count:")
    total_problems = 1 + num_random_problems
    print(f"    - RL:     {rl_wins}/{total_problems}")
    print(f"    - Random: {random_wins}/{total_problems}")
    print(f"    - SPT:    {spt_wins}/{total_problems}")
    print(f"    - LPT:    {lpt_wins}/{total_problems}")
    print(f"    - SEAM:   {seam_wins}/{total_problems}")
    
    # 개선율 계산
    improvement_vs_random = (avg_random - avg_rl) / avg_random * 100
    improvement_vs_spt = (avg_spt - avg_rl) / avg_spt * 100
    improvement_vs_lpt = (avg_lpt - avg_rl) / avg_lpt * 100
    improvement_vs_seam = (avg_seam - avg_rl) / avg_seam * 100
    print(f"\n  📊 개선율:")
    print(f"    - vs Random: {improvement_vs_random:+.1f}%")
    print(f"    - vs SPT:    {improvement_vs_spt:+.1f}%")
    print(f"    - vs LPT:    {improvement_vs_lpt:+.1f}%")
    print(f"    - vs SEAM:   {improvement_vs_seam:+.1f}%")
    print("="*60)
    
    # 전체 요약을 세션 폴더에 저장
    if eval_dir:
        summary_path = os.path.join(session_dir, 'summary.json')
        with open(summary_path, 'w') as f:
            json.dump({
                'episode': episode,
                'total_problems': 10,
                'average_makespans': {
                    'rl': avg_rl,
                    'random': avg_random,
                    'spt': avg_spt,
                    'lpt': avg_lpt,
                    'seam': avg_seam
                },
                'std_makespans': {
                    'rl': np.std(rl_makespans),
                    'random': np.std(random_makespans),
                    'spt': np.std(spt_makespans),
                    'lpt': np.std(lpt_makespans),
                    'seam': np.std(seam_makespans)
                },
                'win_counts': {
                    'rl': rl_wins,
                    'random': random_wins,
                    'spt': spt_wins,
                    'lpt': lpt_wins,
                    'seam': seam_wins
                },
                'improvements': {
                    'vs_random': improvement_vs_random,
                    'vs_spt': improvement_vs_spt,
                    'vs_lpt': improvement_vs_lpt,
                    'vs_seam': improvement_vs_seam
                },
                'all_results': eval_results
            }, f, indent=2)
        print(f"\n📁 평가 결과 저장: {session_dir}")
    
    # 🔥 SNU 데이터 상세 성능 CSV 저장 (세션 폴더에 누적)
    if eval_dir:
        # 🔥 세션 폴더 (0826_20_52) 바로 아래에 저장
        session_base_dir = os.path.dirname(session_dir)  # episode 폴더의 부모 (0826_20_52)
        snu_detail_csv_path = os.path.join(session_base_dir, 'snu_algorithm_performance.csv')
        
        # 헤더 작성 (첫 번째 에피소드인 경우)
        write_header = not os.path.exists(snu_detail_csv_path)
        
        import csv
        def _pad_samples(values, target_len=10):
            padded = list(values)[:target_len]
            if len(padded) < target_len:
                padded += ["" for _ in range(target_len - len(padded))]
            return padded
        with open(snu_detail_csv_path, 'a', newline='', encoding='utf-8-sig') as f:
            writer = csv.writer(f)
            
            if write_header:
                writer.writerow([
                    'episode', 'algorithm', 'min_makespan', 'max_makespan', 'mean_makespan', 'std_makespan',
                    'sample_1', 'sample_2', 'sample_3', 'sample_4', 'sample_5',
                    'sample_6', 'sample_7', 'sample_8', 'sample_9', 'sample_10'
                ])
            
            # RL 데이터
            writer.writerow([
                episode, 'RL', 
                min(rl_makespans), max(rl_makespans), rl_avg, rl_std
            ] + _pad_samples(rl_makespans))
            
            # Random 데이터
            writer.writerow([
                episode, 'Random',
                min(random_makespans), max(random_makespans), random_avg, random_std
            ] + _pad_samples(random_makespans))
            
            # SPT 데이터
            writer.writerow([
                episode, 'SPT',
                min(spt_makespans), max(spt_makespans), spt_avg, spt_std
            ] + _pad_samples(spt_makespans))
            # LPT 데이터
            writer.writerow([
                episode, 'LPT',
                min(lpt_makespans), max(lpt_makespans), lpt_avg, lpt_std
            ] + _pad_samples(lpt_makespans))
            # SEAM 데이터
            writer.writerow([
                episode, 'SEAM',
                min(seam_makespans), max(seam_makespans), seam_avg, seam_std
            ] + _pad_samples(seam_makespans))
        
        print(f"📊 SNU 상세 성능 저장: {snu_detail_csv_path}")
    
    # 🔥 평가 완료 후 train 모드로 복구
    trainer.actor.train()
    trainer.actor.default_decode_type = "sampling"  # 원래 샘플링 모드로 복구
    
    # CSV 저장
    if csv_path:
        import csv
        with open(csv_path, 'a', newline='') as f:
            writer = csv.writer(f)
            writer.writerow([
                episode,
                avg_rl,
                np.std(rl_makespans),
                np.min(rl_makespans),
                np.max(rl_makespans),
                avg_random,
                np.std(random_makespans),
                avg_spt,
                np.std(spt_makespans),
                avg_lpt,
                np.std(lpt_makespans),
                avg_seam,
                np.std(seam_makespans),
                rl_wins,
                random_wins,
                spt_wins,
                lpt_wins,
                seam_wins,
                improvement_vs_random,
                improvement_vs_spt,
                improvement_vs_lpt,
                improvement_vs_seam
            ])

    return {
        'avg_rl': avg_rl,
        'avg_random': avg_random,
        'avg_spt': avg_spt,
        'avg_lpt': avg_lpt,
        'avg_seam': avg_seam,
        'rl_wins': rl_wins,
        'random_wins': random_wins,
        'spt_wins': spt_wins,
        'lpt_wins': lpt_wins,
        'seam_wins': seam_wins,
        'improvement_vs_random': improvement_vs_random,
        'improvement_vs_spt': improvement_vs_spt,
        'improvement_vs_lpt': improvement_vs_lpt,
        'improvement_vs_seam': improvement_vs_seam,
        'all_results': eval_results
    }
