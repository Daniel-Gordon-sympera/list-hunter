# tests/test_http_client.py
"""Tests for http_client: proxy wiring and Cloudflare detection."""

from unittest.mock import patch

import pytest
from crawl4ai.async_configs import ProxyConfig

import config
from http_client import ScraperClient, is_cloudflare_challenge


class TestProxyConfig:
    def test_no_proxy_when_env_unset(self):
        """No proxy_config when PROXY_URL is None."""
        with patch.object(config, "PROXY_URL", None):
            client = ScraperClient()
            assert client._browser_config.proxy_config is None

    def test_proxy_configured_when_env_set(self):
        """proxy_config wired to BrowserConfig when PROXY_URL is set."""
        with patch.object(config, "PROXY_URL", "http://user:pass@proxy.example.com:8080"):
            client = ScraperClient()
            pc = client._browser_config.proxy_config
            assert isinstance(pc, ProxyConfig)
            assert pc.server == "http://proxy.example.com:8080"
            assert pc.username == "user"
            assert pc.password == "pass"

    def test_brightdata_proxy_format_parsed(self):
        """Bright Data residential proxy URL parses correctly."""
        url = "http://brd-customer-hl_1a29887d-zone-residential_proxy1:pi63dagbslkk@brd.superproxy.io:33335"
        with patch.object(config, "PROXY_URL", url):
            client = ScraperClient()
            pc = client._browser_config.proxy_config
            assert pc.server == "http://brd.superproxy.io:33335"
            assert pc.username == "brd-customer-hl_1a29887d-zone-residential_proxy1"
            assert pc.password == "pi63dagbslkk"


class TestCloudflareDetection:
    def test_detects_challenge_page(self):
        assert is_cloudflare_challenge("<title>Just a moment...</title>")

    def test_clean_html_not_flagged(self):
        assert not is_cloudflare_challenge("<html><body>Hello</body></html>")
