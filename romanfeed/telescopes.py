"""Which telescope took this picture?

The intro card names the object and the telescope, and the Shorts say the
same thing, so the answer has to come from what the archives actually tell us:
the source label, the credit line, the title, the keywords. Archive titles
carry instrument tags ("(NIRCam Image)", "WFC3/UVIS") and credit lines carry
agency lists ("NASA, ESA, CSA, STScI" -- CSA only appears on Webb releases),
which between them identify most images.

When nothing matches, the answer is None and callers say less rather than
guess: a wrong telescope on screen is worse than no telescope."""
from __future__ import annotations

import re

from romanfeed.sources.base import ImageAsset

# (key, full name for the intro card, short name for titles, patterns).
# Order matters only for display when several match; Roman first because
# it is the channel's headline act.
TELESCOPES: list[tuple[str, str, str, tuple[str, ...]]] = [
    ("roman", "NASA's Nancy Grace Roman Space Telescope", "Roman", (r"\bnancy grace roman\b", r"\broman space telescope\b", r"\bwfi\b")),
    ("webb", "the James Webb Space Telescope", "Webb", (r"\bwebb\b", r"\bjwst\b", r"\bnircam\b", r"\bmiri\b", r"\bniriss\b", r"\bnirspec\b", r"\bcsa\b")),
    ("hubble", "the Hubble Space Telescope", "Hubble", (r"\bhubble\b", r"\bhst\b", r"\bwfc3\b", r"\bwfpc2\b", r"\bacs\b", r"\bstis\b", r"\bnicmos\b")),
    ("spitzer", "NASA's Spitzer Space Telescope", "Spitzer", (r"\bspitzer\b",)),
    ("chandra", "NASA's Chandra X-ray Observatory", "Chandra", (r"\bchandra\b",)),
    ("euclid", "ESA's Euclid space telescope", "Euclid", (r"\beuclid\b",)),
    ("wise", "NASA's WISE space telescope", "WISE", (r"\bwise\b", r"\bneowise\b")),
    ("galex", "NASA's GALEX space telescope", "GALEX", (r"\bgalex\b",)),
    ("herschel", "ESA's Herschel Space Observatory", "Herschel", (r"\bherschel\b",)),
]


def _blob(asset: ImageAsset) -> str:
    # "Roman" alone is too common a word (the Roman empire, a person) to trust
    # outside the dedicated source; the patterns above need the full name.
    return " ".join([asset.source, asset.credit, asset.title, " ".join(asset.keywords)]).lower()


def telescopes_for(asset: ImageAsset) -> list[str]:
    """Keys of every telescope the asset's metadata names, in TELESCOPES order."""
    blob = _blob(asset)
    found = [key for key, _full, _short, pats in TELESCOPES if any(re.search(p, blob) for p in pats)]
    # The archive itself is the strongest signal: esawebb.org only hosts Webb
    # images (plus the odd Hubble comparison, which the title then names).
    src = asset.source.lower()
    if "esa/webb" in src and "webb" not in found:
        found.insert(0, "webb")
    if "esa/hubble" in src and "hubble" not in found:
        found.insert(0, "hubble")
    if "roman" in src and "roman" not in found:
        found.insert(0, "roman")
    order = [t[0] for t in TELESCOPES]
    return sorted(dict.fromkeys(found), key=order.index)


def telescope_line(asset: ImageAsset) -> str | None:
    """'Imaged by the James Webb Space Telescope' -- or None if unknown.

    Two telescopes (a Hubble + Webb comparison, a Chandra composite) are both
    named; three or more read as a list nobody finishes, so the first two win."""
    keys = telescopes_for(asset)
    if not keys:
        return None
    full = {k: f for k, f, _s, _p in TELESCOPES}
    names = [full[k] for k in keys[:2]]
    if len(names) == 2:
        # "the James Webb Space Telescope and the Hubble Space Telescope" is a
        # mouthful; drop the repeated article/agency on the second.
        second = re.sub(r"^(the |NASA's |ESA's )", "", names[1])
        return f"Imaged by {names[0]} and {second}"
    return f"Imaged by {names[0]}"


def telescope_short(asset: ImageAsset) -> str | None:
    """'Webb', 'Hubble & Webb' -- for titles, or None if unknown."""
    keys = telescopes_for(asset)
    if not keys:
        return None
    short = {k: s for k, _f, s, _p in TELESCOPES}
    return " & ".join(short[k] for k in keys[:2])


def prefer_known_lead(assets: list[ImageAsset]) -> list[ImageAsset]:
    """Move the first asset with a known telescope to the front.

    The lead image names the video and opens it with the intro card; an intro
    that cannot say which telescope took the picture is half an intro. The
    rest of the curated order is untouched."""
    for i, a in enumerate(assets):
        if telescope_line(a):
            if i:
                return [a] + assets[:i] + assets[i + 1:]
            return list(assets)
    return list(assets)
