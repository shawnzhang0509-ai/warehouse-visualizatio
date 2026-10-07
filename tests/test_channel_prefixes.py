from channel_prefixes import load_po_channel_prefixes, parse_channel_line
from pathlib import Path


def test_parse_lines():
    assert parse_channel_line("130") == "130"
    assert parse_channel_line("河北_378") == "378"
    assert parse_channel_line("山东_999") == "999"
    assert parse_channel_line("# comment") is None


def test_load_file_count():
    path = Path(__file__).resolve().parents[1] / "Data-NZ" / "po_channel_prefixes.txt"
    codes = load_po_channel_prefixes(path)
    assert len(codes) == 124
    assert "378" in codes
    assert "130" in codes
