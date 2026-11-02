import { mkdir, mkdtemp, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pendingMigrations } from '../../src/shared/prisma/pending-migrations.js';

// Answers the query of `_prisma_migrations` with these rows, or fails.
const database = (answer: string[] | Error) => ({
  $queryRaw: (async () => {
    if (answer instanceof Error) {
      throw answer;
    }

    return answer.map((migration_name) => ({ migration_name }));
  }) as never,
});

describe('pendingMigrations', () => {
  let dir: string;

  beforeAll(async () => {
    dir = await mkdtemp(join(tmpdir(), 'migrations-'));
    await mkdir(join(dir, '20260101_first'));
    await mkdir(join(dir, '20260102_second'));
    // Not a migration: Prisma keeps its lock file next to them.
    await writeFile(
      join(dir, 'migration_lock.toml'),
      'provider = "postgresql"',
    );
  });

  it('finds nothing when every folder is applied', async () => {
    expect(
      await pendingMigrations(
        database(['20260101_first', '20260102_second']),
        dir,
      ),
    ).toEqual([]);
  });

  it('finds the folder the database does not know', async () => {
    expect(await pendingMigrations(database(['20260101_first']), dir)).toEqual([
      '20260102_second',
    ]);
  });

  it('ignores a migration of the database that has no folder', async () => {
    expect(
      await pendingMigrations(
        database(['20260101_first', '20260102_second', '20250101_old']),
        dir,
      ),
    ).toEqual([]);
  });

  it('finds every folder when the database was never migrated', async () => {
    expect(
      await pendingMigrations(
        database(new Error('relation "_prisma_migrations" does not exist')),
        dir,
      ),
    ).toEqual(['20260101_first', '20260102_second']);
  });

  it('fails when the folder of the migrations is missing', async () => {
    await expect(
      pendingMigrations(database([]), join(dir, 'nowhere')),
    ).rejects.toThrow('ENOENT');
  });
});
