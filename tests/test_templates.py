from src.config import CONNECTION_NOTE_MAX_CHARS
from src.templates import (
    FOLLOW_UP_MAX_CHARS,
    build_connection_note,
    build_follow_up,
    first_name,
    infer_honorific,
)

# The worst realistic case for length: a long multi-part name, a long
# title, and a long company name -- verified by hand to be the tightest
# case before these templates were finalized.
LONG_NAME = "Venkata Subramaniam"  # recognized male first name -> "Sir" added
LONG_TITLE = "Senior Vice President - Human Resources and Talent Acquisition"
LONG_COMPANY = "Krishna Institute of Medical Sciences (KIMS)"


def test_honorific_recognizes_common_names():
    assert infer_honorific("Ravi Kumar") == "Sir"
    assert infer_honorific("Priya Sharma") == "Ma'am"


def test_honorific_omitted_for_unrecognized_name():
    assert infer_honorific("Xyzko Blorptown") is None


def test_first_name_extraction():
    assert first_name("Venkata Subramaniam Rao") == "Venkata"
    assert first_name("Ravi") == "Ravi"


def test_connection_note_contains_exact_facts_not_paraphrased():
    note = build_connection_note("Priya Sharma", "hr_ta")
    assert "email ID" in note
    assert "batch profile" in note


def test_connection_note_never_exceeds_limit_worst_case():
    for role in ("hr_ta", "ops_scm"):
        note = build_connection_note(LONG_NAME, role)
        assert len(note) <= CONNECTION_NOTE_MAX_CHARS, f"{role}: {len(note)} chars"


def test_connection_note_greets_with_name_and_honorific():
    note = build_connection_note("Ravi Kumar", "hr_ta")
    assert note.startswith("Hi Ravi Sir,")


def test_connection_note_omits_honorific_for_unrecognized_name():
    note = build_connection_note("Xyzko Blorptown", "hr_ta")
    assert note.startswith("Hi Xyzko,")


def test_follow_up_contains_exact_program_facts():
    msg = build_follow_up("Charan", "Human Resources Executive", "Divi's Laboratories", "hr_ta")
    for fact in ("62", "24.89 LPA", "47.99 LPA", "#21", "#98", "8160685489",
                 "amitkumarmalviya.gscm2026@iimu.ac.in"):
        assert fact in msg, f"missing fact: {fact}"


def test_follow_up_never_exceeds_limit_worst_case():
    for role in ("hr_ta", "ops_scm"):
        msg = build_follow_up(LONG_NAME, LONG_TITLE, LONG_COMPANY, role)
        assert len(msg) <= FOLLOW_UP_MAX_CHARS, f"{role}: {len(msg)} chars"


def test_follow_up_drops_overlong_title_rather_than_exceed_limit():
    msg = build_follow_up("Charan", LONG_TITLE, "Divi's Laboratories", "hr_ta")
    assert LONG_TITLE not in msg
    assert len(msg) <= FOLLOW_UP_MAX_CHARS


def test_follow_up_keeps_short_title():
    msg = build_follow_up("Charan", "Human Resources Executive", "Divi's Laboratories", "hr_ta")
    assert "Human Resources Executive" in msg


def test_follow_up_handles_missing_title():
    msg = build_follow_up("Charan", None, "Divi's Laboratories", "hr_ta")
    assert "Divi's Laboratories" in msg
    assert len(msg) <= FOLLOW_UP_MAX_CHARS


def test_ops_scm_follow_up_mentions_supply_chain_focus():
    msg = build_follow_up("Ravi", "Plant Head", "Cipla", "ops_scm")
    assert "Supply Chain" in msg
