import { SectionBanner } from "@/components/SectionBanner";
import { IncidentsList } from "@/features/incidents/components/IncidentsList";
import { mockIncidents } from "@/features/incidents/mockIncidents";

export default function Incidents() {
  return (
    <section className="flex flex-col gap-4 w-screen p-4">
      <SectionBanner title="Incidents" />
      <IncidentsList incidents={mockIncidents} />
    </section>
  );
}
