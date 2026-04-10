# Lightspeed Agent Documentation

Welcome to the Red Hat Lightspeed Agent for Google Cloud documentation.

## Overview

The Lightspeed Agent is an A2A-ready (Agent-to-Agent) service that provides AI-powered access to Red Hat Insights. It enables natural language interaction with Red Hat's infrastructure management tools.

## Documentation

### Getting Started

- [README](../README.md) - Quick start guide and installation
- [Configuration](configuration.md) - Environment variables and settings

### Architecture

- [Architecture Overview](architecture.md) - System design, components, database schema, and ADRs

### API Reference

- [API Documentation](api.md) - Endpoints, request/response formats
- [Authentication](authentication.md) - OAuth 2.0 and JWT validation

### Deployment

- [Container Deployment](../README.md#container-deployment-podman) - Local Podman deployment
- [Cloud Run Deployment](../deploy/cloudrun/README.md) - Google Cloud Run deployment

### Integration

- [MCP Server Integration](mcp-integration.md) - Red Hat Lightspeed MCP server and console.redhat.com APIs
- [Marketplace Integration](marketplace.md) - Google Cloud Marketplace, DCR, and billing

### Operations

- [Metering](metering.md) - Usage tracking and billing metrics
- [Rate Limiting](rate-limiting.md) - Request throttling and quotas
- [VPC flow logs and Splunk export](vpc-flow-logs.md) - SEC-NET-REQ-5, subnet flow logs, Pub/Sub export (optional)
- [Troubleshooting](troubleshooting.md) - Common issues and solutions

## Quick Links

| Topic | Description |
|-------|-------------|
| [AgentCard](api.md#get-well-knownagentjson) | A2A agent discovery |
| [Authentication](authentication.md#token-introspection) | Token validation setup |
| [Environment Variables](configuration.md#environment-variables) | Configuration reference |
| [Health Checks](api.md#health-endpoints) | Monitoring endpoints |
| [Metering](metering.md#local-testing) | Usage tracking and testing |
| [Rate Limiting](rate-limiting.md#local-testing) | Throttling and quotas |

## Features

- **AI-Powered**: Built with Google ADK and Gemini 2.5 Flash
- **A2A Protocol**: Interoperates with other agents
- **OAuth 2.0**: Secure authentication via Red Hat SSO
- **Marketplace Ready**: Google Cloud Marketplace integration
- **Usage Metering**: Automatic billing and quota management
- **Container Native**: Runs on Podman, Kubernetes, or Cloud Run

## Red Hat Insights Services

The agent provides access to:

| Service | Description |
|---------|-------------|
| **Advisor** | System recommendations and configuration assessment |
| **Inventory** | System management and tracking |
| **Vulnerability** | CVE analysis and security threats |
| **Remediations** | Issue resolution and playbooks |
| **Planning** | RHEL upgrade and migration planning |
| **Image Builder** | Custom RHEL image creation |

## Support

- **Issues**: [GitHub Issues](https://github.com/your-org/lightspeed-agent/issues)
- **Red Hat Support**: [Red Hat Customer Portal](https://access.redhat.com)
