"""Temporary, read-only: how our recent live streams did, and what metadata they carry."""
from romanfeed.publish.youtube import client

yt = client()
items, token = [], None
for _ in range(3):
    r = yt.liveBroadcasts().list(part="id,snippet,status", mine=True, maxResults=50,
                                 **({"pageToken": token} if token else {})).execute()
    items += r.get("items", [])
    token = r.get("nextPageToken")
    if not token:
        break
ids = [b["id"] for b in items][:50]
stats = {}
for i in range(0, len(ids), 50):
    v = yt.videos().list(part="snippet,statistics,liveStreamingDetails", id=",".join(ids[i:i + 50])).execute()
    stats.update({x["id"]: x for x in v.get("items", [])})
rows = []
for b in items[:50]:
    x = stats.get(b["id"], {})
    sn, st, ld = x.get("snippet", {}), x.get("statistics", {}), x.get("liveStreamingDetails", {})
    start = ld.get("actualStartTime", "")[:16] or ("sched " + b["snippet"].get("scheduledStartTime", "")[:16])
    end = ld.get("actualEndTime", "")[:16]
    rows.append(f"{start} {b['status'].get('lifeCycleStatus','')} views={st.get('viewCount','-')} "
                f"now={ld.get('concurrentViewers','-')} likes={st.get('likeCount','-')} cat={sn.get('categoryId','-')} "
                f"tags={len(sn.get('tags', []))} thumbs={','.join(sorted(sn.get('thumbnails', {})))} end={end} "
                f"{b['snippet'].get('title','')[:70]}")
rows.sort(reverse=True)
print(f"broadcasts listed: {len(items)}")
for r in rows[:40]:
    print(r)
