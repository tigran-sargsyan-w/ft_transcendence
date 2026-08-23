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

## Validation errors

Field validation errors should be structured rather than returned as an unparseable text blob. The concrete schema can evolve with the chosen validation library, but must remain predictable for the frontend.

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
