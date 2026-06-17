import asyncio
import httpx
from typing import Callable, Any, Dict, List
from loguru import logger
from .config import BaseWorkerSettings

async def heartbeat_loop(settings: BaseWorkerSettings, get_fingerprint_fn: Callable[[], Dict[str, Any]] = None, get_metrics_fn: Callable[[], Dict[str, Any]] = None):
    """
    Sends a periodic ping to the Go API.
    """
    logger.info("Starting Heartbeat Loop...")
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                fingerprint = get_fingerprint_fn() if get_fingerprint_fn else {}
                metrics = get_metrics_fn() if get_metrics_fn else {}
                
                payload = {
                    "worker_id": settings.WORKER_ID,
                    "supported_tasks": settings.SUPPORTED_TASKS,
                    "status": "idle",
                    "fingerprint": fingerprint,
                    "metrics": metrics
                }
                
                headers = {
                    "X-Internal-Secret": settings.WORKER_SECRET,
                    "Content-Type": "application/json"
                }
                
                url = f"{settings.Agent Core_API_BASE}/workers/heartbeat"
                resp = await client.post(url, json=payload, headers=headers)
                
                if resp.status_code != 200:
                    logger.warning(f"Heartbeat rejected: {resp.status_code} {resp.text}")
                    
            except Exception as e:
                logger.error(f"Heartbeat failed: {e}")
                
            await asyncio.sleep(15)

async def poll_tasks(settings: BaseWorkerSettings, task_handler: Callable[[str, str, Dict[str, Any]], Any]):
    """
    HTTP Long-Polling for Tasks.
    task_handler is an async function: async def handler(task_id: str, task_type: str, payload: dict) -> None
    """
    logger.info(f"Starting Task Polling Loop for capabilities: {settings.SUPPORTED_TASKS}")
    
    async with httpx.AsyncClient(timeout=25.0) as client:
        while True:
            try:
                headers = {"X-Internal-Secret": settings.WORKER_SECRET}
                payload = {
                    "worker_id": settings.WORKER_ID,
                    "capabilities": settings.SUPPORTED_TASKS
                }
                
                url = f"{settings.Agent Core_API_BASE}/tasks/pop"
                resp = await client.post(url, json=payload, headers=headers)
                
                if resp.status_code == 204:
                    continue
                    
                if resp.status_code != 200:
                    logger.error(f"Polling error {resp.status_code}: {resp.text}")
                    await asyncio.sleep(2)
                    continue
                    
                raw = resp.json()
                data = raw.get("data", raw)
                if not data.get("has_task"):
                    continue
                    
                task = data["task"]
                task_id = task.get("task_id")
                task_type = task.get("task_type")
                task_payload = task.get("payload", {})
                
                logger.info(f"=== Received Task: {task_id} ({task_type}) ===")
                
                try:
                    # Execute the task handler
                    await task_handler(task_id, task_type, task_payload)
                except Exception as ex:
                    logger.error(f"Task {task_id} unhandled failure: {ex}")
                    await report_task_complete(task_id, "failed", settings, error_msg=str(ex))
                    
            except httpx.ReadTimeout:
                continue
            except Exception as e:
                logger.error(f"Network error in polling loop: {e}")
                await asyncio.sleep(3)

async def report_task_complete(task_id: str, status: str, settings: BaseWorkerSettings, result_url: str = None, error_msg: str = None, outputs: List[Dict[str, Any]] = None):
    """
    Notify the Go API that the task is complete.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        payload = {
            "task_id": task_id,
            "status": status,
            "result_url": result_url,
            "error": error_msg,
            "outputs": outputs or []
        }
        headers = {
            "X-Internal-Secret": settings.WORKER_SECRET
        }
        url = f"{settings.Agent Core_API_BASE}/tasks/complete"
        try:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code == 200:
                logger.info(f"[Task {task_id}] Reported {status} to Go API.")
            else:
                body = resp.text[:500]
                logger.error(f"[Task {task_id}] Go API returned HTTP {resp.status_code} for complete report. Body: {body}")
        except Exception as e:
            logger.error(f"[Task {task_id}] Failed to report complete: {e}")

def report_task_progress_sync(task_id: str, node: str, current: int, total: int, settings: BaseWorkerSettings):
    """
    Sync version to notify the Go API of task progress (useful for blocking threads).
    Uses httpx sync client to avoid adding `requests` as a separate dependency.
    """
    percent = int((current / total) * 100) if total > 0 else 0
    payload = {
        "task_id": task_id,
        "node": node,
        "percent": percent
    }
    headers = {
        "X-Internal-Secret": settings.WORKER_SECRET
    }
    url = f"{settings.Agent Core_API_BASE}/tasks/progress"
    try:
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
        logger.debug(f"[Task {task_id}] Reported progress: {percent}% (Node: {node})")
    except Exception as e:
        logger.error(f"[Task {task_id}] Failed to report progress: {e}")

async def report_task_progress(task_id: str, node: str, current: int, total: int, settings: BaseWorkerSettings):
    """
    Async version to notify the Go API of task progress.
    """
    percent = int((current / total) * 100) if total > 0 else 0
    payload = {
        "task_id": task_id,
        "node": node,
        "percent": percent
    }
    headers = {
        "X-Internal-Secret": settings.WORKER_SECRET
    }
    url = f"{settings.Agent Core_API_BASE}/tasks/progress"
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            await client.post(url, json=payload, headers=headers)
            logger.debug(f"[Task {task_id}] Reported progress: {percent}% (Node: {node})")
        except Exception as e:
            logger.error(f"[Task {task_id}] Failed to report progress: {e}")
