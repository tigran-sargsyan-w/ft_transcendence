-- CreateTable
CREATE TABLE "incidents" (
    "id" UUID NOT NULL,
    "environmentId" TEXT NOT NULL,
    "title" TEXT NOT NULL,
    "severity" TEXT NOT NULL,
    "status" TEXT NOT NULL,
    "seedNodeIds" TEXT[],
    "createdById" UUID NOT NULL,
    "commanderId" UUID NOT NULL,
    "openedAtEpoch" TEXT NOT NULL,
    "openedAtRevision" INTEGER NOT NULL,
    "version" INTEGER NOT NULL DEFAULT 1,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMP(3) NOT NULL,

    CONSTRAINT "incidents_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "incident_events" (
    "id" UUID NOT NULL,
    "seq" SERIAL NOT NULL,
    "incidentId" UUID NOT NULL,
    "version" INTEGER NOT NULL,
    "type" TEXT NOT NULL,
    "actorId" UUID NOT NULL,
    "data" JSONB NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "incident_events_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE INDEX "incident_events_incidentId_seq_idx" ON "incident_events"("incidentId", "seq");

-- AddForeignKey
ALTER TABLE "incidents" ADD CONSTRAINT "incidents_createdById_fkey" FOREIGN KEY ("createdById") REFERENCES "users"("id") ON DELETE RESTRICT ON UPDATE RESTRICT;

-- AddForeignKey
ALTER TABLE "incidents" ADD CONSTRAINT "incidents_commanderId_fkey" FOREIGN KEY ("commanderId") REFERENCES "users"("id") ON DELETE RESTRICT ON UPDATE RESTRICT;

-- AddForeignKey
ALTER TABLE "incident_events" ADD CONSTRAINT "incident_events_incidentId_fkey" FOREIGN KEY ("incidentId") REFERENCES "incidents"("id") ON DELETE RESTRICT ON UPDATE RESTRICT;

-- AddForeignKey
ALTER TABLE "incident_events" ADD CONSTRAINT "incident_events_actorId_fkey" FOREIGN KEY ("actorId") REFERENCES "users"("id") ON DELETE RESTRICT ON UPDATE RESTRICT;
