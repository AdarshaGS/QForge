# Security Policy

QForge is a desktop SQL client that stores database connection details and,
via the OS keychain, database/SSH passwords. Please report security issues
privately rather than through a public GitHub issue.

## Supported Versions

Only the latest released version is supported with security fixes. There is
no long-term-support branch.

## Reporting a Vulnerability

Email **adarshgs1928@gmail.com** with:

- A description of the issue and its impact.
- Steps to reproduce (a minimal `connections.json` snippet, SQL, or sample
  data helps, as long as it contains no real credentials or personal data).
- The QForge version and OS you tested on.

You should get an acknowledgement within a few days. This is a
single-maintainer project, so please be patient with the timeline for a fix
or advisory — but you will be kept informed of progress.

Please don't publicly disclose the issue until a fix has shipped.

## Scope

Examples of what's in scope:

- Credential handling — passwords leaking into `connections.json`, logs,
  query history, exported files, or crash reports.
- A malicious or malformed `connections.json`, imported CSV/JSON/Excel file,
  or SQL snippet causing code execution, path traversal, or reading/writing
  files outside the intended application-data directory.
- SQL injection in SQL that QForge itself generates (e.g. export-as-`INSERT`,
  CSV-import statements, the query-diff/verifier tool) — as opposed to SQL
  the user deliberately writes and runs themselves.
- Anything that defeats the OS keychain credential storage in
  `utils/credential_store.py` without the OS keychain itself being
  compromised.

Out of scope / not a QForge vulnerability by itself:

- The SQL editor executing whatever SQL a user chooses to write and run —
  that's the product's job. Database permissions are QForge's real security
  boundary; client-side guards (where they exist) reduce accidents, not
  access.
- Issues that require an already-compromised machine, OS keychain, or
  `connections.json` file to have been tampered with by an attacker who
  already has that level of access.

## Known Limitations (not vulnerabilities, but worth knowing)

- Passwords are stored via the OS keyring only (`utils/credential_store.py`)
  — there is no encrypted-file fallback. If the OS keyring is unavailable,
  QForge keeps the password in `connections.json` in plain text rather than
  silently losing it, and warns you when this happens.
- QForge has no built-in telemetry or crash reporting; nothing about your
  usage or data leaves your machine via QForge itself.
