# Standard User Management — Backend API v1 (Draft)

**Status:** Proposed contract for team review; implementation in progress on `feat/standard-user-management`.
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

## Proposed new HTTP endpoints

All require a valid session. These are **proposed routes, not existing routes**.

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/v1/users?search=...&limit=20` | Find users by public display name; omit self; cap results |
| POST | `/api/v1/users/me/avatar` | Upload/replace JPEG, PNG or WebP; server-generated stored filename |
| DELETE | `/api/v1/users/me/avatar` | Remove avatar, revert to default fallback |
| GET | `/api/v1/users/:id/avatar` | Serve stored image (raw bytes, not JSON) |
| GET | `/api/v1/users/me/friends` | Accepted friends, with online status |
| GET | `/api/v1/users/me/friend-requests` | Incoming and outgoing pending requests |
| POST | `/api/v1/users/me/friend-requests` | Create request: `{targetUserId: UUID}` |
| POST | `/api/v1/users/me/friend-requests/:id/accept` | Accept incoming request |
| DELETE | `/api/v1/users/me/friend-requests/:id` | Cancel outgoing or decline incoming request |
| DELETE | `/api/v1/users/me/friends/:userId` | Remove accepted friendship |

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

1. Review this contract with backend owner Nathan before finalizing behavior/API.
2. Prisma friendship schema + migration; model validation.
3. Friends HTTP service/controllers + unit/E2E tests.
4. Avatar processing/storage + tests.
5. Presence tracking and friend-only Socket.IO events + tests.
6. Frontend after PR #136 merges; verify with two test accounts end-to-end.

**Note:** This document is a draft. It is not an assertion that any new route already works.
