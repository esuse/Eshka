"""
payments.py — приём оплаты.

Здесь спрятана вся «денежная» логика, чтобы остальной код про неё не думал.
Способ оплаты выбирается в .env через PAYMENT_PROVIDER:

  • manual    — РУЧНОЙ режим (рекомендуется для старта).
       Клиент переводит тебе деньги (по номеру телефона через СБП или на кошелёк),
       нажимает в боте «Я оплатил». Тебе приходит уведомление, ты проверяешь
       поступление в своём банке и жмёшь «Подтвердить» — ключ выдаётся автоматически.
       Это работает сразу, без договоров и подключений.

  • yookassa  — АВТОМАТический приём СБП через сервис ЮKassa.
       Тут оплату подтверждает не человек, а сама платёжная система.
       Нужен договор с ЮKassa и ключи (YOOKASSA_SHOP_ID / YOOKASSA_SECRET_KEY).
       Ниже — рабочая заготовка: если ключи заданы, создаём счёт и даём ссылку.

  • antarctic — кошелёк Antarctic Wallet.
       Публичного API для приёма платежей у него мне найти не удалось,
       поэтому это ЗАГЛУШКА: показываем реквизиты и работаем как ручной режим.
       Когда/если у Antarctic появится API — дописывается здесь же.

Главная идея: какой бы способ ни был, итог один — платёж получает статус
'confirmed', и бот автоматически выдаёт/продлевает ключ.
"""

import config


def payment_instructions(amount: int, plan_days: int) -> str:
    """
    Текст с инструкцией для клиента: куда и сколько переводить.
    Зависит от выбранного способа оплаты.
    """
    if config.PAYMENT_PROVIDER == "antarctic":
        wallet = config.ANTARCTIC_WALLET or "(адрес кошелька не задан в .env)"
        return (
            f"💳 К оплате: <b>{amount} ₽</b> за {plan_days} дней\n\n"
            f"Переведите на кошелёк Antarctic Wallet:\n<code>{wallet}</code>\n\n"
            "После перевода нажмите кнопку «✅ Я оплатил» ниже."
        )

    # manual (и временно antarctic) — показываем реквизиты СБП.
    phone = config.SBP_PHONE or "(номер не задан в .env)"
    receiver = config.SBP_RECEIVER or ""
    text = (
        f"💳 К оплате: <b>{amount} ₽</b> за {plan_days} дней\n\n"
        f"Переведите по СБП на номер:\n<code>{phone}</code>\n"
    )
    if receiver:
        text += f"Получатель: {receiver}\n"
    if config.ANTARCTIC_WALLET:
        text += f"\nИли на Antarctic Wallet:\n<code>{config.ANTARCTIC_WALLET}</code>\n"
    text += (
        "\nВ комментарии к переводу можно ничего не писать.\n"
        "После перевода нажмите «✅ Я оплатил» — администратор подтвердит, "
        "и ключ придёт автоматически."
    )
    return text


def is_auto_provider() -> bool:
    """Авто-режим (подтверждает платёжная система) или ручной (подтверждает админ)?"""
    return config.PAYMENT_PROVIDER == "yookassa"


def create_yookassa_payment(amount: int, plan_days: int, user_id: int) -> dict:
    """
    Заготовка под ЮKassa. Создаёт счёт и возвращает ссылку на оплату.
    Возвращаем словарь: {"ok": bool, "url": str, "payment_id": str, "error": str}.

    Чтобы заработало:
      1) Зарегистрируйся в ЮKassa, получи Shop ID и секретный ключ.
      2) Впиши их в .env (YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY).
      3) Поставь PAYMENT_PROVIDER=yookassa.
    """
    if not (config.YOOKASSA_SHOP_ID and config.YOOKASSA_SECRET_KEY):
        return {
            "ok": False,
            "url": "",
            "payment_id": "",
            "error": "Не заданы ключи ЮKassa в .env — используй ручной режим (manual).",
        }

    try:
        # Импортируем библиотеку только здесь, чтобы она не требовалась в ручном режиме.
        from yookassa import Configuration, Payment

        Configuration.account_id = config.YOOKASSA_SHOP_ID
        Configuration.secret_key = config.YOOKASSA_SECRET_KEY

        payment = Payment.create(
            {
                "amount": {"value": f"{amount}.00", "currency": "RUB"},
                # Способ оплаты "sbp" — оплата через Систему быстрых платежей.
                "payment_method_data": {"type": "sbp"},
                "confirmation": {"type": "redirect", "return_url": "https://t.me"},
                "capture": True,
                "description": f"VPN-подписка на {plan_days} дней (клиент {user_id})",
                "metadata": {"user_id": str(user_id), "plan_days": str(plan_days)},
            }
        )
        return {
            "ok": True,
            "url": payment.confirmation.confirmation_url,
            "payment_id": payment.id,
            "error": "",
        }
    except Exception as exc:  # noqa: BLE001 — для новичка важно увидеть текст ошибки
        return {"ok": False, "url": "", "payment_id": "", "error": str(exc)}


def check_yookassa_payment(payment_id: str) -> bool:
    """Проверить у ЮKassa, оплачен ли счёт. True — деньги поступили."""
    if not (config.YOOKASSA_SHOP_ID and config.YOOKASSA_SECRET_KEY):
        return False
    try:
        from yookassa import Configuration, Payment

        Configuration.account_id = config.YOOKASSA_SHOP_ID
        Configuration.secret_key = config.YOOKASSA_SECRET_KEY
        payment = Payment.find_one(payment_id)
        return payment.status == "succeeded"
    except Exception:  # noqa: BLE001
        return False
