"""LLM 输出文本处理工具 — 剥离思维链、清理噪声"""
import re


def strip_think(text: str) -> str:
    """移除 LLM 返回中的 <think...</think...> 思维链"""
    while True:
        m = re.search(r"<think\b", text)
        if not m:
            break
        start = m.start()
        close = re.search(r"</think\b[\s>]*", text[start:])
        if close:
            end_pos = start + close.end()
            text = text[:start] + text[end_pos:]
        else:
            text = text[:start]
            break
    return text.strip()
