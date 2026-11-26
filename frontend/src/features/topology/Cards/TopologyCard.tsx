import { MainCard } from "@/components/cards/MainCard";
import { TopologyCanvas } from "../TopologyCanvas";
import type { TopologySnapshot } from "../types";

interface TopologyCardProps {
  topology: TopologySnapshot;
  selectedId?: string;
  onNodeClick: (nodeId: string) => void;
  onPaneClick: () => void;
}

export const TopologyCard = ({
  topology,
  selectedId,
  onNodeClick,
  onPaneClick,
}: TopologyCardProps) => {
  return (
    <MainCard title="Environment name" className="flex-3/5">
      <TopologyCanvas
        topology={topology}
        selectedId={selectedId}
        onNodeClick={onNodeClick}
        onPaneClick={onPaneClick}
      />
    </MainCard>
  );
};
