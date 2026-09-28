import { createContext, useContext, useEffect, useState, useCallback } from 'react'
import { AlertTriangle, Inbox, X } from 'lucide-react'

export const t = v => v == null || v === '' ? 'Not available' : typeof v === 'string' || typeof v === 'number' ? String(v) : JSON.stringify(v)
export const fmt = v => { if (!v) return 'Not available'; const d = new Date(v); return isNaN(d) ? String(v) : d.toLocaleString() }

export function useAsync(fn, deps = []) {
  const [s, set] = useState({ data: null, loading: true, error: null })
  const load = useCallback(() => { set(x => ({ ...x, loading: true, error: null })); fn().then(data => set({ data, loading: false, error: null })).catch(e => set({ data: null, loading: false, error: e })) }, deps)
  useEffect(load, [load])
  return { ...s, reload: load }
}

const tones = { ok: 'text-ok border-ok/40', crit: 'text-crit border-crit/40', warn: 'text-warn border-warn/40', info: 'text-info border-info/40', dim: 'text-dim border-line' }
export const Badge = ({ tone = 'info', children }) => <span className={`inline-block rounded border px-1.5 py-0.5 text-xs ${tones[tone]}`}>{children}</span>
export const sevTone = s => ({ critical: 'crit', high: 'crit', medium: 'warn', low: 'info' }[String(s).toLowerCase()] || 'dim')

export const Card = ({ title, icon, children, className = '' }) => (
  <section className={`rounded-lg border border-line bg-panel ${className}`}>
    {title && <h3 className="flex items-center gap-2 border-b border-line px-4 py-3 text-sm font-medium">{icon}{title}</h3>}
    <div className="p-4">{children}</div>
  </section>)

export const StatCard = ({ label, value, tone = 'info' }) => (
  <div className="rounded-lg border border-line bg-panel p-4"><div className="text-sm text-dim">{label}</div><div className={`mt-1 font-mono text-2xl ${tones[tone].split(' ')[0]}`}>{value}</div></div>)

export const LoadingState = ({ rows = 3 }) => <div className="space-y-2" aria-busy>{Array.from({ length: rows }, (_, i) => <div key={i} className="h-10 animate-pulse rounded bg-line/60" />)}</div>
export const ErrorState = ({ error, onRetry, msg }) => (
  <div className="rounded-lg border border-crit/40 bg-crit/5 p-4 text-sm"><div className="flex items-center gap-2 text-crit"><AlertTriangle size={16} />{msg || 'Could not load data.'}</div>
    <p className="mt-1 text-dim">{error?.response?.data?.detail || error?.message}</p>{onRetry && <button onClick={onRetry} className="mt-3 rounded border border-line px-3 py-1 hover:bg-line">Retry</button>}</div>)
export const EmptyState = ({ msg }) => <div className="flex items-center gap-2 py-4 text-sm text-dim"><Inbox size={16} />{msg}</div>

export function Async({ state, empty, emptyMsg, children, rows }) {
  if (state.loading) return <LoadingState rows={rows} />
  if (state.error) return <ErrorState error={state.error} onRetry={state.reload} />
  if (empty?.(state.data) ?? !state.data) return <EmptyState msg={emptyMsg} />
  return children(state.data)
}

export function Modal({ open, onClose, title, children }) {
  if (!open) return null
  return <div className="fixed inset-0 z-50 grid place-items-center bg-black/70 p-4" role="dialog" aria-modal onClick={onClose}>
    <div className="w-full max-w-md rounded-lg border border-line bg-panel" onClick={e => e.stopPropagation()}>
      <div className="flex items-center justify-between border-b border-line px-4 py-3 font-medium">{title}<button onClick={onClose} aria-label="Close"><X size={16} /></button></div>
      <div className="p-4">{children}</div></div></div>
}

const ToastCtx = createContext(() => {})
export const useToast = () => useContext(ToastCtx)
export function ToastProvider({ children }) {
  const [msg, setMsg] = useState(null)
  const show = useCallback((m, tone = 'ok') => { setMsg({ m, tone }); setTimeout(() => setMsg(null), 4000) }, [])
  return <ToastCtx.Provider value={show}>{children}{msg && <div role="status" className={`fixed bottom-4 right-4 z-50 rounded border bg-panel px-4 py-2 text-sm ${tones[msg.tone]}`}>{msg.m}</div>}</ToastCtx.Provider>
}
