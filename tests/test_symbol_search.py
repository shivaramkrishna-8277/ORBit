"""Fyers symbol master search (offline fixture)."""
import json

from src.broker import symbol_search as sym


def _fixture_master() -> dict:
    return {
        "NSE:SBIN-EQ": {
            "symTicker": "NSE:SBIN-EQ",
            "exSymName": "STATE BANK OF INDIA",
        },
        "NSE:TCS-EQ": {
            "symTicker": "NSE:TCS-EQ",
            "exSymName": "TATA CONSULTANCY SERV LT",
        },
        "NSE:RELIANCE-EQ": {
            "symTicker": "NSE:RELIANCE-EQ",
            "exSymName": "RELIANCE INDUSTRIES LTD",
        },
        "NSE:NIFTY50-INDEX": {
            "symTicker": "NSE:NIFTY50-INDEX",
            "exSymName": "NIFTY 50",
        },
    }


def test_search_finds_ticker_prefix():
    hits = sym.search_nse_equity("TCS", master=_fixture_master())
    assert hits[0]["symbol"] == "NSE:TCS-EQ"


def test_search_finds_company_name():
    hits = sym.search_nse_equity("reliance", master=_fixture_master())
    assert any(h["symbol"] == "NSE:RELIANCE-EQ" for h in hits)


def test_excludes_indices():
    hits = sym.search_nse_equity("NIFTY", master=_fixture_master())
    assert all(not h["symbol"].endswith("-INDEX") for h in hits)


def test_resolve_exact_ticker():
    assert sym.resolve_nse_equity("sbin", master=_fixture_master()) == "NSE:SBIN-EQ"
    assert sym.resolve_nse_equity("NSE:RELIANCE-EQ", master=_fixture_master()) == "NSE:RELIANCE-EQ"


def test_load_master_from_cache_file(tmp_path, monkeypatch):
    cache = tmp_path / "NSE_CM_sym_master.json"
    cache.write_text(json.dumps(_fixture_master()), encoding="utf-8")
    monkeypatch.setattr(sym, "CACHE_PATH", cache)
    monkeypatch.setattr(sym, "_cache_is_fresh", lambda _path: True)
    data = sym.load_master(cache_path=cache)
    assert "NSE:SBIN-EQ" in data