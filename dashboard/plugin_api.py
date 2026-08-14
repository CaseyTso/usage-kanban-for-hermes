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


def _http_json(url, headers=None, method="GET", form=None, timeout=15):
    req = urlrequest.Request(url, method=method)
    req.add_header("User-Agent", USER_AGENT)
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    body = None
    if form is not None:
        body = urlparse.urlencode(form).encode("utf-8")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urlrequest.urlopen(req, data=body, timeout=timeout) as resp:
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

    def run_codex():
        return _cached("codex", _fetch_codex)

    def run_opencode(acc):
        return _cached(("opencode", acc["id"]), lambda: _fetch_opencode(acc["key"]))

    def run_deepseek(acc):
        return _cached(("deepseek", acc["id"]), lambda: _fetch_deepseek(acc["key"]))

    with ThreadPoolExecutor(max_workers=4) as pool:
        codex_future = pool.submit(run_codex)
        opencode_futures = [pool.submit(run_opencode, acc) for acc in opencode_accounts]
        deepseek_futures = [pool.submit(run_deepseek, acc) for acc in deepseek_accounts]
        codex_result = codex_future.result()
        opencode_results = [future.result() for future in opencode_futures]
        deepseek_results = [future.result() for future in deepseek_futures]

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
