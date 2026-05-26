"""API 服务器启动入口"""
import uvicorn
from src.api import create_app

app = create_app()

if __name__ == "__main__":
    uvicorn.run("run_api:app", host="0.0.0.0", port=8000, reload=True)
