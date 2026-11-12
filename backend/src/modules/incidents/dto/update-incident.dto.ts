import {
  IsIn,
  IsInt,
  IsNotEmpty,
  IsString,
  IsUUID,
  MaxLength,
  Min,
} from 'class-validator';
import { SEVERITIES, STATUSES } from '../incident-events.js';
import { NoNul, OptionalField, Trim } from './validators.js';

// `version` is the one the client read. At least one of the other fields is
// needed: the service checks it.
export class UpdateIncidentDto {
  @IsInt()
  @Min(1)
  version!: number;

  @OptionalField()
  @IsIn(STATUSES)
  status?: string;

  @OptionalField()
  @IsIn(SEVERITIES)
  severity?: string;

  @OptionalField()
  @Trim()
  @IsString()
  @IsNotEmpty()
  @MaxLength(200)
  @NoNul()
  title?: string;

  // A user id: the column is a UUID, and the service checks that the user
  // exists.
  @OptionalField()
  @IsUUID()
  commanderId?: string;
}
