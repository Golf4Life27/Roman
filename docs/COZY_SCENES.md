# Cozy scenes

Animated "cozy room with a view of the night sky" loops: a fireplace or a
wood stove flickering, steam off a mug, snow or leaves outside, stars and
aurora in the window. They are 10-second clips whose first and last frames
are the same picture, so they repeat forever without a visible jump (the
seam measures about 1.5 on a 0-255 scale, inside normal frame-to-frame motion).

They run in two places, both automatic:

| Where | When | What |
|---|---|---|
| The nightly live stream | every other night, plus every peak night | one scene, all night, under freshly composed music |
| The Saturday cozy video | weekly (switch: `cozy.enabled`) | a 3-hour video of the week's scene, intro and subscribe cards in the first minute, marked as AI-generated |

## What decides the scene

`romanfeed cozy themes` prints it. Each day has a list of themes, most
specific first; the first one with a scene wins:

1. **Peak nights**: Halloween, Christmas Eve/Day, New Year's Eve, a meteor
   shower's peak (Quadrantids, Lyrids, Eta Aquariids, Perseids, Orionids,
   Leonids, Geminids), an eclipse, a notable rocket launch (crewed flights,
   Artemis, Starship, Moon/Mars missions, the Roman telescope; dates from
   Launch Library 2, free, no key).
2. **Sky events** around those peaks (the night before and after).
3. **Holidays**: Halloween (all October), Thanksgiving (Nov 1 to the day),
   Christmas (Dec 1–27), New Year, Valentine's, Easter week, Fourth of July.
4. **The season**, then **evergreen** (any time).

Within a theme the scenes take turns, and last night's is skipped.

## Where the scenes live

The `cozy-scenes` release on this repo: one `<id>.mp4` per scene and
`scenes.json` listing their titles and theme tags. Public, so the live server
downloads them with no token.

## New scenes, every month, by themselves

`.github/workflows/cozy-scenes.yml` runs on the 1st. It looks 45 days ahead,
finds the themes with fewer than 2 scenes (1 for a launch or an eclipse),
soonest first, and makes new ones with the Runway developer API:

- a still from a prompt (`romanfeed/cozy/prompts.py`: 8 rooms × 15 themes),
  20 credits
- a 10-second loop from it, first frame = last frame, 400 credits

That is ~420 credits ($4.20) a scene. `cozy.monthly_credits` caps the month
(2000 = $20, about 4 scenes); a failed or moderated attempt counts against
the cap too. Developer API credits are separate from the Runway app plan:
prepaid at dev.runwayml.com, $10 minimum.

**One-time setup (the owner):** create an API key at dev.runwayml.com,
prepay credits, and save the key as the repository secret
`RUNWAYML_API_SECRET`. Until then the monthly run prints its plan and
spends nothing.

## Adding a scene by hand

Make it in the Runway app (image, then a 10 s video with the same image as
start and end frame), then run **Cozy scenes → import** with:

```json
[{"id": "summer-dock", "title": "Summer Dock", "themes": ["summer"], "url": "https://...mp4"}]
```

Theme keys: halloween, thanksgiving, autumn, christmas, new_year, winter,
valentines, easter, spring, summer, independence_day, meteors, eclipse,
launch, evergreen.

## Another channel

Everything is in the channel yaml (`live.scenes`, the `cozy:` block) and the
prompt tables. A new niche needs its own rooms and decor in `prompts.py`
(or a copy of it) and its own release tag (`cozy.release_tag`).
