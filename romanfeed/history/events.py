"""Today's events for the history channel, from Wikipedia's "On this day" feed.

Two lists per date: `selected` (picked by Wikipedia's editors for the Main
Page) and `events` (everything). An event qualifies only if:

  - it is not about war, death, disaster or crime (BLOCK below). Those are
    the stories most likely to get a Short age-restricted or demonetised,
    and the channel is about the days that changed things for the better
    or the stranger;
  - one of its linked articles has a lead image on Wikimedia Commons whose
    licence is public domain or CC0, checked file by file (a "free" photo
    on Wikipedia is often CC BY-SA, which a video cannot carry cleanly).

Facts are not copyrighted; the article text is (CC BY-SA). Scripts are
written fresh from these facts (romanfeed/history/script.py) and the post
credits Wikipedia as the source.
"""
from __future__ import annotations

import logging
import re
import time
import urllib.parse
from dataclasses import dataclass, field
from datetime import date

import requests

log = logging.getLogger(__name__)

FEED = "https://en.wikipedia.org/api/rest_v1/feed/onthisday/{kind}/{mm:02d}/{dd:02d}"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"
# Wikimedia asks every client to identify itself and how to reach its owner.
USER_AGENT = "RomanfeedHistory/1.0 (https://github.com/Golf4Life27/Roman)"

BLOCK = re.compile(
    r"\b(wars?|warfare|battles?|killed|kills|killing|dead|deaths?|died|dies|bomb\w*|attack\w*|massacre\w*|shot|shoots?|"
    r"shooting|assassinat\w*|murder\w*|crash\w*|disasters?|sinks?|sank|sunk|explosions?|explodes?|earthquakes?|floods?|"
    r"flooding|hurricanes?|cyclones?|tsunamis?|fires?|executed|execution|genocide|holocaust|terror\w*|coup|riots?|"
    r"invasions?|invades?|invaded|sieges?|hostages?|kidnap\w*|suicide|plague|epidemic|pandemic|slave\w*|lynch\w*|"
    r"torpedo\w*|hijack\w*|injur\w*|wounded|victims?|casualties|arrested|convicted|prison\w*|nazis?|extermination|"
    r"famine|avalanches?|soldiers?|troops|weapons?|missiles?|nuclear test|strike|strikes|protest\w*|"
    r"ripper|serial killer|criminals?|crimes?|police|gangs?|robbery|robbed|theft|stolen)\b",
    re.I,
)
# Divisive or grim-by-another-name: politics, courts and scandals make poor
# bedtime-friendly Shorts and invite comment wars.
POLITICS = re.compile(
    r"\b(party|politburo|communist|regime|puppet|cabinet|annex\w*|referendum|trials?|scandal|dismissed|"
    r"withdraws?|socialist republic|converts?|caste|caliph|denounces?|conspiracy|revolutionary)\b", re.I)
# What makes a good minute: someone did something first, fastest, or new.
INTEREST = re.compile(
    r"\b(first|invent\w*|discover\w*|patent\w*|records?|breaks?|broke|launch\w*|lands?|landed|opens?|opened|"
    r"publish\w*|premieres?|broadcast\w*|films?|filmed|books?|world series|nobel|prize|flights?|flies|flew|"
    r"founded|completed|unveil\w*|summit|expedition|telescope|space|moon|orbit\w*|satellite|computer|radio|"
    r"television|electric\w*|telephone|photograph\w*|olympic\w*|championship|sound barrier|fastest|tallest)\b", re.I)
# Not photographs of the thing: flags, logos, maps, emblems, drawings of signs.
NOT_A_PHOTO = re.compile(r"(flag|logo|map|coat[_ ]of[_ ]arms|seal|emblem|icon|signature|locator|insignia|\.svg$)",
                         re.I)
# Public domain in the US only (expired US copyright, no notice, not
# renewed): the work can still be protected abroad (E. H. Shepard's 1928 Pooh
# drawings are, in the UK, until 2046), and the videos go out worldwide.
US_ONLY_PD = re.compile(r"PD.?US.?(expired|not.?renewed|no.?notice|1923|1926|1929)|PD.?1996|PD.?US.?unpublished|"
                        r"non.?free|fair use", re.I)
FREE_LICENCES = ("public domain", "pd", "cc0", "cc-zero", "no restrictions")


@dataclass
class Image:
    file: str          # "File:Bell X-1 46-062 (in flight).jpg"
    url: str           # a ~1600 px rendering to download
    credit: str        # "NASA", "United States Air Force"
    licence: str       # "Public domain"


@dataclass
class Event:
    year: int
    text: str
    selected: bool                         # on Wikipedia's Main Page list for the date
    pages: list[dict] = field(default_factory=list)   # title, extract, image source
    image: Image | None = None

    @property
    def key(self) -> str:
        return f"{self.year}:{self.pages[0]['title'] if self.pages else self.text[:40]}"

    def source_text(self) -> str:
        """Everything the script may draw facts from: the event line, the full
        text of its main articles (enrich()) and the summaries of the rest."""
        parts = [f"{self.year}: {self.text}"]
        parts += [f"{p['title']}: {p.get('text') or p['extract']}" for p in self.pages
                  if p.get("text") or p.get("extract")]
        return "\n\n".join(parts)


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
    return s


def _get(session, url: str, *, params=None, tries: int = 4):
    """GET with backoff: Wikimedia answers 429 when asked too quickly (often
    on GitHub's shared runners). Honours Retry-After, says so in the log, and
    gives up after about two minutes rather than stalling the run."""
    for attempt in range(tries):
        r = session.get(url, params=params, timeout=30)
        if r.status_code != 429 and r.status_code < 500:
            r.raise_for_status()
            return r
        if attempt == tries - 1:
            break
        wait = 5 * 2 ** attempt
        try:
            wait = max(wait, int(r.headers.get("retry-after", "0")))
        except (TypeError, ValueError, AttributeError):
            pass
        wait = min(wait, 60)
        log.warning("Wikimedia answered HTTP %s for %s; retrying in %d s", r.status_code, url.split("?")[0], wait)
        time.sleep(wait)
    r.raise_for_status()
    return r


def fetch(day: date, session=None) -> list[Event]:
    """Every event for the date (any year), selected ones first."""
    session = session or _session()
    out: dict[str, Event] = {}
    for kind in ("selected", "events"):
        data = _get(session, FEED.format(kind=kind, mm=day.month, dd=day.day)).json()
        for e in data.get(kind, []):
            pages = [{"title": p.get("titles", {}).get("normalized") or p.get("title", ""),
                      "extract": p.get("extract", ""),
                      "image": (p.get("originalimage") or {}).get("source", "")} for p in e.get("pages", [])]
            ev = Event(int(e["year"]), e["text"], kind == "selected", pages)
            out.setdefault(f"{ev.year}:{ev.text[:60].lower()}", ev)
    return list(out.values())


def safe(event: Event) -> bool:
    """No war, death, disaster or crime in the event itself. Only the event
    line is judged: the linked articles add background (Apollo 7's mentions
    the Apollo 1 fire) that would sink good stories."""
    return not BLOCK.search(event.text) and not POLITICS.search(event.text)


def interest(event: Event) -> int:
    return len(INTEREST.findall(event.text))


def commons_file(url: str) -> str | None:
    """'File:Name.jpg' for a Commons image URL (original or thumbnail); None
    for files hosted on English Wikipedia itself, which are usually non-free."""
    if "/wikipedia/commons/" not in url:
        return None
    path = urllib.parse.urlparse(url).path.split("/")
    if "thumb" in path:
        name = path[path.index("thumb") + 3]
    else:
        name = path[-1]
    return "File:" + urllib.parse.unquote(name).replace("_", " ")


def licences(files: list[str], session=None) -> dict[str, Image]:
    """Free-to-use images among `files`, keyed by file title."""
    session = session or _session()
    out: dict[str, Image] = {}
    for i in range(0, len(files), 40):
        r = _get(session, COMMONS_API, params={
            "action": "query", "titles": "|".join(files[i:i + 40]),
            "iiprop": "extmetadata|url|size", "iiurlwidth": 1600, "format": "json", "formatversion": 2,
            "prop": "imageinfo|categories", "cllimit": "max",
        }).json()
        for p in r.get("query", {}).get("pages", []):
            cats = " ".join(c.get("title", "") for c in p.get("categories", []))
            if US_ONLY_PD.search(cats):
                continue
            info = (p.get("imageinfo") or [{}])[0]
            meta = info.get("extmetadata") or {}
            lic = re.sub("<[^>]+>", "", meta.get("LicenseShortName", {}).get("value", "")).strip()
            if not lic.lower().startswith(FREE_LICENCES) or NOT_A_PHOTO.search(p["title"]):
                continue
            if info.get("width", 0) < 600:
                continue
            credit = re.sub("<[^>]+>", "", meta.get("Artist", {}).get("value", "")).strip()
            credit = re.sub(r"\s+", " ", credit)
            credit = re.sub(r"^(Unknown author)+$", "Unknown author", credit)[:80]
            if not credit or "uploaded" in credit.lower() or credit.lower().startswith("unknown"):
                credit = "Wikimedia Commons"
            out[p["title"]] = Image(p["title"], info.get("thumburl") or info.get("url", ""), credit, lic)
    return out


def enrich(event: Event, session=None, *, chars: int = 15000) -> Event:
    """Add the full plain text of the event's main articles (the "(pictured)"
    one and the first link): summaries leave out the details worth saying."""
    session = session or _session()
    text = event.text.lower()
    main = [p for p in event.pages if p["title"] and f"{p['title'].lower()} (pictured)" in text]
    main += [p for p in event.pages[:1] if p not in main]
    if not main:
        return event
    r = _get(session, "https://en.wikipedia.org/w/api.php", params={
        "action": "query", "prop": "extracts", "explaintext": 1, "format": "json", "formatversion": 2,
        "titles": "|".join(p["title"] for p in main),
    }).json()
    by_title = {pg.get("title", ""): pg.get("extract", "") for pg in r.get("query", {}).get("pages", [])}
    for p in main:
        body = by_title.get(p["title"], "")
        # The References/See also tail is not narration material.
        body = re.split(r"\n==\s*(See also|References|Notes|External links|Further reading)\s*==", body)[0]
        p["text"] = body[:chars]
    return event


def image_files(event: Event) -> list[str]:
    """Commons files for the event, best first: the article the event line
    marks "(pictured)", then its first link (the subject). Later links are
    background (a road type, the stratosphere) whose picture would mislead."""
    text = event.text.lower()
    pictured = [p for p in event.pages if p["title"] and f"{p['title'].lower()} (pictured)" in text]
    order = pictured + [p for p in event.pages[:1] if p not in pictured]
    return [f for f in (commons_file(p["image"]) for p in order if p.get("image")) if f]


def pick(day: date, n: int = 3, *, events: list[Event] | None = None, session=None,
         skip: set[str] | None = None) -> list[Event]:
    """Up to `n` safe events with a free image, spread across the centuries.
    Main Page picks and "firsts" rank first; ties break differently each year
    so the same date does not repeat last year's three. Images are checked a
    few candidates at a time (Wikimedia rate-limits big lookups). `skip`
    holds keys already posted."""
    session = session or _session()
    events = events if events is not None else fetch(day, session)
    pool = [e for e in events if safe(e) and e.key not in (skip or set())]
    pool.sort(key=lambda e: (-(2 * e.selected + min(interest(e), 3)), (e.year * 7919 + day.year) % 997))
    chosen: list[Event] = []
    spare: list[Event] = []
    for i in range(0, len(pool), 8):
        batch = pool[i:i + 8]
        files = {e.key: image_files(e) for e in batch}
        wanted = sorted({f for fs in files.values() for f in fs})
        free = licences(wanted, session) if wanted else {}
        for e in batch:
            e.image = next((free[f] for f in files[e.key] if f in free), None)
            if not e.image:
                continue
            if len(chosen) < n and all(abs(e.year - c.year) >= 15 for c in chosen):
                chosen.append(e)   # 1888, 1947, 1968 -- not 1966 and 1968
            else:
                spare.append(e)
        if len(chosen) == n:
            break
        time.sleep(1)
    for e in spare:   # a thin day: fill without the spread rule
        if len(chosen) == n:
            break
        chosen.append(e)
    return sorted(chosen, key=lambda e: e.year)
