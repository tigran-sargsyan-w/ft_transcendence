import { Transform } from 'class-transformer';
import { IsInt, IsString, Matches, Max, MaxLength, Min, MinLength } from 'class-validator';

export class SearchUsersDto {
  @Transform(({ value }) => (typeof value === 'string' ? value.trim() : value))
  @IsString()
  @MinLength(2)
  @MaxLength(64)
  // PostgreSQL text cannot contain NUL, even in a bound query parameter.
  @Matches(/^[^\0]*$/, {
    message: 'search must not contain a NUL character',
  })
  search!: string;

  // Only decimal integers are accepted; reject "1e1", "0x10" and arrays.
  @Transform(({ value }) =>
    typeof value === 'string' && /^[1-9]\d*$/.test(value)
      ? Number(value)
      : value,
  )
  @IsInt()
  @Min(1)
  @Max(20)
  limit = 20;
}
