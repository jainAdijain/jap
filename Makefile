# JAP: one command per job. Run `make help` for the list.
#
# Every target takes ENV=dev|test|prod (PRD H7); dev is the default, so `make start` works as
# before. dev is this checkout. test and prod are git worktrees in $(JAP_ENVS_DIR)/<env>, each
# on a release tag with its own .env, data/ and Docker volumes (make env-create, make promote).
ENV ?= dev
JAP_ENVS_DIR ?= $(HOME)/jap-envs
ifeq ($(ENV),dev)
ROOT := .
else ifneq ($(filter test prod,$(ENV)),)
ROOT := $(JAP_ENVS_DIR)/$(ENV)
else
$(error ENV must be dev, test or prod)
endif

# Host ports and the project name: the environment's .env wins, these are the defaults
# (keep in step with infra/compose.yaml).
-include $(ROOT)/.env
JAP_PROJECT ?= jap
JAP_UI_PORT ?= 5173
JAP_API_PORT ?= 8001
JAP_INNGEST_PORT ?= 8290
JAP_ADMINER_PORT ?= 8080
HEALTH_URL = http://127.0.0.1:$(JAP_API_PORT)/api/health
OLLAMA_URL ?= http://127.0.0.1:11434
EMBED_MODEL ?= nomic-embed-text

COMPOSE_FILES := -f $(ROOT)/infra/compose.yaml $(if $(filter 1 true,$(JAP_RELEASE)),-f $(ROOT)/infra/compose.release.yaml)
COMPOSE := docker compose -p $(JAP_PROJECT) --env-file $(ROOT)/.env $(COMPOSE_FILES)
TUNNEL := docker compose --env-file .env.tunnel -f infra/tunnel/compose.yaml

.DEFAULT_GOAL := help
.PHONY: help env env-create promote mark-passed rollback tunnel-start tunnel-stop tunnel-status mac-wake start stop restart reset clean logs ps migrate seed test test-app test-ui qa backup restore form-check two-step-off sign-out-all set-password email-test code-mail-check ollama-check llm-check demo demo-clear

help: ## List the targets
	@grep -hE '^[a-z-]+:.*## ' $(firstword $(MAKEFILE_LIST)) | awk 'BEGIN {FS = ":.*## "} {printf "  make %-13s %s\n", $$1, $$2}'

env: ## Create .env from .env.example if it is missing (dev; test and prod use env-create)
ifeq ($(ENV),dev)
	@test -f .env || { cp .env.example .env; echo "Created .env from .env.example. Add your OpenRouter key and email password to it."; }
else
	@test -f $(ROOT)/.env || { echo "No $(ENV) environment yet. Run: make env-create ENV=$(ENV) REF=<tag>"; exit 1; }
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
	rm -rf $(ROOT)/backend/.mypy_cache $(ROOT)/backend/.ruff_cache $(ROOT)/backend/.pytest_cache $(ROOT)/frontend/node_modules $(ROOT)/frontend/dist
	find $(ROOT)/backend -name __pycache__ -type d -prune -exec rm -rf {} +

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

backup: ## Dump the database to data/backups/
	@JAP_ROOT="$(ROOT)" COMPOSE="$(COMPOSE)" infra/scripts/backup.sh

restore: ## Restore a backup: make restore FILE=data/backups/<file>.sql.gz
	@JAP_ROOT="$(ROOT)" COMPOSE="$(COMPOSE)" infra/scripts/restore.sh "$(FILE)"

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

# ---- Environments and hosting (PRD H1 to H8; README, Hosting) ----

env-create: ## Make the test or prod environment: make env-create ENV=test REF=v0.13.0
	@test "$(ENV)" != dev || { echo "dev is this checkout. Use ENV=test or ENV=prod."; exit 1; }
	@test -n "$(REF)" || { echo "Usage: make env-create ENV=$(ENV) REF=<tag>"; exit 1; }
	@mkdir -p $(JAP_ENVS_DIR)
	@test -d $(ROOT) || git worktree add --detach $(ROOT) $(REF)
	@infra/scripts/env-create.sh $(ENV) $(ROOT)

promote: ## Move test or prod to a release tag, rebuild, migrate: make promote ENV=prod REF=v0.13.0
	@JAP_ENVS_DIR="$(JAP_ENVS_DIR)" infra/scripts/promote.sh "$(ENV)" "$(ROOT)" "$(REF)"

mark-passed: ## After checking test by hand: allow its tag into prod
	@sha=$$(git -C $(JAP_ENVS_DIR)/test rev-parse HEAD) && echo $$sha > $(JAP_ENVS_DIR)/test.passed && \
		echo "Marked $$(git -C $(JAP_ENVS_DIR)/test describe --tags --always) as passed in test."

rollback: ## Put test or prod back on the tag it ran before the last promote: make rollback ENV=prod
	@JAP_ENVS_DIR="$(JAP_ENVS_DIR)" infra/scripts/promote.sh "$(ENV)" "$(ROOT)" --rollback

tunnel-start: ## Start JAP's Cloudflare Tunnel (only after sign-in is merged, PRD H6)
	@test -f .env.tunnel || { echo "No .env.tunnel. Copy infra/tunnel/.env.tunnel.example to .env.tunnel and paste the token."; exit 1; }
	@printf 'The tunnel puts JAP on the internet. Is sign-in (PRD S1 to S9) merged and running in every environment it serves? Type yes: '; \
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
