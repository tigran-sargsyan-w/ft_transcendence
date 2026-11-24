-- One row per unordered pair of users; the application inserts canonical UUID order.
-- PostgreSQL checks preserve these invariants even under concurrent requests.

CREATE TABLE "friendships" (
    "id" UUID NOT NULL,
    "userLowId" UUID NOT NULL,
    "userHighId" UUID NOT NULL,
    "requestedById" UUID NOT NULL,
    "status" TEXT NOT NULL DEFAULT 'pending',
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "acceptedAt" TIMESTAMP(3),

    CONSTRAINT "friendships_pkey" PRIMARY KEY ("id"),
    CONSTRAINT "friendships_distinct_users_check" CHECK ("userLowId" < "userHighId"),
    CONSTRAINT "friendships_requester_membership_check" CHECK (
        "requestedById" = "userLowId" OR "requestedById" = "userHighId"
    ),
    CONSTRAINT "friendships_status_check" CHECK (
        ("status" = 'pending' AND "acceptedAt" IS NULL) OR
        ("status" = 'accepted' AND "acceptedAt" IS NOT NULL)
    )
);

CREATE UNIQUE INDEX "friendships_userLowId_userHighId_key"
    ON "friendships"("userLowId", "userHighId");

CREATE INDEX "friendships_userLowId_status_idx"
    ON "friendships"("userLowId", "status");

CREATE INDEX "friendships_userHighId_status_idx"
    ON "friendships"("userHighId", "status");

CREATE INDEX "friendships_requestedById_status_idx"
    ON "friendships"("requestedById", "status");

ALTER TABLE "friendships"
    ADD CONSTRAINT "friendships_userLowId_fkey"
    FOREIGN KEY ("userLowId") REFERENCES "users"("id")
    ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "friendships"
    ADD CONSTRAINT "friendships_userHighId_fkey"
    FOREIGN KEY ("userHighId") REFERENCES "users"("id")
    ON DELETE CASCADE ON UPDATE CASCADE;

ALTER TABLE "friendships"
    ADD CONSTRAINT "friendships_requestedById_fkey"
    FOREIGN KEY ("requestedById") REFERENCES "users"("id")
    ON DELETE CASCADE ON UPDATE CASCADE;
