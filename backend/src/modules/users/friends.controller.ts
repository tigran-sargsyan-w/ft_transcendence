import {
  applyDecorators,
  Body,
  Controller,
  Delete,
  Get,
  HttpCode,
  Param,
  ParseUUIDPipe,
  Post,
  UseGuards,
} from '@nestjs/common';
import { Throttle, ThrottlerGuard } from '@nestjs/throttler';
import { CurrentUser } from '../auth/auth.decorators.js';
import type { PublicUser } from '../auth/auth.service.js';
import { CreateFriendRequestDto } from './dto/create-friend-request.dto.js';
import { FriendsService } from './friends.service.js';

// Match the write limit already used by the incidents API.
const LimitWrites = () =>
  applyDecorators(
    UseGuards(ThrottlerGuard),
    Throttle({ default: { limit: 30, ttl: 60_000 } }),
  );

@Controller('users/me')
export class FriendsController {
  constructor(private readonly friends: FriendsService) {}

  @Get('friends')
  listFriends(@CurrentUser() user: PublicUser) {
    return this.friends.listFriends(user.id);
  }

  @Get('friend-requests')
  listRequests(@CurrentUser() user: PublicUser) {
    return this.friends.listRequests(user.id);
  }

  @LimitWrites()
  @Post('friend-requests')
  sendRequest(
    @CurrentUser() user: PublicUser,
    @Body() dto: CreateFriendRequestDto,
  ) {
    return this.friends.sendRequest(user.id, dto.targetUserId);
  }

  @LimitWrites()
  @Post('friend-requests/:id/accept')
  @HttpCode(200)
  acceptRequest(
    @CurrentUser() user: PublicUser,
    @Param('id', ParseUUIDPipe) id: string,
  ) {
    return this.friends.acceptRequest(user.id, id);
  }

  @LimitWrites()
  @Delete('friend-requests/:id')
  @HttpCode(204)
  deleteRequest(
    @CurrentUser() user: PublicUser,
    @Param('id', ParseUUIDPipe) id: string,
  ) {
    return this.friends.deleteRequest(user.id, id);
  }

  @LimitWrites()
  @Delete('friends/:userId')
  @HttpCode(204)
  removeFriend(
    @CurrentUser() user: PublicUser,
    @Param('userId', ParseUUIDPipe) userId: string,
  ) {
    return this.friends.removeFriend(user.id, userId);
  }
}
