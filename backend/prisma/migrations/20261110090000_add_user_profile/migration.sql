-- AlterTable
ALTER TABLE "users" ADD COLUMN     "avatarUrl" TEXT,
ADD COLUMN     "displayName" TEXT;

-- Existing accounts get the same default name as a new account without one.
UPDATE "users" SET "displayName" = 'User ' || left("id"::text, 8);

ALTER TABLE "users" ALTER COLUMN "displayName" SET NOT NULL;
