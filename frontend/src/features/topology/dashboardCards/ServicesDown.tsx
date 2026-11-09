import { StatCard } from "@/components/cards/StatCard";
import { mockTopology } from "../mockTopology";
import { countByStatus, getServices } from "../utils";

export const ServicesDown = () => {
  const services = getServices(mockTopology.nodes);
  const counts = countByStatus(services);

  return (
    <StatCard
      title="Services down"
      value={counts.down}
      description={`of ${services.length}`}
    />
  );
};
