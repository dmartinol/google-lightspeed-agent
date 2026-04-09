# Red Hat Lightspeed Agent for Google Cloud - Container Image
# Built on Red Hat Universal Base Image (UBI)

# =============================================================================
# Build Stage
# =============================================================================
FROM registry.access.redhat.com/ubi10/python-312-minimal:latest as builder

WORKDIR /opt/app-root/src

# Install Python dependencies
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir ".[agent]"

# =============================================================================
# Production Stage
# =============================================================================
FROM registry.access.redhat.com/ubi10/python-312-minimal:latest as production

# Labels for container metadata
LABEL org.opencontainers.image.title="Red Hat Lightspeed Agent for Google Cloud"
LABEL org.opencontainers.image.description="A2A-ready agent for Red Hat Insights using Google ADK"
LABEL org.opencontainers.image.vendor="Red Hat"
LABEL org.opencontainers.image.licenses="Apache-2.0"
LABEL io.k8s.display-name="Red Hat Lightspeed Agent for Google Cloud"
LABEL io.k8s.description="A2A-ready agent for Red Hat Insights using Google ADK"

WORKDIR /opt/app-root/src

# Copy installed packages from builder
COPY --from=builder /opt/app-root/lib/python3.12/site-packages /opt/app-root/lib/python3.12/site-packages

# Copy application code
COPY src/ ./src/
COPY agent.py ./
COPY pyproject.toml README.md ./

# Install the application
RUN pip install --no-cache-dir -e ".[agent]"

# Create directory for data (if using SQLite)
RUN mkdir -p /opt/app-root/src/data

# Environment variables
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AGENT_HOST=0.0.0.0 \
    AGENT_PORT=8000

# Expose the agent port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

# UBI images already run as non-root user (UID 1001)
# Default command
CMD ["python", "-m", "lightspeed_agent.main"]
