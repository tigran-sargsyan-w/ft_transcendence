# Security Baseline

These rules apply regardless of the final product concept or technology stack.

## Credentials and secrets

- Never store passwords in plaintext.
- Never commit real secrets, tokens, API keys, private keys, or credentials.
- Local secrets belong in ignored environment/configuration files.
- Keep `.env.example` free of real secret values.
- Secret rotation must not require changing business logic.

## Validation and authorization

- Validate untrusted input on the backend even when frontend validation exists.
- Authorization decisions are enforced server-side.
- Never trust client-supplied ownership, role, score, permission, or identity claims without verification.
- Use allow-lists and explicit validation where practical.

## Transport

Connections from browsers, scripts, or external APIs to the backend must use HTTPS in the final deployed application. Internal container/service communication may follow the deployment design while preserving the project's security requirements.

## Authentication

- Authentication failures should avoid leaking sensitive implementation details.
- Password verification uses a suitable password-hashing function rather than reversible encryption.
- Session/token credentials must be treated as secrets.
- Sensitive authentication endpoints should be designed with rate limiting/brute-force resistance in mind.

## Logging

Do not log:

- plaintext passwords
- password hashes
- authentication tokens/session secrets
- API keys
- private keys
- full secret-bearing environment variables

Security-relevant events should be loggable without exposing the protected value itself.

## Data access

- Apply least privilege when configuring application/database/service credentials.
- Keep privileged administrative functionality separate from normal user capabilities.
- Database access must not bypass module/business authorization rules merely because the caller can reach a repository.

## Future security modules

Advanced infrastructure such as WAF/ModSecurity and HashiCorp Vault can strengthen this baseline later. They complement rather than replace secure application design.
