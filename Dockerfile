FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data/cache

ENV HOST=0.0.0.0 \
    PORT=8080 \
    TRUST_PROXY=true \
    CACHE_DIR=/app/data/cache \
    PUBLIC_BASE_URL=https://music.yeuxark.com

EXPOSE 8080

CMD ["gunicorn", "-b", "0.0.0.0:8080", "-w", "2", "-k", "gevent", "--timeout", "120", "app:app"]
