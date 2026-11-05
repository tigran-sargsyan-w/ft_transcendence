import { Body, Controller, HttpCode, Param, Post } from '@nestjs/common';
import { AnalysisService } from './analysis.service.js';
import { AnalyzeDto } from './dto/analyze.dto.js';

@Controller('environments/:environmentId/analyses')
export class AnalysisController {
  constructor(private readonly analysis: AnalysisService) {}

  // Nothing is created or stored: 200, not 201.
  @Post()
  @HttpCode(200)
  analyze(
    @Param('environmentId') environmentId: string,
    @Body() body: AnalyzeDto,
  ) {
    return this.analysis.analyze(environmentId, body);
  }
}
