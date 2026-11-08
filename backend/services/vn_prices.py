"""Live prices for Vietnamese instruments Yahoo does not carry (ChillyBank).

Yahoo prices HOSE as ``XXX.VN``. HNX/UPCoM stocks, and domestic gold with its premium over
world spot, have no feed among the app's sources, so they live as ``manual`` instruments
and this job refreshes their ``manual_price``. It touches only instruments that opt in through
``price_source_id``:

- ``vndirect:<TICKER>`` — last daily close from VNDirect's chart API (quoted in thousand VND).
- ``pnj:<CODE>`` — PNJ's buy-back price (``giamua``, thousand VND per chỉ) as VND per lượng.
  The buy-back price, not the selling price, because a holding is worth what a shop pays for it.

The hosts are fixed here; nothing the model writes reaches them.
"""

from __future__ import annotations

import logging
import time

import httpx

from backend.db.instrument_store import InstrumentStore
from backend.services.instruments import update_instrument

logger = logging.getLogger(__name__)

_VNDIRECT = "https://dchart-api.vndirect.com.vn/dchart/history"
_PNJ = "https://edge-api.pnj.io/ecom-frontend/v1/get-gold-price?zone=00"
_HEADERS = {"User-Agent": "Mozilla/5.0 ChillyBank"}
CHI_PER_LUONG = 10


def vndirect_close(payload: dict) -> float | None:
    closes = payload.get("c") or []
    return float(closes[-1]) * 1000 if payload.get("s") == "ok" and closes else None


def pnj_luong_price(payload: dict, code: str) -> float | None:
    for row in payload.get("data") or []:
        if row.get("masp") == code and row.get("giamua"):
            return float(row["giamua"]) * 1000 * CHI_PER_LUONG
    return None


def _opted_in() -> list[dict]:
    store = InstrumentStore()
    return [
        i
        for i in store.query_instruments()
        if i.get("price_source") == "manual"
        and str(i.get("price_source_id") or "").startswith(("vndirect:", "pnj:"))
    ]


async def refresh_vn_prices() -> int:
    """Refresh every opted-in instrument; returns how many prices were written."""
    instruments = _opted_in()
    if not instruments:
        return 0
    written = 0
    now = int(time.time())
    async with httpx.AsyncClient(timeout=15, headers=_HEADERS) as client:
        pnj: dict | None = None
        for inst in instruments:
            source, _, code = str(inst["price_source_id"]).partition(":")
            try:
                if source == "vndirect":
                    params = {"resolution": "D", "symbol": code.upper(), "from": now - 10 * 86400, "to": now}
                    price = vndirect_close((await client.get(_VNDIRECT, params=params)).json())
                else:
                    if pnj is None:
                        pnj = (await client.get(_PNJ)).json()
                    price = pnj_luong_price(pnj, code.upper())
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning("vn_prices: %s failed: %s", inst["price_source_id"], exc)
                continue
            if price is None:
                logger.warning("vn_prices: no price for %s", inst["price_source_id"])
                continue
            if price != inst.get("manual_price"):
                result = update_instrument(inst["id"], {"manual_price": price, "price_source": "manual"})
                written += 0 if result.get("error") else 1
    return written
