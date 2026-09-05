# Space Screens site

Three static pages (home, privacy policy, terms of service) deployed to
Vercel in the existing `roman` project (root directory `site/`).

Live URLs (2026-09-05):

- https://roman-orpin.vercel.app
- https://roman-orpin.vercel.app/privacy
- https://roman-orpin.vercel.app/terms

The `roman-golf4life27s-projects.vercel.app` alias sits behind Vercel
Authentication by default; use the `roman-orpin` domain for anything public. Google's OAuth consent screen requires a
home page and privacy policy URL before the app can be published to
production, and the API audit form asks for them again.

Redeploy after edits: pushes to `main` deploy automatically through the
Vercel GitHub integration (project root is `site/`). Manual alternative:

```bash
npx vercel deploy --prod
```

Custom domain: add it in the Vercel project → Domains, then update the
Branding page in Google Auth Platform and the YouTube channel link.
