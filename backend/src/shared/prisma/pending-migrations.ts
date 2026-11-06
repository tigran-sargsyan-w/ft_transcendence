import { readdir } from 'node:fs/promises';
import { join } from 'node:path';
import type { PrismaService } from './prisma.service.js';

// The migrations of the repository that the database has not applied, or did
// not finish. A backend started before `prisma migrate deploy`, or after a
// new migration was pulled, would fail on its first query.
// They are read where `prisma migrate deploy` reads them (prisma.config.ts):
// one folder per migration, relative to the working directory.
export async function pendingMigrations(
  prisma: Pick<PrismaService, '$queryRaw'>,
  dir = join(process.cwd(), 'prisma', 'migrations'),
) {
  const folders = (await readdir(dir, { withFileTypes: true }))
    .filter((entry) => entry.isDirectory())
    .map((entry) => entry.name);

  let applied: Set<string>;

  try {
    const rows = await prisma.$queryRaw<{ migration_name: string }[]>`
      SELECT migration_name FROM _prisma_migrations
      WHERE finished_at IS NOT NULL AND rolled_back_at IS NULL`;

    applied = new Set(rows.map((row) => row.migration_name));
  } catch {
    // No such table: nothing was ever applied. A role that cannot read it is
    // as unusable, so it is reported the same way.
    applied = new Set();
  }

  return folders.filter((name) => !applied.has(name));
}
