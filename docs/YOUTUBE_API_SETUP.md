# YouTube API setup (one time, ~30 minutes)

Goal: a refresh token the daily workflow can use to upload without you, and
an API audit so uploads can go Public without a manual flip.

## 1. Google Cloud project

1. https://console.cloud.google.com → project picker → **New project** → name `space-screens` → Create.
2. **APIs & Services → Library** → search "YouTube Data API v3" → **Enable**.

## 2. OAuth consent screen

1. **APIs & Services → OAuth consent screen** (Google may call it "Google Auth Platform → Branding").
2. User type: **External**. App name `Space Screens`, your support email, your developer email. Save.
3. **Scopes** → Add → tick `https://www.googleapis.com/auth/youtube.upload` → Update → Save.
4. **Audience / Publishing status → Publish app** (move from *Testing* to *In production*).
   This matters: a project left in Testing gets refresh tokens that **expire
   after 7 days**, which would silently stop the daily upload. In production,
   unverified apps show a warning screen during sign-in (fine, it is only you)
   and refresh tokens do not expire. Skip Google's "verification" for now.

## 3. OAuth client

1. **APIs & Services → Credentials → Create credentials → OAuth client ID**.
2. Application type: **Desktop app**. Name `romanfeed-local`. Create.
3. **Download JSON** → save as `secrets/client_secret.json` in the repo (git-ignored).

## 4. Mint the token locally

```bash
pip install -e ".[youtube]"
python -m romanfeed auth
```

A browser opens. Sign in with the Google account that owns the **Space
Screens** channel (if that account has several channels, pick Space Screens
when asked). Click through "Google hasn't verified this app" → Advanced →
Go to Space Screens → Allow. The token lands in `secrets/youtube.token.json`.

## 5. Give the daily workflow the token

GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**:

- Name: `YOUTUBE_TOKEN_JSON`
- Value: the full contents of `secrets/youtube.token.json`

The workflow writes it back to disk at run time. Rotate it by re-running
step 4 and updating the secret.

## 6. File the API audit (same day)

Uploads from unaudited projects are forced Private. Form:
https://support.google.com/youtube/contact/yt_api_form

What to say: a single-channel tool that renders and uploads the owner's own
videos to the owner's own channel once a day; no third-party users; scope
`youtube.upload` only; quota needs are one upload per day. Approval takes
days to weeks; nothing else is blocked while you wait.

## 7. First real upload

In `config/channels/deep-space-ambient.yaml` set `publish.mode: upload`
(leave `privacy: private`). Then:

```bash
python -m romanfeed run config/channels/deep-space-ambient.yaml --images 6 --seconds 8
```

A Private video appears in Studio within a minute. Watch it. If it is good,
set it Public in Studio by hand. Once the audit clears, change
`publish.privacy` to `public` and the loop is fully hands-off.

## Troubleshooting

- `invalid_grant` / token expired after a week → the consent screen is still
  in Testing. Publish the app (step 2.4) and re-run step 4.
- `quotaExceeded` → the project is still on the default 10,000 units/day and
  something is looping; one upload costs one Video Uploads unit, so this
  should never trigger on the daily job. Check the Actions log.
- Upload succeeds but privacy is Private though config says public → audit
  not yet approved (step 6).
