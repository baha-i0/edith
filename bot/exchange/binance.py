"""Binance USD-M Futures REST istemcisi (bagimlilik: sadece requests).

Neden hazir kutuphane degil: emir gonderen katmanin her satirini gormek,
imzalama/zaman senkronu/yuvarlama hatalarini kendi kontrolumuzde tutmak.
Bu dosyadaki hatalar dogrudan para kaybettirir.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
import urllib.parse
from typing import Any, Dict, List, Optional

import requests

from ..config import Config
from ..models import Candle, SymbolFilters, safe_float
from .base import MarketData

LIVE_BASE = "https://fapi.binance.com"
TESTNET_BASE = "https://testnet.binancefuture.com"

log = logging.getLogger(__name__)


class BinanceError(RuntimeError):
    def __init__(self, code: int, msg: str):
        super().__init__(f"Binance hata {code}: {msg}")
        self.code = code
        self.msg = msg


class BinanceFutures(MarketData):
    def __init__(self, cfg: Config, api_key: str = "", api_secret: str = "",
                 testnet: bool = False):
        self.cfg = cfg
        self.base = TESTNET_BASE if testnet else LIVE_BASE
        self.key = api_key
        self.secret = api_secret
        self.session = requests.Session()
        if api_key:
            self.session.headers["X-MBX-APIKEY"] = api_key
        self.session.headers["User-Agent"] = "edith-bot/1.0"
        self._time_offset = 0
        self._filters: Dict[str, SymbolFilters] = {}

    # --------------------------------------------------------------- alt yapi
    def _request(self, method: str, path: str, params: Optional[dict] = None,
                 signed: bool = False) -> Any:
        params = dict(params or {})
        e = self.cfg.execution
        last_err: Optional[Exception] = None

        for attempt in range(e.max_retries):
            try:
                if signed:
                    if not (self.key and self.secret):
                        raise BinanceError(-1, "imzali istek icin API anahtari yok")
                    params["timestamp"] = int(time.time() * 1000) + self._time_offset
                    params["recvWindow"] = e.recv_window
                    query = urllib.parse.urlencode(params, doseq=True)
                    sig = hmac.new(self.secret.encode(), query.encode(), hashlib.sha256).hexdigest()
                    url = f"{self.base}{path}?{query}&signature={sig}"
                    resp = self.session.request(method, url, timeout=e.request_timeout)
                else:
                    resp = self.session.request(
                        method, f"{self.base}{path}", params=params, timeout=e.request_timeout
                    )

                if resp.status_code == 200:
                    return resp.json()

                body = _safe_json(resp)
                code = int(body.get("code", resp.status_code))
                msg = str(body.get("msg", resp.text[:200]))

                if code == -1021:  # zaman senkronu bozuk
                    self.sync_time()
                    last_err = BinanceError(code, msg)
                elif resp.status_code in (418, 429) or code == -1003:  # rate limit / ban
                    wait = min(60, 2 ** attempt * 5)
                    log.warning("Rate limit, %ss bekleniyor", wait)
                    time.sleep(wait)
                    last_err = BinanceError(code, msg)
                elif 500 <= resp.status_code < 600:
                    last_err = BinanceError(code, msg)
                else:
                    # 4xx: istek hatali, tekrar denemek anlamsiz
                    raise BinanceError(code, msg)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_err = exc

            if attempt < e.max_retries - 1:
                time.sleep(min(8.0, 0.5 * (2 ** attempt)))

        raise BinanceError(-2, f"{path} {e.max_retries} denemede basarisiz: {last_err}")

    def sync_time(self) -> int:
        server = self._request("GET", "/fapi/v1/time")["serverTime"]
        self._time_offset = int(server) - int(time.time() * 1000)
        return self._time_offset

    # ------------------------------------------------------------ market veri
    def klines(self, symbol: str, interval: str, limit: int = 500,
               start_ms: Optional[int] = None, end_ms: Optional[int] = None) -> List[Candle]:
        params: Dict[str, Any] = {"symbol": symbol, "interval": interval,
                                  "limit": min(limit, 1500)}
        if start_ms:
            params["startTime"] = start_ms
        if end_ms:
            params["endTime"] = end_ms
        raw = self._request("GET", "/fapi/v1/klines", params)
        now = int(time.time() * 1000)
        return [
            Candle(
                open_time=int(k[0]), open=float(k[1]), high=float(k[2]), low=float(k[3]),
                close=float(k[4]), volume=float(k[5]), close_time=int(k[6]),
                closed=int(k[6]) < now,
            )
            for k in raw
        ]

    def book_ticker(self, symbol: str) -> Dict[str, float]:
        d = self._request("GET", "/fapi/v1/ticker/bookTicker", {"symbol": symbol})
        if isinstance(d, list):
            d = next((x for x in d if x.get("symbol") == symbol), {})
        bid = safe_float(d.get("bidPrice"))
        ask = safe_float(d.get("askPrice"))
        mid = (bid + ask) / 2 if bid and ask else 0.0
        spread_bps = ((ask - bid) / mid * 10_000) if mid else 9_999.0
        return {"bid": bid, "ask": ask, "mid": mid, "spread_bps": spread_bps}

    def funding(self, symbol: str) -> Dict[str, float]:
        d = self._request("GET", "/fapi/v1/premiumIndex", {"symbol": symbol})
        if isinstance(d, list):
            d = next((x for x in d if x.get("symbol") == symbol), {})
        return {
            "rate": safe_float(d.get("lastFundingRate")),
            "next_funding_ms": safe_float(d.get("nextFundingTime")),
            "mark": safe_float(d.get("markPrice")),
        }

    def filters(self, symbol: str) -> SymbolFilters:
        if symbol in self._filters:
            return self._filters[symbol]
        info = self._request("GET", "/fapi/v1/exchangeInfo")
        for s in info.get("symbols", []):
            if s["symbol"] != symbol:
                continue
            tick = step = min_qty = min_notional = 0.0
            max_qty = float("inf")
            for f in s["filters"]:
                t = f["filterType"]
                if t == "PRICE_FILTER":
                    tick = float(f["tickSize"])
                elif t == "LOT_SIZE":
                    step = float(f["stepSize"])
                    min_qty = float(f["minQty"])
                    max_qty = float(f["maxQty"])
                elif t == "MARKET_LOT_SIZE":
                    max_qty = min(max_qty, float(f["maxQty"]))
                elif t in ("MIN_NOTIONAL", "NOTIONAL"):
                    min_notional = float(f.get("notional", f.get("minNotional", 0)))
            sf = SymbolFilters(
                symbol=symbol, tick_size=tick, step_size=step, min_qty=min_qty,
                min_notional=min_notional or 5.0,
                price_precision=int(s.get("pricePrecision", 8)),
                qty_precision=int(s.get("quantityPrecision", 8)),
                max_qty=max_qty,
            )
            self._filters[symbol] = sf
            return sf
        raise BinanceError(-1, f"sembol bulunamadi: {symbol}")

    # ------------------------------------------------------------ hesap/emir
    def account(self) -> dict:
        return self._request("GET", "/fapi/v2/account", signed=True)

    def balances(self) -> Dict[str, float]:
        acc = self.account()
        return {
            "equity": safe_float(acc.get("totalMarginBalance")),
            "available": safe_float(acc.get("availableBalance")),
            "unrealized": safe_float(acc.get("totalUnrealizedProfit")),
            "wallet": safe_float(acc.get("totalWalletBalance")),
        }

    def position_risk(self, symbol: Optional[str] = None) -> List[dict]:
        params = {"symbol": symbol} if symbol else {}
        data = self._request("GET", "/fapi/v2/positionRisk", params, signed=True)
        return [p for p in data if abs(safe_float(p.get("positionAmt"))) > 0]

    def set_leverage(self, symbol: str, leverage: int) -> dict:
        return self._request("POST", "/fapi/v1/leverage",
                             {"symbol": symbol, "leverage": leverage}, signed=True)

    def set_margin_type(self, symbol: str, margin_type: str) -> None:
        try:
            self._request("POST", "/fapi/v1/marginType",
                          {"symbol": symbol, "marginType": margin_type}, signed=True)
        except BinanceError as exc:
            if exc.code != -4046:  # "No need to change margin type"
                raise

    def market_order(self, symbol: str, side: str, qty: float,
                     reduce_only: bool = False, client_id: str = "") -> dict:
        params: Dict[str, Any] = {
            "symbol": symbol, "side": side, "type": "MARKET", "quantity": qty,
        }
        if reduce_only:
            params["reduceOnly"] = "true"
        if client_id:
            params["newClientOrderId"] = client_id
        return self._request("POST", "/fapi/v1/order", params, signed=True)

    def post_only_order(self, symbol: str, side: str, qty: float, price: float,
                        client_id: str = "") -> dict:
        """Maker-only limit emri (timeInForce=GTX).

        GTX = "Good Till Crossing": emir tahtaya maker olarak yazilamayacaksa
        (yani aninda dolup taker olacaksa) borsa emri REDDEDER. Bu sayede
        %0.02 maker komisyonu garanti; %0.05 taker'a kazara dusulmez.
        Reddedilme normal bir sonuctur, hata degil -- cagiran taraf ele alir.
        """
        params: Dict[str, Any] = {
            "symbol": symbol, "side": side, "type": "LIMIT", "quantity": qty,
            "price": price, "timeInForce": "GTX",
        }
        if client_id:
            params["newClientOrderId"] = client_id
        return self._request("POST", "/fapi/v1/order", params, signed=True)

    def query_order(self, symbol: str, client_id: str) -> dict:
        return self._request("GET", "/fapi/v1/order",
                             {"symbol": symbol, "origClientOrderId": client_id}, signed=True)

    def cancel_order(self, symbol: str, client_id: str) -> dict:
        return self._request("DELETE", "/fapi/v1/order",
                             {"symbol": symbol, "origClientOrderId": client_id}, signed=True)

    def stop_market(self, symbol: str, side: str, stop_price: float,
                    client_id: str = "") -> dict:
        """Tum pozisyonu kapatan koruma emri (closePosition=true).

        closePosition kullanmak, kismi cikislardan sonra miktar uyusmazligi
        yuzunden koruma emrinin reddedilmesini engeller.
        """
        params: Dict[str, Any] = {
            "symbol": symbol, "side": side, "type": "STOP_MARKET",
            "stopPrice": stop_price, "closePosition": "true",
            "workingType": "MARK_PRICE", "priceProtect": "true",
        }
        if client_id:
            params["newClientOrderId"] = client_id
        return self._request("POST", "/fapi/v1/order", params, signed=True)

    def take_profit_market(self, symbol: str, side: str, stop_price: float,
                           qty: Optional[float] = None, client_id: str = "") -> dict:
        params: Dict[str, Any] = {
            "symbol": symbol, "side": side, "type": "TAKE_PROFIT_MARKET",
            "stopPrice": stop_price, "workingType": "MARK_PRICE",
            "priceProtect": "true",
        }
        if qty is None:
            params["closePosition"] = "true"
        else:
            params["quantity"] = qty
            params["reduceOnly"] = "true"
        if client_id:
            params["newClientOrderId"] = client_id
        return self._request("POST", "/fapi/v1/order", params, signed=True)

    def user_trades(self, symbol: str, start_ms: int, limit: int = 500) -> List[dict]:
        """Gerceklesmis islemler. Gercek PnL ve komisyonu buradan okuyoruz;
        tahmin yerine borsanin kendi kaydi."""
        return self._request(
            "GET", "/fapi/v1/userTrades",
            {"symbol": symbol, "startTime": start_ms, "limit": limit}, signed=True,
        )

    def cancel_all(self, symbol: str) -> dict:
        return self._request("DELETE", "/fapi/v1/allOpenOrders", {"symbol": symbol}, signed=True)

    def open_orders(self, symbol: str) -> List[dict]:
        return self._request("GET", "/fapi/v1/openOrders", {"symbol": symbol}, signed=True)


def _safe_json(resp) -> dict:
    try:
        d = resp.json()
        return d if isinstance(d, dict) else {"msg": str(d)}
    except ValueError:
        return {"msg": resp.text[:200]}
