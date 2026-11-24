import { MainCard } from "@/components/cards/MainCard";
import { TopologyCanvas } from "../TopologyCanvas";
import type { TopologySnapshot } from "../types";

interface TopologyCardProps {
  topology: TopologySnapshot;
  onNodeClick: (nodeId: string) => void;
}

export const TopologyCard = ({ topology, onNodeClick }: TopologyCardProps) => {
  return (
    <MainCard title="Environment name" className="flex-3/5">
      <TopologyCanvas topology={topology} onNodeClick={onNodeClick} />
    </MainCard>
  );
};
