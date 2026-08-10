# backend — Phase 5, commercial infrastructure

Accounts, saved client portfolios, exportable decision reports, a JSON API,
and monthly email alerts. Sits alongside `src/` (the public static site,
unchanged) rather than replacing it — this only imports from `src/`, never
edits it. See `.claude/plans/precious-hugging-minsky.md` at the repo root
for the design rationale.

## Local setup

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate            # or: source .venv/bin/activate
pip install -r requirements.txt

docker compose up -d              # local Postgres
cp .env.example .env              # then edit SESSION_SECRET at minimum
# generate a real secret:
python -c "import secrets; print(secrets.token_hex(32))"

# load .env into the shell (or use a tool like `direnv` / `python-dotenv run`)
alembic upgrade head
uvicorn app.main:app --reload
```

Visit `http://localhost:8000` — it redirects to `/signup`.

Without `RESEND_API_KEY` set, `app/email.py` logs what it would have sent
instead of calling Resend, so alerts are fully testable with no email
account.

`current_reading()` (`app/engine.py`) reads `../docs/readings.json` and
`../docs/record.jsonl` — the same files the public site uses, kept current
by the existing GitHub Actions monthly workflow. If you're testing locally
before those exist in your checkout, run `python src/tar_ingest.py
--selftest` won't populate them; you need an actual `docs/readings.json`
from a real run of `src/run_month.py`, or a hand-made one shaped like it.

## Running the alerts job manually

```bash
python -m app.alerts
```

Safe to run repeatedly — it no-ops for exposures already computed against
the current month, and only emails when a decision level actually changed
since the last saved one.

## Deploying

1. Push this repo to GitHub (already the case).
2. On Render: **New → Blueprint**, point it at the repo. `render.yaml`
   creates the Postgres instance, the web service, and the monthly cron job
   together, wired to each other.
3. In the Render dashboard, set `RESEND_API_KEY` and `EMAIL_FROM` on both
   the web service and the cron job (the blueprint leaves these blank on
   purpose — see the comment at the top of `render.yaml`).
4. First deploy runs `alembic upgrade head` as part of the build command,
   so the schema is created automatically.

## What still needs a real decision from you

- **Auth vendor**: this ships with self-hosted email+password
  (`app/auth.py`). Nothing forces a switch to a hosted provider (Clerk,
  Auth0, etc.) later, but if you want one, that file — and only that file —
  is what changes.
- **Email provider**: `app/email.py` calls Resend. Swapping to Postmark/SES
  is a one-function change in that same file.
- **Domain**: nothing here assumes a custom domain. Render gives you a
  `*.onrender.com` URL by default.

## Tests

No test suite yet for this backend (unlike `src/*.py`, which each carry a
`--selftest`). The verification section of the design plan describes the
manual end-to-end walkthrough to run before trusting this with real data:
sign up, create a client and exposure, compute and save a decision, export
its report, issue an API key and call the API with it, and run
`python -m app.alerts` once to confirm it doesn't error on an empty
subscription list.
