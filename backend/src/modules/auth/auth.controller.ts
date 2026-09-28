import {
  Body,
  Controller,
  Get,
  HttpCode,
  Post,
  Req,
  Res,
} from '@nestjs/common';
import type { Response } from 'express';
import {
  type AuthenticatedRequest,
  CurrentUser,
  Public,
} from './auth.decorators.js';
import { AuthService, type PublicUser } from './auth.service.js';
import { LoginDto } from './dto/login.dto.js';
import { RegisterDto } from './dto/register.dto.js';
import {
  SESSION_COOKIE,
  SESSION_TTL_MS,
  sessionCookieOptions,
} from './session-cookie.js';

@Controller('auth')
export class AuthController {
  constructor(private readonly authService: AuthService) {}

  @Public()
  @Post('register')
  register(@Body() dto: RegisterDto) {
    return this.authService.register(dto);
  }

  // passthrough: we set the cookie ourselves but still return the user,
  // so the response keeps its { data } envelope.
  @Public()
  @Post('login')
  @HttpCode(200)
  async login(
    @Body() dto: LoginDto,
    @Res({ passthrough: true }) response: Response,
  ) {
    const { token, user } = await this.authService.login(dto);

    response.cookie(SESSION_COOKIE, token, {
      ...sessionCookieOptions,
      maxAge: SESSION_TTL_MS,
    });

    return user;
  }

  // Protected: the guard has already checked the session cookie.
  @Post('logout')
  @HttpCode(204)
  async logout(
    @Req() request: AuthenticatedRequest,
    @Res({ passthrough: true }) response: Response,
  ) {
    await this.authService.logout(request.cookies[SESSION_COOKIE]);

    response.clearCookie(SESSION_COOKIE, sessionCookieOptions);
  }

  @Get('me')
  me(@CurrentUser() user: PublicUser) {
    return user;
  }
}
