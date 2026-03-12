# Red Hat Lightspeed Agent for Google Cloud - Makefile
# Common development and deployment commands

.PHONY: help build build-agent build-marketplace run stop logs logs-mcp clean test lint dev check-env

# Default target
help:
	@echo "Red Hat Lightspeed Agent for Google Cloud - Available Commands"
	@echo ""
	@echo "Development:"
	@echo "  make dev          - Run agent in development mode (no container)"
	@echo "  make test         - Run tests"
	@echo "  make lint         - Run linter and type checker"
	@echo ""
	@echo "Container (Podman):"
	@echo "  make build             - Build all container images (agent + marketplace handler)"
	@echo "  make build-agent       - Build agent container image only"
	@echo "  make build-marketplace - Build marketplace handler container image only"
	@echo "  make run          - Start the pod with all services"
	@echo "  make stop         - Stop and remove the pod"
	@echo "  make logs         - View agent container logs"
	@echo "  make logs-mcp     - View MCP server container logs"
	@echo "  make logs-all     - View all container logs"
	@echo "  make status       - Show pod and container status"
	@echo "  make check-env    - Check required environment variables"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean        - Remove containers, images, and volumes"
	@echo ""
	@echo "Required Environment Variables:"
	@echo "  GOOGLE_API_KEY           - Google AI Studio API key"
	@echo ""

# =============================================================================
# Development Commands
# =============================================================================

dev:
	@echo "Starting agent in development mode..."
	source .venv/bin/activate && python -m lightspeed_agent.main

test:
	@echo "Running tests..."
	source .venv/bin/activate && python -m pytest tests/ -v

lint:
	@echo "Running linter..."
	source .venv/bin/activate && ruff check src/ tests/
	@echo "Running type checker..."
	source .venv/bin/activate && mypy src/lightspeed_agent/ --ignore-missing-imports

# =============================================================================
# Container Commands (Podman)
# =============================================================================

IMAGE_NAME ?= localhost/lightspeed-agent
MARKETPLACE_IMAGE_NAME ?= localhost/marketplace-handler
IMAGE_TAG ?= latest
POD_NAME = lightspeed-agent-pod

build: build-agent build-marketplace

build-agent:
	@echo "Building agent container image..."
	podman build -t $(IMAGE_NAME):$(IMAGE_TAG) -f Containerfile .

build-marketplace:
	@echo "Building marketplace handler container image..."
	podman build -t $(MARKETPLACE_IMAGE_NAME):$(IMAGE_TAG) -f Containerfile.marketplace-handler .

run: check-env build
	@echo "Starting pod..."
	@if podman pod exists $(POD_NAME); then \
		echo "Pod already exists. Stopping and removing..."; \
		podman pod stop $(POD_NAME) 2>/dev/null || true; \
		podman pod rm $(POD_NAME) 2>/dev/null || true; \
	fi
	@mkdir -p config
	podman play kube lightspeed-agent-pod.yaml
	@echo ""
	@echo "Pod started. Services available at:"
	@echo "  - Agent API:  http://localhost:8000"
	@echo "  - Health:     http://localhost:8000/health"
	@echo "  - AgentCard:  http://localhost:8000/.well-known/agent.json"
	@echo "  - MCP Server: http://localhost:8081 (internal)"
	@echo ""
	@echo "View logs:"
	@echo "  make logs      - Agent logs"
	@echo "  make logs-mcp  - MCP server logs"

stop:
	@echo "Stopping pod..."
	podman pod stop $(POD_NAME) 2>/dev/null || true
	podman pod rm $(POD_NAME) 2>/dev/null || true
	@echo "Pod stopped and removed."

cve-scan:
	@echo "Scanning for CVEs with trivy"
	podman run --rm -v $$(pwd):/app:Z aquasec/trivy fs --file-patterns pip:requirements-.*\.txt /app

logs:
	@echo "Showing agent logs..."
	podman logs -f $(POD_NAME)-lightspeed-agent

logs-mcp:
	@echo "Showing MCP server logs..."
	podman logs -f $(POD_NAME)-insights-mcp

logs-all:
	@echo "Showing all container logs..."
	@for container in $$(podman pod inspect $(POD_NAME) --format '{{range .Containers}}{{.Name}} {{end}}'); do \
		echo "=== $$container ==="; \
		podman logs --tail 50 $$container 2>/dev/null || true; \
		echo ""; \
	done

status:
	@echo "Pod status:"
	@podman pod ps --filter name=$(POD_NAME)
	@echo ""
	@echo "Container status:"
	@podman ps --filter pod=$(POD_NAME) --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

check-env:
	@echo "Checking required environment variables..."
	@missing=0; \
	if [ -z "$$GOOGLE_API_KEY" ] && [ "$$GOOGLE_GENAI_USE_VERTEXAI" != "TRUE" ]; then \
		echo "  ✗ GOOGLE_API_KEY is not set (required unless using Vertex AI)"; \
		missing=1; \
	else \
		echo "  ✓ GOOGLE_API_KEY is set (or using Vertex AI)"; \
	fi; \
	if [ $$missing -eq 1 ]; then \
		echo ""; \
		echo "Missing required environment variables!"; \
		echo "See .env.example for configuration options."; \
		exit 1; \
	else \
		echo ""; \
		echo "All required environment variables are set."; \
	fi

# =============================================================================
# Cleanup Commands
# =============================================================================

clean: stop
	@echo "Removing container images..."
	podman rmi $(IMAGE_NAME):$(IMAGE_TAG) 2>/dev/null || true
	podman rmi $(MARKETPLACE_IMAGE_NAME):$(IMAGE_TAG) 2>/dev/null || true
	@echo "Removing dangling images..."
	podman image prune -f
	@echo "Cleanup complete."

clean-all: clean
	@echo "Removing all volumes..."
	podman volume prune -f
	@echo "Full cleanup complete."
