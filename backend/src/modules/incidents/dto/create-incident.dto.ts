import {
  ArrayMaxSize,
  ArrayNotEmpty,
  IsArray,
  IsIn,
  IsNotEmpty,
  IsString,
  MaxLength,
} from 'class-validator';
import { SEVERITIES } from '../incident-events.js';
import { PlainText, Trim } from './validators.js';

export class CreateIncidentDto {
  @Trim()
  @IsString()
  @IsNotEmpty()
  @MaxLength(200)
  @PlainText()
  title!: string;

  @IsIn(SEVERITIES)
  severity!: string;

  // Checked against the topology the server follows.
  @IsString()
  @IsNotEmpty()
  environmentId!: string;

  // At least one, at most 50 (as in the analyses): each id must be a node of
  // the topology of `environmentId`, which the service checks.
  @IsArray()
  @ArrayNotEmpty()
  @ArrayMaxSize(50)
  @IsString({ each: true })
  @IsNotEmpty({ each: true })
  seedNodeIds!: string[];
}
