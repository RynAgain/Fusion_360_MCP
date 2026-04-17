"""
tests/test_web_search.py
Comprehensive tests for the web search module, MCP tool integration,
and configuration settings.

All HTTP calls are mocked -- no real network requests are made.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from ai.web_search import WebSearchProvider
from config.settings import DEFAULTS, Settings
from mcp.server import TOOL_CATEGORIES, TOOL_DEFINITIONS
from mcp.tool_groups import TOOL_GROUPS, get_tools_for_groups


# ======================================================================
# TestWebSearchProvider
# ======================================================================


class TestWebSearchProvider:
    """Tests for the WebSearchProvider class."""

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    def test_init_default_backend(self):
        provider = WebSearchProvider()
        assert provider.backend == "duckduckgo"
        assert provider.searxng_url is None
        assert provider.timeout == 10

    def test_init_searxng_backend(self):
        provider = WebSearchProvider(
            backend="searxng",
            searxng_url="http://localhost:8888",
        )
        assert provider.backend == "searxng"
        assert provider.searxng_url == "http://localhost:8888"

    def test_init_searxng_strips_trailing_slash(self):
        provider = WebSearchProvider(
            backend="searxng",
            searxng_url="http://localhost:8888/",
        )
        assert provider.searxng_url == "http://localhost:8888"

    def test_init_invalid_backend_raises(self):
        with pytest.raises(ValueError, match="Unsupported search backend"):
            WebSearchProvider(backend="google")

    def test_init_searxng_without_url_raises(self):
        with pytest.raises(ValueError, match="searxng_url is required"):
            WebSearchProvider(backend="searxng")

    def test_init_custom_timeout(self):
        provider = WebSearchProvider(timeout=30)
        assert provider.timeout == 30

    # ------------------------------------------------------------------
    # DuckDuckGo search
    # ------------------------------------------------------------------

    @patch("ai.web_search.DDGS", create=True)
    def test_search_duckduckgo(self, _mock_ddgs_class):
        """Test DuckDuckGo search returns formatted results."""
        # Mock the DDGS context manager and .text() method
        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.__enter__ = MagicMock(return_value=mock_ddgs_instance)
        mock_ddgs_instance.__exit__ = MagicMock(return_value=False)
        mock_ddgs_instance.text.return_value = [
            {"title": "Result 1", "href": "https://example.com/1", "body": "Snippet 1"},
            {"title": "Result 2", "href": "https://example.com/2", "body": "Snippet 2"},
        ]

        with patch("duckduckgo_search.DDGS", return_value=mock_ddgs_instance):
            provider = WebSearchProvider(backend="duckduckgo")
            results = provider.search("fusion 360 api", max_results=2)

        assert len(results) == 2
        assert results[0]["title"] == "Result 1"
        assert results[0]["url"] == "https://example.com/1"
        assert results[0]["snippet"] == "Snippet 1"
        assert results[1]["title"] == "Result 2"

    # ------------------------------------------------------------------
    # SearXNG search
    # ------------------------------------------------------------------

    @patch("ai.web_search.requests.get")
    def test_search_searxng(self, mock_get):
        """Test SearXNG search returns formatted results."""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "results": [
                {"title": "SearX Result 1", "url": "https://searx.example.com/1", "content": "Content 1"},
                {"title": "SearX Result 2", "url": "https://searx.example.com/2", "content": "Content 2"},
                {"title": "SearX Result 3", "url": "https://searx.example.com/3", "content": "Content 3"},
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        provider = WebSearchProvider(
            backend="searxng",
            searxng_url="http://localhost:8888",
        )
        results = provider.search("fusion 360 api", max_results=2)

        assert len(results) == 2
        assert results[0]["title"] == "SearX Result 1"
        assert results[0]["url"] == "https://searx.example.com/1"
        assert results[0]["snippet"] == "Content 1"
        mock_get.assert_called_once_with(
            "http://localhost:8888/search",
            params={"q": "fusion 360 api", "format": "json"},
            timeout=10,
        )

    # ------------------------------------------------------------------
    # Search edge cases
    # ------------------------------------------------------------------

    @patch("ai.web_search.requests.get")
    def test_search_no_results(self, mock_get):
        """Test search with no results returns empty list."""
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": []}
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        provider = WebSearchProvider(
            backend="searxng",
            searxng_url="http://localhost:8888",
        )
        results = provider.search("nonexistent query xyz")
        assert results == []

    @patch("ai.web_search.requests.get")
    def test_search_with_error_returns_empty(self, mock_get):
        """Test search gracefully handles errors and returns empty list."""
        mock_get.side_effect = Exception("Network error")

        provider = WebSearchProvider(
            backend="searxng",
            searxng_url="http://localhost:8888",
        )
        results = provider.search("fusion 360 api")
        assert results == []

    def test_search_duckduckgo_import_error_returns_empty(self):
        """Test DuckDuckGo search handles import errors gracefully."""
        provider = WebSearchProvider(backend="duckduckgo")

        with patch.dict("sys.modules", {"duckduckgo_search": None}):
            # Force ImportError in _search_duckduckgo
            with patch.object(
                provider, "_search_duckduckgo", side_effect=ImportError("no module")
            ):
                results = provider.search("test query")
                assert results == []

    # ------------------------------------------------------------------
    # Page fetching
    # ------------------------------------------------------------------

    @patch("ai.web_search.requests.get")
    def test_fetch_page_success(self, mock_get):
        """Test successful page fetch and content extraction."""
        html = """
        <html>
        <head><title>Test Page</title></head>
        <body>
            <nav>Navigation</nav>
            <script>var x = 1;</script>
            <style>.cls { color: red; }</style>
            <main>
                <h1>Main Content</h1>
                <p>This is the page content.</p>
            </main>
            <footer>Footer text</footer>
        </body>
        </html>
        """
        mock_resp = MagicMock()
        mock_resp.text = html
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        provider = WebSearchProvider()
        result = provider.fetch_page("https://example.com/page")

        assert result["success"] is True
        assert result["url"] == "https://example.com/page"
        assert result["title"] == "Test Page"
        assert result["error"] is None
        # nav, script, style, footer should be stripped
        assert "Navigation" not in result["content"]
        assert "var x = 1" not in result["content"]
        assert "color: red" not in result["content"]
        assert "Footer text" not in result["content"]
        # Main content should be present
        assert "Main Content" in result["content"]
        assert "This is the page content." in result["content"]

    @patch("ai.web_search.requests.get")
    def test_fetch_page_with_truncation(self, mock_get):
        """Test page fetch truncates long content."""
        # Create content longer than max_chars
        long_text = "x" * 5000
        html = f"<html><head><title>Long Page</title></head><body><p>{long_text}</p></body></html>"
        mock_resp = MagicMock()
        mock_resp.text = html
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        provider = WebSearchProvider()
        result = provider.fetch_page("https://example.com/long", max_chars=2000)

        assert result["success"] is True
        assert result["title"] == "Long Page"
        # Content should be truncated
        assert len(result["content"]) < 5000
        assert "truncated" in result["content"]

    @patch("ai.web_search.requests.get")
    def test_fetch_page_with_error(self, mock_get):
        """Test page fetch handles errors gracefully."""
        mock_get.side_effect = Exception("Connection refused")

        provider = WebSearchProvider()
        result = provider.fetch_page("https://example.com/broken")

        assert result["success"] is False
        assert result["url"] == "https://example.com/broken"
        assert result["title"] == ""
        assert result["content"] == ""
        assert "Connection refused" in result["error"]

    @patch("ai.web_search.requests.get")
    def test_fetch_page_no_title(self, mock_get):
        """Test page fetch when page has no title tag."""
        html = "<html><body><p>Content without title</p></body></html>"
        mock_resp = MagicMock()
        mock_resp.text = html
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        provider = WebSearchProvider()
        result = provider.fetch_page("https://example.com/no-title")

        assert result["success"] is True
        assert result["title"] == ""
        assert "Content without title" in result["content"]

    # ------------------------------------------------------------------
    # search_fusion_docs
    # ------------------------------------------------------------------

    def test_search_fusion_docs_prepends_query(self):
        """Test search_fusion_docs prepends 'Autodesk Fusion 360 API'."""
        provider = WebSearchProvider()
        with patch.object(provider, "search", return_value=[]) as mock_search:
            provider.search_fusion_docs("extrude feature")
            mock_search.assert_called_once_with(
                "Autodesk Fusion 360 API extrude feature"
            )

    def test_search_fusion_docs_returns_results(self):
        """Test search_fusion_docs returns search results."""
        provider = WebSearchProvider()
        fake_results = [
            {"title": "Fusion API", "url": "https://example.com", "snippet": "doc"}
        ]
        with patch.object(provider, "search", return_value=fake_results):
            results = provider.search_fusion_docs("sketch constraints")
        assert len(results) == 1
        assert results[0]["title"] == "Fusion API"

    # ------------------------------------------------------------------
    # search_and_summarize
    # ------------------------------------------------------------------

    def test_search_and_summarize_combines_results(self):
        """Test search_and_summarize fetches pages and compiles summary."""
        provider = WebSearchProvider()

        fake_search_results = [
            {"title": "Result 1", "url": "https://example.com/1", "snippet": "Snippet 1"},
            {"title": "Result 2", "url": "https://example.com/2", "snippet": "Snippet 2"},
        ]
        fake_page = {
            "url": "https://example.com/1",
            "title": "Result 1",
            "content": "Page content here",
            "success": True,
            "error": None,
        }

        with patch.object(provider, "search", return_value=fake_search_results):
            with patch.object(provider, "fetch_page", return_value=fake_page):
                summary = provider.search_and_summarize("test query", max_results=2)

        assert "Search Results for: test query" in summary
        assert "Result 1" in summary
        assert "Result 2" in summary
        assert "Page content here" in summary
        assert "https://example.com/1" in summary

    def test_search_and_summarize_no_results(self):
        """Test search_and_summarize with no results."""
        provider = WebSearchProvider()
        with patch.object(provider, "search", return_value=[]):
            summary = provider.search_and_summarize("nonexistent query")
        assert "No search results found" in summary

    def test_search_and_summarize_fetch_failure_uses_snippet(self):
        """Test search_and_summarize falls back to snippet when fetch fails."""
        provider = WebSearchProvider()

        fake_results = [
            {"title": "Result 1", "url": "https://example.com/1", "snippet": "Fallback snippet"},
        ]
        failed_page = {
            "url": "https://example.com/1",
            "title": "",
            "content": "",
            "success": False,
            "error": "timeout",
        }

        with patch.object(provider, "search", return_value=fake_results):
            with patch.object(provider, "fetch_page", return_value=failed_page):
                summary = provider.search_and_summarize("test", max_results=1)

        assert "Fallback snippet" in summary


# ======================================================================
# TestWebSearchMCPTools
# ======================================================================


class TestWebSearchMCPTools:
    """Tests for web search MCP tool registration and categories."""

    def test_web_search_tool_registered(self):
        """Test web_search tool is in TOOL_DEFINITIONS."""
        names = [t["name"] for t in TOOL_DEFINITIONS]
        assert "web_search" in names

    def test_web_fetch_tool_registered(self):
        """Test web_fetch tool is in TOOL_DEFINITIONS."""
        names = [t["name"] for t in TOOL_DEFINITIONS]
        assert "web_fetch" in names

    def test_fusion_docs_search_tool_registered(self):
        """Test fusion_docs_search tool is in TOOL_DEFINITIONS."""
        names = [t["name"] for t in TOOL_DEFINITIONS]
        assert "fusion_docs_search" in names

    def test_web_search_tool_schema(self):
        """Test web_search tool has correct schema."""
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "web_search")
        assert "query" in tool["input_schema"]["properties"]
        assert "max_results" in tool["input_schema"]["properties"]
        assert tool["input_schema"]["required"] == ["query"]

    def test_web_fetch_tool_schema(self):
        """Test web_fetch tool has correct schema."""
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "web_fetch")
        assert "url" in tool["input_schema"]["properties"]
        assert "max_chars" in tool["input_schema"]["properties"]
        assert tool["input_schema"]["required"] == ["url"]

    def test_fusion_docs_search_tool_schema(self):
        """Test fusion_docs_search tool has correct schema."""
        tool = next(t for t in TOOL_DEFINITIONS if t["name"] == "fusion_docs_search")
        assert "query" in tool["input_schema"]["properties"]
        assert tool["input_schema"]["required"] == ["query"]

    def test_web_search_tools_in_categories(self):
        """Test web search tools have category assignments."""
        assert TOOL_CATEGORIES["web_search"] == "Web Search"
        assert TOOL_CATEGORIES["web_fetch"] == "Web Search"
        assert TOOL_CATEGORIES["fusion_docs_search"] == "Web Search"

    def test_web_search_tool_group_exists(self):
        """Test web_search group exists in TOOL_GROUPS."""
        assert "web_search" in TOOL_GROUPS
        assert "web_search" in TOOL_GROUPS["web_search"]
        assert "web_fetch" in TOOL_GROUPS["web_search"]
        assert "fusion_docs_search" in TOOL_GROUPS["web_search"]

    def test_get_tools_for_web_search_group(self):
        """Test get_tools_for_groups returns web search tools."""
        tools = get_tools_for_groups(["web_search"])
        assert "web_search" in tools
        assert "web_fetch" in tools
        assert "fusion_docs_search" in tools

    def test_tool_execution_with_mocked_provider(self):
        """Test MCPServer can handle web search tools via bridge."""
        from mcp.server import MCPServer

        mock_bridge = MagicMock()
        mock_bridge.execute.return_value = {
            "status": "success",
            "results": [{"title": "Test", "url": "https://example.com", "snippet": "Test"}],
        }

        server = MCPServer(mock_bridge)
        result = server.execute_tool("web_search", {"query": "test query"})

        assert result["status"] == "success"
        mock_bridge.execute.assert_called_once_with(
            "web_search", {"query": "test query"}
        )


# ======================================================================
# TestWebSearchConfig
# ======================================================================


class TestWebSearchConfig:
    """Tests for web search configuration settings."""

    def test_default_web_search_enabled(self):
        """Test web_search_enabled defaults to True."""
        assert DEFAULTS["web_search_enabled"] is True

    def test_default_web_search_backend(self):
        """Test web_search_backend defaults to 'duckduckgo'."""
        assert DEFAULTS["web_search_backend"] == "duckduckgo"

    def test_default_web_search_searxng_url(self):
        """Test web_search_searxng_url defaults to None."""
        assert DEFAULTS["web_search_searxng_url"] is None

    def test_default_web_search_max_results(self):
        """Test web_search_max_results defaults to 5."""
        assert DEFAULTS["web_search_max_results"] == 5

    def test_default_web_search_timeout(self):
        """Test web_search_timeout defaults to 10."""
        assert DEFAULTS["web_search_timeout"] == 10

    def test_settings_get_web_search_enabled(self):
        """Test Settings.get returns web_search_enabled."""
        s = Settings()
        s._data = dict(DEFAULTS)
        s._loaded = True
        assert s.get("web_search_enabled") is True

    def test_settings_custom_backend(self):
        """Test Settings can store a custom backend."""
        s = Settings()
        s._data = dict(DEFAULTS)
        s._loaded = True
        s.set("web_search_backend", "searxng")
        assert s.get("web_search_backend") == "searxng"

    def test_settings_custom_searxng_url(self):
        """Test Settings can store a SearXNG URL."""
        s = Settings()
        s._data = dict(DEFAULTS)
        s._loaded = True
        s.set("web_search_searxng_url", "http://localhost:9999")
        assert s.get("web_search_searxng_url") == "http://localhost:9999"

    def test_settings_custom_timeout(self):
        """Test Settings can store a custom timeout."""
        s = Settings()
        s._data = dict(DEFAULTS)
        s._loaded = True
        s.set("web_search_timeout", 30)
        assert s.get("web_search_timeout") == 30
