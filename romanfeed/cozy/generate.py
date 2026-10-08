"""The monthly job that keeps the scene library ahead of the calendar.

Looks `lookahead_days` ahead, lists every theme those days will ask for
(holidays, sky events, launches, the season, evergreen), and makes new scenes
for the soonest themes short of `per_theme`, until the month's credit cap in
`cozy.monthly_credits` is reached. Each scene: one still (Runway text to
image), one 10-second loop from it (first frame = last frame), both attached
to the release, the manifest updated after every scene so a failure halfway
loses nothing already paid for.

`import_scenes` is the other way in: loops made elsewhere (by hand in the
Runway app) handed over as URLs, e.g. the six starter scenes.
"""
from __future__ import annotations

import json
import logging
import tempfile
from datetime import date
from pathlib import Path

import requests

from romanfeed.config import ChannelConfig
from romanfeed.cozy import scenes as sc
from romanfeed.cozy.calendar import SEASONS, fetch_launches, upcoming
from romanfeed.cozy.prompts import DECOR, ROOMS, ScenePrompt, scene_prompt
from romanfeed.render import ffmpeg

log = logging.getLogger(__name__)


def wanted(library: sc.Library, start: date, *, days: int, per_theme: int, launches=None) -> list[str]:
    """Theme keys to make a scene for, soonest first, one entry per missing scene."""
    up = upcoming(start, days, launches=launches)

    def rank(key: str) -> int:  # the fallbacks only matter where nothing specific exists
        return 2 if key == "evergreen" else 1 if key in SEASONS.values() else 0

    out = []
    for key, dates in sorted(up.items(), key=lambda kv: (rank(kv[0]), kv[1][0])):
        if key not in DECOR:
            continue
        # One-off events (a launch, an eclipse) get one scene; recurring themes per_theme.
        target = 1 if key in {"launch", "eclipse"} else per_theme
        out += [key] * max(0, target - len(library.tagged(key)))
    return out


def next_prompt(library: sc.Library, theme: str) -> ScenePrompt:
    """The first room not yet made for this theme (ids are theme-room)."""
    taken = {s.id for s in library.scenes}
    for i in range(len(ROOMS)):
        p = scene_prompt(theme, i)
        if p.id not in taken:
            return p
    p = scene_prompt(theme, len(taken))
    n = 2
    while f"{p.id}-{n}" in taken:
        n += 1
    return ScenePrompt(f"{p.id}-{n}", p.title, p.themes, p.image, p.motion)


def _download(url: str, dest: Path) -> Path:
    with requests.get(url, stream=True, timeout=300) as r:
        r.raise_for_status()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
    return dest


def _check_loop(path: Path) -> None:
    d = ffmpeg.probe_duration(str(path))
    if not 4 <= d <= 16:
        raise ValueError(f"{path.name}: {d:.1f} s is not a scene loop")


def _publish_manifest(release, library: sc.Library, work: Path) -> None:
    path = work / sc.MANIFEST
    sc.save(library, path)
    release.upload(path, sc.MANIFEST)


def generate(cfg: ChannelConfig, *, today: date, dry_run: bool = True, client=None, release=None,
             max_scenes: int | None = None) -> list[sc.Scene]:
    from romanfeed.cozy import runway

    c = cfg.cozy
    work = Path(tempfile.mkdtemp(prefix="cozy-gen-"))
    library = sc.fetch_manifest(c.repo, c.release_tag, work / sc.MANIFEST)
    month = today.strftime("%Y-%m")
    spent = library.spend.get(month, 0)
    cost = runway.scene_cost(c.image_model, c.video_model)
    todo = wanted(library, today, days=c.lookahead_days, per_theme=c.per_theme,
                  launches=fetch_launches(cache=work / "launches.json"))
    print(f"{len(library.scenes)} scenes in the library; {len(todo)} wanted: {', '.join(todo) or 'none'}")
    print(f"budget {month}: {spent} of {c.monthly_credits} credits spent; ~{cost} per scene")
    if not dry_run and client is None:
        secret = runway.api_secret()
        if not secret:
            print("RUNWAYML_API_SECRET is not set: nothing generated (see docs/COZY_SCENES.md)")
            return []
        client = runway.Client(secret)
    made: list[sc.Scene] = []
    failures = 0
    for theme in todo:
        if max_scenes is not None and len(made) >= max_scenes:
            break
        if spent + cost > c.monthly_credits:
            print(f"stopping: the next scene would pass the {c.monthly_credits}-credit monthly cap")
            break
        p = next_prompt(library, theme)
        if dry_run:
            print(f"would make {p.id} ({p.title}) for {theme}\n  image:  {p.image}\n  motion: {p.motion}")
            library.scenes.append(sc.Scene(p.id, "", p.title, p.themes))  # in memory only: no repeats
            spent += cost
            continue
        if client is not None and (bal := client.balance()) is not None and bal < cost:
            print(f"stopping: Runway API balance {bal} is below one scene ({cost}); top up at dev.runwayml.com")
            break
        try:
            img_url = client.image(p.image, model=c.image_model)
            img = work / f"{p.id}.png"
            _download(img_url, img)
            vid_url = client.loop(runway.data_uri(img.read_bytes()), p.motion, model=c.video_model)
            mp4 = _download(vid_url, work / f"{p.id}.mp4")
            _check_loop(mp4)
        except Exception as exc:  # moderation, outage: paid or not, move on and say so
            failures += 1
            spent += cost  # a moderated generation is charged too; count it against the cap
            library.spend[month] = spent
            log.error("scene %s failed: %s", p.id, exc)
            if failures >= 2:
                print("stopping after two failed scenes")
                break
            continue
        release.upload(mp4, f"{p.id}.mp4")
        scene = sc.Scene(p.id, f"{p.id}.mp4", p.title, p.themes, today.isoformat(),
                         f"runway {c.image_model} + {c.video_model}", p.image, p.motion)
        library.scenes.append(scene)
        spent += cost
        library.spend[month] = spent
        _publish_manifest(release, library, work)
        made.append(scene)
        print(f"made {scene.id}: {scene.title}")
    if not dry_run and release is not None and failures:
        _publish_manifest(release, library, work)  # record the spend of failed attempts too
    return made


def import_scenes(cfg: ChannelConfig, items: list[dict], *, release, today: date) -> list[sc.Scene]:
    """Add loops made elsewhere. Each item: id, url, title, themes, and
    optionally prompt, motion, source. An existing id is replaced."""
    c = cfg.cozy
    work = Path(tempfile.mkdtemp(prefix="cozy-import-"))
    library = sc.fetch_manifest(c.repo, c.release_tag, work / sc.MANIFEST)
    added = []
    for it in items:
        mp4 = _download(it["url"], work / f"{it['id']}.mp4")
        _check_loop(mp4)
        release.upload(mp4, f"{it['id']}.mp4")
        scene = sc.Scene(it["id"], f"{it['id']}.mp4", it["title"], list(it["themes"]), today.isoformat(),
                         it.get("source", "runway app"), it.get("prompt", ""), it.get("motion", ""))
        library.scenes = [s for s in library.scenes if s.id != scene.id] + [scene]
        added.append(scene)
        print(f"imported {scene.id}: {scene.title} [{', '.join(scene.themes)}]")
    _publish_manifest(release, library, work)
    return added


def load_items(text: str) -> list[dict]:
    items = json.loads(text)
    for it in items:
        missing = {"id", "url", "title", "themes"} - set(it)
        if missing:
            raise ValueError(f"scene {it.get('id', '?')}: missing {sorted(missing)}")
    return items
