# Build stage for Python
FROM python:3.11-slim as python-builder

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Build stage for React UI
FROM node:18-alpine as ui-builder

WORKDIR /app/ui

# Install dependencies
COPY ui/package*.json ./
RUN npm ci

# Build the UI
COPY ui/ ./
RUN npm run build

# Production stage
FROM python:3.11-slim

WORKDIR /app

# Copy Python dependencies
COPY --from=python-builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=python-builder /usr/local/bin /usr/local/bin

# Copy built UI
COPY --from=ui-builder /app/ui/dist /app/static

# Copy application code
COPY src/ /app/src/
COPY configs/ /app/configs/

# Create output directories
RUN mkdir -p /app/output /app/audit

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run the application
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
