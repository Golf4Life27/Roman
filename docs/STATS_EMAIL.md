# Weekly stats email (one time, ~15 minutes)

Every Monday at 8:07 AM Central the **Weekly stats** workflow
(`.github/workflows/weekly-stats.yml`) emails the numbers that decide the
YouTube Partner Program goal, so there is no need to open YouTube Studio:

- subscribers now vs 1,000, and the net change over the last week;
- watch hours over the last 12 months vs 4,000;
- days left to 2027-01-31, the pace each goal needs per day, and whether the
  last 7 days are on pace or behind (and by how much per day);
- views per subscriber, last 7 days vs the 7 before, daily watch hours;
- the live-stream trigger: once the channel has **100 subscribers OR averages
  15 watch hours/day** over the last 7 days, the email says
  `TRIGGER MET: ask Claude to switch on the nightly stream` (also in the subject);
- the top 5 videos of the week by watch time.

YouTube Analytics lags 2-3 days, so the weekly figures end two days before the
email is sent; the email states the dates. The watch-hour figure is all watch
time. YPP counts only public long-form hours (Shorts views do not count), so
Studio's YPP number may be slightly lower.

## 1. Re-mint the YouTube token with the analytics scope

Follow docs/YOUTUBE_API_SETUP.md **section 8** (add the scopes under Data
Access, enable the **YouTube Analytics API**), then on your own computer:

```bash
python -m romanfeed auth --scope manage
```

Sign in as the channel owner and click Allow. Then GitHub repo →
**Settings → Secrets and variables → Actions → Secrets** → `YOUTUBE_TOKEN_JSON`
→ **Update** → paste the full contents of `secrets/youtube.token.json`.
The new token still includes the upload scope, so daily uploads keep working.

## 2. Create an app password for the sending account

Gmail and Google Workspace do not accept the normal account password over
SMTP; they need an *app password*, which requires 2-Step Verification.

1. Sign in to the account that will send the email (it can be the same one
   that receives it).
2. https://myaccount.google.com/security → turn on **2-Step Verification**
   if it is not on already.
3. https://myaccount.google.com/apppasswords → name it `space-screens stats`
   → **Create** → copy the 16-character password.
   Google Workspace: if the page says app passwords are not available, a
   Workspace admin has to allow them for the account first.

## 3. Add the secrets and the recipient variable

GitHub repo → **Settings → Secrets and variables → Actions**:

- **Secrets** tab → New repository secret:
  - `SMTP_USER`: the sending address from step 2
  - `SMTP_PASSWORD`: the app password from step 2
- **Variables** tab → New repository variable:
  - `STATS_EMAIL_TO`: the address that should receive the report
    (comma-separate for several). Keep it out of the repository files
    themselves: this repo is public.

Nothing else is needed for Gmail: the server defaults to `smtp.gmail.com` on
port 465, with an automatic fallback to STARTTLS on 587. For another provider,
set `SMTP_HOST` / `SMTP_PORT` in the workflow's env.

## 4. Run it once by hand

Actions → **Weekly stats** → **Run workflow**:

1. First with **dry_run** ticked: the report appears in the job log and on the
   run's summary page, and nothing is sent. This proves the token.
2. Then with **dry_run** unticked: the email should arrive within a minute.

If the run says the token cannot read statistics or analytics, the secret
still holds the old token (redo step 1). If it says the Analytics API is not
enabled, enable it (section 8 of docs/YOUTUBE_API_SETUP.md). If the SMTP
secrets or the variable are missing, the run still succeeds and prints the
report with a warning.

Locally the same report is `python -m romanfeed stats` (add `--email` with
`SMTP_USER`, `SMTP_PASSWORD` and `STATS_EMAIL_TO` set in the environment).
