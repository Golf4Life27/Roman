# Channel setup: name and YouTube Studio settings

Research date: 2026-09-05. Handle availability was checked by HTTP status of
`youtube.com/@handle` and must be re-confirmed in Studio before committing.

## Context that changed the plan

Roman **launched 2026-08-30** on a Falcon Heavy. It is on a ~3-month cruise to
L2 and NASA expects first observations **by early 2027**
([NASA](https://www.nasa.gov/news-release/nasas-dark-universe-seeking-nancy-grace-roman-space-telescope-launches/)).
Hubble and Webb carry the channel for 4–6 months; Roman's first images become a
launch-moment content event on a channel that is already monetised.

## 1. Channel name

### What wins in this niche

| Channel | Subs | Pattern |
|---|---|---|
| Ambient Worlds | 1.19M | Brandable two-word name; keywords live in titles |
| Soothing Relaxation | 12M | Two-word brand, 8 h+ versions |
| Relaxation Ambient Music | 458K | Pure keyword name; inactive 3 yrs, still 100–460K views/video |
| SpaceAmbient | 112K | Keyword name, short single tracks |
| Relaxation Time | 110K | "4K OLED Screensaver" titles; 1–6K views/video — "screensaver" alone is saturated |
| Space Relax Music Channel | 65K | Keyword name; 3 h / 8 h alternating |

The big winners use a **short, brandable two-word name** and put search
keywords in titles. Pure-keyword names top out around 60–460K and get cloned.

### Naming constraints

- NASA imagery is public domain, but "the NASA Insignia, Logotype,
  identifiers, and imagery are not in the public domain" and commercial use
  must not imply endorsement ([NASA Brand Center](https://www.nasa.gov/nasa-brand-center/images-and-media/)).
  "Identifiers" covers mission names. A channel called "Roman Space Telescope
  Ambient" implies affiliation.
- YouTube's impersonation/trademark policies bar names that confuse viewers
  about the source ([policy](https://support.google.com/youtube/answer/2801947?hl=en)).
- ESA/Hubble and ESA/Webb logos need written consent ([ESA/Hubble](https://esahubble.org/copyright/)).
- "Roman" alone is generic (Roman Empire, Roman Reigns) and loses in search.
- Keep "Roman", "Webb", "Hubble" in **titles and descriptions** (factual use),
  not in the channel name. The repo can stay "RomanFeed"; that's internal.

### Shortlist (✅ = handle free on 2026-09-05)

| # | Name | Handle | Why |
|---|---|---|---|
| **1** | **Deep Field Screens** | @DeepFieldScreens ✅ | "Deep field" is the shared Hubble/Webb/Roman survey term; telescope-agnostic; "Screens" states the use. Siblings: @DeepFieldSleep ✅, Deep Field Piano, Deep Field Lofi |
| **2** | **Telescope Sleep** | @TelescopeSleep ✅ | Sleep intent + literal subject; outlives any mission; siblings Telescope Focus / Telescope Piano |
| **3** | **Deep Sky Drift** | @DeepSkyDrift ✅ | "Deep sky" = astronomer's term for nebulae/galaxies; "drift" = the slow pan; brandable |
| 4 | Telescope Screens | @TelescopeScreens ✅ | Most literal; pairs with Telescope Sleep |
| 5 | Cosmos Sleep Screen | @CosmosSleepScreen ✅ | Keyword stack; clone-prone |
| 6 | Universe Screens | @UniverseScreens ✅ | Broad, generic |
| 7 | Infrared Drift | @InfraredDrift ✅ | Nod to Webb/Roman being IR; too niche for search |
| 8 | Long Exposure Ambient | @LongExposureAmbient ✅ | Clever, long |
| 9 | Far Light Ambient | @FarLightAmbient ✅ | Evocative; weak search |
| 10 | Starfield Ambient | @StarfieldAmbient ✅ | Collides with Bethesda's *Starfield* game; avoid |

Taken: @SpaceAmbient, @DeepFieldAmbient, @OrbitalAmbient, @NebulaSleep,
@StellarDrift, @QuietCosmos, @SlowCosmos, @StillSpace, @SleepCosmos,
@SpaceDrift, @RomanFeed.

**Recommendation: Deep Field Screens.** Register @DeepFieldScreens and the
sibling handles (@DeepFieldSleep at minimum) the same day.

## 2. YouTube Studio settings checklist

### Identity (Customization / Settings → Channel)

- [ ] Handle and name from above; country: US.
- [ ] Channel keywords: `space ambient, sleep music, sleep screen, 4K screensaver, Hubble, James Webb, Roman Space Telescope, focus music, study music, relaxing space`.
- [ ] Description: lead with the use case ("Long 4K sleep screens of real Hubble, Webb and Roman telescope imagery with original ambient music"), then a standing credit block: "Imagery: NASA, ESA, CSA, STScI; ESA/Hubble and ESA/Webb (CC BY 4.0). Not affiliated with or endorsed by NASA or ESA."
- [ ] Audience (Advanced settings): **No, not made for kids**.
- [ ] Banner 2560×1440 (≤6 MB, min 2048×1152), all text inside the 1546×423 safe area; avatar 800×800.
- [ ] Branding watermark (small telescope glyph), shown for the entire video.
- [ ] Featured sections: "Sleep (8–10 h)", "Focus (1–3 h)", "By telescope" playlists.
- [ ] Channel trailer: a 1 h video (autoplays with sound for non-subscribers).

### Upload defaults (Settings → Upload defaults)

- [ ] Privacy: **Private** (API uploads are forced private until the audit anyway); publish via scheduled `videos.update` or Studio.
- [ ] Category: Music, or Science & Technology (pipeline default is 28 = Science & Technology; switch in config if Music tests better).
- [ ] License: **Standard YouTube License** (not Creative Commons, or others can reuse the renders).
- [ ] Language: English; title/description language English.
- [ ] Comments: "Hold potentially inappropriate comments for review" + "Increase strictness"; block links; blocked-words list for spam/crypto terms. An unattended channel can also "Hold all".
- [ ] Altered content: **No** for slow pans of real telescope images. Disclosure is required for realistic content a viewer "could easily mistake for a real person, place, scene, or event" ([YouTube](https://support.google.com/youtube/answer/15447836?hl=en)); AI music needs no disclosure unless it imitates a real artist. If we ever generate or inpaint sky scenes: **Yes**.
- [ ] Made for kids: No.
- [ ] Default description: credits block + "No mid-roll ads" if mid-rolls are disabled.

### YouTube Data API v3

- [ ] Google Cloud project → enable YouTube Data API v3 → OAuth client (Desktop) → `secrets/client_secret.json`.
- [ ] Complete OAuth consent-screen verification (`youtube.upload` is a sensitive scope).
- [ ] **File the "YouTube API Services – Audit and Quota Extension Form" on day one.** Uploads from unverified projects created after 2020-07-28 are restricted to private ([videos.insert](https://developers.google.com/youtube/v3/docs/videos/insert)). Until approved, flip videos public in Studio.
- Quota: `videos.insert` costs 1 unit of the Video Uploads bucket (100/day); general pool 10,000 units/day. One upload a day is trivial.

## 3. Format benchmarks

- **Length:** Ambient Worlds uploads run exactly 3:00:01 or 6:00:01; Space Relax alternates 3 h and 8 h; Relaxation Time pairs a 1 h cut with a ~12 h cut of the same asset. Sleep-niche playbook: publish each asset set at 1 h / 3 h / 8 h / 10 h as separate videos.
- **Cadence:** top channels post every 4–30 days. Daily is above norm and raises the mass-production flag; daily uploads plus weekly "hero" renders is the compromise.
- **Title patterns:** `[Evocative name] – Deep Space Ambient Music for Sleep & Meditation 🌌`; `Franchise | 🌙 Place, Peaceful Music & Ambience in 4K, Human-Made, No Mid-roll Ads`. Trust tokens seen in top titles: "4K UHD", "No Mid-roll Ads", "Human-Made", "Black Screen". Suggested: `Webb: Pillars of Creation | 8 Hour 4K Sleep Screen, Original Ambient, No Mid-rolls`.
- **Thumbnails:** full-bleed telescope image, one large duration badge ("8 HOURS"), optional "4K" pill, no faces.
- **Black-screen trick:** fade to black at 30–60 min on sleep cuts (Soothing Relaxation has a whole "Fade To Black Screen" playlist); keep the image on focus cuts.
- **24/7 live stream** for always-on watch hours once the library exists.
- **4K/HDR:** YouTube recommends 2160p SDR at 35–45 Mbps, HDR 44–56 Mbps, AAC 384 kbps. Telescope images are ideal HDR sources (deep blacks), but an 8 h 4K upload is ~160 GB; budget bandwidth and runner disk.
- **RPM:** third-party estimates put sleep/soundscape RPM near $11. Indicative only.

## Sources

- https://www.nasa.gov/nasa-brand-center/images-and-media/
- https://esahubble.org/copyright/ · https://esawebb.org/copyright/ · https://www.stsci.edu/copyright
- https://support.google.com/youtube/answer/2801947 · /1311392 · /72851 · /13429240 · /12843009 · /15447836 · /2660027 · /9483359 · /1722171 · /3376882
- https://blog.youtube/news-and-events/disclosing-ai-generated-content/
- https://developers.google.com/youtube/v3/docs/videos/insert · https://developers.google.com/youtube/v3/guides/quota_and_compliance_audits
- https://help.epidemicsound.com/hc/en-us/articles/26254496789266 · https://help.artlist.io/hc/en-us/articles/29490991524253 · https://uppbeat.io/commercial-licenses
- https://suno.com/terms · https://terms.law/ai-output-rights/suno/
- https://videos.feedspot.com/ambient_music_youtube_channels/ · https://blog.youtube/culture-and-trends/ambient-music/ · https://vidiq.com/youtube-stats/channel/@ambientworlds/
- https://www.socialmediatoday.com/news/youtube-clarifies-monetization-update-inauthentic-repeated-content/752892/
- Channel stats read from youtube.com/@AmbientWorlds, @RelaxationMeditationMusic, @SpaceAmbient, @SpaceRelaxMusicChannel, @Relaxation_Time on 2026-09-05.

Unverified: Uppbeat's passive-listening clause; any USPTO filing for "Roman Space Telescope".
