.PHONY: up down build logs restart shell-api shell-worker ps clean

# ── Development ──────────────────────────────────────────────────────────────

up:
	docker compose up -d

build:
	docker compose up -d --build

down:
	docker compose down

restart:
	docker compose restart

logs:
	docker compose logs -f

logs-api:
	docker compose logs -f api

logs-worker:
	docker compose logs -f worker

ps:
	docker compose ps

shell-api:
	docker compose exec api bash

shell-worker:
	docker compose exec worker bash

# ── Production ────────────────────────────────────────────────────────────────

prod-up:
	docker compose -f docker-compose.prod.yml up -d --build

prod-down:
	docker compose -f docker-compose.prod.yml down

prod-logs:
	docker compose -f docker-compose.prod.yml logs -f

# ── Cleanup ───────────────────────────────────────────────────────────────────

clean:
	docker compose down -v --remove-orphans
