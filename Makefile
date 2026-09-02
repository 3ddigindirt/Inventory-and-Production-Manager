.PHONY: up down logs health backup rebuild
up:
	docker compose up -d --build

rebuild:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f app

health:
	curl -fsS http://localhost:8000/health

backup:
	docker compose --profile backup run --rm backup
