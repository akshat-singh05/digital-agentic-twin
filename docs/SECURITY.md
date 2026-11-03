# Security notes

What the Digital Twin protects, how, and — just as important — what it does not. Read this
before putting other people's data in it.

## What is protected

| Area | How |
|---|---|
| Passwords | Salted scrypt hashes (stdlib). Never stored or returned in clear. |
| Sessions | Signed access tokens that expire (`ACCESS_TOKEN_MINUTES`). They carry a per-user version, so a password change or reset, or **Sign out everywhere**, revokes every token issued before. |
| Password reset | Emailed link, random, single use, expires (`RESET_TOKEN_MINUTES`), and only its SHA-256 is stored, so a copy of the database cannot reset anyone's password. Asking for a new link voids the old ones. The request answers the same whether or not the address has an account. |
| Email confirmation | Same kind of link. `REQUIRE_EMAIL_VERIFICATION=true` makes it mandatory for signing in. |
| Access control | Users reach only their own data (others' records answer 404). Admins can read but never act for a user. |
| Guessing and abuse | Per-client-address limits, stricter for login, register and password-reset requests. Shared between processes with `REDIS_URL`. |
| Browser | CSP on the dashboard (no framing, no plugins, `connect-src 'self'`, fixed `base-uri` and form targets), `nosniff`, `no-referrer`, a restrictive `Permissions-Policy`, HSTS over HTTPS, and `Cache-Control: no-store` on every API response. CORS is limited to configured origins and carries no credentials. |
| Logs | Emails are masked (`p***@example.com`); passwords are never logged. Reset and confirmation links are logged only in `MAIL_MODE=console` (see Email). |
| Audit | Account creation, sign-ins, failed sign-ins on a real account, password changes and resets, email confirmation and sign-out-everywhere are recorded per user. |

## Data at rest: not encrypted by the app

The database (SQLite by default) stores names, emails, usage figures, plan prices, briefs and
the notes you type, **in clear**. The app does not encrypt it, on purpose:

- SQLite has no built-in encryption, and an application-level key lives on the same server as the
  data, so field encryption protects only against a stolen file — exactly what disk encryption
  already does — while breaking lookups (login by email) and queries (sums, filters).
- What actually protects the data is encrypting where it lives:
  - run the database on an **encrypted volume** (BitLocker, LUKS, an encrypted cloud disk);
  - in Docker, mount `/data` from such a volume and keep it readable by the container user only;
  - **encrypt backups**, and test restoring them;
  - on a managed database (PostgreSQL), enable the provider's encryption at rest.
- Passwords and reset tokens are already stored only as hashes.

## Email

`MAIL_MODE=console` writes each message, including the working link, to the log. It exists for
development. For real use set `MAIL_MODE=smtp` and the `SMTP_*` settings (STARTTLS by default) and
`APP_BASE_URL` so the links point at your address. Sending never reports failure to the caller,
so a request cannot reveal whether an address is registered.

## Known gaps

- No two-factor sign-in, and no per-account lockout (limits are per client address).
- The access token lives in the browser's `sessionStorage`, readable by any script on the page.
  The CSP has to allow inline scripts because the dashboard uses inline event handlers, so it
  limits where the page can connect and be framed, not what scripts may run.
- No token refresh: a token lasts until it expires or is revoked.
- Registration is open. Without `REQUIRE_EMAIL_VERIFICATION`, anyone can register any address.
- The rate limiter uses a fixed one-minute window with Redis (a client can briefly send up to
  twice the limit across a minute boundary). If Redis is down it falls back to a per-process
  counter and logs the problem.
- Behind a proxy the limiter sees the address Uvicorn reports; configure forwarded headers
  (`--proxy-headers`, `--forwarded-allow-ips`) correctly or every client looks the same.
- SQLite allows one writer at a time. When running several workers, apply migrations once
  (`alembic upgrade head`) before starting them, rather than letting each migrate at startup.
- No automated dependency or vulnerability scanning in CI.

## Reporting a problem

Open an issue on the repository, or contact the repository owner directly for anything sensitive.
