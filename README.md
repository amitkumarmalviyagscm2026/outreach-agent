# Outreach Agent

Pick a sector, click "Run workflow" on GitHub, get back an Excel file of
top companies in that sector with two contacts per company (HR/Talent
Acquisition, and a senior Ops/Supply-Chain contact), each with a
connection note (≤300 characters) and a post-acceptance follow-up message,
built from a fixed template carrying real facts about the sender's own
program (batch size, rankings, placement numbers) -- personalized only by
the contact's name, honorific, title, and company.

This is a standalone rebuild of an interactive Claude-Code outreach
workflow, so it can run unattended from GitHub Actions instead of needing
a live chat session.

## ⚠️ Run this from a private repository

Every run produces personal data about real people -- names, job titles,
LinkedIn URLs, and messages drafted to them. On a **public** repository,
GitHub Actions run logs are visible to anyone and uploaded artifacts (the
output Excel file) can be downloaded by any signed-in GitHub user. So:

- To use this, **copy the code into a private repository** of your own
  and add your API keys as secrets there. Don't run it on a public repo.
- This repository is a public copy of the code only. It has no secrets
  configured and isn't used for runs.

## How contact research works

`src/crustdata_client.py` is written directly against Crustdata's
documented Person Search API (`POST /person/search`), confirmed from their
docs rather than guessed. It filters people in one call by their current
employer's name and a title keyword, so there's no separate "look up the
company, get an ID, then search people" step. Person Search bills **0.03
credits per result returned, not per request** — a search that matches
nobody costs nothing, and with `limit=1` per call, a full 100-company run
costs at most `100 × 2 × 0.03 = 6` credits for contact research.

If Crustdata changes their schema after this was written, the place to
look is the `fields`/response paths at the top of `crustdata_client.py`
(`basic_profile.name`, `experience.employment_details.current.title`,
`social_handles.professional_network_identifier.profile_url`) — run
`--count 1 --mode test` and print the raw response if a field comes back
empty unexpectedly.

## Troubleshooting: Gemini won't respond

Three distinct problems showed up during setup. `GEMINI_MODELS` in
`src/config.py` is currently `["gemini-flash-lite-latest"]` (Gemini 3.5
Flash Lite) -- here's why, and what to check if it ever needs to change:

**A pinned model name 404s even though it's listed as available.**
`GET /v1beta/models` can list a model (e.g. `gemini-2.5-flash`) as
supporting `generateContent`, but actually calling it 404s anyway for that
account/key -- a known, unresolved Gemini quirk. Always use a `-latest`
alias (`gemini-flash-latest`, `gemini-flash-lite-latest`), never a pinned
dotted version; aliases route to whatever's actually live for your key.

**Different models have wildly different free-tier daily quotas --
check before relying on one.** AI Studio's own **Rate Limit** dashboard
(a tab next to the Usage dashboard, or console.cloud.google.com's Gemini
API "Quotas" page) showed the full, non-Lite alias
(`gemini-flash-latest` -> Gemini 3.8 Flash) capped at just **5 requests/
minute and 20 requests/DAY** on this project's free tier -- a single test
burst exceeded it (29/20), and it then fails on every call until the next
daily reset, no matter how well retries are tuned. `gemini-flash-lite-latest`
(-> Gemini 3.5 Flash Lite) has a far larger budget -- 15 RPM / 500 RPD --
comfortably enough for a 100-company run (~200 Gemini calls). **Before
adding any model to `GEMINI_MODELS`, check its RPM/RPD on that dashboard
first** -- a model that's "available" can still be useless if its daily
cap is smaller than one full run needs.

**A model within its quota can still hit transient 429/503.**
`generate_json()` in `gemini_client.py` retries with real exponential
backoff, and reads Google's own suggested wait time from the 429 response
body (`retryDelay`) when present, obeying that instead of guessing. It
also accepts an ordered **list** of models and falls through to the next
one on exhaustion -- currently a list of one, since Flash Lite alone
covers this pipeline's volume, but the fallback logic is there if a
second well-suited model is ever added. To find what your key can
currently call:

```powershell
$body = @{ contents = @(@{ parts = @(@{ text = "Say hello in one word." }) }) } | ConvertTo-Json -Depth 5
try {
  Invoke-RestMethod -Uri "https://generativelanguage.googleapis.com/v1beta/models/MODEL_NAME:generateContent?key=YOUR_KEY" -Method Post -Body $body -ContentType "application/json"
} catch {
  $_.Exception.Response.StatusCode
  $_.ErrorDetails.Message
}
```

Or list everything your key can currently see (not the same as what it can
actually *call* -- see above):

```powershell
(Invoke-RestMethod -Uri "https://generativelanguage.googleapis.com/v1beta/models?key=YOUR_KEY").models |
  Where-Object { $_.supportedGenerationMethods -contains "generateContent" } |
  Select-Object name
```

## Troubleshooting: the Groq Search contact fallback

This fallback has had two dead ends before landing on its current design --
both confirmed live, not assumed, on 2026-09-30:

1. **Google Custom Search JSON API** -- closed to new Google Cloud accounts
   entirely (live 403s, no workaround, deprecated by 2027 even for existing
   customers).
2. **Gemini's own Google Search grounding** -- looked promising (a free
   quota on the same `GEMINI_API_KEY` already in use), but turned out
   structurally unworkable on a new Google account: Gemini 2.5 (the only
   model family with any free-tier grounding) returned a live 404 --
   *"This model ... is no longer available to new users"* -- and Gemini 3.x
   (what this account's key CAN call) gets **no free-tier grounding at
   all**, paid-only. No model name swap could fix this.

The current fallback uses **Groq's `browser_search` built-in tool** on
`openai/gpt-oss-120b` / `openai/gpt-oss-20b` -- a real-time web search
capability, confirmed active via Groq's own docs as of 2026-09-30. This is
a *different* feature from Groq's `groq/compound` / `groq/compound-beta`
models, which were separately decommissioned on 2026-09-21 and would not
have worked either. It needs `GROQ_API_KEY` (see Setup) -- without it,
there's no contact-search fallback at all, only Crustdata.

**Live-tested 2026-09-30 and it works** -- a real run found and verified
both an HR/TA and an Ops/SCM contact via citation. Two things the first
live run surfaced and fixed:

- **Speed -- two rounds of fixes.** A single fallback company first took
  13m49s: `browser_search` pulls a lot of web content into context, and
  one call alone triggered a 455-second server-suggested `retry-after`
  against the free tier's 8K tokens/minute budget. Capped each retry wait
  at `BROWSER_SEARCH_MAX_WAIT_SECONDS` (45s) with fewer retries -- but the
  very next real run then showed sustained 429s on *every* call after the
  first, on both models. Root cause: a single browser_search call can use
  close to or all of the entire per-minute token budget by itself, so the
  original 10-second pacing between calls (fine for the lighter,
  non-search Groq calls) left the 1-minute window still full when the
  next call fired -- it 429'd immediately regardless of which company it
  was for. Fixed with `BROWSER_SEARCH_PACING_SECONDS` (65s, its own
  constant separate from the 10s used elsewhere) so a call mostly succeeds
  on the first try instead of retrying into a wall. Even with this fix,
  the fallback is still inherently slow (a company takes roughly a minute
  or two per role found) and not viable for dozens of companies in one
  run; treat it as a backstop for a handful of stragglers once Crustdata's
  credits run out, not a bulk substitute for them.
- **Two unrelated bugs the real output caught**, both fixed the same day:
  `infer_honorific()`'s curated name lists were missing common names (a
  found contact named "Rishi" or "Nishit" got no honorific at all,
  silently falling back to first-name-only) -- lists substantially
  expanded in `templates.py`. And `qa.py`'s `PLACEHOLDER_PATTERNS` still
  had a bare `\bGSCM\b` check left over from when drafting was LLM-based;
  since templates.py now deliberately writes "GSCM" in every follow-up's
  intro and signature, that check flagged **100% of real follow-ups** as
  having a "leftover placeholder token" -- removed, with a regression test
  (`test_real_template_output_passes_qa_cleanly`) pinning down real
  template output passing QA cleanly.

If a run reaches the fallback (its log line starts with "Switching to the
free Groq Search fallback") and it's misbehaving, check the log for:

- **`unparseable response`** -- the model didn't return valid JSON (or
  didn't wrap it the way `parse_json_loosely` expects). The log prints the
  first 300 characters of what it actually said; if it's consistently
  ignoring the "respond with only a JSON object" instruction, the prompt in
  `groq_contact_search.py`'s `SYSTEM_PROMPT` needs tightening.
- **`claimed URL not found among cited sources`** -- the model reported a
  LinkedIn URL that didn't match any URL pulled from the response's
  `executed_tools` field, so it was correctly discarded rather than trusted
  blindly. The log prints the URLs it actually found. If `executed_tools`'
  real shape doesn't carry usable URLs at all (Groq's docs don't pin down
  its exact schema), `groq_client._extract_urls()` needs adjusting --
  tell Claude this specific symptom if you hit it.
- **A `RuntimeError` about the request itself** -- e.g. a 404/400 naming a
  model that no longer exists on Groq's side, similar to what happened
  twice already with Gemini model names in this project. Paste the exact
  error.

## Setup

1. Create the repo and add secrets (PowerShell):
   ```powershell
   mkdir outreach-agent; cd outreach-agent
   git init
   gh repo create outreach-agent --private --source=. --remote=origin
   gh secret set CRUSTDATA_API_KEY
   gh secret set GEMINI_API_KEY
   ```
   `GEMINI_API_KEY` is a **free** Google AI Studio key from
   [aistudio.google.com/apikey](https://aistudio.google.com/apikey) — sign
   in with a Google account, no credit card needed. It's rate/quota
   limited rather than a spend-based trial, so it doesn't run out the way
   a paid API's trial credit does.

   **Add `GROQ_API_KEY` as a third secret** — a free key from
   [console.groq.com/keys](https://console.groq.com/keys), no credit card.
   It's no longer just a nice-to-have: Groq now does double duty as (a) the
   backup LLM for discovery.py when Gemini's free tier is overloaded, and
   (b) the **only** contact-search fallback once Crustdata's credit balance
   runs out. Without it, the pipeline still works, but a Crustdata
   exhaustion mid-run leaves all remaining companies blank instead of
   falling back to anything.

   How the backup-LLM fallback behaves (`src/llm.py`):
   - Gemini is always tried first.
   - With Groq configured, Gemini gets 3 retries instead of 5, so a failing
     call hands off in ~40 seconds rather than ~3 minutes.
   - Once Gemini fails completely, it's skipped for 5 minutes and calls go
     straight to Groq, so an outage doesn't cost a full retry cycle per call.
   - The end of each run's log shows how many calls each provider handled.
   - Groq free tier: 30 requests/minute, 1,000/day, 8K tokens/minute per
     model (`openai/gpt-oss-120b`, then `openai/gpt-oss-20b`).

   *(History: this fallback went through two dead ends before landing on
   Groq -- Google's Custom Search JSON API turned out closed to new Google
   Cloud accounts entirely, and Gemini's own Search grounding turned out
   closed to new Google accounts on the one model that has it free at all.
   Both confirmed via live errors, not assumption -- see Troubleshooting
   below and the git log for the full story.)*

   How the contact-search fallback works (`src/groq_contact_search.py`):
   once Crustdata is exhausted, each remaining company gets up to 2 Groq
   calls (HR/TA, Ops/SCM) with the `browser_search` built-in tool enabled,
   asking the model to search for and cite a real LinkedIn profile. The
   model's claimed URL is **never trusted on its word** — it's cross-checked
   against URLs pulled from the response's `executed_tools` field, and
   discarded (left blank) if it doesn't match a real one the tool actually
   surfaced. Flagged as `sourced via free Groq Search fallback -- verify`
   in the output.

2. Local dev (optional but recommended before pushing):
   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   copy .env.example .env   # then fill in real keys, for local use only
   ```

## Usage

**Always run `test` mode first.** It's hard-capped at 10 companies
regardless of what you type into "Number of companies" — this is your
safety net against burning Crustdata credits (Gemini's free tier has no
spend to burn, but it does have a per-minute rate limit worth not hammering
blind) on a mistake.

Via GitHub CLI:
```powershell
gh workflow run outreach.yml -f sector="Pharma" -f company_count=5 -f run_mode=test
gh run watch
gh run download   # after it finishes, pulls the .xlsx artifact
```

Or via the Actions tab → "Outreach Agent" → "Run workflow", fill in the
sector, leave `run_mode` as `test`, run it, download the artifact from the
finished run's page, and open it in Excel — click a LinkedIn cell to
confirm it's a real hyperlink, and read a few notes for tone and length.

Only after that looks right, run again with `run_mode: full` and the
company count you actually want (up to 100).

## Local testing without spending real credits

```powershell
pytest tests/
```
Runs `qa.py`'s validation rules and `workbook.py`'s hyperlink-writing logic
against fixture data — no live API calls, no cost.

To smoke-test against the real APIs as cheaply as possible:
```powershell
python run.py --sector "Pharma" --count 1 --mode test
```

## How it works

| Stage | File | What it does |
|---|---|---|
| 1. Discover | `src/discovery.py` | One LLM call (Gemini, Groq as backup) **for the whole run**: sector → ranked top-N company names (JSON, not free text) |
| 2. Research | `src/contact_search.py` | Per company: 2 Crustdata Person Search calls (HR/TA, Ops/SCM), filtered by current employer name + title keyword. Falls back to Groq Search (`browser_search` tool) for the rest of the run once Crustdata's balance is exhausted (needs `GROQ_API_KEY`, lower confidence — see Setup) |
| 3. Draft | `src/drafting.py` + `src/templates.py` | **No LLM call.** A fixed template per role, filled in with the contact's name, honorific, title, and company — see below |
| 4. Validate | `src/qa.py` | Length, combined-salutation, placeholder, malformed-link, and orphan-message checks; failures get a `QA_FLAG`, never silently dropped |
| 5. Write | `src/workbook.py` | `.xlsx` with real clickable LinkedIn hyperlinks (not bare URLs), frozen header row |

`src/pipeline.py` wires these together with per-company error isolation —
one failed lookup or draft doesn't take down a 100-company run; it shows
up as a `QA_FLAG` on that row instead.

## Message content: fixed templates, not LLM-composed

The connection note and follow-up are built by `src/templates.py`, not
written by an LLM. The follow-up asserts real, specific facts about the
sender's own program — batch size, national/global ranking, recruiter
names, average and highest placement package — and those numbers have to
be exact every time. An LLM asked to "personalize" a message risks
paraphrasing "24.89 LPA" into something close but wrong, with no way to
catch it downstream. A fixed template can't drift.

**To update the facts** (a new batch, new ranking, new placement numbers),
edit the constants at the top of `src/templates.py` — `BATCH_SIZE`,
`NIRF_RANK`, `AVG_PACKAGE_LPA`, etc. Nothing else needs to change.

**Personalization is limited to four things**, read from Crustdata: the
contact's name, an honorific inferred from their first name (a curated
list of common Indian names in `templates.py`; an unrecognized name gets
no honorific rather than a guessed one), their title (dropped from the
sentence if it's unusually long, to guarantee the message stays under its
character limit), and the company name. HR/TA and Ops/SCM contacts get
differently worded messages, framed around their actual function.

**Length is guaranteed, not requested.** `tests/test_templates.py` checks
both templates against a deliberately long name + title + company and
confirms they still fit under 300 / 800 characters — this is enforced by
the template logic itself (dropping the honorific, then the title clause,
before ever truncating a sentence), not by asking an LLM to hit a budget.

One effect of this: **Gemini/Groq are now only called once per run** (for
the company list in step 1), not once per company. The Groq fallback
section above still applies to that one call, just far less often.

## Cost control

- `run_mode: test` clamps to 10 companies at both the workflow layer and
  inside `run.py` independently (`src/config.clamp_company_count`), so it
  holds even if you call `run.py` directly.
- The job log prints a projected Crustdata request/credit estimate before
  the research loop starts, and an actual request/result/credit count plus
  a QA summary at the end — check these in the Actions run log to track
  spend over time.
- Contact scope is fixed at exactly 2 roles per company (HR/TA, Ops/SCM),
  not the full CEO/COO/CHRO/Plant-Head/Campus-Recruiter list, specifically
  to keep per-run cost predictable.
- If Crustdata's balance runs out mid-run, the rest of the run switches to
  the free Groq Search fallback (if `GROQ_API_KEY` is set) rather than
  stopping — see Setup for what this trades off (lower-confidence
  contacts, flagged in the output).

## What it deliberately won't do

- Never sends anything on LinkedIn — output is drafts only, for a human to
  review and send.
- Never invents a contact's title, a company fact, or a LinkedIn URL that
  Crustdata didn't return — a blank cell beats a wrong one.
- Never guesses a gender-specific honorific it isn't confident about —
  omits it rather than risk misgendering someone.
