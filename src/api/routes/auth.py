from fastapi import APIRouter, HTTPException, Header, Request
from pydantic import BaseModel, Field

from src.api.auth import authenticate, create_token, decode_token, register_user, get_users_db
from src.api.routes.rate_limiter import login_limiter

auth_router = APIRouter(prefix="/auth", tags=["auth"])


class LoginReq(BaseModel):
    username: str
    password: str


class RegisterReq(BaseModel):
    username: str = Field(min_length=2, max_length=64, pattern=r"^[a-zA-Z0-9_\-\u4e00-\u9fff]+$")
    password: str = Field(min_length=8, max_length=128)


def _check_auth(authorization: str = Header(default="")) -> str:
    if not authorization.startswith("Bearer "):
        raise HTTPException(401, "未提供认证令牌")
    user = decode_token(authorization[7:])
    if not user:
        raise HTTPException(401, "令牌无效或已过期")
    return user


def _get_current_user(authorization: str = Header(default="")) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        return "anonymous"
    token = authorization[7:]
    payload = decode_token(token)
    return payload if payload else "anonymous"


@auth_router.post("/register")
def api_register(req: RegisterReq, authorization: str = Header(default="")):
    users = get_users_db()
    if users:
        if not authorization.startswith("Bearer "):
            raise HTTPException(401, "需要管理员认证才能注册新用户")
        user = decode_token(authorization[7:])
        if not user:
            raise HTTPException(401, "令牌无效或已过期")
    try:
        token = register_user(req.username, req.password)
        return {"access_token": token, "token_type": "bearer"}
    except ValueError as e:
        raise HTTPException(400, str(e))


@auth_router.post("/login")
def api_login(req: LoginReq, request: Request):
    client_ip = request.client.host if request.client else "unknown"
    if not login_limiter.is_allowed(f"login:{client_ip}"):
        raise HTTPException(429, "请求过于频繁，请稍后再试")
    token = authenticate(req.username, req.password)
    if not token:
        raise HTTPException(401, "用户名或密码错误")
    return {"access_token": token, "token_type": "bearer"}
