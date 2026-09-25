import { useEffect, useState } from 'react'
import { TopologyView, type TopologyViewState } from './features/topology/TopologyView'
import { sampleTopology } from './features/topology/sampleTopology'
import { getBackendHealth } from './services/health'

type BackendStatus = 'loading' | 'healthy' | 'unavailable'

function App() {
  const [backendStatus, setBackendStatus] =
    useState<BackendStatus>('loading')
  const [topologyState, setTopologyState] = useState<TopologyViewState>({
    status: 'loading',
  })

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

  useEffect(() => {
    // Boundary for a future Nest/Socket.IO topology source
    // For now -> we resolve the local fixture asynchronously
    let cancelled = false

    async function loadTopology() {
      setTopologyState({ status: 'loading' })
      try {
        await new Promise((resolve) => setTimeout(resolve, 200))
        if (cancelled) return

        if (sampleTopology.nodes.length === 0) {
          setTopologyState({ status: 'empty' })
          return
        }

        setTopologyState({ status: 'ready', topology: sampleTopology })
      } catch (error) {
        if (cancelled) return
        const message =
          error instanceof Error ? error.message : 'Unknown topology error'
        setTopologyState({ status: 'error', message })
      }
    }

    void loadTopology()
    return () => {
      cancelled = true
    }
  }, [])

  return (
    <main>
      <h1>Infrastructure Intelligence Platform</h1>
      <p>Backend status: {backendStatus}</p>

      <h2>Infrastructure topology</h2>
      <TopologyView state={topologyState} />
    </main>
  )
}

export default App
