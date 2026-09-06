# Space Screens site

Three static pages (home, privacy policy, terms of service) deployed to
Vercel in the existing `roman` project (root directory `site/`).

Live URLs (custom domain spacescreens.app bought via Vercel 2026-09-05):

- https://spacescreens.app
- https://spacescreens.app/privacy
- https://spacescreens.app/terms

Fallback Vercel domain: https://roman-orpin.vercel.app (the
`roman-golf4life27s-projects` alias sits behind Vercel Authentication). Google's OAuth consent screen requires a
home page and privacy policy URL before the app can be published to
production, and the API audit form asks for them again.

Redeploy after edits: pushes to `main` deploy automatically through the
Vercel GitHub integration (project root is `site/`). Manual alternative:

```bash
npx vercel deploy --prod
```

Domain management: Vercel project → Domains. Google Auth Platform's
authorized domain is `spacescreens.app`.
