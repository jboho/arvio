import pytest

from arvio.stats import bootstrap_kappa, kappa, wilson


def test_wilson_with_no_data_rules_nothing_out():
    assert wilson(0, 0) == (0.0, 1.0)


def test_wilson_matches_the_known_value():
    low, high = wilson(8, 10)
    assert low == pytest.approx(0.4902, abs=1e-4)
    assert high == pytest.approx(0.9433, abs=1e-4)


def test_wilson_stays_in_range_at_the_edges():
    assert wilson(10, 10)[1] == 1.0
    assert wilson(0, 10)[0] == 0.0
    assert wilson(10, 10)[0] < 1.0


def two_by_two(both_yes, human_only, judge_only, both_no):
    return (
        [("y", "y")] * both_yes
        + [("y", "n")] * human_only
        + [("n", "y")] * judge_only
        + [("n", "n")] * both_no
    )


def test_kappa_matches_a_hand_computed_table():
    # observed 0.7, chance 0.5 -> (0.7 - 0.5) / 0.5
    assert kappa(two_by_two(20, 5, 10, 15)) == pytest.approx(0.4)


def test_kappa_perfect_agreement():
    assert kappa(two_by_two(5, 0, 0, 5)) == pytest.approx(1.0)


def test_kappa_is_none_when_the_judge_used_one_class():
    assert kappa([("y", "y"), ("n", "y"), ("y", "y")]) is None
    assert kappa([]) is None


def test_kappa_counts_unmapped_choices_as_disagreement():
    # observed 2/4; chance (1*2 + 1*2) / 16 = 0.25 -> (0.5 - 0.25) / 0.75
    pairs = [("a", "a"), ("b", "b"), (None, "a"), (None, "b")]
    assert kappa(pairs) == pytest.approx(1 / 3)


def test_bootstrap_repeats_with_the_same_seed():
    pairs = two_by_two(20, 5, 10, 15)
    first = bootstrap_kappa(pairs, resamples=200, seed=7)
    assert first == bootstrap_kappa(pairs, resamples=200, seed=7)
    low, high, used = first
    assert low < 0.4 < high
    assert used == 200


def test_bootstrap_is_none_when_kappa_never_defined():
    assert bootstrap_kappa([("y", "y")] * 5, resamples=50) is None
    assert bootstrap_kappa([]) is None
