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

  // The engine requires them for blast_radius and attack_paths, and refuses
  // an id that is not in the topology: it answers GRAPH_MISSING_SEEDS or
  // GRAPH_INVALID_TOPOLOGY, which is passed on.
  @IsOptional()
  @IsArray()
  @ArrayMaxSize(50)
  @IsString({ each: true })
  @IsNotEmpty({ each: true })
  seedNodeIds?: string[];

  // The engine's own defaults are 5 levels and 20 paths.
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
