import type { VariantProps } from "class-variance-authority";
import type { badgeVariants } from "@/components/ui/badge";

export type CriticalityLevel = "critical" | "high" | "moderate" | "low";
type KnownReason =
  | "high_betweenness"
  | "many_dependents"
  | "exposed_to_internet"
  | "structural_role"
  | "single_node";

const reasonLabels: Record<KnownReason, string> = {
  high_betweenness: "High betweenness",
  many_dependents: "Many dependents",
  exposed_to_internet: "Exposed to internet",
  structural_role: "Structural role",
  single_node: "Single node",
};

type BadgeVariant = NonNullable<VariantProps<typeof badgeVariants>["variant"]>;

export const criticalityStyles: Record<
  CriticalityLevel,
  { text: string; bar: string; badge: BadgeVariant }
> = {
  critical: {
    text: "text-severity-sev1",
    bar: "bg-severity-sev1/15 [&>[data-slot=progress-indicator]]:bg-severity-sev1",
    badge: "sev1",
  },
  high: {
    text: "text-severity-sev2",
    bar: "bg-severity-sev2/15 [&>[data-slot=progress-indicator]]:bg-severity-sev2",
    badge: "sev2",
  },
  moderate: {
    text: "text-severity-sev3",
    bar: "bg-severity-sev3/15 [&>[data-slot=progress-indicator]]:bg-severity-sev3",
    badge: "sev3",
  },
  low: {
    text: "text-muted-foreground",
    bar: "[&>[dat-slot=progress-indicator]]:bg-muted-foreground",
    badge: "secondary",
  },
};

export function getCriticalityLevel(score: number): CriticalityLevel {
  if (score >= 0.7) return "critical";
  if (score >= 0.4) return "high";
  if (score >= 0.15) return "moderate";
  return "low";
}

export function getReasonLabel(code: string): string {
  return reasonLabels[code as KnownReason] ?? code;
}
