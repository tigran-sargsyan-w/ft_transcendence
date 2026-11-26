import { useEffect, useMemo } from "react";
import {
  Background,
  Controls,
  ReactFlow,
  useEdgesState,
  useNodesState,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { ServiceNode } from "./ServiceNode";
import { topologyToFlowElements } from "./topologyToFlowElements";
import type { TopologySnapshot } from "./types";

const nodeTypes = {
  service: ServiceNode,
};

type TopologyCanvasProps = {
  topology: TopologySnapshot;
  onNodeClick?: (nodeId: string) => void;
};

export function TopologyCanvas({ topology, onNodeClick }: TopologyCanvasProps) {
  const initial = useMemo(() => topologyToFlowElements(topology), [topology]);
  const [nodes, setNodes, onNodesChange] = useNodesState(initial.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initial.edges);

  useEffect(() => {
    // Replace from topology snapshot ids -> avoids duplicate nodes/edges on refresh
    setNodes(initial.nodes);
    setEdges(initial.edges);
  }, [initial, setNodes, setEdges]);

  return (
    <div style={{ width: "100%", height: 420 }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        colorMode="dark"
        fitView
        fitViewOptions={{ padding: 0.15, maxZoom: 1 }}
        nodesConnectable={false}
        onNodeClick={(_event, node) => onNodeClick?.(node.id)}
      >
        <Background gap={20} size={1} color="var(--border)" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
