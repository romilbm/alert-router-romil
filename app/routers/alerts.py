from typing import Optional

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.engine import evaluate_alert
from app.models import Alert
from app.state import app_state

router = APIRouter()


@router.post("/alerts")
async def submit_alert(alert: Alert):
    result = evaluate_alert(alert, app_state, dry_run=False)
    return result


@router.get("/alerts")
async def list_alerts(
    service: Optional[str] = Query(default=None),
    severity: Optional[str] = Query(default=None),
    routed: Optional[bool] = Query(default=None),
    suppressed: Optional[bool] = Query(default=None),
):
    results = list(app_state.alerts.values())

    if service is not None:
        results = [r for r in results if app_state.alert_inputs.get(r.alert_id) and
                   app_state.alert_inputs[r.alert_id].service == service]

    if severity is not None:
        results = [r for r in results if app_state.alert_inputs.get(r.alert_id) and
                   app_state.alert_inputs[r.alert_id].severity == severity]

    if routed is not None:
        results = [r for r in results if (r.routed_to is not None) == routed]

    if suppressed is not None:
        results = [r for r in results if r.suppressed == suppressed]

    return {"alerts": results, "total": len(results)}


@router.get("/alerts/{alert_id}")
async def get_alert(alert_id: str):
    result = app_state.alerts.get(alert_id)
    if result is None:
        return JSONResponse(status_code=404, content={"error": "alert not found"})
    return result
