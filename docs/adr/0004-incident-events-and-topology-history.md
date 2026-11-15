# ADR 0004: Incident events and topology history, append-only

- Status: Proposed
- Date: 2026-11-05

## Context

The incident response room v1 lets users open incidents by hand, follow them on a timeline and see changes live. Incident replay will come later, but what it needs must be stored from the start. The subject requires that several users can act at the same time without race conditions or corrupted data.

Today nothing about incidents is stored: Prisma has only `User` and `Session`. The live topology lives in memory ([`topology-state.ts`](../../backend/src/modules/topology/topology-state.ts)). Each run of Nest has its own `epoch`, and `revision` starts at 1 in each epoch ([realtime topology protocol](../architecture/realtime-topology.md#revision-and-epoch)), so `(epoch, revision)` already names one topology state. `TopologyService.changes$` publishes a `snapshot` each time one is loaded (at start, and again each time Nest resynchronizes with the collector) and a `delta` each time the graph changes; the realtime gateway relays them.

## Considered options

### Topology history

- **A. Store the published messages:** every snapshot and delta, keyed by environment, epoch and revision. A replay starts from a snapshot and applies the deltas after it. Small rows; replay reads more deltas as an epoch gets longer.
- **B. Store the whole graph at each change:** the simplest replay, but the full graph is written for every change.
- **C. A, plus a full snapshot every N changes:** faster replay, more code. Only useful at a larger scale.

### History after a lost write

- **A. A fresh snapshot once writes work again:** the replay restarts from it. The revisions lost before it cannot be rebuilt.
- **B. Mark the affected history as incomplete:** an extra column or table, and the replay still needs a later snapshot to be useful.

### Concurrent updates

- **A. Optimistic locking:** each incident has a `version`; an update says which version it read and is refused if the incident changed meanwhile.
- **B. Row lock (`SELECT … FOR UPDATE`):** writes are serialized, but a user acting on an outdated view still overwrites a change without knowing it.
- **C. Last write wins:** a concurrent change is silently lost. Rejected.

### Append-only guarantee

- **A. In the application:** no code path updates or deletes events or history.
- **B. Also in PostgreSQL:** the backend role gets only `SELECT` and `INSERT` on these tables.

## Decision

Topology history A with a fresh snapshot after a lost write (A), concurrent updates A, append-only A (B is decided with the database roles, see below).

**`incidents`:** `id` (UUID), `environmentId`, `title`, `severity` (`sev1`, the most severe, to `sev4`), `status` (`open`, `investigating`, `resolved`; any change from one to another is allowed, going back and reopening included), `seedNodeIds` (1 to 50 node ids of the topology of `environmentId`: each id is checked as in the analyses, see `checkSeeds` in [`analysis.service.ts`](../../backend/src/modules/analysis/analysis.service.ts), the maximum of 50 is the one of `AnalyzeDto`, and "at least one" is specific to incidents; set when the incident is created and not editable in v1), `createdById` and `commanderId` (users; in v1 any existing user, checked before the write, the error code belongs to the API contract), `openedAtEpoch` and `openedAtRevision` (the topology the incident was opened against: an incident is created only while the topology of its environment is known, otherwise the analyses' errors `TOPOLOGY_UNAVAILABLE` or `TOPOLOGY_UNKNOWN_ENVIRONMENT` apply), `version` (starts at 1), `createdAt`, `updatedAt`.

**`incident_events`** (the timeline): `id`, `seq`, `incidentId`, `version` (the incident version the event belongs to; for a note, the version read just before it), `type` (`created`, `title_changed`, `severity_changed`, `status_changed`, `commander_changed`, `note_added`), `actorId` (user), `data`, `createdAt`. A request that changes the incident (create it, or edit the title, severity, status or commander) writes one new version and one event for each changed field, all with that version; a request that changes nothing writes nothing and keeps the version. `data` holds `{ "<field>": { "from": …, "to": … } }` for that field; `created` holds every initial value; `note_added` holds the text. A note does not change the incident: it writes no `incidents` row and keeps the current `version`, so it never makes another user's update fail with a `409`. `seq` is an auto-incremented integer that sorts the timeline. It follows the order of insertion, not necessarily the order of commit (a transaction can take its number and commit after another one), and it has gaps when a transaction aborts ([PostgreSQL 18, sequence functions](https://www.postgresql.org/docs/18/functions-sequence.html)). For the events that change the incident it is also the order of the versions, because the second `UPDATE` waits for the first transaction (see below). `(incidentId, seq)` is indexed for the timeline, since PostgreSQL does not index a foreign key by itself. The relations of this table and of `incidents` are declared with `onDelete: Restrict` and `onUpdate: Restrict`, because Prisma adds `ON UPDATE CASCADE` by default (as in the `sessions` migration).

**`topology_history`:** `id`, `environmentId`, `epoch`, `revision`, `kind` (`snapshot` or `delta`), `payload` (the `TopologySnapshot` or `TopologyDelta`), `recordedAt` (set by Nest when the message is published; for a snapshot written after a lost write, when it was read from the state). `(environmentId, epoch, revision)` is unique, which also serves the reads of the last snapshot at or below a revision and of the deltas in revision order. `(environmentId, recordedAt)` is indexed, because the replay looks for the last row recorded before a time (a tie within one millisecond goes to the higher revision). A subscriber to `TopologyService.changes$` writes it, one message at a time and in order. A delta does not name its environment, so it takes the one of the last snapshot; on start the writer also stores the current snapshot, if any (a row already stored is skipped). It never delays or stops the live topology.

**After a lost write:** a failed write is logged (once for the whole outage) and the writer remembers that the history may have a gap. The first write that succeeds afterwards stores a snapshot of the current topology (`TopologyService.snapshot()`) instead of the delta; a snapshot stored normally, such as a resynchronization, ends the gap too. The writer also retries that snapshot after a delay that grows, until it succeeds, so that the gap does not last until the next change. A row already stored is skipped. There is no "incomplete" marker in v1: the revisions lost before that snapshot cannot be rebuilt, and the replay can tell, because two consecutive rows of an epoch whose revisions differ by more than one mark a gap. If Nest restarts or crashes before that snapshot is written, the gap stays open and unmarked (the writer's memory is lost): the replay of that epoch ends at its last stored row.

**Replay (later):** the last row recorded at or before a time T gives the epoch and revision. Start from the last snapshot of that epoch at or below that revision, then apply its deltas in revision order (never in `recordedAt` order). Each delta's `baseRevision` is the revision before it: if the row stored just before it has another revision, or two consecutive rows differ by more than one, there is a gap until the next snapshot.

**Updating an incident**, in one transaction:

1. read the incident; if it does not exist, answer `404` (its values are the "from" of the event);
2. update it where `id` and `version` match what the client read, in one `UPDATE` statement that also adds 1 to `version`;
3. if no row matched, the incident changed meanwhile: roll back and answer `409` with the code `INCIDENT_VERSION_CONFLICT` and the current incident in `error.details` (the code joins the API conventions with the API contract);
4. insert the event with the new version.

Under PostgreSQL's default isolation level (Read Committed), a second `UPDATE` of the same row waits for the first transaction; if it commits, the `WHERE` clause is checked again on the new row, so the old `version` no longer matches. Creating an incident writes version 1 and its `created` event in the same transaction. Adding a note is also one transaction: read the incident (`404` if it does not exist), then insert the `note_added` event with the version just read.

**Live order.** Live updates go to the room `incident:<incidentId>` once the transaction is committed (there is no room per environment for incidents in v1). Two committed updates can still reach a client in the other order, since each one is sent after its own transaction. So `incident:updated` carries the incident `version`: a client ignores a version that is not newer than its own, and reloads the incident over REST when the version is more than one ahead (a message was missed or came late). A timeline entry, sent on its own, carries its `id` and `seq`: a client drops one it already has and sorts by `seq`. A client joins the room first and then reads the incident and its whole timeline over REST, at the first load and after every reconnection, so that nothing sent in between is missed; it applies the same "newer only" rule to what REST returns and merges timeline entries by `id`. The event names and payloads belong to the API contract.

**Not in v1:** no purge (retention is decided before the replay screen); the collector status (`topology:status`) is not stored.

**Database privileges:** which PostgreSQL role the backend uses is still an open decision of [ADR 0003](./0003-vault-secret-delivery.md) ("Open integration decisions"). The plan discussed by the team on 2026-11-05 is default privileges for the tables that the migration role creates. Whether the backend role also loses `UPDATE` and `DELETE` on `incident_events` and `topology_history` (option B) is decided with that work.

## Consequences

### Positive

- The live protocol does not change: the history is a copy of what clients already receive.
- A replay can rebuild any revision whose deltas since the last snapshot are all stored, and an incident points to the one it was opened against.
- A concurrent change is never lost: the second writer gets a `409` and the current state.
- The timeline is also an audit trail: who changed what, in order.

### Negative / trade-offs

- One database write per published snapshot or delta; after a delta, Nest waits 300 ms (`batchWindowMs`) before reading the next batch, so at most about three deltas per second. A write lost while the database is down leaves a gap in the replay, which ends at the snapshot written once the database answers again; the revisions in between cannot be rebuilt, and if Nest restarts first, the gap stays unmarked.
- Without a purge, the history only grows.
- Replaying far into a long epoch reads many deltas; option C can be added later without changing the model.
- Every update request must send the version it read. Two users editing different fields of the same incident still get a `409` for the second one, and a request retried after a lost answer gets a `409` that shows its own change.
- No idempotency in v1: a create retried after a lost answer makes a second incident.
- The timeline order (`seq`) is the order of insertion: two notes sent at the same moment can be numbered in a different order than they were committed. A timeline does not need more.
- A client that has not joined the incident room receives nothing about it: a new incident shows up at the next load of the list. A crash between a commit and the send loses that message until the next update or reconnection.
- The origin of an incident (`seedNodeIds`) cannot be changed in v1, and an incident cannot be created while the topology of its environment is unknown.
- A replay cannot show when the topology was stale, nor the time between two runs of Nest.
- A user who appears in a timeline cannot be deleted (no account deletion exists today).
- This assumes one backend instance: two instances would each publish their own epoch at the same time.

## Related

- Issue #119 (incident room v1)
- Pull request #121 (incident room contract, a draft from the frontend): to be aligned with this ADR
- [ADR 0003](./0003-vault-secret-delivery.md) (database roles)
- [Realtime topology protocol](../architecture/realtime-topology.md)
- [Graph contract](../architecture/graph-contract.md)
- [API conventions](../architecture/api-conventions.md)
- [PostgreSQL 18, Read Committed isolation level](https://www.postgresql.org/docs/18/transaction-iso.html#XACT-READ-COMMITTED)
