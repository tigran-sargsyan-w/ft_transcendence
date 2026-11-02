import {
  ArrayMaxSize,
  ArrayNotEmpty,
  ArrayUnique,
  IsArray,
  IsIn,
  IsInt,
  IsNotEmpty,
  IsOptional,
  IsString,
  Max,
  Min,
} from 'class-validator';
import { ANALYSES } from '../analyses.js';

export class AnalyzeDto {
  @IsArray()
  @ArrayNotEmpty()
  @ArrayUnique()
  @IsIn(ANALYSES, { each: true })
  analyses!: string[];

  // Required for blast_radius and attack_paths, and each id must be a node of
  // the topology. The service checks both before calling the engine, so the
  // user gets a VALIDATION_ERROR that names this field.
  @IsOptional()
  @IsArray()
  @ArrayMaxSize(50)
  @IsString({ each: true })
  @IsNotEmpty({ each: true })
  seedNodeIds?: string[];

  // Without them, attack_paths goes 5 levels deep and stops at 20 paths, and
  // blast_radius has no depth limit.
  @IsOptional()
  @IsInt()
  @Min(1)
  @Max(10)
  maxDepth?: number;

  @IsOptional()
  @IsInt()
  @Min(1)
  @Max(100)
  maxPaths?: number;
}
