FROM python:3.12-slim

ENV LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/
COPY scripts/ ./scripts/
COPY configs/ ./configs/

ENV GONGSI_RAW_DIR=/data \
    GONGSI_META_DIR=/data \
    GONGSI_DERIVED_DIR=/stores \
    GONGSI_LLM_CACHE=/srv/var/llm_cache \
    ANSWER_STATE_DIR=/srv/var/answer_state \
    PORT=80

ENV WATCHDOG_WARMUP_GRACE_SEC=240 \
    WATCHDOG_INTERVAL_SEC=30 \
    WATCHDOG_RETRY_INTERVAL_SEC=5 \
    WATCHDOG_PROBE_TIMEOUT_SEC=20 \
    WATCHDOG_FAIL_LIMIT=3 \
    WATCHDOG_DEEP_EVERY=20

VOLUME ["/srv/var"]
EXPOSE 80

HEALTHCHECK --interval=30s --timeout=10s --start-period=240s --retries=5 \
  CMD python -c "import urllib.request,sys,os,json; \
r=urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','80')+'/health',timeout=8); \
sys.exit(0 if json.load(r).get('status')=='ok' else 1)"

CMD ["python", "scripts/supervise.py"]
