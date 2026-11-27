import { SectionBanner } from "@/components/SectionBanner";
import { IncidentsList } from "@/features/incidents/components/IncidentsList";
import { IncidentRoom } from "@/features/incidents/IncidentRoom";
import { mockIncidents } from "@/features/incidents/mockIncidents";
import type { IncidentSummary } from "@/features/incidents/type";
import { useState } from "react";

export default function Incidents() {
  const [selectedIncident, setSelectedIncident] =
    useState<IncidentSummary | null>(null);

  return (
    <section className="flex flex-col gap-4 w-screen p-4">
      <SectionBanner
        title={selectedIncident ? selectedIncident.title : "Incidents"}
      />
      {selectedIncident ? (
        <IncidentRoom
          incident={selectedIncident}
          onBack={() => setSelectedIncident(null)}
        />
      ) : (
        <IncidentsList
          incidents={mockIncidents}
          onIncidentClick={(incident: IncidentSummary) =>
            setSelectedIncident(incident)
          }
        />
      )}
    </section>
  );
}
