# Authentication and User Contract

This document describes expected behavior, not a concrete framework or session technology.

## Mandatory foundation

The application must support secure user registration and login with email and password.

### Registration

- Email is required and must be validated.
- Email identity must be unique according to the final normalization rule.
- Passwords must never be stored in plaintext.
- Backend validation is authoritative even if the frontend also validates.
- Registration failures return structured, non-sensitive errors.

### Login

- Login accepts the chosen user identifier and password.
- Credentials are verified server-side.
- Invalid credentials must not expose password hashes or internal implementation details.
- Successful login establishes an authenticated context using a mechanism selected later.

### Logout

Logout invalidates or ends the current authenticated context according to the future session/token design.

### Current user

Authenticated clients must have a defined way to retrieve the current user's public/application-safe identity and profile data.

## Password handling

The final implementation must use an appropriate salted password-hashing algorithm. The concrete library and algorithm parameters will be selected with the backend stack and recorded in an ADR if the decision is significant.

## Conceptual user data

The minimal conceptual model currently includes:

- stable user identifier
- email
- password hash
- creation/update timestamps

Additional profile fields should be introduced only when product requirements are known.

## Explicitly deferred

These are optional/future decisions and are not part of this foundation contract:

- OAuth
- 2FA
- refresh-token design
- session-cookie vs token authentication
- roles/permissions beyond what the product requires
- avatars/friends/profile expansion
