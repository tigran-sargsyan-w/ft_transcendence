# Authentication + Standard User Management — Postman demo (v1.2)

**Branch:** `feat/standard-user-management`  
**Collection:** `tests/postman/user-management.postman_collection.json`  
**Target:** Authorized local development instance only (`https://transcendence.test:8443`).

## Quick start

1. Import `user-management.postman_collection.json` into Postman. No backend or database reset is needed.
2. In **Collection → Variables**, verify two values:
   - `base_url` = `https://transcendence.test:8443`
   - **`fixtures_dir` = `tests/postman/fixtures`** (the only fixture setting)
3. Set the **Postman working directory to the repository root** (`ft_transcendence/`), so `tests/postman/fixtures/...` resolves to real files. Files must be present on the machine/runtime **where Postman sends requests**. The VS Code extension and desktop runner can resolve filesystem paths differently; verify their actual working directory. If you cannot set the working directory, edit **only `fixtures_dir`** to an accessible absolute folder path (if your runner permits it). Postman may restrict reading files outside its working directory; do not enable unrestricted file access unless you trust the collection and local paths.
4. Run folders `00` → `08` **sequentially** in Collection Runner. Ensure certificate verification remains enabled with the local trusted CA.
5. Check Runner assertions and Postman Console, especially folder `06`: **no `file load error`**. On authorized requests, the actual Cookie header must have **exactly one `sid`**.

### One folder variable controls every avatar upload

The collection contains **only these form-data file sources**, composed from one variable:

| Test requests | Resolved file source |
|---|---|
| `06.1c`, `06.2`, `06.7`, `06.8` | `{{fixtures_dir}}/um-avatar.png` |
| `06.5` | `{{fixtures_dir}}/um-avatar-replacement.jpg` |
| `06.6` | `{{fixtures_dir}}/um-invalid.txt` |

The three fixture files are bundled in `tests/postman/fixtures/`. **You should not need to select files in six different requests** if your Postman runner supports collection-variable expansion in `formdata.src`. This is supported by some Postman/Newman setups, but **was not verified live in your VS Code runner**. If the extension cannot expand `{{fixtures_dir}}` in multipart file paths, that is a Postman extension limitation, not evidence of an API bug; use Postman Desktop/Newman with a configured working directory, or select the files manually as a fallback.

### Why other variables start blank

This retains the **original v1.0 behavior**: `00.1` generates a fresh `run_id`, demo password, and Ada/Bob/Carol emails and display names. Registration stores UUIDs; login stores separate `sid` cookies. The application has no public endpoint to delete demo accounts, so using the same hardcoded emails for every run would make subsequent runs fail with `409 EMAIL_ALREADY_EXISTS`. Only the fixture *directory* is a static user-editable setting.

### Safety and scope

- 104 requests cover registration, login, `/auth/me`, profile updates, public search, friendship request/accept/decline/cancel/remove, avatar upload/read/delete, HTTP presence snapshot, logout/session revocation, and expected validation/auth errors.
- Cookie jar is disabled on individual requests and at collection level, avoiding Postman's auto-appended duplicate `sid` cookies. Each user is identified by one explicit cookie captured from `Set-Cookie`.
- Every full run leaves **three new demo accounts in the local dev PostgreSQL database**. Temporary friendship and avatar changes are cleaned up by the flow; real user deletion is not supported.
- **Do not use production**. Never share raw `sid` values or passwords from logs or commit Postman Current values after running the collection.
- A bare HTTP Engine.IO handshake is **not** a real authenticated Socket.IO connection; `presence:changed` transitions need a separate Socket.IO test client. Rate-limit stress, token expiry, and extensive malicious file fuzzing remain outside this demo suite.

**Note:** The preceding v1.1 run confirmed that cookie isolation worked in the local Postman environment, but file uploads could not find their fixtures. This v1.2 adds the single-directory variable; the user’s Postman runtime must verify that file-source interpolation works before this suite is considered fully green.
