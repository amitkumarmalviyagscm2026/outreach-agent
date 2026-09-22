# Outreach Agent

Pick a sector, click "Run workflow" on GitHub, get back an Excel file of
top companies in that sector with two contacts per company (HR/Talent
Acquisition, and a senior Ops/Supply-Chain contact), each with a
personalized LinkedIn connection note (≤300 characters) and a
post-acceptance follow-up message.

This is a standalone rebuild of an interactive Claude-Code outreach
workflow, so it can run unattended from GitHub Actions instead of needing
a live chat session.

## ⚠️ Before your first REAL run

Crustdata's docs are behind a login, so `src/crustdata_client.py` was
written from public fragments only (confirmed: `Authorization: Bearer
<key>` + `x-api-version` header, and a `/company/search` endpoint). Every
line marked `# TODO confirm` is a best-effort placeholder for a path,
param name, or response field this repo could not verify without your
account. **Log into https://app.crustdata.com/api/docs and fix those
before trusting output against your real Crustdata balance.**

The fastest way to find what's wrong: run `--count 1 --mode test`, and
temporarily add a `print(data)` right after the first `self._post(...)`
call in `crustdata_client.py` to see the real response shape, then fix the
field names it's reading.

## Setup

1. Create the repo and add secrets (PowerShell):
   ```powershell
   mkdir outreach-agent; cd outreach-agent
   git init
   gh repo create outreach-agent --private --source=. --remote=origin
   gh secret set CRUSTDATA_API_KEY
   gh secret set ANTHROPIC_API_KEY
   ```
   `ANTHROPIC_API_KEY` is a Claude **API** key from
   [console.anthropic.com](https://console.anthropic.com) — separate
   billing from a Claude.ai / Claude Code subscription.

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
safety net against burning Crustdata credits or Anthropic spend on a
mistake.

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
| 1. Discover | `src/discovery.py` | One Anthropic call: sector → ranked top-N company names (JSON, not free text) |
| 2. Research | `src/crustdata_client.py` | Per company: resolve the company, then 2 Crustdata person searches (HR/TA, Ops/SCM) — cheap DB tier first, live tier only on a miss |
| 3. Draft | `src/drafting.py` | One Anthropic call per contact: a ≤300-char connection note + follow-up, using only facts actually returned by Crustdata |
| 4. Validate | `src/qa.py` | Length, combined-salutation, placeholder, malformed-link, and orphan-message checks; failures get a `QA_FLAG`, never silently dropped |
| 5. Write | `src/workbook.py` | `.xlsx` with real clickable LinkedIn hyperlinks (not bare URLs), frozen header row |

`src/pipeline.py` wires these together with per-company error isolation —
one failed lookup or draft doesn't take down a 100-company run; it shows
up as a `QA_FLAG` on that row instead.

## Cost control

- `run_mode: test` clamps to 10 companies at both the workflow layer and
  inside `run.py` independently (`src/config.clamp_company_count`), so it
  holds even if you call `run.py` directly.
- The job log prints a projected minimum Crustdata call count before the
  research loop starts, and an actual db/live call count plus a QA summary
  at the end — check these in the Actions run log to track spend over
  time.
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
