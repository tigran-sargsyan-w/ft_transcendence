import type { IncidentSummary } from "../type";
import { Card, CardContent, CardHeader } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { useState } from "react";
import {
  Table,
  TableHeader,
  TableRow,
  TableHead,
  TableBody,
  TableCell,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { formateDate } from "@/lib/date";
import { formatElapsedSince, getServicesLabel } from "../utils";
import { mockTopology } from "@/features/topology/mockTopology";

interface IncidentsListProps {
  incidents: IncidentSummary[];
  onIncidentClick: (incident: IncidentSummary) => void;
}

const FILTERS = ["Active", "Resolved", "All"] as const;

type Filter = (typeof FILTERS)[number];

export const IncidentsList = ({
  incidents,
  onIncidentClick,
}: IncidentsListProps) => {
  const [selectedFilter, setSelectedFilter] = useState<Filter>("All");

  const visibleIncidents = incidents.filter((incident) => {
    if (selectedFilter === "All") return true;
    if (selectedFilter === "Resolved") return incident.status === "resolved";
    return incident.status !== "resolved"; // "Active"
  });

  return (
    <Card>
      <CardHeader className="flex justify-between">
        <div className="flex gap-2 p-1  rounded-md ">
          {FILTERS.map((filter) => (
            <Button
              key={filter}
              variant={filter === selectedFilter ? "default" : "outline"}
              aria-pressed={filter === selectedFilter}
              onClick={() => setSelectedFilter(filter)}
              className="cursor-pointer"
            >
              {filter}
            </Button>
          ))}
        </div>
        <div>SEARCH</div>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Severity</TableHead>
              <TableHead>Incident</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Started</TableHead>
              <TableHead>Duration</TableHead>
              <TableHead>Commander</TableHead>
              <TableHead>Services</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {visibleIncidents.map((incident) => (
              <TableRow
                key={incident.id}
                className="cursor-pointer"
                onClick={() => onIncidentClick(incident)}
              >
                <TableCell>
                  <Badge variant={incident.severity} className="uppercase">
                    {incident.severity}
                  </Badge>
                </TableCell>
                <TableCell>{incident.title}</TableCell>
                <TableCell>
                  <Badge variant={incident.status} className="uppercase">
                    {incident.status}
                  </Badge>
                </TableCell>
                <TableCell>{formateDate(incident.startedAt)}</TableCell>
                <TableCell>
                  {formatElapsedSince(incident.startedAt, incident.resolvedAt)}
                </TableCell>
                <TableCell>{incident.commander?.displayName}</TableCell>
                <TableCell>
                  {getServicesLabel(mockTopology, incident.seedNodeIds).map(
                    (service) => (
                      <Badge variant="outline" className="lowercase">
                        {service}
                      </Badge>
                    ),
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </CardContent>
    </Card>
  );
};
