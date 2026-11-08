"""The shadow report decides whether the screen gates may be enforced, so the rows
it counts as evidence must be the right ones: only fully scored jobs have a real
score to compare the gate against."""

from maintenance.gate_shadow_report import summarize


def _row(score, rough=None, c1=None, german="unstated", applied=False, screen_only=False):
    breakdown = {"german_required": german,
                 "screen_gates": {"german_c1": c1, "rough_score": rough,
                                  "below_floor": rough is not None and rough < 30,
                                  "enforced": False}}
    if screen_only:
        breakdown["screen_only"] = True
    return {"resume_score": score, "application_date": "2026-11-10" if applied else None,
            "score_breakdown": breakdown}


def test_a_skipped_job_that_scored_30_plus_counts_against_the_floor():
    out = summarize([_row(45, rough=20), _row(12, rough=15), _row(70, rough=60)], floor=30)
    assert out["full_scored"] == 3
    assert out["floor_would_skip"] == 2
    assert out["floor_skip_scored_30_plus"] == 1


def test_an_applied_job_under_the_floor_makes_it_too_strict():
    rows = [_row(12, rough=15, applied=True)] + [_row(10, rough=10) for _ in range(60)]
    out = summarize(rows, floor=30)
    assert out["floor_skip_applied"] == 1
    assert out["verdict_floor"] == "too strict"


def test_floor_ok_when_skips_stay_low():
    rows = [_row(10, rough=10) for _ in range(60)] + [_row(40, rough=25)]
    out = summarize(rows, floor=30)
    assert out["verdict_floor"] == "ok"


def test_screen_only_rows_are_not_evidence():
    out = summarize([_row(20, rough=20, screen_only=True)], floor=30)
    assert out["full_scored"] == 0
    assert out["floor_would_skip"] == 0


def test_the_floor_argument_is_used_not_the_stored_flag():
    out = summarize([_row(50, rough=35)], floor=40)
    assert out["floor_would_skip"] == 1


def test_c1_hit_the_scorer_did_not_call_c1_is_a_false_positive():
    rows = [_row(40, c1="sehr gute Deutsch", german="B2"),
            _row(40, c1="verhandlungssicher", german="C1-fluent")]
    out = summarize(rows, floor=30)
    assert out["c1_hits_full_scored"] == 2
    assert out["c1_false_positives"] == 1
    assert out["c1_false_positive_rate"] == 0.5
    assert out["verdict_c1"] == "too strict"


def test_empty_input_reports_no_data():
    out = summarize([], floor=30)
    assert out["full_scored"] == 0
    assert out["floor_would_skip"] == 0
    assert out["c1_hits_full_scored"] == 0
    assert out["verdict_floor"] == "no data"
    assert out["verdict_c1"] == "no data"
