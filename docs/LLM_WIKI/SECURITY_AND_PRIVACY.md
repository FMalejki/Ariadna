# Security and privacy

STATUS: PARTIALLY_IMPLEMENTED

## Implemented (with tests in `tests/test_api.py` unless noted)

- **Authentication.** JWT bearer tokens; passwords hashed with scrypt. The admin user is bootstrapped only when `OSINT_ADMIN_PASSWORD` is set. If `OSINT_JWT_SECRET` is unset an ephemeral secret is generated and tokens die on restart.
- **Roles.** viewer < analyst < admin. Viewers read. Analysts run discovery, review, import. Admins configure providers and policies, delete data, read the audit log, edit the wiki.
- **Audit log.** Task approvals/retries, investigation create/notes/export/import, entity merges and reversals, entity/source deletion and correction, match and claim decisions, LLM extraction events, and provider/policy changes are written to `audit_logs` (admin-readable at `GET /audit`). **Not audited:** relationship reviews (recorded as `reviewed_by`/`reviewed_at` on the relationship plus an `analyst_confirmed` evidence row) and wiki writes (recorded as `wiki_revisions` with author and note).
- **Secrets.** Configuration comes from environment variables. `GET /settings` returns a public view without secrets (tested).
- **Untrusted content.** Raw artifacts are served only to analysts or higher, as an attachment with `X-Content-Type-Options: nosniff`; the UI never renders them. Observations store extracted text as data.
- **Prompt injection.** Delimited-data prompts and strict output validation (see [LLM_POLICY](LLM_POLICY.md)).
- **Input validation.** Seeds and identifiers are validated; invalid input is a 422. Wiki write paths are restricted to `.md` files inside `docs/LLM_WIKI`.
- **Data governance.** Admin-only, reasoned, audited deletion of sources and entities; artifact purge; optional artifact retention (`OSINT_ARTIFACT_RETENTION_DAYS`); investigation export.
- **Imports** require an authorization note, which is recorded on the tool run.

## Privacy stance

Public, authorized, non-private information only. No doxxing, no deanonymization, no private-data collection, no wallet-ownership attribution, no Telegram scraping. Username matches are leads, not identity claims. The Maigret placeholder is disabled partly because of its profile-dossier extraction.

## Known gaps

- No rate limiting or lockout on login. No password-change UI. No refresh tokens. The JWT is stored in browser `localStorage` (XSS-exposed); httpOnly cookies are PLANNED.
- Artifact files are stored unencrypted on local disk.
- No TLS termination or reverse proxy configuration is provided.
- No dependency vulnerability scan has been run.
- The S3/MinIO artifact backend and its access policies do not exist yet.
