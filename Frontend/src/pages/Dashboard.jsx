import { Link } from 'react-router-dom'
import { api } from '../services/api'
import { Card, StatCard, LoadingState, ErrorState, EmptyState, useAsync, Badge, sevTone, t, fmt } from '../components/ui'

export const computeStats = list => {
  const res = list.filter(i => /resolved|closed/i.test(i.status)), times = res.map(i => i.resolution_minutes).filter(Number.isFinite)
  const matched = list.filter(i => i.historical_matches?.length)
  return { active: list.length - res.length, resolved: res.length, matches: list.reduce((n, i) => n + (i.historical_matches?.length || 0), 0),
    rollbacks: list.filter(i => i.rollback).length, avg: times.length ? Math.round(times.reduce((a, b) => a + b) / times.length) : null,
    reuse: matched.length, good: list.filter(i => i.feedback?.useful === true || i.feedback?.rating >= 4).length,
    prevented: matched.reduce((n, i) => n + (i.failed_attempts?.length || 0), 0), correlated: list.filter(i => i.deployment_correlation).length }
}

export default function Dashboard() {
  const inc = useAsync(api.incidents, []), dep = useAsync(api.deployments, [])
  if (inc.loading) return <LoadingState rows={6} />
  if (inc.error) return <ErrorState error={inc.error} onRetry={inc.reload} />
  const list = inc.data, s = computeStats(list)
  if (!list.length) return <EmptyState msg="No analytics data available yet." />
  const cards = [['Active Incidents', s.active, 'crit'], ['Resolved Incidents', s.resolved, 'ok'], ['Historical Matches', s.matches, 'info'], ['Rollbacks', s.rollbacks, 'warn'], ['Avg Resolution Time', s.avg != null ? `${s.avg} min` : 'N/A', 'info'], ['Memory Reuse', s.reuse, 'ok'], ['Successful Recommendations', s.good, 'ok'], ['Failed Attempts Prevented', s.prevented, 'warn']]
  const active = list.filter(i => !/resolved|closed/i.test(i.status))
  return <div className="space-y-4">
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{cards.map(([l, v, tone]) => <StatCard key={l} label={l} value={v} tone={tone} />)}</div>
    <div className="grid gap-4 lg:grid-cols-2">
      <Card title="Active incidents">{active.length ? active.map(i => <Link key={i.id} to={`/incidents/${i.id}`} className="flex items-center gap-2 py-1 text-sm hover:text-info"><Badge tone={sevTone(i.severity)}>{t(i.severity)}</Badge><span className="font-mono">{i.id}</span>{i.title}</Link>) : <EmptyState msg="No active incidents." />}</Card>
      <Card title="Recent deployments">{dep.loading ? <LoadingState rows={2} /> : dep.error ? <ErrorState error={dep.error} onRetry={dep.reload} /> : dep.data.length ? dep.data.slice(0, 5).map(d => <Link key={d.id} to={`/deployments/${d.id}`} className="flex gap-2 py-1 text-sm hover:text-info"><span className="font-mono">{t(d.version)}</span>{t(d.service)}<span className="ml-auto text-dim">{t(d.status)}</span></Link>) : <EmptyState msg="No recent deployments." />}</Card>
      <Card title="Recent incidents">{list.slice(0, 5).map(i => <div key={i.id} className="py-1 text-sm"><span className="font-mono text-dim">{i.id}</span> {t(i.title)} <span className="text-dim">{fmt(i.created_at)}</span></div>)}</Card>
      <Card title="Memory insights"><p className="text-sm">{s.reuse} of {list.length} incidents reused prior experience.</p><p className="text-sm">{s.correlated} incidents had a deployment correlation.</p><p className="text-sm">{s.prevented} previous failed attempts surfaced before repeating them.</p></Card>
    </div></div>
}
