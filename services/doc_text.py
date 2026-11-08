"""文档导入：txt / md / pdf / docx → 纯文本（与业务类型无关的共享基建）。

- `extract_text`：按扩展名解析（txt/md 直接解码、pdf 逐页、docx 段落 + 表格），
  统一换行、压缩多余空行；20MB 上限 + 扩展名白名单，失败抛本地化 ValueError。
- `split_chunks`：按空行分段聚合成长度受限的块（超长段落硬切），
  供流水线逐块调用 LLM 拆章（长文用 map 式解析，避免一次塞爆上下文）。

漫画 / 短剧共用本模块；拆章提示词与写入在各流水线内（保持两条线独立）。
"""

import io
import re
from pathlib import Path

from i18n import L

MAX_DOC_BYTES = 20 * 1024 * 1024  # 单文件上限 20MB
MAX_CHUNK_CHARS = 6000  # 单次交给 LLM 的正文长度上限（约 3–4k tokens）

_TEXT_EXTS = {".txt", ".md", ".markdown", ".text"}
_ALL_EXTS = _TEXT_EXTS | {".pdf", ".docx"}

ACCEPT_ATTR = ".txt,.md,.markdown,.text,.pdf,.docx"


def _decode(data: bytes, lang: str) -> str:
    """文本解码：utf-8（含 BOM）→ gb18030（中文旧文档）→ utf-16。"""
    for enc in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            text = data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
        # utf-16 解码对任意字节都“成功”，用空字节比例粗筛掉误判
        if enc == "utf-16" and text and text.count("\x00") > len(text) * 0.1:
            continue
        return text
    raise ValueError(
        L(
            lang,
            "无法识别文本编码（请另存为 UTF-8 后重试）",
            "Cannot detect the text encoding (please save as UTF-8)",
        )
    )


def _pdf_text(data: bytes, lang: str) -> str:
    try:
        from pypdf import PdfReader
    except Exception as e:  # 依赖缺失（打包遗漏）时给出可执行提示
        raise ValueError(L(lang, f"PDF 解析组件不可用：{e}", f"PDF parser unavailable: {e}")) from e
    try:
        reader = PdfReader(io.BytesIO(data))
        if getattr(reader, "is_encrypted", False):
            try:
                reader.decrypt("")
            except Exception:
                raise ValueError(L(lang, "PDF 已加密，无法读取", "The PDF is encrypted and cannot be read"))
        pages = [(page.extract_text() or "") for page in reader.pages]
    except ValueError:
        raise
    except Exception as e:
        raise ValueError(L(lang, f"PDF 解析失败：{e}", f"Failed to parse the PDF: {e}")) from e
    return "\n\n".join(p for p in pages if p.strip())


def _docx_text(data: bytes, lang: str) -> str:
    try:
        import docx
    except Exception as e:
        raise ValueError(L(lang, f"DOCX 解析组件不可用：{e}", f"DOCX parser unavailable: {e}")) from e
    try:
        document = docx.Document(io.BytesIO(data))
        parts = [p.text for p in document.paragraphs if p.text and p.text.strip()]
        for table in document.tables:  # 表格内容也尽量保留（小说/剧本里常见分栏）
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text and c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
    except Exception as e:
        raise ValueError(L(lang, f"DOCX 解析失败：{e}", f"Failed to parse the DOCX: {e}")) from e
    return "\n".join(parts)


def _normalize(text: str) -> str:
    """统一换行、去掉行尾空白、把 3+ 连续空行压成 2 行。"""
    text = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def extract_text(filename: str, data: bytes, lang: str = "zh") -> str:
    """把上传文档解析成纯文本；扩展名 / 大小 / 内容为空时抛本地化 ValueError。"""
    ext = Path(filename or "").suffix.lower()
    if ext not in _ALL_EXTS:
        raise ValueError(
            L(
                lang,
                f"不支持的文件类型：{ext or '未知'}（支持 txt / md / pdf / docx）",
                f"Unsupported file type: {ext or 'unknown'} (txt / md / pdf / docx)",
            )
        )
    if not data:
        raise ValueError(L(lang, "文件为空", "The file is empty"))
    if len(data) > MAX_DOC_BYTES:
        mb = MAX_DOC_BYTES // (1024 * 1024)
        raise ValueError(L(lang, f"文件超过 {mb}MB 上限", f"The file exceeds the {mb}MB limit"))

    if ext in _TEXT_EXTS:
        text = _decode(data, lang)
    elif ext == ".pdf":
        text = _pdf_text(data, lang)
    else:
        text = _docx_text(data, lang)

    text = _normalize(text)
    if not text:
        raise ValueError(
            L(
                lang,
                "未能从文档中提取到文本（可能是扫描版 PDF / 纯图片文档）",
                "No text could be extracted (scanned PDF or image-only document?)",
            )
        )
    return text


def split_chunks(text: str, max_chars: int = MAX_CHUNK_CHARS) -> list[str]:
    """按空行分段后聚合成不超过 max_chars 的块；超长段落硬切。"""
    max_chars = max(1000, int(max_chars or MAX_CHUNK_CHARS))
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paragraphs:
        while len(para) > max_chars:  # 单段超长：先硬切，余下继续参与聚合
            if buf:
                chunks.append(buf)
                buf = ""
            chunks.append(para[:max_chars])
            para = para[max_chars:]
        if not para:
            continue
        if buf and len(buf) + len(para) + 2 > max_chars:
            chunks.append(buf)
            buf = para
        else:
            buf = f"{buf}\n\n{para}".strip() if buf else para
    if buf:
        chunks.append(buf)
    return chunks


ALLOWED_EXTS = _ALL_EXTS


def imports_dir() -> Path:
    """上传文档暂存目录（data/imports，启动/首次使用时创建）。"""
    from config import data_dir

    path = Path(data_dir) / "imports"
    path.mkdir(parents=True, exist_ok=True)
    return path


__all__ = ["ACCEPT_ATTR", "ALLOWED_EXTS", "MAX_DOC_BYTES", "extract_text", "imports_dir", "split_chunks"]
