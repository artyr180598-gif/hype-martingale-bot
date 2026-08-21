from dataclasses import dataclass
from decimal import Decimal

import httpx


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    symbol: str
    last: Decimal
    change_24h: Decimal
    volume_24h: Decimal
    funding_rate: Decimal | None
    open_interest: Decimal | None


class BybitMarketService:
    BASE_URL = "https://api.bybit.com"

    async def snapshot(self, symbol: str) -> MarketSnapshot:
        async with httpx.AsyncClient(base_url=self.BASE_URL, timeout=5.0) as client:
            ticker_response = await client.get(
                "/v5/market/tickers", params={"category": "linear", "symbol": symbol}
            )
            ticker_response.raise_for_status()
            ticker = ticker_response.json()["result"]["list"][0]

            oi_response = await client.get(
                "/v5/market/open-interest",
                params={"category": "linear", "symbol": symbol, "intervalTime": "5min", "limit": 1},
            )
            oi_response.raise_for_status()
            oi_list = oi_response.json()["result"]["list"]
            oi = Decimal(oi_list[0]["openInterest"]) if oi_list else None

            return MarketSnapshot(
                symbol=symbol,
                last=Decimal(ticker["lastPrice"]),
                change_24h=Decimal(ticker["price24hPcnt"]) * 100,
                volume_24h=Decimal(ticker["volume24h"]),
                funding_rate=Decimal(ticker["fundingRate"]) if ticker.get("fundingRate") else None,
                open_interest=oi,
            )


def format_snapshot(snapshot: MarketSnapshot) -> str:
    change = f"{snapshot.change_24h:+.2f}%"
    funding = "n/a" if snapshot.funding_rate is None else f"{snapshot.funding_rate * 100:+.4f}%"
    oi = "n/a" if snapshot.open_interest is None else f"{snapshot.open_interest:,.0f}"
    return (
        f"📊 <b>{snapshot.symbol}</b>\n\n"
        f"Price: <code>{snapshot.last}</code>\n"
        f"24h: <b>{change}</b>\n"
        f"Volume: <code>{snapshot.volume_24h:,.0f}</code>\n"
        f"Funding: <code>{funding}</code>\n"
        f"Open Interest: <code>{oi}</code>\n\n"
        "Data: Bybit linear perpetuals\n"
        "No synthetic values."
    )
