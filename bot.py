"""
Главный файл бота. Запускай его командой:   python bot.py

Что происходит в цикле (по шагам):
1. Собираем свежие новости из RSS и Telegram.
2. Отдаём новости в ИИ — он говорит, какие монеты "bullish" (растут), а какие "bearish" (падают).
3. По каждой монете из списка:
   а. Качаем свечи с KuCoin.
   б. Считаем индикаторы (RSI, скользящие средние) — получаем технический сигнал.
   в. Объединяем технический сигнал + мнение ИИ → итоговое решение (buy/sell/hold).
   г. Если есть открытая позиция — проверяем стоп-лосс и тейк-профит.
   д. Если решение buy/sell — исполняем сделку.
4. Засыпаем на LOOP_INTERVAL_SECONDS и идём по кругу.

Бот не агрессивный: предпочитает "ничего не делать", чем делать сомнительную сделку.
Это правильно — большая часть прибыли получается от того, что ты НЕ влез в плохую сделку.
"""

import logging
import time

import config
from ai_analyzer import analyze_news, combine_signals
from kucoin_client import KuCoinTrader
from news_collector import collect_all_news
from strategies import analyze_candles
from trader import Trader

# Настраиваем логирование: и в файл, и в консоль.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("bot")


def extract_base_currency(symbol: str) -> str:
    """Из 'BTC-USDT' делаем 'BTC'. Из фьючерса 'XBTUSDTM' — тоже 'BTC' (XBT — старое обозначение BTC)."""
    if "-" in symbol:
        return symbol.split("-")[0]
    if symbol.startswith("XBT"):
        return "BTC"
    return symbol.replace("USDTM", "").replace("USDT", "")


def run_one_iteration(client: KuCoinTrader, trader: Trader) -> None:
    """Один проход по всем монетам — то, что повторяется в цикле."""

    # Шаг 1: новости
    news = []
    ai_verdicts = {}
    if config.USE_AI_NEWS:
        news = collect_all_news()
        # Список монет, по которым ждём мнение ИИ.
        all_currencies = list({extract_base_currency(s) for s in config.SPOT_SYMBOLS + config.FUTURES_SYMBOLS})
        ai_verdicts = analyze_news(news, all_currencies)

    # Шаг 2: спот
    for symbol in config.SPOT_SYMBOLS:
        currency = extract_base_currency(symbol)
        candles = client.get_spot_candles(symbol, config.TIMEFRAME, limit=100)
        if not candles:
            continue

        result = analyze_candles(candles)
        technical_signal = result["signal"]
        current_price = result["price"]

        # Перед тем как открыть новую сделку — проверим, не пора ли закрыть старую.
        trader.check_stop_loss_take_profit(symbol, current_price)

        # Объединяем технику и ИИ.
        ai_verdict = ai_verdicts.get(currency, {})
        final_signal = combine_signals(ai_verdict, technical_signal) if ai_verdict else technical_signal

        log.info("[SPOT %s] техника=%s, ии=%s, итог=%s, цена=%.2f",
                 symbol, technical_signal,
                 ai_verdict.get("sentiment", "—"), final_signal, current_price)

        if final_signal in ("buy", "sell"):
            trader.execute_spot_trade(symbol, final_signal, current_price)

    # Шаг 3: фьючерсы (если в конфиге есть символы)
    for symbol in config.FUTURES_SYMBOLS:
        currency = extract_base_currency(symbol)
        # Преобразуем "1hour" из config.TIMEFRAME в минуты для фьючерсного API.
        tf_minutes = {"1min": 1, "5min": 5, "15min": 15, "30min": 30,
                      "1hour": 60, "4hour": 240, "1day": 1440}.get(config.TIMEFRAME, 60)
        candles = client.get_futures_candles(symbol, tf_minutes, limit=100)
        if not candles:
            continue

        result = analyze_candles(candles)
        technical_signal = result["signal"]
        current_price = result["price"]

        ai_verdict = ai_verdicts.get(currency, {})
        final_signal = combine_signals(ai_verdict, technical_signal) if ai_verdict else technical_signal

        log.info("[FUTURES %s] техника=%s, ии=%s, итог=%s",
                 symbol, technical_signal, ai_verdict.get("sentiment", "—"), final_signal)

        if final_signal in ("buy", "sell"):
            trader.execute_futures_trade(symbol, final_signal, current_price)


def main() -> None:
    log.info("=" * 60)
    log.info("Запуск бота. LIVE_TRADING=%s", config.LIVE_TRADING)
    log.info("Спот: %s", config.SPOT_SYMBOLS)
    log.info("Фьючерсы: %s (плечо до %dx)", config.FUTURES_SYMBOLS, config.MAX_LEVERAGE)
    log.info("Риск на сделку: %.1f%%, стоп: %.1f%%, тейк: %.1f%%",
             config.RISK_PER_TRADE_PERCENT, config.STOP_LOSS_PERCENT, config.TAKE_PROFIT_PERCENT)
    log.info("=" * 60)

    if not config.LIVE_TRADING:
        log.warning("РЕЖИМ СИМУЛЯЦИИ. Реальных ордеров не будет. Чтобы включить — поставь LIVE_TRADING=true в .env")

    client = KuCoinTrader()
    trader = Trader(client)

    # Главный цикл: бесконечно крутимся, пока пользователь не нажмёт Ctrl+C.
    while True:
        try:
            run_one_iteration(client, trader)
        except KeyboardInterrupt:
            log.info("Остановка бота (Ctrl+C)")
            break
        except Exception as e:
            # Любая непойманная ошибка не должна валить бота — просто логируем и идём дальше.
            log.exception("Ошибка в цикле: %s", e)

        log.info("Сплю %d секунд до следующей проверки", config.LOOP_INTERVAL_SECONDS)
        time.sleep(config.LOOP_INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
