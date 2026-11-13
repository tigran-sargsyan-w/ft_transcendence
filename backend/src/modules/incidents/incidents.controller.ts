import {
  applyDecorators,
  Body,
  Controller,
  Get,
  Param,
  ParseUUIDPipe,
  Patch,
  Post,
  Query,
  UseGuards,
} from '@nestjs/common';
import { Throttle, ThrottlerGuard } from '@nestjs/throttler';
import { CurrentUser } from '../auth/auth.decorators.js';
import type { PublicUser } from '../auth/auth.service.js';
import { CreateIncidentDto } from './dto/create-incident.dto.js';
import { CreateNoteDto } from './dto/create-note.dto.js';
import {
  ListIncidentsDto,
  TimelineQueryDto,
} from './dto/incident-queries.dto.js';
import { UpdateIncidentDto } from './dto/update-incident.dto.js';
import { IncidentsService } from './incidents.service.js';

// Anyone who registers can open incidents, change them and add notes, and the
// timeline is never purged: 30 per minute and per client for each of these
// three routes, counted apart from each other and from the login and the
// analyses.
const LimitWrites = () =>
  applyDecorators(
    UseGuards(ThrottlerGuard),
    Throttle({ default: { limit: 30, ttl: 60_000 } }),
  );

@Controller('incidents')
export class IncidentsController {
  constructor(private readonly incidents: IncidentsService) {}

  @Get()
  list(@Query() query: ListIncidentsDto) {
    return this.incidents.list(query);
  }

  @LimitWrites()
  @Post()
  create(@CurrentUser() user: PublicUser, @Body() dto: CreateIncidentDto) {
    return this.incidents.create(user.id, dto);
  }

  @Get(':id')
  get(@Param('id', ParseUUIDPipe) id: string) {
    return this.incidents.get(id);
  }

  @LimitWrites()
  @Patch(':id')
  update(
    @Param('id', ParseUUIDPipe) id: string,
    @CurrentUser() user: PublicUser,
    @Body() dto: UpdateIncidentDto,
  ) {
    return this.incidents.update(id, user.id, dto);
  }

  @Get(':id/timeline')
  timeline(
    @Param('id', ParseUUIDPipe) id: string,
    @Query() query: TimelineQueryDto,
  ) {
    return this.incidents.timeline(id, query);
  }

  @LimitWrites()
  @Post(':id/notes')
  addNote(
    @Param('id', ParseUUIDPipe) id: string,
    @CurrentUser() user: PublicUser,
    @Body() dto: CreateNoteDto,
  ) {
    return this.incidents.addNote(id, user.id, dto);
  }
}
