import { Card, CardContent } from "@/components/ui/card";
import type { IncidentSummary } from "../type";
import { Badge } from "@/components/ui/badge";
import { formatElapsedSince } from "../utils";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface IncidentBannerDashboardProps {
  incident: IncidentSummary;
}

export const IncidentBannerDashboard = ({
  incident,
}: IncidentBannerDashboardProps) => {
  return (
    <Card className="bg-severity-sev1/15 text-severity-sev1 ring-severity-sev1/30">
      <CardContent
        className={cn(
          "grid items-center gap-x-4 gap-y-2",
          "grid-cols-[1fr_auto] [grid-template-areas:'indicator_action'_'text_text']",
          "md:grid-cols-[auto_1fr_auto] md:[grid-template-areas:'indicator_text_action']",
        )}
      >
        <div className="flex items-center gap-2 [grid-area:indicator]">
          <span
            aria-hidden="true"
            className="size-2.5 rounded-full bg-current"
          />
          <Badge variant={incident.severity}>
            {incident.severity.toUpperCase()}
          </Badge>
        </div>
        <div className="flex min-w-0 items-baseline gap-3 [grid-area:text] md:items-center">
          <strong className="min-w-0 flex-1 truncate font-semibold text-foreground">
            {incident.title}
          </strong>
          <div className="flex shrink-0 flex-col items-end text-xs md:flex-row md:items-center md:gap-2">
            <span>{incident.status}</span>
            <span aria-hidden="true" className="hidden md:inline">
              ·
            </span>
            <span>
              {formatElapsedSince(incident.startedAt, incident.resolvedAt)}
            </span>
          </div>
        </div>
        <Button className="cursor-pointer [grid-area:action]">
          Join the incident room
        </Button>
      </CardContent>
    </Card>
  );
};
