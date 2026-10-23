"""Temporary: find which URL shape serves page 2 of an ESA category as JSON."""
import requests
from romanfeed.sources.base import USER_AGENT

s = requests.Session(); s.headers["User-Agent"] = USER_AGENT
B = "https://esahubble.org/images/archive/category/nebulae"
def ids(url):
    try:
        r = s.get(url, timeout=60)
        try:
            d = r.json()
        except ValueError:
            return r.status_code, None, r.headers.get("content-type", "")[:30]
        if isinstance(d, dict):
            return r.status_code, None, "dict keys " + ",".join(list(d)[:6])
        return r.status_code, [x.get("ID") for x in d], ""
    except requests.RequestException as e:
        return "ERR", None, str(e)[:60]
_, p1, _ = ids(B + "/json/")
p1 = set(p1 or [])
print(f"page1 {len(p1)} ids")
for u in [B + "/page/2/json/", B + "/json/?page=2", B + "/json/page/2/", B + "/json/2/",
          B + "/list/page/2/json/", B + "/viewall/page/2/json/", B + "/page/2/?format=json",
          "https://esahubble.org/images/json/?category=nebulae&page=2",
          "https://esahubble.org/images/archive/search/json/?category=nebulae&page=2",
          B + "/json/?rank=100", B + "/json/?offset=100", B + "/page/2/"]:
    code, got, note = ids(u)
    new = len(set(got) - p1) if got is not None else None
    print(f"{u.replace('https://esahubble.org','')} -> {code} n={len(got) if got is not None else '-'} new={new} {note}")
