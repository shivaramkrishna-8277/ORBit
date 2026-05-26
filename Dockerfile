FROM python:3.13-slim

# System deps (needed by fyers-apiv3 and some crypto libs)
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc \
        libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer-cached unless requirements.txt changes)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code
COPY . .

# Runtime directories (volumes will overlay these, but they must exist in the image)
RUN mkdir -p config data logs

# OAuth callback port (Fyers redirect URI)
EXPOSE 8080

CMD ["python", "main.py"]
