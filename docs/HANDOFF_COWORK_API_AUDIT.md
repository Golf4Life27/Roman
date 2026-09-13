# Handoff to Cowork: YouTube API audit → fully hands-off uploads

Owner: Alex. Repo: github.com/Golf4Life27/Roman (branch `main`).

**Goal:** get the Google Cloud project audited so uploads stop being forced
Private, then switch the channel to publish Public automatically.

This is the last recurring manual step in the pipeline. Everything else
already runs unattended on GitHub Actions.

## Why this is needed

Uploads made through the YouTube Data API from an **unaudited** project are
forced to Private regardless of what the code requests
([videos.insert docs](https://developers.google.com/youtube/v3/docs/videos/insert)).
So right now every video lands Private and a human has to flip it Public in
Studio. The audit removes that.

Nothing is blocked while the audit is pending — the channel keeps rendering
and uploading on schedule. Approval typically takes days to weeks.

## Facts you will need on the form

| Field | Value |
|---|---|
| Google Cloud project name | `space-screens` |
| Project number | `171265576463` |
| OAuth client ID | `171265576463-gfefm6g3qcvjm24colnbeet5o3o9nnel.apps.googleusercontent.com` |
| Contact / consent-screen email | `akb.solutions.office@gmail.com` |
| YouTube channel | Space Screens — https://www.youtube.com/@SpaceScreens |
| Channel owner account | `alex@akb-properties.com` |
| Website | https://spacescreens.app |
| Privacy policy | https://spacescreens.app/privacy |
| Terms of service | https://spacescreens.app/terms |
| Scope requested | `https://www.googleapis.com/auth/youtube.upload` (this one only) |
| Consent screen status | External, **In production**, unverified (no logo uploaded) |
| Domain ownership | Verified in Search Console via HTML file |

**Actual API usage — state this accurately, do not say "once a day":**

- Schedule: Mon/Wed/Fri at 03:00 UTC (`.github/workflows/daily.yml`)
- Each run uploads **2 videos**: a 1-hour cut and an 8-hour cut of the same
  imagery
- So **6 `videos.insert` calls per week**, ~26 per month
- `videos.insert` costs 1 unit of the Video Uploads bucket (limit 100/day);
  the general 10,000 units/day pool is untouched
- **No quota increase is being requested** — only the audit, to lift the
  forced-Private restriction

## What to write in the description

Substance to convey, in your own words:

> Space Screens is a single-channel tool that renders long-form ambient
> videos from public-domain and CC BY astronomy imagery (NASA image library,
> ESA/Hubble, ESA/Webb) and uploads them to the owner's own YouTube channel
> on a fixed schedule. It is operated solely by the channel owner. It has no
> third-party users and no sign-up; the only account that ever authorises it
> is the channel owner's. It requests the `youtube.upload` scope only, and
> uses it only to call `videos.insert` with the video file, title,
> description and tags. It does not read or modify any other YouTube data.
> Source: https://github.com/Golf4Life27/Roman

If the form asks about data handling: no end-user data is collected or
stored; the only stored credential is the owner's own OAuth refresh token,
held in an encrypted GitHub Actions secret. That matches
https://spacescreens.app/privacy.

## Steps

1. Sign in as **`akb.solutions.office@gmail.com`** (the Cloud project owner —
   not the channel-owner account).
2. Open https://support.google.com/youtube/contact/yt_api_form
3. Fill it using the table and description above.
4. **Do NOT** submit the app for OAuth *verification* in Google Auth Platform
   → Verification Center. That is a different, heavier review (demo video,
   logo, reviewer) and is not required here. The app works In production
   unverified. If a screen pushes toward it, back out.
5. Save the confirmation/reference number somewhere Alex can find it, and
   tell him it was filed and what the reference is.

## After approval lands

Google replies by email to the contact address. Once approved:

1. Edit `config/channels/deep-space-ambient.yaml`, change
   `publish.privacy: private` → `publish.privacy: public`.
2. Commit on a branch, open a PR to `main`, wait for CI green, merge.
3. On the next Mon/Wed/Fri run, confirm in Studio that the new video is
   Public without anyone touching it. That is the finish line: the loop is
   then fully hands-off.

Leave `publish.mode: upload` alone — it is already correct.

## Related, still open (ask Alex before doing)

**Move the channel to a Brand Account.** The channel is currently owned by
the personal account `alex@akb-properties.com`, so it is tied to one login.
YouTube Settings → Advanced settings → "Move channel to a Brand Account",
then add `akb.solutions.office@gmail.com` as an Owner. Best done while the
channel is small. This does not affect the API audit either way.
