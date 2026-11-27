import { formateDate } from "@/lib/date";
import type { IncidentSummary } from "./type";
import { StatCard } from "@/components/cards/StatCard";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { IncidentTimeline } from "./components/IncidentTimeline";
import { IncidentParticipants } from "./components/IncidentParticipants";
import { Runbook } from "./components/Runbook";
import { IncidentChat } from "./components/IncidentChat";

interface IncidentRoomProps {
  incident: IncidentSummary;
  onBack: () => void;
}

export const IncidentRoom = ({ incident, onBack }: IncidentRoomProps) => {
  const timeSteps = [
    {
      title: "1 - Open",
      value: formateDate(incident.startedAt),
      tone: incident.status === "open" ? "danger" : "default",
    },
    {
      title: "2 - Investigating",
      value: "time investigating",
      tone: incident.status === "investigating" ? "warning" : "default",
    },
    {
      title: "3 - Resolved",
      value: incident.resolvedAt ? incident.resolvedAt : "---",
      tone: "success",
    },
  ] as const;

  return (
    <section className="flex flex-col gap-4 w-full">
      <div className="flex gap-4">
        {timeSteps.map((step) => (
          <StatCard
            title={step.title}
            value={step.value}
            tone={step.tone}
            key={step.title}
          />
        ))}
      </div>
      <div className="flex gap-4 flex-1">
        <div className="flex flex-col flex-1 gap-4">
          <Card>
            <CardHeader>
              <CardTitle>Impact</CardTitle>
            </CardHeader>
          </Card>
          <IncidentTimeline />
        </div>
        <div className="flex flex-col flex-1 gap-4">
          <IncidentParticipants />
          <Runbook />
          <IncidentChat />
        </div>
      </div>
    </section>
  );
};
