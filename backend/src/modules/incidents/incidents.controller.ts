import {
  Body,
  Controller,
  Get,
  Param,
  ParseUUIDPipe,
  Patch,
  Post,
  Query,
} from '@nestjs/common';
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

@Controller('incidents')
export class IncidentsController {
  constructor(private readonly incidents: IncidentsService) {}

  @Get()
  list(@Query() query: ListIncidentsDto) {
    return this.incidents.list(query);
  }

  @Post()
  create(@CurrentUser() user: PublicUser, @Body() dto: CreateIncidentDto) {
    return this.incidents.create(user.id, dto);
  }

  @Get(':id')
  get(@Param('id', ParseUUIDPipe) id: string) {
    return this.incidents.get(id);
  }

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

  @Post(':id/notes')
  addNote(
    @Param('id', ParseUUIDPipe) id: string,
    @CurrentUser() user: PublicUser,
    @Body() dto: CreateNoteDto,
  ) {
    return this.incidents.addNote(id, user.id, dto);
  }
}
