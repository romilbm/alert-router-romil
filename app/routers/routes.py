from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.models import RouteConfig
from app.state import app_state

router = APIRouter()


@router.post("/routes", status_code=201)
async def create_or_update_route(route: RouteConfig):
    created = route.id not in app_state.routes
    app_state.routes[route.id] = route
    return {"id": route.id, "created": created}


@router.get("/routes")
async def list_routes():
    return {"routes": list(app_state.routes.values())}


@router.delete("/routes/{route_id}")
async def delete_route(route_id: str):
    if route_id not in app_state.routes:
        return JSONResponse(status_code=404, content={"error": "route not found"})
    del app_state.routes[route_id]
    stale_keys = [k for k in app_state.suppression_windows if k[0] == route_id]
    for k in stale_keys:
        del app_state.suppression_windows[k]
    return {"id": route_id, "deleted": True}
