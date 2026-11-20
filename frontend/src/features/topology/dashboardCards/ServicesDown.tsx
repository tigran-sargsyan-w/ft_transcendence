import { StatCard } from "@/components/cards/StatCard";
import { countByStatus, getServices } from "../utils";
import type { TopologySnapshot } from "../types";

interface ServicesDownProps {
  topology: TopologySnapshot;
}

export const ServicesDown = ({ topology }: ServicesDownProps) => {
  const services = getServices(topology.nodes);
  const counts = countByStatus(services);

  return (
    <StatCard
      title="Services down"
      value={counts.down}
      description={`of ${services.length}`}
    />
  );
};
