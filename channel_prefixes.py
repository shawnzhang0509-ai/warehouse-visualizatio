"""从 po_channel_prefixes.txt 读取渠道前三位（支持 河北_378 / 378）。"""

import re
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent

def normalize_sku_channel_code(channel: str | None, sku: str | None = "") -> str:
    """三位渠道号：996 / 996.0 / '996' → '996'；缺省从 SKU 前三位取。"""
    text = str(channel or "").strip()
    if text:
        try:
            num = float(str(text).replace(",", ""))
            if abs(num - round(num)) < 1e-9:
                n = int(round(num))
                s = str(n)
                return s.zfill(3) if len(s) < 3 else s[:3]
        except ValueError:
            pass
        digits = re.sub(r"\D", "", text)
        if len(digits) >= 3:
            return digits[:3]
        if digits:
            return digits.zfill(3)
    sku_text = str(sku or "").strip()
    if sku_text:
        prefix = sku_text.split("-", 1)[0] if "-" in sku_text else sku_text[:3]
        digits = re.sub(r"\D", "", prefix)
        if len(digits) >= 3:
            return digits[:3]
        if digits:
            return digits.zfill(3)
    return ""


def parse_channel_line(line: str) -> str | None:
    text = (line or "").strip()
    if not text or text.startswith("#"):
        return None
    if "_" in text:
        text = text.split("_")[-1].strip()
    else:
        text = re.sub(r"^[\u4e00-\u9fff]+", "", text).strip()
    if re.fullmatch(r"\d{3}", text):
        return text
    m = re.search(r"(\d{3})\s*$", text)
    return m.group(1) if m else None


def load_po_channel_prefixes(path: Path | None) -> list[str]:
    if path is None or not Path(path).is_file():
        return []
    codes: set[str] = set()
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = Path(path).read_text(encoding=encoding)
            break
        except Exception:
            text = ""
    if not text:
        return []
    for line in text.splitlines():
        code = parse_channel_line(line)
        if code:
            codes.add(code)
    return sorted(codes, key=int)


def po_channel_prefixes_path(region_key: str, region_cfg: dict | None = None) -> Path:
    cfg = dict(region_cfg or {})
    rel = str(cfg.get("po_channel_prefixes_file") or "po_channel_prefixes.txt").strip()
    template_dir = str(cfg.get("template_dir") or f"Data-{region_key.upper()}").strip()
    path = Path(template_dir)
    if not path.is_absolute():
        path = ROOT_DIR / path
    return (path / rel).resolve()


def load_region_po_channel_prefixes(region_key: str, region_cfg: dict | None = None) -> list[str]:
    return load_po_channel_prefixes(po_channel_prefixes_path(region_key, region_cfg))
