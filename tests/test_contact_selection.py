"""Contact-selection rules, using the real bad picks from earlier runs as
cases: a junior HR associate chosen over a senior leader, a first-listed
current role unrelated to the search, and wrong-company matches."""
from src.crustdata_client import (
    HR_TA_TITLE_KEYWORDS,
    OPS_SCM_EXCLUDE,
    OPS_SCM_TITLE_KEYWORDS,
    build_search_body,
    pick_best_contact,
    seniority_score,
)


def _profile(name, roles, url=None):
    return {
        "basic_profile": {"name": name},
        "experience": {"employment_details": {"current": [
            {"name": company, "title": title} for company, title in roles
        ]}},
        "social_handles": {"professional_network_identifier": {"profile_url": url or f"https://www.linkedin.com/in/{name.lower().replace(' ', '-')}"}},
    }


def test_seniority_ordering():
    assert seniority_score("Chief Human Resources Officer") == 5
    assert seniority_score("Vice President - Talent Acquisition") == 4
    assert seniority_score("Head of HR") == 3
    assert seniority_score("Associate Director, HR") == 3  # senior marker beats "Associate"
    assert seniority_score("Assistant General Manager - HR") == 2
    assert seniority_score("HR Manager") == 1
    assert seniority_score("Senior Office Associate - Corp HR") == -1
    assert seniority_score("HR Intern") == -1
    # word boundaries: "COO" must not match inside "Coordinator"
    assert seniority_score("Supply Chain Coordinator") == -1


def test_picks_most_senior_not_first_returned():
    # Real case: ITC's HR contact was a "Senior Office Associate" because
    # they happened to be returned first.
    profiles = [
        _profile("Rosie Ghosh", [("ITC Limited", "Senior Office Associate - Corp HR")]),
        _profile("Anil Mehta", [("ITC Limited", "Head - Human Resources")]),
        _profile("Kavya Rao", [("ITC Limited", "HR Manager")]),
    ]
    c = pick_best_contact("hr_ta", profiles, "ITC", HR_TA_TITLE_KEYWORDS, [])
    assert c.name == "Anil Mehta"
    assert c.title == "Head - Human Resources"


def test_title_comes_from_the_matching_role_not_the_first_one():
    # Real case: Britannia's "HR/TA" contact showed "Head - Digital
    # Solutions" -- the person's first-listed current role, not the one
    # that matched the search.
    profiles = [
        _profile("Sandip Singh", [
            ("Some Startup", "Head - Digital Solutions"),
            ("Britannia Industries", "HR Business Partner"),
        ]),
    ]
    c = pick_best_contact("hr_ta", profiles, "Britannia", HR_TA_TITLE_KEYWORDS, [])
    assert c.name == "Sandip Singh"
    assert c.title == "HR Business Partner"


def test_rejects_profile_whose_matching_role_is_at_another_company():
    profiles = [
        _profile("Someone", [
            ("Britannia Industries", "Head - Digital Solutions"),
            ("Other Co", "HR Manager"),
        ]),
    ]
    c = pick_best_contact("hr_ta", profiles, "Britannia", HR_TA_TITLE_KEYWORDS, [])
    assert c.name is None


def test_ops_excludes_sales_and_hr_operations():
    profiles = [
        _profile("Sales Person", [("Cipla", "Head - Sales Operations")]),
        _profile("HR Person", [("Cipla", "HR Operations Lead")]),
        _profile("Plant Person", [("Cipla", "Plant Head - Goa")]),
    ]
    c = pick_best_contact("ops_scm", profiles, "Cipla", OPS_SCM_TITLE_KEYWORDS, OPS_SCM_EXCLUDE)
    assert c.name == "Plant Person"


def test_no_suitable_candidate_returns_blank_not_a_guess():
    c = pick_best_contact("hr_ta", [], "Cipla", HR_TA_TITLE_KEYWORDS, [])
    assert c.name is None and c.title is None and c.linkedin_url is None


def test_search_filters_to_india_and_fetches_multiple_candidates():
    body = build_search_body("ITC Limited", HR_TA_TITLE_KEYWORDS)
    conditions = body["filters"]["conditions"]
    assert {"field": "basic_profile.location.country", "type": "=", "value": "India"} in conditions
    company = next(c for c in conditions if c.get("field", "").endswith("company_name"))
    assert company["value"] == "ITC"  # legal suffix stripped
    assert body["limit"] > 1
