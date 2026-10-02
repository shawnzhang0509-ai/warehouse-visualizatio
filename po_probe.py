"""PO 模板 SQL 诊断：与 app 导出共用，不修改用户脚本。"""

import re


def parse_sku_filter(sql_text: str) -> str | None:
    m = re.search(
        r"DECLARE\s+@SkuFilter\s+\w+(?:\([^)]*\))?\s*=\s*'([^']*)'",
        sql_text,
        re.I,
    )
    return m.group(1) if m else None


def warn_po_sql_patterns(sql_text: str) -> list[str]:
    warnings = []
    if re.search(r"LIKE\s+@SkuFilter\s*(?!\+)", sql_text, re.I):
        warnings.append(
            "模板含 LIKE @SkuFilter 但未拼接 + '%'；@SkuFilter 填 996 时只会匹配 SKU 完全等于 996"
        )
    if re.search(r"LIKE\s+'996'\s*(?!%)", sql_text, re.I):
        warnings.append("模板含 LIKE '996' 缺少 %，996 系列 SKU 匹配不到")
    return warnings


def _strip_line_comments(sql_text: str) -> str:
    lines = []
    for line in sql_text.splitlines():
        if "--" in line:
            line = line[: line.index("--")]
        lines.append(line)
    return "\n".join(lines)


def build_po_row_count_batch(sql_text: str) -> str | None:
    """DECLARE + SELECT 批处理：在同一会话内对 SELECT 体做 COUNT（不删用户 WHERE）。"""
    cleaned = _strip_line_comments(sql_text)
    match = re.search(r"\bSELECT\b", cleaned, re.I)
    if not match:
        return None
    preamble = cleaned[: match.start()].strip()
    select_part = cleaned[match.start() :].strip().rstrip(";")
    select_part = re.sub(r"\s+ORDER\s+BY\s+.+$", "", select_part, flags=re.I | re.S)
    if not select_part:
        return None
    parts = []
    if preamble:
        parts.append(preamble)
    parts.append(f"SELECT COUNT_BIG(*) AS PoRowCount FROM (\n{select_part}\n) AS __po_probe")
    return "\n".join(parts)
