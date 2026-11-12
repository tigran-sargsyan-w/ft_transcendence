# API Conventions

These conventions define a stable direction for frontend/backend contracts without selecting a backend framework.

## Base path and versioning

HTTP API endpoints should use an explicit version prefix when the application API is introduced:

```text
/api/v1/...
```

## Naming

- Use lowercase resource names.
- Prefer plural nouns for collections, for example `/users`.
- Use path parameters for resource identity, for example `/users/{id}`.
- Keep actions in HTTP semantics where practical instead of embedding verbs in every path.

## Success responses

For endpoints returning data, prefer a predictable envelope:

```json
{
  "data": {}
}
```

List endpoints may later add `meta` for pagination/filter information.

## Error responses

Errors should have a stable machine-readable code and a user/developer-readable message:

```json
{
  "error": {
    "code": "AUTH_INVALID_CREDENTIALS",
    "message": "Invalid credentials"
  }
}
```

Do not expose stack traces, secrets, SQL details, password hashes, or internal infrastructure information to clients.

`error.details` is optional. It is only present for the codes below that define it, and its shape depends on the code.

## Error codes

The `code` is stable and machine-readable. An error that defines its own code uses it; any other error uses the name of its HTTP status (`404` gives `NOT_FOUND`). A change that adds a code adds it to this table.

| Code | Status | When | `details` |
|------|--------|------|-----------|
| `VALIDATION_ERROR` | 400 | Invalid body, or a property the endpoint does not accept | `{ "<field>": ["<message>"] }` |
| `EMAIL_ALREADY_EXISTS` | 409 | Registration with an email already in use | none |
| `AUTH_INVALID_CREDENTIALS` | 401 | Login with an unknown email or a wrong password (same answer for both) | none |
| `UNAUTHORIZED` | 401 | Missing, unknown or expired session on a protected route | none |
| `TOO_MANY_REQUESTS` | 429 | Too many login attempts, or too many analyses | none |
| `SERVICE_UNAVAILABLE` | 503 | `GET /health` cannot reach a dependency, cannot read the migrations of the repository (`{ "migrations": "unavailable" }`), or the database has migrations that are not applied | `{ "<dependency>": "unavailable" }`, or `{ "migrations": "pending" }` |
| `INTERNAL_ERROR` | 500 | Unexpected server error. The message is always `Internal server error`; the real error is only logged | none |
| `BAD_REQUEST` | 400 | Malformed request, such as invalid JSON, or an id in the path that is not a UUID | none |
| `NOT_FOUND` | 404 | Unknown route | none |
| `USER_NOT_FOUND` | 404 | `GET /users/:id` with an id that no user has | none |
| `PAYLOAD_TOO_LARGE` | 413 | Request body over the size limit | none |
| `INCIDENT_NOT_FOUND` | 404 | No incident has that id (routes of `/incidents`) | none |
| `INCIDENT_VERSION_CONFLICT` | 409 | `PATCH /incidents/:id` with a `version` that is not the current one | the current incident |
| `TOPOLOGY_UNKNOWN_ENVIRONMENT` | 404 | An `environmentId` the server does not follow: `topology:subscribe` (in the acknowledgement), the analyses, `POST /incidents` | none |
| `TOPOLOGY_UNAVAILABLE` | 503 | No topology yet: `topology:subscribe` (in the acknowledgement), the analyses, `POST /incidents` | none |
| `TOPOLOGY_NOT_SUBSCRIBED` | — | `topology:resync` from a socket that has not subscribed | none |

A `—` in the status column marks a code that only travels in the acknowledgement of a Socket.IO command, never in an HTTP response (see [Realtime topology protocol](./realtime-topology.md#errors)).

## Validation errors

Field validation errors should be structured rather than returned as an unparseable text blob. A validation failure returns `VALIDATION_ERROR`, with `error.details` mapping each invalid field to the list of its messages:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Request validation failed",
    "details": {
      "email": ["email must be an email"],
      "password": ["password must be longer than or equal to 8 characters"]
    }
  }
}
```

Known limit: only flat request bodies are supported. A nested object that fails validation appears under its top-level field with an empty list (`"address": []`); its inner messages are not reported. This will be extended with the first nested DTO.

## HTTP status expectations

Use standard HTTP semantics consistently, for example:

- `200` successful read/update when a body is returned
- `201` resource created
- `204` successful operation with no response body
- `400` malformed/invalid request
- `401` unauthenticated
- `403` authenticated but not authorized
- `404` resource not found
- `409` state/uniqueness conflict
- `429` rate limit exceeded
- `500` unexpected server failure

## Identifiers and timestamps

- IDs must be stable and treated as opaque by clients.
- Timestamps exchanged through APIs should use a single documented format; ISO 8601 UTC is the default direction unless an ADR chooses otherwise.

## Pagination

When list endpoints require pagination, use one consistent strategy across the project. Cursor vs offset pagination remains undecided until product requirements are clearer.
