import 'dotenv/config'
import { readFileSync } from 'node:fs'
import { defineConfig } from 'prisma/config'

const databaseUrlFile = process.env.DATABASE_URL_FILE

let databaseUrl = process.env.DATABASE_URL ?? ''

if (databaseUrlFile !== undefined) {
  if (!databaseUrlFile) {
    throw new Error('DATABASE_URL_FILE must not be empty')
  }

  if (process.env.DATABASE_URL) {
    throw new Error('DATABASE_URL must not be set in Vault mode')
  }

  databaseUrl = readFileSync(databaseUrlFile, 'utf8').trim()

  if (!databaseUrl) {
    throw new Error('Database secret file is empty')
  }
}

export default defineConfig({
  schema: 'prisma/schema.prisma',
  migrations: {
    path: 'prisma/migrations',
  },
  datasource: {
    url: databaseUrl,
  },
})