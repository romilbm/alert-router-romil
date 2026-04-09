from fastapi import APIRouter

from app.engine import evaluate_alert
from app.models import Alert
from app.state import app_state

router = APIRouter()


@router.get("/stats")
async def get_stats():
    return app_state.stats


@router.post("/test")
async def dry_run_alert(alert: Alert):
    result = evaluate_alert(alert, app_state, dry_run=True)
    return result


@router.post("/reset")
async def reset_state():
    app_state.reset()
    return {"status": "ok"}
