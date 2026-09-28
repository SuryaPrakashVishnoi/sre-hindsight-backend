import { useState } from 'react'
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer } from 'recharts'
import { Send } from 'lucide-react'
import { api, API_URL, MOCK } from '../services/api'
import { Card, Async, LoadingState, ErrorState, EmptyState, useAsync, t } from '../components/ui'
import { computeStats } from './Dashboard'

const Chart = ({ title, data }) => <Card title={title}>{data.length < 1 ? <EmptyState msg="Not enough data yet." /> :
  <div className="h-48"><ResponsiveContainer><BarChart data={data}><XAxis dataKey="k" stroke="#8b97a6" fontSize={11} /><YAxis allowDecimals={false} stroke="#8b97a6" fontSize={11} /><Tooltip contentStyle={{ background: '#151b23', border: '1px solid #262e3a' }} /><Bar dataKey="v" fill="#5aa9f0" /></BarChart></ResponsiveContainer></div>}</Card>
const count = (arr, f) => Object.entries(arr.reduce((a, x) => { const k = f(x); if (k) a[k] = (a[k] || 0) + 1; return a }, {})).map(([k, v]) => ({ k, v }))

export function Analytics() {
  const s = useAsync(api.incidents, [])
  return <Async state={s} empty={d => !d.length} emptyMsg="Not enough data yet.">{d => { const st = computeStats(d); return <div className="grid gap-4 md:grid-cols-2">
    <Chart title="Incident volume" data={count(d, i => i.created_at?.slice(0, 10))} />
    <Chart title="Severity distribution" data={count(d, i => i.severity)} />
    <Chart title="Resolution time (min)" data={d.filter(i => Number.isFinite(i.resolution_minutes)).map(i => ({ k: i.id, v: i.resolution_minutes }))} />
    <Chart title="Memory & deployments" data={[{ k: 'Memory reuse', v: st.reuse }, { k: 'Good recs', v: st.good }, { k: 'Failed prevented', v: st.prevented }, { k: 'Correlated', v: st.correlated }]} /></div> }}</Async>
}

export function Chat() {
  const [msgs, setMsgs] = useState([{ r: 'ai', t: 'Describe the incident. I will check organizational memory first.' }]), [txt, setTxt] = useState(''), [last, setLast] = useState(null), [busy, setBusy] = useState(false)
  const push = (r, x) => setMsgs(m => [...m, { r, t: x }])
  const fromLast = f => last ? f(last) : 'Analyze an incident first.'
  const quick = { 'Find similar incidents': () => fromLast(l => l.historical_matches?.length ? l.historical_matches.map(m => m.incident_id).join(', ') : 'No relevant historical incident was found.'),
    'What fixed this before?': () => fromLast(l => l.historical_matches?.map(m => `${m.incident_id}: ${t(m.successful_fix)}`).join('\n') || 'Not available'),
    'What failed last time?': () => fromLast(l => l.failed_attempts?.join(', ') || 'No failed attempts recorded.'),
    'Was there a recent deployment?': () => fromLast(l => l.deployment_correlation ? `Potential correlation: ${l.deployment_correlation.version} (${t(l.deployment_correlation.change)}). Correlation does not prove causation.` : 'No deployment correlation detected.'),
    'Why are you recommending this?': () => fromLast(l => t(l.explanation)) }
  const send = async () => { if (!txt.trim()) return; const q = txt; setTxt(''); push('eng', q); setBusy(true)
    try { const r = await api.analyze({ title: q, error: q, symptoms: q }); setLast(r)
      push('ai', `Found ${r.historical_matches?.length || 0} similar incident(s).\n${r.historical_matches?.map(m => `${m.incident_id}: ${t(m.root_cause)} → ${t(m.successful_fix)}`).join('\n') || ''}`) } catch (e) { push('ai', `Analysis failed: ${e.message}`) } finally { setBusy(false) } }
  return <Card title="Incident assistant"><div className="mb-3 flex max-h-[50vh] flex-col gap-2 overflow-y-auto">{msgs.map((m, i) => <div key={i} className={`max-w-[85%] whitespace-pre-line rounded px-3 py-2 text-sm ${m.r === 'ai' ? 'self-start bg-line/50' : 'self-end bg-info/20'}`}><div className="text-xs text-dim">{m.r === 'ai' ? 'AI' : 'Engineer'}</div>{m.t}</div>)}{busy && <LoadingState rows={1} />}</div>
    <div className="mb-3 flex flex-wrap gap-2">{Object.entries(quick).map(([k, f]) => <button key={k} onClick={() => { push('eng', k); push('ai', f()) }} className="rounded border border-line px-2 py-1 text-xs hover:bg-line">{k}</button>)}</div>
    <div className="flex gap-2"><input value={txt} onChange={e => setTxt(e.target.value)} onKeyDown={e => e.key === 'Enter' && send()} placeholder="Production API is returning 500 errors…" className="flex-1 rounded border border-line bg-bg px-3 py-2 text-sm" /><button onClick={send} aria-label="Send" className="rounded bg-ok px-3 text-black"><Send size={14} /></button></div></Card>
}

export function Settings() {
  const h = useAsync(api.health, [])
  const rows = d => [['Environment', import.meta.env.MODE], ['API URL', API_URL], ['Mock Mode', MOCK ? 'Enabled' : 'Disabled'], ['AI Model', d?.model], ['Hindsight Status', d?.hindsight_status ?? d?.hindsight], ['Memory Bank', d?.memory_bank], ['Deployment Provider', d?.deployment_provider], ['GitHub Repository', d?.github_repo]]
  return <Card title="Settings (read-only)">{h.loading ? <LoadingState /> : <>{h.error && <ErrorState error={h.error} onRetry={h.reload} msg="Backend health check failed." />}
    <dl className="mt-2 space-y-1 text-sm">{rows(h.data).map(([k, v]) => <div key={k}><dt className="inline text-dim">{k}: </dt><dd className="inline">{t(v)}</dd></div>)}</dl></>}
    <p className="mt-3 text-xs text-dim">API keys are never exposed in the frontend.</p></Card>
}
