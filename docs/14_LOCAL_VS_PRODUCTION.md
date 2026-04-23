# 14 Local Vs Production

## Local

### Shape

- single machine
- all services often on one Docker host
- PostgreSQL, Redis, and Qdrant still run locally
- local disk for checkpoints and images
- permissive CORS
- Google SSO configured with `allow_insecure_http=True`
- `uvicorn.run(..., reload=True)` available in direct execution mode

### Why local is simpler

- no service discovery problem
- no load balancer
- no distributed state problem
- service endpoints are usually `localhost`

## Production

### Shape

- multiple services on separate containers or hosts
- likely load balancing in front of API and frontend
- network latency between services
- TLS and auth hardening
- persistent shared storage requirements

### What changes operationally

- every local file assumption becomes a deployment decision
- every background task becomes a durability question
- every retry can create duplicate work if ids are not deterministic

## Code and config changes needed for production

### 1. Database

Use:

- PostgreSQL `DATABASE_URL`

Note:

- the project is now standardized on Postgres for local and production

### 2. Checkpointing

Change from:

- local `checkpoints.db`

To:

- shared checkpoint backend if running multiple API instances

### 3. Assets

Change from:

- local `assets/images`

To:

- object storage or shared persistent volume

### 4. Security

Change from:

- `allow_origins=["*"]`
- `allow_insecure_http=True`
- default JWT fallback secret

To:

- explicit allowed origins
- secure OAuth redirect URIs
- required secret injection

### 5. Caching and ephemeral state

Current change:

- Redis exact cache is already wired locally and in production

Still add later:

- queueing or shared short-lived coordination if worker architecture is introduced

## Common migration mistakes

### Mistake 1: scaling API replicas without shared checkpoint store

What happens:

- chat continuity breaks across requests
- history becomes inconsistent across nodes

### Mistake 2: leaving images on local container filesystem

What happens:

- image references break after reschedule or scale-out
- different nodes have different asset sets

### Mistake 3: assuming async equals horizontally scalable

What happens:

- OCR and parsing still block workers
- ingestion competes with chat traffic

### Mistake 4: keeping wildcard CORS and insecure OAuth settings

What happens:

- unnecessary security exposure in production

## Production behavior differences that surprise teams

### Network latency

Local:

- near-zero service-to-service latency

Production:

- every OpenAI, DB, Qdrant, and storage call pays network cost

### Partial failure

Local:

- things mostly fail together

Production:

- one subsystem can degrade while others stay alive

### Persistence semantics

Local:

- a file path feels durable enough

Production:

- container-local storage is often ephemeral

## Bottom line

The project should now be run against the same service-backed dependencies locally and in production. The remaining deployment work is mostly about shared checkpoints, object storage, and infrastructure hardening rather than swapping core data stores.
