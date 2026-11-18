"""OAuth redirect handling tests with mocked GitHub HTTP calls."""
from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from agent.github_auth import create_login_url, exchange_code


class OAuthRedirectTests(unittest.TestCase):
    def setUp(self):
        self.redirect_uri = "https://issue-flow-ai.vercel.app/auth/github/callback"
        self.config = SimpleNamespace(
            ready=True,
            client_id="mock-client-id",
            client_secret="mock-client-secret",
        )

    def test_authorize_url_contains_exact_environment_callback(self):
        with patch("agent.github_auth.get_github_app_config", return_value=self.config):
            url, state = create_login_url(self.redirect_uri)
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query["redirect_uri"], [self.redirect_uri])
        self.assertEqual(len(query["state"][0]), len(state))
        self.assertEqual(query["prompt"], ["select_account"])

    def test_code_exchange_reuses_same_callback_uri(self):
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"access_token": "mock-access-token"},
        )
        with patch("agent.github_auth.get_github_app_config", return_value=self.config), \
             patch("agent.github_auth.requests.post", return_value=response) as post:
            token = exchange_code("mock-code", redirect_uri=self.redirect_uri)
        self.assertEqual(token, "mock-access-token")
        self.assertEqual(
            post.call_args.kwargs["data"]["redirect_uri"],
            self.redirect_uri,
        )

    def test_legacy_exchange_without_redirect_keeps_compatible_request_shape(self):
        response = SimpleNamespace(
            raise_for_status=lambda: None,
            json=lambda: {"access_token": "mock-access-token"},
        )
        with patch("agent.github_auth.get_github_app_config", return_value=self.config), \
             patch("agent.github_auth.requests.post", return_value=response) as post:
            exchange_code("mock-code")
        self.assertNotIn("redirect_uri", post.call_args.kwargs["data"])


if __name__ == "__main__":
    unittest.main()
