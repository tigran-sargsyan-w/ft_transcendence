import {
  ConflictException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { validationError } from '../../app.setup.js';
import type { Prisma } from '../../generated/prisma/client.js';
import { PrismaService } from '../../shared/prisma/prisma.service.js';
import { checkNodeIds } from '../analysis/analysis.service.js';
import { knownSnapshot } from '../topology/known-snapshot.js';
import { TopologyService } from '../topology/topology.service.js';
import { PUBLIC_PROFILE } from '../users/users.service.js';
import type { CreateIncidentDto } from './dto/create-incident.dto.js';
import type { CreateNoteDto } from './dto/create-note.dto.js';
import type {
  ListIncidentsDto,
  TimelineQueryDto,
} from './dto/incident-queries.dto.js';
import type { UpdateIncidentDto } from './dto/update-incident.dto.js';
import {
  changedFields,
  resolvedAt,
  statusHistory,
  toTimelineEntry,
  type UserRef,
} from './incident-events.js';

type Db = Prisma.TransactionClient;

// Only the id, the display name and the avatar of a user: never the email.
const WITH_COMMANDER = { commander: { select: PUBLIC_PROFILE } } as const;
const WITH_ACTOR = { actor: { select: PUBLIC_PROFILE } } as const;

const incidentNotFound = () =>
  new NotFoundException('Incident not found', {
    errorCode: 'INCIDENT_NOT_FOUND',
  });

type IncidentRow = {
  id: string;
  environmentId: string;
  title: string;
  severity: string;
  status: string;
  seedNodeIds: string[];
  version: number;
  createdAt: Date;
  commander: UserRef;
};

type StatusHistory = ReturnType<typeof statusHistory>;

// `IncidentSummary` of the contract. `startedAt` is the creation date.
function summaryOf(incident: IncidentRow, history: StatusHistory) {
  return {
    id: incident.id,
    title: incident.title,
    severity: incident.severity,
    status: incident.status,
    environmentId: incident.environmentId,
    startedAt: incident.createdAt.toISOString(),
    resolvedAt: resolvedAt(incident.status, history),
    commander: incident.commander,
    seedNodeIds: incident.seedNodeIds,
  };
}

// `Incident` of the contract, as far as v1 goes: no participants, no runbook,
// no saved analysis, so no role either.
function incidentOf(incident: IncidentRow, history: StatusHistory) {
  return {
    ...summaryOf(incident, history),
    version: incident.version,
    participants: [],
    runbook: [],
    statusHistory: history,
    myRole: null,
    latestAnalysis: null,
  };
}

// The status history of each incident, from its `created` event and its
// status changes.
async function statusHistories(db: Db, incidentIds: string[]) {
  const events = await db.incidentEvent.findMany({
    where: {
      incidentId: { in: incidentIds },
      type: { in: ['created', 'status_changed'] },
    },
    orderBy: { seq: 'asc' },
    select: { incidentId: true, type: true, data: true, createdAt: true },
  });

  return new Map(
    incidentIds.map((id) => [
      id,
      statusHistory(events.filter((event) => event.incidentId === id)),
    ]),
  );
}

async function viewIncident(db: Db, id: string) {
  const incident = await db.incident.findUnique({
    where: { id },
    include: WITH_COMMANDER,
  });

  if (!incident) {
    throw incidentNotFound();
  }

  // `incident.id` and not `id`: the path may write the UUID in capitals, the
  // rows give it in small letters.
  const histories = await statusHistories(db, [incident.id]);

  return incidentOf(incident, histories.get(incident.id) ?? []);
}

// The incident and its history are two reads. In one transaction at this level
// they see the same data: without it, a reopen committed in between would give
// `resolved` next to a history that ends on `open`. Only for `get` and `list`:
// an update at this level could be refused by PostgreSQL when the row changed.
// So the two paths of `update` that only read (nothing to change, and the 409)
// can still see such a reopen; `resolvedAt` ignores a history that disagrees.
const ONE_SNAPSHOT = { isolationLevel: 'RepeatableRead' } as const;

// v1 has no organization, no participant and no role: every signed-in user
// sees and changes every incident. That is only for the demo data; the
// isolation by organization and the permissions will come separately
// (docs/architecture/incidents-api.md).
@Injectable()
export class IncidentsService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly topology: TopologyService,
  ) {}

  list(query: ListIncidentsDto) {
    return this.prisma.$transaction(async (tx) => {
      // An undefined filter is ignored by Prisma.
      const incidents = await tx.incident.findMany({
        where: {
          status: query.status,
          severity: query.severity,
          environmentId: query.environmentId,
        },
        include: WITH_COMMANDER,
        orderBy: { createdAt: 'desc' },
      });
      const histories = await statusHistories(
        tx,
        incidents.map((incident) => incident.id),
      );

      return incidents.map((incident) =>
        summaryOf(incident, histories.get(incident.id) ?? []),
      );
    }, ONE_SNAPSHOT);
  }

  get(id: string) {
    return this.prisma.$transaction((tx) => viewIncident(tx, id), ONE_SNAPSHOT);
  }

  create(userId: string, dto: CreateIncidentDto) {
    // An incident is opened against a topology that is known: the same errors
    // as the analyses, and the ids must be nodes of it.
    const snapshot = knownSnapshot(this.topology.snapshot(), dto.environmentId);
    const seedNodeIds = [...new Set(dto.seedNodeIds)];

    checkNodeIds(snapshot, seedNodeIds);
    // Prisma sets the default date of each insert on its own: the same date
    // for both, so that `startedAt` is the date of the first status.
    const createdAt = new Date();

    return this.prisma.$transaction(async (tx) => {
      const incident = await tx.incident.create({
        data: {
          createdAt,
          environmentId: dto.environmentId,
          title: dto.title,
          severity: dto.severity,
          status: 'open',
          seedNodeIds,
          createdById: userId,
          commanderId: userId,
          openedAtEpoch: snapshot.epoch,
          openedAtRevision: snapshot.revision,
        },
        select: { id: true },
      });

      await tx.incidentEvent.create({
        data: {
          createdAt,
          incidentId: incident.id,
          version: 1,
          type: 'created',
          actorId: userId,
          data: {
            environmentId: dto.environmentId,
            title: dto.title,
            severity: dto.severity,
            status: 'open',
            seedNodeIds,
            commanderId: userId,
          },
        },
      });

      return viewIncident(tx, incident.id);
    });
  }

  // One transaction (docs/adr/0004): read, update where the version is the
  // one the client read, then write the events.
  update(id: string, userId: string, dto: UpdateIncidentDto) {
    const { version, ...wanted } = dto;

    if (Object.values(wanted).every((value) => value === undefined)) {
      throw validationError({
        body: ['send at least one of status, severity, title and commanderId'],
      });
    }

    return this.prisma.$transaction(async (tx) => {
      const current = await tx.incident.findUnique({ where: { id } });

      if (!current) {
        throw incidentNotFound();
      }

      if (
        wanted.commanderId !== undefined &&
        wanted.commanderId !== current.commanderId
      ) {
        const user = await tx.user.findUnique({
          where: { id: wanted.commanderId },
          select: { id: true },
        });

        if (!user) {
          throw validationError({
            commanderId: ['commanderId is not a user'],
          });
        }
      }

      const events = changedFields(current, wanted);

      // Every value is the current one: nothing to write, not even a version.
      if (events.length === 0) {
        return viewIncident(tx, id);
      }

      // The version is in the WHERE clause, so a change made since the client
      // read the incident matches no row. The second of two simultaneous
      // updates waits for the first one, then finds another version.
      const { count } = await tx.incident.updateMany({
        where: { id, version },
        data: { ...wanted, version: { increment: 1 } },
      });

      if (count === 0) {
        throw new ConflictException(
          {
            message: 'The incident changed since it was read',
            details: await viewIncident(tx, id),
          },
          { errorCode: 'INCIDENT_VERSION_CONFLICT' },
        );
      }

      await tx.incidentEvent.createMany({
        data: events.map((event) => ({
          incidentId: id,
          version: version + 1,
          type: event.type,
          actorId: userId,
          data: event.data,
        })),
      });

      return viewIncident(tx, id);
    });
  }

  async timeline(id: string, query: TimelineQueryDto) {
    const incident = await this.prisma.incident.findUnique({
      where: { id },
      select: { id: true },
    });

    if (!incident) {
      throw incidentNotFound();
    }

    // No entry of v1 concerns a node.
    if (query.nodeId !== undefined) {
      return [];
    }

    const events = await this.prisma.incidentEvent.findMany({
      where: { incidentId: id },
      orderBy: { seq: query.order ?? 'asc' },
      include: WITH_ACTOR,
    });
    const entries = events.map((event) => toTimelineEntry(event));
    const { kind } = query;

    return kind
      ? entries.filter((entry) => kind.includes(entry.kind))
      : entries;
  }

  // A note does not change the incident: no new version, so it never makes
  // another user's update fail.
  addNote(id: string, userId: string, dto: CreateNoteDto) {
    return this.prisma.$transaction(async (tx) => {
      const incident = await tx.incident.findUnique({
        where: { id },
        select: { version: true },
      });

      if (!incident) {
        throw incidentNotFound();
      }

      const event = await tx.incidentEvent.create({
        data: {
          incidentId: id,
          version: incident.version,
          type: 'note_added',
          actorId: userId,
          data: { body: dto.body },
        },
        include: WITH_ACTOR,
      });

      return toTimelineEntry(event);
    });
  }
}
