import { cp, mkdir, mkdtemp } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pendingMigrations } from '../src/shared/prisma/pending-migrations.js';
import { PrismaService } from '../src/shared/prisma/prisma.service.js';

// The query of `_prisma_migrations` itself, against the migrated test database
// (the unit tests only answer it with a stub).
describe('pendingMigrations (e2e)', () => {
  const prisma = new PrismaService();

  beforeAll(() => prisma.onModuleInit());
  afterAll(() => prisma.onModuleDestroy());

  it('finds nothing in the migrations of the repository', async () => {
    expect(await pendingMigrations(prisma)).toEqual([]);
  });

  it('finds a migration the database has not applied', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'migrations-'));

    await cp(join(process.cwd(), 'prisma', 'migrations'), dir, {
      recursive: true,
    });
    await mkdir(join(dir, '99990101000000_not_applied'));

    expect(await pendingMigrations(prisma, dir)).toEqual([
      '99990101000000_not_applied',
    ]);
  });
});
