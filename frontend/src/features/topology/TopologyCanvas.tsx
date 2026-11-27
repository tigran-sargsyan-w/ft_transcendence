import { useEffect, useMemo, useSyncExternalStore } from "react";
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

const touchQuery = window.matchMedia("(pointer: coarse)");

function subscribeTouch(onChange: () => void) {
  touchQuery.addEventListener("change", onChange);
  return () => touchQuery.removeEventListener("change", onChange);
}

function getIsTouch() {
  return touchQuery.matches;
}

type TopologyCanvasProps = {
  topology: TopologySnapshot;
  selectedId?: string;
  onNodeClick?: (nodeId: string) => void;
  onPaneClick?: () => void;
};

export function TopologyCanvas({
  topology,
  selectedId,
  onNodeClick,
  onPaneClick,
}: TopologyCanvasProps) {
  // On touch screens, a swipe over the graph scrolls the page instead of panning
  const isTouch = useSyncExternalStore(subscribeTouch, getIsTouch);
  const initial = useMemo(() => topologyToFlowElements(topology), [topology]);
  const [nodes, setNodes, onNodesChange] = useNodesState(initial.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initial.edges);

  useEffect(() => {
    // Replace from topology snapshot ids -> avoids duplicate nodes/edges on refresh
    setNodes(initial.nodes);
    setEdges(initial.edges);
  }, [initial, setNodes, setEdges]);

  useEffect(() => {
    setNodes((current) =>
      current.map((node) => ({ ...node, selected: node.id === selectedId })),
    );
  }, [initial, selectedId, setNodes]);

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
        panOnDrag={!isTouch}
        zoomOnPinch={!isTouch}
        preventScrolling={!isTouch}
        nodesDraggable={false}
        onNodeClick={(_event, node) => onNodeClick?.(node.id)}
        onPaneClick={() => onPaneClick?.()}
      >
        <Background gap={20} size={1} color="var(--border)" />
        <Controls showInteractive={false} />
      </ReactFlow>
    </div>
  );
}
