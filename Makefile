.PHONY: install dev-api dev-web check test openapi build-web

install:
	cd backend && UV_CACHE_DIR=$(CURDIR)/.uv-cache uv sync
	pnpm --config.store-dir=$(CURDIR)/.pnpm-store --dir frontend install

dev-api:
	UV_CACHE_DIR=$(CURDIR)/.uv-cache uv run --project backend openclip serve

dev-web:
	pnpm --config.store-dir=$(CURDIR)/.pnpm-store --dir frontend dev

check:
	cd backend && UV_CACHE_DIR=$(CURDIR)/.uv-cache uv run ruff check src tests
	cd backend && UV_CACHE_DIR=$(CURDIR)/.uv-cache uv run ruff format --check src tests
	cd backend && UV_CACHE_DIR=$(CURDIR)/.uv-cache uv run pyright
	cd backend && UV_CACHE_DIR=$(CURDIR)/.uv-cache uv run pytest
	pnpm --config.store-dir=$(CURDIR)/.pnpm-store --dir frontend lint
	pnpm --config.store-dir=$(CURDIR)/.pnpm-store --dir frontend typecheck
	pnpm --config.store-dir=$(CURDIR)/.pnpm-store --dir frontend test --run

test:
	cd backend && UV_CACHE_DIR=$(CURDIR)/.uv-cache uv run pytest
	pnpm --config.store-dir=$(CURDIR)/.pnpm-store --dir frontend test --run

openapi:
	UV_CACHE_DIR=$(CURDIR)/.uv-cache uv run --project backend python scripts/export_openapi.py
	pnpm --config.store-dir=$(CURDIR)/.pnpm-store --dir frontend gen:api

build-web:
	pnpm --config.store-dir=$(CURDIR)/.pnpm-store --dir frontend build
