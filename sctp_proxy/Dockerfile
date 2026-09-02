FROM python:3.11-slim

# Install system dependencies once at build time
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libsctp-dev \
        gcc \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies once at build time
RUN pip install --no-cache-dir pysctp docker

# Copy proxy script into image
COPY sctp_proxy_v5.py /app/sctp_proxy_v5.py
WORKDIR /app

# Default command: run the proxy
# Override with docker-compose if you need different behavior
CMD ["python3", "/app/sctp_proxy_v5.py"]
