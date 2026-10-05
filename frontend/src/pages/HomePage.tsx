import { useEffect, useState } from 'react'
import { Link } from 'react-router'
import { getBackendHealth } from '../services/health'

type BackendStatus = 'loading' | 'healthy' | 'unavailable'

export function HomePage() {
  const [backendStatus, setBackendStatus] =
    useState<BackendStatus>('loading')

  useEffect(() => {
    async function checkBackendHealth() {
      try {
        await getBackendHealth()
        setBackendStatus('healthy')
      } catch {
        setBackendStatus('unavailable')
      }
    }

    void checkBackendHealth()
  }, [])

  return (
    <main>
      <h1>Infrastructure Intelligence Platform</h1>
      <p>Frontend is running.</p>

      <p>Backend status: {backendStatus}</p>

      <p>
        <Link to="/register">Create account</Link>
      </p>
    </main>
  )
}
