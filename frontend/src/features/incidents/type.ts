export type IncidentSeverity = "sev1" | "sev2" | "sev3" | "sev4";

export type IncidentStatus =
  | "open"
  | "investigating"
  | "mitigated"
  | "resolved";

export interface IncidentSummary {
  id: string;
  title: string;
  severity: IncidentSeverity;
  status: IncidentStatus;
  environmentId: string;
  startedAt: string;
  resolvedAt: string | null;
  commander: UserRef | null;
  seedNodeIds: string[]; // services at the origin of the incident
}

export interface UserRef {
  id: string;
  displayName: string;
  avatarUrl: string | null;
}
