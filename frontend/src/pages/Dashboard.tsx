import { ActiveIncidents } from "@/features/incidents/dashboardCards/ActiveIncidents";
import { OpenIncidents } from "@/features/incidents/dashboardCards/OpenIncidents";
import { ResolvedIncidents } from "@/features/incidents/dashboardCards/ResolvedIncidents";
import { ServicesDown } from "@/features/topology/dashboardCards/ServicesDown";
import { ServicesImpacted } from "@/features/topology/dashboardCards/ServicesImpacted";

export default function Dashboard() {
  return (
    <div className="flex flex-col gap-4 w-screen p-4">
      <div className="flex gap-4">
        <OpenIncidents /> <ServicesDown /> <ServicesImpacted />{" "}
        <ResolvedIncidents />
      </div>
      <div className="flex gap-4">
        <div className="flex flex-col gap-4 w-3/5">
          <ActiveIncidents />
          <OpenIncidents />
        </div>
        <div className="flex flex-col gap-4 w-2/5">
          <OpenIncidents />
          <OpenIncidents />
        </div>
      </div>
    </div>
  );
}
