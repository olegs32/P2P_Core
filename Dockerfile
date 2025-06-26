# Multi-stage Dockerfile for P2P Admin System

# Stage 1: Builder
FROM python:3.11-slim as builder

# Install build dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    make \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir --user -r requirements.txt

# Stage 2: Runtime
FROM python:3.11-slim

# Install runtime dependencies
RUN apt-get update && apt-get install -y \
    curl \
    iputils-ping \
    traceroute \
    net-tools \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 -s /bin/bash p2puser

# Set working directory
WORKDIR /app

# Copy Python packages from builder
COPY --from=builder /root/.local /home/p2puser/.local

# Copy application code
COPY --chown=p2puser:p2puser . .

# Create necessary directories
RUN mkdir -p data logs certs cache temp && \
    chown -R p2puser:p2puser data logs certs cache temp

# Switch to non-root user
USER p2puser

# Add user's local bin to PATH
ENV PATH=/home/p2puser/.local/bin:$PATH

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV NODE_HOST=0.0.0.0
ENV NODE_PORT=8000
ENV DHT_PORT=5678

# Expose ports
EXPOSE 8000 5678

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:${NODE_PORT}/health || exit 1

# Default command
CMD ["python", "run.py"]

# === Dockerfile.streamlit ===
# Separate Dockerfile for Streamlit admin interface

FROM python:3.11-slim as streamlit

# Install dependencies
RUN apt-get update && apt-get install -y \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -u 1000 -s /bin/bash streamlituser

# Set working directory
WORKDIR /app

# Copy requirements
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY --chown=streamlituser:streamlituser . .

# Switch to non-root user
USER streamlituser

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV STREAMLIT_SERVER_PORT=8501
ENV STREAMLIT_SERVER_ADDRESS=0.0.0.0

# Expose Streamlit port
EXPOSE 8501

# Default command
CMD ["streamlit", "run", "admin/app.py"]