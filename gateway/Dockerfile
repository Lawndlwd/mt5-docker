FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /srv

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY main.py .

RUN useradd --system --no-create-home gateway
USER gateway

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/gateway/ping', timeout=3)"

CMD ["sh", "-c", "exec uvicorn main:app --host 0.0.0.0 --port 8080 --proxy-headers --forwarded-allow-ips='*' --log-level ${LOG_LEVEL:-info}"]
