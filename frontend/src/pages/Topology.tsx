import { StatCard } from "@/components/cards/StatCard";
import { toneClasses } from "@/components/cards/statTones";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";
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
  const stats = [
    { title: "Services", value: totalServices, tone: "default" },
    { title: "Healthy", value: totalHealthyServices, tone: "success" },
    { title: "Impacted", value: totalImpactedServices, tone: "warning" },
    { title: "Down", value: totalDownServices, tone: "danger" },
    { title: "Last Scan", value: 5, tone: "default" },
  ] as const;

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
      <Card className="py-3 md:hidden">
        <CardContent className="grid grid-cols-5 divide-x divide-border text-center">
          {stats.map((stat) => (
            <div key={stat.title} className="flex flex-col gap-0.5 px-1">
              <span className={cn("text-lg font-bold", toneClasses[stat.tone])}>
                {stat.value}
              </span>
              <span className="text-[10px] uppercase text-muted-foreground">
                {stat.title}
              </span>
            </div>
          ))}
        </CardContent>
      </Card>
      <div className="hidden md:flex gap-4">
        {stats.map((stat) => (
          <StatCard key={stat.title} {...stat} description="" />
        ))}
      </div>
      <div className="flex flex-col md:flex-row gap-4">
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
