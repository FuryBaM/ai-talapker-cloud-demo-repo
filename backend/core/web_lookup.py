import re
import urllib.parse
import urllib.request
from html import unescape
from typing import List
from urllib.error import URLError
from urllib.parse import urlparse
from xml.etree import ElementTree as ET

from core.config import WEB_SEARCH_MAX_RESULTS, WEB_SEARCH_WHITELIST


def _is_allowed_domain(url: str, whitelist: List[str]) -> bool:
    try:
        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return any(host == domain or host.endswith("." + domain) for domain in whitelist)


def _fetch_text(url: str, timeout: int = 8) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; SaginovAssistant/1.0)",
            "Accept-Language": "ru,en;q=0.8",
        },
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        raw = response.read(400_000).decode("utf-8", errors="ignore")
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", raw)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()[:8000]


def _bing_rss_search(query: str, whitelist: List[str], limit: int) -> List[str]:
    urls: List[str] = []
    for domain in whitelist:
        rss_query = urllib.parse.quote(f"site:{domain} {query}")
        feed_url = f"https://www.bing.com/search?format=rss&q={rss_query}"
        try:
            request = urllib.request.Request(
                feed_url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; SaginovAssistant/1.0)"},
            )
            with urllib.request.urlopen(request, timeout=8) as response:
                xml_text = response.read().decode("utf-8", errors="ignore")
        except URLError:
            continue

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            continue

        for item in root.findall(".//item/link"):
            link = (item.text or "").strip()
            if link and _is_allowed_domain(link, whitelist) and link not in urls:
                urls.append(link)
            if len(urls) >= limit:
                return urls
    return urls[:limit]


def search_whitelisted_web(
    query: str,
    whitelist: List[str] | None = None,
    limit: int = WEB_SEARCH_MAX_RESULTS,
) -> List[str]:
    allowed = whitelist or WEB_SEARCH_WHITELIST
    urls = _bing_rss_search(query, allowed, limit)
    snippets: List[str] = []
    for url in urls:
        if not _is_allowed_domain(url, allowed):
            continue
        try:
            text = _fetch_text(url)
        except URLError:
            continue
        if text:
            snippets.append(f"Source: {url}\n{text}")
        if len(snippets) >= limit:
            break
    return snippets[:limit]
