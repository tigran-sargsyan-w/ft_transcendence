# Incidents API

How the browser opens, reads and updates incidents, and reads their timeline. This is the first version (v1) of the incident room: incidents are opened by hand, every action is written in a timeline that is only ever added to, and two people changing the same incident at the same time cannot overwrite each other. The data model and the reasons are in [ADR 0004](../adr/0004-incident-events-and-topology-history.md); the screens' draft is [pull request #121](https://github.com/tigran-sargsyan-w/ft_transcendence/pull/121). Where this page and the draft differ, this page is what the server answers.

## Status

Implemented in the `incidents` module. Live updates over Socket.IO are not there yet: a screen reloads over REST.

**Every signed-in user sees and changes every incident.** v1 has no organization, participant or role. That is only acceptable for the demo data: the isolation by organization and the permissions will come separately, and then an incident of another organization will answer `404 INCIDENT_NOT_FOUND`.

## Differences from the draft

The server answers differently from the screens' draft on these points:

- Three statuses: there is no `mitigated`, and a request that sends it gets a `400`.
- `seedNodeIds` cannot be changed with `PATCH` (a `400`).
- `PATCH` needs `version`, and ids are UUIDs, not `inc_042`.
- No roles, so no `FORBIDDEN`: any signed-in user can change an incident, and any existing user can be made commander. `INCIDENT_INVALID_COMMANDER` does not exist; an unknown `commanderId` is a `VALIDATION_ERROR`.
- The fields v1 does not fill, and the new `action` values, are listed below.

## Routes

All of them are under `/api/v1`, need a session ([Authentication and users](./auth-and-users.md)) and answer in the `data` / `error` envelopes ([API conventions](./api-conventions.md)). An id in the path that is not a UUID is a `400 BAD_REQUEST`.

| Method | Path | Body or query | Success |
|--------|------|---------------|---------|
| `GET` | `/incidents` | query: `status`, `severity`, `environmentId`, all optional | `200`, a list of `IncidentSummary`, the newest first |
| `POST` | `/incidents` | `title`, `severity`, `environmentId`, `seedNodeIds` | `201`, an `Incident`; the creator is its commander |
| `GET` | `/incidents/:id` | none | `200`, an `Incident` |
| `PATCH` | `/incidents/:id` | `version`, and at least one of `status`, `severity`, `title`, `commanderId` | `200`, the `Incident` |
| `GET` | `/incidents/:id/timeline` | query: `kind`, `order`, `nodeId`, all optional | `200`, a list of timeline entries |
| `POST` | `/incidents/:id/notes` | `body` | `201`, the new timeline entry |

An unknown field, or a value out of the limits below, is a `400 VALIDATION_ERROR` (`details` names the fields).

| Field | Rule |
|-------|------|
| `title` | 1 to 200 characters once trimmed (counted in UTF-16 units: an emoji counts for 2) |
| `severity` | `sev1` (the most severe), `sev2`, `sev3`, `sev4` |
| `status` | `open`, `investigating`, `resolved`. Any change from one to another is allowed, going back and reopening included |
| `environmentId` | The environment the server follows, as in the [Analyses API](./analysis-api.md): `TOPOLOGY_UNKNOWN_ENVIRONMENT` (404) for another one, `TOPOLOGY_UNAVAILABLE` (503) while there is no topology yet |
| `seedNodeIds` | 1 to 50 ids of nodes of that topology, checked as in the analyses (`details.seedNodeIds`). A repeated id is ignored. Set when the incident is opened and not editable in v1 |
| `commanderId` | The id of a user; any existing user in v1 (`details.commanderId` otherwise) |
| `version` | An integer from 1 to 2147483647: the `version` of the incident as the client last read it |
| `body` | 1 to 2000 characters once trimmed, plain text |
| `kind` | One or more of `infra_event`, `status_change`, `severity_change`, `analysis`, `action`, `note`, separated by commas |
| `order` | `asc` (the default, oldest first) or `desc` |

Texts (title, note, `environmentId` filter) cannot hold a NUL character or a lone surrogate (half of an emoji): PostgreSQL would refuse them.

## The incident

```json
{
  "id": "0b0f…",
  "title": "API down",
  "severity": "sev2",
  "status": "investigating",
  "environmentId": "env_local_compose",
  "startedAt": "2026-11-12T10:00:00.000Z",
  "resolvedAt": null,
  "commander": { "id": "6c1e…", "displayName": "Ada", "avatarUrl": null },
  "seedNodeIds": ["svc:demo:db"],
  "version": 2,
  "participants": [],
  "runbook": [],
  "statusHistory": [
    { "status": "open", "at": "2026-11-12T10:00:00.000Z" },
    { "status": "investigating", "at": "2026-11-12T10:02:00.000Z" }
  ],
  "myRole": null,
  "latestAnalysis": null
}
```

The list holds only the fields down to `seedNodeIds`, without `version` and what follows: the `IncidentSummary` of the draft. A user is only `{ id, displayName, avatarUrl }`, never an email.

What v1 does not fill, and what a screen should read it as:

- `participants` and `runbook` are empty lists; `myRole` is `null`, since there are no roles.
- `latestAnalysis` is `null` and means "not available", not "loading": nothing computes it in v1. The impact panel can call the [Analyses API](./analysis-api.md) on demand.
- `startedAt` is the creation date. `resolvedAt` is the date of the last change to `resolved` while the incident is resolved, and `null` otherwise (an incident that is reopened has none).

## Two people changing the same incident

`version` starts at 1 and grows by 1 each time an update changes something. `PATCH` sends the `version` the client read; if the incident has changed since, nothing is written and the answer is:

```json
{
  "error": {
    "code": "INCIDENT_VERSION_CONFLICT",
    "message": "The incident changed since it was read",
    "details": { "id": "0b0f…", "version": 3, "status": "resolved" }
  }
}
```

`details` is the current `Incident`, in full (shortened here). The screen shows it and lets the user decide again. Two simultaneous updates that ask for different values are refused the same way: one passes, the other gets this `409`. (If they ask for the same values, the second may find nothing left to change and get a `200`.) Two users editing different fields of the same incident also get the `409`, for the second one.

A `PATCH` that asks for the values the incident already has changes nothing: `200`, the same `version`, no event, whatever `version` it sent. A note does not change the incident either: it keeps the `version`, so adding a note never makes another user's update fail.

## Timeline

Every change is an event that is only ever added: opening the incident, then one event for each changed field of an update (title, severity, status, commander, in that order), and each note. The events of one update all carry the new `version`.

```json
{
  "id": "5d2a…",
  "seq": 12,
  "incidentId": "0b0f…",
  "kind": "status_change",
  "occurredAt": "2026-11-12T10:02:00.000Z",
  "summary": "Ada changed the status from open to investigating",
  "author": { "id": "6c1e…", "displayName": "Ada", "avatarUrl": null },
  "nodeIds": [],
  "data": { "kind": "status_change", "from": "open", "to": "investigating" }
}
```

`seq` sorts the timeline; it follows the order of insertion and has gaps. The list is not paginated.

| Event | `kind` | `data` |
|-------|--------|--------|
| Status changed | `status_change` | `{ kind, from, to }` |
| Severity changed | `severity_change` | `{ kind, from, to }` |
| Note | `note` | `{ kind }`; the text is in `summary` |
| Incident opened | `action` | `{ kind: "action", action: "created" }` |
| Title changed | `action` | `{ kind: "action", action: "title_changed" }` |
| Commander changed | `action` | `{ kind: "action", action: "role_changed", userId }`, the new commander |

`created` and `title_changed` are not in the draft's list of actions: a screen that does not know an `action` shows its `summary`. No entry is an `infra_event` or an `analysis` in v1, so filtering on them gives an empty list. No entry has nodes (`nodeIds` is empty), so `nodeId` is accepted and always gives an empty list.

## Errors

Shape and codes: [API conventions](./api-conventions.md).

| Status | Code | When |
|--------|------|------|
| 400 | `VALIDATION_ERROR` | The request breaks the table above, or a `PATCH` has no field to change (`details.body`) |
| 401 | `UNAUTHORIZED` | No session |
| 404 | `INCIDENT_NOT_FOUND` | No incident has that id |
| 404 | `TOPOLOGY_UNKNOWN_ENVIRONMENT` | `POST` for an environment the server does not follow |
| 409 | `INCIDENT_VERSION_CONFLICT` | The `version` is not the current one; `details` is the current incident |
| 503 | `TOPOLOGY_UNAVAILABLE` | `POST` before the server has any topology |

## Limits of v1

- No participants, runbook, saved analysis, "compromised" mark, invitations or chat; no live updates (Socket.IO comes next).
- No pagination, no purge.
- No idempotency: a `POST` that is sent again after a lost answer opens a second incident.
- An incident is opened against the topology the server knows at that moment (`epoch` and `revision` are stored for the replay); it cannot be opened while the topology is unknown.
