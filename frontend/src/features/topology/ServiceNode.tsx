import { memo } from "react";
import { Handle, Position, type Node, type NodeProps } from "@xyflow/react";

import { cn } from "@/lib/utils";
import type { NodeKind, NodeStatus } from "./types";

export type ServiceNodeData = {
  label: string;
  kind: NodeKind;
  status: NodeStatus;
};

export type ServiceFlowNode = Node<ServiceNodeData, "service">;

const statusStyles: Record<NodeStatus, { border: string; dot: string }> = {
  healthy: { border: "border-l-health-healthy", dot: "bg-health-healthy" },
  degraded: { border: "border-l-health-degraded", dot: "bg-health-degraded" },
  down: { border: "border-l-health-down", dot: "bg-health-down" },
  unknown: { border: "border-l-health-unknown", dot: "bg-health-unknown" },
  compromised: {
    border: "border-l-health-compromised",
    dot: "bg-health-compromised",
  },
};

function ServiceNodeComponent({ data, selected }: NodeProps<ServiceFlowNode>) {
  const styles = statusStyles[data.status];

  return (
    <div
      className={cn(
        "min-w-36 rounded-md border border-l-4 border-border bg-muted px-3 py-2 text-xs text-card-foreground shadow-md",
        styles.border,
        selected && "ring-2 ring-primary",
      )}
    >
      <Handle
        type="target"
        position={Position.Top}
        className="opacity-0!"
        isConnectable={false}
      />
      <div className="flex items-center gap-2 font-semibold">
        <span className={cn("size-2 shrink-0 rounded-full", styles.dot)} />
        {data.label}
      </div>
      <div className="mt-0.5 text-muted-foreground">{data.status}</div>
      <Handle
        type="source"
        position={Position.Bottom}
        className="opacity-0!"
        isConnectable={false}
      />
    </div>
  );
}

export const ServiceNode = memo(ServiceNodeComponent);
