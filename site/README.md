# Space Screens site

Three static pages (home, privacy policy, terms of service) deployed to
Vercel as project `space-screens`. Google's OAuth consent screen requires a
home page and privacy policy URL before the app can be published to
production, and the API audit form asks for them again.

Redeploy after edits:

```bash
npx vercel deploy --prod site
```

Custom domain: add it in the Vercel project → Domains, then update the
Branding page in Google Auth Platform and the YouTube channel link.
