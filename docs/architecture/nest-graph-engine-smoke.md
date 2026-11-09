# Nest ↔ Graph Engine smoke

Manual checks that the live analyses path works end to end:

Compose → topology → Nest `POST /api/v1/environments/{id}/analyses` → Graph Engine → results + freshness.
