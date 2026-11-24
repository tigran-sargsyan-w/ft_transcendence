import { Transform } from 'class-transformer';
import { IsUUID } from 'class-validator';

export class CreateFriendRequestDto {
  @Transform(({ value }) =>
    typeof value === 'string' ? value.toLowerCase() : value,
  )
  @IsUUID()
  targetUserId!: string;
}
