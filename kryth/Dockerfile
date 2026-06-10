# KRYTH - Autonomous AI Coding Agent
# Dockerfile

# Use official Python image
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    curl \
    git \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy project files
COPY pyproject.toml README.md ./
COPY src/ src/

# Install KRYTH and dependencies
RUN pip install --no-cache-dir -e ".[bridge]"

# Install Playwright browsers
RUN playwright install chromium

# Create directories for persistence
RUN mkdir -p /root/.kryth /workspace

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PIP_NO_CACHE_DIR=1

# Volume mounts
VOLUME ["/root/.kryth", "/workspace"]

# Default working directory
WORKDIR /workspace

# Entry point
ENTRYPOINT ["kryth"]

# Default command (interactive)
CMD ["--help"]