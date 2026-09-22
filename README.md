# Outreach Agent

Pick a sector, click "Run workflow" on GitHub, get back an Excel file of
top companies in that sector with two contacts per company (HR/Talent
Acquisition, and a senior Ops/Supply-Chain contact), each with a
personalized LinkedIn connection note (≤300 characters) and a
post-acceptance follow-up message.

This is a standalone rebuild of an interactive Claude-Code outreach
workflow, so it can run unattended from GitHub Actions instead of needing
a live chat session.

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
| 1. Discover | `src/discovery.py` | One Gemini call: sector → ranked top-N company names (JSON, not free text) |
| 2. Research | `src/crustdata_client.py` | Per company: 2 Crustdata Person Search calls (HR/TA, Ops/SCM), filtered by current employer name + title keyword in one query each |
| 3. Draft | `src/drafting.py` | One Gemini call per company, covering both contacts: a ≤300-char connection note + follow-up each, using only facts actually returned by Crustdata |
| 4. Validate | `src/qa.py` | Length, combined-salutation, placeholder, malformed-link, and orphan-message checks; failures get a `QA_FLAG`, never silently dropped |
| 5. Write | `src/workbook.py` | `.xlsx` with real clickable LinkedIn hyperlinks (not bare URLs), frozen header row |

`src/pipeline.py` wires these together with per-company error isolation —
one failed lookup or draft doesn't take down a 100-company run; it shows
up as a `QA_FLAG` on that row instead.

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

## What it deliberately won't do

- Never sends anything on LinkedIn — output is drafts only, for a human to
  review and send.
- Never invents a contact's title, a company fact, or a LinkedIn URL that
  Crustdata didn't return — a blank cell beats a wrong one.
- Never guesses a gender-specific honorific it isn't confident about —
  omits it rather than risk misgendering someone.
