"""
usage-kanban backend: aggregates quota data for the Hermes desktop plugin.

Mounted by the Hermes gateway at /api/plugins/usage-kanban/*
State files (accounts.json, settings.json) live next to this file.
Only stdlib + FastAPI (provided by the gateway) are used.
"""

import json
import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib import error as urlerror
from urllib import parse as urlparse
from urllib import request as urlrequest

from fastapi import APIRouter

router = APIRouter()

BASE_DIR = Path(__file__).resolve().parent
ACCOUNTS_FILE = BASE_DIR / "accounts.json"
SETTINGS_FILE = BASE_DIR / "settings.json"

CODEX_AUTH_FILE = Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "auth.json"
CODEX_USAGE_URL = "https://chatgpt.com/backend-api/wham/usage"
CODEX_TOKEN_URL = "https://auth.openai.com/oauth/token"
CODEX_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
OPENCODE_USAGE_URL = "https://opencode.ai/zen/go/v1/usage"
DEEPSEEK_BALANCE_URL = "https://api.deepseek.com/user/balance"

ANTIGRAVITY_AUTH_DIR_ENV = "CLIPROXY_AUTH_DIR"
ANTIGRAVITY_PROXY_URL_ENV = "CLIPROXY_PROXY_URL"
ANTIGRAVITY_CONFIG_FILE_ENV = "CLIPROXY_CONFIG_FILE"
ANTIGRAVITY_DEFAULT_AUTH_DIR = Path.home() / ".cli-proxy-api" / "auth"
ANTIGRAVITY_DEFAULT_CONFIG_FILE = Path.home() / ".cli-proxy-api" / "config.yaml"

ANTIGRAVITY_QUOTA_URL = "https://daily-cloudcode-pa.googleapis.com/v1internal:retrieveUserQuotaSummary"
ANTIGRAVITY_PLAN_URL = "https://cloudcode-pa.googleapis.com/v1internal:loadCodeAssist"
ANTIGRAVITY_UA = (
    "Antigravity/1.0.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

CACHE_TTL = 10.0
_CACHE = {}
_CODEX_FRESH = {}

# opencode.ai sits behind Cloudflare and answers the default python-urllib
# User-Agent with 403 error 1010 — send a browser-like UA instead.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 usage-kanban/1.0"
)

CHIP_MODES = ("pinned", "auto", "worst", "errors")


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _iso_from_unix(ts):
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except Exception:
        return None


def _read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path, data):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def _load_accounts():
    return _read_json(ACCOUNTS_FILE, {"accounts": []})


def _save_accounts(data):
    _write_json(ACCOUNTS_FILE, data)


def _load_settings():
    defaults = {"chipMode": "auto", "pinned": None, "alertYellow": 10.0, "alertRed": 2.0}
    data = _read_json(SETTINGS_FILE, {})
    for key, value in defaults.items():
        data.setdefault(key, value)
    return data


def _save_settings(data):
    _write_json(SETTINGS_FILE, data)


def _mask_key(key):
    if not key:
        return ""
    if len(key) <= 8:
        return key[:2] + "****"
    return key[:4] + "…" + key[-4:]


def _cached(key, fn):
    hit = _CACHE.get(key)
    now = time.time()
    if hit and now - hit[0] < CACHE_TTL:
        return hit[1]
    value = fn()
    _CACHE[key] = (now, value)
    return value


def _http_json(url, headers=None, method="GET", form=None, json_body=None, timeout=15, proxy_url=None):
    req = urlrequest.Request(url, method=method)
    ua = (headers or {}).get("User-Agent") or USER_AGENT
    req.add_header("User-Agent", ua)
    for name, value in (headers or {}).items():
        if name.lower() != "user-agent":
            req.add_header(name, value)
    body = None
    if form is not None:
        body = urlparse.urlencode(form).encode("utf-8")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    elif json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        if not headers or "Content-Type" not in headers:
            req.add_header("Content-Type", "application/json")

    handlers = []
    if proxy_url == "":
        # Explicit `direct` / `none` must also bypass HTTP(S)_PROXY inherited
        # by the gateway process.
        handlers.append(urlrequest.ProxyHandler({}))
    elif proxy_url:
        handlers.append(urlrequest.ProxyHandler({"http": proxy_url, "https": proxy_url}))
    opener = urlrequest.build_opener(*handlers) if handlers else urlrequest.build_opener()
    try:
        with opener.open(req, data=body, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw.decode("utf-8") or "{}")
    except urlerror.HTTPError as exc:
        try:
            payload = json.loads(exc.read().decode("utf-8") or "{}")
        except Exception:
            payload = {}
        return exc.code, payload
    except Exception as exc:
        raise RuntimeError("网络错误: %s" % exc)


# ---------------------------------------------------------------- Codex Plus

def _codex_auth():
    if not CODEX_AUTH_FILE.exists():
        return None, "未检测到 Codex 登录（~/.codex/auth.json）"
    try:
        data = json.loads(CODEX_AUTH_FILE.read_text(encoding="utf-8"))
        tokens = data.get("tokens") or {}
        if not tokens.get("access_token"):
            return None, "auth.json 中没有 access_token，请在 Codex CLI 重新登录"
        return tokens, None
    except Exception as exc:
        return None, "读取 auth.json 失败: %s" % exc


def _codex_refresh(tokens):
    refresh_token = tokens.get("refresh_token")
    if not refresh_token:
        return None
    try:
        status, payload = _http_json(
            CODEX_TOKEN_URL,
            method="POST",
            form={
                "grant_type": "refresh_token",
                "client_id": CODEX_CLIENT_ID,
                "refresh_token": refresh_token,
            },
        )
        if status == 200 and payload.get("access_token"):
            expires_in = int(payload.get("expires_in", 3600))
            _CODEX_FRESH.update(
                access_token=payload["access_token"],
                expires_at=time.time() + expires_in - 60,
            )
            return payload["access_token"]
    except Exception:
        pass
    return None


def _codex_bearer(tokens):
    fresh = _CODEX_FRESH
    if fresh.get("access_token") and time.time() < fresh.get("expires_at", 0):
        return fresh["access_token"]
    return tokens.get("access_token")


def _fetch_codex():
    tokens, err = _codex_auth()
    if err:
        return {"present": False, "status": "error", "error": err}
    headers = {"Authorization": "Bearer " + (_codex_bearer(tokens) or ""), "Accept": "application/json"}
    account_id = tokens.get("account_id")
    if account_id:
        headers["ChatGPT-Account-Id"] = account_id
    try:
        status, payload = _http_json(CODEX_USAGE_URL, headers)
    except Exception as exc:
        return {"present": True, "status": "error", "error": str(exc)}
    if status == 401:
        refreshed = _codex_refresh(tokens)
        if not refreshed:
            return {"present": True, "status": "error", "error": "Codex 会话已过期，请在 Codex CLI 重新登录"}
        headers["Authorization"] = "Bearer " + refreshed
        try:
            status, payload = _http_json(CODEX_USAGE_URL, headers)
        except Exception as exc:
            return {"present": True, "status": "error", "error": str(exc)}
    if status != 200:
        return {"present": True, "status": "error", "error": "接口返回 HTTP %s" % status}
    rate_limit = payload.get("rate_limit") or {}
    # Newer accounts expose their only window in primary_window (604800s = weekly);
    # older ones carry a separate secondary_window for the weekly limit.
    weekly = rate_limit.get("secondary_window") or rate_limit.get("primary_window")
    if not weekly:
        return {"present": True, "status": "error", "error": "接口未返回额度窗口数据"}
    windows = [{
        "key": "weekly",
        "usedPercent": int(weekly.get("used_percent", 0)),
        "resetsAt": _iso_from_unix(weekly.get("reset_at")),
    }]
    return {
        "present": True,
        "status": "ok",
        "planType": payload.get("plan_type"),
        "windows": windows,
    }


# ---------------------------------------------------------------- Antigravity

def _clean_proxy_url(val):
    if not val:
        return None
    val = val.strip().strip("\"'")
    if not val:
        return None
    if val.lower() in ("direct", "none", "null", "no", "off", "empty", "disabled"):
        return ""
    return val


def _proxy_config_error(proxy_url):
    if proxy_url in (None, ""):
        return None
    try:
        scheme = urlparse.urlsplit(proxy_url).scheme.lower()
    except Exception:
        scheme = ""
    if scheme in ("http", "https"):
        return None
    if scheme in ("socks5", "socks5h"):
        return "额度看板暂不支持 SOCKS 代理，请为 CLIPROXY_PROXY_URL 配置 HTTP/HTTPS 代理"
    return "额度看板检测到不支持的代理协议，请检查 CLIProxyAPI proxy-url"


def _get_cliproxy_proxy_url():
    env_proxy = os.environ.get(ANTIGRAVITY_PROXY_URL_ENV)
    if env_proxy is not None and env_proxy.strip():
        return _clean_proxy_url(env_proxy)
    config_path = Path(os.environ.get(ANTIGRAVITY_CONFIG_FILE_ENV) or ANTIGRAVITY_DEFAULT_CONFIG_FILE)
    if config_path.exists():
        try:
            content = config_path.read_text(encoding="utf-8")
            for raw_line in content.splitlines():
                if raw_line.startswith((" ", "\t", "#")):
                    continue
                stripped = raw_line.strip()
                if stripped.startswith("proxy-url:") or stripped.startswith("proxy_url:"):
                    val = stripped.split(":", 1)[1]
                    return _clean_proxy_url(val)
        except Exception:
            pass
    return None


def _discover_antigravity_credentials():
    auth_dir = Path(os.environ.get(ANTIGRAVITY_AUTH_DIR_ENV) or ANTIGRAVITY_DEFAULT_AUTH_DIR)
    if not auth_dir.exists() or not auth_dir.is_dir():
        return []
    creds = []
    for file_path in sorted(auth_dir.glob("antigravity-*.json")):
        if not file_path.is_file():
            continue
        data = _read_json(file_path, None)
        if not isinstance(data, dict):
            continue
        if data.get("disabled") is True:
            continue

        acc_id = data.get("id") or file_path.stem
        email = data.get("email") or data.get("user") or ""
        alias = data.get("alias") or data.get("name") or email or file_path.stem
        project_id = data.get("project_id") or data.get("project") or data.get("projectId") or ""
        token = data.get("access_token") or data.get("token") or ""
        if not token and isinstance(data.get("tokens"), dict):
            token = data.get("tokens", {}).get("access_token") or ""

        creds.append({
            "file_path": file_path,
            "id": acc_id,
            "email": email,
            "alias": alias,
            "project_id": project_id,
            "access_token": token,
            "hidden": bool(data.get("hidden", False)),
        })
    return creds


def _normalize_group_name(group):
    g = (group or "").strip()
    g_lower = g.lower()
    if "gemini" in g_lower:
        return "Gemini"
    if "claude" in g_lower or "gpt" in g_lower or "openai" in g_lower or "3p" in g_lower or "third" in g_lower:
        return "Claude / GPT"
    return g


def _normalize_bucket_label(label, bucket_id=None):
    lbl = (label or "").strip()
    lbl_lower = lbl.lower()
    if "5" in lbl_lower and ("h" in lbl_lower or "hour" in lbl_lower or "小时" in lbl_lower):
        return "5 小时"
    if "week" in lbl_lower or "周" in lbl_lower or "7d" in lbl_lower:
        return "本周"
    if "month" in lbl_lower or "月" in lbl_lower or "30d" in lbl_lower:
        return "本月"
    if "day" in lbl_lower or "天" in lbl_lower or "24h" in lbl_lower:
        return "本日"
    if bucket_id:
        bid_lower = bucket_id.lower()
        if "5h" in bid_lower or "5-hour" in bid_lower or "5hour" in bid_lower:
            return "5 小时"
        if "week" in bid_lower or "7d" in bid_lower:
            return "本周"
    return lbl


def _parse_antigravity_summary(payload):
    if not isinstance(payload, dict):
        return []
    windows = []
    groups = payload.get("groups")
    if groups is None and isinstance(payload.get("response"), dict):
        groups = payload.get("response", {}).get("groups")
    if groups is None:
        groups = payload.get("quotaGroups")
    if groups is None and isinstance(payload.get("response"), dict):
        groups = payload.get("response", {}).get("quotaGroups")
    if not isinstance(groups, list):
        groups = []

    for g in groups:
        if not isinstance(g, dict) or g.get("disabled") is True:
            continue
        raw_group = g.get("displayName") or g.get("name") or g.get("group") or g.get("title") or ""
        group_desc = g.get("description") or g.get("groupDescription")
        g_name = _normalize_group_name(raw_group)

        buckets = g.get("buckets") or g.get("quotaBuckets") or []
        if not isinstance(buckets, list):
            buckets = []

        for b in buckets:
            if not isinstance(b, dict) or b.get("disabled") is True:
                continue
            bucket_key = b.get("bucketId") or b.get("key") or b.get("id") or ""
            raw_label = b.get("displayName") or b.get("label") or b.get("name") or b.get("window") or ""
            label = _normalize_bucket_label(raw_label, bucket_key)
            bucket_desc = b.get("description") or b.get("bucketDescription")

            rem_frac = b.get("remainingFraction")
            if rem_frac is None:
                rem_frac = b.get("remaining_fraction")
            if rem_frac is None and isinstance(b.get("remaining"), dict):
                remaining_obj = b.get("remaining", {})
                rem_frac = remaining_obj.get("remainingFraction")
                if rem_frac is None:
                    rem_frac = remaining_obj.get("remaining_fraction")
                if rem_frac is None and remaining_obj.get("case") in ("remainingFraction", "remaining_fraction"):
                    rem_frac = remaining_obj.get("value")
            if rem_frac is None and isinstance(b.get("remaining"), (int, float)):
                rem_frac = b.get("remaining")

            used_pct = None
            if rem_frac is not None:
                try:
                    frac_val = float(rem_frac)
                    val = (1.0 - frac_val) * 100.0
                    val = max(0.0, min(100.0, val))
                    used_pct = round(val, 2)
                except (ValueError, TypeError):
                    used_pct = None

            resets_at = b.get("resetTime") or b.get("resetsAt") or b.get("reset_time") or b.get("reset_at")
            if isinstance(resets_at, (int, float)):
                resets_at = _iso_from_unix(resets_at)

            win_key = bucket_key if bucket_key else f"{g_name}_{label}".strip("_")
            win = {
                "key": win_key,
                "group": g_name,
                "label": label,
                "usedPercent": used_pct,
                "resetsAt": resets_at,
            }
            if group_desc:
                win["groupDescription"] = group_desc
            if bucket_desc:
                win["description"] = bucket_desc

            windows.append(win)
    return windows


def _fetch_antigravity_plan(token, proxy_url):
    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "User-Agent": ANTIGRAVITY_UA,
        "Client-Metadata": '{"ideType":"ANTIGRAVITY"}',
        "X-Goog-Api-Client": "antigravity",
    }
    try:
        status, payload = _http_json(
            ANTIGRAVITY_PLAN_URL,
            headers=headers,
            method="POST",
            json_body={"metadata": {"ideType": "ANTIGRAVITY"}},
            proxy_url=proxy_url,
            timeout=10,
        )
        if status == 200 and isinstance(payload, dict):
            paid_tier = payload.get("paidTier")
            if isinstance(paid_tier, dict) and paid_tier.get("name"):
                return paid_tier.get("name")
            current_tier = payload.get("currentTier")
            if isinstance(current_tier, dict) and current_tier.get("name"):
                return current_tier.get("name")
            plan = payload.get("plan")
            if isinstance(plan, dict) and plan.get("name"):
                return plan.get("name")
            if isinstance(plan, str) and plan:
                return plan
            if payload.get("tier"):
                return str(payload.get("tier"))
    except Exception:
        pass
    return None


def _fetch_antigravity(cred):
    file_path = cred.get("file_path")
    token = cred.get("access_token")
    project_id = cred.get("project_id")
    proxy_url = _get_cliproxy_proxy_url()

    proxy_error = _proxy_config_error(proxy_url)
    if proxy_error:
        return {"status": "error", "error": proxy_error}

    if not token:
        return {"status": "error", "error": "未检测到有效 access token"}
    if not project_id:
        return {"status": "error", "error": "未检测到 project_id"}

    headers = {
        "Authorization": "Bearer " + token,
        "Content-Type": "application/json",
        "User-Agent": ANTIGRAVITY_UA,
        "Client-Metadata": '{"ideType":"ANTIGRAVITY"}',
        "X-Goog-Api-Client": "antigravity",
    }

    try:
        status, payload = _http_json(
            ANTIGRAVITY_QUOTA_URL,
            headers=headers,
            method="POST",
            json_body={"project": project_id},
            proxy_url=proxy_url,
        )
    except Exception:
        return {"status": "error", "error": "网络请求失败，请检查网络或代理配置"}

    if status == 401:
        # Re-read file and retry once
        if file_path and isinstance(file_path, Path) and file_path.exists():
            re_data = _read_json(file_path, {})
            new_token = (
                re_data.get("access_token")
                or re_data.get("token")
                or (re_data.get("tokens", {}).get("access_token") if isinstance(re_data.get("tokens"), dict) else "")
            )
            if new_token:
                token = new_token
                headers["Authorization"] = "Bearer " + token
                try:
                    status, payload = _http_json(
                        ANTIGRAVITY_QUOTA_URL,
                        headers=headers,
                        method="POST",
                        json_body={"project": project_id},
                        proxy_url=proxy_url,
                    )
                except Exception:
                    return {"status": "error", "error": "网络请求失败，请检查网络或代理配置"}

    if status == 401:
        return {"status": "error", "error": "凭证已过期或无效（401），等待 CLIProxyAPI 自动续期"}
    if status == 403:
        return {"status": "error", "error": "无权限访问配额接口（403）"}
    if status == 429:
        return {"status": "error", "error": "配额接口请求过于频繁（429）"}
    if status != 200:
        return {"status": "error", "error": "接口返回 HTTP %s" % status}

    windows = _parse_antigravity_summary(payload)
    if not windows:
        return {"status": "error", "error": "额度接口未返回可识别的窗口数据"}
    plan_name = _fetch_antigravity_plan(token, proxy_url)
    return {
        "status": "ok",
        "plan": plan_name,
        "windows": windows,
    }


# ---------------------------------------------------------------- opencode-go

def _fetch_opencode(key):
    try:
        status, payload = _http_json(
            OPENCODE_USAGE_URL,
            {"Authorization": "Bearer " + key, "Accept": "application/json"},
        )
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    if status == 401:
        return {"status": "error", "error": "API key 无效（401）"}
    if status == 403:
        return {"status": "error", "error": "该 key 没有 Go 订阅（403）"}
    if status != 200:
        return {"status": "error", "error": "接口返回 HTTP %s" % status}
    usage = payload.get("usage") or {}
    windows = []
    for key_name in ("rolling", "weekly", "monthly"):
        win = usage.get(key_name)
        if win:
            windows.append({
                "key": key_name,
                "usedPercent": int(win.get("percent", 0)),
                "windowStatus": win.get("status"),
                "resetsAt": win.get("resetsAt"),
            })
    return {"status": "ok", "windows": windows}


# ---------------------------------------------------------------- DeepSeek

def _fetch_deepseek(key):
    try:
        status, payload = _http_json(
            DEEPSEEK_BALANCE_URL,
            {"Authorization": "Bearer " + key, "Accept": "application/json"},
        )
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    if status == 401:
        return {"status": "error", "error": "API key 无效（401）"}
    if status != 200:
        return {"status": "error", "error": "接口返回 HTTP %s" % status}
    balances = []
    for entry in payload.get("balance_infos") or []:
        balances.append({
            "currency": entry.get("currency"),
            "total": entry.get("total_balance"),
            "granted": entry.get("granted_balance"),
            "toppedUp": entry.get("topped_up_balance"),
        })
    return {
        "status": "ok",
        "isAvailable": bool(payload.get("is_available")),
        "balances": balances,
    }


# ---------------------------------------------------------------- routes

@router.get("/status")
def get_status():
    accounts = _load_accounts()["accounts"]
    opencode_accounts = [a for a in accounts if a.get("provider") == "opencode"]
    deepseek_accounts = [a for a in accounts if a.get("provider") == "deepseek"]
    antigravity_accounts = _discover_antigravity_credentials()

    def run_codex():
        return _cached("codex", _fetch_codex)

    def run_antigravity(acc):
        return _cached(("antigravity", acc["id"]), lambda: _fetch_antigravity(acc))

    def run_opencode(acc):
        return _cached(("opencode", acc["id"]), lambda: _fetch_opencode(acc["key"]))

    def run_deepseek(acc):
        return _cached(("deepseek", acc["id"]), lambda: _fetch_deepseek(acc["key"]))

    with ThreadPoolExecutor(max_workers=8) as pool:
        codex_future = pool.submit(run_codex)
        antigravity_futures = [pool.submit(run_antigravity, acc) for acc in antigravity_accounts]
        opencode_futures = [pool.submit(run_opencode, acc) for acc in opencode_accounts]
        deepseek_futures = [pool.submit(run_deepseek, acc) for acc in deepseek_accounts]
        codex_result = codex_future.result()
        antigravity_results = [future.result() for future in antigravity_futures]
        opencode_results = [future.result() for future in opencode_futures]
        deepseek_results = [future.result() for future in deepseek_futures]

    antigravity_out = []
    for acc, result in zip(antigravity_accounts, antigravity_results):
        antigravity_out.append({
            "id": acc["id"],
            "alias": acc.get("alias", ""),
            "email": acc.get("email", ""),
            "hidden": bool(acc.get("hidden", False)),
            **result,
        })
    opencode_out = []
    for acc, result in zip(opencode_accounts, opencode_results):
        opencode_out.append({
            "id": acc["id"],
            "alias": acc.get("alias", ""),
            "hidden": bool(acc.get("hidden", False)),
            **result,
        })
    deepseek_out = []
    for acc, result in zip(deepseek_accounts, deepseek_results):
        deepseek_out.append({
            "id": acc["id"],
            "alias": acc.get("alias", ""),
            "hidden": bool(acc.get("hidden", False)),
            **result,
        })
    return {
        "generatedAt": _now_iso(),
        "codex": codex_result,
        "antigravity": {"accounts": antigravity_out},
        "opencode": {"accounts": opencode_out},
        "deepseek": {"accounts": deepseek_out},
    }


@router.get("/settings")
def get_settings():
    return _load_settings()


@router.put("/settings")
def put_settings(body: dict):
    settings = _load_settings()
    if "chipMode" in body and body["chipMode"] in CHIP_MODES:
        settings["chipMode"] = body["chipMode"]
    if "pinned" in body:
        settings["pinned"] = body["pinned"] if isinstance(body["pinned"], dict) else None
    for name in ("alertYellow", "alertRed"):
        if name in body:
            try:
                value = float(body[name])
                if value >= 0:
                    settings[name] = value
            except (TypeError, ValueError):
                pass
    _save_settings(settings)
    return {"ok": True, "settings": settings}


@router.get("/accounts")
def get_accounts():
    data = _load_accounts()
    out = []
    for acc in data["accounts"]:
        out.append({
            "id": acc["id"],
            "provider": acc["provider"],
            "alias": acc.get("alias", ""),
            "hidden": bool(acc.get("hidden", False)),
            "keyMasked": _mask_key(acc.get("key", "")),
        })
    return {"accounts": out}


@router.post("/accounts")
def post_account(body: dict):
    provider = body.get("provider")
    alias = (body.get("alias") or "").strip()
    key = (body.get("key") or "").strip()
    if provider not in ("opencode", "deepseek"):
        return {"ok": False, "error": "provider 无效"}
    if not alias:
        return {"ok": False, "error": "别名不能为空"}
    if not key:
        return {"ok": False, "error": "API key 不能为空"}
    data = _load_accounts()
    acc = {
        "id": uuid.uuid4().hex[:12],
        "provider": provider,
        "alias": alias,
        "key": key,
        "hidden": False,
        "createdAt": _now_iso(),
    }
    data["accounts"].append(acc)
    _save_accounts(data)
    _CACHE.pop(("opencode", acc["id"]), None)
    _CACHE.pop(("deepseek", acc["id"]), None)
    return {"ok": True, "account": {"id": acc["id"], "provider": acc["provider"], "alias": acc["alias"], "hidden": acc["hidden"], "keyMasked": _mask_key(key)}}


@router.patch("/accounts/{account_id}")
def patch_account(account_id: str, body: dict):
    data = _load_accounts()
    for acc in data["accounts"]:
        if acc["id"] == account_id:
            if isinstance(body.get("alias"), str) and body["alias"].strip():
                acc["alias"] = body["alias"].strip()
            if "hidden" in body:
                acc["hidden"] = bool(body["hidden"])
            if isinstance(body.get("key"), str) and body["key"].strip():
                acc["key"] = body["key"].strip()
            _save_accounts(data)
            _CACHE.pop((acc["provider"], acc["id"]), None)
            return {"ok": True}
    return {"ok": False, "error": "账号不存在"}


@router.delete("/accounts/{account_id}")
def delete_account(account_id: str):
    data = _load_accounts()
    remaining = []
    removed = None
    for acc in data["accounts"]:
        if acc["id"] == account_id:
            removed = acc
        else:
            remaining.append(acc)
    if removed is None:
        return {"ok": False, "error": "账号不存在"}
    data["accounts"] = remaining
    _save_accounts(data)
    _CACHE.pop((removed["provider"], removed["id"]), None)
    return {"ok": True}
