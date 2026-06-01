<div align="center">

<img src="krythbanner.png" alt="KRYTH Banner" width="100%"/>

# KRYTH

### Autonomous Coding Intelligence

Build applications, fix bugs, execute workflows, and automate development directly from your terminal.

<p align="center">
  <a href="#installation">Install</a> •
  <a href="#features">Features</a> •
  <a href="#examples">Examples</a> •
  <a href="#commands">Commands</a> •
  <a href="#roadmap">Roadmap</a>
</p>

<br>

![Python](https://img.shields.io/badge/Python-3.10+-blue)
![License](https://img.shields.io/badge/License-MIT-yellow)
![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20Linux%20%7C%20macOS-green)
![Open Source](https://img.shields.io/badge/Open%20Source-Yes-brightgreen)

### Your autonomous AI development partner.

Plan. Build. Debug. Test. Improve.

</div>

---

## What is KRYTH?

KRYTH is an autonomous AI coding agent designed for developers who want more than a chatbot.

Instead of only generating text, KRYTH can:

- Analyze projects
- Create execution plans
- Read and modify files
- Execute commands
- Build complete applications
- Generate tests
- Debug issues
- Maintain session memory
- Automate repetitive development tasks

All from your terminal.

---

## Why KRYTH?

Traditional AI tools stop after generating code.

KRYTH continues the workflow.

```text
Request
   ↓
Planning
   ↓
Tool Execution
   ↓
File Creation
   ↓
Testing
   ↓
Verification
   ↓
Summary
```

You focus on the outcome.

KRYTH handles the execution.

---

# Features

## Autonomous Planning

KRYTH analyzes requests and creates structured execution plans before making changes.

```bash
kryth "Build a FastAPI backend with authentication"
```

```text
✓ Analyze requirements
✓ Design architecture
✓ Create project structure
✓ Generate code
✓ Generate tests
✓ Verify implementation
```

---

## Intelligent File Operations

Create, edit, patch, refactor, and organize files automatically.

```bash
kryth "Refactor this Flask application into FastAPI"
```

Capabilities:

- Create files
- Edit existing code
- Apply patches
- Refactor projects
- Generate documentation
- Organize project structure

---

## Command Execution

KRYTH can safely execute development workflows.

Examples:

```bash
pip install
pytest
docker compose up
npm install
uv sync
git operations
```

Execution results are displayed in real time.

---

## Project Generation

Build complete projects from natural language.

### Web Applications

- React
- Next.js
- Vue
- SvelteKit

### Backends

- FastAPI
- Flask
- Django
- Express

### Databases

- PostgreSQL
- MySQL
- MongoDB
- SQLite

### Infrastructure

- Docker
- CI/CD
- Kubernetes
- Terraform

---

## Intelligent Debugging

Paste an error.

Get analysis, fixes, and implementation assistance.

```bash
kryth "Fix this SQLAlchemy session issue"
```

KRYTH can:

- Analyze stack traces
- Detect root causes
- Suggest fixes
- Apply code changes
- Verify corrections

---

## Test Generation

Generate:

- Unit tests
- Integration tests
- API tests
- End-to-end tests

```bash
kryth "Generate pytest tests for this project"
```

---

## Session Persistence

Resume work without losing context.

```bash
/session
```

Continue development exactly where you left off.

---

## Permission Profiles

Choose how autonomous KRYTH should be.

```bash
/profile
```

Profiles:

| Profile | Description |
|----------|-------------|
| Safe | Maximum confirmations |
| Default | Balanced workflow |
| Auto | More autonomous |
| YOLO | Minimal confirmations |
| Readonly | Analysis only |

---



## Project Memory

Teach KRYTH your standards.

```bash
/memory
```

Examples:

```text
Use FastAPI for APIs
Prefer async code
Maintain 90% test coverage
Use PostgreSQL in production
```

KRYTH remembers and follows project instructions.

---

## Beautiful Terminal Experience

Built specifically for developers.

Features:

- Rich terminal UI
- Live execution updates
- Planning visualizations
- Session analytics
- Workflow summaries
- Permission approvals
- Command dashboards

---

# Installation

## PyPI

```bash
pip install kryth
```

---

## With Bridge Support

```bash
pip install "kryth[bridge]"
```

---

## Using uv

```bash
uv pip install kryth
```

---

## Development Installation

```bash
git clone https://github.com/navadeep0508/KRYTH-Autonomous-AI-Coding-Agent.git

cd KRYTH-Autonomous-AI-Coding-Agent

pip install -e ".[bridge]"
```

---

# Quick Start

Launch KRYTH:

```bash
kryth
```

Example request:

```text
Create a FastAPI application with:

- JWT Authentication
- PostgreSQL
- SQLAlchemy
- Alembic
- Docker
- Unit Tests
```

KRYTH will:

```text
✓ Create architecture
✓ Generate models
✓ Configure database
✓ Implement authentication
✓ Create API routes
✓ Generate tests
✓ Verify project structure
```

---

# Examples

## Create a SaaS Starter

```bash
kryth "Build a SaaS starter template with authentication and payments"
```

---

## Create a REST API

```bash
kryth "Create a FastAPI CRUD API with PostgreSQL"
```

---

## Debug an Error

```bash
kryth "Fix this error: SQLAlchemy session already closed"
```

---

## Generate Tests

```bash
kryth "Generate pytest tests with high coverage"
```

---

## Refactor Legacy Code

```bash
kryth "Modernize this codebase and add type hints"
```

---

## Improve Performance

```bash
kryth "Optimize database queries and remove bottlenecks"
```

---

## Deploy an Application

```bash
kryth "Deploy this application using Docker"
```

---

# Workflow

```text
┌─────────────────┐
│ User Request    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Project Analysis│
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Planning Phase  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Tool Execution  │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ File Operations │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Verification    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Summary Report  │
└─────────────────┘
```

---

# Commands

## Core Commands

| Command | Description |
|----------|-------------|
| `/help` | Show available commands |
| `/status` | Session status and metrics |
| `/models` | View configured models |
| `/tools` | List available tools |
| `/config` | Configure KRYTH |
| `/profile` | Manage permission profiles |
| `/memory` | View project memory |
| `/session` | Resume previous sessions |
| `/diag` | Diagnostics and health checks |
| 

---

# Supported Use Cases

## Web Development

- Full-stack applications
- REST APIs
- Authentication systems
- Admin dashboards
- SaaS platforms

---

## Backend Engineering

- FastAPI
- Flask
- Django
- Node.js
- Database design

---

## DevOps

- Docker
- CI/CD
- Infrastructure
- Deployment automation

---

## Testing

- Unit testing
- Integration testing
- API testing
- Coverage improvements

---

## Maintenance

- Refactoring
- Bug fixing
- Performance optimization
- Documentation generation

---

# Architecture

```text
User
 │
 ▼
KRYTH CLI
 │
 ├── Planning Engine
 ├── Execution Engine
 ├── Tool System
 ├── Session Manager
 ├── Memory System
 └── Model Router
 │
 ▼
Development Workflow
```

---

# Roadmap

## Current

- Autonomous planning
- Tool execution
- File editing
- Session persistence
- Multiple model support
- Browser bridge
- Permission profiles

## Upcoming

- Multi-agent workflows
- MCP integration ecosystem
- Team collaboration
- Cloud execution
- Visual workflow inspector
- Enhanced deployment support

---

# Contributing

Contributions are welcome.

```bash
git clone https://github.com/navadeep0508/KRYTH-Autonomous-AI-Coding-Agent.git

cd KRYTH-Autonomous-AI-Coding-Agent

pip install -e .
```

Create a feature branch and submit a pull request.

---

# Community

### Website

https://kryth.vercel.app

### GitHub

https://github.com/navadeep0508/KRYTH-Autonomous-AI-Coding-Agent

### Issues

Report bugs and request features through GitHub Issues.

---

# License

MIT License

---

<div align="center">

# KRYTH

### Autonomous Coding Intelligence

Build faster. Automate more. Focus on creating.

⭐ Star the project if you find it useful.

</div>