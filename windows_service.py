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


if __name__ == "__main__":
    win32serviceutil.HandleCommandLine(ShineHeMCPService)
