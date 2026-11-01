"""Live feeds of official Government of India announcements (public RSS).

Every source is tried independently. One that refuses or fails is reported in `errors` and
skipped - the others still work. We identify ourselves honestly (see ingestion.web.HEADERS)
and never try to get around a site that blocks automated access."""
import html
import re
import time
import xml.etree.ElementTree as ET

import httpx

from ingestion.web import HEADERS, tls_context

FEEDS = {
    "mygov": {"label": "MyGov - campaigns and schemes", "url": "https://www.mygov.in/rss.xml"},
    "rbi": {"label": "RBI - press releases", "url": "https://www.rbi.org.in/pressreleases_rss.xml"},
    "digitalindia": {"label": "Digital India", "url": "https://www.digitalindia.gov.in/feed"},
    "pib": {"label": "PIB - press releases", "url": "https://www.pib.gov.in/RssMain.aspx",
            "params": {"ModId": 6, "Lang": 1, "Regid": 3}},
}
TTL = 300                                   # be polite: refresh each feed at most every 5 minutes
_cache = {}                                 # key -> (time, items)


def _clean(text):
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def latest(key: str, limit: int = 20):
    """Newest items of one feed as [{title, url, summary, source}]. Raises RuntimeError on failure."""
    if key not in FEEDS:
        raise RuntimeError("Unknown source.")
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < TTL:
        return hit[1][:limit]
    feed = FEEDS[key]
    try:
        r = httpx.get(feed["url"], params=feed.get("params"), headers=HEADERS,
                      follow_redirects=True, timeout=20, verify=tls_context())
        if r.status_code in (401, 403):
            raise RuntimeError(f"{feed['label']} does not allow automated access.")
        r.raise_for_status()
        root = ET.fromstring(r.content.decode("utf-8-sig").encode("utf-8"))
    except RuntimeError:
        raise
    except (httpx.HTTPError, ET.ParseError, UnicodeDecodeError) as e:
        if hit:
            return hit[1][:limit]           # serve the last good copy
        raise RuntimeError(f"{feed['label']} is unreachable right now ({e.__class__.__name__}).")
    items = []
    for it in root.iter("item"):
        title, link = _clean(it.findtext("title")), (it.findtext("link") or "").strip()
        if title and link.startswith("http"):
            items.append({"title": title, "url": link, "source": key,
                          "summary": _clean(it.findtext("description"))[:300]})
    _cache[key] = (time.time(), items)
    return items[:limit]


def latest_all(per_source: int = 10, only: str | None = None):
    """Items from every source (or just `only`), plus a status line for each source."""
    keys = [only] if only and only != "all" else list(FEEDS)
    items, status = [], []
    for k in keys:
        try:
            got = latest(k, per_source)
            items += got
            status.append({"key": k, "label": FEEDS[k]["label"], "ok": True, "count": len(got)})
        except RuntimeError as e:
            status.append({"key": k, "label": FEEDS.get(k, {}).get("label", k), "ok": False, "error": str(e)})
    return items, status
