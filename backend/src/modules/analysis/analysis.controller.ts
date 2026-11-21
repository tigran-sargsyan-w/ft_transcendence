import {
  Body,
  Controller,
  HttpCode,
  Param,
  Post,
  UseGuards,
} from '@nestjs/common';
import { Throttle, ThrottlerGuard } from '@nestjs/throttler';
import { AnalysisService } from './analysis.service.js';
import { AnalyzeDto } from './dto/analyze.dto.js';

@Controller('environments/:environmentId/analyses')
export class AnalysisController {
  constructor(private readonly analysis: AnalysisService) {}

  // Every call can reach the engine, and anyone who registers can make them:
  // 30 per minute and per client, counted on its own (not with the login).
  @UseGuards(ThrottlerGuard)
  @Throttle({ default: { limit: 30, ttl: 60_000 } })
  @Post()
  // Nothing is created or stored: 200, not 201.
  @HttpCode(200)
  analyze(
    @Param('environmentId') environmentId: string,
    @Body() body: AnalyzeDto,
  ) {
    return this.analysis.analyze(environmentId, body);
  }
}
