# ADR 0002: Server-side sessions with an opaque cookie

- Status: Proposed
- Date: 2026-09-28

## Context

[Authentication and users](../architecture/auth-and-users.md) requires login, logout, a session and a current-user endpoint; registration already exists. The subject requires hashed and salted passwords, backend validation of every input and HTTPS (terminated by Nginx). The realtime gateway (Socket.IO, same origin) will need to authenticate its handshake with the same mechanism.

## Considered options

### A. Session stored in the database, opaque token in a cookie

Real logout and revocation: deleting the row ends the session. The browser sends the cookie on its own, including during the Socket.IO handshake. Costs one database lookup per authenticated request.

### B. JWT in a cookie

Stateless, no lookup. Logout and revocation need a server-side deny-list, which brings back the state we wanted to avoid.

### C. JWT in `localStorage`

Readable by any script on the page, so one XSS leaks the credential. Rejected.

## Decision

Option A.

- **Token:** 32 random bytes (`crypto.randomBytes`). Only its SHA-256 is stored (`sessions.tokenHash`), so a database leak gives no usable session.
- **Cookie:** `sid`, `HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`, `Max-Age` equal to the session lifetime.
- **Lifecycle:** a new session at each login; logout deletes it; an expired session is refused and deleted when read. Lifetime 7 days (`SESSION_TTL_HOURS`), no sliding renewal for now.
- **Passwords:** argon2id (`argon2` package), already used by registration.
- **Brute force:** login attempts are rate-limited per IP.
- **CSRF:**
  - `SameSite=Lax`: the cookie is not sent on cross-site `POST`, which protects every state-changing route that needs a session;
  - no `GET` route changes state;
  - the backend only parses JSON bodies (the `urlencoded` parser is disabled), so a cross-site HTML form cannot post a usable body to `login` (login CSRF).
- **Protected by default:** a global guard requires a session; routes that must stay open (health, register, login) are marked public.

## Consequences

### Positive

- Logout and revocation are immediate.
- The Socket.IO handshake reuses the cookie with no extra token exchange.
- 2FA or OAuth could later plug into the same session.

### Negative / trade-offs

- One database query per authenticated request.
- Expired rows are only deleted when read; unused ones stay until a cleanup job exists.
- The per-IP rate limit needs `trust proxy` once Nginx is in front, otherwise every client shares the proxy's IP.

## Related

- Issue #46
- [API conventions](../architecture/api-conventions.md)
- [Security baseline](../architecture/security-baseline.md)
