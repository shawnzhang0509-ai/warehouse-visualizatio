"""SQL 模板占位符（与 shopmanagement 等仓库一致：{sku} → 渠道前三位等）。"""

import re


def _sql_literal(value: str) -> str:
    return (value or "").replace("'", "''")


def apply_sql_placeholders(sql_text: str, variables: dict[str, str] | None = None) -> tuple[str, list[str]]:
    """
    替换 {sku} 等占位符。返回 (新 SQL, 日志说明列表)。
    常见写法：p.Sku LIKE '{sku}%' — 空 sku 时改为 LIKE '%'（查全部）。
    """
    if not sql_text:
        return sql_text, []
    variables = variables or {}
    sku = _sql_literal(str(variables.get("sku") or variables.get("SKU") or "").strip())
    notes: list[str] = []
    out = sql_text

    like_pattern = re.compile(
        r"LIKE\s+(?P<q>['\"])\{sku\}%(?P=q)",
        re.IGNORECASE,
    )
    if like_pattern.search(out):
        like_expr = f"LIKE '{sku}%'" if sku else "LIKE '%'"
        out = like_pattern.sub(like_expr, out)
        notes.append(f"{{sku}} → '{sku or '(空=全部 SKU)'}'")

    if re.search(r"\{sku\}", out, re.IGNORECASE):
        repl = sku
        out = re.sub(r"\{sku\}", repl, out, flags=re.IGNORECASE)
        if f"{{sku}}" not in "".join(notes):
            notes.append(f"{{sku}} → '{sku or '(空)'}'")

    return out, notes


def placeholders_in_sql(sql_text: str) -> bool:
    return bool(sql_text and re.search(r"\{sku\}", sql_text, re.IGNORECASE))


def placeholder_context_for_region(region_key: str, region_cfg: dict | None = None) -> dict[str, str]:
    import os

    from runner_config import load_runner_config

    sku = os.getenv("PO_SKU_PREFIX", "").strip()
    cfg = region_cfg if isinstance(region_cfg, dict) else {}
    if not sku:
        sku = str(cfg.get("po_sku_prefix") or "").strip()
    if not sku:
        try:
            loaded = load_runner_config()
            rk = (region_key or "").strip().upper()
            reg = (loaded.get("regions") or {}).get(rk) or {}
            sku = str(reg.get("po_sku_prefix") or "").strip()
            if not sku:
                sku = str((loaded.get("settings") or {}).get("po_sku_prefix") or "").strip()
        except Exception:
            pass
    return {"sku": sku}
