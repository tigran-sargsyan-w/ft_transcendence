-- Store canonical, stripped WebP bytes in a separate one-to-one table.
-- Keeping image data out of public.user reads avoids transferring blobs accidentally.
CREATE TABLE "user_avatars" (
    "userId" UUID NOT NULL,
    "data" BYTEA NOT NULL,
    "mimeType" VARCHAR(32) NOT NULL,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "user_avatars_pkey" PRIMARY KEY ("userId"),
    CONSTRAINT "user_avatars_mimeType_check" CHECK ("mimeType" = 'image/webp'),
    CONSTRAINT "user_avatars_data_size_check" CHECK (octet_length("data") BETWEEN 1 AND 2097152)
);

ALTER TABLE "user_avatars"
    ADD CONSTRAINT "user_avatars_userId_fkey"
    FOREIGN KEY ("userId") REFERENCES "users"("id")
    ON DELETE CASCADE ON UPDATE CASCADE;
