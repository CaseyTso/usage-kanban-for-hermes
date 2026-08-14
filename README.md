# Usage Kanban (usage-kanban)

> **English** · [**中文（简体）**](README-zh.md)

A Hermes desktop plugin that shows a snapshot of your LLM API quotas in a right-side pane. Also adds a status-bar chip so you can see the tightest quota at a glance.

## Screenshot

![usage-kanban screenshot](docs/screenshot.jpg)

## Supported sources

- **Codex Plus** — weekly quota (auto-detects the local `~/.codex/auth.json`; no key needed)
- **opencode-go** — three windows per API key: rolling 5h / weekly / monthly
- **DeepSeek** — balance (total + expandable granted/topped-up breakdown)

## Installation

### 1. Desktop plugin (hot-reloads on save; symlinks are officially supported)

```bash
ln -s "$PWD" ~/.hermes/desktop-plugins/usage-kanban
```

Or copy this directory to `~/.hermes/desktop-plugins/usage-kanban`. Note: the directory name must equal the plugin id (`usage-kanban`).

### 2. Python backend (takes effect after a gateway restart)

```bash
mkdir -p ~/.hermes/plugins/usage-kanban/dashboard
cp dashboard/manifest.json dashboard/plugin_api.py ~/.hermes/plugins/usage-kanban/dashboard/
```

Then add `usage-kanban` to the `plugins.enabled` list in `~/.hermes/config.yaml`, and **restart the Hermes gateway**. Until then the board shows a "backend unavailable" message, which is expected.

### 3. Add accounts

Open the settings page (sidebar "额度看板设置", or the "设置" button in the pane's top-right):

- **opencode-go**: alias + API key (stored in plain text in the backend `accounts.json`; only the trailing few characters are shown in the UI)
- **DeepSeek**: same as above
- **Codex Plus**: nothing to configure — it appears automatically

> For a full walkthrough in Chinese, see [README-zh.md](README-zh.md).

## Settings

- **Status-bar chip display mode**: pinned subscription (you pick which one) / auto (errors first, else the tightest window) / always tightest / always error count
- **DeepSeek money alert**: turns yellow/red when balance is below configurable lines (default ¥10 / ¥2)
- **Percent-window highlighting**: remaining < 20% yellow, < 5% red (fixed)
- **Hide account** toggle (hidden accounts are excluded from the board and the chip)

## Data-source notes

- Codex Plus `wham/usage` and opencode-go `zen/go/v1/usage` are **undocumented endpoints** used by the official clients; they may change at any time. When that happens a card shows the error reason.
- DeepSeek `balance` is an official, public endpoint.
- All requests are proxied through the Python backend (see `docs/adr/0001-provider-traffic-via-python-backend.md`).

## Project layout

```text
plugin.js                  Desktop plugin (single self-contained file)
dashboard/manifest.json    Backend manifest
dashboard/plugin_api.py    Backend: quota aggregation + account/settings storage + Codex token auto-refresh
CONTEXT.md                 Domain glossary
docs/adr/                  Architecture decision records
research/                  Research notes (quota APIs, SDK facts, vision-acceptance notes)
FACTS.md                   Hermes plugin SDK fact-finding report
```

## Troubleshooting

- **Board shows "backend unavailable"** — the plugin is not in `plugins.enabled`, or the gateway wasn't restarted (see step 2).
- **Codex card says "session expired"** — re-login in the Codex CLI (`codex login`).
- **opencode-go reports 403** — that key has no Go subscription.
- **Want another provider** — add a fetch function in the backend + one aggregation branch in `/status`, then add one card in the front end (the provider/account abstraction is ready for this).

## License

Copyright (C) 2026 CaseyTso

Released under the GNU Affero General Public License v3.0 (AGPL-3.0). See [LICENSE](LICENSE).
