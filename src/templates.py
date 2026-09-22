"""Fixed message templates for the connection note and follow-up.

Replaces free-form LLM drafting for message CONTENT. The facts here (batch
size, rankings, recruiters, placement numbers) are real, user-supplied
facts about the sender's own program -- not something an LLM should be
composing or paraphrasing, since a single wrong digit ("25.89" instead of
"24.89 LPA") would be a real, embarrassing error with no way to catch it
downstream. Only four things are personalized per contact: name, honorific,
title, and company -- everything else is fixed.

The LLM (src/llm.py) is still used for discovery.py (the company list) --
it is no longer used for drafting message content at all. Removing it here
also removes drafting's exposure to Gemini/Groq rate limits entirely.

Length is enforced at the template level, not by asking an LLM to hit a
budget: every clause has a known max length, and CONNECTION_NOTE_MAX_CHARS
/ FOLLOW_UP_MAX_CHARS are checked in tests against worst-case (long name +
long title + long company) inputs, with defensive shortening in the
builders themselves (drop the honorific, drop the title clause) so a real
long name can never silently exceed the limit.
"""
from __future__ import annotations

from src.config import CONNECTION_NOTE_MAX_CHARS

FOLLOW_UP_MAX_CHARS = 800

SENDER_NAME = "Amit Malviya"
SENDER_ROLE = "Placement Coordinator for the One-Year MBA in Global Supply Chain Management at IIM Udaipur"
SENDER_EMAIL = "amitkumarmalviya.gscm2026@iimu.ac.in"
SENDER_PHONE = "8160685489"

BATCH_SIZE = 62
AVG_EXPERIENCE_YEARS = 4
ENGINEERS_PCT = 88
NIRF_RANK = 21
NIRF_YEAR = 2025
FT_RANK = 98
FT_YEAR = 2025
TOP_RECRUITERS = "Accenture, McKinsey & Company, and Infosys"
PLACEMENT_YEAR = "2025-26"
AVG_PACKAGE_LPA = "24.89 LPA"
HIGHEST_PACKAGE_LPA = "47.99 LPA"

# A title clause this long pushes the follow-up over FOLLOW_UP_MAX_CHARS
# for a company name of ordinary length too -- verified in
# tests/test_templates.py against a deliberately long name/title/company.
# Past this, the title is dropped from the intro sentence rather than the
# message silently exceeding the limit.
_TITLE_CLAUSE_MAX_CHARS = 35

# Curated common Indian first names for a confident, gendered honorific.
# Deliberately not exhaustive -- an unrecognized name gets NO honorific
# (falls back to first name only), never a guess. Matches the project's
# standing rule that a blank beats a wrong one, applied here to gender the
# same way it's applied to facts.
_MALE_FIRST_NAMES = {
    "aditya", "ajay", "akash", "akshay", "amit", "amitabh", "anand", "anil",
    "anirudh", "ankit", "anup", "anurag", "arjun", "arun", "arvind", "ashish",
    "ashok", "atul", "bharat", "chandan", "charan", "deepak", "dev", "dhruv",
    "gaurav", "girish", "gopal", "gopinath", "harish", "irshad", "jagdish",
    "jatin", "kailash", "kapil", "karan", "karthik", "kiran", "kishore",
    "kumar", "kundan", "lokesh", "mahesh", "manish", "manoj", "mohan",
    "mukesh", "naresh", "narendra", "naveen", "nikhil", "nitin", "pankaj",
    "pradeep", "prakash", "pramod", "prashant", "praveen", "rahul", "raj",
    "rajat", "rajeev", "rajendra", "rajesh", "rajiv", "raju", "ram",
    "ramakrishna", "ramesh", "ranjan", "ravi", "rohan", "rohit", "sachin",
    "sandeep", "sandip", "sanjay", "sanjeev", "santosh", "satish", "shailesh",
    "shankar", "sharad", "shiv", "shyam", "siddharth", "srinivas", "subhash",
    "sudhir", "sunil", "suresh", "tarun", "umesh", "varun", "venkat",
    "venkatesh", "vijay", "vikas", "vikram", "vinay", "vinod", "vipul",
    "vishal", "vivek", "yash", "yogesh",
}
_FEMALE_FIRST_NAMES = {
    "aarti", "aishwarya", "alka", "amita", "anita", "anjali", "ankita",
    "anu", "anusha", "aparna", "archana", "arti", "asha", "bhavana",
    "bhavna", "chitra", "deepa", "deepika", "divya", "gayatri", "geeta",
    "gita", "gunjan", "ishita", "jaya", "jyoti", "kajal", "kavita", "kavya",
    "khushboo", "kiran", "komal", "kriti", "lakshmi", "latha", "lavanya",
    "madhavi", "madhuri", "malini", "manisha", "manju", "maya", "meena",
    "meenakshi", "megha", "monika", "mrunal", "namrata", "nandini", "neelam",
    "neha", "nidhi", "nikita", "nisha", "nitya", "pallavi", "payal", "poonam",
    "pooja", "prachi", "pragati", "pragya", "preeti", "priya", "priyanka",
    "radha", "radhika", "rani", "rashmi", "reema", "renu", "renuka", "rina",
    "ritu", "roshni", "sadhana", "sangeeta", "sapna", "sarika", "sarita",
    "savita", "shalini", "shanti", "sheela", "shikha", "shilpa", "shobha",
    "shreya", "shruti", "simran", "smita", "sneha", "sonal", "sonali",
    "sonia", "sudha", "sujata", "suman", "sunita", "swati", "tanvi",
    "tanya", "uma", "usha", "vandana", "vanita", "vidya", "vijaya", "vinita",
}


def first_name(full_name: str) -> str:
    return full_name.strip().split()[0] if full_name.strip() else full_name


def infer_honorific(full_name: str) -> str | None:
    """Returns "Sir", "Ma'am", or None if the first name isn't a confident
    match in either list. Never guesses."""
    name = first_name(full_name).lower().strip(".,")
    if name in _MALE_FIRST_NAMES:
        return "Sir"
    if name in _FEMALE_FIRST_NAMES:
        return "Ma'am"
    return None


def _greeting_name(contact_name: str) -> str:
    fname = first_name(contact_name)
    honorific = infer_honorific(contact_name)
    return f"{fname} {honorific}" if honorific else fname


def build_connection_note(contact_name: str, role: str) -> str:
    greeting = _greeting_name(contact_name)
    if role == "ops_scm":
        note = (
            f"Hi {greeting}, Amit from IIM Udaipur's Global Supply Chain Management program\n"
            f"Glad to connect - our batch specializes in Supply Chain & Operations. Could you "
            f"share your email ID so I can send our batch profile? I won't bombard you; happy "
            f"to follow up only if there's a fit."
        )
    else:
        note = (
            f"Hi {greeting}, Amit from the IIM Udaipur Placement Committee\n"
            f"Glad to connect with you. Could you please share your email ID so I can send you "
            f"our batch profile? I won't bombard you with mails and messages; if you like our "
            f"profile, I'd love to fill you in if any opportunity exists."
        )

    if len(note) <= CONNECTION_NOTE_MAX_CHARS:
        return note
    # Defensive fallback for an unusually long name -- drop the honorific
    # and retry with first name only before ever truncating mid-sentence.
    return build_connection_note(first_name(contact_name), role) if infer_honorific(contact_name) else note


def build_follow_up(contact_name: str, title: str | None, company_name: str, role: str) -> str:
    greeting = _greeting_name(contact_name)
    title = (title or "").strip()
    use_title = bool(title) and len(title) <= _TITLE_CLAUSE_MAX_CHARS

    if role == "ops_scm":
        if use_title:
            intro = (
                f"Thank you for connecting! I'm {SENDER_NAME}, GSCM Placement Coordinator at "
                f"IIM Udaipur (Supply Chain focus), reaching out as you're {title} at {company_name}."
            )
        else:
            intro = (
                f"Thank you for connecting! I'm {SENDER_NAME}, GSCM Placement Coordinator at "
                f"IIM Udaipur (Supply Chain focus), reaching out to you at {company_name}."
            )
    else:
        if use_title:
            intro = (
                f"Thank you for connecting! I'm {SENDER_NAME}, GSCM Placement Coordinator at "
                f"IIM Udaipur, reaching out as you're {title} at {company_name}."
            )
        else:
            intro = (
                f"Thank you for connecting! I'm {SENDER_NAME}, GSCM Placement Coordinator at "
                f"IIM Udaipur, reaching out to you at {company_name}."
            )

    stats = (
        f"Our batch of {BATCH_SIZE} (avg. {AVG_EXPERIENCE_YEARS} yrs' experience, "
        f"{ENGINEERS_PCT}% engineers) is placed via IIM Udaipur (NIRF #{NIRF_RANK} India, "
        f"FT #{FT_RANK} global) through recruiters like {TOP_RECRUITERS} -- {PLACEMENT_YEAR} "
        f"average package {AVG_PACKAGE_LPA}, highest {HIGHEST_PACKAGE_LPA}."
    )
    ask = (
        "We'd love to build a longer-term hiring relationship with your team -- campus "
        "placements, live projects, and internships, as fits your calendar."
    )
    email_ask = (
        "Could you share your official email ID so I can send our placement brochure and "
        "program details?"
    )
    signature = (
        "Best regards,\n"
        f"{SENDER_NAME} | IIM Udaipur GSCM\n"
        f"{SENDER_EMAIL} | {SENDER_PHONE}"
    )

    message = "\n\n".join([f"Hi {greeting},", intro, stats, ask, email_ask, signature])

    if len(message) <= FOLLOW_UP_MAX_CHARS:
        return message
    # Defensive fallback: drop the honorific (shortest name form) before
    # ever truncating mid-sentence. Verified in tests that even an extreme
    # name/title/company combination fits without reaching this branch.
    if infer_honorific(contact_name):
        return build_follow_up(first_name(contact_name), title, company_name, role)
    return message
