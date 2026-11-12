import { TeamOnlineCard } from "@/features/team/TeamOnlineCard";
import { DashboardIncidentsBanners } from "@/features/incidents/DashboardIncidentsBanners";
import { ActiveIncidents } from "@/features/incidents/dashboardCards/ActiveIncidents";
import { OpenIncidents } from "@/features/incidents/dashboardCards/OpenIncidents";
import { ResolvedIncidents } from "@/features/incidents/dashboardCards/ResolvedIncidents";
import { CriticalNodes } from "@/features/topology/dashboardCards/CriticalNodes";
import { ServicesDown } from "@/features/topology/dashboardCards/ServicesDown";
import { ServicesHealth } from "@/features/topology/dashboardCards/ServicesHealth";
import { ServicesImpacted } from "@/features/topology/dashboardCards/ServicesImpacted";
import { mockIncidents } from "@/features/incidents/mockIncidents";
import { WelcomeDashboardBanner } from "@/components/WelcomeDashboardBanner";
import { mockCriticalNodes } from "@/features/topology/mockCriticalNodes";
import { mockTeam } from "@/features/team/mockTeam";

export default function Dashboard() {
  return (
    <div className="flex flex-col gap-4 w-screen p-4">
      <WelcomeDashboardBanner />
      <DashboardIncidentsBanners incidents={mockIncidents} />
      <div className="flex gap-4">
        <OpenIncidents /> <ServicesDown /> <ServicesImpacted />{" "}
        <ResolvedIncidents />
      </div>
      <div className="flex gap-4">
        <div className="flex flex-col gap-4 w-3/5">
          <ActiveIncidents incidents={mockIncidents} />
          <ServicesHealth />
        </div>
        <div className="flex flex-col gap-4 w-2/5">
          <CriticalNodes criticalNodes={mockCriticalNodes} />
          <TeamOnlineCard team={mockTeam} />
        </div>
      </div>
    </div>
  );
}
