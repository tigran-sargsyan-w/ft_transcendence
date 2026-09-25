import { useEffect, useState } from 'react'
import { TopologyCanvas } from './features/topology/TopologyCanvas'
import { sampleTopology } from './features/topology/sampleTopology'
import { getBackendHealth } from './services/health'

type BackendStatus = 'loading' | 'healthy' | 'unavailable'

function App() {
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
      <p>Backend status: {backendStatus}</p>

      <h2>Infrastructure topology</h2>
      <p>Sample fixture (React Flow POC).</p>
      <TopologyCanvas topology={sampleTopology} />
    </main>
  )
}

export default App
