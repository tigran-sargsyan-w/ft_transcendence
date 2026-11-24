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

// Full class names so that Tailwind generates them.
const statusClasses: Record<NodeStatus, string> = {
  healthy: "text-health-healthy",
  degraded: "text-health-degraded",
  down: "text-health-down",
  unknown: "text-health-unknown",
  compromised: "text-health-compromised",
};

function ServiceNodeComponent({ data, selected }: NodeProps<ServiceFlowNode>) {
  return (
    <div
      className={cn(
        "min-w-36 rounded-lg border bg-card px-3 py-2.5 text-xs text-card-foreground shadow-sm",
        selected && "ring-2 ring-primary",
      )}
    >
      <Handle type="target" position={Position.Left} />
      <div className="mb-1 font-semibold">{data.label}</div>
      <div className="text-muted-foreground">{data.kind}</div>
      <div className={cn("mt-1.5", statusClasses[data.status])}>
        {data.status}
      </div>
      <Handle type="source" position={Position.Right} />
    </div>
  );
}

export const ServiceNode = memo(ServiceNodeComponent);
