"""Every error leaves the API as {"error": {"code", "message"}}."""

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from functions.events import EventNotSent


class ApiError(HTTPException):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(status_code=status, detail=message)
        self.code = code


def _body(code: str, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code, "message": message}}


def _field_message(err: dict[str, object]) -> str:
    raw_loc = err.get("loc", ())
    parts = raw_loc if isinstance(raw_loc, tuple | list) else ()
    loc = [str(p) for p in parts if p not in ("body", "query", "path")]
    msg = str(err.get("msg", "invalid")).removeprefix("Value error, ")
    field = " ".join(loc).replace("_", " ")
    return f"{field[:1].upper()}{field[1:]}: {msg}" if loc else msg


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(_body(exc.code, str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(HTTPException)
    async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "error")
        return JSONResponse(_body(code, str(exc.detail)), status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        message = "; ".join(_field_message(e) for e in exc.errors())
        return JSONResponse(_body("validation", message), status_code=422)

    @app.exception_handler(EventNotSent)
    async def event_error(_: Request, exc: EventNotSent) -> JSONResponse:
        return JSONResponse(_body("inngest_unreachable", str(exc)), status_code=503)
