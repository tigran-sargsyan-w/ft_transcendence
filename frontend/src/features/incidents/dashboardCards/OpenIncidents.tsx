import { StatCard } from "@/components/cards/StatCard";
import type { IncidentSummary } from "../type";

interface OpenIncidentsProps {
  incidents: IncidentSummary[];
}

export const OpenIncidents = ({ incidents }: OpenIncidentsProps) => {
  const totalOpenIncidents = incidents.filter(
    (i) => i.status === "open",
  ).length;

  return (
    <StatCard
      title="Open incidents"
      value={totalOpenIncidents}
      description="active"
    />
  );
};
