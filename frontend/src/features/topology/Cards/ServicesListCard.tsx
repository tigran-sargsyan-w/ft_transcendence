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
import { mockServiceDetails } from "../mockServiceDetails";
import type { GraphNode } from "../types";

interface ServicesListCardProps {
  services: GraphNode[];
}

export const ServicesListCard = ({ services }: ServicesListCardProps) => {
  const detailedServices = services.map((node) => {
    const details = mockServiceDetails[node.id];
    return {
      id: node.id,
      name: node.label,
      status: node.status,
      image: node.metadata?.image as string | undefined,
      role: details?.role,
      lastEvent: details?.lastEvent,
    };
  });

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
            <TableRow key={s.id}>
              <TableCell>{s.name}</TableCell>
              <TableCell>
                <Badge variant={s.status} className="uppercase">
                  {s.status}
                </Badge>
              </TableCell>
              <TableCell>{s.role}</TableCell>
              <TableCell>{"Criticality"}</TableCell>
              <TableCell>{"used by"}</TableCell>
              <TableCell>
                {s.lastEvent.at} : {s.lastEvent.type}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </MainCard>
  );
};
