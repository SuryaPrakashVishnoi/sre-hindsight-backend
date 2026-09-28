// Development mock data (VITE_USE_MOCK_DATA=true). Never used against a real backend.
const inc001 = { id: 'INC-001', title: 'Classroom Projector Has No Signal', service: 'Classroom Infrastructure', severity: 'low', status: 'resolved', created_at: '2026-09-10T09:00:00Z', error: 'No Signal', symptoms: 'Projector shows blank screen', impact: 'Lecture delayed', environment: 'campus', resolution_minutes: 20,
  historical_matches: [], root_cause: { historical_evidence: 'Damaged HDMI cable', inference: null, unknown: null },
  successful_fix: 'Replaced HDMI cable', failed_attempts: ['Restarted projector', 'Restarted laptop', 'Changed display settings'],
  recommended_actions: ['Replace HDMI cable'], feedback: { rating: 5, useful: true } }
const dep = { id: 'dep-184', version: 'v1.8.4', service: 'Authentication API', environment: 'production', commit: '8f72abc', pr_number: 142, pr_url: null, change: 'Authentication middleware refactor', deployed_at: '2026-09-27T14:27:00Z', status: 'completed', rollback_available: true, related_incidents: ['INC-104'],
  timeline: [{ time: '14:20', label: 'Deployment started' }, { time: '14:27', label: 'Deployment completed' }] }
const inc104 = { id: 'INC-104', title: 'Production API Returning 500 Errors', service: 'Authentication API', severity: 'critical', status: 'open', created_at: '2026-09-27T14:32:00Z', error: 'HTTP 500', symptoms: 'Login requests failing', impact: 'Users cannot sign in', environment: 'production',
  historical_matches: [{ incident_id: 'INC-071', similarity: 0.82, root_cause: 'Middleware rejected valid tokens', successful_fix: 'Rolled back release', failed_attempts: ['Restarted pods'] }],
  root_cause: { historical_evidence: 'INC-071 traced to middleware token validation', inference: 'Recent middleware change may be involved', unknown: 'Whether config differs between environments' },
  recommended_actions: ['Inspect configuration', 'Compare with previous version', 'Check service metrics', 'Review deployment logs', 'Apply corrective action'],
  failed_attempts: ['Restarted pods'], explanation: 'INC-071 had the same 500 pattern after a middleware change.',
  deployment_correlation: { deployment_id: 'dep-184', version: 'v1.8.4', deployed_at: dep.deployed_at, incident_at: '2026-09-27T14:32:00Z', commit: '8f72abc', pr_number: 142, pr_url: null, change: 'Authentication middleware refactor', rollback_available: true },
  timeline: [{ time: '14:20', label: 'Deployment started' }, { time: '14:27', label: 'Deployment completed' }, { time: '14:32', label: 'Incident detected' }, { time: '14:33', label: 'AI analysis' }, { time: '14:34', label: 'Historical memory found' }] }
const all = [inc104, inc001]
export const mock = {
  health: () => ({ status: 'mock' }),
  analyze: () => inc104,
  feedback: () => ({ ok: true }),
  incidents: () => all,
  incident: id => all.find(i => i.id === id) || null,
  search: ({ q }) => all.filter(i => JSON.stringify(i).toLowerCase().includes((q || '').toLowerCase())),
  memory: id => all.find(i => i.id === id),
  deployments: () => [dep],
  deployment: () => dep,
  rollback: id => ({ rollback_id: 'MOCK-RB-001', deployment: id, timestamp: new Date().toISOString(), status: 'simulated', result: 'Simulated only', mock: true }),
}
