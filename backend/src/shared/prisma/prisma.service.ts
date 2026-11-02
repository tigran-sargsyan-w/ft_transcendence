import { Injectable, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { PrismaPg } from '@prisma/adapter-pg';
import { readFileSync } from 'node:fs';
import { PrismaClient } from '../../generated/prisma/client.js';

@Injectable()
export class PrismaService
  extends PrismaClient
  implements OnModuleInit, OnModuleDestroy
{
  constructor() {
    const databaseUrlFile = process.env.DATABASE_URL_FILE;

    let databaseUrl: string;

    if (databaseUrlFile !== undefined) {
      if (!databaseUrlFile) {
        throw new Error('DATABASE_URL_FILE must not be empty');
      }

      if (process.env.DATABASE_URL) {
        throw new Error('DATABASE_URL must not be set in Vault mode');
      }

      databaseUrl = readFileSync(databaseUrlFile, 'utf8').trim();
    } else {
      databaseUrl = process.env.DATABASE_URL ?? '';
    }

    if (!databaseUrl) {
      throw new Error('Database URL is missing or empty');
    }

    const adapter = new PrismaPg({
      connectionString: databaseUrl,
    });

    super({ adapter });
  }

  async onModuleInit() {
    await this.$connect();
  }

  async onModuleDestroy() {
    await this.$disconnect();
  }
}