# Environment Setup

## Files

- `.env`
  - real secrets and local machine values
  - ignored by Git
- `.env.example`
  - safe template
  - committed to Git

## Recommended workflow

1. Copy `.env.example` to `.env`
2. Fill in real secrets
3. Adjust storage settings depending on run mode
4. Adjust security settings depending on run mode

## Local non-Docker mode

Use:

- `DATABASE_URL=postgresql://<user>:<password>@localhost:5432/<db>`
- `REDIS_URL=redis://localhost:6379/0`
- `QDRANT_URL=http://localhost:6333`

Optional:

- run PostgreSQL, Redis, and Qdrant locally, ideally through Docker Compose
- keep `APP_ENV=development`
- keep localhost values in `ALLOWED_ORIGINS` and `TRUSTED_HOSTS`

## Docker Compose mode

Use:

- `DATABASE_URL=postgresql://<user>:<password>@postgres:5432/<db>`
- `REDIS_URL=redis://redis:6379/0`
- `QDRANT_URL=http://qdrant:6333`

Compose will read the same `.env` file and inject these values into containers.

Security settings to review before deployment:

- `APP_ENV=production`
- `JWT_SECRET_KEY=<strong-random-secret>`
- `ALLOWED_ORIGINS=https://your-frontend.example.com`
- `TRUSTED_HOSTS=your-api.example.com`
- `ENABLE_DOCS=false` if you do not want public schema/docs exposure
- `GOOGLE_OAUTH_ALLOW_INSECURE_HTTP=false`

## Git safety rules

- never commit `.env`
- commit `.env.example`
- rotate any secret that was ever pasted into chat, logs, screenshots, or Git history

## Production advice

- store secrets in a secret manager, not a checked-in file
- do not use placeholder local passwords in shared environments
- set a strong `JWT_SECRET_KEY`
- use separate credentials for local, staging, and production
- do not use wildcard browser origins in production
- keep Google OAuth callbacks on HTTPS in production

## Best-practice recommendation

Use the same backing services everywhere:

- PostgreSQL for relational data
- Redis for exact cache and future coordination
- Qdrant for retrieval

This keeps the code path consistent from laptop to CI to AWS.
