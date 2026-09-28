import React from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom'
import './index.css'
import Layout from './components/Layout'
import { ToastProvider } from './components/ui'
import Dashboard from './pages/Dashboard'
import { Incidents, IncidentDetail } from './pages/Incidents'
import Memory from './pages/Memory'
import { Deployments, DeploymentDetail } from './pages/Deployments'
import { Analytics, Chat, Settings } from './pages/Other'

createRoot(document.getElementById('root')).render(
  <BrowserRouter><ToastProvider><Routes>
    <Route element={<Layout />}>
      <Route path="/" element={<Navigate to="/dashboard" replace />} />
      <Route path="/dashboard" element={<Dashboard />} />
      <Route path="/incidents" element={<Incidents />} />
      <Route path="/incidents/:id" element={<IncidentDetail />} />
      <Route path="/memory" element={<Memory />} />
      <Route path="/deployments" element={<Deployments />} />
      <Route path="/deployments/:id" element={<DeploymentDetail />} />
      <Route path="/analytics" element={<Analytics />} />
      <Route path="/chat" element={<Chat />} />
      <Route path="/settings" element={<Settings />} />
    </Route>
  </Routes></ToastProvider></BrowserRouter>
)
