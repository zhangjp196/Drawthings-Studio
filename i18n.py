"""后端用户可见文案的中英文选择（与前端 I18N 对应）。

依据请求头 Accept-Language 选择语言：含 zh（且优先级更高）→ 中文，否则（en 或未知）→ 默认中文，
仅当明确以 en 开头时返回英文。与前端「中文/EN 切换」保持一致的默认值（zh）。
"""


def lang_of(accept_language: str) -> str:
    """Accept-Language → 'zh' | 'en'。

    解析语言范围与 q 值：zh 系与 en 系各取最高 q 值比较；
    中文浏览器通常发送 "zh-CN,zh;q=0.9,en;q=0.8"（zh 优先级更高 → 中文）。
    无法识别 / 为空 → 'zh'（默认中文）。
    """
    al = (accept_language or "").lower()
    best = None  # (q, lang)
    for part in al.split(","):
        part = part.strip()
        if not part:
            continue
        code, sep, q = part.partition(";")
        code = code.strip()
        if code.startswith("zh"):
            lang = "zh"
        elif code.startswith("en"):
            lang = "en"
        else:
            continue
        try:
            qv = float(q.strip().replace("q=", "") or 1) if sep else 1.0
        except ValueError:
            qv = 1.0
        if best is None or qv > best[0]:
            best = (qv, lang)
    return best[1] if best else "zh"


def L(accept_language: str, zh: str, en: str) -> str:
    """按请求语言返回文案（Accept-Language 明确为 en 时英文，否则中文）。"""
    return en if lang_of(accept_language) == "en" else zh
