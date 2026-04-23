# 05 Auth And Multi-Tenancy Flow

## Why this functionality exists

The app is designed for collaborative research teams, but each team must see only its own vault.

That creates two separate requirements:

- user authentication
- tenant isolation

These are related but not the same thing.

## Design thinking in order

### Step 1: Authenticate users with a simple, strong default

Username plus password with JWT is enough for the current product stage.

That led to:

- bcrypt password hashes
- JWT bearer tokens
- FastAPI dependency-based auth checks

### Step 2: Model team isolation explicitly

Instead of manually assigning every chunk to a user ID, the app derives a stable `tenant_id` from `team_code`.

That makes it easy for multiple users in one team to share the same vault.

### Step 3: Enforce tenant isolation at storage query time

The model prompt is not a security boundary.

Real isolation happens because:

- Qdrant filters on `tenant_id`
- cache keys are tenant-scoped
- trace and usage lookups are tenant-scoped

## Dependencies introduced

- `bcrypt`
- `python-jose`
- `fastapi-sso`
- `sqlalchemy`

## Files involved

- `app/api/auth.py`
- `app/core/auth.py`
- `app/core/database.py`
- `app/schemas/auth.py`
- `app/api/routes.py`

## Runtime execution flow

### Registration

`app/api/auth.py -> register_user()`

This flow:

1. validates incoming user payload
2. checks whether username exists
3. derives `tenant_id` from `team_code`
4. hashes the password
5. stores the user row

### Login

`app/api/auth.py -> login_for_access_token()`

This flow:

1. reads OAuth2 password form data
2. queries the user by username
3. verifies bcrypt hash
4. creates JWT token
5. returns token plus tenant context

### Authenticated request handling

Protected routes use:

- `Depends(get_current_user)`

`app/core/auth.py -> get_current_user()`

This flow:

1. extracts bearer token
2. decodes JWT
3. loads username from token
4. queries the user row
5. returns the current user object

### Multi-tenancy enforcement

The current user object is then used to scope:

- retrieval filters
- cache keys
- usage logs
- trace logs

That means tenant isolation is propagated from auth into every important storage access.

## Flow across files

`dashboard.py`
-> `app/api/auth.py`
-> `app/core/auth.py`
-> `app/core/database.py`
-> authenticated routes in `app/api/routes.py`
-> tenant-scoped retrieval and cache logic

## What to rebuild first if doing this from scratch

1. Add user table.
2. Add bcrypt hash helpers.
3. Add JWT token creation and validation.
4. Add `team_code -> tenant_id` mapping.
5. Add `Depends(get_current_user)` to protected routes.
6. Add tenant filters to storage access.

This order ensures isolation is built into the product, not bolted on later.
