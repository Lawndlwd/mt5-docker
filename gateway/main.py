"""Linux gateway in front of the MT5 workers running inside the Windows VM.

Each worker owns exactly one MT5 terminal, and an MT5 terminal can only be logged
into one account at a time. The gateway therefore sends at most one request to a
worker at once and queues the rest, so two users can never read each other's data.
Everything else (paths, bodies, status codes) is passed through unchanged.
"""

import asyncio
import os
import secrets
import time

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

WORKER_HOST = os.getenv("WORKER_HOST", "windows")
WORKER_BASE_PORT = int(os.getenv("WORKER_BASE_PORT", "9001"))
MT5_INSTANCES = int(os.getenv("MT5_INSTANCES", "2"))
GATEWAY_API_KEY = os.getenv("GATEWAY_API_KEY", "")
QUEUE_TIMEOUT = float(os.getenv("QUEUE_TIMEOUT", "60"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "120"))
# After a connection failure a worker is kept out of rotation for this long.
COOLDOWN = float(os.getenv("WORKER_COOLDOWN", "5"))

WORKERS = [f"http://{WORKER_HOST}:{WORKER_BASE_PORT + i}" for i in range(MT5_INSTANCES)]
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
    "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}

app = FastAPI(title="MT5 API Gateway", docs_url=None, redoc_url=None, openapi_url=None)
free_workers: asyncio.Queue[str] = asyncio.Queue()
client: httpx.AsyncClient


@app.on_event("startup")
async def startup() -> None:
    global client
    client = httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=5))
    for worker in WORKERS:
        free_workers.put_nowait(worker)


@app.on_event("shutdown")
async def shutdown() -> None:
    await client.aclose()


def release(worker: str, delay: float = 0) -> None:
    if delay:
        asyncio.get_running_loop().call_later(delay, free_workers.put_nowait, worker)
    else:
        free_workers.put_nowait(worker)


def authorized(request: Request) -> bool:
    if not GATEWAY_API_KEY:
        return True
    return secrets.compare_digest(request.headers.get("x-api-key", ""), GATEWAY_API_KEY)


@app.get("/gateway/ping")
async def gateway_ping() -> dict:
    """Liveness of the gateway itself (used by the Docker healthcheck)."""
    return {"status": "ok"}


@app.get("/gateway/health")
async def gateway_health() -> JSONResponse:
    """Probes every worker directly (bypasses the queue). 200 if at least one is up."""
    async def probe(worker: str) -> dict:
        started = time.monotonic()
        try:
            r = await client.get(f"{worker}/", timeout=5)
            return {"worker": worker, "up": r.status_code == 200,
                    "ms": round((time.monotonic() - started) * 1000)}
        except httpx.HTTPError as e:
            return {"worker": worker, "up": False, "error": type(e).__name__}

    results = await asyncio.gather(*(probe(w) for w in WORKERS))
    up = sum(r["up"] for r in results)
    return JSONResponse(
        status_code=200 if up else 503,
        content={"status": "ok" if up else "down", "workers_up": up,
                 "workers_total": len(WORKERS), "idle": free_workers.qsize(),
                 "workers": results},
    )


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy(path: str, request: Request) -> Response:
    if request.method != "OPTIONS" and path not in ("", "health") and not authorized(request):
        return JSONResponse(status_code=401, content={"detail": "Invalid or missing X-API-Key."})

    body = await request.body()
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in HOP_BY_HOP and k.lower() != "x-api-key"}

    # Try each worker at most once; only connection failures (request never
    # reached MT5) are retried on another worker.
    for _ in range(len(WORKERS)):
        try:
            worker = await asyncio.wait_for(free_workers.get(), timeout=QUEUE_TIMEOUT)
        except asyncio.TimeoutError:
            return JSONResponse(status_code=503, content={"detail": "All MT5 terminals are busy, retry later."})
        try:
            upstream = await client.request(
                request.method, f"{worker}/{path}", params=request.query_params,
                content=body, headers=headers,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            release(worker, delay=COOLDOWN)
            continue
        except httpx.TimeoutException:
            release(worker)
            return JSONResponse(status_code=504, content={"detail": "MT5 terminal did not respond in time."})
        release(worker)
        out_headers = {k: v for k, v in upstream.headers.items()
                       if k.lower() not in HOP_BY_HOP and k.lower() != "content-encoding"}
        return Response(content=upstream.content, status_code=upstream.status_code, headers=out_headers)

    return JSONResponse(status_code=503, content={"detail": "MT5 workers are not reachable (Windows may still be booting)."})
