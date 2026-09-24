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
| `SERVICE_UNAVAILABLE` | 503 | `GET /health` cannot reach a dependency | `{ "<dependency>": "unavailable" }` |
| `INTERNAL_ERROR` | 500 | Unexpected server error. The message is always `Internal server error`; the real error is only logged | none |
| `BAD_REQUEST` | 400 | Malformed request, such as invalid JSON | none |
| `NOT_FOUND` | 404 | Unknown route | none |
| `PAYLOAD_TOO_LARGE` | 413 | Request body over the size limit | none |

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
