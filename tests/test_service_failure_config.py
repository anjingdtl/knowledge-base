"""get_service_failure_config 解析逻辑测试。

sc.exe qfailure 的输出在中文 Windows 上会被本地化(RESTART → 重新启动,
Delay → 延迟, milliseconds → 毫秒),旧实现硬编码匹配英文关键词导致 actions
永远解析为空、UI 始终显示「未配置」。本测试覆盖中英文两种真实输出。

输出样本采集自本机 sc.exe qfailure ShineHeMCP:
- EN_OUTPUT  : PowerShell chcp 65001 (UTF-8) 下的英文输出
- ZH_OUTPUT  : Python subprocess 默认 GBK 代码页下的中文本地化输出
"""
from src.services.mcp_launcher import _parse_failure_config


EN_OUTPUT = """[SC] QueryServiceConfig2 SUCCESS

SERVICE_NAME: ShineHeMCP
        RESET_PERIOD (in seconds)    : 86400
        REBOOT_MESSAGE               :
        COMMAND_LINE                 :
        FAILURE_ACTIONS              : RESTART -- Delay = 5000 milliseconds.
                                       RESTART -- Delay = 10000 milliseconds.
                                       RESTART -- Delay = 30000 milliseconds.
"""

ZH_OUTPUT = """[SC] QueryServiceConfig2 成功

SERVICE_NAME: ShineHeMCP
        RESET_PERIOD (秒数)        : 86400
        REBOOT_MESSAGE               :
        COMMAND_LINE                 :
        FAILURE_ACTIONS              : 重新启动 -- 延迟 = 5000 毫秒。
                                       重新启动 -- 延迟 = 10000 毫秒。
                                       重新启动 -- 延迟 = 30000 毫秒。
"""

UNCONFIGURED_OUTPUT = """[SC] QueryServiceConfig2 SUCCESS

SERVICE_NAME: ShineHeMCP
        RESET_PERIOD (in seconds)    : 0
        REBOOT_MESSAGE               :
        COMMAND_LINE                 :
"""

NOT_INSTALLED_OUTPUT = """[SC] OpenService FAILED 1060:

The specified service does not exist as an installed service.
"""


def test_parse_english_output():
    info = _parse_failure_config(EN_OUTPUT)
    assert info["configured"] is True
    assert info["reset_period"] == 86400
    assert [a["delay_ms"] for a in info["actions"]] == [5000, 10000, 30000]


def test_parse_chinese_localized_output():
    """中文 Windows: sc.exe 输出「重新启动 / 延迟」,不含 RESTART/Delay 关键词。

    旧实现在此输出下 configured 恒为 False(Bug 1)。
    """
    info = _parse_failure_config(ZH_OUTPUT)
    assert info["configured"] is True
    assert info["reset_period"] == 86400
    assert [a["delay_ms"] for a in info["actions"]] == [5000, 10000, 30000]


def test_parse_unconfigured_service():
    info = _parse_failure_config(UNCONFIGURED_OUTPUT)
    assert info["configured"] is False
    assert info["actions"] == []


def test_parse_empty_and_garbage():
    assert _parse_failure_config("")["configured"] is False
    assert _parse_failure_config(NOT_INSTALLED_OUTPUT)["configured"] is False
