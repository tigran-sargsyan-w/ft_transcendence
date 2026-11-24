import { IncidentBannerDashboard } from "./components/IncidentBannerDashboard";
import type { IncidentSummary } from "./type";

interface DashboardIncidentsBannersProps {
  incidents: IncidentSummary[];
}

export const DashboardIncidentsBanners = ({
  incidents,
}: DashboardIncidentsBannersProps) => {
  return (
    <div className="flex flex-col gap-2">
      {incidents
        .filter((i) => i.severity === "sev1" && i.status !== "resolved")
        .map((i) => {
          return <IncidentBannerDashboard key={i.id} incident={i} />;
        })}
    </div>
  );
};
