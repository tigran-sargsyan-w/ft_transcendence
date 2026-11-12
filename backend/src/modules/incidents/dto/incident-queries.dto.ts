import { Transform } from 'class-transformer';
import { IsIn, IsNotEmpty, IsOptional, IsString } from 'class-validator';
import { SEVERITIES, STATUSES, TIMELINE_KINDS } from '../incident-events.js';
import { PlainText } from './validators.js';

// Every filter is optional.
export class ListIncidentsDto {
  @IsOptional()
  @IsIn(STATUSES)
  status?: string;

  @IsOptional()
  @IsIn(SEVERITIES)
  severity?: string;

  @IsOptional()
  @IsString()
  @IsNotEmpty()
  @PlainText()
  environmentId?: string;
}

export class TimelineQueryDto {
  // "status_change,severity_change": one or more kinds.
  @IsOptional()
  @Transform(({ value }) =>
    typeof value === 'string' ? value.split(',') : value,
  )
  @IsIn(TIMELINE_KINDS, { each: true })
  kind?: string[];

  // Oldest first by default.
  @IsOptional()
  @IsIn(['asc', 'desc'])
  order?: 'asc' | 'desc';

  // No entry of v1 concerns a node: any value gives an empty timeline.
  @IsOptional()
  @IsString()
  @IsNotEmpty()
  nodeId?: string;
}
