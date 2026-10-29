from pydantic import BaseModel


class Check(BaseModel):
    ok: bool
    detail: str


class Health(BaseModel):
    ok: bool
    checks: dict[str, Check]
