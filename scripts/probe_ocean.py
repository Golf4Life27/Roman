"""One-off probe: how much free-to-use ocean footage exists on Wikimedia Commons."""
import collections, json, re, sys, time
import requests

API = "https://commons.wikimedia.org/w/api.php"
UA = {"User-Agent": "RomanFeed/0.1 (+https://github.com/Golf4Life27/Roman)"}
Q = ["coral reef", "sea turtle", "humpback whale", "kelp forest", "jellyfish", "manta ray", "school of fish",
     "reef fish", "dolphin underwater", "shark underwater", "octopus", "sea lion underwater", "ocean waves",
     "seascape", "underwater", "deep sea", "nudibranch", "anemone clownfish", "whale shark", "sea star"]


def lic(s):
    s = (s or "").lower()
    if "public domain" in s or s.startswith("pd") or "cc0" in s: return "pd"
    if "by-sa" in s or "by sa" in s: return "by-sa"
    if "nc" in s.split("-") or "-nc" in s or "nd" in s.split("-"): return "nc/nd"
    if s.startswith("cc by") or s.startswith("cc-by"): return "by"
    return "other:" + s[:20]


def search(q, kind, pages):
    seen = []
    cont = {}
    for _ in range(pages):
        p = {"action": "query", "format": "json", "generator": "search", "gsrnamespace": 6,
             "gsrsearch": f"{q} filetype:{kind}", "gsrlimit": 50, "prop": "imageinfo",
             "iiprop": "url|size|extmetadata|mediatype", "iiextmetadatafilter": "LicenseShortName|Artist|Credit"}
        p.update(cont)
        r = requests.get(API, params=p, headers=UA, timeout=60)
        r.raise_for_status()
        j = r.json()
        for pg in (j.get("query", {}).get("pages") or {}).values():
            ii = (pg.get("imageinfo") or [{}])[0]
            em = ii.get("extmetadata") or {}
            seen.append({"title": pg.get("title"), "w": ii.get("width", 0), "h": ii.get("height", 0),
                         "dur": ii.get("duration", 0), "lic": lic(em.get("LicenseShortName", {}).get("value")),
                         "artist": re.sub("<[^>]+>", "", em.get("Artist", {}).get("value", ""))[:40]})
        if "continue" not in j: break
        cont = j["continue"]
        time.sleep(0.5)
    return seen


def main():
    allimg, allvid = {}, {}
    for q in Q:
        imgs = search(q, "bitmap", 6)
        vids = search(q, "video", 2)
        for x in imgs: allimg[x["title"]] = x
        for x in vids: allvid[x["title"]] = x
        big = [x for x in imgs if x["w"] >= 1920 and x["w"] >= x["h"]]
        good = [x for x in big if x["lic"] in ("pd", "by")]
        vgood = [x for x in vids if x["w"] >= 1280 and x["dur"] >= 15 and x["lic"] in ("pd", "by")]
        print(f"::notice::{q}: images {len(imgs)}, landscape>=1920 {len(big)}, usable(pd/by) {len(good)}; videos {len(vids)}, usable>=720p/15s {len(vgood)}")
    big = [x for x in allimg.values() if x["w"] >= 1920 and x["w"] >= x["h"]]
    c = collections.Counter(x["lic"] for x in big)
    print("::notice::UNIQUE images", len(allimg), "landscape>=1920", len(big), "by licence", dict(c.most_common(8)))
    vg = [x for x in allvid.values() if x["w"] >= 1280 and x["dur"] >= 15]
    cv = collections.Counter(x["lic"] for x in vg)
    print("::notice::UNIQUE videos", len(allvid), ">=720p/15s", len(vg), "by licence", dict(cv.most_common(8)),
          "hours(pd/by)", round(sum(x["dur"] for x in vg if x["lic"] in ("pd", "by")) / 3600, 1))
    art = collections.Counter(x["artist"] for x in big if x["lic"] in ("pd", "by"))
    print("::notice::top image credits", art.most_common(12))
    vart = collections.Counter(x["artist"] for x in vg if x["lic"] in ("pd", "by"))
    print("::notice::top video credits", vart.most_common(12))
    for x in sorted(vg, key=lambda x: -x["dur"])[:15]:
        print("::notice::video", x["title"][:80], x["w"], "x", x["h"], round(x["dur"]), "s", x["lic"])
    for h in ["OceanScreens", "DeepBlueScreens", "OceanScreen", "CalmOceanScreens", "OceanSleepScreen"]:
        try:
            s = requests.get(f"https://www.youtube.com/@{h}", headers=UA, timeout=30).status_code
        except Exception as e:
            s = str(e)[:40]
        print(f"::notice::handle @{h}: HTTP {s}")


main()
