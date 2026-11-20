import { Module } from '@nestjs/common';
import { PrismaModule } from '../../shared/prisma/prisma.module.js';
import { FriendsController } from './friends.controller.js';
import { FriendsService } from './friends.service.js';
import { UsersController } from './users.controller.js';
import { UsersService } from './users.service.js';

@Module({
  imports: [PrismaModule],
  controllers: [UsersController, FriendsController],
  providers: [UsersService, FriendsService],
})
export class UsersModule {}
