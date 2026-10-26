import hmac
import json
import os
import threading
import time
import uuid
from collections import OrderedDict, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, Header, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import chat
from .data import MAX_BYTES, analyze, read_csv, suggest_mapping
from .identification import identify
from .pann_adapter import available as pann_available
from .parameter_comparison import with_ground_truth
from .schemas import AnalysisRequest, ChatRequest, FitRequest
from .report import ReportRequest, build_pdf

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
from . import archive
archive.store = archive.ArchiveStore(os.getenv("ARCHIVE_DIR") or ROOT / "runtime" / "archive")
TOKEN = os.getenv("APP_ACCESS_TOKEN", "")
if (os.getenv("APP_ENV") == "production" or os.getenv("RENDER")) and len(TOKEN) < 20:
    raise RuntimeError("Production requires APP_ACCESS_TOKEN of at least 20 characters.")
app = FastAPI(title="Buck Lab API", version="0.1.0")
origins = [s.strip().rstrip("/") for s in os.getenv("CORS_ORIGINS", "http://localhost:8000,http://127.0.0.1:8000,http://localhost:5500,http://127.0.0.1:5500").split(",") if s.strip()]
lock = threading.RLock()
datasets = OrderedDict()
jobs = OrderedDict()
rate = defaultdict(deque)
pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="buck-fit")
TTL = 3600


@app.middleware("http")
async def size_limit(request: Request, call_next):
    length = request.headers.get("content-length")
    if length and (not length.isdigit() or int(length) > MAX_BYTES + 1024 * 1024):
        return JSONResponse(status_code=413, content={"detail": "请求过大，CSV 上限为 32 MB。"})
    # Reject missing/incorrect credentials before parsing an upload body.
    if TOKEN and request.url.path.startswith("/api/") and request.url.path != "/api/health" and request.method != "OPTIONS":
        if not hmac.compare_digest(request.headers.get("authorization", ""), "Bearer " + TOKEN):
            return JSONResponse(status_code=401, content={"detail": "访问口令不正确，请在连接设置中输入后端 APP_ACCESS_TOKEN。"})
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    if request.url.path.startswith(("/api/archive", "/api/jobs")):
        response.headers["Cache-Control"] = "no-store"
    return response


def cleanup():
    now = time.time()
    active_datasets = {j["dataset_id"] for j in jobs.values() if j.get("status") in ("queued", "running")}
    for store in (datasets, jobs):
        for key, item in list(store.items()):
            if store is datasets and key in active_datasets:
                continue
            if now - item["created"] > TTL and item.get("status") not in ("queued", "running"):
                del store[key]
    for key, events in list(rate.items()):
        if not events or now - events[-1] > 120:
            del rate[key]


def session(x_session_id: str = Header(...)):
    try:
        uuid.UUID(x_session_id)
    except ValueError:
        raise HTTPException(400, "无效会话，请刷新页面。")
    with lock:
        cleanup()
    return x_session_id


def throttle(owner, action, maximum):
    with lock:
        now = time.time()
        events = rate[(owner, action)]
        while events and now - events[0] > 60:
            events.popleft()
        if len(events) >= maximum:
            raise HTTPException(429, "请求较频繁，请稍后再试。")
        events.append(now)


def get_dataset(identifier, owner):
    with lock:
        item = datasets.get(identifier)
        if not item or item["owner"] != owner:
            raise HTTPException(404, "数据已过期或不属于当前会话，请重新上传。")
        return item["data"]


def add_dataset(raw, name, owner):
    try:
        data = read_csv(raw, name)
    except (ValueError, UnicodeError) as exc:
        raise HTTPException(422, str(exc))
    identifier = uuid.uuid4().hex
    with lock:
        if sum(item["owner"] == owner for item in datasets.values()) >= 2:
            previous = next(k for k, v in datasets.items() if v["owner"] == owner)
            del datasets[previous]
        size = int(data.frame.memory_usage(deep=True).sum()) + len(raw)
        if sum(v["size"] for v in datasets.values()) + size > 96 * 1024 * 1024 or len(datasets) >= 12:
            raise HTTPException(503, "服务器数据容量已满，请删除不用的数据或稍后重试。")
        datasets[identifier] = {"owner": owner, "data": data, "raw": raw, "created": time.time(), "size": size}
    return {"dataset_id": identifier, "filename": data.name, "columns": list(data.frame.columns),
            "rows": len(data.frame), "sha256": data.sha256, "suggested_mapping": suggest_mapping(data)}


def compact(data):
    return {k: v for k, v in data.items() if k != "chart"}


@app.get("/api/health")
def health():
    return {"status": "ok", "version": "0.1.0", "chat_mode": "deepseek" if os.getenv("DEEPSEEK_API_KEY") else "local",
            "model": os.getenv("DEEPSEEK_MODEL", "deepseek-flash"), "requires_token": bool(TOKEN),
            "pann_available": pann_available(),
            "source_url": os.getenv("SOURCE_URL", ""), "archive_enabled": True,
            "archive_persistent": not bool(os.getenv("RENDER")) or (bool(os.getenv("ARCHIVE_DIR")) and any(os.path.ismount(p) for p in [archive.store.directory, *archive.store.directory.parents] if str(p) not in ("/", ".")))}


@app.post("/api/datasets")
async def upload(file: UploadFile = File(...), owner: str = Depends(session)):
    throttle(owner, "upload", 8)
    try:
        if not (file.filename or "").lower().endswith(".csv"):
            raise HTTPException(422, "请上传 .csv 文件。")
        raw = await file.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise HTTPException(413, "CSV 超过 32 MB。")
        return add_dataset(raw, Path(file.filename).name, owner)
    finally:
        await file.close()


@app.post("/api/reports/pdf")
def export_pdf(body: ReportRequest, owner: str = Depends(session)):
    # The caller supplies its saved result snapshot: exporting a restored history
    # must not depend on the temporary job/dataset still being in memory.
    throttle(owner, "report", 6)
    return Response(build_pdf(body), media_type="application/pdf", headers={
        "Content-Disposition": 'attachment; filename="buck-report.pdf"',
        "Cache-Control": "no-store",
    })


@app.post("/api/demo")
def demo(owner: str = Depends(session)):
    throttle(owner, "upload", 8)
    return add_dataset((ROOT / "examples" / "plecs_buck_case16a.csv").read_bytes(), "PLECS · Case16A.csv", owner)


@app.delete("/api/datasets/{identifier}")
def delete_dataset(identifier: str, owner: str = Depends(session)):
    get_dataset(identifier, owner)
    with lock:
        if any(j["owner"] == owner and j["dataset_id"] == identifier and j["status"] in ("running", "queued") for j in jobs.values()):
            raise HTTPException(409, "请等待当前辨识任务结束后再清除数据。")
        datasets.pop(identifier, None)
        for k in [k for k, j in jobs.items() if j["owner"] == owner and j["dataset_id"] == identifier]:
            del jobs[k]
    return {"deleted": True}


@app.get("/api/datasets/{identifier}")
def dataset_status(identifier: str, owner: str = Depends(session)):
    data = get_dataset(identifier, owner)
    return {"dataset_id": identifier, "sha256": data.sha256}


@app.get("/api/datasets/{identifier}/csv")
def download_csv(identifier: str, owner: str = Depends(session)):
    # Preserve original bytes (encoding, separators and hash) for browser archives.
    with lock:
        get_dataset(identifier, owner)
        raw = datasets[identifier]["raw"]
    return Response(raw, media_type="text/csv", headers={"Cache-Control": "no-store"})


@app.post("/api/analyze")
def analyze_route(body: AnalysisRequest, owner: str = Depends(session)):
    try:
        return analyze(get_dataset(body.dataset_id, owner), body.mapping)
    except ValueError as exc:
        raise HTTPException(422, str(exc))


def start_fit(body: FitRequest, owner):
    dataset = get_dataset(body.dataset_id, owner)
    if body.options.method == "pann" and not pann_available():
        raise HTTPException(422, "此后端未安装 PANN 依赖，请选择积分辨识。")
    with lock:
        pending = [j for j in jobs.values() if j["status"] in ("queued", "running")]
        for job_id, job in jobs.items():
            if job["owner"] == owner and job["status"] in ("queued", "running"):
                if job.get("config") == body.model_dump():
                    return {"job_id": job_id, "status": job["status"], "resumed": True}
                raise HTTPException(409, "当前会话已有辨识任务，请等待完成。")
        if len(pending) >= 3:
            raise HTTPException(503, "辨识队列已满，请稍后重试。")
        if len(jobs) >= 24:
            removable = next((k for k, v in jobs.items() if v["status"] not in ("queued", "running")), None)
            if removable:
                del jobs[removable]
        identifier = uuid.uuid4().hex
        jobs[identifier] = {"owner": owner, "created": time.time(), "status": "queued", "stage": "queued",
                            "dataset_snapshot": {"filename": dataset.name, "sha256": dataset.sha256},
                            "dataset_id": body.dataset_id, "config": body.model_dump(), "result": None, "error": None}
    def execute():
        def finish(**updates):
            with lock:
                finished = time.time()
                jobs[identifier].update(created=finished, **updates)
                if body.dataset_id in datasets:
                    datasets[body.dataset_id]["created"] = finished
        def progress(stage):
            with lock:
                jobs[identifier]["stage"] = stage
        with lock:
            jobs[identifier]["status"] = "running"
        try:
            result = identify(dataset, body.mapping, body.options, progress=progress)
            finish(status="complete", stage="complete", result=result)
        except Exception as exc:
            # No raw stack traces / server paths in API responses.
            error = str(exc) if isinstance(exc, ValueError) else "辨识计算失败，请查看后端日志并检查输入条件。"
            if not isinstance(exc, ValueError):
                import logging
                logging.exception("Identification failure")
            finish(status="failed", error=error)
    try:
        pool.submit(execute)
    except RuntimeError:
        # A rejected submission must not leave a permanent queued task behind.
        with lock:
            jobs[identifier].update(status="failed", stage="failed", error="辨识任务未能启动，请稍后重试。")
        raise HTTPException(503, "辨识任务未能启动，请稍后重试。")
    return {"job_id": identifier, "status": "queued"}


@app.post("/api/identify", status_code=202)
def fit_route(body: FitRequest, owner: str = Depends(session)):
    throttle(owner, "fit", 6)
    return start_fit(body, owner)


@app.get("/api/jobs/active")
def active_job(owner: str = Depends(session)):
    """Recover a lost submission response without creating another job."""
    with lock:
        for identifier, job in jobs.items():
            if job["owner"] == owner and job["status"] in ("queued", "running"):
                return {"job": {"job_id": identifier, "status": job["status"]}}
    return {"job": None}


@app.get("/api/jobs/{identifier}")
def get_job(identifier: str, owner: str = Depends(session)):
    with lock:
        job = jobs.get(identifier)
        if not job or job["owner"] != owner:
            raise HTTPException(404, "任务不存在或已过期。")
        return {"job_id": identifier, "status": job["status"], "stage": job.get("stage"),
                "result": job["result"], "error": job["error"]}


@app.get("/api/experiments")
def completed_experiments(owner: str = Depends(session)):
    """Backfill completed jobs that predate durable browser run snapshots."""
    with lock:
        runs = []
        for identifier, job in jobs.items():
            if job["owner"] != owner or job["status"] != "complete":
                continue
            dataset = job.get("dataset_snapshot")
            if not dataset:
                cached = datasets.get(job["dataset_id"])
                data = cached["data"] if cached and cached["owner"] == owner else None
                dataset = {"filename": data.name if data else job["result"].get("filename", ""),
                           "sha256": data.sha256 if data else job["result"].get("file_sha256", "")}
            result = compact(job["result"])
            result = json.loads(json.dumps(result))
            pann = result.get("diagnostics", {}).get("pann", {})
            pann.pop("history", None)
            pann.pop("epoch_history", None)
            runs.append({"id": identifier, "created": int(job["created"]*1000),
                         "updated": 0, "dataset": dataset, "result": result, "ground_truth": None})
        return {"experiments": runs}


@app.post("/api/chat")
async def chat_route(body: ChatRequest, owner: str = Depends(session)):
    throttle(owner, "chat", 10)
    async def invoke(name):
        try:
            if name == "get_waveform_summary":
                if not body.dataset_id or not body.mapping:
                    return None
                return compact(analyze(get_dataset(body.dataset_id, owner), body.mapping))
            if name == "get_identification_result":
                if not body.result_id:
                    return None
                if body.saved_result:
                    # User's persisted experiment snapshot, not a newly computed job.
                    return compact({**with_ground_truth(body.saved_result.result, body.ground_truth),
                                    "result_id": body.result_id, "result_source": "saved_conversation_snapshot"})
                job = jobs.get(body.result_id)
                if not job or job["owner"] != owner or job["dataset_id"] != body.dataset_id or job["status"] != "complete":
                    return None
                if job["config"]["mapping"] != (body.mapping.model_dump() if body.mapping else None) or job["config"]["options"] != body.options.model_dump():
                    return None
                return compact({**with_ground_truth(job["result"], body.ground_truth), "result_id": body.result_id})
            if name == "start_identification":
                if not body.identification_enabled:
                    return {"error": "请先点击左侧“进入参数辨识”，确认基本信息后再执行辨识。"}
                if not body.dataset_id or not body.mapping:
                    return {"error": "请先上传并确认 CSV 列映射。"}
                throttle(owner, "fit", 6)
                return start_fit(FitRequest(dataset_id=body.dataset_id, mapping=body.mapping, options=body.options), owner)
        except (HTTPException, ValueError) as exc:
            if name == "start_identification" and isinstance(exc, HTTPException) and exc.status_code == 409:
                existing = active_job(owner)["job"]
                if existing:
                    return {**existing, "resumed": True, "configuration_changed": True}
            if name == "get_waveform_summary":
                raise HTTPException(422, exc.detail if isinstance(exc, HTTPException) else str(exc))
            return {"error": exc.detail if isinstance(exc, HTTPException) else str(exc)}
        return None
    return await chat.answer(body, invoke)


@app.post("/api/jobs/{identifier}/analysis-copy")
def copy_analysis(identifier: str, owner: str = Depends(session)):
    """Copy a completed experiment into a fresh owner; never execute a new fit."""
    throttle(owner, "analysis-copy", 6)
    with lock:
        source = jobs.get(identifier)
        if not source or source["owner"] != owner:
            raise HTTPException(404, "实验结果已过期，无法复制到新对话。请重新辨识后再试。")
        if source["status"] != "complete":
            raise HTTPException(409, "请等待辨识完成后再创建分析会话。")
        stored = datasets.get(source["dataset_id"])
        if not stored or stored["owner"] != owner:
            raise HTTPException(404, "实验结果已过期，无法复制到新对话。请重新辨识后再试。")
        copied = json.loads(json.dumps(source))
        target = str(uuid.uuid4())
        dataset = add_dataset(stored["raw"], stored["data"].name, target)
        if len(jobs) >= 24:
            removable = next((key for key, job in jobs.items() if key != identifier and job["status"] not in ("queued", "running")), None)
            if removable:
                del jobs[removable]
        job_id = uuid.uuid4().hex
        copied.update(owner=target, created=time.time(), dataset_id=dataset["dataset_id"])
        copied["config"]["dataset_id"] = dataset["dataset_id"]
        jobs[job_id] = copied
    return {"session_id": target, "dataset": dataset, "job_id": job_id}


# Local convenience; GitHub Pages deploys only this directory, never backend or .env.
app.include_router(archive.router)
app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")
# Added last so CORS also wraps auth and payload-limit error responses.
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "DELETE"],
                   allow_headers=["Authorization", "Content-Type", "X-Session-ID", "X-Archive-Key"], allow_credentials=False)
