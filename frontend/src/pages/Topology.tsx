import { StatCard } from "@/components/cards/StatCard";
import { SectionBanner } from "@/components/SectionBanner";
import { SelectedServiceCard } from "@/features/topology/Cards/SelectedServiceCard";
import { ServicesListCard } from "@/features/topology/Cards/ServicesListCard";
import { TopologyCard } from "@/features/topology/Cards/TopologyCard";

export default function Topology() {
  return (
    <section className="flex flex-col gap-4 w-screen p-4">
      <SectionBanner title="Topology" />
      <div className="flex gap-4">
        <StatCard title="Services" value={5} description="" />
        <StatCard title="Healthy" value={3} description="" tone="success" />
        <StatCard title="Impacted" value={1} description="" tone="warning" />
        <StatCard title="Down" value={1} description="" tone="danger" />
        <StatCard title="Last Scan" value={5} description="" />
      </div>
      <div className="flex gap-4">
        <TopologyCard />
        <SelectedServiceCard />
      </div>
      <ServicesListCard />
    </section>
  );
}
