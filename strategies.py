"""
Торговые стратегии (технический анализ).

Стратегия — это правило: "если случилось X, покупай; если Y — продавай".
Мы реализуем две классические стратегии:

1. RSI (Relative Strength Index, индекс относительной силы)
   Показывает, перекуплен или перепродан актив. Шкала от 0 до 100.
   - RSI < 30 → актив перепродан → возможный отскок вверх → СИГНАЛ НА ПОКУПКУ
   - RSI > 70 → актив перекуплен → возможна коррекция вниз → СИГНАЛ НА ПРОДАЖУ

2. MA Crossover (пересечение скользящих средних)
   Берём две средние цены: "быструю" (за 9 свечей) и "медленную" (за 21 свечу).
   - Быстрая пересекла медленную снизу вверх → начало восходящего тренда → ПОКУПКА
   - Быстрая пересекла медленную сверху вниз → начало нисходящего тренда → ПРОДАЖА

Ни одна стратегия не работает 100%. Поэтому мы соединяем их + ИИ-анализ новостей (см. ai_analyzer.py).
"""

import logging

import numpy as np
import pandas as pd

import config

log = logging.getLogger(__name__)


def calculate_rsi(prices: pd.Series, period: int = 14) -> float:
    """
    Считаем RSI по формуле Wilder'а.

    Аргумент prices — это последовательность цен закрытия (Series — это как массив, но "умный").
    Возвращаем одно число — текущее значение RSI.
    """
    # Считаем разницу между соседними ценами: +5, -3, +1 ...
    delta = prices.diff()

    # Разделяем на "прирост" и "падение" — отдельные ряды.
    gains = delta.where(delta > 0, 0.0)
    losses = -delta.where(delta < 0, 0.0)

    # Скользящее среднее приростов и падений.
    avg_gain = gains.rolling(window=period).mean()
    avg_loss = losses.rolling(window=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)   # делим, защищаясь от деления на 0
    rsi = 100 - (100 / (1 + rs))
    return float(rsi.iloc[-1])  # последнее значение — текущий RSI


def calculate_ma_crossover(prices: pd.Series, fast: int, slow: int) -> str:
    """
    Сигнал по пересечению скользящих средних.

    Возвращает "buy", "sell" или "hold".
    """
    if len(prices) < slow + 1:
        return "hold"  # данных мало — не рискуем

    ma_fast = prices.rolling(window=fast).mean()
    ma_slow = prices.rolling(window=slow).mean()

    # Сравниваем ДВЕ ПОСЛЕДНИЕ свечи, чтобы поймать именно момент пересечения.
    # На предпоследней быстрая была ниже медленной, а на последней — выше = пересечение вверх.
    prev_fast, prev_slow = ma_fast.iloc[-2], ma_slow.iloc[-2]
    curr_fast, curr_slow = ma_fast.iloc[-1], ma_slow.iloc[-1]

    if prev_fast <= prev_slow and curr_fast > curr_slow:
        return "buy"
    if prev_fast >= prev_slow and curr_fast < curr_slow:
        return "sell"
    return "hold"


def analyze_candles(candles: list[list]) -> dict:
    """
    Главная функция модуля. Принимает свечи с KuCoin и возвращает:
        {"signal": "buy"|"sell"|"hold", "rsi": 42.3, "ma_signal": "buy", "price": 67500.0}

    candles — список вида [[time, open, close, high, low, volume, ...], ...]
    Это формат, в котором отдаёт свечи KuCoin API.
    """
    # Превращаем в pandas DataFrame — это удобная таблица с колонками.
    df = pd.DataFrame(candles, columns=["time", "open", "close", "high", "low", "volume", "turnover"])
    df = df.astype(float)
    df = df.sort_values("time").reset_index(drop=True)  # KuCoin отдаёт от новых к старым

    closes = df["close"]
    current_price = closes.iloc[-1]

    # Считаем индикаторы (только если они включены в конфиге).
    rsi_value = None
    rsi_signal = "hold"
    if config.USE_RSI and len(closes) > config.RSI_PERIOD:
        rsi_value = calculate_rsi(closes, config.RSI_PERIOD)
        if rsi_value < config.RSI_OVERSOLD:
            rsi_signal = "buy"
        elif rsi_value > config.RSI_OVERBOUGHT:
            rsi_signal = "sell"

    ma_signal = "hold"
    if config.USE_MA_CROSSOVER:
        ma_signal = calculate_ma_crossover(closes, config.MA_FAST, config.MA_SLOW)

    # Итоговый технический сигнал: если хотя бы один индикатор говорит "buy" и второй не против — покупаем.
    final = "hold"
    if rsi_signal == "buy" and ma_signal != "sell":
        final = "buy"
    elif rsi_signal == "sell" and ma_signal != "buy":
        final = "sell"
    elif ma_signal == "buy" and rsi_signal != "sell":
        final = "buy"
    elif ma_signal == "sell" and rsi_signal != "buy":
        final = "sell"

    log.info("RSI=%.1f, MA=%s → итог=%s, цена=%.2f", rsi_value or -1, ma_signal, final, current_price)
    return {
        "signal": final,
        "rsi": rsi_value,
        "ma_signal": ma_signal,
        "price": current_price,
    }
