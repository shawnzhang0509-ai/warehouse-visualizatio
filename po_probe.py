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
            "模板含 LIKE @SkuFilter 但未拼接 + '%'；@SkuFilter 为空时 LIKE '' 几乎无 SKU，填 996 时只匹配完全等于 996"
        )
    if re.search(r"LIKE\s+'996'\s*(?!%)", sql_text, re.I):
        warnings.append("模板含 LIKE '996' 缺少 %，996 系列 SKU 匹配不到")
    if re.search(r"\bWHERE\b[^;]*\bCheckinDate\b", sql_text, re.I):
        warnings.append(
            "WHERE 里用了别名 CheckinDate（SQL Server 不允许）；应写 c.ActualArrivingDate IS NULL"
        )
    return warnings


def po_template_filter_hints(sql_text: str) -> list[str]:
    low = sql_text.lower()
    hints = []
    if "actualarrivingdate is null" in low or re.search(r"\bcheckindate\s+is\s+null", low, re.I):
        hints.append("含「到货日为空」条件")
    if "inner join" in low and "containers" in low:
        hints.append("对 Containers 用 INNER JOIN（无柜 PO 会被丢掉）")
    return hints


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
