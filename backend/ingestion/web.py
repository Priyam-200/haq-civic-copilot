import ipaddress
import re
import socket
import ssl
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

from ingestion.pdf import extract_pages

MAX_BYTES = 20 * 1024 * 1024
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; ForgeBot/1.0; civic-explainer)"}
# main content containers of common government sites, tried in order
CONTENT_IDS = ["innner-page-main-about-us-content-right-part", "content", "main-content"]


class FetchError(ValueError):
    pass


def tls_context():
    """Verify certificates with the operating system's trust store. Many government sites serve
    an incomplete certificate chain that browsers repair but plain Python cannot; verification
    stays ON."""
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        return ssl.create_default_context()


def _check_public(url: str):
    """Block localhost / private network addresses so the server cannot be used to probe them."""
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise FetchError("Please give a full http(s) link.")
    try:
        infos = socket.getaddrinfo(p.hostname, None)
    except socket.gaierror:
        raise FetchError("Could not find that website.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise FetchError("That address is not allowed.")


def fetch(url: str):
    """Download a page or PDF. Returns (bytes, content_type, final_url)."""
    _check_public(url)
    try:
        with httpx.Client(follow_redirects=True, timeout=30, headers=HEADERS, max_redirects=5,
                          verify=tls_context()) as c:
            with c.stream("GET", url) as r:
                if r.status_code >= 400:
                    raise FetchError(f"The website returned an error ({r.status_code}).")
                _check_public(str(r.url))
                data = b""
                for part in r.iter_bytes():
                    data += part
                    if len(data) > MAX_BYTES:
                        raise FetchError("That file is too big (max 20 MB).")
                return data, r.headers.get("content-type", "").lower(), str(r.url)
    except httpx.HTTPError as e:
        if "CERTIFICATE" in str(e).upper():
            raise FetchError("That website's security certificate could not be verified, so it was not imported.")
        raise FetchError(f"Could not download this link: {e.__class__.__name__}")


def html_to_text(html: str):
    """Returns (title, text) with one paragraph per line."""
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "nav", "header", "footer", "noscript", "form"]):
        t.decompose()
    title = (soup.title.string or "").strip() if soup.title and soup.title.string else ""
    root = next((soup.find(id=i) for i in CONTENT_IDS if soup.find(id=i)), soup.body or soup)
    lines = []
    for el in root.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "div"]):
        if el.name == "div" and el.find(["p", "li", "div", "h1", "h2", "h3"]):
            continue                              # only take leaf blocks
        t = re.sub(r"\s+", " ", el.get_text(" ", strip=True))
        if t and (not lines or lines[-1] != t):
            lines.append(t)
    return title, "\n".join(lines)


def _paginate(text: str, words_per_page: int = 450):
    pages, cur, n = [], [], 0
    for line in text.split("\n"):
        cur.append(line)
        n += len(line.split())
        if n >= words_per_page:
            pages.append("\n".join(cur))
            cur, n = [], 0
    if cur:
        pages.append("\n".join(cur))
    return [{"page": i + 1, "text": t, "ocr": False} for i, t in enumerate(pages)]


def load_url(url: str):
    """Returns (title, pages, final_url) for a PDF or an HTML page."""
    data, ctype, final = fetch(url)
    if "pdf" in ctype or data[:5] == b"%PDF-":
        try:
            return final.rsplit("/", 1)[-1] or "Document", extract_pages(data), final
        except ValueError as e:
            raise FetchError(str(e))
        except Exception:
            raise FetchError("Could not read this PDF.")
    html = data.decode("utf-8", errors="ignore")
    title, text = html_to_text(html)
    if len(text.split()) < 30:
        raise FetchError("Not enough readable text on that page.")
    return title or final, _paginate(text), final
