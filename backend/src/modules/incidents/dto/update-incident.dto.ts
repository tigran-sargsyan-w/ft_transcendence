import {
  IsIn,
  IsInt,
  IsNotEmpty,
  IsString,
  IsUUID,
  Max,
  MaxLength,
  Min,
} from 'class-validator';
import { Transform } from 'class-transformer';
import { SEVERITIES, STATUSES } from '../incident-events.js';
import { PlainText, OptionalField, Trim } from './validators.js';

// `version` is the one the client read. At least one of the other fields is
// needed: the service checks it.
export class UpdateIncidentDto {
  @IsInt()
  @Min(1)
  // The column is a 32-bit integer.
  @Max(2147483647)
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
  @PlainText()
  title?: string;

  // A user id: the column is a UUID, and the service checks that the user
  // exists. In small letters, like every id the API gives: the same user in
  // capitals is not another commander.
  @OptionalField()
  @Transform(({ value }) =>
    typeof value === 'string' ? value.toLowerCase() : value,
  )
  @IsUUID()
  commanderId?: string;
}
