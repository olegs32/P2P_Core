# Makefile for P2P Admin System

.PHONY: help install run test clean docker-build docker-up docker-down lint format security-check docs

# Variables
PYTHON := python3
PIP := pip3
DOCKER_COMPOSE := docker-compose
PROJECT_NAME := p2p-admin-system
VENV := venv

# Colors
RED := \033[0;31m
GREEN := \033[0;32m
YELLOW := \033[0;33m
NC := \033[0m # No Color

# Default target
help:
	@echo "$(GREEN)P2P Admin System - Available Commands:$(NC)"
	@echo "  $(YELLOW)make install$(NC)        - Install dependencies"
	@echo "  $(YELLOW)make run$(NC)            - Run single node locally"
	@echo "  $(YELLOW)make run-cluster$(NC)    - Run 3-node cluster locally"
	@echo "  $(YELLOW)make run-admin$(NC)      - Run Streamlit admin interface"
	@echo "  $(YELLOW)make test$(NC)           - Run tests"
	@echo "  $(YELLOW)make lint$(NC)           - Run code linting"
	@echo "  $(YELLOW)make format$(NC)         - Format code"
	@echo "  $(YELLOW)make clean$(NC)          - Clean up files"
	@echo "  $(YELLOW)make docker-build$(NC)   - Build Docker images"
	@echo "  $(YELLOW)make docker-up$(NC)      - Start Docker containers"
	@echo "  $(YELLOW)make docker-down$(NC)    - Stop Docker containers"
	@echo "  $(YELLOW)make docs$(NC)           - Generate documentation"

# Install dependencies
install:
	@echo "$(GREEN)Creating virtual environment...$(NC)"
	$(PYTHON) -m venv $(VENV)
	@echo "$(GREEN)Installing dependencies...$(NC)"
	$(VENV)/bin/$(PIP) install --upgrade pip
	$(VENV)/bin/$(PIP) install -r requirements.txt
	@echo "$(GREEN)Installation complete!$(NC)"

# Run single node
run:
	@echo "$(GREEN)Starting P2P node...$(NC)"
	$(VENV)/bin/$(PYTHON) run.py --host 127.0.0.1 --port 8000 --dht-port 5678

# Run bootstrap node
run-bootstrap:
	@echo "$(GREEN)Starting bootstrap node...$(NC)"
	$(VENV)/bin/$(PYTHON) run.py --host 127.0.0.1 --port 8000 --dht-port 5678 --log-level DEBUG

# Run additional nodes
run-node1:
	@echo "$(GREEN)Starting node 1...$(NC)"
	$(VENV)/bin/$(PYTHON) run.py --host 127.0.0.1 --port 8001 --dht-port 5679 --bootstrap 127.0.0.1:5678

run-node2:
	@echo "$(GREEN)Starting node 2...$(NC)"
	$(VENV)/bin/$(PYTHON) run.py --host 127.0.0.1 --port 8002 --dht-port 5680 --bootstrap 127.0.0.1:5678

# Run 3-node cluster (requires tmux)
run-cluster:
	@echo "$(GREEN)Starting 3-node cluster in tmux...$(NC)"
	@command -v tmux >/dev/null 2>&1 || { echo "$(RED)tmux is required but not installed.$(NC)" >&2; exit 1; }
	tmux new-session -d -s p2p-cluster
	tmux send-keys -t p2p-cluster "make run-bootstrap" C-m
	tmux split-window -t p2p-cluster -h
	tmux send-keys -t p2p-cluster "sleep 5 && make run-node1" C-m
	tmux split-window -t p2p-cluster -v
	tmux send-keys -t p2p-cluster "sleep 10 && make run-node2" C-m
	tmux select-pane -t p2p-cluster:0.0
	tmux split-window -t p2p-cluster -v
	tmux send-keys -t p2p-cluster "sleep 15 && make run-admin" C-m
	tmux attach-session -t p2p-cluster

# Run Streamlit admin
run-admin:
	@echo "$(GREEN)Starting Streamlit admin interface...$(NC)"
	$(VENV)/bin/streamlit run admin/app.py

# Run tests
test:
	@echo "$(GREEN)Running tests...$(NC)"
	$(VENV)/bin/pytest tests/ -v --cov=core --cov=api --cov=services --cov-report=html

test-unit:
	@echo "$(GREEN)Running unit tests...$(NC)"
	$(VENV)/bin/pytest tests/unit/ -v

test-integration:
	@echo "$(GREEN)Running integration tests...$(NC)"
	$(VENV)/bin/pytest tests/integration/ -v

# Linting
lint:
	@echo "$(GREEN)Running linters...$(NC)"
	$(VENV)/bin/flake8 core api services admin --max-line-length=120
	$(VENV)/bin/pylint core api services admin
	$(VENV)/bin/mypy core api services

# Format code
format:
	@echo "$(GREEN)Formatting code...$(NC)"
	$(VENV)/bin/black core api services admin tests
	$(VENV)/bin/isort core api services admin tests

# Security check
security-check:
	@echo "$(GREEN)Running security checks...$(NC)"
	$(VENV)/bin/bandit -r core api services
	$(VENV)/bin/safety check

# Clean up
clean:
	@echo "$(GREEN)Cleaning up...$(NC)"
	find . -type f -name "*.pyc" -delete
	find . -type d -name "__pycache__" -delete
	find . -type d -name ".pytest_cache" -delete
	find . -type d -name ".mypy_cache" -delete
	rm -rf htmlcov/
	rm -rf dist/
	rm -rf build/
	rm -rf *.egg-info
	rm -rf logs/*
	rm -rf data/*
	rm -rf cache/*
	rm -rf temp/*

# Docker commands
docker-build:
	@echo "$(GREEN)Building Docker images...$(NC)"
	$(DOCKER_COMPOSE) build

docker-up:
	@echo "$(GREEN)Starting Docker containers...$(NC)"
	$(DOCKER_COMPOSE) up -d
	@echo "$(GREEN)Containers started!$(NC)"
	@echo "  Admin UI: http://localhost:8501"
	@echo "  API Bootstrap: http://localhost:8000"
	@echo "  Prometheus: http://localhost:9090"
	@echo "  Grafana: http://localhost:3000"

docker-down:
	@echo "$(GREEN)Stopping Docker containers...$(NC)"
	$(DOCKER_COMPOSE) down

docker-logs:
	$(DOCKER_COMPOSE) logs -f

docker-ps:
	$(DOCKER_COMPOSE) ps

docker-exec-bootstrap:
	$(DOCKER_COMPOSE) exec p2p-bootstrap /bin/bash

docker-clean:
	@echo "$(GREEN)Cleaning Docker resources...$(NC)"
	$(DOCKER_COMPOSE) down -v --remove-orphans
	docker system prune -f

# Development setup
dev-setup: install
	@echo "$(GREEN)Setting up development environment...$(NC)"
	cp .env.example .env
	mkdir -p logs data cache temp certs
	@echo "$(GREEN)Generating self-signed certificates...$(NC)"
	openssl req -x509 -newkey rsa:4096 -keyout certs/key.pem -out certs/cert.pem -days 365 -nodes -subj "/CN=localhost"
	@echo "$(GREEN)Development environment ready!$(NC)"

# Generate documentation
docs:
	@echo "$(GREEN)Generating documentation...$(NC)"
	$(VENV)/bin/sphinx-build -b html docs/source docs/build/html

# Database migrations (for future use)
db-init:
	@echo "$(GREEN)Initializing database...$(NC)"
	$(VENV)/bin/alembic init alembic

db-migrate:
	@echo "$(GREEN)Creating migration...$(NC)"
	$(VENV)/bin/alembic revision --autogenerate -m "$(message)"

db-upgrade:
	@echo "$(GREEN)Applying migrations...$(NC)"
	$(VENV)/bin/alembic upgrade head

# Performance testing
perf-test:
	@echo "$(GREEN)Running performance tests...$(NC)"
	$(VENV)/bin/locust -f tests/performance/locustfile.py --host=http://localhost:8000

# Create release
release:
	@echo "$(GREEN)Creating release...$(NC)"
	$(VENV)/bin/$(PYTHON) setup.py sdist bdist_wheel

# Monitor logs
monitor-logs:
	tail -f logs/*.log

# Quick commands for development
.PHONY: q1 q2 q3
q1: run-bootstrap
q2: run-node1
q3: run-node2