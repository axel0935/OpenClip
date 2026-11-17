.PHONY: help install dev-api dev-web check test openapi build-web build-wheel doctor docker-build

UV_CACHE_DIR := $(CURDIR)/.uv-cache
PNPM := pnpm --config.store-dir=$(CURDIR)/.pnpm-store --dir frontend
# pnpm is optional: the backend leg of `make check` must still work without it.
HAS_PNPM := $(shell command -v pnpm >/dev/null 2>&1 && echo yes || echo no)

help:
	@echo "OpenClip targets:"
	@echo "  install       Sync backend (uv) and install frontend deps (pnpm)"
	@echo "  dev-api       Run the FastAPI server on 127.0.0.1:8000 with reload"
	@echo "  dev-web       Run the Vite dev server on :5173 (proxies the API)"
	@echo "  check         Lint, format check, typecheck and test backend and frontend"
	@echo "  test          Tests only"
	@echo "  openapi       Regenerate docs/openapi.json and frontend/src/api/schema.d.ts"
	@echo "  build-web     Production build of the frontend"
	@echo "  build-wheel   Build the distributable wheel (bundles the web UI if pnpm is present)"
	@echo "  doctor        Run the local self-check (openclip doctor)"
	@echo "  docker-build  Build the Docker image (needs Docker)"

install:
	cd backend && UV_CACHE_DIR=$(UV_CACHE_DIR) uv sync
	$(PNPM) install

dev-api:
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --project backend openclip serve

dev-web:
	$(PNPM) dev

check:
	cd backend && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run ruff check src tests
	cd backend && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run ruff format --check src tests
	cd backend && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run pyright
	cd backend && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run pytest
ifeq ($(HAS_PNPM),yes)
	$(PNPM) lint
	$(PNPM) typecheck
	$(PNPM) test --run
else
	@echo "make check: pnpm not found, skipping the frontend leg"
endif

test:
	cd backend && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run pytest
ifeq ($(HAS_PNPM),yes)
	$(PNPM) test --run
else
	@echo "make test: pnpm not found, skipping the frontend tests"
endif

openapi:
	UV_CACHE_DIR=$(UV_CACHE_DIR) uv run --project backend python scripts/export_openapi.py
	$(PNPM) gen:api

build-web:
	$(PNPM) build

# Builds the wheel that `pipx install` / `uvx --from` can use. The web bundle is
# staged into backend/src/openclip/web (git-ignored) because the wheel ships the
# UI as package data; without pnpm the wheel is API-only.
build-wheel:
ifeq ($(HAS_PNPM),yes)
	$(PNPM) build
	rm -rf backend/src/openclip/web
	cp -r frontend/dist backend/src/openclip/web
else
	@echo "make build-wheel: pnpm not found, building an API-only wheel"
endif
	cd backend && UV_CACHE_DIR=$(UV_CACHE_DIR) uv build --out-dir ../dist
	@echo "dist/:"
	@ls -1 dist

doctor:
	cd backend && UV_CACHE_DIR=$(UV_CACHE_DIR) uv run openclip doctor

docker-build:
	docker build -f docker/Dockerfile -t openclip:local .
