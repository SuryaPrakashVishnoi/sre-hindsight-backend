import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import { api } from '../services/api'
import { Async, useAsync } from '../components/ui'
import { MemoryCard } from '../components/incident'

export default function Memory() {
  const [sp, setSp] = useSearchParams(), q = sp.get('q') || '', [draft, setDraft] = useState(q)
  useEffect(() => setDraft(q), [q])
  const s = useAsync(() => api.search(q), [q])
  return <div className="space-y-4">
    <form onSubmit={e => { e.preventDefault(); setSp(draft ? { q: draft } : {}) }} className="flex gap-2">
      <input value={draft} onChange={e => setDraft(e.target.value)} placeholder="Search by incident, error, service, root cause or keyword" className="flex-1 rounded border border-line bg-panel px-3 py-2 text-sm" />
      <button className="rounded bg-info px-4 text-sm font-medium text-black">Search</button></form>
    <Async state={s} empty={d => !d.length} emptyMsg="No relevant information found.">{d => <div className="space-y-3">{d.map(m => <MemoryCard key={m.id} m={m} />)}</div>}</Async></div>
}
