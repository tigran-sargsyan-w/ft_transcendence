import { IncidentBanner } from "./components/IncidentBanner";
import type { IncidentSummary } from "./type";

interface DashboardBannerProps {
  incidents: IncidentSummary[];
}

export const DashboardBanner = ({ incidents }: DashboardBannerProps) => {
  return (
    <div className="flex flex-col gap-2">
      {incidents
        .filter((i) => i.severity === "sev1" && i.status !== "resolved")
        .map((i) => {
          return <IncidentBanner key={i.id} incident={i} />;
        })}
    </div>
  );
};
