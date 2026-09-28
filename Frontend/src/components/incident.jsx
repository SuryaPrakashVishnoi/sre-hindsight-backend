import { useState } from 'react'
import { Link } from 'react-router-dom'
import { Brain, Search, Wrench, XCircle, Lightbulb, Rocket, ExternalLink, ThumbsUp, ThumbsDown } from 'lucide-react'
import { Badge, Card, EmptyState, Modal, sevTone, t, fmt, useToast } from './ui'
import { api, MOCK } from '../services/api'

const list = v => Array.isArray(v) ? v : v ? [v] : []

export const IncidentTable = ({ rows }) => (
  <div className="overflow-x-auto rounded-lg border border-line bg-panel"><table className="w-full min-w-[720px] text-left text-sm">
    <thead className="border-b border-line text-dim"><tr>{['ID', 'Title', 'Service', 'Severity', 'Status', 'Created', 'Matches', 'Deployment', 'Confidence'].map(h => <th key={h} className="px-3 py-2 font-normal">{h}</th>)}</tr></thead>
    <tbody>{rows.map(i => <tr key={i.id} className="border-b border-line/60 last:border-0 hover:bg-line/30">
      <td className="px-3 py-2 font-mono"><Link className="text-info" to={`/incidents/${i.id}`}>{i.id}</Link></td><td className="px-3 py-2">{t(i.title)}</td><td className="px-3 py-2">{t(i.service)}</td>
      <td className="px-3 py-2"><Badge tone={sevTone(i.severity)}>{t(i.severity)}</Badge></td><td className="px-3 py-2">{t(i.status)}</td><td className="px-3 py-2">{fmt(i.created_at)}</td>
      <td className="px-3 py-2">{i.historical_matches ? i.historical_matches.length : 'Not available'}</td>
      <td className="px-3 py-2">{i.deployment_correlation ? i.deployment_correlation.version || 'Yes' : 'None'}</td>
      <td className="px-3 py-2">{i.confidence != null ? i.confidence : 'Not available'}</td></tr>)}</tbody></table></div>)

export const HistoricalMatches = ({ matches = [] }) => (
  <Card title="Historical Memory" icon={<Brain size={16} className="text-ok" />}>
    {!matches.length ? <EmptyState msg="No relevant historical incident was found." /> :
      <div className="space-y-3">{matches.map((m, k) => <div key={k} className="rounded border border-line p-3 text-sm">
        <div className="flex items-center gap-2"><Link to={`/incidents/${m.incident_id || m.id}`} className="font-mono text-info">{m.incident_id || m.id}</Link>
          {m.similarity != null && <Badge tone="ok">similarity {m.similarity}</Badge>}</div>
        <p className="mt-2"><span className="text-dim">Root cause: </span>{t(m.root_cause)}</p>
        <p><span className="text-dim">Successful fix: </span>{t(m.successful_fix)}</p>
        <p><span className="text-dim">Failed attempts: </span>{list(m.failed_attempts).join(', ') || 'None recorded'}</p></div>)}</div>}
  </Card>)

export const RootCause = ({ rc }) => (
  <Card title="Root Cause" icon={<Search size={16} className="text-info" />}>
    <div className="grid gap-3 md:grid-cols-3 text-sm">
      {[['Historical Evidence', rc?.historical_evidence, 'ok'], ['Current Inference', rc?.inference, 'warn'], ['Unknown Information', rc?.unknown, 'dim']].map(([h, v, tone]) =>
        <div key={h} className="rounded border border-line p-3"><Badge tone={tone}>{h}</Badge><p className="mt-2">{t(v)}</p></div>)}</div>
  </Card>)

export const RecommendedActions = ({ steps = [] }) => (
  <Card title="Recommended Actions" icon={<Wrench size={16} className="text-info" />}>
    {!steps.length ? <EmptyState msg="Not available" /> : <ol className="space-y-2 text-sm">{steps.map((s, i) => <li key={i} className="flex gap-3"><span className="font-mono text-dim">{String(i + 1).padStart(2, '0')}</span>{t(s)}</li>)}</ol>}
  </Card>)

export const FailedAttempts = ({ items = [] }) => (
  <Card title="Failed Attempts" icon={<XCircle size={16} className="text-crit" />}>
    {!items.length ? <EmptyState msg="No failed attempts recorded." /> : <ul className="space-y-2 text-sm">{items.map((a, i) => <li key={i} className="rounded border border-crit/30 bg-crit/5 px-3 py-2 line-through decoration-crit/50">{t(a)}</li>)}</ul>}
  </Card>)

export const WhyRecommendation = ({ text, confidence }) => (
  <Card title="Why This Recommendation" icon={<Lightbulb size={16} className="text-warn" />}>
    <p className="text-sm">{t(text)}</p><p className="mt-2 text-sm text-dim">Confidence: {confidence != null ? confidence : 'Not available'}</p></Card>)

export const IncidentTimeline = ({ items = [] }) => (
  <Card title="Timeline">{!items.length ? <EmptyState msg="Not available" /> :
    <ol className="border-l border-line pl-4 text-sm">{items.map((e, i) => <li key={i} className="relative pb-3 last:pb-0"><span className="absolute -left-[21px] top-1.5 h-2 w-2 rounded-full bg-info" /><span className="font-mono text-dim">{e.time}</span> {e.label}</li>)}</ol>}</Card>)

export function useRollback(id) {
  const [open, setOpen] = useState(false), [busy, setBusy] = useState(false), [result, setResult] = useState(null), toast = useToast()
  const confirm = async () => { setBusy(true); try { setResult(await api.rollback(id)); setOpen(false) } catch (e) { toast(e.response?.data?.detail || e.message, 'crit') } finally { setBusy(false) } }
  return { open, setOpen, busy, result, confirm }
}

export const RollbackModal = ({ rb, d }) => (
  <Modal open={rb.open} onClose={() => rb.setOpen(false)} title="Rollback Deployment">
    <p className="text-sm">Deployment: <b>{t(d.version || d.deployment_id)}</b><br />Related PR: {d.pr_number ? `#${d.pr_number}` : 'Not available'}</p>
    <p className="mt-3 text-sm text-warn">{MOCK ? 'MOCK MODE: No production deployment will be changed. This rollback is simulated.' : 'This action may affect the production environment.'}</p>
    <div className="mt-4 flex justify-end gap-2"><button className="rounded border border-line px-3 py-1.5 text-sm" onClick={() => rb.setOpen(false)}>Cancel</button>
      <button disabled={rb.busy} className="rounded bg-crit px-3 py-1.5 text-sm font-medium text-black disabled:opacity-50" onClick={rb.confirm}>{rb.busy ? 'Rolling back…' : 'Confirm Rollback'}</button></div></Modal>)

export const RollbackResult = ({ r }) => r && (
  <div className={`mt-4 rounded border p-3 text-sm ${r.mock ? 'border-warn/50' : 'border-ok/50'}`}>
    {r.mock && <p className="mb-2 font-medium text-warn">MOCK MODE: No production deployment will be changed. This rollback is simulated.</p>}
    {[['Rollback ID', r.rollback_id], ['Deployment', r.deployment || r.deployment_id], ['Timestamp', fmt(r.timestamp)], ['Status', r.status], ['Result', r.result]].map(([k, v]) => <p key={k}><span className="text-dim">{k}: </span>{t(v)}</p>)}</div>)

export function DeploymentCorrelation({ d }) {
  const rb = useRollback(d?.deployment_id)
  return <Card title="Deployment Correlation" icon={<Rocket size={16} className="text-warn" />}>
    {!d ? <EmptyState msg="No deployment correlation detected." /> : <>
      <p className="mb-3 text-sm text-warn">Potential deployment correlation detected.</p>
      <dl className="grid gap-x-6 gap-y-1 text-sm sm:grid-cols-2">{[['Deployment', d.deployment_id], ['Version', d.version], ['Deployment Time', fmt(d.deployed_at)], ['Incident Time', fmt(d.incident_at)], ['Commit', d.commit], ['Pull Request', d.pr_number ? `#${d.pr_number}` : null], ['Change', d.change]].map(([k, v]) => <div key={k}><dt className="inline text-dim">{k}: </dt><dd className="inline">{t(v)}</dd></div>)}</dl>
      <p className="mt-3 text-xs text-dim">Correlation does not prove causation.</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {d.pr_url && <a href={d.pr_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 rounded border border-line px-3 py-1.5 text-sm text-info">View PR <ExternalLink size={12} /></a>}
        {d.rollback_available && <button onClick={() => rb.setOpen(true)} className="rounded border border-crit/50 px-3 py-1.5 text-sm text-crit">Roll Back Deployment</button>}</div>
      <RollbackResult r={rb.result} /><RollbackModal rb={rb} d={d} /></>}
  </Card>
}

export function FeedbackPanel({ incidentId }) {
  const [f, setF] = useState({ useful: null, rating: 0, action_taken: '', result: '', actual_root_cause: '' }), [done, setDone] = useState(false), [busy, setBusy] = useState(false), toast = useToast()
  const set = k => e => setF({ ...f, [k]: e.target.value })
  const submit = async () => { setBusy(true); try { await api.feedback({ incident_id: incidentId, ...f, rating: f.rating || undefined }); setDone(true) } catch (e) { toast(e.response?.data?.detail || e.message, 'crit') } finally { setBusy(false) } }
  if (done) return <Card><p className="text-ok">✓ Feedback saved to organizational memory.</p></Card>
  const inp = 'w-full rounded border border-line bg-bg px-2 py-1.5 text-sm'
  return <Card title="Was this recommendation useful?"><div className="space-y-3">
    <div className="flex flex-wrap items-center gap-2">
      {[[true, 'Useful', ThumbsUp], [false, 'Not Useful', ThumbsDown]].map(([v, l, I]) => <button key={l} onClick={() => setF({ ...f, useful: v })} className={`inline-flex items-center gap-1 rounded border px-3 py-1.5 text-sm ${f.useful === v ? 'border-info text-info' : 'border-line'}`}><I size={14} />{l}</button>)}
      <span className="ml-2 text-sm text-dim">Rating</span>{[1, 2, 3, 4, 5].map(n => <button key={n} onClick={() => setF({ ...f, rating: n })} className={`h-7 w-7 rounded border text-sm ${f.rating === n ? 'border-warn text-warn' : 'border-line'}`}>{n}</button>)}</div>
    <input className={inp} placeholder="Action taken" value={f.action_taken} onChange={set('action_taken')} />
    <input className={inp} placeholder="Result" value={f.result} onChange={set('result')} />
    <input className={inp} placeholder="Actual root cause" value={f.actual_root_cause} onChange={set('actual_root_cause')} />
    <button disabled={busy || f.useful === null} onClick={submit} className="rounded bg-ok px-3 py-1.5 text-sm font-medium text-black disabled:opacity-50">Save feedback</button></div></Card>
}

export function MemoryCard({ m }) {
  const [full, setFull] = useState(null), [err, setErr] = useState(null), x = full || m
  const load = () => api.memory(m.id).then(setFull).catch(setErr)
  return <Card><div className="flex items-center gap-2"><Link to={`/incidents/${m.id}`} className="font-mono text-info">{m.id}</Link><span className="text-sm">{t(m.title)}</span></div>
    <dl className="mt-3 grid gap-1 text-sm sm:grid-cols-2">{[['Symptoms', x.symptoms], ['Impact', x.impact], ['Root Cause', x.root_cause?.historical_evidence ?? x.root_cause], ['Successful Fix', x.successful_fix], ['Failed Attempts', list(x.failed_attempts).join(', ')], ['Feedback', x.feedback ? `${x.feedback.rating ?? '–'}/5` : null], ['Deployment', x.deployment_correlation?.version ?? x.deployment], ['Git Commit', x.deployment_correlation?.commit ?? x.commit], ['Pull Request', x.deployment_correlation?.pr_number ? `#${x.deployment_correlation.pr_number}` : x.pr], ['Related Incidents', list(x.related_incidents).join(', ')], ['Timestamp', fmt(x.created_at)]].map(([k, v]) => <div key={k}><dt className="inline text-dim">{k}: </dt><dd className="inline">{t(v || null)}</dd></div>)}</dl>
    {!full && <button onClick={load} className="mt-3 text-sm text-info">Load full memory record</button>}{err && <p className="mt-2 text-sm text-crit">Could not load record.</p>}</Card>
}
