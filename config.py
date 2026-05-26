"""
Конфигурация бота. Здесь все настройки в одном месте.

Зачем отдельный файл config.py?
- Чтобы не было "магических чисел" в коде. Захотел поменять плечо с 3 на 5 — поправил в одном месте.
- Чтобы не лезть в логику бота, когда нужно просто подкрутить параметры.
"""

import os
from dotenv import load_dotenv

# Загружаем переменные из файла .env (там лежат секретные ключи)
load_dotenv()


# ===== Секреты (из .env, в коде не светятся) =====
KUCOIN_API_KEY = os.getenv("KUCOIN_API_KEY", "")
KUCOIN_API_SECRET = os.getenv("KUCOIN_API_SECRET", "")
KUCOIN_API_PASSPHRASE = os.getenv("KUCOIN_API_PASSPHRASE", "")

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "0"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "")
TELEGRAM_PHONE = os.getenv("TELEGRAM_PHONE", "")
TELEGRAM_CHANNELS = [c.strip() for c in os.getenv("TELEGRAM_CHANNELS", "").split(",") if c.strip()]


# ===== Режим работы =====
# Если False — бот ничего не покупает по-настоящему, только пишет в лог "купил бы".
# Начинать ВСЕГДА с False. Включай True только после долгого тестирования.
LIVE_TRADING = os.getenv("LIVE_TRADING", "false").lower() == "true"


# ===== Какие монеты торгуем =====
# Спотовые пары: покупаем за USDT
SPOT_SYMBOLS = ["BTC-USDT", "ETH-USDT", "SOL-USDT"]

# Фьючерсные контракты (на KuCoin Futures формат отличается!)
FUTURES_SYMBOLS = ["XBTUSDTM", "ETHUSDTM"]


# ===== Управление риском (САМОЕ ВАЖНОЕ!) =====
# Сколько % от депозита тратим на одну сделку. 2% — золотое правило для новичка.
RISK_PER_TRADE_PERCENT = 2.0

# Стоп-лосс: на сколько % ниже цены покупки закрываем сделку в минус.
# Это страховка. Без стоп-лосса даже с ИИ можно слить весь депозит на одной сделке.
STOP_LOSS_PERCENT = 3.0

# Тейк-профит: на сколько % выше цены покупки фиксируем прибыль.
TAKE_PROFIT_PERCENT = 6.0

# Максимальное плечо на фьючерсах. Чем больше — тем выше и прибыль, и риск ликвидации.
# Новичку 3x — это уже много. Профи редко идут выше 10x.
MAX_LEVERAGE = 3


# ===== Стратегии =====
# Какие индикаторы используем. Можно включать/выключать каждый.
USE_RSI = True              # Индекс относительной силы (перекупленность/перепроданность)
USE_MA_CROSSOVER = True     # Пересечение скользящих средних (тренд)
USE_AI_NEWS = True          # ИИ-анализ новостей

# Параметры RSI: классика — 14 свечей, перекуплено выше 70, перепродано ниже 30.
RSI_PERIOD = 14
RSI_OVERSOLD = 30           # Ниже — сигнал на покупку
RSI_OVERBOUGHT = 70         # Выше — сигнал на продажу

# Скользящие средние: быстрая пересекает медленную = смена тренда.
MA_FAST = 9
MA_SLOW = 21

# Таймфрейм свечей: "1min", "5min", "15min", "1hour", "4hour", "1day"
# Для новичка — "1hour", меньше шума.
TIMEFRAME = "1hour"


# ===== Цикл бота =====
# Как часто бот проверяет рынок и новости (в секундах).
# 300 = каждые 5 минут. Чаще — рискуешь упереться в лимиты API.
LOOP_INTERVAL_SECONDS = 300


# ===== Новости =====
# RSS-ленты популярных крипто-сайтов. Можно добавлять свои.
NEWS_RSS_FEEDS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
]

# Сколько последних новостей анализируем за один проход.
NEWS_LIMIT = 20
