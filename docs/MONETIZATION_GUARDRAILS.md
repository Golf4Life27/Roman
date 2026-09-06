# Monetisation guardrails

These are the rules that decide whether this channel earns anything. The
pipeline enforces the ones it can; the rest are checklist items. Sources and
detail are in CHANNEL_SETUP.md.

## 1. YouTube Partner Program thresholds

- Today: 1,000 subscribers + 4,000 public watch hours in 12 months.
- **From 2027-02-01 new entrants need 8,000 watch hours.** Existing partners
  are grandfathered. Target: qualify before February 2027. With 8-hour
  videos, 4,000 hours is about 500 full watches.

## 2. Inauthentic content policy (July 2025)

Non-monetisable: "image slideshows … with minimal or no narrative, commentary,
or educational value" and "AI-generated content made with generic or
unoriginal templates giving the impression of mass production." A bare
pan-over-JPEG channel is the textbook case.

What keeps us on the right side, and what the pipeline does about it:

| Lever | Status |
|---|---|
| Per-video substance: captions with object, instrument, date; chaptered description with credits | Built (captions + chapters + credits) |
| Materially varied videos: different subject mix daily, no repeats until pool cycles | Built (ledger + seeded selection) |
| Original music composed/owned for the channel, not a library loop | **Open** — see §4 |
| Real motion design beyond Ken Burns (parallax, depth, grading) | Roadmap |
| Short spoken or text intro explaining what you're about to see | Roadmap |
| Cadence: daily is above niche norm and raises the mass-production flag | Consider daily + weekly "hero" renders |

## 3. AI disclosure

Slow pans of real telescope images: answer **No** to altered/synthetic
content. If we ever generate or inpaint sky scenes, answer **Yes** (realistic
places). AI-generated music needs no disclosure unless it imitates a real
artist. NASA additionally requires AI-altered NASA imagery to be labelled and
never carry the insignia.

## 4. Music licensing — the biggest revenue risk

- **Epidemic Sound and Artlist prohibit this format** (music against still or
  minimal visuals / passive-listening content). Do not use them.
- **Suno / Udio paid plans** assign commercial rights to output; free tiers do
  not. AI-only audio is not Content ID eligible. Usable, but generic AI tracks
  feed the mass-production test: arrange/master them, or commission.
- **Commissioned composer with a written exclusive licence** is the strongest
  position for both Content ID and "originality".
- **YouTube Audio Library** is safe for YPP members but shared with thousands
  of channels.
- **Uppbeat** paid plans safelist channels; passive-listening clause unverified.

Pipeline enforcement: every track must be in `assets/music/manifest.yaml`
with a licence of `owned | generated | licensed | cc0`; anything else
(including the synthesised placeholder) blocks upload.

## 5. Image licensing and credit

- NASA imagery: not copyrighted in the US; acknowledge NASA; must not imply
  endorsement; NASA insignia/logos/mission identifiers are **not** public
  domain. Don't put "NASA", "Roman", "Webb", "Hubble" in the channel name;
  factual use in titles/descriptions is fine.
- STScI / Webb / Hubble releases: public domain, credit NASA and STScI plus
  the per-image credit line.
- ESA/Hubble and ESA/Webb: CC BY 4.0 — credit clear, unaltered, on-screen
  and in the description; note significant modifications.

Pipeline enforcement: credit is burned into every frame's lower third and
listed in the description. Add "Not affiliated with or endorsed by NASA/ESA"
to the channel description.

## 6. API upload restriction

Uploads via the Data API from unverified projects are forced **private**.
File the YouTube API Services audit/quota form on day one and complete OAuth
consent verification. Until approved, review and flip to public in Studio
(the pipeline uploads private by default anyway).
