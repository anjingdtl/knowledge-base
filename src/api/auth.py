"""JWT 认证工具

用户账户持久化到 SQLite 数据库，密码使用 bcrypt 哈希，JWT 密钥从环境变量或按安装生成。
"""
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bcrypt
from jose import JWTError, jwt

logger = logging.getLogger(__name__)

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24

# ---------------------------------------------------------------------------
# JWT Secret: 优先环境变量，其次按安装持久化到数据目录
# ---------------------------------------------------------------------------

_SECRET_FILE_NAME = ".jwt_secret"


def _load_or_generate_secret() -> str:
    """从环境变量读取 JWT 密钥，未设置则在数据目录生成并持久化一个随机密钥。"""
    env_secret = os.environ.get("SHINEHE_JWT_SECRET")
    if env_secret:
        return env_secret

    try:
        from src.utils.paths import get_data_dir
        data_dir = get_data_dir()
    except Exception:
        # paths 模块不可用时回退到项目根目录
        data_dir = Path(__file__).resolve().parent.parent.parent / "data"

    secret_path = data_dir / _SECRET_FILE_NAME
    if secret_path.exists():
        return secret_path.read_text(encoding="utf-8").strip()

    generated = secrets.token_urlsafe(48)
    secret_path.write_text(generated, encoding="utf-8")
    logger.info("Generated new JWT secret at %s", secret_path)
    return generated


SECRET_KEY = _load_or_generate_secret()

# ---------------------------------------------------------------------------
# 用户持久化 — SQLite users 表
# ---------------------------------------------------------------------------

_USERS_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    username TEXT PRIMARY KEY,
    hashed TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
"""

# 内存缓存，启动时从 DB 加载
_users_db: dict[str, dict] = {}


def _ensure_users_table():
    """确保 users 表存在并加载用户到内存缓存。"""
    try:
        from src.services.db import Database
        conn = Database.get_conn()
        conn.execute(_USERS_SCHEMA)
        conn.commit()
        rows = conn.execute("SELECT username, hashed FROM users").fetchall()
        _users_db.clear()
        for row in rows:
            _users_db[row["username"]] = {"username": row["username"], "hashed": row["hashed"]}
        logger.debug("Loaded %d users from database", len(_users_db))
    except Exception:
        logger.warning("Could not load users from database; starting with empty user store")


def _persist_user(username: str, hashed: str):
    """将用户写入 SQLite。"""
    try:
        from src.services.db import Database
        conn = Database.get_conn()
        conn.execute(
            "INSERT INTO users (username, hashed) VALUES (?, ?)",
            (username, hashed),
        )
        conn.commit()
    except Exception:
        logger.error("Failed to persist user %s to database", username)


# 首次导入时尝试加载
_ensure_users_table()


# ---------------------------------------------------------------------------
# 密码哈希 — passlib bcrypt
# ---------------------------------------------------------------------------

def hash_password(password: str) -> str:
    """使用 bcrypt 哈希密码。"""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """验证密码与 bcrypt 哈希是否匹配。"""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# ---------------------------------------------------------------------------
# JWT 令牌
# ---------------------------------------------------------------------------

def create_token(user_id: str) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    return jwt.encode({"sub": user_id, "exp": expire}, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> str | None:
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload.get("sub")
    except JWTError:
        return None


# ---------------------------------------------------------------------------
# 用户注册 / 认证
# ---------------------------------------------------------------------------

def get_users_db() -> dict:
    """返回内存用户缓存（路由层用于判断是否首个注册用户）。"""
    return _users_db


def register_user(username: str, password: str) -> str:
    if username in _users_db:
        raise ValueError("用户已存在")
    hashed = hash_password(password)
    _users_db[username] = {"username": username, "hashed": hashed}
    _persist_user(username, hashed)
    return create_token(username)


def authenticate(username: str, password: str) -> str | None:
    user = _users_db.get(username)
    if not user or not verify_password(password, user["hashed"]):
        return None
    return create_token(username)
