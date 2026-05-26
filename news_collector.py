"""
Сборщик новостей.

Откуда берём новости:
1. RSS-ленты крипто-сайтов (CoinDesk, Cointelegraph и т.д.) — это публичные потоки новостей
   в специальном формате, их умеет читать библиотека feedparser.
2. Telegram-каналы — через библиотеку telethon (она работает как обычный Telegram-клиент,
   читает каналы под твоим аккаунтом).

На выходе функции возвращают список словарей вида:
    {"source": "coindesk", "title": "...", "text": "...", "url": "..."}

Эти словари потом отдаются ИИ для анализа.
"""

import logging
from datetime import datetime, timedelta, timezone

import feedparser
from telethon.sync import TelegramClient

import config

log = logging.getLogger(__name__)


def fetch_rss_news() -> list[dict]:
    """Качаем последние новости из всех RSS-лент, перечисленных в config.NEWS_RSS_FEEDS."""
    news = []
    for url in config.NEWS_RSS_FEEDS:
        try:
            # feedparser сам разбирает XML — нам отдают готовый объект с заголовками и текстом.
            feed = feedparser.parse(url)
            for entry in feed.entries[: config.NEWS_LIMIT]:
                news.append({
                    "source": feed.feed.get("title", url),
                    "title": entry.get("title", ""),
                    "text": entry.get("summary", ""),
                    "url": entry.get("link", ""),
                    "published": entry.get("published", ""),
                })
        except Exception as e:
            # Если один источник лёг — не падаем, просто идём дальше.
            log.warning("RSS %s не доступен: %s", url, e)
    return news


def fetch_telegram_news() -> list[dict]:
    """
    Качаем последние сообщения из Telegram-каналов.

    При ПЕРВОМ запуске Telegram попросит код подтверждения — придёт в Telegram.
    После этого создастся файл .session, и дальше всё будет работать без вопросов.
    """
    if not config.TELEGRAM_API_ID or not config.TELEGRAM_CHANNELS:
        return []  # Если в .env пусто — Telegram просто отключён.

    news = []
    # Файл сессии сохраняется, чтобы каждый раз не вводить код.
    client = TelegramClient("bot_session", config.TELEGRAM_API_ID, config.TELEGRAM_API_HASH)
    try:
        client.start(phone=config.TELEGRAM_PHONE)
        since = datetime.now(timezone.utc) - timedelta(hours=6)  # только свежее за 6 часов
        for channel in config.TELEGRAM_CHANNELS:
            try:
                for msg in client.iter_messages(channel, limit=config.NEWS_LIMIT):
                    if msg.date < since or not msg.text:
                        continue
                    news.append({
                        "source": f"telegram:{channel}",
                        "title": msg.text[:100],   # первые 100 символов — как заголовок
                        "text": msg.text,
                        "url": f"https://t.me/{channel}/{msg.id}",
                        "published": msg.date.isoformat(),
                    })
            except Exception as e:
                log.warning("Канал %s недоступен: %s", channel, e)
    finally:
        client.disconnect()
    return news


def collect_all_news() -> list[dict]:
    """Главная функция: собирает новости отовсюду в один список."""
    all_news = fetch_rss_news() + fetch_telegram_news()
    log.info("Собрано %d новостей", len(all_news))
    return all_news
