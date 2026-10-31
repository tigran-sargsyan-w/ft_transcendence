import { IsNotEmpty, IsOptional, IsString } from 'class-validator';

export class SubscribeDto {
  // Absent means the environment the server follows.
  @IsOptional()
  @IsString()
  @IsNotEmpty()
  environmentId?: string;
}
