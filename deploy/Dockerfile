FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# Dashboard root is /srv/dashboard so app.config.BASE_DIR (parents[2]) resolves here.
WORKDIR /srv/dashboard

COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

COPY backend backend
COPY frontend frontend
COPY config/dashboard.example.yaml config/dashboard.example.yaml

# uid 1000 so a host ./data directory owned by the typical first user is writable.
RUN useradd --uid 1000 --create-home app \
    && mkdir -p /srv/dashboard/data /config \
    && chown -R app:app /srv/dashboard/data

# Host ./data is mounted at /srv/dashboard/data (default workbook.path: data/budget.xlsx works as-is).
# Host ./config is mounted read-only at /config (put dashboard.yaml there).
ENV DASH_DATA_DIR=/srv/dashboard/data \
    DASH_CONFIG=/config/dashboard.yaml

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--app-dir", "backend", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*"]
