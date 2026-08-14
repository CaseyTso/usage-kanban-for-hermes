// usage-kanban — Hermes desktop plugin (single-file disk plugin)
// Quota kanban for Codex Plus / opencode-go / DeepSeek.
// All provider HTTP goes through the Python backend (docs/adr/0001).

import {
  host, haptic, useQuery, useMutation, queryClient,
  ROUTES_AREA, SIDEBAR_NAV_AREA, STATUSBAR_AREAS,
} from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useState, useEffect } from 'react'

let ctx = null

const Q_STATUS = ['usage-kanban', 'status']
const Q_SETTINGS = ['usage-kanban', 'settings']
const Q_ACCOUNTS = ['usage-kanban', 'accounts']

const C = {
  fg: 'var(--ui-text-primary, #e5e7eb)',
  fg2: 'var(--ui-text-secondary, #9ca3af)',
  fg3: 'var(--ui-text-tertiary, #6b7280)',
  border: 'var(--ui-border, #374151)',
  bg: 'var(--ui-bg-elevated, rgba(255,255,255,0.04))',
  danger: 'var(--ui-danger, #ef4444)',
  warn: 'var(--ui-warning, #f59e0b)',
  ok: 'var(--ui-success, #22c55e)',
  accent: 'var(--ui-accent, #3b82f6)',
}

const el = function (type, props) { return jsx(type, props) }
const els = function (type, props) { return jsxs(type, props) }
const nbsp = function (s) { return s == null ? '' : String(s) }
const fmtMoney = function (s) { const n = Number(s); return Number.isFinite(n) ? n.toFixed(2) : nbsp(s) }
const pctWidth = function (v) { return Math.max(0, Math.min(100, Math.round(v))) + '%' }
const remaining = function (used) { return 100 - used }

const OC_LABELS = { rolling: '5 小时', weekly: '本周', monthly: '本月' }

const btnStyle = {
  fontSize: 12, padding: '4px 10px', borderRadius: 6,
  border: '1px solid ' + C.border, background: C.bg, color: C.fg, cursor: 'pointer',
}
const inputStyle = {
  fontSize: 12, padding: '4px 8px', borderRadius: 6, width: '100%', boxSizing: 'border-box',
  border: '1px solid ' + C.border, background: C.bg, color: C.fg,
}
const selectStyle = {
  fontSize: 12, padding: '4px 8px', borderRadius: 6, boxSizing: 'border-box',
  border: '1px solid ' + C.border, background: C.bg, color: C.fg,
}

function fmtReset(iso) {
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return ''
    const diff = d.getTime() - Date.now()
    if (diff < 0) return '已重置'
    const mins = Math.max(0, Math.round(diff / 60000))
    const h = Math.floor(mins / 60)
    const m = mins % 60
    if (h >= 48) return Math.round(h / 24) + ' 天后重置'
    if (h >= 1) return h + ' 小时 ' + m + ' 分钟后重置'
    return m + ' 分钟后重置'
  } catch (e) { return '' }
}

function fmtGenerated(iso) {
  try {
    const d = new Date(iso)
    if (Number.isNaN(d.getTime())) return ''
    return d.toLocaleString('zh-CN', { hour12: false })
  } catch (e) { return '' }
}

// ---------------------------------------------------------------- widgets

function Bar(props) {
  const used = props.used
  const remain = remaining(used)
  const color = remain < 5 ? C.danger : remain < 20 ? C.warn : C.ok
  return els('div', { style: { display: 'flex', flexDirection: 'column', gap: 3, padding: '3px 0' }, children: [
    els('div', { style: { display: 'flex', justifyContent: 'space-between', gap: 8, fontSize: 11, color: C.fg2 }, children: [
      el('span', { children: props.label }),
      els('span', { children: [
        el('span', { children: '已用 ' + Math.round(used) + '%' }),
        props.resetsAt ? el('span', { style: { marginLeft: 6, color: C.fg3 }, children: fmtReset(props.resetsAt) }) : null,
        props.suffix != null ? el('span', { style: { marginLeft: 6, color: C.danger }, children: props.suffix }) : null,
      ] }),
    ] }),
    el('div', { style: { height: 6, borderRadius: 3, background: C.bg, overflow: 'hidden' }, children:
      el('div', { style: { height: '100%', width: pctWidth(used), background: color, borderRadius: 3 } }) }),
  ] })
}

function Card(props) {
  return els('div', { style: {
    border: '1px solid ' + C.border, borderRadius: 8, padding: 10, marginBottom: 8,
    background: C.bg, opacity: props.error ? 0.55 : 1,
  }, children: [
    els('div', { style: { display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }, children: [
      el('div', { style: { fontWeight: 600, fontSize: 13, color: C.fg }, children: props.title }),
      props.tag ? el('span', { style: { fontSize: 10, padding: '1px 6px', borderRadius: 4, border: '1px solid ' + C.border, color: C.fg2 }, children: props.tag }) : null,
    ] }),
    props.error ? el('div', { style: { fontSize: 12, color: C.danger }, children: props.error }) : props.children,
  ] })
}

function CodexCard(props) {
  const codex = props.codex
  if (!codex || !codex.present) return null
  const tag = codex.planType === 'plus' ? 'Codex Plus' : (codex.planType || '')
  const windows = (codex.windows || []).map(function (w) {
    return el(Bar, { key: w.key, label: '周窗口', used: w.usedPercent, resetsAt: w.resetsAt })
  })
  return el(Card, { title: 'Codex', tag: tag, error: codex.status === 'error' ? codex.error : null, children: windows })
}

function OpencodeCard(props) {
  const acc = props.acc
  const windows = (acc.windows || []).map(function (w) {
    return el(Bar, { key: w.key, label: OC_LABELS[w.key] || w.key, used: w.usedPercent, resetsAt: w.resetsAt, suffix: w.windowStatus === 'rate-limited' ? '已限流' : null })
  })
  return el(Card, { title: acc.alias || acc.id, tag: 'opencode-go', error: acc.status === 'error' ? acc.error : null, children: windows })
}

function DeepseekCard(props) {
  const acc = props.acc
  const settings = props.settings
  const [open, setOpen] = useState(false)
  const err = acc.status === 'error' ? acc.error : null
  const b = (acc.balances || [])[0]
  let color = C.fg
  if (err || acc.isAvailable === false) {
    color = C.danger
  } else if (b) {
    const total = Number(b.total)
    if (Number.isFinite(total)) {
      const red = Number(settings && settings.alertRed != null ? settings.alertRed : 2)
      const yellow = Number(settings && settings.alertYellow != null ? settings.alertYellow : 10)
      if (total < red) color = C.danger
      else if (total < yellow) color = C.warn
    }
  }
  return el(Card, { title: acc.alias || acc.id, tag: 'DeepSeek', error: err, children: [
    els('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }, children: [
      el('span', { style: { fontSize: 11, color: C.fg2 }, children: '余额（' + (b ? b.currency : 'CNY') + '）' }),
      el('span', { style: { fontSize: 16, fontWeight: 600, color: color }, children: b ? fmtMoney(b.total) : '—' }),
    ] }),
    acc.isAvailable === false && !err ? el('div', { style: { fontSize: 11, color: C.danger }, children: '余额不可用（is_available=false）' }) : null,
    b ? el('button', { type: 'button', style: { fontSize: 11, color: C.accent, background: 'none', border: 'none', padding: 0, cursor: 'pointer' }, onClick: function () { setOpen(!open) }, children: open ? '收起明细' : '展开明细' }) : null,
    open && b ? els('div', { style: { fontSize: 11, color: C.fg2, marginTop: 4, display: 'flex', flexDirection: 'column', gap: 2 }, children: [
      el('span', { children: '赠送：' + fmtMoney(b.granted) }),
      el('span', { children: '充值：' + fmtMoney(b.toppedUp) }),
    ] }) : null,
  ] })
}

// ---------------------------------------------------------------- pane

function App() {
  const statusQ = useQuery({ queryKey: Q_STATUS, queryFn: function () { return ctx.rest('/status') }, staleTime: 10000 })
  const settingsQ = useQuery({ queryKey: Q_SETTINGS, queryFn: function () { return ctx.rest('/settings') }, staleTime: 30000 })
  const status = statusQ.data
  const settings = settingsQ.data
  const refresh = function () { queryClient.invalidateQueries({ queryKey: Q_STATUS }) }

  if (statusQ.isLoading) return el('div', { style: { padding: 12, fontSize: 12, color: C.fg3 }, children: '加载中…' })
  if (statusQ.isError) return els('div', { style: { padding: 12, fontSize: 12, display: 'flex', flexDirection: 'column', gap: 8 }, children: [
    el('div', { style: { color: C.danger, lineHeight: 1.6 }, children: '后端不可用：请按 README 在 config.yaml 的 plugins.enabled 中加入 usage-kanban，并重启 Hermes gateway（挑你方便的时间）。' }),
    el('button', { type: 'button', onClick: refresh, style: btnStyle, children: '重试' }),
  ] })

  const codex = status.codex
  const ocAccounts = ((status.opencode && status.opencode.accounts) || []).filter(function (a) { return !a.hidden })
  const dsAccounts = ((status.deepseek && status.deepseek.accounts) || []).filter(function (a) { return !a.hidden })

  return els('div', { style: { padding: 10, display: 'flex', flexDirection: 'column', minHeight: '100%', boxSizing: 'border-box' }, children: [
    els('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }, children: [
      el('div', { style: { fontWeight: 600, fontSize: 13, color: C.fg }, children: '额度看板' }),
      els('div', { style: { display: 'flex', gap: 6 }, children: [
        el('button', { type: 'button', onClick: refresh, style: btnStyle, children: '刷新' }),
        el('button', { type: 'button', onClick: function () { host.navigate('/usage-kanban-settings') }, style: btnStyle, children: '设置' }),
      ] }),
    ] }),
    el(CodexCard, { codex: codex }),
    ocAccounts.map(function (a) { return el(OpencodeCard, { key: a.id, acc: a }) }),
    dsAccounts.map(function (a) { return el(DeepseekCard, { key: a.id, acc: a, settings: settings }) }),
    els('div', { style: { marginTop: 'auto', paddingTop: 8, fontSize: 10, color: C.fg3, display: 'flex', flexDirection: 'column', gap: 2 }, children: [
      el('span', { children: ocAccounts.length + dsAccounts.length + ' 个已配置账号' + (codex && codex.present ? ' · Codex 已检测' : '') }),
      status.generatedAt ? el('span', { children: '更新于 ' + fmtGenerated(status.generatedAt) }) : null,
      el('span', { children: '点击「设置」管理账号与芯片显示' }),
    ] }),
  ] })
}

// ---------------------------------------------------------------- chip

function errorCount(status) {
  let n = 0
  if (status.codex && status.codex.status === 'error') n++
  ;((status.opencode && status.opencode.accounts) || []).forEach(function (a) { if (a.status === 'error') n++ })
  ;((status.deepseek && status.deepseek.accounts) || []).forEach(function (a) { if (a.status === 'error' || a.isAvailable === false) n++ })
  return n
}

function worstWindow(status) {
  let best = null
  const consider = function (text, w) {
    if (!w) return
    if (!best || w.usedPercent > best.used) {
      best = { used: w.usedPercent, text: text + ' ' + Math.round(w.usedPercent) + '%', alert: remaining(w.usedPercent) < 5 }
    }
  }
  const codex = status.codex
  if (codex && codex.status === 'ok') consider('Codex · 周', (codex.windows || [])[0])
  ;((status.opencode && status.opencode.accounts) || []).forEach(function (a) {
    if (a.hidden || a.status !== 'ok') return
    const ws = a.windows || []
    const name = 'opencode ' + (a.alias || a.id)
    consider(name + ' · 5h', ws.find(function (w) { return w.key === 'rolling' }))
    consider(name + ' · 周', ws.find(function (w) { return w.key === 'weekly' }))
    consider(name + ' · 月', ws.find(function (w) { return w.key === 'monthly' }))
  })
  return best
}

function resolvePinned(status, pinned) {
  if (!pinned || !status) return null
  if (pinned.provider === 'codex') {
    const codex = status.codex
    if (!codex || !codex.present || codex.status !== 'ok') return null
    const w = ((codex.windows || []).find(function (x) { return x.key === (pinned.windowKey || 'weekly') })) || (codex.windows || [])[0]
    if (!w) return null
    return { text: 'Codex · 周 ' + Math.round(w.usedPercent) + '%', alert: remaining(w.usedPercent) < 5 }
  }
  if (pinned.provider === 'opencode') {
    const acc = ((status.opencode && status.opencode.accounts) || []).find(function (a) { return a.id === pinned.accountId })
    if (!acc || acc.hidden || acc.status !== 'ok') return null
    const w = ((acc.windows || []).find(function (x) { return x.key === pinned.windowKey })) || (acc.windows || [])[0]
    if (!w) return null
    return { text: 'opencode ' + (acc.alias || acc.id) + ' · ' + (OC_LABELS[w.key] || w.key) + ' ' + Math.round(w.usedPercent) + '%', alert: remaining(w.usedPercent) < 5 }
  }
  if (pinned.provider === 'deepseek') {
    const acc = ((status.deepseek && status.deepseek.accounts) || []).find(function (a) { return a.id === pinned.accountId })
    if (!acc || acc.hidden) return null
    if (acc.status !== 'ok') return { text: 'DeepSeek ' + (acc.alias || acc.id) + ' 异常', alert: true }
    const b = (acc.balances || [])[0]
    if (!b) return null
    return { text: 'DeepSeek ' + (acc.alias || acc.id) + ' ¥' + fmtMoney(b.total), alert: acc.isAvailable === false }
  }
  return null
}

function Chip() {
  const statusQ = useQuery({ queryKey: Q_STATUS, queryFn: function () { return ctx.rest('/status') }, staleTime: 10000 })
  const settingsQ = useQuery({ queryKey: Q_SETTINGS, queryFn: function () { return ctx.rest('/settings') }, staleTime: 30000 })
  const status = statusQ.data
  const settings = settingsQ.data || {}
  const mode = settings.chipMode || 'auto'
  let text = '—'
  let alert = false
  if (statusQ.isError) {
    text = '额度看板 ⚠'
  } else if (status) {
    const errs = errorCount(status)
    const worst = worstWindow(status)
    if (mode === 'pinned') {
      const pinned = resolvePinned(status, settings.pinned)
      if (pinned) { text = pinned.text; alert = pinned.alert }
      else if (worst) { text = worst.text; alert = worst.alert }
      else text = '额度正常'
    } else if (mode === 'worst') {
      if (worst) { text = worst.text; alert = worst.alert }
    } else if (mode === 'errors') {
      text = errs > 0 ? errs + ' 个账号异常' : '额度正常'
      alert = errs > 0
    } else {
      if (errs > 0) { text = errs + ' 个账号异常'; alert = true }
      else if (worst) { text = worst.text; alert = worst.alert }
      else text = '额度正常'
    }
  }
  return el('button', { type: 'button', className: 'px-1.5 text-[0.6875rem]', style: { color: alert ? C.danger : C.fg3, background: 'none', border: 'none', cursor: 'pointer' }, onClick: function () { haptic('tap'); host.navigate('/usage-kanban-settings') }, children: text })
}

// ---------------------------------------------------------------- settings

function pinToValue(pinned) {
  if (!pinned) return ''
  if (pinned.provider === 'opencode') return 'opencode|' + pinned.accountId + '|' + (pinned.windowKey || '')
  if (pinned.provider === 'deepseek') return 'deepseek|' + pinned.accountId + '|balance'
  return 'codex|weekly'
}

function valueToPin(value) {
  if (!value) return null
  const parts = value.split('|')
  if (parts[0] === 'codex') return { provider: 'codex', accountId: null, windowKey: 'weekly' }
  if (parts[0] === 'opencode') return { provider: 'opencode', accountId: parts[1], windowKey: parts[2] || 'weekly' }
  if (parts[0] === 'deepseek') return { provider: 'deepseek', accountId: parts[1], windowKey: 'balance' }
  return null
}

function pinOptions(status) {
  const opts = []
  const codex = status && status.codex
  if (codex && codex.present) opts.push({ value: 'codex|weekly', label: 'Codex Plus · 周窗口' })
  ;(((status && status.opencode && status.opencode.accounts) || [])).forEach(function (a) {
    ;((a.windows || [])).forEach(function (w) {
      opts.push({ value: 'opencode|' + a.id + '|' + w.key, label: 'opencode-go ' + (a.alias || a.id) + ' · ' + (OC_LABELS[w.key] || w.key) })
    })
  })
  ;(((status && status.deepseek && status.deepseek.accounts) || [])).forEach(function (a) {
    opts.push({ value: 'deepseek|' + a.id + '|balance', label: 'DeepSeek ' + (a.alias || a.id) + ' · 余额' })
  })
  return opts
}

function invalidateAll() {
  queryClient.invalidateQueries({ queryKey: Q_STATUS })
  queryClient.invalidateQueries({ queryKey: Q_ACCOUNTS })
  queryClient.invalidateQueries({ queryKey: Q_SETTINGS })
}

function Section(props) {
  return els('div', { style: { marginBottom: 16 }, children: [
    el('div', { style: { fontWeight: 600, fontSize: 13, color: C.fg, marginBottom: 8 }, children: props.title }),
    props.children,
  ] })
}

function AccountRow(props) {
  const acc = props.acc
  const [alias, setAlias] = useState(acc.alias)
  const [key, setKey] = useState('')
  const save = useMutation({ mutationFn: function (body) { return ctx.rest('/accounts/' + acc.id, { method: 'PATCH', body: body }) } })
  const remove = useMutation({ mutationFn: function () { return ctx.rest('/accounts/' + acc.id, { method: 'DELETE' }) } })

  const doSave = function (extra) {
    const body = Object.assign({}, extra)
    if (alias !== acc.alias) body.alias = alias
    if (key && key.trim()) body.key = key.trim()
    if (Object.keys(body).length === 0) { host.notify({ kind: 'info', message: '没有需要保存的改动' }); return }
    save.mutateAsync(body).then(function (res) {
      if (res && res.ok) { setKey(''); invalidateAll(); host.notify({ kind: 'info', message: '已保存' }) }
      else host.notify({ kind: 'error', message: (res && res.error) || '保存失败' })
    }).catch(function (e) { host.notifyError(e, '保存失败') })
  }
  const doRemove = function () {
    remove.mutateAsync().then(function (res) {
      if (res && res.ok) { invalidateAll(); host.notify({ kind: 'info', message: '已删除' }) }
      else host.notify({ kind: 'error', message: (res && res.error) || '删除失败' })
    }).catch(function (e) { host.notifyError(e, '删除失败') })
  }

  return els('div', { style: { border: '1px solid ' + C.border, borderRadius: 8, padding: 10, marginBottom: 8, display: 'flex', flexDirection: 'column', gap: 6 }, children: [
    els('div', { style: { display: 'flex', justifyContent: 'space-between', alignItems: 'center' }, children: [
      el('span', { style: { fontSize: 12, fontWeight: 600, color: C.fg }, children: acc.provider === 'opencode' ? 'opencode-go' : 'DeepSeek' }),
      el('span', { style: { fontSize: 11, color: C.fg3 }, children: acc.keyMasked || '' }),
    ] }),
    el('input', { value: alias, onChange: function (e) { setAlias(e.target.value) }, style: inputStyle, placeholder: '别名' }),
    el('input', { type: 'password', value: key, onChange: function (e) { setKey(e.target.value) }, style: inputStyle, placeholder: '输入新 key 覆盖（留空不修改）' }),
    els('div', { style: { display: 'flex', gap: 6, alignItems: 'center' }, children: [
      el('button', { type: 'button', onClick: doSave, style: btnStyle, children: '保存' }),
      els('label', { style: { display: 'flex', alignItems: 'center', gap: 4, fontSize: 12, color: C.fg2, cursor: 'pointer' }, children: [
        el('input', { type: 'checkbox', checked: !!acc.hidden, onChange: function (e) { doSave({ hidden: e.target.checked }) }, style: { cursor: 'pointer' } }),
        el('span', { children: '隐藏' }),
      ] }),
      el('button', { type: 'button', onClick: doRemove, style: Object.assign({}, btnStyle, { color: C.danger }), children: '删除' }),
    ] }),
  ] })
}

function SettingsPage() {
  const settingsQ = useQuery({ queryKey: Q_SETTINGS, queryFn: function () { return ctx.rest('/settings') }, staleTime: 30000 })
  const accountsQ = useQuery({ queryKey: Q_ACCOUNTS, queryFn: function () { return ctx.rest('/accounts') }, staleTime: 30000 })
  const statusQ = useQuery({ queryKey: Q_STATUS, queryFn: function () { return ctx.rest('/status') }, staleTime: 10000 })
  const saveSettings = useMutation({ mutationFn: function (body) { return ctx.rest('/settings', { method: 'PUT', body: body }) } })
  const addAccount = useMutation({ mutationFn: function (body) { return ctx.rest('/accounts', { method: 'POST', body: body }) } })

  const [mode, setMode] = useState('auto')
  const [pinned, setPinned] = useState('')
  const [yellow, setYellow] = useState('10')
  const [red, setRed] = useState('2')
  const [prov, setProv] = useState('opencode')
  const [alias, setAlias] = useState('')
  const [key, setKey] = useState('')

  const settings = settingsQ.data
  useEffect(function () {
    if (!settings) return
    setMode(settings.chipMode || 'auto')
    setPinned(pinToValue(settings.pinned))
    setYellow(settings.alertYellow != null ? String(settings.alertYellow) : '10')
    setRed(settings.alertRed != null ? String(settings.alertRed) : '2')
  }, [settings])

  const doSaveSettings = function () {
    const y = Number(yellow)
    const r = Number(red)
    if (!Number.isFinite(y) || !Number.isFinite(r) || y < 0 || r < 0) {
      host.notify({ kind: 'error', message: '警戒金额必须是大于等于 0 的数字' })
      return
    }
    const body = { chipMode: mode, pinned: valueToPin(pinned), alertYellow: y, alertRed: r }
    saveSettings.mutateAsync(body).then(function (res) {
      if (res && res.ok) { invalidateAll(); host.notify({ kind: 'info', message: '设置已保存' }) }
      else host.notify({ kind: 'error', message: (res && res.error) || '保存失败' })
    }).catch(function (e) { host.notifyError(e, '保存失败') })
  }

  const doAdd = function () {
    if (!alias.trim()) { host.notify({ kind: 'error', message: '别名不能为空' }); return }
    if (!key.trim()) { host.notify({ kind: 'error', message: 'API key 不能为空' }); return }
    addAccount.mutateAsync({ provider: prov, alias: alias.trim(), key: key.trim() }).then(function (res) {
      if (res && res.ok) {
        setAlias(''); setKey('')
        invalidateAll()
        host.notify({ kind: 'info', message: '已添加账号' })
      } else host.notify({ kind: 'error', message: (res && res.error) || '添加失败' })
    }).catch(function (e) { host.notifyError(e, '添加失败') })
  }

  const accounts = (accountsQ.data && accountsQ.data.accounts) || []
  const options = pinOptions(statusQ.data)

  return els('div', { style: { padding: 16, maxWidth: 560, display: 'flex', flexDirection: 'column', gap: 4 }, children: [
    el('div', { style: { fontWeight: 700, fontSize: 15, color: C.fg, marginBottom: 8 }, children: '额度看板设置' }),

    el(Section, { title: '状态栏芯片', children: [
      els('div', { style: { display: 'flex', flexDirection: 'column', gap: 8 }, children: [
        el('label', { style: { fontSize: 12, color: C.fg2 }, children: '显示模式' }),
        el('select', { value: mode, onChange: function (e) { setMode(e.target.value) }, style: selectStyle, children: [
          el('option', { value: 'pinned', children: '固定订阅（自选显示哪一个）' }),
          el('option', { value: 'auto', children: '自动（异常优先，无异常显最紧张项）' }),
          el('option', { value: 'worst', children: '始终显示最紧张项' }),
          el('option', { value: 'errors', children: '始终显示异常计数' }),
        ] }),
        el('label', { style: { fontSize: 12, color: C.fg2 }, children: '固定显示的订阅' }),
        el('select', { value: pinned, onChange: function (e) { setPinned(e.target.value) }, style: selectStyle, children: [
          el('option', { value: '', children: '（未选择，回落为自动）' }),
          options.map(function (o) { return el('option', { key: o.value, value: o.value, children: o.label }) }),
        ] }),
      ] }),
    ] }),

    el(Section, { title: 'DeepSeek 金额警戒（元）', children:
      els('div', { style: { display: 'flex', gap: 8 }, children: [
        el('label', { style: { display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: C.fg2, flex: 1 }, children: [
          el('span', { children: '黄线（低于此金额变黄）' }),
          el('input', { type: 'number', value: yellow, onChange: function (e) { setYellow(e.target.value) }, style: inputStyle }),
        ] }),
        el('label', { style: { display: 'flex', flexDirection: 'column', gap: 4, fontSize: 12, color: C.fg2, flex: 1 }, children: [
          el('span', { children: '红线（低于此金额变红）' }),
          el('input', { type: 'number', value: red, onChange: function (e) { setRed(e.target.value) }, style: inputStyle }),
        ] }),
      ] }) }),
    el('button', { type: 'button', onClick: doSaveSettings, style: btnStyle, children: '保存设置' }),

    el(Section, { title: '账号管理', children: [
      accounts.map(function (a) { return el(AccountRow, { key: a.id, acc: a }) }),
      els('div', { style: { border: '1px dashed ' + C.border, borderRadius: 8, padding: 10, display: 'flex', flexDirection: 'column', gap: 6 }, children: [
        el('select', { value: prov, onChange: function (e) { setProv(e.target.value) }, style: selectStyle, children: [
          el('option', { value: 'opencode', children: 'opencode-go' }),
          el('option', { value: 'deepseek', children: 'DeepSeek' }),
        ] }),
        el('input', { value: alias, onChange: function (e) { setAlias(e.target.value) }, style: inputStyle, placeholder: '别名（如：主号 / 小号2）' }),
        el('input', { type: 'password', value: key, onChange: function (e) { setKey(e.target.value) }, style: inputStyle, placeholder: 'API key' }),
        el('button', { type: 'button', onClick: doAdd, style: btnStyle, children: '添加账号' }),
      ] }),
      el('div', { style: { fontSize: 11, color: C.fg3, marginTop: 8, lineHeight: 1.6 }, children: 'key 明文存储在后端目录 accounts.json，界面仅显示脱敏尾号。Codex Plus 无需配置，检测到 ~/.codex/auth.json 即自动出现。' }),
    ] }),
  ] })
}

// ---------------------------------------------------------------- plugin

export default {
  id: 'usage-kanban',
  name: '额度看板',
  register: function (c) {
    ctx = c
    ctx.register({ id: 'pane', area: 'panes', title: '额度看板', data: { placement: 'right', width: '320px' }, render: function () { return el(App, {}) } })
    ctx.register({ id: 'chip', area: STATUSBAR_AREAS.right, order: 130, render: function () { return el(Chip, {}) } })
    ctx.register({ id: 'settings', area: ROUTES_AREA, data: { path: '/usage-kanban-settings' }, render: function () { return el(SettingsPage, {}) } })
    ctx.register({ id: 'settings-nav', area: SIDEBAR_NAV_AREA, data: { path: '/usage-kanban-settings', label: '额度看板设置', codicon: 'gear' } })
  },
}
