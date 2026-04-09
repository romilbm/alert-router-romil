from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.engine import evaluate_alert
from app.models import Alert
from app.state import app_state

router = APIRouter()


@router.post("/alerts")
async def submit_alert(alert: Alert):
    result = evaluate_alert(alert, app_state, dry_run=False)
    return result


@router.get("/alerts/{alert_id}")
async def get_alert(alert_id: str):
    result = app_state.alerts.get(alert_id)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "alert not found"})
    return result
