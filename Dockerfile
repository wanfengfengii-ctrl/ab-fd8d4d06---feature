FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_PORT=8080

WORKDIR /app

# Application code, tests and the one-shot verification script.
COPY app ./app
COPY tests ./tests
COPY verify ./verify

# Build-time syntax/build check for every module.
RUN python -m compileall -q app tests verify \
    && chmod +x verify/verify.sh

EXPOSE 8080

HEALTHCHECK --interval=2s --timeout=2s --start-period=1s --retries=15 \
    CMD python -c "import os,urllib.request,sys; \
port=os.environ.get('APP_PORT','8080'); \
sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:%s/healthz'%port, timeout=2).status==200 else 1)"

CMD ["python", "-m", "app.server"]
