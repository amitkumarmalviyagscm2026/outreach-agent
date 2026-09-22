from src.config import CONNECTION_NOTE_MAX_CHARS
from src.qa import validate_contact_messages


def test_clean_message_passes():
    result = validate_contact_messages(
        connection_note="Hi Rahul, I'm exploring supply chain roles in pharma and admire Cipla's manufacturing scale. Would love to connect.",
        follow_up_message="Thanks for connecting, Rahul! I'd love to learn more about your team's work.",
        linkedin_url="https://www.linkedin.com/in/rahul-example",
    )
    assert result.ok
    assert result.flags == []


def test_over_length_note_fails():
    long_note = "x" * (CONNECTION_NOTE_MAX_CHARS + 1)
    result = validate_contact_messages(long_note, "fine", "https://www.linkedin.com/in/x")
    assert not result.ok
    assert any("exceeds" in f for f in result.flags)


def test_combined_salutation_fails():
    result = validate_contact_messages(
        "Dear Sir/Madam, I would like to connect.",
        "Thanks for connecting.",
        "https://www.linkedin.com/in/x",
    )
    assert not result.ok
    assert any("salutation" in f for f in result.flags)


def test_placeholder_token_fails():
    result = validate_contact_messages(
        "Hi {name}, great to see your work at [Company].",
        "Thanks for connecting.",
        "https://www.linkedin.com/in/x",
    )
    assert not result.ok
    assert any("placeholder" in f for f in result.flags)


def test_unexpanded_gscm_fails():
    result = validate_contact_messages(
        "As a GSCM student I'd love to connect.",
        "Thanks for connecting.",
        "https://www.linkedin.com/in/x",
    )
    assert not result.ok


def test_bare_initial_greeting_fails():
    result = validate_contact_messages(
        "Hi B, great to connect.",
        "Thanks for connecting.",
        "https://www.linkedin.com/in/x",
    )
    assert not result.ok
    assert any("bare initial" in f for f in result.flags)


def test_doubled_punctuation_fails():
    result = validate_contact_messages(
        "Hi Ravi Sir,, great to connect.",
        "Thanks for connecting.",
        "https://www.linkedin.com/in/x",
    )
    assert not result.ok


def test_malformed_linkedin_url_fails():
    result = validate_contact_messages(
        "Hi Ravi, great to connect.",
        "Thanks for connecting.",
        "not-a-url",
    )
    assert not result.ok
    assert any("valid LinkedIn URL" in f for f in result.flags)


def test_orphan_url_without_messages_fails():
    result = validate_contact_messages(None, None, "https://www.linkedin.com/in/x")
    assert not result.ok
    assert any("orphan" in f for f in result.flags)


def test_blank_contact_is_not_flagged_as_orphan():
    # No linkedin_url and no messages -- this is a genuine "no contact
    # found" row, not an orphan; pipeline.py flags it separately.
    result = validate_contact_messages(None, None, None)
    assert result.ok
