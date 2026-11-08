"""ChillyBank's Vietnamese price job refreshes only opted-in manual instruments, in VND."""

from __future__ import annotations

import httpx
import pytest

from backend.db.instrument_store import InstrumentStore
from backend.services import vn_prices

VNDIRECT = {"s": "ok", "t": [1, 2], "c": [27.1, 27.4]}
PNJ = {"data": [{"masp": "SJC", "giaban": 14100, "giamua": 13800}]}


@pytest.fixture
def feeds(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    hits: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        hits.append(request.url.host)
        return httpx.Response(200, json=PNJ if "pnj" in request.url.host else VNDIRECT)

    real = httpx.AsyncClient
    monkeypatch.setattr(
        vn_prices.httpx, "AsyncClient", lambda **kw: real(transport=httpx.MockTransport(handler), **kw)
    )
    return hits


def _manual(symbol: str, source_id: str, price: float) -> dict:
    return InstrumentStore().create(
        {
            "symbol": symbol,
            "name": symbol,
            "asset_class": "equity",
            "price_source": "manual",
            "price_source_id": source_id,
            "quote_currency": "VND",
            "manual_price": price,
        }
    )


async def test_opted_in_instruments_get_vnd_prices(db_isolated, feeds: list[str]) -> None:
    shs = _manual("SHS", "vndirect:SHS", 1.0)
    sjc = _manual("SJC", "pnj:SJC", 1.0)
    mine = _manual("ART", "", 5_000_000.0)

    assert await vn_prices.refresh_vn_prices() == 2

    store = InstrumentStore()
    assert store.find_by_id(shs["id"])["manual_price"] == pytest.approx(27_400)
    # PNJ quotes thousand VND per chỉ; a lượng is ten chỉ, valued at the buy-back price.
    assert store.find_by_id(sjc["id"])["manual_price"] == pytest.approx(138_000_000)
    assert store.find_by_id(mine["id"])["manual_price"] == 5_000_000.0
    assert sorted(set(feeds)) == ["dchart-api.vndirect.com.vn", "edge-api.pnj.io"]


async def test_a_failed_feed_leaves_the_last_price(db_isolated, monkeypatch) -> None:
    sjc = _manual("SJC", "pnj:SJC", 120_000_000.0)
    real = httpx.AsyncClient
    monkeypatch.setattr(
        vn_prices.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(lambda r: httpx.Response(503, text="down")), **kw),
    )
    assert await vn_prices.refresh_vn_prices() == 0
    assert InstrumentStore().find_by_id(sjc["id"])["manual_price"] == 120_000_000.0
