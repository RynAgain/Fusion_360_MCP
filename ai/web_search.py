"""
ai/web_search.py
Web search and page fetching capabilities for the AI agent.

Allows the agent to search the internet for up-to-date information
(Fusion 360 API docs, design patterns, troubleshooting, etc.).

Supports multiple backends:
1. DuckDuckGo (free, no API key required) -- default
2. SearXNG (self-hosted, optional)
3. Direct URL fetching with content extraction
"""

import ipaddress
import logging
import re
import socket as _socket
from typing import Any
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from ai.context_manager import ContextManager

logger = logging.getLogger(__name__)

# Tags whose content is stripped when extracting readable text from HTML
_STRIP_TAGS = {"script", "style", "nav", "footer", "header", "aside", "noscript"}

# TASK-056: SSRF protection -- blocked private/reserved IP ranges
_BLOCKED_NETWORKS = [
    ipaddress.ip_network('10.0.0.0/8'),
    ipaddress.ip_network('172.16.0.0/12'),
    ipaddress.ip_network('192.168.0.0/16'),
    ipaddress.ip_network('169.254.0.0/16'),
    ipaddress.ip_network('127.0.0.0/8'),
    ipaddress.ip_network('::1/128'),
    ipaddress.ip_network('fc00::/7'),
    ipaddress.ip_network('fe80::/10'),
]


def _is_safe_url(url: str) -> bool:
    """Check URL does not resolve to a private/reserved IP (SSRF protection).

    Returns ``True`` (allow) when DNS resolution fails -- better to let the
    request through (it will likely fail anyway at the HTTP layer) than to
    block all web fetches when the DNS resolver is slow or unreachable.
    """
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            return False
        addr_info = _socket.getaddrinfo(hostname, None, _socket.AF_UNSPEC)
        for family, _, _, _, sockaddr in addr_info:
            ip = ipaddress.ip_address(sockaddr[0])
            for net in _BLOCKED_NETWORKS:
                if ip in net:
                    logger.warning(
                        "SSRF protection: %s resolved to private IP %s", url, ip,
                    )
                    return False
        return True
    except ValueError:
        # Malformed URL or IP -- block it.
        return False
    except (_socket.gaierror, OSError) as exc:
        # DNS resolution failed -- allow the request through.  The HTTP
        # layer will raise its own error if the host is truly unreachable.
        logger.warning(
            "SSRF check: DNS resolution failed for %s (%s); allowing request", url, exc,
        )
        return True


class WebSearchProvider:
    """Provides web search and page fetching capabilities.

    Supports multiple backends:
    1. DuckDuckGo (free, no API key required) -- default
    2. SearXNG (self-hosted, optional)
    3. Direct URL fetching with content extraction
    """

    def __init__(
        self,
        backend: str = "duckduckgo",
        searxng_url: str | None = None,
        timeout: int = 10,
    ):
        """Initialize with chosen backend.

        Parameters:
            backend:     ``"duckduckgo"`` or ``"searxng"``.
            searxng_url: Base URL for a SearXNG instance (required when
                         *backend* is ``"searxng"``).
            timeout:     HTTP request timeout in seconds.
        """
        if backend not in ("duckduckgo", "searxng"):
            raise ValueError(f"Unsupported search backend: {backend}")
        if backend == "searxng" and not searxng_url:
            raise ValueError("searxng_url is required when backend is 'searxng'")

        self.backend = backend
        self.searxng_url = searxng_url.rstrip("/") if searxng_url else None
        self.timeout = timeout

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(self, query: str, max_results: int = 5) -> list[dict[str, str]]:
        """Search the web using the configured backend.

        Returns a list of result dicts::

            [{"title": str, "url": str, "snippet": str}, ...]

        On error, returns an empty list and logs a warning.
        """
        try:
            if self.backend == "duckduckgo":
                return self._search_duckduckgo(query, max_results)
            else:
                return self._search_searxng(query, max_results)
        except Exception as exc:
            logger.warning("Web search failed (%s): %s", self.backend, exc)
            return []

    # ------------------------------------------------------------------
    # Backend implementations
    # ------------------------------------------------------------------

    def _search_duckduckgo(self, query: str, max_results: int) -> list[dict[str, str]]:
        """Search using the ``duckduckgo_search`` Python package."""
        from duckduckgo_search import DDGS

        results: list[dict[str, str]] = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "snippet": r.get("body", ""),
                })
        return results

    def _search_searxng(self, query: str, max_results: int) -> list[dict[str, str]]:
        """Search using a SearXNG instance's JSON API."""
        resp = requests.get(
            f"{self.searxng_url}/search",
            params={"q": query, "format": "json"},
            timeout=self.timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        results: list[dict[str, str]] = []
        for r in data.get("results", [])[:max_results]:
            results.append({
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                "snippet": r.get("content", ""),
            })
        return results

    # ------------------------------------------------------------------
    # Page fetching
    # ------------------------------------------------------------------

    def fetch_page(self, url: str, max_chars: int = 10000) -> dict[str, Any]:
        """Fetch a web page and extract readable text content.

        Uses ``requests`` to fetch HTML and ``beautifulsoup4`` to extract
        text (stripping scripts, styles, nav, footer).  The output is
        truncated to *max_chars* using the context manager's head+tail
        filter pattern.

        Returns::

            {
                "url": str,
                "title": str,
                "content": str,
                "success": bool,
                "error": str | None,
            }
        """
        # TASK-056: SSRF protection -- block requests to private/reserved IPs
        if not _is_safe_url(url):
            logger.warning("SSRF blocked: URL %s resolves to a private/reserved IP", url)
            return {
                "url": url,
                "title": "",
                "content": "",
                "success": False,
                "error": "URL blocked: resolves to a private or reserved IP address",
            }

        try:
            session = requests.Session()
            session.max_redirects = 5
            # TASK-149: Honest user agent that identifies the bot
            resp = session.get(url, timeout=self.timeout, allow_redirects=True, headers={
                "User-Agent": "Artifex360/1.0 (AI Design Assistant; https://github.com/Artifex360)",
            })
            resp.raise_for_status()

            soup = BeautifulSoup(resp.text, "html.parser")

            # Extract title
            title = ""
            if soup.title and soup.title.string:
                title = soup.title.string.strip()

            # Remove unwanted tags
            for tag in soup.find_all(_STRIP_TAGS):
                tag.decompose()

            # Extract text
            text = soup.get_text(separator="\n", strip=True)

            # Truncate using context manager's filter pattern (head + tail)
            text = ContextManager.filter_operation_output(text, max_chars=max_chars)

            # TASK-155: Try to extract structured specs from the page text
            result: dict[str, Any] = {
                "url": url,
                "title": title,
                "content": text,
                "success": True,
                "error": None,
            }
            specs = self._extract_specs(text, url)
            if specs:
                result["extracted_specs"] = specs
            return result

        except Exception as exc:
            logger.warning("Failed to fetch page %s: %s", url, exc)
            return {
                "url": url,
                "title": "",
                "content": "",
                "success": False,
                "error": str(exc),
            }

    # ------------------------------------------------------------------
    # Structured data extraction (TASK-155)
    # ------------------------------------------------------------------

    def _extract_specs(self, text: str, url: str) -> dict | None:
        """Try to extract structured specs from page text.

        Looks for common patterns found in product datasheets and spec pages:
        dimensions, mounting hole sizes, and spacing/pitch values.

        Returns a dict of extracted specs, or ``None`` if nothing was found.
        """
        specs: dict[str, Any] = {}

        # Look for dimension patterns: "192mm x 192mm" or "192 x 192 mm"
        dim_patterns = re.findall(
            r'(\d+(?:\.\d+)?)\s*(?:x|\u00d7|X)\s*(\d+(?:\.\d+)?)'
            r'\s*(?:(?:x|\u00d7|X)\s*(\d+(?:\.\d+)?))?\s*(mm|cm|in|inch|")',
            text,
        )
        if dim_patterns:
            specs["dimensions"] = [
                {"w": d[0], "h": d[1], "d": d[2] or None, "unit": d[3]}
                for d in dim_patterns[:5]
            ]

        # Look for mounting hole patterns: "M2.5" "M3" etc.
        mount_patterns = re.findall(
            r'(M\d+(?:\.\d+)?)\s*(?:screw|mount|hole|thread|bolt)',
            text,
            re.IGNORECASE,
        )
        if mount_patterns:
            specs["mounting"] = list(set(mount_patterns))

        # Look for spacing/pitch patterns: "2.5mm pitch" "58mm spacing"
        pitch_patterns = re.findall(
            r'(\d+(?:\.\d+)?)\s*(mm|cm|in)\s*'
            r'(?:pitch|spacing|apart|centers|center.to.center)',
            text,
            re.IGNORECASE,
        )
        if pitch_patterns:
            specs["spacing"] = [
                {"value": p[0], "unit": p[1]} for p in pitch_patterns[:5]
            ]

        return specs if specs else None

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    def search_fusion_docs(self, query: str) -> list[dict[str, str]]:
        """Convenience method: prepends 'Autodesk Fusion 360 API' to the query.

        Searches and returns results targeting Fusion 360 documentation.
        """
        prefixed_query = f"Autodesk Fusion 360 API {query}"
        return self.search(prefixed_query)

    def search_and_summarize(self, query: str, max_results: int = 3) -> str:
        """Search, fetch top results, and compile a readable summary.

        Returns formatted text with sources.
        """
        results = self.search(query, max_results=max_results)
        if not results:
            return f"No search results found for: {query}"

        sections: list[str] = [f"## Search Results for: {query}\n"]

        for i, result in enumerate(results, 1):
            sections.append(f"### {i}. {result['title']}")
            sections.append(f"URL: {result['url']}")

            if result.get("url"):
                page = self.fetch_page(result["url"], max_chars=3000)
                if page["success"] and page["content"]:
                    sections.append(f"\n{page['content']}\n")
                else:
                    # Fall back to snippet
                    sections.append(f"\n{result.get('snippet', 'No content available.')}\n")
            else:
                sections.append(f"\n{result.get('snippet', 'No content available.')}\n")

            sections.append("---")

        return "\n".join(sections)
