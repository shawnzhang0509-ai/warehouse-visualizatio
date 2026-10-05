"""SQL 模板占位符（与 shopmanagement 等仓库一致：{sku} → 渠道前三位等）。"""

import re


def _sql_literal(value: str) -> str:
    return (value or "").replace("'", "''")


def _channel_in_clause(channel_prefixes: list[str]) -> str:
    codes = sorted({str(c).strip() for c in channel_prefixes if str(c).strip().isdigit() and len(str(c).strip()) == 3}, key=int)
    if not codes:
        return ""
    inner = ", ".join(f"'{_sql_literal(c)}'" for c in codes)
    return f"LEFT(p.Sku, 3) IN ({inner})"


def apply_sql_placeholders(
    sql_text: str,
    variables: dict | None = None,
) -> tuple[str, list[str]]:
    """
    替换 {sku} 等占位符。返回 (新 SQL, 日志说明列表)。
    常见写法：p.Sku LIKE '{sku}%' —
      - 配置了 po_sku_prefix → LIKE '130%'
      - 否则有 po_channel_prefixes.txt → LEFT(p.Sku,3) IN (...)
      - 否则空 → LIKE '%'
    """
    if not sql_text:
        return sql_text, []
    variables = variables or {}
    sku = _sql_literal(str(variables.get("sku") or variables.get("SKU") or "").strip())
    channel_prefixes = variables.get("channel_prefixes") or []
    if isinstance(channel_prefixes, str):
        channel_prefixes = [channel_prefixes]
    notes: list[str] = []
    out = sql_text

    sku_like_pattern = re.compile(
        r"(?P<col>(?:\w+\.)?Sku)\s+LIKE\s+(?P<q>['\"])\{sku\}%(?P=q)",
        re.IGNORECASE,
    )
    bare_like_pattern = re.compile(
        r"LIKE\s+(?P<q>['\"])\{sku\}%(?P=q)",
        re.IGNORECASE,
    )

    if sku_like_pattern.search(out) or bare_like_pattern.search(out):
        if sku:
            repl = f"LIKE '{sku}%'"
            out = sku_like_pattern.sub(lambda m: f"{m.group('col')} {repl}", out)
            out = bare_like_pattern.sub(repl, out)
            notes.append(f"{{sku}} → 单渠道 '{sku}'")
        elif channel_prefixes:
            in_clause = _channel_in_clause(list(channel_prefixes))
            if in_clause:
                out = sku_like_pattern.sub(in_clause, out)
                out = bare_like_pattern.sub(in_clause.replace("p.Sku", "Sku"), out)
                notes.append(f"{{sku}} → 渠道列表 {len(set(channel_prefixes))} 个（po_channel_prefixes.txt）")
            else:
                out = sku_like_pattern.sub(lambda m: f"{m.group('col')} LIKE '%'", out)
                out = bare_like_pattern.sub("LIKE '%'", out)
                notes.append("{sku} → (渠道列表为空，改为全部 SKU)")
        else:
            out = sku_like_pattern.sub(lambda m: f"{m.group('col')} LIKE '%'", out)
            out = bare_like_pattern.sub("LIKE '%'", out)
            notes.append("{sku} → (空=全部 SKU)")

    if re.search(r"\{sku\}", out, re.IGNORECASE):
        repl = sku
        out = re.sub(r"\{sku\}", repl, out, flags=re.IGNORECASE)
        if not notes:
            notes.append(f"{{sku}} → '{sku or '(空)'}'")

    return out, notes


def placeholders_in_sql(sql_text: str) -> bool:
    return bool(sql_text and re.search(r"\{sku\}", sql_text, re.IGNORECASE))


def placeholder_context_for_region(region_key: str, region_cfg: dict | None = None) -> dict[str, str]:
    import os

    from runner_config import load_runner_config

    sku = os.getenv("PO_SKU_PREFIX", "").strip()
    cfg = dict(region_cfg) if isinstance(region_cfg, dict) else {}
    rk = (region_key or "").strip().upper()
    try:
        loaded = load_runner_config()
        reg = dict((loaded.get("regions") or {}).get(rk) or {})
        reg.update(cfg)
        cfg = reg
        if not sku:
            sku = str(cfg.get("po_sku_prefix") or "").strip()
        if not sku:
            sku = str((loaded.get("settings") or {}).get("po_sku_prefix") or "").strip()
    except Exception:
        if not sku:
            sku = str(cfg.get("po_sku_prefix") or "").strip()
    from channel_prefixes import load_region_po_channel_prefixes

    prefixes = load_region_po_channel_prefixes(rk, cfg)
    return {"sku": sku, "channel_prefixes": prefixes}
