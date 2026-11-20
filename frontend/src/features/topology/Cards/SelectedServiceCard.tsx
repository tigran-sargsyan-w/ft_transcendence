import { MainCard } from "@/components/cards/MainCard";
import { Button } from "@/components/ui/button";

export const SelectedServiceCard = () => {
  return (
    <MainCard title="Selected service" className="flex-2/5">
      <div>
        <div className="flex justify-between">
          <span>TITLE</span>
          <span>BADGE</span>
        </div>
        <div>
          <span>Join incident room</span>
        </div>
        <div className="grid-cols-2">
          <span>Role</span>
          <span>Info</span>
        </div>
        <div className="flex flex-col">
          <div className="flex justify-between">
            <span>Cricalitty</span>
            <span>0.92</span>
          </div>
          <span>Jauge</span>
        </div>
        <div className="flex flex-col">
          <span>Depends on</span>
          <span>badges</span>
        </div>
        <div className="flex flex-col">
          <span>Udes by</span>
          <span>badges</span>
        </div>
        <div>
          <Button>View logs</Button>
          <Button>Run blast radius</Button>
        </div>
      </div>
    </MainCard>
  );
};
