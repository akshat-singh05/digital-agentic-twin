FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY alembic.ini seed_data.py ./
COPY app ./app
COPY frontend ./frontend
COPY migrations ./migrations

# Run as an unprivileged user; the database lives on a volume at /data.
RUN useradd --system --no-create-home twin && mkdir /data && chown twin /data
USER twin

# SECRET_KEY has no default: the app refuses to start without one (DEBUG=false).
ENV DEBUG=false \
    DATABASE_URL=sqlite:////data/digital_twin.db \
    CORS_ORIGINS=http://127.0.0.1:8000,http://localhost:8000
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=3s --start-period=10s \
  CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/health', timeout=2)"

# One worker: the rate limiter keeps its counters in process memory.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
