"""
Исполнитель сделок.

Этот модуль — связующее звено между "решением" (купить/продать) и "действием" (отправить ордер).

Он отвечает за:
- Расчёт РАЗМЕРА позиции (важнее, чем сам сигнал! плохой размер = слив депо).
- Постановку стоп-лосса и тейк-профита (защита от просадок).
- Запись истории сделок в файл (чтобы потом анализировать, что сработало).
"""

import json
import logging
import os
from datetime import datetime

import config
from kucoin_client import KuCoinTrader

log = logging.getLogger(__name__)

TRADES_FILE = "trades.json"


class Trader:
    def __init__(self, client: KuCoinTrader):
        self.client = client
        # Открытые позиции: {symbol: {"side": "buy", "entry_price": 67500, "size": 0.001, "stop": 65475, "tp": 71550}}
        self.positions: dict = {}

    def calculate_position_size(self, balance: float) -> float:
        """
        Сколько USDT тратим на одну сделку.

        Формула простая: процент от депозита (из config.RISK_PER_TRADE_PERCENT).
        Например, депо $1000, риск 2% → $20 на сделку.

        Так даже после серии из 10 убыточных сделок депо потеряет всего ~20% — это спасает от слива.
        """
        return balance * (config.RISK_PER_TRADE_PERCENT / 100)

    def execute_spot_trade(self, symbol: str, signal: str, current_price: float) -> None:
        """Открываем или закрываем спот-сделку по сигналу."""
        balance = self.client.get_spot_balance("USDT")

        if signal == "buy" and symbol not in self.positions:
            amount = self.calculate_position_size(balance)
            if amount < 1:
                log.warning("Слишком маленький депозит для %s (нужно хотя бы 1 USDT)", symbol)
                return

            self.client.place_spot_order(symbol, "buy", amount)

            # Запоминаем позицию, чтобы потом отслеживать стоп-лосс и тейк-профит.
            self.positions[symbol] = {
                "side": "buy",
                "entry_price": current_price,
                "amount_usdt": amount,
                "stop_loss": current_price * (1 - config.STOP_LOSS_PERCENT / 100),
                "take_profit": current_price * (1 + config.TAKE_PROFIT_PERCENT / 100),
                "opened_at": datetime.utcnow().isoformat(),
            }
            self._log_trade(symbol, "OPEN_BUY", current_price, amount)

        elif signal == "sell" and symbol in self.positions:
            # Закрываем по сигналу (выходим из лонга).
            self.client.place_spot_order(symbol, "sell", 0)
            entry = self.positions[symbol]["entry_price"]
            pnl = (current_price - entry) / entry * 100
            self._log_trade(symbol, "CLOSE", current_price, self.positions[symbol]["amount_usdt"], pnl)
            del self.positions[symbol]

    def check_stop_loss_take_profit(self, symbol: str, current_price: float) -> None:
        """
        Проверяем, не пора ли закрыть позицию по стоп-лоссу/тейк-профиту.

        Это критично: даже если сигналов на выход нет, мы ОБЯЗАНЫ выйти,
        если цена упала ниже стопа (ограничиваем убыток) или выросла до тейка (фиксируем прибыль).
        """
        if symbol not in self.positions:
            return

        pos = self.positions[symbol]

        if current_price <= pos["stop_loss"]:
            log.warning("STOP-LOSS сработал для %s по цене %.2f (вход был %.2f)",
                        symbol, current_price, pos["entry_price"])
            self.client.place_spot_order(symbol, "sell", 0)
            self._log_trade(symbol, "STOP_LOSS", current_price, pos["amount_usdt"], -config.STOP_LOSS_PERCENT)
            del self.positions[symbol]

        elif current_price >= pos["take_profit"]:
            log.info("TAKE-PROFIT сработал для %s по цене %.2f", symbol, current_price)
            self.client.place_spot_order(symbol, "sell", 0)
            self._log_trade(symbol, "TAKE_PROFIT", current_price, pos["amount_usdt"], config.TAKE_PROFIT_PERCENT)
            del self.positions[symbol]

    def execute_futures_trade(self, symbol: str, signal: str, current_price: float) -> None:
        """То же самое для фьючерсов: открываем/закрываем позицию с плечом."""
        balance = self.client.get_spot_balance("USDT")  # маржу всё равно берём с основного счёта
        if signal == "buy" and symbol not in self.positions:
            amount = self.calculate_position_size(balance)
            self.client.place_futures_order(symbol, "buy", amount, config.MAX_LEVERAGE)
            self.positions[symbol] = {
                "side": "buy",
                "entry_price": current_price,
                "amount_usdt": amount,
                "leverage": config.MAX_LEVERAGE,
                "opened_at": datetime.utcnow().isoformat(),
            }
            self._log_trade(symbol, "OPEN_LONG", current_price, amount)

        elif signal == "sell" and symbol not in self.positions:
            # На фьючерсах "sell" — это шорт (открываем позицию на падение), а не закрытие.
            amount = self.calculate_position_size(balance)
            self.client.place_futures_order(symbol, "sell", amount, config.MAX_LEVERAGE)
            self.positions[symbol] = {
                "side": "sell",
                "entry_price": current_price,
                "amount_usdt": amount,
                "leverage": config.MAX_LEVERAGE,
                "opened_at": datetime.utcnow().isoformat(),
            }
            self._log_trade(symbol, "OPEN_SHORT", current_price, amount)

    def _log_trade(self, symbol: str, action: str, price: float, amount: float, pnl: float = 0.0) -> None:
        """Сохраняем каждую сделку в файл — потом можно посмотреть статистику."""
        record = {
            "time": datetime.utcnow().isoformat(),
            "symbol": symbol,
            "action": action,
            "price": price,
            "amount_usdt": amount,
            "pnl_percent": pnl,
        }
        # Дописываем в JSON-файл (по одной строке на сделку, формат JSONL).
        with open(TRADES_FILE, "a") as f:
            f.write(json.dumps(record) + "\n")
        log.info("Сделка записана: %s", record)
