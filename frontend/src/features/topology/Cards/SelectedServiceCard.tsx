import { MainCard } from "@/components/cards/MainCard";
import { Button } from "@/components/ui/button";
import type { GraphNode } from "../types";
import { Badge } from "@/components/ui/badge";
import { getServiceDetails } from "../utils";
import { formateDate } from "@/lib/date";
import { Progress } from "@/components/ui/progress";
import { getCriticalityLevel, criticalityStyles } from "../criticaly";

interface SelectedServiceCardProps {
  service: GraphNode | undefined;
  criticality: number | undefined;
}

export const SelectedServiceCard = ({
  service,
  criticality,
}: SelectedServiceCardProps) => {
  if (!service) {
    return (
      <MainCard title="Selected service" className="flex-2/5">
        <p className="text-muted-foreground">No service selected</p>
      </MainCard>
    );
  }

  const details = getServiceDetails(service);
  const level = criticality && getCriticalityLevel(criticality);
  const style = level ? criticalityStyles[level] : 0;

  return (
    <MainCard title="Selected service" className="flex-2/5">
      <div className="flex flex-col gap-2">
        <div className="flex justify-between">
          <span className="text-xl font-bold">{details.name}</span>
          <Badge variant={details.status} className="uppercase">
            {details.status}
          </Badge>
        </div>
        <div>
          <span>Join incident room</span>
        </div>
        <div className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2">
          <span className="text-muted-foreground">Role</span>
          <span>{details.role}</span>
          <span className="text-muted-foreground">Image</span>
          <span>{details.image}</span>
          <span className="text-muted-foreground">Environment</span>
          <span>environement_name</span>
          <span className="text-muted-foreground">Last event</span>
          <span>
            {formateDate(details.lastEvent.at)} : {details.lastEvent.type}
          </span>
        </div>
        <div className="flex flex-col">
          <div className="flex justify-between">
            <span className="text-muted-foreground">Cricalitty</span>
            <span>{criticality?.toFixed(2)}</span>
          </div>
          <Progress
            value={criticality ? criticality * 100 : undefined}
            className={style ? `${style.bar}` : undefined}
          />
        </div>
        <div className="flex flex-col">
          <span className="font-semibold">Depends on</span>
          <span>badges</span>
        </div>
        <div className="flex flex-col">
          <span>Used by</span>
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
