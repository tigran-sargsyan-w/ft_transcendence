import { useEffect, useMemo } from 'react'
import {
  Background,
  Controls,
  MiniMap,
  ReactFlow,
  useEdgesState,
  useNodesState,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'

import { topologyToFlowElements } from './topologyToFlowElements'
import type { TopologySnapshot } from './types'

type TopologyCanvasProps = {
  topology: TopologySnapshot
}

export function TopologyCanvas({ topology }: TopologyCanvasProps) {
  const initial = useMemo(
    () => topologyToFlowElements(topology),
    [topology],
  )
  const [nodes, setNodes, onNodesChange] = useNodesState(initial.nodes)
  const [edges, setEdges, onEdgesChange] = useEdgesState(initial.edges)

  useEffect(() => {
    setNodes(initial.nodes)
    setEdges(initial.edges)
  }, [initial, setNodes, setEdges])

  return (
    <div style={{ width: '100%', height: '70vh', minHeight: 420 }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        fitView
        nodesConnectable={false}
      >
        <Background />
        <Controls />
        <MiniMap />
      </ReactFlow>
    </div>
  )
}
