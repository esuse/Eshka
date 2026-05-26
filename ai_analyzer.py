"""
ИИ-анализатор новостей.

Идея простая:
1. Берём список новостей.
2. Склеиваем их в один большой текст.
3. Отправляем в Claude (или GPT) вместе с инструкцией: "проанализируй новости, скажи bullish/bearish/neutral
   по каждой монете, дай оценку уверенности от 0 до 100".
4. ИИ возвращает JSON, мы его парсим — и решаем, что покупать/продавать.

Почему ИИ, а не просто ключевые слова?
- Заголовок "Bitcoin ETF approved" — это позитив.
- Заголовок "SEC sues Coinbase" — это негатив для всего рынка.
- Заголовок "Whale moves 10000 BTC to exchange" — нейтральный для новичка, но опытный поймёт, что это давление продаж.
- ИИ улавливает контекст, сарказм, отрицания ("not approved") — простой поиск по словам этого не умеет.
"""

import json
import logging

import anthropic

import config

log = logging.getLogger(__name__)


# Инструкция для ИИ. Пишем её один раз, чтобы можно было кэшировать (см. prompt caching).
SYSTEM_PROMPT = """Ты опытный крипто-аналитик. Твоя задача — анализировать новости и определять их влияние на цену криптовалют.

Для каждой монеты из списка верни оценку:
- sentiment: "bullish" (рост), "bearish" (падение) или "neutral" (нейтрально)
- confidence: число от 0 до 100 — насколько ты уверен в своём прогнозе
- reasoning: одно предложение, почему ты так думаешь

ВАЖНО:
- Не выдумывай новости, которых не было.
- Если по монете нет новостей — ставь neutral с confidence=0.
- Отвечай ТОЛЬКО валидным JSON, без лишнего текста, без markdown-обёрток.

Формат ответа:
{
  "BTC": {"sentiment": "bullish", "confidence": 75, "reasoning": "..."},
  "ETH": {"sentiment": "neutral", "confidence": 10, "reasoning": "..."}
}
"""


def analyze_news(news: list[dict], symbols: list[str]) -> dict:
    """
    Отправляем новости в ИИ и получаем оценки по каждой монете.

    symbols: список монет вроде ["BTC", "ETH", "SOL"] — для них ИИ должен дать вердикт.
    Возвращает словарь { "BTC": {"sentiment": "bullish", "confidence": 75, ...}, ... }
    """
    if not news:
        log.info("Новостей нет — ИИ не зовём")
        return {}

    if not config.ANTHROPIC_API_KEY:
        log.warning("ANTHROPIC_API_KEY не задан — ИИ-анализ пропущен")
        return {}

    # Склеиваем новости в один текст. Ограничиваем размер каждого заголовка, чтобы не съесть весь контекст.
    news_text = "\n\n".join(
        f"[{n['source']}] {n['title']}\n{n['text'][:500]}"
        for n in news
    )

    user_message = (
        f"Проанализируй новости и дай оценку для этих монет: {', '.join(symbols)}.\n\n"
        f"НОВОСТИ:\n{news_text}"
    )

    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    try:
        # Используем Claude — отлично работает с длинными текстами и анализом тональности.
        response = client.messages.create(
            model="claude-sonnet-4-6",   # быстрая и недорогая модель, хватает для новостей
            max_tokens=2000,
            system=[
                # cache_control делает кэширование промпта — повторные вызовы стоят в 10 раз дешевле.
                {"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}
            ],
            messages=[{"role": "user", "content": user_message}],
        )
        raw = response.content[0].text.strip()

        # На всякий случай: если ИИ обернул JSON в ```json ... ``` — снимаем обёртку.
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

        verdicts = json.loads(raw)
        log.info("ИИ выдал оценки: %s", verdicts)
        return verdicts
    except Exception as e:
        log.error("Ошибка ИИ-анализа: %s", e)
        return {}


def combine_signals(ai_verdict: dict, technical_signal: str) -> str:
    """
    Объединяем сигнал от ИИ и сигнал от технического анализа (RSI, MA).

    Логика "двойного подтверждения":
    - Покупаем, только если ОБА говорят "вверх".
    - Продаём, только если ОБА говорят "вниз".
    - В остальных случаях — ничего не делаем (hold).

    Это сильно снижает количество ложных срабатываний.
    """
    ai_sentiment = ai_verdict.get("sentiment", "neutral")
    ai_confidence = ai_verdict.get("confidence", 0)

    # Если ИИ не уверен — игнорируем его, доверяем только технике.
    if ai_confidence < 60:
        ai_sentiment = "neutral"

    if technical_signal == "buy" and ai_sentiment in ("bullish", "neutral"):
        return "buy"
    if technical_signal == "sell" and ai_sentiment in ("bearish", "neutral"):
        return "sell"
    return "hold"
