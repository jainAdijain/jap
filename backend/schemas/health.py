from pydantic import BaseModel


class Check(BaseModel):
    ok: bool
    detail: str


class Health(BaseModel):
    ok: bool
    env: str = "dev"
    checks: dict[str, Check]
