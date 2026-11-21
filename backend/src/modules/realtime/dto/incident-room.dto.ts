import { IsUUID } from 'class-validator';

export class IncidentRoomDto {
  // Any UUID is accepted here, in capitals too; an id that is not one would
  // reach PostgreSQL and end as a 500.
  @IsUUID()
  incidentId!: string;
}
