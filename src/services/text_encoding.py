"""文本编码探测与安全纠错。

导入链路不能把 ``charset-normalizer`` 的单个猜测直接当作事实：对中文
Windows 文件而言，GB18030/GBK 和 UTF-8 经常都需要被纳入比较。这里用
可读性评分选择候选，并只在明显改善时修复已经产生的 mojibake（错码文本）。
"""
from __future__ import annotations

from dataclasses import dataclass
import re


_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_ODD_SCRIPT_RE = re.compile(r"[\u0370-\u052f\u0600-\u08ff\u0900-\u0fff\u1100-\u1fff\u2500-\u2bff\ue000-\uf8ff]")


@dataclass(frozen=True)
class DecodedText:
    """一次解码的结果，供调用方记录导入诊断信息。"""

    text: str
    encoding: str
    confidence: str


def _readability_score(text: str) -> float:
    """为中文业务文本评分；分数仅用于同一原始字节的候选比较。"""
    if not text:
        return 0.0
    length = len(text)
    cjk = len(_CJK_RE.findall(text))
    latin_or_digits = sum(ch.isascii() and (ch.isalnum() or ch in " \t\r\n.,;:!?-_()[]{}") for ch in text)
    printable = sum(ch.isprintable() or ch in "\r\n\t" for ch in text)
    replacement = text.count("\ufffd")
    controls = len(_CONTROL_RE.findall(text))
    odd_scripts = len(_ODD_SCRIPT_RE.findall(text))

    # 汉字在此类知识库中是最有辨识力的信号；英文/代码文件仍能取得正分。
    return (
        cjk * 3.0
        + latin_or_digits * 0.55
        + printable * 0.1
        - replacement * 40.0
        - controls * 12.0
        - odd_scripts * 1.6
        - (length - printable) * 3.0
    )


def _charset_normalizer_candidate(raw: bytes) -> tuple[str, str] | None:
    """可选地加入 charset-normalizer 的结果；不可用不影响基础转码。"""
    try:
        from charset_normalizer import from_bytes

        best = from_bytes(raw).best()
        if best and best.encoding:
            return str(best), best.encoding
    except Exception:
        # 导入不能因第三方编码猜测器异常而失败。
        return None
    return None


def decode_text_bytes(raw: bytes) -> DecodedText:
    """将文件字节解码为文本，优先保留有效 UTF-8，再比较中文编码候选。

    GB18030 是 GBK 的超集，因而覆盖 Windows 中文环境常见的 ANSI 文本；
    Big5 保留用于外部资料。所有候选都必须严格解码，绝不静默吞掉坏字节。
    """
    if not raw:
        return DecodedText("", "utf-8", "high")

    # UTF-8 有明确定义且可严格解码时不猜测其他编码，避免把正常英文/代码误转。
    try:
        return DecodedText(raw.decode("utf-8-sig"), "utf-8", "high")
    except UnicodeDecodeError:
        pass

    candidates: list[tuple[str, str]] = []
    guessed = _charset_normalizer_candidate(raw)
    if guessed:
        candidates.append(guessed)

    for encoding in ("gb18030", "gbk", "big5", "utf-16", "utf-16-le", "utf-16-be"):
        try:
            candidates.append((raw.decode(encoding), encoding))
        except UnicodeDecodeError:
            continue

    if not candidates:
        return DecodedText(raw.decode("utf-8", errors="replace"), "utf-8", "low")

    # 稳定的次级排序确保不同 Python/库版本下可预测。
    text, encoding = max(candidates, key=lambda item: (_readability_score(item[0]), -len(item[1])))
    return DecodedText(text, encoding.lower(), "medium")


def repair_mojibake(text: str) -> tuple[str, bool]:
    """仅在可读性显著提高时修复一次常见的错误转码。

    该步骤能修复诸如 UTF-8 字节被当作 Latin-1/GBK 保存后又重新读入的
    文本。PDF 缺失 ToUnicode 字符表属于字体映射问题，不会被伪装成“已修复”。
    """
    if not text:
        return text, False

    original_score = _readability_score(text)
    best_text = text
    best_score = original_score
    for source_encoding in ("latin1", "cp1252", "gbk", "gb18030"):
        try:
            repaired = text.encode(source_encoding).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
        score = _readability_score(repaired)
        if score > best_score:
            best_text, best_score = repaired, score

    # 一个字符的偶然提升不足以授权改写原文。
    if best_text != text and best_score >= original_score + max(8.0, len(text) * 0.08):
        return best_text, True
    return text, False
