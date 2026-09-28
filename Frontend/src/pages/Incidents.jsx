import { useState } from 'react'
import { useNavigate, useParams, useLocation } from 'react-router-dom'
import { Plus } from 'lucide-react'
import { api } from '../services/api'
import { Card, Async, useAsync, useToast, Badge, sevTone, t, fmt } from '../components/ui'
import { IncidentTable, HistoricalMatches, RootCause, RecommendedActions, FailedAttempts, WhyRecommendation, IncidentTimeline, DeploymentCorrelation, FeedbackPanel } from '../components/incident'

const blank = { title: '', service: '', error: '', symptoms: '', impact: '', environment: 'production', severity: 'medium' }

function IncidentForm({ onClose }) {
  const [f, setF] = useState(blank), [busy, setBusy] = useState(false), go = useNavigate(), toast = useToast()
  const inp = 'w-full rounded border border-line bg-bg px-2 py-1.5 text-sm'
  const submit = async e => { e.preventDefault(); setBusy(true)
    try { const r = await api.analyze(f); go(`/incidents/${r.id || 'new'}`, { state: { analysis: { ...f, ...r } } }) } catch (x) { toast(x.response?.data?.detail || x.message, 'crit') } finally { setBusy(false) } }
  return <Card title="Create incident"><form onSubmit={submit} className="grid gap-3 sm:grid-cols-2">
    {['title', 'service', 'error', 'impact'].map(k => <input key={k} required={k !== 'impact'} className={inp} placeholder={k[0].toUpperCase() + k.slice(1)} value={f[k]} onChange={e => setF({ ...f, [k]: e.target.value })} />)}
    <textarea className={`${inp} sm:col-span-2`} placeholder="Symptoms" value={f.symptoms} onChange={e => setF({ ...f, symptoms: e.target.value })} />
    <select className={inp} value={f.environment} onChange={e => setF({ ...f, environment: e.target.value })}>{['production', 'staging', 'development'].map(x => <option key={x}>{x}</option>)}</select>
    <select className={inp} value={f.severity} onChange={e => setF({ ...f, severity: e.target.value })}>{['low', 'medium', 'high', 'critical'].map(x => <option key={x}>{x}</option>)}</select>
    <div className="flex gap-2 sm:col-span-2"><button disabled={busy} className="rounded bg-ok px-3 py-1.5 text-sm font-medium text-black">{busy ? 'Analyzing…' : 'Analyze incident'}</button><button type="button" onClick={onClose} className="rounded border border-line px-3 py-1.5 text-sm">Cancel</button></div></form></Card>
}

export function Incidents() {
  const [show, setShow] = useState(false), s = useAsync(api.incidents, [])
  return <div className="space-y-4">
    <div className="flex justify-end"><button onClick={() => setShow(!show)} className="inline-flex items-center gap-1 rounded bg-ok px-3 py-1.5 text-sm font-medium text-black"><Plus size={14} />Create Incident</button></div>
    {show && <IncidentForm onClose={() => setShow(false)} />}
    <Async state={s} empty={d => !d.length} emptyMsg="No incidents yet.">{d => <IncidentTable rows={d} />}</Async></div>
}

export function IncidentDetail() {
  const { id } = useParams(), { state } = useLocation(), [resolved, setResolved] = useState(false)
  const s = useAsync(() => state?.analysis ? Promise.resolve(state.analysis) : api.incident(id), [id])
  return <Async state={s} emptyMsg="Incident not found.">{i => <div className="space-y-4">
    <Card><div className="flex flex-wrap items-center gap-2"><span className="font-mono text-dim">{i.id}</span><h2 className="font-medium">{t(i.title)}</h2><Badge tone={sevTone(i.severity)}>{t(i.severity)}</Badge><Badge tone="dim">{resolved ? 'resolved' : t(i.status)}</Badge></div>
      <dl className="mt-3 grid gap-1 text-sm sm:grid-cols-2">{[['Service', i.service], ['Environment', i.environment], ['Error', i.error], ['Symptoms', i.symptoms], ['Impact', i.impact], ['Created', fmt(i.created_at)]].map(([k, v]) => <div key={k}><dt className="inline text-dim">{k}: </dt><dd className="inline">{t(v)}</dd></div>)}</dl></Card>
    <div className="grid gap-4 lg:grid-cols-2"><HistoricalMatches matches={i.historical_matches} /><RootCause rc={i.root_cause} /></div>
    <div className="grid gap-4 lg:grid-cols-2"><RecommendedActions steps={i.recommended_actions} /><FailedAttempts items={i.failed_attempts} /></div>
    <WhyRecommendation text={i.explanation} confidence={i.confidence} />
    <DeploymentCorrelation d={i.deployment_correlation} />
    <IncidentTimeline items={i.timeline} />
    {!resolved && !/resolved|closed/i.test(i.status) && <button onClick={() => setResolved(true)} className="rounded border border-ok/50 px-3 py-1.5 text-sm text-ok">Mark resolved</button>}
    {(resolved || /resolved|closed/i.test(i.status)) && <FeedbackPanel incidentId={i.id} />}</div>}</Async>
}
