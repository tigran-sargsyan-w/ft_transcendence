# Standard User Management — Frontend API Handoff

**Status:** Backend implemented and tested; frontend integration pending.  
**Source branch:** `feat/standard-user-management`  
**API prefix:** `/api/v1` (use the existing same-origin API proxy; local HTTPS: `https://localhost:8443`)

## 1. Authentication and response conventions

- Existing endpoints: `POST /auth/register`, `POST /auth/login`, `GET /auth/me`, `POST /auth/logout`.
- Authentication uses the `sid` **HttpOnly session cookie**. For browser requests, use same-origin URLs and `credentials: 'include'`; **do not** read or store the cookie token in JavaScript.
- All User Management endpoints require an authenticated session.
- JSON success responses are wrapped in `{ "data": ... }`, except `204 No Content` responses. Avatar image downloads return binary `image/webp`, **not JSON**.
- Errors use `{ "error": { "code": "...", "message": "...", "details": ... } }`.

## 2. Profiles and user search

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/users/:id` | Public profile: `{ id, displayName, avatarUrl }` |
| `PATCH` | `/users/me` | Update own profile, JSON `{ "displayName": "New name" }` (1–64 trimmed chars) |
| `GET` | `/users?search=bob&limit=20` | Find users by display name; `search` = 2–64 trimmed chars, `limit` = 1–20 (default 20) |

The search is case-insensitive, excludes the signed-in user, and returns only `{ id, displayName, avatarUrl }` per result — **no email or presence status**. Example:

```json
{ "data": [{ "id": "<user-uuid>", "displayName": "Bob", "avatarUrl": null }] }
```

## 3. Avatar

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/users/me/avatar` | Upload/replace using `multipart/form-data`, **one field named `file`**; returns updated user in `{ data: ... }` (201) |
| `GET` | `/users/:id/avatar` | Returns WebP image bytes (200), or 404 if missing |
| `DELETE` | `/users/me/avatar` | Remove avatar; 204 No Content |

Accepted upload formats: JPEG/PNG/WebP, maximum **2 MiB**, image dimensions up to **4096×4096**. Do not set `Content-Type` manually when using `FormData`; the browser must set the multipart boundary. Upload/delete must originate from the configured trusted application origin.

When an avatar exists, `avatarUrl` is a relative path such as `/api/v1/users/<uuid>/avatar`; resolve it against the application origin. When it is `null`, display a frontend fallback (e.g. initials). After upload/delete, update the local user state.

## 4. Friends and requests

| Method | Endpoint | Purpose |
|---|---|---|
| `GET` | `/users/me/friends` | Accepted friends, including `isOnline` |
| `GET` | `/users/me/friend-requests` | Pending requests, split into `incoming` and `outgoing` |
| `POST` | `/users/me/friend-requests` | Send request: `{ "targetUserId": "<user-uuid>" }` (201) |
| `POST` | `/users/me/friend-requests/:id/accept` | Accept an incoming pending request (200) |
| `DELETE` | `/users/me/friend-requests/:id` | Cancel sent request or decline received request (204) |
| `DELETE` | `/users/me/friends/:userId` | Remove accepted friend by the **other user's UUID** (204) |

`GET /users/me/friends` response:

```json
{
  "data": [
    {
      "id": "<friendship-uuid>",
      "user": { "id": "<friend-uuid>", "displayName": "Bob", "avatarUrl": null },
      "friendsSince": "2026-11-20T20:22:22.350Z",
      "isOnline": false
    }
  ]
}
```

`GET /users/me/friend-requests` response:

```json
{
  "data": {
    "incoming": [{ "id": "<request-uuid>", "status": "pending", "direction": "incoming", "user": { "id": "<user-uuid>", "displayName": "Ada", "avatarUrl": null }, "createdAt": "<ISO timestamp>" }],
    "outgoing": []
  }
}
```

Use the **request ID** for accepting/cancelling requests; use the **friend user's ID** for removal. A pending request is **not** yet a friendship. Refresh the requests/friends lists after mutations.

## 5. Real-time presence (Socket.IO)

- Connect to the **same application origin** through Socket.IO's default `/socket.io/` path, with the existing `sid` cookie (Socket.IO v4). Do not use a plain WebSocket client or manually subscribe to user rooms.
- `isOnline` means **at least one authenticated Socket.IO connection**, not simply being logged in over HTTP.
- The server emits `presence:changed` **only to accepted friends** when online status changes:

```json
{
  "event": "presence:changed",
  "payload": { "userId": "<friend-uuid>", "isOnline": true },
  "timestamp": "<ISO timestamp>"
}
```

- Fetch `GET /users/me/friends` for the initial snapshot and **refetch after Socket.IO reconnect**, since events may have been missed.
- Ordinary final disconnect has a **5-second offline grace period**; logout/session expiry causes immediate offline when no other active session remains.
- Presence is currently supported on **one NestJS backend instance** (no cross-instance synchronization yet).

## 6. Frontend integration checklist

- Load current account with `GET /auth/me`; render profile and update display name.
- Use user search to find people; support sending, accepting, declining and removing friends.
- Show pending requests separately from accepted friends.
- Render avatar URL or fallback initials; upload with `FormData` and update profile state.
- Load the friends snapshot, listen for `presence:changed`, and refetch after reconnect.
- Handle `400` validation errors, `401` expired sessions, `404` missing resources, `409` existing friendship/request and `429` rate limits.

**Implementation detail:** Search is limited to 60 requests/min per client; friendship mutations to 30/min per endpoint/client; avatar mutations to 10/min per endpoint/client. Use a debounced search input to avoid unnecessary requests.

For implementation details, see `docs/architecture/user-management-v1.md` and `docs/architecture/realtime-topology.md` in the same branch.
