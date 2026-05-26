"""JWT 认证工具"""
import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError

SECRET_KEY = "shinehe-kb-secret-change-in-production"
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

_users_db: dict[str, dict] = {}


def hash_password(password: str) -> str:
    salt = os.urandom(16).hex()
    h = hashlib.sha256((salt + password).encode()).hexdigest()
    return f"{salt}${h}"


def verify_password(plain: str, hashed: str) -> bool:
    salt, h = hashed.split("$", 1)
    return hmac.compare_digest(hashlib.sha256((salt + plain).encode()).hexdigest(), h)


def create_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": user_id, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


def register_user(username: str, password: str) -> str:
    if username in _users_db:
        raise ValueError("用户已存在")
    _users_db[username] = {"username": username, "hashed": hash_password(password)}
    return create_token(username)


def authenticate(username: str, password: str) -> str | None:
    user = _users_db.get(username)
    if not user or not verify_password(password, user["hashed"]):
        return None
    return create_token(username)
