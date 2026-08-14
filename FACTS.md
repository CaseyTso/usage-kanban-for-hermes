# Hermes Desktop Plugin SDK — Fact-Finding Report (`usage-kanban`)

Verified against: `apps/desktop/src/sdk/index.ts`, `apps/desktop/src/contrib/plugin.ts`, `apps/desktop/src/contrib/plugins.ts`, `apps/desktop/src/contrib/runtime-loader.ts`, `apps/desktop/src/hermes.ts`, `apps/desktop/electron/preload.ts` + `fs-read-dir.ts`, `apps/desktop/src/plugins/kanban/*`, `plugins/kanban/dashboard/plugin_api.py`, official docs `website/docs/developer-guide/desktop-plugin-sdk.md`, the `hermes-desktop-plugins` skill (`references/desktop-plugins.md` + `templates/plugin.js`), and the `hermes-example-plugins` repo. All raw reads via raw.githubusercontent.com / docs site.

---

## 1. Disk plugin layout

**Fact:** runtime-loadable ("disk") plugin path (docs: "Write access to $HERMES_HOME/desktop-plugins/", default `~/.hermes`, named profile uses `~/.hermes/profiles/<profile>/desktop-plugins/`):
- `~/.hermes/desktop-plugins/<id>/plugin.js`  (default)
- `~/.hermes/profiles/<profile>/desktop-plugins/<id>/plugin.js`  (named profile)
- The **folder name must exactly equal the `id`** of the default export.

The loader (`scanDiskPlugins` in runtime-loader.ts) scans every dir under `desktop-plugins/`, keeps those containing a `plugin.js`, and blob-imports that ONE file. The dir + each plugin.js are fs-watched: save → hot-reload in place; new folder → auto-loads; removed folder → cleanly unloads. Manual fallback: ⌘K → **Reload desktop plugins**.

**Extra files in the plugin folder are NOT runtime-importable.** The loader rewrites only `@hermes/plugin-sdk`, `react`, `react/jsx-runtime`, `react/jsx-dev-runtime` and evaluates `plugin.js` as a single blob. Any other specifier (`./helper.js` in the same folder included) fails up-front with an "unsupported import" error (`unsupportedImports()`). A disk plugin must be **self-contained in one file** — inline your helper logic. (The in-repo `kanban` plugin is *bundled*/built and thus can multi-file; a disk plugin cannot.)

**Symlinked plugin folder:** supported/reliable. `readDir` (`electron/fs-read-dir.ts`) uses `withFileTypes:true`; a symlink dir reports `isSymbolicLink()` ≠ `isDirectory()`, but `entryForDirent` stat-follows it and returns `isDirectory:true`, so the scanner accepts it. Per-file watch follows the same resolved path. Caveat: the root comes from `desktop.desktopPluginsRoot()` (IPC `hermes:fs:desktopPluginsRoot`), resolved **Electron-locally**, so a remote backend (OAuth remote) has no usable disk-plugins dir (#66899) — plugins only load against a local Electron root.

Sources:
- https://raw.githubusercontent.com/NousResearch/hermes-agent/main/apps/desktop/src/contrib/runtime-loader.ts
- https://raw.githubusercontent.com/NousResearch/hermes-agent/main/apps/desktop/src/contrib/plugins.ts
- https://raw.githubusercontent.com/NousResearch/hermes-agent/main/apps/desktop/electron/fs-read-dir.ts
- https://raw.githubusercontent.com/NousResearch/hermes-agent/main/website/docs/developer-guide/desktop-plugin-sdk.md

---

## 2. Python backend for a DESKTOP plugin

**Critical fact: the desktop plugin's Python backend is NOT inside desktop-plugins/.** Desktop plugins reuse the **dashboard plugin backend mount** under the general Python plugin dir:
```
~/.hermes/plugins/<id>/dashboard/
    ├── manifest.json     # { "name": "<id>", "api": "plugin_api.py" }
    └── plugin_api.py     # exports  router = APIRouter()
```
- No `plugin_api.py` sits beside the browser `plugin.js`; UI lives in `desktop-plugins/<id>/`, backend lives in `plugins/<id>/dashboard/`. The Kanban plugin is the canonical example: UI derived in `apps/desktop/src/plugins/kanban/`, backend reused from `plugins/kanban/dashboard/plugin_api.py`.
- Required export: `router = APIRouter()` (FastAPI). Routes mount under `/api/plugins/<id>/` (e.g. `GET /api/plugins/kanban/board`).
- **ctx.rest mapping:** `ctx.rest('/board')` → `GET /api/plugins/<id>/board`. Implemented in `hermes.ts` `pluginRest` (builds `/api/plugins/${pluginId}${suffix}` and calls the desktop bridge `api` with `profileScoped()`). `PluginRestOptions = { method?, body?, upload?, timeoutMs? }`. Traversal is rejected, so a plugin can't hit another plugin's API or core routes.
- **Filesystem + egress:** the backend runs **inside the gateway process** (":Backend code runs inside the gateway process, so it can import from the hermes-agent codebase, e.g. `hermes_state`, `hermes_cli.config`:"). It therefore has **unrestricted HTTPS egress** and **OS filesystem access** — reading `~/.codex/auth.json` works if the gateway user can read it. There is no sandbox on outbound network or file reads for the backend. (The renderer plugin.js is a browser context and cannot `fs`-read files directly — route it through `ctx.rest`/`host.request`.)
- **Gating:** toggling the plugin in desktop Settings does NOT import Python. `plugin_api.py` loads only when the id is in `plugins.enabled` (and not `plugins.disabled`) in `config.yaml`; project plugins never auto-import. Routes mount at gateway startup → restart the gateway; errors go to `~/.hermes/logs/errors.log`.
- **Auth:** every `/api/plugins/...` request needs the dashboard session bearer token/cookie; `ctx.socket` uses `?token=`. Desktop REST sends it automatically via `profileScoped()`.

Backend (docs):
```python
# plugin_api.py
from fastapi import APIRouter
router = APIRouter()

@router.get("/board")
async def board():
    return {"items": ["one", "two", "three"]}

@router.post("/action")
async def action(body: dict):
    return {"ok": True, "received": body}
```

Renderer:
```js
const load = () => ctx.rest('/board')                                        // GET /api/plugins/<id>/board
const act  = () => ctx.rest('/action', { method: 'POST', body: { go: true } })
const stop = ctx.socket('/events', frame => queryClient.invalidateQueries({ queryKey: [ctx.source, 'board'] }))
```

Example manifest (from hermes-example-plugins/example-dashboard): `{ "name":"example", "label":"Example", "api":"plugin_api.py", "entry":"dist/index.js", ... }`

Sources:
- https://raw.githubusercontent.com/NousResearch/hermes-agent/main/apps/desktop/src/hermes.ts (pluginRest ~line 297)
- https://raw.githubusercontent.com/NousResearch/hermes-agent/main/website/docs/developer-guide/desktop-plugin-sdk.md#a-backend-for-your-plugin
- https://raw.githubusercontent.com/NousResearch/hermes-agent/main/plugins/kanban/dashboard/plugin_api.py

---

## 3. Kanban desktop plugin (template for usage-kanban)

**Files (merged PR #61173, `apps/desktop/src/plugins/kanban/`):** `plugin.tsx`, `api.ts`, `board.tsx`, `board-switcher.tsx`, `drawer.tsx`, `i18n.ts`, `kanban.css`, `model-override.tsx`, `orchestration.tsx`, `types.ts`, `ui.tsx`. It is a **bundled** plugin (real JSX, Vite-built). It ships **no own plugin_api.py** — it reuses `plugins/kanban/dashboard/plugin_api.py` via `ctx.rest`.

Minimal disk-plugin pane + statusBar chip (from the docs' quick start — closest minimal single-file example):
```js
import { host, haptic, useValue } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'

function HelloPane() {
  const gateway = useValue(host.state.gateway)
  return jsxs('div', { className: 'flex h-full flex-col gap-2 p-3 text-sm',
    children: [
      jsx('div', { className: 'font-medium', children: 'Usage Kanban' }),
      jsx('div', { className: 'text-(--ui-text-tertiary)', children: `gateway: ${gateway}` })]})
}
export default {
  id: 'usage-kanban',
  name: 'Usage Kanban',
  register(ctx) {
    ctx.register({ id: 'pane', area: 'panes', title: 'usage', data: { placement: 'right', width: '300px' }, render: () => jsx(HelloPane, {}) })
    ctx.register({ id: 'chip', area: 'statusBar.right', order: 130,
      render: () => jsx('button', { type: 'button', className: 'px-1.5 text-[0.6875rem] text-(--ui-text-tertiary)',
        onClick: () => { haptic('tap'); host.notify({ kind: 'info', message: 'Hello' }) }, children: 'usage' }) })
  }
}
```

Kanban plugin.tsx multi-area contribution (registerMany):
```tsx
ctx.registerMany([
  { id: 'page',  area: ROUTES_AREA,        data: { path: '/kanban' }, render: () => <KanbanBoardPage/> },
  { id: 'nav',   area: SIDEBAR_NAV_AREA,   order: 50, data: { codicon: 'project', label: 'Kanban', path: '/kanban' } },
  { id: 'count', area: STATUSBAR_AREAS.right, order: 80, render: () => <KanbanCount/> },
  { id: 'open',  area: PALETTE_AREA, data: { id: 'kanban.open', label: 'Kanban: Open board', run: () => host.navigate('/kanban') } },
  { id: 'new-task', area: KEYBINDS_AREA, data: { id: 'kanban.newTask', category: 'view', defaults: ['mod+alt+n'], run: newTask } },
])
```
Pane placement: `area:'panes'` + `data:{ placement:'left'|'right'|'bottom'|'main', width, height }` stacks as tabs. To land on an edge: `data:{ placement:'bottom', dock:{ pane:'workspace', pos:'bottom' }, height:'200px' }`.

Live status chip with poll (kanban api.ts/plugin.tsx): `useQuery({ queryFn: () => fetchBoard(false), queryKey: boardKey(slug,false), refetchInterval: 60_000 })`.

Sources:
- https://raw.githubusercontent.com/NousResearch/hermes-agent/main/apps/desktop/src/plugins/kanban/plugin.tsx
- https://raw.githubusercontent.com/NousResearch/hermes-agent/main/apps/desktop/src/plugins/kanban/api.ts
- https://raw.githubusercontent.com/NousResearch/hermes-agent/main/skills/autonomous-ai-agents/hermes-agent/templates/plugin.js

---

## 4. SDK surface (verified from apps/desktop/src/sdk/index.ts)

**Contracts:** `HermesPlugin`, `PluginContext`, `PluginContribution`, `PluginStorage`, `PluginOs`, `PluginRestOptions`, `PluginNativeNotificationInput`, `Contribution`.

**Host:** `host.state.*` (readonly atoms: activeSessionId, cwd, gateway, model, profile, viewport); `host.notify`, `host.notifyError`, `host.navigate`, `host.openSession`, `host.newChat`, `host.onEvent`, `host.logs`, `host.restartGateway`, `host.status`, `host.request(method, params)` (gateway JSON-RPC).

**React/state:** `useValue` (re-exported from `@nanostores/react`); `atom`, `computed` (re-exported from `nanostores`); `useQuery`, `useMutation`, `useQueryClient`, `queryClient` (all re-exported from `@tanstack/react-query` — disk plugins import them from `@hermes/plugin-sdk`, sharing the app's ONE QueryClient). `Contribute` for mount-scoped UI.

**jsx/jsxs:** disk plugins use `import { jsx, jsxs } from 'react/jsx-runtime'` (no JSX syntax — file is not compiled). `react`, `react/jsx-runtime`, `react/jsx-dev-runtime` are shimmed to the app's React singleton by the loader.

**UI components REALLY exported** (verbatim from sdk/index.ts): `StatusDot`, `Badge`, `Button`, `Checkbox`, `Codicon`, `ConfirmDialog`, `ContextMenu/Content/Item/Separator/Trigger`, `CopyButton`, `DecodeText`, `Dialog/Content/Description/Footer/Header/Title/Trigger`, `DropdownMenu/Content/Item/Separator/Trigger`, `EmptyState`, `ErrorState`, `FadeScroll`, `GlyphSpinner`, `Input`, `Kbd/KbdGroup`, `Loader`, `LogView`, `Popover/Content/Trigger`, `ScrollArea`, `SearchField`, `SegmentedControl`, `Select/Content/Item/Trigger/Value`, `Separator`, `Skeleton`, `Switch`, `Tabs/List/Trigger`, `Textarea`, `Tip/Tooltip/Content/Provider/Trigger`, `ModelCatalogMenu`. **There is NO `Text` and NO `ProgressBar` export** (important for a quota UI — build bars with styled divs using theme vars `var(--ui-*)`, or reuse `StatusDot`/`Loader`).

**Helpers:** `cn`, `icons.*` (lucide set), `haptic`, `useI18n`, `usePluginI18n`, `profileColor`, `profileColorSoft`, `relativeTime`, `fmtDateTime`, `fmtDayTime`, `coarseElapsed`, `compactNumber`, `evaluateRuntimeReadiness`.

**ctx.storage:** `{ get<T>(key, fallback):T; set(key, value):void; remove(key):void }` — JSON-persisted, namespaced under `hermes.plugin.<id>.*` (plugins can't clobber each other). Handlers should read via `.get()` not render closures.

**host.notify:** `host.notify({ kind: 'info'|'warn'|'error'|..., message: string })` — in-app toast. `host.notifyError(error, msg)` for errors.

**Area constants:** `PANES_AREA='panes'`, `ROUTES_AREA`, `SIDEBAR_NAV_AREA`, `STATUSBAR_AREAS={ left:'statusBar.left', right:'statusBar.right' }`, `TITLEBAR_AREAS`, `PALETTE_AREA`, `KEYBINDS_AREA`, `THEMES_AREA`. Statusbar/statusbar-contribution payload = the `Contribution` shape (`{ id, area, order?, title?, when?, enabled?, render?, data? }`); chips render via `render()`.

Sources:
- https://raw.githubusercontent.com/NousResearch/hermes-agent/main/apps/desktop/src/sdk/index.ts
- https://raw.githubusercontent.com/NousResearch/hermes-agent/main/apps/desktop/src/sdk/runtime.ts
- https://raw.githubusercontent.com/NousResearch/hermes-agent/main/apps/desktop/src/contrib/plugin.ts

---

## 5. Plugin settings UI

**Fact: there is NO dedicated per-plugin settings-panel contribution** in the shipped SDK surface (`sdk/index.ts` has no settings/`SETTINGS_AREA` export). The only automatic settings surface is **Settings → Plugins**, which lists every plugin with a live enable/disable toggle, "reveal folder", and rescan — it does not render a custom form. `HermesPlugin.name`/`description` only feed the About/inventory row.

**Closest workaround for real plugin settings:** register a full settings page via a route + sidebar nav entry, then persist options in `ctx.storage` (and/or `ctx.rest` backend + config.yaml):
```js
ctx.register({ id: 'settings', area: ROUTES_AREA, data: { path: '/usage-kanban-settings' }, render: () => jsx(SettingsPage, {}) })
ctx.register({ id: 'settings-nav', area: SIDEBAR_NAV_AREA, data: { path: '/usage-kanban-settings', label: 'Usage Kanban', codicon: 'gear' } })
// then host.navigate('/usage-kanban-settings') from a chip/command
```
A config-state bridge is only an RFC (`docs/rfcs/plugin-config-state-bridge.md`), not a shipped SDK area.

---

## 6. ctx.os.notify / ctx.os.openExternal signatures

From `PluginOs` (contrib/plugin.ts):
```ts
interface PluginOs {
  notify: (input: PluginNativeNotificationInput) => void;   // { title: string; body?: string; silent?: boolean }
  openExternal: (url: string) => Promise<boolean>;         // OS default handler; false if shell can't
  revealPath: (path: string) => Promise<boolean>;
  writeClipboard: (text: string) => Promise<boolean>;
}
```
- `ctx.os.notify({ title, body?, silent? })`: native OS notification attributed to the plugin id; gated by Settings ▸ Notifications ▸ "Plugin notifications"; fires **only while the user is away** from Hermes (use `host.notify` for the in-app toast); throttled per plugin. Never throws — degrades to silent/false when unavailable.
- `ctx.os.openExternal(url)`: `Promise<boolean>`, resolves false (never throws) when no Electron shell / unsupported.

Source: https://raw.githubusercontent.com/NousResearch/hermes-agent/main/apps/desktop/src/contrib/plugin.ts (PluginOs), and PluginNativeNotificationInput at apps/desktop/src/store/native-notifications.ts.

---

## Deliverables for `usage-kanban`
- **UI:** single disk file `~/.hermes/desktop-plugins/usage-kanban/plugin.js` (or the active profile dir), importing ONLY `@hermes/plugin-sdk`, `react`, `react/jsx-runtime`; use `jsx()/jsxs()`, never JSX syntax. Self-contained — inline helpers.
- **Pane:** `ctx.register({ id:'pane', area:'panes', title, data:{ placement:'right', width:'320px' }, render })`.
- **Statusbar chip:** `ctx.register({ id:'usage', area:'statusBar.right', order, render })`.
- **Quota fetch:** `useQuery` (imported from `@hermes/plugin-sdk`) with `refetchInterval` shares the app's QueryClient; call `host.request`/`ctx.rest` in `queryFn`. No ProgressBar export — hand-roll bars with theme-variable styles.
- **Backend (optional):** `~/.hermes/plugins/usage-kanban/dashboard/{manifest.json, plugin_api.py}` with `router = APIRouter()`; add `usage-kanban` to `plugins.enabled` in config.yaml; restart gateway; reachable at `/api/plugins/usage-kanban/*`. Backend has full Python filesystem access + HTTPS egress (can read `~/.codex/auth.json` if permitted).
