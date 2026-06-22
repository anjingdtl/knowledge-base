"""ShineHeKnowledge MCP Server — Windows 服务包装

使用 pywin32 将 MCP Server 注册为 Windows 服务，支持：
- 开机自启
- 崩溃自动重启（通过 Windows 服务恢复策略）
- 优雅停止（SIGTERM → uvicorn shutdown）

注册服务（需管理员权限）:
    python windows_service.py install

配置崩溃重启策略:
    sc failure ShineHeMCP reset= 86400 actions= restart/5000/restart/10000/restart/30000

启动/停止:
    sc start ShineHeMCP
    sc stop ShineHeMCP

卸载:
    python windows_service.py remove
"""

import sys
import os
import logging

# 确保项目根目录在 sys.path 中
SERVICE_DIR = os.path.dirname(os.path.abspath(__file__))
if SERVICE_DIR not in sys.path:
    sys.path.insert(0, SERVICE_DIR)

import win32serviceutil
import win32service
import win32event
import servicemanager


class ShineHeMCPService(win32serviceutil.ServiceFramework):
    _svc_name_ = "ShineHeMCP"
    _svc_display_name_ = "ShineHe Knowledge MCP Server"
    _svc_description_ = "本地知识库 MCP Server (streamable-http 模式，端口 9000)"
    _svc_start_type_ = win32service.SERVICE_AUTO_START

    # 崩溃时自动重启相关
    _svc_restart_on_fail_ = True

    def __init__(self, args):
        win32serviceutil.ServiceFramework.__init__(self, args)
        self.hWaitStop = win32event.CreateEvent(None, 0, 0, None)
        self._server = None
        self._thread = None

    def SvcStop(self):
        """服务停止回调"""
        self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
        win32event.SetEvent(self.hWaitStop)
        if self._server is not None:
            try:
                self._server.should_exit = True
            except Exception:
                pass

    def SvcDoRun(self):
        """服务主逻辑"""
        servicemanager.LogMsg(
            servicemanager.EVENTLOG_INFORMATION_TYPE,
            servicemanager.PYS_SERVICE_STARTED,
            (self._svc_name_, ""),
        )
        self._run_server()

    def _run_server(self):
        """启动 MCP Server (streamable-http)"""
        try:
            # 启动期加载配置、注入 secret 到进程环境，并诊断 API Key 是否就位
            self._diagnose_secrets()

            # 注入 Session TTL patch
            from src.mcp_cli import _patch_session_idle_timeout, SESSION_IDLE_TIMEOUT
            _patch_session_idle_timeout(SESSION_IDLE_TIMEOUT)

            from src.mcp_server import mcp

            # 使用 uvicorn 直接驱动，方便控制生命周期
            import uvicorn
            from fastmcp.server.mixins.transport import TransportMixin

            # 获取 ASGI app
            app = mcp.http_app(path="/mcp")

            config = uvicorn.Config(
                app,
                host="127.0.0.1",
                port=9000,
                log_level="info",
                access_log=False,
                log_config=None,  # 禁用 uvicorn 默认日志配置，避免 Windows 服务环境下 formatter 冲突
            )
            server = uvicorn.Server(config)
            self._server = server

            # 在线程中运行 uvicorn，同时等待停止信号
            import threading

            def _serve():
                server.run()

            thread = threading.Thread(target=_serve, daemon=True)
            thread.start()

            # 等待停止事件或 uvicorn 退出
            while thread.is_alive():
                rc = win32event.WaitForSingleObject(self.hWaitStop, 3000)
                if rc == win32event.WAIT_OBJECT_0:
                    server.should_exit = True
                    thread.join(timeout=10)
                    break

        except Exception as e:
            servicemanager.LogErrorMsg(f"ShineHeMCP 服务异常: {e}")
            raise

    def _diagnose_secrets(self):
        """启动期加载配置，把 keyring/env 中的 secret 注入进程环境，
        并在 API Key 缺失时记录 Windows 事件日志告警。

        Windows 服务运行在 SYSTEM 账户，读不到交互式账户的 keyring 凭据，
        因此需要通过系统环境变量（setx /M 或服务 Environment 注册表）注入。
        """
        try:
            from src.utils.config import Config
            config = Config()
            config.load()
            # 把已加载的 secret 写回 os.environ，确保后续延迟 Config.load
            # 也能稳定读到（不覆盖已有的环境变量）
            config.export_secret_env(os.environ, overwrite=False)
            llm_key = config.get("llm.api_key", "")
            emb_key = config.get("embedding.api_key", "") or llm_key
            missing = []
            if not llm_key:
                missing.append("SHINEHE_LLM_API_KEY (ask/RAG 生成)")
            if not emb_key:
                missing.append("SHINEHE_EMBEDDING_API_KEY (向量/语义搜索)")
            if missing:
                servicemanager.LogErrorMsg(
                    "ShineHeMCP 服务启动检测到 API Key 缺失: "
                    + ", ".join(missing)
                    + "。对应功能将不可用。请以管理员身份执行 "
                    "`setx SHINEHE_LLM_API_KEY <KEY> /M`（embedding 同理）"
                    "设置系统环境变量后重启服务，或在服务运行账户下配置 keyring。"
                )
        except Exception as exc:
            servicemanager.LogErrorMsg(f"ShineHeMCP 配置加载失败: {exc}")


if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(ShineHeMCPService)
