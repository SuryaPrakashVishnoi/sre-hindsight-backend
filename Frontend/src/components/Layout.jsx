import { useState } from 'react'
import { NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { LayoutDashboard, AlertOctagon, Brain, Rocket, BarChart3, MessageSquare, Settings, Menu, Search, Bell, User } from 'lucide-react'
import { api, MOCK } from '../services/api'
import { useAsync } from './ui'

const nav = [['/dashboard', 'Dashboard', LayoutDashboard], ['/incidents', 'Incidents', AlertOctagon], ['/memory', 'Memory', Brain], ['/deployments', 'Deployments', Rocket], ['/analytics', 'Analytics', BarChart3], ['/chat', 'AI Chat', MessageSquare], ['/settings', 'Settings', Settings]]

export function Sidebar({ open, onClose }) {
  return <>
    {open && <div className="fixed inset-0 z-30 bg-black/60 lg:hidden" onClick={onClose} />}
    <aside className={`fixed inset-y-0 left-0 z-40 w-56 border-r border-line bg-bg p-3 transition-transform lg:static lg:translate-x-0 ${open ? '' : '-translate-x-full'}`}>
      <div className="mb-6 flex items-center gap-2 px-2 pt-2"><div className="grid h-8 w-8 place-items-center rounded bg-ok/15 text-ok"><Brain size={18} /></div>
        <div><div className="text-sm font-semibold leading-tight">SRE HINDSIGHT</div><div className="text-xs text-dim">AI incident response</div></div></div>
      <nav className="space-y-1">{nav.map(([to, label, Icon]) => <NavLink key={to} to={to} onClick={onClose}
        className={({ isActive }) => `flex items-center gap-3 rounded px-3 py-2 text-sm ${isActive ? 'bg-panel text-ink' : 'text-dim hover:bg-panel'}`}><Icon size={16} />{label}</NavLink>)}</nav>
    </aside></>
}

export function Topbar({ onMenu }) {
  const { pathname } = useLocation(); const go = useNavigate()
  const h = useAsync(api.health, [])
  const status = MOCK ? ['MOCK MODE', 'text-warn'] : h.loading ? ['Checking…', 'text-dim'] : h.error ? ['Backend offline', 'text-crit'] : ['Operational', 'text-ok']
  const page = nav.find(([p]) => pathname.startsWith(p))?.[1] || 'SRE Hindsight'
  return <header className="flex items-center gap-3 border-b border-line px-4 py-3">
    <button className="lg:hidden" onClick={onMenu} aria-label="Menu"><Menu size={20} /></button>
    <h1 className="text-sm font-medium">{page}</h1>
    <span className={`ml-2 text-xs ${status[1]}`}>● {status[0]}</span>
    <form className="ml-auto hidden items-center gap-2 rounded border border-line px-2 py-1 sm:flex" onSubmit={e => { e.preventDefault(); go(`/memory?q=${encodeURIComponent(e.target.q.value)}`) }}>
      <Search size={14} className="text-dim" /><input name="q" placeholder="Search memory" className="bg-transparent text-sm outline-none" /></form>
    <Bell size={16} className="text-dim" /><User size={16} className="text-dim" />
  </header>
}

export default function Layout() {
  const [open, setOpen] = useState(false)
  return <div className="flex min-h-screen"><Sidebar open={open} onClose={() => setOpen(false)} />
    <div className="min-w-0 flex-1"><Topbar onMenu={() => setOpen(true)} /><main className="mx-auto max-w-6xl p-4 md:p-6"><Outlet /></main></div></div>
}
