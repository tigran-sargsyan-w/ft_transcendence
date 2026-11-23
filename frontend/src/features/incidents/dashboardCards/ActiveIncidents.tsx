import { MainCard } from "@/components/cards/MainCard";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatElapsedSince } from "../utils";
import type { IncidentSummary } from "../type";

interface ActiveIncidentsProps {
  incidents: IncidentSummary[];
}

export const ActiveIncidents = ({ incidents }: ActiveIncidentsProps) => {
  return (
    <MainCard
      title="Active incidents"
      linkLabel="View all incidents"
      linkTarget="/incidents"
    >
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-px">Severity</TableHead>
            <TableHead className="w-full">Incident</TableHead>
            <TableHead className="w-px">Status</TableHead>
            <TableHead className="hidden md:table-cell">Duration</TableHead>
            <TableHead className="hidden md:table-cell">Commander</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {incidents.map((i) => (
            <TableRow key={i.id}>
              <TableCell>
                <Badge variant={i.severity}>{i.severity.toUpperCase()}</Badge>
              </TableCell>
              <TableCell className="max-w-0 w-full truncate">
                {i.title}
              </TableCell>
              <TableCell>
                <Badge variant={i.status} className="uppercase">
                  {i.status}
                </Badge>
              </TableCell>
              <TableCell className="hidden md:table-cell">
                {formatElapsedSince(i.startedAt)}
              </TableCell>
              <TableCell className="hidden md:table-cell">
                {i.commander?.displayName}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </MainCard>
  );
};
