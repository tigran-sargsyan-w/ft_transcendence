import { StatCard } from "@/components/cards/StatCard";
import { SectionBanner } from "@/components/SectionBanner";
import { SelectedServiceCard } from "@/features/topology/Cards/SelectedServiceCard";
import { ServicesListCard } from "@/features/topology/Cards/ServicesListCard";
import { TopologyCard } from "@/features/topology/Cards/TopologyCard";
import { mockTopology } from "@/features/topology/mockTopology";
import {
  getCriticality,
  getDependances,
  getDownServices,
  getHealthyServices,
  getImpactedServices,
  getServices,
  getIsUsedBy,
  getServiceGraph,
} from "@/features/topology/utils";
import { mockCriticalNodes } from "@/features/topology/mockCriticalNodes";
import { useState, useMemo } from "react";
import type { GraphNode } from "@/features/topology/types";
import { getRunningIncidents } from "@/features/incidents/utils";
import { mockIncidents } from "@/features/incidents/mockIncidents";

export default function Topology() {
  const services = getServices(mockTopology.nodes);
  const totalServices = services.length;
  const totalHealthyServices = getHealthyServices(mockTopology).length;
  const totalImpactedServices = getImpactedServices(mockTopology).length;
  const totalDownServices = getDownServices(mockTopology).length;

  const [selectedService, setSelectedService] = useState<GraphNode | undefined>(
    undefined,
  );

  const serviceGraph = useMemo(() => getServiceGraph(mockTopology), []);

  function handleNodeClick(nodeId: string) {
    const node = services.find((service) => service.id === nodeId);
    setSelectedService(node);
  }

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
        <TopologyCard
          topology={serviceGraph}
          selectedId={selectedService?.id}
          onNodeClick={handleNodeClick}
          onPaneClick={() => setSelectedService(undefined)}
        />
        <SelectedServiceCard
          service={selectedService}
          criticality={
            selectedService
              ? getCriticality(mockCriticalNodes, selectedService.id)
              : undefined
          }
          dependances={
            selectedService
              ? getDependances(mockTopology, selectedService.id)
              : undefined
          }
          usedBy={
            selectedService
              ? getIsUsedBy(mockTopology, selectedService.id)
              : undefined
          }
          runningIncidents={
            selectedService
              ? getRunningIncidents(selectedService.id, mockIncidents)
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
