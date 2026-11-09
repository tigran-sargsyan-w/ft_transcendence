import { MainCard } from "@/components/cards/MainCard";
import { Badge } from "@/components/ui/badge";
import { mockTopology } from "../mockTopology";
import { countByStatus, getServices, STATUS_ORDER } from "../utils";
import { useState } from "react";
import type { NodeStatus } from "../types";

export const ServicesHealth = () => {
  const [selectedStatus, setSelectedStatus] = useState<NodeStatus | null>(null);

  const services = getServices(mockTopology.nodes);
  const counts = countByStatus(services);

  const selectStatus = (status: NodeStatus) => {
    selectedStatus === status
      ? setSelectedStatus(null)
      : setSelectedStatus(status);
  };

  return (
    <MainCard
      title="Services health"
      linkLabel="Open topology"
      linkTarget="/topology"
    >
      {services.length === 0 ? (
        <p className="text-sm text-muted-foreground">No services to display.</p>
      ) : (
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap gap-2">
            {STATUS_ORDER.filter((status) => counts[status] > 0).map(
              (status) => (
                <Badge
                  key={status}
                  variant={status}
                  className="capitalize cursor-pointer"
                  onClick={() => selectStatus(status)}
                >
                  {counts[status]} {status}
                </Badge>
              ),
            )}
          </div>

          <ul className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-4">
            {services.map((service) => {
              return (
                (service.status === selectedStatus ||
                  selectedStatus === null) && (
                  <li
                    key={service.id}
                    className="flex items-center justify-between gap-2 rounded-md border px-3 py-2"
                  >
                    <span
                      className="truncate text-sm font-medium"
                      title={service.label}
                    >
                      {service.label}
                    </span>
                    <Badge variant={service.status} className="capitalize">
                      {service.status}
                    </Badge>
                  </li>
                )
              );
            })}
          </ul>
        </div>
      )}
    </MainCard>
  );
};
