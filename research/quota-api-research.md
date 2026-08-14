# LLM Provider Quota / Balance API Research

Three providers: exact endpoints, auth, response shapes, rate limits, docs URLs, and caveats.
All endpoints were verified by inspecting the official client source and/or hitting the endpoint live with a dummy credential to confirm routing/response shape.

---

## 1) OpenAI — Codex / Codex Plus weekly usage

**Bottom line: there is no public/documented usage API, but the Codex CLI itself fetches usage from an unofficial ChatGPT backend endpoint, and that is the endpoint a dashboard should use.**

### Primary endpoint (unofficial, used by the official Codex CLI)

`GET https://chatgpt.com/backend-api/wham/usage`

Headers:
- `Authorization: Bearer <access_token>` — **required**. An **OAuth access token (JWT) issued by auth.openai.com**, NOT a platform API key.
- `ChatGPT-Account-Id: <account_id>` — optional; needed to disambiguate accounts.
- `Accept: application/json`

curl example (token from `~/.codex/auth.json` → `tokens.access_token`):

```bash
curl -s -X GET "https://chatgpt.com/backend-api/wham/usage" \
  -H "Authorization: Bearer $CODEX_ACCESS_TOKEN" \
  -H "ChatGPT-Account-Id: <account_id>" \
  -H "Accept: application/json"
```

Sample JSON response (shape from OpenTokenUsage reverse-engineering + CodexBar research):

```jsonc
{
  "plan_type": "plus",                       // "free" | "plus" | "pro" | "business" | ...
  "rate_limit": {
    "primary_window": {                      // 5-hour rolling window
      "used_percent": 6,                     // integer 0-100
      "reset_at": 1738300000,                // unix seconds
      "limit_window_seconds": 18000          // 5h = 18000s
    },
    "secondary_window": {                    // weekly (7-day) window
      "used_percent": 24,
      "reset_at": 1738900000,
      "limit_window_seconds": 604800         // 7d
    }
  },
  "code_review_rate_limit": {                // separate weekly code-review limit (optional)
    "primary_window": { "used_percent": 0, "reset_at": 1738900000, "limit_window_seconds": 604800 }
  },
  "credits": {                               // optional — purchased credits
    "has_credits": true,
    "unlimited": false,
    "balance": 820.6969075
  },
  "rate_limit_reset_credits": {              // optional — on-demand resets
    "available_count": 1
  }
}
```

> The "5h remaining" and "weekly limit" shown in the Codex TUI **/status** come from `primary_window` (5h) and `secondary_window` (weekly). **Codex Plus** (`plan_type: "plus"`) is identified by the `plan_type` field. Both windows are enforced independently — hitting either throttles you.

### Companion endpoints (same host/auth)
- `GET https://chatgpt.com/backend-api/wham/rate-limit-reset-credits` — list available on-demand resets.
- `POST https://chatgpt.com/backend-api/wham/rate-limit-reset-credits/consume` — redeem a reset (`{"redeem_request_id": "..."}`).

**Authoritative source:** the official Codex CLI (openai/codex). In `codex-rs/backend-client/src/client/rate_limit_resets.rs` it builds `{base}/wham/usage` for ChatGPT-style hosts (`https://chatgpt.com/backend-api`) or `{base}/api/codex/usage` for Codex-API hosts. Headers come from `auth_provider.add_auth_headers()` (Bearer JWT) + optional `ChatGPT-Account-Id` + optional `X-OpenAI-Fedramp: true`.

There is also a **local JSON-RPC** path if the dashboard controls the machine: run `codex -s read-only -a untrusted app-server` and call `account/read` and `account/rateLimits/read`.

### Auth token acquisition / refresh (required)
Credentials live in `~/.codex/auth.json` (or `$CODEX_HOME/auth.json`; or OS keyring):

```jsonc
{
  "OPENAI_API_KEY": null,
  "tokens": {
    "access_token": "<jwt>",          // <- use this as the Bearer
    "refresh_token": "<token>",
    "id_token": "<jwt>",
    "account_id": "<uuid>"            // <- ChatGPT-Account-Id header
  },
  "last_refresh": "2026-01-28T08:05:37Z"
}
```

Refresh (client id observed in the CLI):

```bash
curl -s "https://auth.openai.com/oauth/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=refresh_token" \
  -d "client_id=app_EMoamEEZ73f0CkXaXp7hrann" \
  -d "refresh_token=$REFRESH_TOKEN"
```

**Caveat (important):** This is an **unofficial, reverse-engineered endpoint used by the official CLI**. It is NOT in any public OpenAI docs and may change without notice. It requires a **logged-in Codex/ChatGPT OAuth session** — a raw platform API key will NOT work (verified live: `sk-...` keys get `invalid_api_key / incorrect api key`).

**CORS:** Live probe returned `access-control-allow-origin` reflecting the request origin + `allow-credentials: true` → browser/WebView calls work (no wildcard + credentials).

---

## 2) OpenCode Go / OpenCode Zen

### Go subscription usage endpoint (confirmed from source)

`GET https://opencode.ai/zen/go/v1/usage`

Headers:
- `Authorization: Bearer <your_api_key>` — **required**. Your OpenCode Zen/**Go API key** (from the opencode.ai console). The handler matches `/^Bearer (S+)$/` and looks up the key.

curl example:

```bash
curl -s "https://opencode.ai/zen/go/v1/usage" \
  -H "Authorization: Bearer $OPCODE_API_KEY"
```

Sample JSON response (exact shape from source `packages/console/app/src/routes/zen/go/v1/usage.ts`):

```json
{
  "usage": {
    "rolling": {                       // 5-hour limit  ($12)
      "status": "ok",                  // "ok" | "rate-limited"
      "percent": 42,                   // integer 0-100
      "resetsAt": "2026-08-13T19:00:00.000Z"  // ISO 8601
    },
    "weekly": {                        // weekly limit   ($30)
      "status": "ok",
      "percent": 18,
      "resetsAt": "2026-08-16T00:00:00.000Z"
    },
    "monthly": {                       // monthly limit  ($60)
      "status": "rate-limited",
      "percent": 100,
      "resetsAt": "2026-08-31T00:00:00.000Z"
    }
  }
}
```

Error shapes (verified live with a dummy key):
- `401` → `{"type":"error","error":{"type":"AuthError","message":"Unauthorized"}}`
- `403` (no Go subscription) → `{"type":"error","error":{"type":"EntitlementError","message":"OpenCode Go subscription required."}}`
- A `200` means the key has an active Go subscription.

> **Plan/tier identification:** A single key returns **all three windows at once** (rolling 5h, weekly, monthly). OpenCode Go is ONE flat subscription ("lite" plan) with nested dollar limits — there are not three separate plans; the three limits are enforced concurrently on the same key. To know a key's tier, read all three `usage.*` blocks. Source (`subscription.ts`, `LiteData.getLimits()`) sets `rollingLimit=12` ($12/5h), `weeklyLimit=30`, `monthlyLimit=60`, window `rollingWindow=5` hours — matching official docs.

**Docs asked about:** `opencode.ai/docs/go` and `opencode.ai/docs/zen`. Handler source: `sst/opencode` → `packages/console/app/src/routes/zen/go/v1/usage.ts` (+ `packages/console/core/src/subscription.ts`, `lite.ts`). Zen chat gateway endpoints share the host: `https://opencode.ai/zen/v1/responses`, `https://opencode.ai/zen/go/v1/chat/completions`, etc. Zed added OpenCode Go support in PR #53651.

**Zen (pay-as-you-go) vs Go (flat):** Zen is a per-token prepaid wallet; Go is the flat capped plan above. For Zen there is no public "balance" REST endpoint in the repo — billing/usage is managed in the opencode.ai console, not via a public programmatic quota endpoint.

**Caveat:** The Go usage endpoint is **not documented as a stable API** — it is what the console UI and clients use. Semi-internal. Requires a **valid OpenCode Go API key** (no browser session).

**CORS:** Live OPTIONS preflight returned **404 with no `access-control-allow-origin`** → the endpoint is **NOT CORS-enabled**. Calls from a browser/WebView will be CORS-blocked — use a local proxy or a native HTTP client.

---

## 3) DeepSeek — balance

**Confirmed. Officially documented; requires only a plain API key.**

`GET https://api.deepseek.com/user/balance`

Headers:
- `Authorization: Bearer <your_api_key>` — **required** (normal DeepSeek platform key; no login session).
- `Accept: application/json`

curl example:

```bash
curl -L -X GET "https://api.deepseek.com/user/balance" \
  -H "Accept: application/json" \
  -H "Authorization: Bearer <TOKEN>"
```

Sample JSON response (official docs; live-verified — invalid key returns 401 `authentication_error`):

```json
{
  "is_available": true,
  "balance_infos": [
    {
      "currency": "CNY",              // CNY | USD
      "total_balance": "110.00",      // granted + topped-up
      "granted_balance": "10.00",
      "topped_up_balance": "100.00"
    }
  ]
}
```

- `is_available` = sufficient balance for API calls. Each `balance_infos[]` entry is a currency bucket (CNY and/or USD).

**Official docs:** https://api-docs.deepseek.com/api/get-user-balance/ (EN) | https://api-docs.deepseek.com/zh-cn/api/get-user-balance/ (ZH)

**Rate limits/quota:** DeepSeek enforces **concurrency limits** (not fixed RPM). Per account: `deepseek-v4-pro` = 500 concurrent, `deepseek-v4-flash` = 2500; exceeding → HTTP 429. Higher via a capacity-expansion request. Docs: https://api-docs.deepseek.com/quick_start/rate_limit

**CORS:** Live probe returned `access-control-allow-origin: <origin>` + `access-control-allow-credentials: true` → **CORS-enabled**, safe from a WebView (credentialed fetch, not wildcard).

---

## Dashboard implementation notes (desktop plugin WebView)

| Provider | Endpoint | Auth | Needs login session? | CORS-friendly? | Cost/quota semantics |
|---|---|---|---|---|---|
| Codex / Codex Plus | `GET chatgpt.com/backend-api/wham/usage` | `Bearer <OAuth JWT>` | **Yes** — OAuth token | Yes (reflects origin) | 5h + weekly windows + credits; `plan_type` tier |
| OpenCode Go | `GET opencode.ai/zen/go/v1/usage` | `Bearer <Zen API key>` | No (plain key) | **No** (no CORS) | rolling 5h/$12 + weekly/$30 + monthly/$60 |
| DeepSeek | `GET api.deepseek.com/user/balance` | `Bearer <API key>` | No (plain key) | Yes | balance buckets per currency |

**Biggest integration caveats:**
1. **Codex & OpenCode endpoints are undocumented/unofficial** — used by official clients/consoles but may change without notice.
2. **Codex needs a captured/refreshed OAuth session** (auth.json + refresh flow); a raw OpenAI platform key won't work. OpenCode & DeepSeek need only the plain API key.
3. **OpenCode Go usage is not CORS-enabled** — proxy it (local server or platform native fetch) if the dashboard renders in a browser WebView. Codex & DeepSeek reflect CORS.
4. For Codex, consider the **local `codex app-server` JSON-RPC** (`account/rateLimits/read`) as an alternative that avoids token parsing.

### Key source URLs
- Codex CLI usage client: https://github.com/openai/codex → `codex-rs/backend-client/src/client/rate_limit_resets.rs`
- Codex reverse-engineering: https://github.com/PowerUserZ/OpenTokenUsage/blob/main/docs/providers/codex.md , https://github.com/steipete/CodexBar/blob/main/docs/codex.md
- OpenCode usage handler source: https://github.com/sst/opencode/blob/dev/packages/console/app/src/routes/zen/go/v1/usage.ts (+ `subscription.ts`, `lite.ts`)
- OpenCode Go docs: https://opencode.ai/docs/go | Zen: https://opencode.ai/docs/zen
- DeepSeek balance docs: https://api-docs.deepseek.com/api/get-user-balance/ | rate limits: https://api-docs.deepseek.com/quick_start/rate_limit

---

## Appendix: Cloudflare 1010 gotcha (opencode.ai)

opencode.ai sits behind Cloudflare. Requests made with the default python-urllib
User-Agent ("Python-urllib/3.x") are rejected with HTTP 403 / Cloudflare error 1010
("Access denied"), even with a valid key. Sending a browser-like User-Agent
(e.g. "curl/8.7.1" or the Mozilla string used in dashboard/plugin_api.py) returns 200.
The backend therefore always sets a browser-like User-Agent on every request.
