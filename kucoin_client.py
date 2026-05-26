"""
Обёртка над KuCoin API.

У KuCoin ДВА разных API:
1. Спот (Spot) — обычная торговля: купил BTC за USDT, лежит у тебя на счёте.
2. Фьючерсы (Futures) — торговля с плечом, можно играть на повышение (long) и на понижение (short).

Мы делаем один класс KuCoinTrader, который умеет и то, и другое.
Если LIVE_TRADING=False, никакие реальные ордера НЕ отправляются (paper trading) — это страховка для тестов.
"""

import logging
import time

from kucoin.client import Market, Trade, User
from kucoin_futures.client import Market as FuturesMarket, Trade as FuturesTrade, User as FuturesUser

import config

log = logging.getLogger(__name__)


class KuCoinTrader:
    def __init__(self):
        # Клиенты для спота. Market — публичные данные (свечи, цены), Trade — ордера, User — баланс.
        self.spot_market = Market(url="https://api.kucoin.com")
        self.spot_trade = Trade(
            key=config.KUCOIN_API_KEY,
            secret=config.KUCOIN_API_SECRET,
            passphrase=config.KUCOIN_API_PASSPHRASE,
        )
        self.spot_user = User(
            key=config.KUCOIN_API_KEY,
            secret=config.KUCOIN_API_SECRET,
            passphrase=config.KUCOIN_API_PASSPHRASE,
        )

        # Клиенты для фьючерсов — отдельные URL и часто отдельные API-ключи.
        self.futures_market = FuturesMarket(url="https://api-futures.kucoin.com")
        self.futures_trade = FuturesTrade(
            key=config.KUCOIN_API_KEY,
            secret=config.KUCOIN_API_SECRET,
            passphrase=config.KUCOIN_API_PASSPHRASE,
        )
        self.futures_user = FuturesUser(
            key=config.KUCOIN_API_KEY,
            secret=config.KUCOIN_API_SECRET,
            passphrase=config.KUCOIN_API_PASSPHRASE,
        )

    # ===== СПОТ =====

    def get_spot_candles(self, symbol: str, timeframe: str = "1hour", limit: int = 100) -> list:
        """
        Качаем свечи (исторические цены) для спотовой пары.
        Возвращаем список из последних `limit` свечей.
        """
        try:
            # KuCoin отдаёт свечи в формате: [time, open, close, high, low, volume, turnover]
            candles = self.spot_market.get_kline(symbol, timeframe)
            return candles[:limit]
        except Exception as e:
            log.error("Не удалось получить свечи для %s: %s", symbol, e)
            return []

    def get_spot_balance(self, currency: str = "USDT") -> float:
        """Сколько у нас USDT (или другой валюты) на спотовом аккаунте."""
        try:
            accounts = self.spot_user.get_account_list(currency=currency, account_type="trade")
            if not accounts:
                return 0.0
            return float(accounts[0]["balance"])
        except Exception as e:
            log.error("Баланс получить не удалось: %s", e)
            return 0.0

    def get_spot_price(self, symbol: str) -> float:
        """Текущая цена пары (например, BTC-USDT)."""
        try:
            ticker = self.spot_market.get_ticker(symbol)
            return float(ticker["price"])
        except Exception as e:
            log.error("Цена для %s не получена: %s", symbol, e)
            return 0.0

    def place_spot_order(self, symbol: str, side: str, usdt_amount: float) -> dict:
        """
        Размещаем рыночный ордер на споте.

        side: "buy" или "sell"
        usdt_amount: сколько USDT тратим (для покупки) или какова сумма в USDT при продаже.

        Рыночный ордер исполняется по текущей цене — это просто и подходит новичку.
        Лимитный ордер требует указать цену, его мы пока не используем.
        """
        if not config.LIVE_TRADING:
            # Симуляция: только логируем, реальный ордер не идёт.
            log.info("[PAPER] %s %s на %.2f USDT", side.upper(), symbol, usdt_amount)
            return {"paper": True, "side": side, "symbol": symbol, "amount": usdt_amount}

        try:
            if side == "buy":
                # При покупке указываем funds — сколько USDT тратим.
                order = self.spot_trade.create_market_order(symbol, "buy", funds=str(usdt_amount))
            else:
                # При продаже считаем, сколько актива у нас есть, и продаём всё.
                base = symbol.split("-")[0]   # из "BTC-USDT" → "BTC"
                balance = self.get_spot_balance(base)
                if balance <= 0:
                    log.warning("Нечего продавать в %s", symbol)
                    return {}
                order = self.spot_trade.create_market_order(symbol, "sell", size=str(balance))
            log.info("Спот-ордер размещён: %s", order)
            return order
        except Exception as e:
            log.error("Ошибка ордера %s %s: %s", side, symbol, e)
            return {}

    # ===== ФЬЮЧЕРСЫ =====

    def get_futures_candles(self, symbol: str, timeframe_minutes: int = 60, limit: int = 100) -> list:
        """
        Свечи для фьючерса. У фьючерсного API таймфрейм задаётся числом минут (60 = 1 час).
        """
        try:
            now_ms = int(time.time() * 1000)
            from_ms = now_ms - timeframe_minutes * 60 * 1000 * limit
            candles = self.futures_market.get_kline_data(symbol, timeframe_minutes, from_ms, now_ms)
            return candles
        except Exception as e:
            log.error("Свечи фьючерсов для %s: %s", symbol, e)
            return []

    def place_futures_order(self, symbol: str, side: str, usdt_amount: float, leverage: int) -> dict:
        """
        Открываем позицию на фьючерсах с плечом.

        side: "buy" (лонг, играем на рост) или "sell" (шорт, играем на падение).
        leverage: плечо (1-100). Чем выше — тем больше прибыль И больше риск ликвидации.
        """
        # Защита: не разрешаем плечо выше, чем в config.
        leverage = min(leverage, config.MAX_LEVERAGE)

        if not config.LIVE_TRADING:
            log.info("[PAPER FUTURES] %s %s на %.2f USDT с плечом %dx",
                     side.upper(), symbol, usdt_amount, leverage)
            return {"paper": True}

        try:
            # На фьючерсах KuCoin размер задаётся количеством контрактов (1 контракт = 1 USDT обычно).
            size = int(usdt_amount * leverage)
            order = self.futures_trade.create_market_order(
                symbol=symbol,
                side=side,
                lever=str(leverage),
                size=size,
            )
            log.info("Фьючерс-ордер размещён: %s", order)
            return order
        except Exception as e:
            log.error("Ошибка фьючерс-ордера: %s", e)
            return {}

    def close_futures_position(self, symbol: str) -> dict:
        """Закрываем открытую позицию (фиксируем прибыль или убыток)."""
        if not config.LIVE_TRADING:
            log.info("[PAPER] Закрыли бы позицию %s", symbol)
            return {"paper": True}
        try:
            # closeOrder=True говорит бирже: "это закрытие, не открытие новой позиции".
            order = self.futures_trade.create_market_order(symbol=symbol, side="buy", close_order=True, size=0)
            log.info("Позиция %s закрыта", symbol)
            return order
        except Exception as e:
            log.error("Не закрылась позиция %s: %s", symbol, e)
            return {}
