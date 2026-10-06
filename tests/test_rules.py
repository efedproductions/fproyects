from app.rules import completion, done_count, mean_positive, rollup, streak_days


def test_rollup_ignores_zeros():
    assert rollup([5, 5, 0]) == 5


def test_rollup_all_zeros_is_zero():
    assert rollup([0, 0, 0]) == 0


def test_rollup_without_children_is_zero():
    assert rollup([]) == 0


def test_rollup_averages_only_done_ones():
    assert rollup([3, 7, 0, 0]) == 5


def test_completion_is_separate_from_the_average():
    values = [5, 5, 0]
    assert done_count(values) == 2
    assert completion(values) == 2 / 3
    assert mean_positive(values) == 5


def test_mean_positive_without_activity_is_none():
    assert mean_positive([0, 0]) is None


def test_streak_counts_consecutive_active_days():
    today = "2026-03-10"
    days = [
        {"day": "2026-03-10", "has_activity": True},
        {"day": "2026-03-09", "has_activity": True},
        {"day": "2026-03-08", "has_activity": False},
        {"day": "2026-03-07", "has_activity": True},
    ]
    from datetime import date

    assert streak_days(days, date.fromisoformat(today)) == 2