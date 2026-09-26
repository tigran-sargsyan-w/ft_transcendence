import { memo } from 'react'
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react'

import type { NodeKind, NodeStatus } from './types'

export type ServiceNodeData = {
  label: string
  kind: NodeKind
  status: NodeStatus
}

export type ServiceFlowNode = Node<ServiceNodeData, 'service'>

function statusColor(status: NodeStatus): string {
  switch (status) {
    case 'healthy':
      return '#2f9e44'
    case 'degraded':
      return '#f59f00'
    case 'down':
      return '#e03131'
    case 'compromised':
      return '#9c36b5'
    default:
      return '#868e96'
  }
}

function ServiceNodeComponent({ data }: NodeProps<ServiceFlowNode>) {
  return (
    <div
      style={{
        minWidth: 140,
        padding: '10px 12px',
        borderRadius: 8,
        border: '1px solid #ced4da',
        background: '#fff',
        fontSize: 12,
        boxShadow: '0 1px 2px rgba(0,0,0,0.06)',
      }}
    >
      <Handle type="target" position={Position.Left} />
      <div style={{ fontWeight: 600, marginBottom: 4 }}>{data.label}</div>
      <div style={{ color: '#495057' }}>{data.kind}</div>
      <div style={{ marginTop: 6, color: statusColor(data.status) }}>
        {data.status}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  )
}

export const ServiceNode = memo(ServiceNodeComponent)
