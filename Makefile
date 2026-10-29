# JAP: one command per job. Run `make help` for the list.
COMPOSE := docker compose --env-file .env -f infra/compose.yaml

# Host ports: .env wins, these are the defaults (keep in step with infra/compose.yaml).
-include .env
JAP_UI_PORT ?= 5173
JAP_API_PORT ?= 8001
JAP_INNGEST_PORT ?= 8290
JAP_ADMINER_PORT ?= 8080
HEALTH_URL = http://127.0.0.1:$(JAP_API_PORT)/api/health
OLLAMA_URL ?= http://127.0.0.1:11434
EMBED_MODEL ?= nomic-embed-text

.DEFAULT_GOAL := help
.PHONY: help env start stop restart reset clean logs ps migrate seed test test-app test-ui backup restore ollama-check llm-check demo demo-clear

help: ## List the targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  make %-13s %s\n", $$1, $$2}'

env: ## Create .env from .env.example if it is missing
	@test -f .env || { cp .env.example .env; echo "Created .env from .env.example. Add your OpenRouter key and Gmail app password to it."; }

start: env ## Start the stack, wait for /api/health, apply migrations
	$(COMPOSE) up -d --build
	@infra/scripts/wait-for-health.sh $(HEALTH_URL)
	@$(MAKE) --no-print-directory migrate
	@$(MAKE) --no-print-directory ollama-check
	@echo "UI http://localhost:$(JAP_UI_PORT)  API http://localhost:$(JAP_API_PORT)  Inngest http://localhost:$(JAP_INNGEST_PORT)  Adminer http://localhost:$(JAP_ADMINER_PORT)"

stop: ## Stop the stack (data is kept)
	$(COMPOSE) down

restart: stop start ## Stop, then start

reset: env ## Wipe the database and start fresh: down -v, up, migrate, seed (asks first)
	@printf 'This deletes the JAP database (profile, jobs, applications). data/ is kept. Type yes to continue: '; \
	read answer; [ "$$answer" = "yes" ] || { echo "Cancelled."; exit 1; }
	$(COMPOSE) down -v
	$(COMPOSE) up -d --build
	@infra/scripts/wait-for-health.sh $(HEALTH_URL)
	@$(MAKE) --no-print-directory migrate seed

clean: ## Remove containers, volumes, JAP images and build caches (asks first; data/ is kept)
	@printf 'This removes JAP containers, volumes, images and build caches. data/ is kept. Type yes to continue: '; \
	read answer; [ "$$answer" = "yes" ] || { echo "Cancelled."; exit 1; }
	-$(COMPOSE) down -v --rmi local --remove-orphans
	-docker builder prune -f
	rm -rf backend/.mypy_cache backend/.ruff_cache backend/.pytest_cache frontend/node_modules frontend/dist
	find backend -name __pycache__ -type d -prune -exec rm -rf {} +

logs: ## Follow all service logs
	$(COMPOSE) logs -f

ps: ## Show the running services
	$(COMPOSE) ps

migrate: ## Apply database migrations (alembic upgrade head)
	$(COMPOSE) exec app alembic upgrade head

seed: ## Load seed/companies.yaml and seed/skills.yaml (safe to repeat)
	$(COMPOSE) exec app python -m database.seed

demo: ## Load made-up postings for checking dedup by hand (JAP Demo Co; never fetched)
	$(COMPOSE) exec app python -m database.demo load

demo-clear: ## Remove the demo postings, listings and boards
	$(COMPOSE) exec app python -m database.demo clear

test: test-app test-ui ## ruff, mypy and pytest for app; lint and build for ui

test-app:
	$(COMPOSE) exec app sh -c 'ruff check . && ruff format --check . && mypy . && pytest -q'

test-ui:
	$(COMPOSE) exec ui sh -c 'npm run lint && npm run build'

backup: ## Dump the database to data/backups/
	@COMPOSE="$(COMPOSE)" infra/scripts/backup.sh

restore: ## Restore a backup: make restore FILE=data/backups/<file>.sql.gz
	@COMPOSE="$(COMPOSE)" infra/scripts/restore.sh "$(FILE)"

llm-check: ## One test call to each OpenRouter model (needs OPENROUTER_API_KEY in .env)
	$(COMPOSE) exec app python -m llm.check

ollama-check: ## Check that Ollama answers on the Mac and has the embedder pulled
	@if ! curl -fsS --max-time 2 $(OLLAMA_URL)/api/tags > /tmp/jap-ollama-tags 2>/dev/null; then \
		echo "WARNING: Ollama is not answering on $(OLLAMA_URL). Start it with: ollama serve (or open the Ollama app)"; \
	elif ! grep -q '"$(EMBED_MODEL)' /tmp/jap-ollama-tags; then \
		echo "WARNING: Ollama is running but $(EMBED_MODEL) is not pulled. Run: ollama pull $(EMBED_MODEL)"; \
	else \
		echo "Ollama OK ($(EMBED_MODEL) ready)"; \
	fi
