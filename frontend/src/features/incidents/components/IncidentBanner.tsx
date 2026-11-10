import { Card, CardContent } from "@/components/ui/card";
import type { IncidentSummary } from "../type";
import { Badge } from "@/components/ui/badge";
import { formatElapsedSince } from "../utils";

interface IncidentBannerProps {
  incident: IncidentSummary;
}

export const IncidentBanner = ({ incident }: IncidentBannerProps) => {
  return (
    <Card className="bg-severity-sev1/15 text-severity-sev1 ring-severity-sev1/30">
      <CardContent className="flex gap-2 items-center">
        <span aria-hidden="true" className="size-2.5 rounded-full bg-current" />
        <Badge variant={incident.severity}>
          {incident.severity.toUpperCase()}
        </Badge>
        <strong className="text-foreground font-semibold">
          {incident.title}
        </strong>
        <span>
          {incident.status} - {formatElapsedSince(incident.startedAt)}
        </span>
      </CardContent>
    </Card>
  );
};
