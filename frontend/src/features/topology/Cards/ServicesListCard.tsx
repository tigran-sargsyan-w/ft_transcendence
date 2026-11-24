import { MainCard } from "@/components/cards/MainCard";
import {
  Table,
  TableHeader,
  TableHead,
  TableCell,
  TableBody,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import type { GraphNode, CriticalNodeRow } from "../types";
import { formateDate } from "@/lib/date";
import { getCriticality, getServiceDetails } from "../utils";

interface ServicesListCardProps {
  services: GraphNode[];
  criticalNodes: CriticalNodeRow[];
  onServiceClick: (service: GraphNode) => void;
}

export const ServicesListCard = ({
  services,
  criticalNodes,
  onServiceClick,
}: ServicesListCardProps) => {
  const detailedServices = services.map(getServiceDetails);

  return (
    <MainCard title="Services environment_name">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Service</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Role</TableHead>
            <TableHead>Image</TableHead>
            <TableHead>Criticality</TableHead>
            <TableHead>Used by</TableHead>
            <TableHead>Last event</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {detailedServices.map((s) => (
            <TableRow
              key={s.id}
              onClick={() => onServiceClick(s.node)}
              className="cursor-pointer"
            >
              <TableCell>{s.name}</TableCell>
              <TableCell>
                <Badge variant={s.status} className="uppercase">
                  {s.status}
                </Badge>
              </TableCell>
              <TableCell>{s.role}</TableCell>
              <TableCell>{s.image}</TableCell>
              <TableCell>
                {getCriticality(criticalNodes, s.id)?.toFixed(2) ?? "-"}
              </TableCell>
              <TableCell>{"used by"}</TableCell>
              <TableCell>
                {formateDate(s.lastEvent.at)} : {s.lastEvent.type}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </MainCard>
  );
};
