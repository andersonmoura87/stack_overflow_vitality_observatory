from datetime import timedelta

from stackoverflow_vitality.silver.history import empty_state, observe, rebuild
from stackoverflow_vitality.silver.models import validate_history
from tests.silver_fixtures import COLLECTED, normalized, page, question


def test_first_version_and_replay_are_idempotent():
    state = empty_state()
    record = normalized()
    assert observe(state, record)
    rebuild(state)
    original = list(state["questions"])
    assert not observe(state, record)
    rebuild(state)
    assert state["questions"] == original
    assert len(state["observations"]) == 1
    assert state["questions"][0]["is_current"]


def test_metrics_have_history_without_new_content_versions():
    first = normalized()
    second = normalized(
        page(
            [question(view_count=50, score=2, answer_count=2)],
            collected=COLLECTED + timedelta(days=1),
        )
    )
    assert first.record_hash == second.record_hash
    state = empty_state()
    for record in (first, second):
        observe(state, record)
    rebuild(state)
    assert len(state["catalog"]) == 1
    assert len(state["questions"]) == 1
    assert len(state["observations"]) == 2
    assert state["questions"][0]["view_count"] == 50


def test_change_closes_previous_and_out_of_order_matches_in_order():
    first = normalized()
    second = normalized(
        page([question(body="<p>Changed</p>")], collected=COLLECTED + timedelta(days=1))
    )
    third = normalized(page(collected=COLLECTED + timedelta(days=2)))
    state = empty_state()
    late = empty_state()
    for record in (first, second, third):
        observe(state, record)
        rebuild(state)
    for record in (third, first, second):
        observe(late, record)
        rebuild(late)
    assert state == late
    rows = state["questions"]
    assert len(rows) == 3
    assert sum(row["is_current"] for row in rows) == 1
    assert rows[0]["valid_to"] == rows[1]["valid_from"]
    assert rows[1]["valid_to"] == rows[2]["valid_from"]
    assert rows[0]["record_hash"] == rows[2]["record_hash"]
    validate_history(rows)


def test_hash_does_not_depend_on_tags_order_or_origin():
    first = normalized()
    second = normalized(
        page([question(tags=["testing", "python"])], run_id="00000000-0000-0000-0000-000000000002")
    )
    assert first.record_hash == second.record_hash
    state = empty_state()
    observe(state, first)
    observe(state, second)
    rebuild(state)
    assert len(state["questions"]) == 1 and len(state["observations"]) == 2


def test_tie_is_deterministic_and_keeps_all_observations():
    one = normalized()
    two = normalized(page([question(title="Different")]))
    a, b = empty_state(), empty_state()
    for row in (one, two):
        observe(a, row)
    for row in (two, one):
        observe(b, row)
    assert rebuild(a)
    rebuild(b)
    assert a == b
    assert len(a["questions"]) == 1 and len(a["observations"]) == 2


def test_sites_are_independent():
    a = normalized()
    b = normalized(page(site="serverfault"))
    state = empty_state()
    observe(state, a)
    observe(state, b)
    rebuild(state)
    assert len(state["questions"]) == 2
