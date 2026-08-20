# Usage Kanban for Hermes

A Hermes desktop plugin (Nous Research Hermes Agent) that shows the current LLM quota across providers at a glance.

## Language

**Provider**:
A configurable quota source behind one common interface. v1 ships Codex Plus, Antigravity, opencode-go, and DeepSeek; more can be added without touching the plugin core.
_Avoid_: source, vendor, platform

**Account**:
One credential under a provider — an API key for opencode-go and DeepSeek, the locally logged-in Codex session for Codex Plus, or a CLIProxyAPI OAuth credential for Antigravity. A provider can have many accounts, shown side by side.
_Avoid_: key, user

**Subscription**:
The provider-side plan an account holds — for example an opencode-go Go subscription with its rolling/weekly/monthly windows. The status-bar chip can be pinned to one account's subscription quota.
_Avoid_: plan, package

**Quota**:
The remaining allowance a provider reports. The unit varies by provider: a weekly window for Codex Plus, shared 5-hour / weekly pools for Antigravity, rolling 5-hour / weekly / monthly windows for opencode-go, money for DeepSeek (CNY balance).
_Avoid_: limit, usage

**Snapshot**:
The quota values fetched on demand — when the plugin opens, or on manual refresh. No history is kept.

**Window**:
A time-boxed usage limit a provider enforces — Codex Plus has a weekly window; Antigravity has separate Gemini and Claude/GPT pools with 5-hour and weekly windows; opencode-go has rolling 5-hour, weekly, and monthly windows. Each percentage window reports a used percentage and a reset time.
_Avoid_: tier, bucket

**Alert line**:
Configurable yellow/red money thresholds for DeepSeek balance highlighting. Percent-based windows instead use fixed remaining-ratio thresholds.
