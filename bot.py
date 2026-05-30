"""
bot.py — Telegram-бот: общается с клиентами и с тобой (админом).

Запуск:   python bot.py

Что умеет КЛИЕНТ:
  /start            — открыть меню
  Выбрать тариф     — бот показывает реквизиты для оплаты и кнопку «Я оплатил»
  «Я оплатил»       — заявка уходит админу на подтверждение
  /status           — посмотреть свою подписку
  /mykey            — получить (ещё раз) свой VPN-ключ

Что умеет АДМИН (только из ADMIN_IDS):
  Подтвердить/Отклонить оплату — кнопками под уведомлением о платеже
  /pending          — список оплат, ждущих подтверждения
  /users            — список всех подписок
  /extend ID ДНЕЙ   — продлить подписку вручную
  /limit ID ГБ МБИТ ПРОФИЛЬ — задать лимиты (профиль: all|web|no_torrent)

Главная «магия»: как только оплата подтверждена (вручную тобой или автоматически
платёжной системой), бот сам генерирует ключ, регистрирует клиента на сервере,
включает лимит скорости и присылает клиенту готовый конфиг. Руками ничего настраивать не надо.
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
import database as db
import payments
import traffic_control as tc
import vpn_manager
from notifier import start_scheduler

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("bot")

router = Router()


# ============================================================
#                  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================================
def main_menu() -> InlineKeyboardMarkup:
    """Кнопки главного меню для клиента."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="🛒 Купить / продлить", callback_data="buy")],
            [InlineKeyboardButton(text="📊 Моя подписка", callback_data="status")],
            [InlineKeyboardButton(text="🔑 Мой ключ", callback_data="mykey")],
        ]
    )


def plans_keyboard() -> InlineKeyboardMarkup:
    """Кнопки с тарифами (берутся из .env)."""
    rows = []
    for plan in config.PLANS:
        text = f"{plan['days']} дней — {plan['price']} ₽"
        rows.append(
            [InlineKeyboardButton(text=text, callback_data=f"plan:{plan['days']}:{plan['price']}")]
        )
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def status_text(user_id: int) -> str:
    """Человеческое описание подписки клиента."""
    sub = db.get_subscription(user_id)
    if not sub or sub["status"] != "active":
        return "У тебя пока нет активной подписки. Нажми «🛒 Купить / продлить»."

    limit_gb = sub["traffic_limit_mb"] / 1024 if sub["traffic_limit_mb"] else 0
    used_gb = sub["traffic_used_mb"] / 1024
    speed = f"{sub['speed_limit_mbit']} Мбит/с" if sub["speed_limit_mbit"] else "без ограничения"
    traffic = f"{used_gb:.1f} / {limit_gb:.0f} ГБ" if limit_gb else f"{used_gb:.1f} ГБ (безлимит)"
    return (
        "📊 <b>Твоя подписка</b>\n\n"
        f"Статус: активна ✅\n"
        f"Действует до: {db.ts_to_str(sub['expires_at'])}\n"
        f"Трафик: {traffic}\n"
        f"Скорость: {speed}\n"
        f"Профиль протоколов: {sub['protocol_profile']}\n"
        f"Твой адрес в VPN: {sub['wg_ip']}"
    )


async def send_key_to_user(bot: Bot, user_id: int) -> None:
    """Отправить клиенту его конфиг текстом и отдельным файлом .conf."""
    sub = db.get_subscription(user_id)
    if not sub or not sub["client_config"]:
        await bot.send_message(user_id, "Ключ ещё не сгенерирован. Сначала оформи подписку.")
        return

    await bot.send_message(
        user_id,
        "🔑 Твой VPN-ключ готов!\n\n"
        "1) Установи приложение Amnezia VPN (или WireGuard).\n"
        "2) Импортируй файл ниже (или скопируй текст конфига).\n"
        "3) Нажми «Подключиться».\n\n"
        f"<pre>{sub['client_config']}</pre>",
    )
    file_bytes = sub["client_config"].encode("utf-8")
    await bot.send_document(
        user_id,
        BufferedInputFile(file_bytes, filename="vpn.conf"),
        caption="Файл конфига — можно импортировать в приложение.",
    )


async def grant_access(bot: Bot, payment_id: int) -> None:
    """
    САМОЕ ГЛАВНОЕ: выдать доступ после подтверждения оплаты.
    Вызывается и при ручном подтверждении (админ нажал кнопку),
    и при автоматическом (платёжная система сообщила об оплате).

    Шаги:
      1) помечаем платёж как 'confirmed';
      2) генерируем ключ и регистрируем клиента на сервере (если ещё нет);
      3) продлеваем подписку на купленное число дней;
      4) включаем лимит скорости для этого клиента;
      5) отправляем клиенту ключ и радостное сообщение.
    """
    payment = db.get_payment(payment_id)
    if not payment or payment["status"] == "confirmed":
        return

    user_id = payment["user_id"]
    db.set_payment_status(payment_id, "confirmed")

    # 2) ключ (создастся один раз; при продлении переиспользуется тот же)
    vpn_manager.issue_key_for_user(
        user_id=user_id,
        traffic_limit_mb=config.DEFAULT_TRAFFIC_LIMIT_GB * 1024,
        speed_limit_mbit=config.DEFAULT_SPEED_LIMIT_MBIT,
        protocol_profile=config.DEFAULT_PROTOCOL_PROFILE,
    )

    # 3) продлеваем подписку
    new_expires = db.extend_subscription(user_id, payment["plan_days"])

    # 4) включаем ограничение скорости
    sub = db.get_subscription(user_id)
    if sub and sub["wg_ip"]:
        for line in tc.apply_all_for_subscription(sub["wg_ip"], sub["speed_limit_mbit"]):
            log.info("tc: %s", line)

    db.log_event("info", f"Оплата #{payment_id} подтверждена, доступ выдан клиенту {user_id}")

    # 5) сообщаем клиенту и отправляем ключ
    await bot.send_message(
        user_id,
        f"✅ Оплата получена! Подписка активна до {db.ts_to_str(new_expires)}.",
    )
    await send_key_to_user(bot, user_id)


def admin_payment_keyboard(payment_id: int) -> InlineKeyboardMarkup:
    """Кнопки «Подтвердить/Отклонить» под уведомлением админу."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm:{payment_id}"),
                InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{payment_id}"),
            ]
        ]
    )


# ============================================================
#                     КОМАНДЫ КЛИЕНТА
# ============================================================
@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    db.upsert_user(
        message.from_user.id,
        message.from_user.username or "",
        message.from_user.full_name or "",
    )
    await message.answer(
        "👋 Привет! Это бот VPN-сервиса.\n\n"
        "Здесь можно купить или продлить подписку и получить ключ для подключения.\n"
        "Выбери действие:",
        reply_markup=main_menu(),
    )


@router.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery) -> None:
    await call.message.edit_text("Выбери действие:", reply_markup=main_menu())
    await call.answer()


@router.callback_query(F.data == "buy")
async def cb_buy(call: CallbackQuery) -> None:
    await call.message.edit_text("Выбери тариф:", reply_markup=plans_keyboard())
    await call.answer()


@router.callback_query(F.data == "status")
async def cb_status(call: CallbackQuery) -> None:
    await call.message.edit_text(status_text(call.from_user.id), reply_markup=main_menu())
    await call.answer()


@router.callback_query(F.data == "mykey")
async def cb_mykey(call: CallbackQuery) -> None:
    await call.answer()
    await send_key_to_user(call.bot, call.from_user.id)


@router.message(Command("status"))
async def cmd_status(message: Message) -> None:
    await message.answer(status_text(message.from_user.id))


@router.message(Command("mykey"))
async def cmd_mykey(message: Message) -> None:
    await send_key_to_user(message.bot, message.from_user.id)


@router.callback_query(F.data.startswith("plan:"))
async def cb_choose_plan(call: CallbackQuery) -> None:
    """Клиент выбрал тариф — создаём платёж и показываем инструкцию по оплате."""
    _, days_str, price_str = call.data.split(":")
    days, price = int(days_str), int(price_str)
    user_id = call.from_user.id

    db.upsert_user(user_id, call.from_user.username or "", call.from_user.full_name or "")

    # Авто-режим (ЮKassa): даём ссылку на оплату.
    if payments.is_auto_provider():
        result = payments.create_yookassa_payment(price, days, user_id)
        if result["ok"]:
            payment_id = db.create_payment(user_id, price, "yookassa", days)
            db.set_payment_status(payment_id, "pending", result["payment_id"])
            kb = InlineKeyboardMarkup(
                inline_keyboard=[[InlineKeyboardButton(text="💳 Оплатить", url=result["url"])]]
            )
            await call.message.edit_text(
                f"Счёт на {price} ₽ за {days} дней создан.\n"
                "Нажми кнопку ниже, оплати, и доступ придёт автоматически.",
                reply_markup=kb,
            )
            await call.answer()
            return
        # если авто не сработал — спокойно откатываемся в ручной режим
        await call.message.answer(f"Авто-оплата недоступна ({result['error']}). Перейдём в ручной режим.")

    # Ручной режим: создаём платёж 'pending' и показываем реквизиты.
    payment_id = db.create_payment(user_id, price, config.PAYMENT_PROVIDER, days)
    text = payments.payment_instructions(price, days)
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="✅ Я оплатил", callback_data=f"paid:{payment_id}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="menu")],
        ]
    )
    await call.message.edit_text(text, reply_markup=kb)
    await call.answer()


@router.callback_query(F.data.startswith("paid:"))
async def cb_paid(call: CallbackQuery) -> None:
    """Клиент нажал «Я оплатил» — уведомляем всех админов с кнопками подтверждения."""
    payment_id = int(call.data.split(":")[1])
    payment = db.get_payment(payment_id)
    if not payment:
        await call.answer("Платёж не найден", show_alert=True)
        return

    await call.message.edit_text(
        "Спасибо! Заявка отправлена администратору. "
        "Как только оплату подтвердят, ключ придёт сюда автоматически. ⏳"
    )
    await call.answer()

    who = call.from_user.username or call.from_user.full_name or str(call.from_user.id)
    note = (
        f"💰 Новая оплата на подтверждение\n\n"
        f"Платёж #{payment_id}\n"
        f"Клиент: {who} (ID {payment['user_id']})\n"
        f"Сумма: {payment['amount']} ₽ за {payment['plan_days']} дней\n\n"
        "Проверь поступление в банке и подтверди:"
    )
    for admin_id in config.ADMIN_IDS:
        try:
            await call.bot.send_message(admin_id, note, reply_markup=admin_payment_keyboard(payment_id))
        except Exception as exc:  # noqa: BLE001
            log.warning("Не смог уведомить админа %s: %s", admin_id, exc)


# ============================================================
#                  ПОДТВЕРЖДЕНИЕ ОПЛАТЫ (АДМИН)
# ============================================================
@router.callback_query(F.data.startswith("confirm:"))
async def cb_confirm(call: CallbackQuery) -> None:
    if not config.is_admin(call.from_user.id):
        await call.answer("Только для администратора", show_alert=True)
        return
    payment_id = int(call.data.split(":")[1])
    await grant_access(call.bot, payment_id)
    await call.message.edit_text(f"✅ Платёж #{payment_id} подтверждён, ключ выдан клиенту.")
    await call.answer()


@router.callback_query(F.data.startswith("reject:"))
async def cb_reject(call: CallbackQuery) -> None:
    if not config.is_admin(call.from_user.id):
        await call.answer("Только для администратора", show_alert=True)
        return
    payment_id = int(call.data.split(":")[1])
    payment = db.get_payment(payment_id)
    db.set_payment_status(payment_id, "rejected")
    await call.message.edit_text(f"❌ Платёж #{payment_id} отклонён.")
    await call.answer()
    if payment:
        try:
            await call.bot.send_message(
                payment["user_id"],
                "К сожалению, оплату не удалось подтвердить. "
                "Проверь, что перевод дошёл, и напиши администратору.",
            )
        except Exception:  # noqa: BLE001
            pass


# ============================================================
#                    АДМИН-КОМАНДЫ
# ============================================================
@router.message(Command("pending"))
async def cmd_pending(message: Message) -> None:
    if not config.is_admin(message.from_user.id):
        return
    rows = db.list_pending_payments()
    if not rows:
        await message.answer("Платежей в ожидании нет.")
        return
    for p in rows:
        who = p["username"] or p["full_name"] or str(p["user_id"])
        await message.answer(
            f"Платёж #{p['id']}: {who} (ID {p['user_id']}) — {p['amount']} ₽ за {p['plan_days']} дней",
            reply_markup=admin_payment_keyboard(p["id"]),
        )


@router.message(Command("users"))
async def cmd_users(message: Message) -> None:
    if not config.is_admin(message.from_user.id):
        return
    rows = db.list_subscriptions()
    if not rows:
        await message.answer("Подписок пока нет.")
        return
    lines = ["<b>Подписки:</b>"]
    for s in rows:
        who = s["username"] or s["full_name"] or str(s["user_id"])
        lines.append(
            f"• {who} (ID {s['user_id']}) — {s['status']}, "
            f"до {db.ts_to_str(s['expires_at'])}, "
            f"{s['speed_limit_mbit']} Мбит/с, профиль {s['protocol_profile']}"
        )
    await message.answer("\n".join(lines))


@router.message(Command("extend"))
async def cmd_extend(message: Message) -> None:
    """Продлить вручную:  /extend <user_id> <дней>"""
    if not config.is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Использование: /extend <user_id> <дней>")
        return
    try:
        user_id, days = int(parts[1]), int(parts[2])
    except ValueError:
        await message.answer("user_id и дни должны быть числами.")
        return
    # На случай если ключа ещё не было — создадим его.
    vpn_manager.issue_key_for_user(
        user_id,
        config.DEFAULT_TRAFFIC_LIMIT_GB * 1024,
        config.DEFAULT_SPEED_LIMIT_MBIT,
        config.DEFAULT_PROTOCOL_PROFILE,
    )
    new_expires = db.extend_subscription(user_id, days)
    await message.answer(f"Готово. Подписка клиента {user_id} активна до {db.ts_to_str(new_expires)}.")
    try:
        await message.bot.send_message(user_id, f"Тебе продлили подписку до {db.ts_to_str(new_expires)} ✅")
        await send_key_to_user(message.bot, user_id)
    except Exception:  # noqa: BLE001
        pass


@router.message(Command("limit"))
async def cmd_limit(message: Message) -> None:
    """Задать лимиты:  /limit <user_id> <трафик_ГБ> <скорость_Мбит> <профиль>"""
    if not config.is_admin(message.from_user.id):
        return
    parts = message.text.split()
    if len(parts) != 5:
        await message.answer(
            "Использование: /limit <user_id> <трафик_ГБ> <скорость_Мбит> <профиль>\n"
            "Профиль: all | web | no_torrent\n"
            "Пример: /limit 123456789 100 50 all"
        )
        return
    try:
        user_id = int(parts[1])
        traffic_gb = int(parts[2])
        speed = int(parts[3])
    except ValueError:
        await message.answer("user_id, трафик и скорость должны быть числами.")
        return
    profile = parts[4]
    if profile not in tc.PROTOCOL_PROFILES:
        await message.answer(f"Профиль должен быть одним из: {', '.join(tc.PROTOCOL_PROFILES)}")
        return

    db.set_limits(user_id, traffic_gb * 1024, speed, profile)
    sub = db.get_subscription(user_id)
    if sub and sub["wg_ip"]:
        for line in tc.set_speed_limit(sub["wg_ip"], speed):
            log.info("tc: %s", line)
    await message.answer(
        f"Лимиты клиента {user_id} обновлены: {traffic_gb} ГБ, {speed} Мбит/с, профиль {profile}."
    )


# ============================================================
#                         ЗАПУСК
# ============================================================
async def main() -> None:
    problems = config.check_required()
    if problems:
        print("⚠️  Не хватает настроек в .env:")
        for p in problems:
            print("   -", p)
        print("Заполни .env (см. .env.example) и запусти снова.")
        return

    db.init_db()
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN, default=_default_props())
    dp = Dispatcher()
    dp.include_router(router)

    # Запускаем фоновый планировщик напоминаний об истечении подписок.
    start_scheduler(bot)

    log.info("Бот запущен. Жду сообщений…")
    await dp.start_polling(bot)


def _default_props():
    """Включаем HTML-разметку в сообщениях по умолчанию (для <b>, <pre> и т.п.)."""
    from aiogram.client.default import DefaultBotProperties

    return DefaultBotProperties(parse_mode="HTML")


if __name__ == "__main__":
    asyncio.run(main())
