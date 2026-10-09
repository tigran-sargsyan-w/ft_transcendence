import { Transform } from 'class-transformer';
import { IsEmail, IsNotEmpty, IsString, MaxLength } from 'class-validator';

export class LoginDto {
  @Transform(({ value }) =>
    typeof value === 'string' ? value.trim().toLowerCase() : value,
  )
  @IsEmail()
  email!: string;

  // No minimum length: a wrong password must fail like any other, with a 401.
  @IsString()
  @IsNotEmpty()
  @MaxLength(128)
  password!: string;
}
