import { Module } from '@nestjs/common';
import { PrismaModule } from '../../shared/prisma/prisma.module.js';
import { PresenceModule } from '../presence/presence.module.js';
import { AvatarController } from './avatar.controller.js';
import { AvatarOriginGuard } from './avatar-origin.guard.js';
import { AvatarService } from './avatar.service.js';
import { FriendsController } from './friends.controller.js';
import { FriendsService } from './friends.service.js';
import { UsersController } from './users.controller.js';
import { UsersService } from './users.service.js';

@Module({
  imports: [PrismaModule, PresenceModule],
  controllers: [UsersController, FriendsController, AvatarController],
  providers: [UsersService, FriendsService, AvatarService, AvatarOriginGuard],
})
export class UsersModule {}
