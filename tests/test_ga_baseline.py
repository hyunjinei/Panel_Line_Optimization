import random

from PPO.eval.methods import _ga_mutate_swap, _ga_order_crossover, _ga_score_from_stats


def test_ga_order_crossover_preserves_permutation():
    parent_a = [1, 2, 3, 4, 5, 6]
    parent_b = [6, 5, 4, 3, 2, 1]
    child = _ga_order_crossover(parent_a, parent_b, random.Random(42))

    assert sorted(child) == sorted(parent_a)
    assert len(child) == len(parent_a)


def test_ga_mutate_swap_preserves_permutation():
    sequence = [1, 2, 3, 4]
    mutated = _ga_mutate_swap(sequence, random.Random(42), mutation_rate=1.0)

    assert sorted(mutated) == sorted(sequence)
    assert mutated != sequence


def test_ga_score_prioritizes_primary_violation_before_makespan():
    fewer_violations = _ga_score_from_stats({
        "total_blocks_processed": 4,
        "total_blocks_expected": 4,
        "total_violations_primary": 1,
        "makespan_hours": 100.0,
    })
    shorter_but_more_violations = _ga_score_from_stats({
        "total_blocks_processed": 4,
        "total_blocks_expected": 4,
        "total_violations_primary": 2,
        "makespan_hours": 1.0,
    })

    assert fewer_violations < shorter_but_more_violations
