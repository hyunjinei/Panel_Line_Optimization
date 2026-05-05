# -*- coding: utf-8 -*-
"""Self-label selection strategy tests."""

# [AGENT-ADD] self-label teacher 선택 기준과 LPT warmup 정책을 고정한다.

import torch
import runtime_config

from PPO.train.assembly_rollout import AssemblyPPORollout


def _build_trainer(strategy="primary_first", policy="candidate_only", warmup=0, profile_expansion=True, heuristic_expansion=True):
    return AssemblyPPORollout(
        actor_params={
            "embedding_dim": 16,
            "hidden_dim": 16,
            "n_layers": 1,
            "n_heads": 2,
            "dropout": 0.0,
            "use_logit_clipping": True,
            "C": 10.0,
            "T": 1.0,
            "feature_dim": 10,
            "feature_mode": "reduced",
            "use_positional_encoding": False,
            "env_state_dim": 4,
            "use_norm": False,
        },
        device=torch.device("cpu"),
        enable_optimizer=False,
        use_block_generator=False,
        mode="self_label",
        self_label_samples=4,
        self_label_mode=2,
        self_label_selection_strategy=strategy,
        self_label_lpt_teacher_policy=policy,
        self_label_lpt_warmup_episodes=warmup,
        self_label_profile_expansion=profile_expansion,
        self_label_heuristic_profile_expansion=heuristic_expansion,
        self_label_print_best_updates=False,
    )


def _record(tag, makespan, violations):
    return {
        "tag": tag,
        "stats": {
            "makespan_hours": makespan,
            "total_violations_primary": violations,
        },
        "schedule": [],
        "episode_data": [],
        "env": None,
    }


def test_legacy_score_keeps_existing_weighted_behavior():
    trainer = _build_trainer(strategy="legacy_score")
    lower_score = _record("lower_score", 90.0, 2)
    zero_violation_but_slower = _record("zero_vio", 93.0, 0)
    best = trainer._select_best_record([lower_score, zero_violation_but_slower])
    assert best["tag"] == "lower_score"


def test_primary_first_prefers_fewer_violations_before_makespan():
    trainer = _build_trainer(strategy="primary_first")
    lower_score = _record("lower_score", 90.0, 2)
    zero_violation_but_slower = _record("zero_vio", 93.0, 0)
    best = trainer._select_best_record([lower_score, zero_violation_but_slower])
    assert best["tag"] == "zero_vio"

def test_default_self_label_policy_is_primary_first_candidate_only():
    trainer = _build_trainer()
    assert trainer.self_label_selection_strategy == "primary_first"
    assert trainer.self_label_lpt_teacher_policy == "candidate_only"
    assert trainer.self_label_samples == 4


def test_candidate_only_lpt_competes_only_through_record_sorting():
    trainer = _build_trainer(strategy="primary_first", policy="candidate_only")
    actor_best = _record("actor", 90.0, 0)
    lpt = _record("lpt", 80.0, 2)
    assert trainer._select_best_record([actor_best, lpt])["tag"] == "actor"
    assert trainer._should_use_lpt_teacher(actor_best, lpt) is False


def test_feasible_first_prefers_zero_violation_group_then_makespan():
    trainer = _build_trainer(strategy="feasible_first")
    infeasible_fast = _record("infeasible_fast", 80.0, 3)
    feasible_slow = _record("feasible_slow", 95.0, 0)
    feasible_faster = _record("feasible_faster", 94.0, 0)
    best = trainer._select_best_record([infeasible_fast, feasible_slow, feasible_faster])
    assert best["tag"] == "feasible_faster"


def test_warmup_only_lpt_teacher_is_used_before_warmup_end():
    trainer = _build_trainer(strategy="primary_first", policy="warmup_only", warmup=1000)
    actor_best = _record("actor", 95.0, 1)
    lpt = _record("lpt", 94.0, 0)
    trainer.episode_count = 500
    assert trainer._should_use_lpt_teacher(actor_best, lpt) is True


def test_warmup_only_lpt_teacher_is_disabled_after_warmup_end():
    trainer = _build_trainer(strategy="primary_first", policy="warmup_only", warmup=1000)
    actor_best = _record("actor", 95.0, 1)
    lpt = _record("lpt", 94.0, 0)
    trainer.episode_count = 1000
    assert trainer._should_use_lpt_teacher(actor_best, lpt) is False


def test_disabled_lpt_teacher_never_overrides_actor_best():
    trainer = _build_trainer(strategy="primary_first", policy="disabled")
    actor_best = _record("actor", 95.0, 1)
    lpt = _record("lpt", 94.0, 0)
    assert trainer._should_use_lpt_teacher(actor_best, lpt) is False


def test_lpt_teacher_bias_override_builds_bias_on_runtime_config(monkeypatch):
    monkeypatch.setattr(
        'runtime_config._RUNTIME_CONFIG',
        {'constraints': {
            'enable_workshop_head_masking': False,
            'enable_assembly_start_leadtime_layers': False,
            'enable_assembly_start_window_filter': False,
        }},
    )
    trainer = _build_trainer(strategy="primary_first")
    trainer.self_label_lpt_teacher_bias_on = True
    cfg = trainer._build_lpt_teacher_runtime_config()
    constraints = cfg.get('constraints', {})
    assert constraints.get('enable_workshop_head_masking') is True
    assert constraints.get('enable_assembly_start_leadtime_layers') is True
    assert constraints.get('enable_assembly_start_window_filter') is True

def test_self_label_episode_expands_profiles_for_rl_lpt_and_seam(monkeypatch):
    trainer = _build_trainer(strategy="primary_first", policy="candidate_only")
    sampled_tags = []
    heuristic_methods = []

    def fake_sample_schedule(model, blocks, metadata, start_date, training_mode, tag, forced_sequence=None):
        sampled_tags.append(tag)
        if forced_sequence is not None:
            return _record(tag, 100.0, 0)
        return _record(tag, 80.0, 1)

    def fake_heuristic(blocks, metadata, start_date, method):
        heuristic_methods.append(method)
        return ([{"block_id": 1}, {"block_id": 2}], {"makespan_hours": 100.0})

    monkeypatch.setattr(trainer, "_compute_episode_start_date", lambda blocks: "2024-01-01")
    monkeypatch.setattr(trainer, "_sample_schedule", fake_sample_schedule)
    monkeypatch.setattr(trainer, "_generate_self_label_heuristic_results", fake_heuristic)
    monkeypatch.setattr(trainer, "_teacher_forcing_update", lambda episode_data: 0.0)
    monkeypatch.setattr(trainer, "_build_episode_diagnostics", lambda **kwargs: {})

    stats = trainer.train_episode_self_label([], {})

    rl_tags = [tag for tag in sampled_tags if tag.startswith("selflabel_rl_")]
    lpt_tags = [tag for tag in sampled_tags if tag.startswith("selflabel_lpt_")]
    seam_tags = [tag for tag in sampled_tags if tag.startswith("selflabel_seam_min_")]
    assert len(rl_tags) == 8 * 8 * 4
    assert len(lpt_tags) == 8
    assert len(seam_tags) == 8
    assert len(sampled_tags) == 272
    assert heuristic_methods.count("lpt") == 8
    assert heuristic_methods.count("seam_min") == 8
    assert stats["diagnostics"]["candidate_count"] == 272
    assert stats["diagnostics"]["profile_expansion"]["expected_total"] == 272
    assert stats["update_source"] == "lpt"
    assert stats["violations"] == 0

