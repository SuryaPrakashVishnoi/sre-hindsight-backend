import { Link, useParams } from 'react-router-dom'
import { api } from '../services/api'
import { Card, Async, useAsync, Badge, t, fmt } from '../components/ui'
import { IncidentTimeline, RollbackModal, RollbackResult, useRollback } from '../components/incident'

export function Deployments() {
  const s = useAsync(api.deployments, [])
  return <Async state={s} empty={d => !d.length} emptyMsg="No recent deployments.">{d =>
    <div className="overflow-x-auto rounded-lg border border-line bg-panel"><table className="w-full min-w-[760px] text-left text-sm">
      <thead className="border-b border-line text-dim"><tr>{['Deployment', 'Version', 'Service', 'Environment', 'Commit', 'PR', 'Time', 'Status', 'Incident'].map(h => <th key={h} className="px-3 py-2 font-normal">{h}</th>)}</tr></thead>
      <tbody>{d.map(x => <tr key={x.id} className="border-b border-line/60 last:border-0"><td className="px-3 py-2 font-mono"><Link className="text-info" to={`/deployments/${x.id}`}>{x.id}</Link></td>
        <td className="px-3 py-2">{t(x.version)}</td><td className="px-3 py-2">{t(x.service)}</td><td className="px-3 py-2">{t(x.environment)}</td><td className="px-3 py-2 font-mono">{t(x.commit)}</td>
        <td className="px-3 py-2">{x.pr_number ? `#${x.pr_number}` : 'Not available'}</td><td className="px-3 py-2">{fmt(x.deployed_at)}</td><td className="px-3 py-2">{t(x.status)}</td>
        <td className="px-3 py-2">{x.related_incidents?.length ? <Badge tone="warn">{x.related_incidents.join(', ')}</Badge> : 'None'}</td></tr>)}</tbody></table></div>}</Async>
}

export function DeploymentDetail() {
  const { id } = useParams(), s = useAsync(() => api.deployment(id), [id]), rb = useRollback(id)
  return <Async state={s} emptyMsg="Deployment not found.">{d => <div className="space-y-4">
    <Card title={`Deployment ${t(d.version)}`}><dl className="grid gap-1 text-sm sm:grid-cols-2">{[['Status', d.status], ['Environment', d.environment], ['Commit', d.commit], ['Pull Request', d.pr_number ? `#${d.pr_number}` : null], ['Changes', d.change], ['Deployed', fmt(d.deployed_at)]].map(([k, v]) => <div key={k}><dt className="inline text-dim">{k}: </dt><dd className="inline">{t(v)}</dd></div>)}</dl>
      <p className="mt-3 text-sm"><span className="text-dim">Related incidents: </span>{d.related_incidents?.length ? d.related_incidents.map(i => <Link key={i} to={`/incidents/${i}`} className="mr-2 font-mono text-info">{i}</Link>) : 'None'}</p>
      {d.pr_url && <a href={d.pr_url} target="_blank" rel="noreferrer" className="mt-3 inline-block text-sm text-info">View PR</a>}
      {d.rollback_available && <div className="mt-3"><button onClick={() => rb.setOpen(true)} className="rounded border border-crit/50 px-3 py-1.5 text-sm text-crit">Roll Back Deployment</button></div>}
      <RollbackResult r={rb.result} /><RollbackModal rb={rb} d={{ version: d.version, pr_number: d.pr_number }} /></Card>
    <IncidentTimeline items={d.timeline} /></div>}</Async>
}
