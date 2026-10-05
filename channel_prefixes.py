"""从 po_channel_prefixes.txt 读取渠道前三位（支持 河北_378 / 378）。"""

import os
import re
from collections import defaultdict
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent

# V4 渠道子目录：河北_321、山东_446（省名_三位渠道号）
_NAMED_CHANNEL_FOLDER_RE = re.compile(r"^([\u4e00-\u9fff]+)_(\d{3})$")
_PLAIN_CHANNEL_FOLDER_RE = re.compile(r"^(\d{3})$")


def parse_named_channel_folder(name: str) -> tuple[str, str] | None:
    """文件夹名 → (省/族名, 三位子渠道)，如 河北_321 → ('河北','321')。"""
    text = (name or "").strip()
    m = _NAMED_CHANNEL_FOLDER_RE.match(text)
    if m:
        return m.group(1), m.group(2)
    m2 = _PLAIN_CHANNEL_FOLDER_RE.match(text)
    if m2:
        return "", m2.group(1)
    m3 = re.match(r"^(\d{3})", text)
    if m3:
        return "", m3.group(1)
    return None


def scan_channel_families_from_dirs(data_dir: Path | str | None) -> dict[str, list[str]]:
    """扫描 Output 下 河北_321 类目录，得到 { '河北': ['321','352',...] }。"""
    base = Path(data_dir or "")
    if not base.is_dir():
        return {}
    subs: dict[str, set[str]] = {}
    for child in base.iterdir():
        if not child.is_dir():
            continue
        parsed = parse_named_channel_folder(child.name)
        if not parsed or not parsed[0]:
            continue
        fam, sub = parsed
        subs.setdefault(fam, set()).add(sub)
    return {fam: sorted(codes, key=lambda c: int(c) if c.isdigit() else c) for fam, codes in subs.items()}


def load_channel_families_file(path: Path | None) -> dict[str, list[str]]:
    """可选 channel_families.csv：family,sub_channel 或 河北_321。"""
    if path is None or not Path(path).is_file():
        return {}
    out: dict[str, set[str]] = {}
    for row in _read_families_table(path):
        fam = str(row.get("family") or row.get("族") or row.get("省") or "").strip()
        sub = str(row.get("sub_channel") or row.get("sub") or row.get("渠道") or "").strip()
        line = str(row.get("folder") or row.get("目录") or "").strip()
        if line and "_" in line:
            parsed = parse_named_channel_folder(line.replace(" ", ""))
            if parsed and parsed[0]:
                fam, sub = parsed
        if not sub:
            sub = parse_channel_line(line or fam) or ""
        if fam and sub:
            out.setdefault(fam, set()).add(sub.zfill(3) if sub.isdigit() else sub[:3])
    return {k: sorted(v, key=lambda c: int(c) if c.isdigit() else c) for k, v in out.items()}


def _read_families_table(path: Path) -> list[dict]:
    import panel_data as pd

    rows = pd._read_table(path) if path.suffix.lower() == ".csv" else []
    if rows:
        return rows
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    out: list[dict] = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "," in line:
            parts = [p.strip() for p in line.split(",", 1)]
            if len(parts) == 2:
                out.append({"family": parts[0], "sub_channel": parts[1]})
                continue
        parsed = parse_named_channel_folder(line)
        if parsed and parsed[0]:
            out.append({"family": parsed[0], "sub_channel": parsed[1]})
    return out


def merge_channel_family_maps(*maps: dict[str, list[str]]) -> dict[str, list[str]]:
    merged: dict[str, set[str]] = {}
    for m in maps:
        for fam, subs in (m or {}).items():
            merged.setdefault(fam, set()).update(subs)
    return {k: sorted(v, key=lambda c: int(c) if c.isdigit() else c) for k, v in merged.items()}


def sub_to_family_map(families: dict[str, list[str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for fam, subs in (families or {}).items():
        for sub in subs:
            out[str(sub).zfill(3) if str(sub).isdigit() else str(sub)] = fam
    return out


def channel_group_key(sub_channel: str, sub_to_family: dict[str, str]) -> str:
    """汇总用渠道键：子渠道 321 在族内则显示 河北，否则仍为 321。"""
    sub = normalize_sku_channel_code(sub_channel, "") or str(sub_channel or "").strip()
    if not sub:
        return ""
    return sub_to_family.get(sub, sub)


def load_channel_families_from_po_prefixes(path: Path | None) -> dict[str, list[str]]:
    """po_channel_prefixes.txt 每行 河北_321 或 河北_321_备注 → 合并为省渠道。"""
    if path is None or not Path(path).is_file():
        return {}
    text = ""
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = Path(path).read_text(encoding=encoding)
            break
        except Exception:
            text = ""
    if not text:
        return {}
    subs: dict[str, set[str]] = defaultdict(set)
    for line in text.splitlines():
        raw = (line or "").strip()
        if not raw or raw.startswith("#"):
            continue
        token = re.split(r"[\s,，\t]+", raw, maxsplit=1)[0].strip()
        parsed = parse_named_channel_folder(token)
        if parsed and parsed[0]:
            subs[parsed[0]].add(parsed[1])
    return {
        fam: sorted(codes, key=lambda c: int(c) if c.isdigit() else c)
        for fam, codes in subs.items()
    }


def channel_family_scan_dirs(region_key: str, data_dir: Path | str | None) -> list[Path]:
    """销量/渠道文件夹可能出现的位置（Output 根、latest、时间戳子目录、Data 模板目录）。"""
    seen: list[Path] = []
    d = Path(data_dir or "")
    if d.is_dir():
        seen.append(d.resolve())
        latest = d / "latest"
        if latest.is_dir():
            seen.append(latest.resolve())
        for child in sorted(d.iterdir()):
            if not child.is_dir():
                continue
            if re.fullmatch(r"\d{8}_\d{6}", child.name):
                seen.append(child.resolve())
    extra = os.getenv("INVENTORY_SALES_ROOT", "").strip()
    if extra:
        p = Path(extra)
        if p.is_dir():
            seen.append(p.resolve())
    try:
        tpl = po_channel_prefixes_path(region_key).parent
        if tpl.is_dir():
            seen.append(tpl.resolve())
    except Exception:
        pass
    out: list[Path] = []
    for p in seen:
        if p not in out:
            out.append(p)
    return out


def channel_families_txt_paths(region_key: str, data_dir: Path | str | None) -> list[Path]:
    paths: list[Path] = []
    tpl = po_channel_prefixes_path(region_key).parent
    for name in ("channel_families.txt", "channel_families.csv"):
        p = tpl / name
        if p.is_file():
            paths.append(p)
    out = Path(data_dir or "")
    for name in ("channel_families.txt", "channel_families.csv"):
        p = out / name
        if p.is_file() and p not in paths:
            paths.append(p)
    return paths


def load_channel_families_from_txt(path: Path | None) -> dict[str, list[str]]:
    """channel_families.txt：每行 河北_321（与销量子文件夹同名即可）。"""
    if path is None or not Path(path).is_file():
        return {}
    if path.suffix.lower() == ".csv":
        return load_channel_families_file(path)
    text = ""
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = Path(path).read_text(encoding=encoding)
            break
        except Exception:
            text = ""
    subs: dict[str, set[str]] = defaultdict(set)
    for line in text.splitlines():
        raw = (line or "").strip()
        if not raw or raw.startswith("#"):
            continue
        parsed = parse_named_channel_folder(raw.split()[0])
        if parsed and parsed[0]:
            subs[parsed[0]].add(parsed[1])
    return {
        fam: sorted(codes, key=lambda c: int(c) if c.isdigit() else c)
        for fam, codes in subs.items()
    }


def load_channel_families_for_region(
    region_key: str,
    data_dir: Path | str | None,
) -> dict[str, list[str]]:
    maps: list[dict[str, list[str]]] = []
    for scan_dir in channel_family_scan_dirs(region_key, data_dir):
        maps.append(scan_channel_families_from_dirs(scan_dir))
    maps.append(load_channel_families_from_po_prefixes(po_channel_prefixes_path(region_key)))
    for fam_path in channel_families_txt_paths(region_key, data_dir):
        maps.append(load_channel_families_from_txt(fam_path))
    return merge_channel_family_maps(*maps)


def family_for_channel_link(link: str, families: dict[str, list[str]]) -> str | None:
    """表格/气泡上的 河北 或 321 → 可下钻的省名。"""
    key = str(link or "").strip()
    if not key or not families:
        return None
    if key in families:
        return key
    code = normalize_sku_channel_code(key, "") or key
    for fam, subs in families.items():
        if code in subs or key in subs:
            return fam
    return None


def list_merged_channel_filter_options(
    prefixes: list[str],
    families: dict[str, list[str]],
) -> list[str]:
    """下拉：全部 + 省族 + 未归并的三位渠道。"""
    covered = sub_to_family_map(families)
    singles = [p for p in prefixes if p not in covered]
    fams = sorted(families.keys())
    return fams + singles


def resolve_channel_filter(
    text: str,
    families: dict[str, list[str]],
) -> tuple[str, set[str]] | None:
    """
    筛选渠道：'河北' → 族内所有三位号；'321' → {321}。
    返回 (mode, allowed_subs) 或 None=全部。
    """
    raw = str(text or "").strip()
    if not raw or raw in ("全部", "ALL", "*"):
        return None
    if raw in families:
        return ("family", set(families[raw]))
    code = normalize_sku_channel_code(raw, "") or normalize_channel_filter_digits(raw)
    if code:
        return ("sub", {code})
    return ("sub", {raw})


def normalize_channel_filter_digits(channel: str) -> str:
    digits = re.sub(r"\D", "", str(channel or ""))
    if len(digits) >= 3:
        return digits[:3]
    return digits.zfill(3) if digits else ""


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
