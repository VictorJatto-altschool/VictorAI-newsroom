FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY victor ./victor
COPY config ./config
RUN pip install --no-cache-dir ".[postgres]"

ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8 PORT=10000
EXPOSE 10000

# One process: a collection cycle every 15 minutes, Telegram buttons answered in between,
# and a tiny HTTP server so the host (and an uptime pinger) can see it is alive.
CMD ["sh", "-c", "python -m victor loop --minutes 15 --lease --http-port ${PORT}"]
