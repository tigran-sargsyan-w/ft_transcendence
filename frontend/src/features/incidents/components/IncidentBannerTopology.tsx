import { Card, CardContent } from "@/components/ui/card";
import type { IncidentSummary } from "../type";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { severityBannerClasses } from "../utils";

interface IncidentBannerTopologyProps {
  incident: IncidentSummary;
}

export const IncidentBannerTopology = ({
  incident,
}: IncidentBannerTopologyProps) => {
  return (
    <Card className={cn(severityBannerClasses[incident.severity], "p-2")}>
      <CardContent className="flex justify-between items-center">
        <span className="uppercase">
          {incident.id} - {incident.severity} - {incident.status}
        </span>
        <Button className="cursor-pointer">Join the incident room</Button>
      </CardContent>
    </Card>
  );
};
