# Standard User Management — Backend API v1 (Draft)

**Status:** Friends API implemented; avatar, presence and user-search contracts proposed for team review on `feat/standard-user-management`.
**Source:** `en.subject.pdf`, IV.3 User Management (Major: 2 points).
**Related:** `docs/architecture/auth-and-users.md`, `docs/architecture/api-conventions.md`.

## Scope and requirements

- Signed-in users can edit their own profile information.
- Users can upload an avatar and have a default avatar if none was uploaded.
- Users can add other users as friends and see whether friends are online.
- Users have a profile page displaying their information.

The existing secure registration/login and session handling are retained; advanced roles, OAuth, organizations and chat are out of scope. The frontend profile/friends pages will be integrated after the independent frontend PR #136.

## Existing interfaces (do not duplicate)

- `POST /api/v1/auth/register`; `POST /api/v1/auth/login`; `POST /api/v1/auth/logout`; `GET /api/v1/auth/me`.
- `PATCH /api/v1/users/me`: update own `displayName` (1–64 chars).
- `GET /api/v1/users/:id`: `{ id, displayName, avatarUrl }` with no email.
- `User.avatarUrl` is nullable and defaults to `null`.
- A global session guard protects new endpoints by default. Success responses use `{data:...}`, errors use `{error:...}`.

## Friends API — implemented

All endpoints below require a valid HTTP session and use the existing `/api/v1` prefix.
Success JSON is wrapped as `{ "data": ... }`; error responses use `{ "error": ... }`.
Presence is **not yet implemented**; the friends list does not pretend an online
status is available until the separate presence stage is finished.

| Method | Route | Success | Purpose |
|---|---|---|---|
| GET | `/api/v1/users/me/friends` | 200 | Accepted friends and acceptance times |
| GET | `/api/v1/users/me/friend-requests` | 200 | Pending incoming/outgoing requests |
| POST | `/api/v1/users/me/friend-requests` | 201 | Send a request; JSON `{ "targetUserId": "UUID" }` |
| POST | `/api/v1/users/me/friend-requests/:id/accept` | 200 | Recipient accepts a pending request |
| DELETE | `/api/v1/users/me/friend-requests/:id` | 204 | Sender cancels, or recipient declines |
| DELETE | `/api/v1/users/me/friends/:userId` | 204 | Either user removes an accepted friendship |

The accepted friends response is a list of
`{ id: friendshipId, user: { id, displayName, avatarUrl }, friendsSince }`.
The pending requests response has `{ incoming: [], outgoing: [] }`; each entry
contains `{ id, status: "pending", direction, user, createdAt }`.
Accepting yields `{ id, status: "accepted", user, acceptedAt }`.
The `user` object always contains *only* the public profile fields, never email.
Timestamps are serialized as ISO 8601 UTC strings.

Writes are limited to 30 requests per minute per endpoint/client. Duplicate,
opposite-direction, or already-accepted pairs return 409 `FRIENDSHIP_ALREADY_EXISTS`.
An unknown target user returns 404 `USER_NOT_FOUND`. Inaccessible, expired,
or already-accepted friend requests return 404 `FRIEND_REQUEST_NOT_FOUND`.
Removing a missing or non-accepted friendship returns 404
`FRIENDSHIP_NOT_FOUND`. Invalid UUIDs and self-friending are rejected with 400.

## Future HTTP endpoints — not implemented in this task

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/v1/users?search=...&limit=20` | Search users by public name |
| POST | `/api/v1/users/me/avatar` | Upload/replace an avatar |
| DELETE | `/api/v1/users/me/avatar` | Revert to default avatar |
| GET | `/api/v1/users/:id/avatar` | Serve stored avatar image |

### Friendship behavior

**Proposal requiring backend-owner agreement:** A friendship request requires acceptance (rather than silently adding an unrelated account). Store one canonical unordered user pair; reject self-friending, duplicates and unauthorized accept/cancel attempts. Use database constraints and transactions for concurrency, and do not expose private email addresses.

A friendship record identifies the two users (`userLowId`, `userHighId`), the initiating user (`requestedById`), its `pending` or `accepted` status, and timestamps. The migration should enforce ordering, membership, state consistency, and uniqueness. The relationship is symmetric once accepted.

### Presence behavior

- Online means at least one currently connected authenticated Socket.IO client, not simply an unexpired HTTP session.
- The existing gateway authenticates socket handshakes, and disconnects sockets when their session ends.
- Track socket counts per user (handle multiple browser tabs), read snapshots through the friends API, and emit `presence:changed` **only to authorized friends**.
- Initial implementation is single-backend-process only; distributed presence requires a shared store/pub-sub.

### Avatar safety

- Allow only actual decodable JPEG, PNG and WebP (check file signatures and decode/re-encode; do not trust MIME/filename).
- Proposed limit: 2 MiB and 1024×1024 dimensions; strip EXIF/metadata.
- Use opaque server-generated filenames in persistent storage, server-controlled URL and secure response headers.
- Reject malformed, oversized or harmful content; clean up replaced and orphaned files.
- Check upload limits through the actual Nginx/WAF HTTPS path.
- Render a default avatar/initials when `avatarUrl === null`.

## API and security conventions

- Backend validates all requests; use UUID parsing and an allowlist for mutation DTOs.
- Own-profile mutation only; `GET /users/:id` never reveals email or password hash.
- Reuse error envelope and HTTP conventions from `api-conventions.md`; extend with friendship/avatar error codes when endpoints are implemented.
- Do not expose presence to strangers. Do not derive identity or friendship authorization from client-supplied IDs.
- Keep tests for duplicate/self requests, concurrent friendship updates, role restrictions, multi-tab presence, expired/logout sessions, malformed uploads, and storage persistence.

## Implementation stages

1. Review the friendship confirmation semantics with backend owner Nathan.
2. Friendship schema, SQL migration and Friends HTTP service/controller are implemented.
3. Friends E2E tests are committed; run them against migrated PostgreSQL before review.
4. Avatar processing/storage + tests: later task.
5. Presence tracking and friend-only Socket.IO events + tests: later task.
6. Frontend after PR #136 merges; verify with two test accounts end-to-end.

**Note:** The Friends API is coded but has not yet passed a real PostgreSQL E2E run in this branch; the other proposed endpoints are not implemented.
