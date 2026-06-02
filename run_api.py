"""API 服务器启动入口"""
import os

import uvicorn
from src.api import create_app

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("SHINEHE_API_HOST", "127.0.0.1")
    port = int(os.environ.get("SHINEHE_API_PORT", "8000"))
    reload = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")
    uvicorn.run("run_api:app", host=host, port=port, reload=reload)
