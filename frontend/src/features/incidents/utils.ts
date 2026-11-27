import type { IncidentSummary, IncidentSeverity } from "./type";

export function formatElapsedSince(
  start: string | Date,
  end?: string | Date | null,
): string {
  const startMs = new Date(start).getTime();
  const endMs = end ? new Date(end).getTime() : Date.now();
  if (Number.isNaN(startMs) || Number.isNaN(endMs)) return "--:--";

  const elapsedMin = Math.max(0, Math.floor((endMs - startMs) / 60000));
  const hh = Math.floor(elapsedMin / 60);
  const mm = elapsedMin % 60;

  return `${String(hh).padStart(2, "0")}h${String(mm).padStart(2, "0")}mn`;
}

export function getRunningIncidents(
  nodesId: string,
  incidents: IncidentSummary[],
): IncidentSummary[] {
  const runningIncidentsOfNode = incidents.filter((i) =>
    i.seedNodeIds.includes(nodesId),
  );

  return runningIncidentsOfNode;
}

export const severityBannerClasses: Record<IncidentSeverity, string> = {
  sev1: "bg-severity-sev1/15 text-severity-sev1 ring-severity-sev1/30",
  sev2: "bg-severity-sev2/15 text-severity-sev2 ring-severity-sev2/30",
  sev3: "bg-severity-sev3/15 text-severity-sev3 ring-severity-sev3/30",
  sev4: "bg-severity-sev4/15 text-severity-sev4 ring-severity-sev4/30",
};
