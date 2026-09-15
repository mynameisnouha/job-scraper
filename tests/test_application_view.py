"""An application as a record: timeline, age, tabs."""
from datetime import datetime, timedelta, timezone

from review import application_view as av
from review import calibration

NOW = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)


def _job(stage, applied_days=10, updated_days=None, **extra):
    job = {"job_id": "j", "application_stage": stage,
           "application_date": (NOW - timedelta(days=applied_days)).isoformat(),
           "stage_updated_at": (NOW - timedelta(days=updated_days)).isoformat()
           if updated_days is not None else None}
    job.update(extra)
    return job


class TestTimeline:
    def test_waiting_shows_applied_then_a_pending_interview(self):
        nodes = av.timeline(_job("applied"))
        assert [n["label"] for n in nodes] == ["Applied", "Interview 1", "Outcome"]
        assert [n["state"] for n in nodes] == ["done", "pending", "pending"]
        assert nodes[0]["date"] == "2 Sep"

    def test_interview_two_implies_interview_one(self):
        nodes = av.timeline(_job("interview_2", updated_days=1))
        assert [n["label"] for n in nodes] == ["Applied", "Interview 1", "Interview 2",
                                              "Interview 3", "Outcome"]
        assert [n["state"] for n in nodes] == ["done", "done", "current", "pending", "pending"]

    def test_rejection_says_nothing_about_how_far_it_got(self):
        nodes = av.timeline(_job("rejected", updated_days=2))
        assert [n["label"] for n in nodes] == ["Applied", "Rejected"]
        assert nodes[1]["state"] == "bad"

    def test_offer_closes_the_line(self):
        nodes = av.timeline(_job("offer", updated_days=0))
        assert nodes[-1]["label"] == "Offer"
        assert nodes[-1]["state"] == "current"

    def test_missing_stage_reads_as_applied(self):
        assert av.stage_of({"application_stage": None}) == "applied"
        assert av.timeline({"application_stage": None})[0]["label"] == "Applied"


class TestAge:
    def test_fresh_application_is_normal(self):
        assert av.age_label(_job("applied", applied_days=6), NOW) == \
            {"text": "6 days · normal", "tone": "neutral"}

    def test_silence_past_the_chase_line_warns(self):
        age = av.age_label(_job("applied", applied_days=av.CHASE_AFTER_DAYS), NOW)
        assert age["tone"] == "warn" and "chase" in age["text"]

    def test_silence_past_the_ghost_line_says_so(self):
        age = av.age_label(_job("applied", applied_days=calibration.GHOSTED_AFTER_DAYS), NOW)
        assert "ghosted" in age["text"]

    def test_resolved_applications_do_not_need_chasing(self):
        assert not av.needs_chasing(_job("rejected", applied_days=60), NOW)
        assert not av.needs_chasing(_job("interview_1", applied_days=60), NOW)
        assert av.needs_chasing(_job("applied", applied_days=60), NOW)


class TestTabs:
    def test_counts_and_filters_agree(self):
        jobs = [_job("applied", 3), _job("applied", 20), _job("rejected", 30),
                _job("interview_1", 15), _job("spam_or_removed", 5)]
        c = av.counts(jobs, NOW)
        assert c == {"open": 2, "chase": 1, "resolved": 2, "all": 5}
        assert len(av.filter_tab(jobs, "open", NOW)) == 2
        assert len(av.filter_tab(jobs, "chase", NOW)) == 1
        assert len(av.filter_tab(jobs, "resolved", NOW)) == 2
        assert len(av.filter_tab(jobs, "all", NOW)) == 5

    def test_rejection_reason_joins_the_stage_label(self):
        assert av.stage_label(_job("rejected", rejection_reason="german_level")) == \
            "Rejected · german level"
        assert av.stage_label(_job("applied")) == "Awaiting reply"
