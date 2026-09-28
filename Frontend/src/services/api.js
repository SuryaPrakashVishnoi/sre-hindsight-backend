import axios from 'axios'
import { mock } from './mock'
export const API_URL = import.meta.env.VITE_API_URL || 'http://localhost:8000'
export const MOCK = import.meta.env.VITE_USE_MOCK_DATA === 'true'
const http = axios.create({ baseURL: API_URL, timeout: 30000 })
// name: mock handler; m/u/d: real HTTP method, url, payload/params; a: mock arg override
const call = (name, m, u, d, a) => MOCK
  ? Promise.resolve(mock[name](a ?? d))
  : http({ method: m, url: u, ...(m === 'get' ? { params: d } : { data: d }) }).then(r => r.data)
const list = x => Array.isArray(x) ? x : x?.items || x?.incidents || x?.deployments || x?.results || []
const norm = i => i && { ...i, id: i.id || i.incident_id }
export const api = {
  health: () => call('health', 'get', '/api/health'),
  analyze: b => call('analyze', 'post', '/api/analyze', b).then(norm),
  feedback: b => call('feedback', 'post', '/api/feedback', b),
  incidents: () => call('incidents', 'get', '/api/incidents').then(x => list(x).map(norm)),
  incident: id => call('incident', 'get', `/api/incidents/${id}`, undefined, id).then(norm),
  search: q => call('search', 'get', '/api/memory/search', { q }).then(x => list(x).map(norm)),
  memory: id => call('memory', 'get', `/api/memory/${id}`, undefined, id).then(norm),
  deployments: () => call('deployments', 'get', '/api/deployments/recent').then(list),
  deployment: id => call('deployment', 'get', `/api/deployments/${id}`, undefined, id),
  rollback: id => call('rollback', 'post', `/api/deployments/${id}/rollback`, undefined, id),
}
