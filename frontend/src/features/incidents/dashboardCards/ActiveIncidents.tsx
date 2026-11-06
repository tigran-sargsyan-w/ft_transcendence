import { MainCard } from "@/components/cards/MainCard";

export const ActiveIncidents = () => {
  return (
    <MainCard
      title="Active incidents"
      linkLabel="View all incidents"
      linkTarget="/incidents"
    >
      <div className="grid grid-cols-5"></div>
    </MainCard>
  );
};
