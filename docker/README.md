# Docker Deployment Guide

This document explains how to deploy and use KRYTH in Docker containers.

## Overview

KRYTH provides Docker support through a pre-built container image that includes all dependencies and configuration for running the AI coding agent in containerized environments.

## Quick Start

### Using Docker Compose

1. **Build and start the container**

```bash
cd docker
API_KEY="your_openrouter_api_key" docker-compose up -d
```

2. **Connect to your workspace**

The container mounts your current directory (`$KRYTH_WORKSPACE:-..`) as `/workspace` inside the container. You can now interact with your code through KRYTH.

### Using Docker Image Directly

1. **Build the image**

```bash
docker build -t kryth:dev ..
```

2. **Run the container**

```bash
docker run -d \
  --name kryth \
  -v "$(pwd):/workspace" \
  -v "$(pwd)/.kryth:/config" \
  -e API_KEY="your_openrouter_api_key" \
  kryth:dev
```

## Configuration

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `API_KEY` | OpenRouter API key for authentication | - |
| `BASE_URL` | Custom API base URL (optional) | - |
| `TERM` | Terminal type (for proper coloring) | `xterm-256color` |

### Volume Mappings

#### Workspace Volume
- **Host**: `${KRYTH_WORKSPACE:-..}` (current directory by default)
- **Container**: `/workspace`
- **Purpose**: Project directory containing your code to be modified by KRYTH

#### Config Volume
- **Host**: `.kryth/` (relative to project root)
- **Container**: `/config`
- **Purpose**: KRYTH configuration files, including `~/.config/kryth/config.toml` (user-wide) and `.kryth/config.toml` (project-specific)

## Important Notes

### Required Setup

1. **API Key**: You need an OpenRouter API key to use KRYTH. Get one at [openrouter.ai](https://openrouter.ai)

2. **Installation**: Install the `kryth` package locally first (outside containers):

```bash
pip install kryth
```

3. **Authentication**: Run `kryth login` once to authenticate with OpenRouter (opens browser OAuth) or `kryth login --paste` to paste API key directly

### Working Directory

- The container's working directory is `/workspace`
- Changes made by KRYTH will be reflected in your host machine through the volume mapping
- Ensure you have proper file system permissions for write operations

### Configuration Persistence

- User configuration (`~/.config/kryth/`) is persisted in the `.kryth` volume
- Project-specific configuration (`.kryth/`) is mounted from your project directory
- API keys and other sensitive data should be managed carefully in container environments

## Development Usage

### Interactive Mode

Once the container is running, you can interact with KRYTH:

```bash
docker exec -it kryth kryth "your prompt here"
```

### Multi-step Tasks

1. Start your container with `docker-compose up -d`
2. Execute interactive sessions with `docker exec -it kryth` and pass prompts to KRYTH
3. Monitor changes in your workspace directory

## Troubleshooting

### Common Issues

#### "Command not found: kryth"
- Ensure the `kryth` package is installed in the container
- This is handled automatically in the Docker build process

#### Permission denied
- Check file permissions in your workspace directory
- The container user may need write access to files

#### Configuration not found
- Verify your `.kryth/config.toml` file exists with proper settings
- User authentication may need to be done outside the container

### Debugging

To inspect container state and logs:

```bash
docker ps
docker logs kryth
docker exec -it kryth bash
```

### Stopping/Removing

```bash
docker-compose down
# Or for direct Docker usage:
docker stop kryth
docker rm kryth
```

## Production Considerations

### Security

- Use `docker login` to pull images from private registries
- Consider using environment-specific Dockerfiles for production deployments
- Implement proper API key management (Kubernetes secrets, environment variables)

### Performance

- For better performance, mount your workspace directory with appropriate caching (e.g., Docker volumes)
- Consider using Docker Compose's `sync` service for faster development iteration

### Monitoring

- Monitor container resource usage with `docker stats kryth`
- Consider implementing logging to a centralized system

## Advanced Usage

### Custom Dockerfiles

The base Dockerfile in `docker/Dockerfile` can be extended for custom requirements:

```dockerfile
FROM kryth:latest
USER root
RUN apt-get update && apt-get install -y some-package
USER appuser
```

### Multi-Stage Builds

For optimized production builds:

```dockerfile
FROM kryth:latest AS production
# Production-specific optimizations
```

### Network Configuration

Expose KRYTH to external networks:

```yaml
services:
  kryth:
    ports:
      - "8080:80"  # If using a web interface
```

## Documentation

- Main project README: [`../README.md`](../README.md)
- Docker reference documentation: Look at the main README for installation and usage
- Configuration options: Check `~/config/kryth/config.toml` for available settings

## Support

For issues with Docker deployment, refer to the main project issues or documentation.

---

*This documentation is auto-generated based on the current Docker configuration.*

## Related Files

- [`Dockerfile`](dockerfile/): Container build specification
- [`docker-compose.yml`](docker-compose.yml): Docker Compose configuration
- [`../pyproject.toml`](../pyproject): Project dependencies and build configuration