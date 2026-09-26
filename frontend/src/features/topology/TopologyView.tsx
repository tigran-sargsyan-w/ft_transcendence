import { TopologyCanvas } from './TopologyCanvas'
import type { TopologySnapshot } from './types'

export type TopologyViewState =
  | { status: 'loading' }
  | { status: 'empty' }
  | { status: 'error'; message: string }
  | { status: 'ready'; topology: TopologySnapshot }

type TopologyViewProps = {
  state: TopologyViewState
}

export function TopologyView({ state }: TopologyViewProps) {
  if (state.status === 'loading') {
    return <p>Loading topology…</p>
  }

  if (state.status === 'empty') {
    return <p>No infrastructure nodes to display.</p>
  }

  if (state.status === 'error') {
    return <p>Could not load topology: {state.message}</p>
  }

  if (state.topology.nodes.length === 0) {
    return <p>No infrastructure nodes to display.</p>
  }

  return <TopologyCanvas topology={state.topology} />
}
