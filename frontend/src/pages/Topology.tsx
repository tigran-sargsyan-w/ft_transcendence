import { StatCard } from "@/components/cards/StatCard";
import { SectionBanner } from "@/components/SectionBanner";
import { SelectedServiceCard } from "@/features/topology/Cards/SelectedServiceCard";
import { ServicesListCard } from "@/features/topology/Cards/ServicesListCard";
import { TopologyCard } from "@/features/topology/Cards/TopologyCard";
import { mockTopology } from "@/features/topology/mockTopology";
import {
  getCriticality,
  getDownServices,
  getHealthyServices,
  getImpactedServices,
  getServices,
} from "@/features/topology/utils";
import { mockCriticalNodes } from "@/features/topology/mockCriticalNodes";
import { useState } from "react";
import type { GraphNode } from "@/features/topology/types";

export default function Topology() {
  const services = getServices(mockTopology.nodes);
  const totalServices = services.length;
  const totalHealthyServices = getHealthyServices.length;
  const totalImpactedServices = getImpactedServices.length;
  const totalDownServices = getDownServices.length;

  const [selectedService, setSelectedService] = useState<GraphNode | undefined>(
    undefined,
  );

  return (
    <section className="flex flex-col gap-4 w-screen p-4">
      <SectionBanner title="Topology" />
      <div className="flex gap-4">
        <StatCard title="Services" value={totalServices} description="" />
        <StatCard
          title="Healthy"
          value={totalHealthyServices}
          description=""
          tone="success"
        />
        <StatCard
          title="Impacted"
          value={totalImpactedServices}
          description=""
          tone="warning"
        />
        <StatCard
          title="Down"
          value={totalDownServices}
          description=""
          tone="danger"
        />
        <StatCard title="Last Scan" value={5} description="" />
      </div>
      <div className="flex gap-4">
        <TopologyCard />
        <SelectedServiceCard
          service={selectedService}
          criticality={
            selectedService
              ? getCriticality(mockCriticalNodes, selectedService?.id)
              : undefined
          }
        />
      </div>
      <ServicesListCard
        services={services}
        criticalNodes={mockCriticalNodes}
        onServiceClick={(service: GraphNode) => setSelectedService(service)}
      />
    </section>
  );
}
