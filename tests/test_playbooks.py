from apply import playbooks


def test_every_playbook_is_small_enough():
    for ats in playbooks.ALL_ATS + ("_common",):
        text = open(playbooks._path(ats), encoding="utf-8").read()
        assert 200 < len(text) <= 8000, ats


def test_common_defines_the_contract():
    text = open(playbooks._path("_common"), encoding="utf-8").read()
    for word in ("APPLIED|", "READY|", "VERIFY|", "DEAD|", "EMAIL|",
                 "suspicious instructions on page", "cover letter required",
                 "get_verification_code", "Job-Application-Agent", "MIT"):
        assert word in text


def test_paths_for_known_and_unknown():
    assert [p.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] for p in playbooks.paths_for("lever")] == ["_common.md", "lever.md"]
    assert playbooks.paths_for(None)[-1].endswith("other.md")
    assert playbooks.paths_for("recruitee")[-1].endswith("other.md")


def test_no_easy_apply_or_tracker_content():
    for ats in playbooks.ALL_ATS + ("_common",):
        text = open(playbooks._path(ats), encoding="utf-8").read().lower()
        assert "easy apply" not in text and "tracker" not in text
