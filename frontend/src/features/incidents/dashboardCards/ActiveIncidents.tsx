import { MainCard } from "@/components/cards/MainCard";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { mockIncidents } from "../mockIncidents";

export const ActiveIncidents = () => {
  return (
    <MainCard
      title="Active incidents"
      linkLabel="View all incidents"
      linkTarget="/incidents"
    >
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Severity</TableHead>
            <TableHead>Incident</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Duration</TableHead>
            <TableHead>Commander</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {mockIncidents.map((i) => (
            <TableRow key={i.id}>
              <TableCell>
                <Badge variant={i.severity}>{i.severity.toUpperCase()}</Badge>
              </TableCell>
              <TableCell>{i.title}</TableCell>
              <TableCell>
                <Badge variant={i.status} className="capitalize">
                  {i.status}
                </Badge>
              </TableCell>
              <TableCell>{i.startedAt}</TableCell>
              <TableCell>{i.commander?.displayName}</TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </MainCard>
  );
};
