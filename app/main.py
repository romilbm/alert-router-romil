from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.routers import routes

app = FastAPI(title="Alert Routing Engine")
app.include_router(routes.router)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = exc.errors()
    msg = errors[0]["msg"] if errors else "Invalid request"
    # Pydantic v2 prefixes custom ValueError messages with "Value error, " — strip it
    if msg.startswith("Value error, "):
        msg = msg[len("Value error, "):]
    return JSONResponse(status_code=400, content={"error": msg})


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
