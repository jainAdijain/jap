# JAP: one command per job. Run `make help` for the list.
#
# Every target takes ENV=local|live (PRD H7); local is the default, so `make start` works as
# before. Both run from this one checkout and share nothing else: their own env file, Docker
# project, database volume, data folder and ports.
#
#   local  .env       this working copy with live reload, never submits, not on the internet
#   live   .env.live  the built image behind the tunnel at jap.tutorial.monster, submits
#
# live adds infra/compose.release.yaml, which mounts no source: its code is baked into the
# image, so editing a file changes nothing until the next `make start ENV=live`.
ENV ?= local
ifeq ($(ENV),local)
ENV_FILE := .env
else ifeq ($(ENV),live)
ENV_FILE := .env.live
else
$(error ENV must be local or live)
endif

# Ports, project name, data folder and JAP_RELEASE: the environment's file wins, these are
# the defaults (keep in step with infra/compose.yaml).
-include $(ENV_FILE)
JAP_PROJECT ?= jap
JAP_UI_PORT ?= 5173
JAP_API_PORT ?= 8001
JAP_INNGEST_PORT ?= 8290
JAP_ADMINER_PORT ?= 8080
JAP_DATA_HOST_DIR ?= data
HEALTH_URL = http://127.0.0.1:$(JAP_API_PORT)/api/health
OLLAMA_URL ?= http://127.0.0.1:11434
EMBED_MODEL ?= nomic-embed-text

COMPOSE_FILES := -f infra/compose.yaml $(if $(filter 1 true,$(JAP_RELEASE)),-f infra/compose.release.yaml)
COMPOSE := docker compose -p $(JAP_PROJECT) --env-file $(ENV_FILE) $(COMPOSE_FILES)
TUNNEL_ENV := infra/tunnel/.env.tunnel
TUNNEL := docker compose --env-file $(TUNNEL_ENV) -f infra/tunnel/compose.yaml

.DEFAULT_GOAL := help
.PHONY: help env tunnel-start tunnel-stop tunnel-status mac-wake start stop restart reset clean logs ps migrate seed test test-app test-ui qa backup restore form-check health-check errors two-step-off sign-out-all set-password email-test code-mail-check ollama-check llm-check demo demo-clear

help: ## List the targets
	@grep -hE '^[a-z-]+:.*## ' $(firstword $(MAKEFILE_LIST)) | awk 'BEGIN {FS = ":.*## "} {printf "  make %-13s %s\n", $$1, $$2}'

env: ## Create this environment's file if it is missing (.env, or .env.live with fresh keys)
ifeq ($(ENV),local)
	@test -f .env || { cp .env.example .env; echo "Created .env from .env.example. Add your OpenRouter key and email password to it."; }
else
	@test -f .env.live || infra/scripts/env-create.sh
endif

start: env ## Start the stack, wait for /api/health, apply migrations
	$(COMPOSE) up -d --build
	@infra/scripts/wait-for-health.sh $(HEALTH_URL)
	@$(MAKE) --no-print-directory migrate
	@$(MAKE) --no-print-directory ollama-check
	@echo "$(ENV): UI http://localhost:$(JAP_UI_PORT)  API http://localhost:$(JAP_API_PORT)  Inngest http://localhost:$(JAP_INNGEST_PORT)$(if $(filter 1 true,$(JAP_RELEASE)),,  Adminer http://localhost:$(JAP_ADMINER_PORT))"

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

demo: ## Load made-up postings for checking dedup and filters by hand (never fetched)
	$(COMPOSE) exec app python -m database.demo load

demo-clear: ## Remove the demo postings, listings and boards
	$(COMPOSE) exec app python -m database.demo clear

test: test-app test-ui ## ruff, mypy and pytest for app; lint and build for ui

test-app:
	$(COMPOSE) exec app sh -c 'ruff check . && ruff format --check . && mypy . && pytest -q'

test-ui:
	$(COMPOSE) exec ui sh -c 'npm run lint && npm run build'

qa: ## Dedup QA set: place ~100 labelled job pairs and print the accuracy
	$(COMPOSE) exec app pytest -q -s tests/test_dedup_qa.py

backup: ## Dump the database to this environment's backups folder
	@DATA_DIR="$(JAP_DATA_HOST_DIR)" COMPOSE="$(COMPOSE)" infra/scripts/backup.sh

restore: ## Restore a backup: make restore FILE=data/backups/<file>.sql.gz
	@DATA_DIR="$(JAP_DATA_HOST_DIR)" COMPOSE="$(COMPOSE)" infra/scripts/restore.sh "$(FILE)"

health-check: ## Run the hourly health watch now: delete old log rows, email alerts that are due
	@curl -fsS -X POST http://127.0.0.1:$(JAP_API_PORT)/api/ops/health-check > /dev/null && \
		echo "Health check started. Results: Settings > Status, or the health-watch run in Inngest."

errors: ## The last 20 warnings and errors JAP logged (log_event, kept 30 days)
	@$(COMPOSE) exec -T db psql -U $(or $(POSTGRES_USER),jap) -d $(or $(POSTGRES_DB),jap) -c \
		"SELECT to_char(created_at, 'MM-DD HH24:MI') AS at, level, event, left(message, 90) AS message FROM log_event WHERE level IN ('warning', 'error', 'critical') ORDER BY created_at DESC LIMIT 20"

form-check: ## Read one live form per ATS now (the weekly form test; nothing is submitted)
	@$(COMPOSE) exec -T app python -m functions.events form-check/requested > /dev/null && \
		echo "Form test started. Results: System status on Settings, or the form-check run in Inngest."

two-step-off: ## Turn two-step sign-in off for an account: make two-step-off EMAIL=<address>
	@test -n "$(EMAIL)" || { echo "Usage: make two-step-off EMAIL=you@example.com"; exit 1; }
	$(COMPOSE) exec app python -m auth.cli two-step-off "$(EMAIL)"

sign-out-all: ## End every session of an account: make sign-out-all EMAIL=<address>
	@test -n "$(EMAIL)" || { echo "Usage: make sign-out-all EMAIL=you@example.com"; exit 1; }
	$(COMPOSE) exec app python -m auth.cli sign-out-all "$(EMAIL)"

set-password: ## Set a new password without email: make set-password EMAIL=<address>
	@test -n "$(EMAIL)" || { echo "Usage: make set-password EMAIL=you@example.com"; exit 1; }
	$(COMPOSE) exec app python -m auth.cli set-password "$(EMAIL)"

email-test: ## Send one test email from the mailbox in .env: make email-test TO=<address>
	@test -n "$(TO)" || { echo "Usage: make email-test TO=you@example.com"; exit 1; }
	$(COMPOSE) exec app python -m pipeline.notify test "$(TO)"

code-mail-check: ## Log in to the mailbox over IMAP and count Greenhouse emails (security codes)
	$(COMPOSE) exec app python -m pipeline.apply.security_code

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

# ---- Hosting (PRD H1 to H8; README, Hosting) ----

tunnel-start: ## Start JAP's Cloudflare Tunnel: live goes on the internet (PRD H6)
	@test -f $(TUNNEL_ENV) || { echo "No $(TUNNEL_ENV). Copy infra/tunnel/.env.tunnel.example to it and paste the token."; exit 1; }
	@printf 'This puts JAP on the internet at jap.tutorial.monster. Is the live stack up (make start ENV=live) with sign-in working? Type yes: '; \
	read answer; [ "$$answer" = "yes" ] || { echo "Cancelled."; exit 1; }
	$(TUNNEL) up -d

tunnel-stop: ## Stop the tunnel: JAP is off the internet, the stacks keep running
	-$(TUNNEL) down

tunnel-status: ## Is the tunnel connected? (last log lines)
	@$(TUNNEL) ps
	@$(TUNNEL) logs --tail 5 2>/dev/null || true

mac-wake: ## Wake the Mac at 05:55 every day for the 06:00 search (asks for your password)
	sudo pmset repeat wakeorpoweron MTWRFSU 05:55:00
	@pmset -g sched
