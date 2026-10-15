"""Канонизация URL, чтобы одна и та же страница не считалась разными узлами."""
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING_PREFIXES = ("utm_",)
_TRACKING_KEYS = {"fbclid", "gclid", "yclid", "ysclid", "ref", "ref_src", "from", "_openstat"}


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").lower()
    if scheme == "http":
        scheme = "https"
    host = parts.netloc.lower().removeprefix("www.").removeprefix("m.")
    path = parts.path.rstrip("/") or "/"
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in _TRACKING_KEYS and not k.lower().startswith(_TRACKING_PREFIXES)]
    return urlunsplit((scheme, host, path, urlencode(sorted(query)), ""))


def domain(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.").removeprefix("m.")


TG_POST_RE = re.compile(
    r"^https?://(?:www\.)?(?:t\.me|telegram\.me)/(?:s/)?([A-Za-z0-9_]{4,})/(\d+)", re.IGNORECASE)
TG_PRIVATE_RE = re.compile(r"^https?://(?:www\.)?(?:t\.me|telegram\.me)/c/\d+/\d+", re.IGNORECASE)
