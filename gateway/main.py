"""Linux gateway in front of the MT5 workers running inside the Windows VM.

Each worker owns exactly one long-lived MT5 terminal, and a terminal can only be
logged into one account at a time. The gateway sends at most one request to a
worker at once and queues the rest (first come, first served), so two users can
never read each other's data.

Scheduling: a free worker that is already logged into the requested account is
preferred (no re-login needed); otherwise the worker idle the longest is used.
GET /gateway/queue reports load and an estimated wait so callers can show it.
Paths, bodies and status codes are passed through unchanged.
"""

import asyncio
import json
import math
import os
import secrets
import time
from collections import deque
from typing import Deque, Dict, List, Optional, Tuple

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse

WORKER_HOST = os.getenv("WORKER_HOST", "windows")
WORKER_BASE_PORT = int(os.getenv("WORKER_BASE_PORT", "9001"))
MT5_INSTANCES = int(os.getenv("MT5_INSTANCES", "2"))
GATEWAY_API_KEY = os.getenv("GATEWAY_API_KEY", "")
QUEUE_TIMEOUT = float(os.getenv("QUEUE_TIMEOUT", "60"))
REQUEST_TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "120"))
# Seconds between worker health probes.
HEALTH_INTERVAL = float(os.getenv("HEALTH_INTERVAL", "15"))

WORKERS = [f"http://{WORKER_HOST}:{WORKER_BASE_PORT + i}" for i in range(MT5_INSTANCES)]
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te",
    "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}

app = FastAPI(title="MT5 API Gateway", docs_url=None, redoc_url=None, openapi_url=None)
client: httpx.AsyncClient


class Scheduler:
    """Free-worker pool + FIFO wait queue with account affinity, health and timing stats.

    Unhealthy workers (failed probe or a request that timed out) stay out of rotation
    until a probe sees them healthy again; requests wait in the queue meanwhile.
    """

    def __init__(self, workers: List[str]):
        self.workers = list(workers)
        self.free: List[str] = list(workers)              # idle workers, longest idle first
        self.waiters: Deque[Tuple[asyncio.Future, Optional[int]]] = deque()
        self.account: Dict[str, Optional[int]] = {w: None for w in workers}
        self.healthy: Dict[str, bool] = {w: True for w in workers}
        self.down_since: Optional[float] = None            # monotonic time all workers went down
        self.avg_ms = 4000.0                               # EMA of request time (seeded)
        self.switch_ms = 8000.0                            # EMA of requests that needed a login

    def _usable(self) -> List[int]:
        return [i for i, w in enumerate(self.free) if self.healthy.get(w, False)]

    def _take(self, login: Optional[int]) -> Optional[str]:
        usable = self._usable()
        if not usable:
            return None
        for i in usable:
            if login is not None and self.account.get(self.free[i]) == login:
                return self.free.pop(i)
        return self.free.pop(usable[0])

    def _dispatch(self) -> None:
        while self.waiters and self._usable():
            fut, login = self.waiters.popleft()
            if fut.done():
                continue
            fut.set_result(self._take(login))

    async def acquire(self, login: Optional[int], timeout: float) -> Tuple[str, int]:
        """Returns (worker, queue position at arrival). Raises asyncio.TimeoutError."""
        if not self.waiters:
            worker = self._take(login)
            if worker:
                return worker, 0
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        entry = (fut, login)
        self.waiters.append(entry)
        position = len(self.waiters)
        try:
            return await asyncio.wait_for(fut, timeout), position
        except asyncio.TimeoutError:
            if entry in self.waiters:
                self.waiters.remove(entry)
            elif fut.done() and not fut.cancelled():
                self.release(fut.result())  # handed over just as we gave up
            raise

    def release(self, worker: str) -> None:
        self.free.append(worker)
        self._dispatch()

    def set_health(self, worker: str, ok: bool) -> None:
        was = self.healthy.get(worker)
        self.healthy[worker] = ok
        if not ok:
            self.account[worker] = None
        up = sum(self.healthy.values())
        self.down_since = None if up else (self.down_since or time.monotonic())
        if ok and not was:
            self._dispatch()

    def record(self, worker: str, login: Optional[int], ok: bool, ms: float, switched: bool) -> None:
        self.account[worker] = login if ok else None
        self.avg_ms = 0.8 * self.avg_ms + 0.2 * ms
        if switched:
            self.switch_ms = 0.8 * self.switch_ms + 0.2 * ms

    def status(self) -> dict:
        up = sum(self.healthy.values())
        n = max(1, up)
        waiting = len(self.waiters)
        idle = len(self._usable())
        busy = up - idle
        rounds = 0 if idle and not waiting else math.ceil((waiting + 1) / n)
        return {
            "workers": len(self.workers),
            "healthy": up,
            "busy": max(0, busy),
            "idle": idle,
            "waiting": waiting,
            "avg_ms": round(self.avg_ms),
            "login_ms": round(self.switch_ms),
            "eta_ms": round(rounds * self.avg_ms + self.switch_ms) if up else None,
            "down_s": round(time.monotonic() - self.down_since) if self.down_since else 0,
        }


sched = Scheduler(WORKERS)


async def probe_worker(worker: str) -> dict:
    started = time.monotonic()
    try:
        r = await client.get(f"{worker}/health", timeout=8)
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        return {"worker": worker, "up": r.status_code == 200, "state": body.get("state"),
                "ms": round((time.monotonic() - started) * 1000)}
    except httpx.HTTPError as e:
        return {"worker": worker, "up": False, "error": type(e).__name__}


async def health_loop() -> None:
    """Keeps sched.healthy current so requests never go to a dead or frozen terminal."""
    while True:
        results = await asyncio.gather(*(probe_worker(w) for w in WORKERS))
        for r in results:
            if sched.healthy.get(r["worker"]) != r["up"]:
                print(f"[gateway] {r['worker']} {'up' if r['up'] else 'DOWN'} {r.get('state') or r.get('error')}", flush=True)
            sched.set_health(r["worker"], r["up"])
        await asyncio.sleep(HEALTH_INTERVAL)


@app.on_event("startup")
async def startup() -> None:
    global client
    client = httpx.AsyncClient(timeout=httpx.Timeout(REQUEST_TIMEOUT, connect=5))
    asyncio.create_task(health_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    await client.aclose()


def authorized(request: Request) -> bool:
    if not GATEWAY_API_KEY:
        return True
    return secrets.compare_digest(request.headers.get("x-api-key", ""), GATEWAY_API_KEY)


def login_of(body: bytes) -> Optional[int]:
    try:
        value = json.loads(body or b"{}").get("login")
        return int(value) if value is not None else None
    except (ValueError, TypeError, AttributeError):
        return None


@app.get("/gateway/ping")
async def gateway_ping() -> dict:
    """Liveness of the gateway itself (used by the Docker healthcheck)."""
    return {"status": "ok"}


@app.get("/gateway/queue")
async def gateway_queue(request: Request) -> JSONResponse:
    """Load and estimated wait for a new request (counts only, no account data)."""
    if not authorized(request):
        return JSONResponse(status_code=401, content={"detail": "Invalid or missing X-API-Key."})
    return JSONResponse(content=sched.status())


@app.get("/gateway/health")
async def gateway_health() -> JSONResponse:
    """Probes every worker directly (bypasses the queue). 200 if at least one is up."""
    results = await asyncio.gather(*(probe_worker(w) for w in WORKERS))
    up = sum(r["up"] for r in results)
    return JSONResponse(
        status_code=200 if up else 503,
        content={"status": "ok" if up else "down", "workers_up": up,
                 "workers_total": len(WORKERS), **sched.status(), "workers": results},
    )


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
async def proxy(path: str, request: Request) -> Response:
    if request.method != "OPTIONS" and path not in ("", "health") and not authorized(request):
        return JSONResponse(status_code=401, content={"detail": "Invalid or missing X-API-Key."})

    body = await request.body()
    login = login_of(body)
    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in HOP_BY_HOP and k.lower() != "x-api-key"}
    arrived = time.monotonic()

    # Try each worker at most once; only connection failures (request never
    # reached MT5) are retried on another worker.
    for _ in range(len(WORKERS)):
        try:
            worker, position = await sched.acquire(login, QUEUE_TIMEOUT)
        except asyncio.TimeoutError:
            return JSONResponse(status_code=503, content={"detail": "All MT5 terminals are busy, retry later.", **sched.status()})
        switched = sched.account.get(worker) != login
        started = time.monotonic()
        try:
            upstream = await client.request(
                request.method, f"{worker}/{path}", params=request.query_params,
                content=body, headers=headers,
            )
        except (httpx.ConnectError, httpx.ConnectTimeout):
            sched.set_health(worker, False)  # back in rotation once a probe sees it up
            sched.release(worker)
            continue
        except httpx.TimeoutException:
            sched.set_health(worker, False)  # likely frozen: the supervisor watchdog restarts it
            sched.release(worker)
            return JSONResponse(status_code=504, content={"detail": "MT5 terminal did not respond in time."})
        ms = (time.monotonic() - started) * 1000
        if login is not None:
            sched.record(worker, login, upstream.status_code < 400, ms, switched)
        sched.release(worker)
        out_headers = {k: v for k, v in upstream.headers.items()
                       if k.lower() not in HOP_BY_HOP and k.lower() != "content-encoding"}
        out_headers["X-Queue-Position"] = str(position)
        out_headers["X-Queue-Wait-Ms"] = str(round((started - arrived) * 1000))
        return Response(content=upstream.content, status_code=upstream.status_code, headers=out_headers)

    return JSONResponse(status_code=503, content={"detail": "MT5 workers are not reachable (Windows may still be booting).", **sched.status()})
