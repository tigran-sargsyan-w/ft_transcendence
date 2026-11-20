import { Module } from '@nestjs/common';
import { PrismaModule } from '../../shared/prisma/prisma.module.js';
import { PresenceService } from './presence.service.js';

// The same in-memory instance is shared by HTTP friends and Socket.IO.
@Module({
  imports: [PrismaModule],
  providers: [PresenceService],
  exports: [PresenceService],
})
export class PresenceModule {}
