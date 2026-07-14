"""请求级搜索执行结果 — 一次 search 的 results/trace/side-channels 同对象返回。

Phase-1 maintainability: 禁止通过 SearchService 实例上的 last_* 跨请求传状态。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SearchExecution:
    """一次搜索请求的完整输出（外层不可变）。

    - results: 主检索结果（与历史 search() 返回 list 元素一致）
    - trace: 编排 trace（mode/stages/route/sources 等）
    - disclose_claims: disclose_only 侧信道 Claim 包装行
    - conflicts: 请求内检测到的冲突
    - fallbacks: 降级记录（from/to/reason）
    - warnings: 请求内警告字符串
    """

    results: tuple[dict[str, Any], ...]
    trace: dict[str, Any] = field(default_factory=dict)
    disclose_claims: tuple[dict[str, Any], ...] = ()
    conflicts: tuple[dict[str, Any], ...] = ()
    fallbacks: tuple[dict[str, Any], ...] = ()
    warnings: tuple[str, ...] = ()
