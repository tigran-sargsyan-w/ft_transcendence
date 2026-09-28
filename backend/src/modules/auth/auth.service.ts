import {
  ConflictException,
  Injectable,
  UnauthorizedException,
} from '@nestjs/common';
import * as argon2 from 'argon2';
import { createHash, randomBytes } from 'node:crypto';
import { Prisma } from '../../generated/prisma/client.js';
import { PrismaService } from '../../shared/prisma/prisma.service.js';
import { LoginDto } from './dto/login.dto.js';
import { RegisterDto } from './dto/register.dto.js';
import { SESSION_TTL_MS } from './session-cookie.js';

// The user fields safe to send to a client (never the password hash).
const PUBLIC_USER = {
  id: true,
  email: true,
  createdAt: true,
  updatedAt: true,
} as const;

// Hashed once at startup. Checked when the email is unknown, so that an
// unknown email and a wrong password take the same time to answer.
const dummyHash = argon2.hash('dummy-password', { type: argon2.argon2id });

export function hashToken(token: string) {
  return createHash('sha256').update(token).digest('hex');
}

@Injectable()
export class AuthService {
  constructor(private readonly prisma: PrismaService) {}

  async register(dto: RegisterDto) {
    const passwordHash = await argon2.hash(dto.password, {
      type: argon2.argon2id,
    });

    try {
      return await this.prisma.user.create({
        data: {
          email: dto.email,
          passwordHash,
        },
        select: PUBLIC_USER,
      });
    } catch (error) {
      if (
        error instanceof Prisma.PrismaClientKnownRequestError &&
        error.code === 'P2002'
      ) {
        throw new ConflictException('Email is already registered', {
          errorCode: 'EMAIL_ALREADY_EXISTS',
        });
      }

      throw error;
    }
  }

  // Returns the session token in clear text: it is only ever sent in the
  // cookie, the database keeps its hash.
  async login(dto: LoginDto) {
    const user = await this.prisma.user.findUnique({
      where: { email: dto.email },
    });

    const passwordMatches = await argon2.verify(
      user?.passwordHash ?? (await dummyHash),
      dto.password,
    );

    if (!user || !passwordMatches) {
      throw new UnauthorizedException('Invalid credentials', {
        errorCode: 'AUTH_INVALID_CREDENTIALS',
      });
    }

    const token = randomBytes(32).toString('base64url');

    await this.prisma.session.create({
      data: {
        userId: user.id,
        tokenHash: hashToken(token),
        expiresAt: new Date(Date.now() + SESSION_TTL_MS),
      },
    });

    return {
      token,
      user: {
        id: user.id,
        email: user.email,
        createdAt: user.createdAt,
        updatedAt: user.updatedAt,
      },
    };
  }
}
