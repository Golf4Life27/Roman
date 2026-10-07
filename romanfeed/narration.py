"""Spoken "here's what you're looking at" for Shorts.

The script is built only from what NASA and ESA wrote about the image -- its
archive caption -- plus two facts we already hold (which telescope, which
year it was released). Nothing is generated, so nothing can be invented: a
Short never says a distance or a size the source did not say.

  1. the first clean sentence of the caption (what the object is)
  2. the first other sentence that carries a distance or a size, if any
  3. "This image from <telescope> was released in <year>."
  4. "The full 8-hour version is on the channel."
  5. a short subscribe call, rotated by image ("Subscribe to travel through time.")

Captions that are mostly credits, links, instrument boilerplate or "this video
shows" lines yield no script, and the Short picker moves to the next image.

The voice is Google Cloud Text-to-Speech (Chirp 3 HD). At three Shorts a day
the channel uses roughly 30,000 characters a month against a free allowance of
1,000,000, and Google's terms let the customer use the audio commercially. The
key comes from the GOOGLE_TTS_API_KEY environment variable; without it the
Shorts fall back to music only, so a missing key never stops a run."""
from __future__ import annotations

import base64
import html
import logging
import os
import re
from pathlib import Path

import requests

from romanfeed.render import ffmpeg
from romanfeed.sources.base import ImageAsset
from romanfeed.telescopes import TELESCOPES, telescopes_for

log = logging.getLogger(__name__)

TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"
DEFAULT_VOICE = "en-US-Chirp3-HD-Charon"
MAX_WORDS = 62          # ~25 s of speech at a calm pace, leaving room in a 45 s Short
SENTENCE_WORDS = (7, 38)
MIN_FACT_WORDS = 18

_SIZE = re.compile(r"\b(light[- ]years?|parsecs?|across|wide|diameter|times (?:the )?(?:size|mass)|million|billion|thousand|miles|kilometers)\b", re.I)
_JUNK = re.compile(
    r"(credit|acknowledg|read more|https?://|www\.|learn more|for more information|project of international cooperation|"
    r"goddard|space telescope science institute|stsci conducts|manages the telescope|operated by|aura|"
    r"this (?:video|clip|animation|visuali[sz]ation|side-by-side|comparison|composite of two)|"
    r"\b(?:at|on the) (?:left|right)\b|image processing|processed by|instrument[s]? used|filters?\b)",
    re.I,
)


def clean_caption(text: str) -> str:
    """Archive caption -> plain prose: no tags, links, parentheticals or credits."""
    t = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    t = re.sub(r"\([^()]*\)", "", t)            # "(NIRCam)", "(also called ...)"
    t = re.sub(r"\[[^\]]*\]", "", t)
    t = re.sub(r"\b(?:Image )?Credit:.*$", "", t, flags=re.I | re.S)
    t = t.replace("’", "'").replace("“", '"').replace("”", '"')
    # "the NASA/ESA/CSA James Webb Space Telescope" is read aloud as three slashes.
    t = re.sub(r"\b(?:NASA|ESA|CSA)(?:/(?:NASA|ESA|CSA))+\s+", "", t)
    # "NASA image release January 13, 2011 These images by ..." -- a dateline glued to the text.
    t = re.sub(r"\b(?:NASA |ESA )?(?:image )?release[sd]?:?\s+[A-Z][a-z]+\.? \d{1,2},? \d{4}\s*", "", t, flags=re.I)
    return " ".join(t.split())


def sentences(text: str) -> list[str]:
    # Split on sentence ends, but not inside "e.g." or after a single capital ("J. Smith").
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z\"'])", text)
    out = []
    for p in parts:
        p = p.strip().strip('"').strip()
        if p and not re.search(r"\b[A-Z]\.$", p):
            out.append(p if p[-1] in ".!?" else p + ".")
    return out


def _usable(sentence: str) -> bool:
    n = len(sentence.split())
    return SENTENCE_WORDS[0] <= n <= SENTENCE_WORDS[1] and not _JUNK.search(sentence)


# An opening line has to stand on its own: "It is based on new observations..."
# points at something the listener never heard.
_DANGLING = re.compile(r"^(?:It|They|Them|Its|Their|Those|These (?:are|were|show)|He|She|Here|There)\b")


def _telescope_name(asset: ImageAsset) -> str | None:
    keys = telescopes_for(asset)
    if not keys:
        return None
    full = {k: f for k, f, _s, _p in TELESCOPES}[keys[0]]
    return re.sub(r"^(NASA's |ESA's )", "the ", full) if not full.startswith("the ") else full


# Subscribe calls, rotated by image so the feed does not hear the same line
# three times a day. (spoken, on-screen headline). All literally true: every
# image is old light, and Shorts post daily.
SUBSCRIBE_CALLS: list[tuple[str, str]] = [
    ("Subscribe to travel through time.", "Subscribe to travel through time"),
    ("Hit subscribe for a new corner of the universe every day.", "A new corner of the universe every day"),
    ("Subscribe, and fall asleep among the stars tonight.", "Fall asleep among the stars tonight"),
    ("Everything you just saw is light from the past. Subscribe to see more.", "Subscribe to see light from the past"),
]


def subscribe_call(key: str) -> tuple[str, str]:
    import hashlib

    return SUBSCRIBE_CALLS[int(hashlib.sha1(key.encode()).hexdigest(), 16) % len(SUBSCRIBE_CALLS)]


def fact_script(asset: ImageAsset, *, full_label: str, include_size: bool = True) -> str | None:
    """The spoken script for one image, or None if its caption is not usable."""
    good = [s for s in sentences(clean_caption(asset.description)) if _usable(s)]
    while good and _DANGLING.match(good[0]):
        good.pop(0)
    if not good:
        return None
    lines = [good[0]]
    size = next((s for s in good[1:] if _SIZE.search(s)), None)
    if size is None and not _SIZE.search(good[0]) and len(good) > 1:
        size = good[1]
    if include_size and size and len((lines[0] + " " + size).split()) <= MAX_WORDS:
        lines.append(size)
    if len(" ".join(lines).split()) < (MIN_FACT_WORDS if include_size else 10):
        return None  # one thin line is not a reason to stop scrolling
    tele = _telescope_name(asset)
    # Only the ESA archives date an image by its release. NASA's library dates
    # are often the upload date (a 2011 release filed in 2017), and a wrong
    # year read aloud is worse than none.
    year = (asset.date or "")[:4] if asset.source.upper().startswith("ESA") else ""
    if tele and year.isdigit():
        lines.append(f"This image from {tele} was released in {year}.")
    elif tele:
        lines.append(f"This image comes from {tele}.")
    lines.append(f"The full {full_label} version is on the channel.")
    # Said while the end card is up (captions stop at "The full ...").
    lines.append(subscribe_call(asset.asset_id)[0])
    return " ".join(lines)


def synthesize(text: str, out_path: Path, *, api_key: str, voice: str = DEFAULT_VOICE, timeout: int = 60) -> Path:
    """Speak `text` with Google Cloud TTS and write a WAV."""
    lang = "-".join(voice.split("-")[:2])
    r = requests.post(
        TTS_URL, params={"key": api_key}, timeout=timeout,
        json={"input": {"text": text}, "voice": {"languageCode": lang, "name": voice},
              "audioConfig": {"audioEncoding": "LINEAR16", "sampleRateHertz": 44100}},
    )
    if r.status_code != 200:
        raise RuntimeError(f"text-to-speech failed: HTTP {r.status_code}: {r.text[:300]}")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(base64.b64decode(r.json()["audioContent"]))
    return out_path


def api_key() -> str | None:
    return os.environ.get("GOOGLE_TTS_API_KEY", "").strip() or None


def caption_chunks(text: str, *, start: float, duration: float, words_per_chunk: int = 6) -> list[tuple[float, float, str]]:
    """Split the script into on-screen chunks timed by their share of the characters.

    Without word timestamps from the voice, character share is a close proxy
    for speaking time; chunks never cross a sentence end, so a caption never
    shows the tail of one sentence and the head of the next."""
    chunks: list[str] = []
    for sent in sentences(text):
        words = sent.split()
        n = max(1, round(len(words) / words_per_chunk))
        size = -(-len(words) // n)
        chunks += [" ".join(words[i:i + size]) for i in range(0, len(words), size)]
    total = sum(len(c) + 1 for c in chunks) or 1
    out, t = [], start
    for c in chunks:
        d = duration * (len(c) + 1) / total
        out.append((round(t, 3), round(t + d, 3), c))
        t += d
    return out


def mix_voice(voice_wav: Path, music: Path, out_path: Path, *, voice_start: float, duration: float, music_gain: float = 0.22) -> Path:
    """Voice over music: the music sits well under the voice for the whole Short."""
    ms = int(voice_start * 1000)
    ffmpeg.run([
        "-i", str(music), "-i", str(voice_wav),
        "-filter_complex",
        f"[0:a]volume={music_gain}[m];[1:a]adelay={ms}|{ms},volume=1.0[v];"
        f"[m][v]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[out]",
        "-map", "[out]", "-t", f"{duration:.3f}", "-c:a", "aac", "-b:a", "160k", str(out_path),
    ])
    return out_path
