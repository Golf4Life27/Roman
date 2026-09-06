# YouTube API setup (one time, ~30 minutes)

Goal: a refresh token the daily workflow can use to upload without you, and
an API audit so uploads can go Public without a manual flip.

## 1. Google Cloud project

1. https://console.cloud.google.com → project picker → **New project** → name `space-screens` → Create.
2. **APIs & Services → Library** → search "YouTube Data API v3" → **Enable**.

## 2. OAuth consent screen (Google Auth Platform)

1. **Google Auth Platform → Overview → Get started**: App name `Space Screens`,
   support email; Audience **External**; contact email; agree; Create.
2. **Branding**: Google requires these before an External app can be published.
   - Application home page: `https://spacescreens.app`
   - Privacy policy: `https://spacescreens.app/privacy`
   - Terms of service: `https://spacescreens.app/terms`
   - Authorized domains → Add domain: `spacescreens.app` (Google rejects shared suffixes like vercel.app)
   - Save.
3. **Data Access → Add or remove scopes** → tick `https://www.googleapis.com/auth/youtube.upload`
   (search "youtube", or paste it under "Manually add scopes") → Update → Save.
4. **Audience → Publishing status → Publish app → Confirm** (move from *Testing* to *In production*).
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

A browser opens. **Sign in with the Google account that owns the Space
Screens channel** — check YouTube Studio → avatar → the email shown under
the channel name. It does not have to be the account that owns the Cloud
project. Authorizing any other account yields a token that uploads fail
with `youtubeSignupRequired` (the account has no channel). If the channel
is a Brand Account, pick the "Space Screens" entry at the picker, not the
email. Click through "Google hasn't verified this app" → Advanced → Go to
Space Screens → Allow. The token lands in `secrets/youtube.token.json`.

Headless alternative (no browser on the machine running the code): build
the authorization URL with `google_auth_oauthlib.flow.Flow`, open it
anywhere, and paste the resulting `http://localhost:8765/?...code=...`
address back into `flow.fetch_token(authorization_response=...)` with
`OAUTHLIB_INSECURE_TRANSPORT=1` set. Persist `flow.code_verifier` between
the two steps.

## 5. Give the daily workflow the token

GitHub repo → **Settings → Secrets and variables → Actions → New repository secret**:

- Name: `YOUTUBE_TOKEN_JSON`
- Value: the full contents of `secrets/youtube.token.json`

The workflow writes it back to disk at run time. Rotate it by re-running
step 4 and updating the secret. Done 2026-09-06.

Then prove the CI path: Actions → **Daily render** → Run workflow → mode
`smoke-test`. A short private [TEST] video appears in Studio a few minutes
later. (Scheduled daily runs stay off until the repository variable
`ROMANFEED_ENABLED` is set to `true`; do that once music is registered.)

## 6. File the API audit (same day)

Uploads from unaudited projects are forced Private. Form:
https://support.google.com/youtube/contact/yt_api_form

What to say: a single-channel tool that renders and uploads the owner's own
videos to the owner's own channel once a day; no third-party users; scope
`youtube.upload` only; quota needs are one upload per day. Approval takes
days to weeks; nothing else is blocked while you wait.

## 7. First real upload

Smoke-test the chain without touching the config (private, [TEST] title,
placeholder audio allowed):

```bash
python -m romanfeed run config/channels/deep-space-ambient.yaml --images 6 --seconds 8 --private-test
```

Done 2026-09-06: https://youtu.be/WXnwlBdhg_A (private).

For real uploads set `publish.mode: upload` in the channel config (leave
`privacy: private` until the audit clears). Licensed music is required.
Watch the first ones in Studio, set them Public by hand, and once the
audit clears change `publish.privacy` to `public` for a hands-off loop.

## Troubleshooting

- `youtubeSignupRequired` on upload → the token was minted by an account
  with no YouTube channel. Re-run step 4 signed in as the channel owner.
- `invalid_grant` / token expired after a week → the consent screen is still
  in Testing. Publish the app (step 2.4) and re-run step 4.
- `quotaExceeded` → the project is still on the default 10,000 units/day and
  something is looping; one upload costs one Video Uploads unit, so this
  should never trigger on the daily job. Check the Actions log.
- Upload succeeds but privacy is Private though config says public → audit
  not yet approved (step 6).
