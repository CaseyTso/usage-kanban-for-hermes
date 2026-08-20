"""
Unit tests for usage-kanban backend (dashboard/plugin_api.py).
Tests Antigravity provider integration, summary parsing with real sanitized schema,
plan fetching, error handling, proxy resolution, credential discovery,
and token/proxy security using stdlib unittest and mock.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import dashboard.plugin_api as api


class TestAntigravitySummaryParsing(unittest.TestCase):
    def test_summary_real_schema_two_groups_four_windows(self):
        # Real sanitized schema: groups[].displayName/description, buckets[].bucketId/displayName/remainingFraction/resetTime
        payload = {
            "groups": [
                {
                    "displayName": "Gemini",
                    "description": "Gemini models quota",
                    "buckets": [
                        {
                            "bucketId": "gemini-5h",
                            "displayName": "5 小时",
                            "remainingFraction": 0.9998,
                            "resetTime": "2026-04-01T05:00:00Z",
                        },
                        {
                            "bucketId": "gemini-weekly",
                            "displayName": "本周",
                            "remainingFraction": 0.85,
                            "resetTime": "2026-04-05T00:00:00Z",
                        },
                    ],
                },
                {
                    "displayName": "Claude / GPT",
                    "description": "Third-party models quota",
                    "buckets": [
                        {
                            "bucketId": "3p-5h",
                            "displayName": "5 小时",
                            "remainingFraction": 1.0,
                            "resetTime": "2026-04-01T05:00:00Z",
                        },
                        {
                            "bucketId": "3p-weekly",
                            "displayName": "本周",
                            "remainingFraction": 0.575,
                            "resetTime": "2026-04-05T00:00:00Z",
                        },
                    ],
                },
            ]
        }
        windows = api._parse_antigravity_summary(payload)
        self.assertEqual(len(windows), 4)

        # Gemini 5h: (1 - 0.9998) * 100 = 0.02%
        self.assertEqual(windows[0]["key"], "gemini-5h")
        self.assertEqual(windows[0]["group"], "Gemini")
        self.assertEqual(windows[0]["label"], "5 小时")
        self.assertEqual(windows[0]["usedPercent"], 0.02)
        self.assertEqual(windows[0]["resetsAt"], "2026-04-01T05:00:00Z")
        self.assertEqual(windows[0]["groupDescription"], "Gemini models quota")

        # Gemini Weekly: (1 - 0.85) * 100 = 15.0%
        self.assertEqual(windows[1]["key"], "gemini-weekly")
        self.assertEqual(windows[1]["group"], "Gemini")
        self.assertEqual(windows[1]["label"], "本周")
        self.assertEqual(windows[1]["usedPercent"], 15.0)
        self.assertEqual(windows[1]["resetsAt"], "2026-04-05T00:00:00Z")

        # Claude / GPT 5h: (1 - 1.0) * 100 = 0.0%
        self.assertEqual(windows[2]["key"], "3p-5h")
        self.assertEqual(windows[2]["group"], "Claude / GPT")
        self.assertEqual(windows[2]["label"], "5 小时")
        self.assertEqual(windows[2]["usedPercent"], 0.0)
        self.assertEqual(windows[2]["resetsAt"], "2026-04-01T05:00:00Z")
        self.assertEqual(windows[2]["groupDescription"], "Third-party models quota")

        # Claude / GPT Weekly: (1 - 0.575) * 100 = 42.5%
        self.assertEqual(windows[3]["key"], "3p-weekly")
        self.assertEqual(windows[3]["group"], "Claude / GPT")
        self.assertEqual(windows[3]["label"], "本周")
        self.assertEqual(windows[3]["usedPercent"], 42.5)
        self.assertEqual(windows[3]["resetsAt"], "2026-04-05T00:00:00Z")

        # Verify exact 4 stable keys
        keys = [w["key"] for w in windows]
        self.assertEqual(keys, ["gemini-5h", "gemini-weekly", "3p-5h", "3p-weekly"])

    def test_response_wrapper_groups(self):
        # Support payload wrapped in response.groups
        payload = {
            "response": {
                "groups": [
                    {
                        "displayName": "Gemini",
                        "buckets": [
                            {
                                "bucketId": "gemini-5h",
                                "displayName": "5 hours",
                                "remainingFraction": 0.5,
                                "resetTime": "2026-04-01T05:00:00Z",
                            }
                        ],
                    }
                ]
            }
        }
        windows = api._parse_antigravity_summary(payload)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["key"], "gemini-5h")
        self.assertEqual(windows[0]["group"], "Gemini")
        self.assertEqual(windows[0]["label"], "5 小时")
        self.assertEqual(windows[0]["usedPercent"], 50.0)

    def test_fallback_labels_and_descriptions(self):
        # Fallback to name/label when displayName is absent, and bucket description
        payload = {
            "groups": [
                {
                    "name": "3P Models",
                    "buckets": [
                        {
                            "bucketId": "3p-weekly",
                            "label": "Weekly Window",
                            "description": "Weekly rolling quota",
                            "remainingFraction": 0.8,
                            "reset_at": 1775347200,
                        }
                    ],
                }
            ]
        }
        windows = api._parse_antigravity_summary(payload)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["key"], "3p-weekly")
        self.assertEqual(windows[0]["group"], "Claude / GPT")
        self.assertEqual(windows[0]["label"], "本周")
        self.assertEqual(windows[0]["usedPercent"], 20.0)
        self.assertEqual(windows[0]["description"], "Weekly rolling quota")

    def test_disabled_bucket_and_group(self):
        payload = {
            "groups": [
                {
                    "displayName": "Gemini",
                    "buckets": [
                        {
                            "bucketId": "gemini-5h",
                            "displayName": "5 hours",
                            "remainingFraction": 0.9,
                            "disabled": False,
                        },
                        {
                            "bucketId": "gemini-weekly",
                            "displayName": "Weekly",
                            "remainingFraction": 0.5,
                            "disabled": True,  # Disabled bucket should be skipped
                        },
                    ],
                },
                {
                    "displayName": "Disabled Group",
                    "disabled": True,  # Disabled group should be skipped
                    "buckets": [
                        {
                            "bucketId": "disabled-5h",
                            "displayName": "5 hours",
                            "remainingFraction": 0.8,
                        }
                    ],
                },
            ]
        }
        windows = api._parse_antigravity_summary(payload)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["key"], "gemini-5h")
        self.assertEqual(windows[0]["group"], "Gemini")
        self.assertEqual(windows[0]["label"], "5 小时")
        self.assertEqual(windows[0]["usedPercent"], 10.0)

    def test_nested_remaining_fraction(self):
        payload = {
            "groups": [
                {
                    "displayName": "Gemini",
                    "buckets": [
                        {
                            "bucketId": "gemini-5h",
                            "displayName": "5 hours",
                            "remaining": {
                                "remainingFraction": 0.75
                            },
                            "resetTime": "2026-04-01T05:00:00Z",
                        }
                    ],
                }
            ]
        }
        windows = api._parse_antigravity_summary(payload)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0]["usedPercent"], 25.0)

    def test_oneof_and_snake_case_remaining_fraction(self):
        payload = {
            "groups": [{
                "displayName": "Gemini Models",
                "buckets": [
                    {
                        "bucketId": "gemini-5h",
                        "displayName": "Five Hour Limit Remaining",
                        "remaining": {"case": "remainingFraction", "value": 0.25},
                        "reset_time": "2026-04-01T05:00:00Z",
                    },
                    {
                        "bucketId": "gemini-weekly",
                        "displayName": "Weekly Limit Remaining",
                        "remaining_fraction": 0.75,
                    },
                ],
            }]
        }

        windows = api._parse_antigravity_summary(payload)
        self.assertEqual([w["usedPercent"] for w in windows], [75.0, 25.0])
        self.assertEqual(windows[0]["resetsAt"], "2026-04-01T05:00:00Z")

    def test_percentage_clamp_and_decimals(self):
        # 1. Decimal precision: 0.9998 -> 0.02
        payload1 = {"groups": [{"displayName": "Gemini", "buckets": [{"bucketId": "gemini-5h", "displayName": "5h", "remainingFraction": 0.9998}]}]}
        windows1 = api._parse_antigravity_summary(payload1)
        self.assertEqual(windows1[0]["usedPercent"], 0.02)

        # 2. Over 100% remaining (> 1.0) clamps usedPercent to 0.0
        payload2 = {"groups": [{"displayName": "Gemini", "buckets": [{"bucketId": "gemini-5h", "displayName": "5h", "remainingFraction": 1.5}]}]}
        windows2 = api._parse_antigravity_summary(payload2)
        self.assertEqual(windows2[0]["usedPercent"], 0.0)

        # 3. Negative remaining (< 0.0) clamps usedPercent to 100.0
        payload3 = {"groups": [{"displayName": "Gemini", "buckets": [{"bucketId": "gemini-5h", "displayName": "5h", "remainingFraction": -0.2}]}]}
        windows3 = api._parse_antigravity_summary(payload3)
        self.assertEqual(windows3[0]["usedPercent"], 100.0)

    def test_missing_fraction_not_exhausted(self):
        # When remainingFraction is missing or None, usedPercent should be None, not 100% (exhausted)
        payload = {
            "groups": [
                {
                    "displayName": "Gemini",
                    "buckets": [
                        {
                            "bucketId": "gemini-unknown",
                            "displayName": "Unknown Window",
                            "resetTime": "2026-04-01T05:00:00Z",
                        }
                    ],
                }
            ]
        }
        windows = api._parse_antigravity_summary(payload)
        self.assertEqual(len(windows), 1)
        self.assertIsNone(windows[0]["usedPercent"])


class TestAntigravityPlan(unittest.TestCase):
    def test_fetch_plan_request_body_and_paid_tier_priority(self):
        called_requests = []

        def mock_http(url, headers=None, method="GET", form=None, json_body=None, timeout=15, proxy_url=None):
            called_requests.append({"url": url, "headers": headers, "body": json_body})
            return 200, {
                "paidTier": {"name": "Google AI Pro"},
                "currentTier": {"name": "Google AI Free"},
                "plan": {"name": "Standard"},
            }

        with patch.object(api, "_http_json", side_effect=mock_http):
            plan = api._fetch_antigravity_plan("test-token", proxy_url=None)

        self.assertEqual(len(called_requests), 1)
        # loadCodeAssist request body must be {"metadata": {"ideType": "ANTIGRAVITY"}}
        self.assertEqual(called_requests[0]["body"], {"metadata": {"ideType": "ANTIGRAVITY"}})
        # paidTier.name has highest priority
        self.assertEqual(plan, "Google AI Pro")

    def test_fetch_plan_failure_fallback_none(self):
        # When plan query fails (e.g. 404 or empty), must return None, NOT "Google AI Pro"
        with patch.object(api, "_http_json", return_value=(404, {})):
            plan = api._fetch_antigravity_plan("test-token", proxy_url=None)
            self.assertIsNone(plan)

        with patch.object(api, "_http_json", side_effect=Exception("Network error")):
            plan2 = api._fetch_antigravity_plan("test-token", proxy_url=None)
            self.assertIsNone(plan2)

        with patch.object(api, "_http_json", return_value=(200, {})):
            plan3 = api._fetch_antigravity_plan("test-token", proxy_url=None)
            self.assertIsNone(plan3)


class TestAntigravityDiscovery(unittest.TestCase):
    def test_discover_credentials_and_disabled(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            auth_dir = Path(tmpdir)
            active_file = auth_dir / "antigravity-active.json"
            active_file.write_text(json.dumps({
                "id": "acc-1",
                "email": "user1@example.com",
                "alias": "Main Account",
                "project_id": "proj-12345",
                "access_token": "dummy-token-active",
                "disabled": False,
            }), encoding="utf-8")

            disabled_file = auth_dir / "antigravity-disabled.json"
            disabled_file.write_text(json.dumps({
                "id": "acc-2",
                "email": "user2@example.com",
                "access_token": "dummy-token-disabled",
                "disabled": True,
            }), encoding="utf-8")

            other_file = auth_dir / "other-service.json"
            other_file.write_text(json.dumps({
                "token": "other-token",
            }), encoding="utf-8")

            with patch.dict(os.environ, {api.ANTIGRAVITY_AUTH_DIR_ENV: str(auth_dir)}):
                creds = api._discover_antigravity_credentials()

            self.assertEqual(len(creds), 1)
            self.assertEqual(creds[0]["id"], "acc-1")
            self.assertEqual(creds[0]["email"], "user1@example.com")
            self.assertEqual(creds[0]["alias"], "Main Account")
            self.assertEqual(creds[0]["project_id"], "proj-12345")
            self.assertEqual(creds[0]["access_token"], "dummy-token-active")


class TestAntigravityProxyConfig(unittest.TestCase):
    def test_proxy_from_env_priority(self):
        with patch.dict(os.environ, {api.ANTIGRAVITY_PROXY_URL_ENV: "http://127.0.0.1:8888"}):
            self.assertEqual(api._get_cliproxy_proxy_url(), "http://127.0.0.1:8888")

    def test_proxy_from_config_file_top_level(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_file = Path(tmpdir) / "config.yaml"
            cfg_file.write_text("proxy-url: \"http://127.0.0.1:7890\"\nother-key: val\n", encoding="utf-8")
            with patch.dict(os.environ, {
                api.ANTIGRAVITY_PROXY_URL_ENV: "",
                api.ANTIGRAVITY_CONFIG_FILE_ENV: str(cfg_file),
            }):
                self.assertEqual(api._get_cliproxy_proxy_url(), "http://127.0.0.1:7890")

    def test_nested_proxy_not_mistakenly_picked(self):
        # Ensure indented nested keys under providers: are ignored
        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_file = Path(tmpdir) / "config.yaml"
            cfg_content = (
                "providers:\n"
                "  custom-provider:\n"
                "    proxy-url: \"http://nested-proxy:9999\"\n"
                "proxy-url: \"http://top-proxy:7890\"\n"
            )
            cfg_file.write_text(cfg_content, encoding="utf-8")
            with patch.dict(os.environ, {
                api.ANTIGRAVITY_PROXY_URL_ENV: "",
                api.ANTIGRAVITY_CONFIG_FILE_ENV: str(cfg_file),
            }):
                self.assertEqual(api._get_cliproxy_proxy_url(), "http://top-proxy:7890")

    def test_direct_none_empty_no_proxy(self):
        # Empty string is an explicit direct sentinel: _http_json uses a blank
        # ProxyHandler so inherited HTTP(S)_PROXY values are also bypassed.
        with patch.dict(os.environ, {api.ANTIGRAVITY_PROXY_URL_ENV: "direct"}):
            self.assertEqual(api._get_cliproxy_proxy_url(), "")

        with patch.dict(os.environ, {api.ANTIGRAVITY_PROXY_URL_ENV: "none"}):
            self.assertEqual(api._get_cliproxy_proxy_url(), "")

        with tempfile.TemporaryDirectory() as tmpdir:
            cfg_file = Path(tmpdir) / "config.yaml"
            cfg_file.write_text("proxy-url: \"direct\"\n", encoding="utf-8")
            with patch.dict(os.environ, {
                api.ANTIGRAVITY_PROXY_URL_ENV: "",
                api.ANTIGRAVITY_CONFIG_FILE_ENV: str(cfg_file),
            }):
                self.assertEqual(api._get_cliproxy_proxy_url(), "")

            cfg_file.write_text("proxy-url: none\n", encoding="utf-8")
            with patch.dict(os.environ, {
                api.ANTIGRAVITY_PROXY_URL_ENV: "",
                api.ANTIGRAVITY_CONFIG_FILE_ENV: str(cfg_file),
            }):
                self.assertEqual(api._get_cliproxy_proxy_url(), "")

    def test_direct_disables_inherited_environment_proxy(self):
        fake_opener = MagicMock()
        fake_response = MagicMock()
        fake_response.__enter__.return_value.status = 200
        fake_response.__enter__.return_value.read.return_value = b"{}"
        fake_opener.open.return_value = fake_response

        with patch.object(api.urlrequest, "ProxyHandler", return_value="DIRECT") as proxy_handler:
            with patch.object(api.urlrequest, "build_opener", return_value=fake_opener) as build_opener:
                status, payload = api._http_json("https://example.invalid", proxy_url="")

        self.assertEqual((status, payload), (200, {}))
        proxy_handler.assert_called_once_with({})
        build_opener.assert_called_once_with("DIRECT")


class TestAntigravityErrorsAndSecurity(unittest.TestCase):
    def test_empty_summary_is_error_not_healthy(self):
        cred = {"file_path": None, "access_token": "dummy-token", "project_id": "dummy-project"}
        with patch.object(api, "_get_cliproxy_proxy_url", return_value=None):
            with patch.object(api, "_http_json", return_value=(200, {"groups": []})):
                res = api._fetch_antigravity(cred)

        self.assertEqual(res["status"], "error")
        self.assertIn("未返回可识别的窗口", res["error"])

    def test_socks_proxy_is_rejected_without_network_or_secret_echo(self):
        cred = {"file_path": None, "access_token": "dummy-token", "project_id": "dummy-project"}
        call = MagicMock()
        with patch.object(api, "_get_cliproxy_proxy_url", return_value="socks5://user:secret@127.0.0.1:1080"):
            with patch.object(api, "_http_json", call):
                res = api._fetch_antigravity(cred)

        self.assertEqual(res["status"], "error")
        self.assertIn("不支持 SOCKS", res["error"])
        self.assertNotIn("secret", res["error"])
        call.assert_not_called()

    def test_network_exception_does_not_leak_proxy_credentials(self):
        # Network errors should return a fixed safe Chinese error without echoing raw exception
        sensitive_proxy = "http://username:secretpassword@proxy.internal:8080"
        cred = {"file_path": None, "access_token": "token-xyz", "project_id": "proj-123"}

        with patch.object(api, "_get_cliproxy_proxy_url", return_value=sensitive_proxy):
            with patch.object(api, "_http_json", side_effect=Exception(f"Failed to connect via {sensitive_proxy}")):
                res = api._fetch_antigravity(cred)

        self.assertEqual(res["status"], "error")
        self.assertEqual(res["error"], "网络请求失败，请检查网络或代理配置")
        self.assertNotIn("secretpassword", res["error"])
        self.assertNotIn("username", res["error"])
        self.assertNotIn("proxy.internal", res["error"])

    def test_errors_do_not_leak_token_or_project(self):
        secret_token = "secret-oauth-token-xyz"
        secret_project = "secret-project-id-999"

        # 1. Missing token
        cred_no_token = {"file_path": None, "access_token": "", "project_id": secret_project}
        res1 = api._fetch_antigravity(cred_no_token)
        self.assertEqual(res1["status"], "error")
        self.assertIn("未检测到有效 access token", res1["error"])
        self.assertNotIn(secret_project, res1["error"])

        # 2. Missing project_id
        cred_no_proj = {"file_path": None, "access_token": secret_token, "project_id": ""}
        res2 = api._fetch_antigravity(cred_no_proj)
        self.assertEqual(res2["status"], "error")
        self.assertIn("未检测到 project_id", res2["error"])
        self.assertNotIn(secret_token, res2["error"])

        # 3. HTTP 401 error
        with patch.object(api, "_http_json", return_value=(401, {"error": "Invalid auth"})):
            cred = {"file_path": None, "access_token": secret_token, "project_id": secret_project}
            res3 = api._fetch_antigravity(cred)
            self.assertEqual(res3["status"], "error")
            self.assertIn("401", res3["error"])
            self.assertNotIn(secret_token, res3["error"])
            self.assertNotIn(secret_project, res3["error"])

        # 4. HTTP 403 error
        with patch.object(api, "_http_json", return_value=(403, {"error": "Forbidden"})):
            cred = {"file_path": None, "access_token": secret_token, "project_id": secret_project}
            res4 = api._fetch_antigravity(cred)
            self.assertEqual(res4["status"], "error")
            self.assertIn("403", res4["error"])
            self.assertNotIn(secret_token, res4["error"])

        # 5. HTTP 429 error
        with patch.object(api, "_http_json", return_value=(429, {"error": "Rate limit"})):
            cred = {"file_path": None, "access_token": secret_token, "project_id": secret_project}
            res5 = api._fetch_antigravity(cred)
            self.assertEqual(res5["status"], "error")
            self.assertIn("429", res5["error"])
            self.assertNotIn(secret_token, res5["error"])

    def test_token_retry_on_401(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "antigravity-account.json"
            file_path.write_text(json.dumps({
                "access_token": "refreshed-token-2",
                "project_id": "proj-test",
            }), encoding="utf-8")

            cred = {
                "file_path": file_path,
                "access_token": "expired-token-1",
                "project_id": "proj-test",
            }

            quota_success_payload = {
                "groups": [
                    {
                        "displayName": "Gemini",
                        "buckets": [{"bucketId": "gemini-5h", "displayName": "5h", "remainingFraction": 0.9}],
                    }
                ]
            }

            calls = []

            def mock_http(url, headers=None, method="GET", form=None, json_body=None, timeout=15, proxy_url=None):
                calls.append((url, headers.get("Authorization")))
                if url == api.ANTIGRAVITY_QUOTA_URL:
                    if headers.get("Authorization") == "Bearer expired-token-1":
                        return 401, {}
                    return 200, quota_success_payload
                if url == api.ANTIGRAVITY_PLAN_URL:
                    return 200, {"paidTier": {"name": "Google AI Pro"}}
                return 404, {}

            with patch.object(api, "_http_json", side_effect=mock_http):
                res = api._fetch_antigravity(cred)

            self.assertEqual(res["status"], "ok")
            self.assertEqual(res["plan"], "Google AI Pro")
            self.assertEqual(len(res["windows"]), 1)
            self.assertEqual(res["windows"][0]["key"], "gemini-5h")
            # Checked that the first call used expired-token-1 and second call used refreshed-token-2
            self.assertEqual(calls[0][1], "Bearer expired-token-1")
            self.assertEqual(calls[1][1], "Bearer refreshed-token-2")


class TestStatusRouteAggregation(unittest.TestCase):
    @patch.object(api, "_fetch_codex", return_value={"present": False, "status": "ok"})
    @patch.object(api, "_fetch_antigravity", return_value={"status": "ok", "plan": "Google AI Pro", "windows": []})
    @patch.object(api, "_discover_antigravity_credentials", return_value=[{
        "id": "ag-test",
        "alias": "Antigravity Main",
        "email": "user@example.com",
        "access_token": "token-test",
        "project_id": "proj-test",
        "hidden": False,
    }])
    def test_status_endpoint(self, mock_discover, mock_fetch_ag, mock_fetch_codex):
        status = api.get_status()
        self.assertIn("antigravity", status)
        ag_accounts = status["antigravity"]["accounts"]
        self.assertEqual(len(ag_accounts), 1)
        self.assertEqual(ag_accounts[0]["id"], "ag-test")
        self.assertEqual(ag_accounts[0]["alias"], "Antigravity Main")
        self.assertEqual(ag_accounts[0]["email"], "user@example.com")
        self.assertEqual(ag_accounts[0]["status"], "ok")
        self.assertEqual(ag_accounts[0]["plan"], "Google AI Pro")
        # Ensure secret fields are not returned in status
        self.assertNotIn("access_token", ag_accounts[0])
        self.assertNotIn("project_id", ag_accounts[0])


if __name__ == "__main__":
    unittest.main()
