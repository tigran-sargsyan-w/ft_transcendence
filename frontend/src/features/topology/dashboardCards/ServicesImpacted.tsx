import { StatCard } from "@/components/cards/StatCard";
import type { TopologySnapshot } from "../types";
import { getImpactedServices } from "../utils";

interface ServicesImpactedProps {
  topology: TopologySnapshot;
}

export const ServicesImpacted = ({ topology }: ServicesImpactedProps) => {
  const impactedServices = getImpactedServices(topology);
  const totalImpactedServices = impactedServices.length;

  return (
    <StatCard
      title="Services impacted"
      value={totalImpactedServices}
      description="blast radius"
    />
  );
};
