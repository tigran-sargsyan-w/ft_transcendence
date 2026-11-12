import { Transform } from 'class-transformer';
import { IsString, Matches, MaxLength, MinLength } from 'class-validator';

export class UpdateProfileDto {
  @Transform(({ value }) => (typeof value === 'string' ? value.trim() : value))
  @IsString()
  @MinLength(1)
  @MaxLength(64)
  // PostgreSQL refuses a NUL character in text: it would be a 500.
  @Matches(/^[^\0]*$/, {
    message: 'displayName must not contain a NUL character',
  })
  displayName!: string;
}
