"""
notifier.py — автоматические напоминания и отключение по истечении срока.

Работает в фоне рядом с ботом (через планировщик APScheduler):
  • раз в день предупреждаем клиента, что подписка заканчивается завтра;
  • раз в полчаса проверяем, у кого срок УЖЕ прошёл — отключаем такого клиента
    на сервере (убираем его peer) и помечаем подписку как 'expired'.

Так тебе не нужно ничего отслеживать вручную — система сама напомнит и сама отключит.
"""

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

import config
import database as db
import traffic_control as tc
import vpn_manager

log = logging.getLogger("notifier")


async def remind_expiring(bot) -> None:
    """Предупредить клиентов, чья подписка истекает в ближайшие 24 часа."""
    start = db.now_ts()
    end = start + 86400  # +24 часа
    for sub in db.subscriptions_expiring_between(start, end):
        try:
            await bot.send_message(
                sub["user_id"],
                "⏳ Напоминание: твоя VPN-подписка заканчивается "
                f"{db.ts_to_str(sub['expires_at'])}.\n"
                "Чтобы не потерять доступ, продли её командой /start → «Купить / продлить».",
            )
            db.log_event("info", f"Напоминание об истечении отправлено клиенту {sub['user_id']}")
        except Exception as exc:  # noqa: BLE001
            log.warning("Не смог напомнить клиенту %s: %s", sub["user_id"], exc)


async def disable_expired(bot) -> None:
    """Отключить клиентов с истёкшей подпиской: убрать peer и пометить 'expired'."""
    for sub in db.subscriptions_already_expired():
        user_id = sub["user_id"]
        if sub["wg_public_key"]:
            line = vpn_manager.remove_peer(sub["wg_public_key"], apply=config.APPLY_TRAFFIC_RULES)
            log.info("Отключение клиента %s: %s", user_id, line)
            # Снимаем и ограничитель скорости (передаём 0 = убрать лимит/класс).
            if sub["wg_ip"]:
                tc.set_speed_limit(sub["wg_ip"], 0)
        db.set_status(user_id, "expired")
        db.log_event("info", f"Подписка клиента {user_id} истекла — доступ отключён")
        try:
            await bot.send_message(
                user_id,
                "⛔️ Срок подписки закончился, доступ к VPN отключён.\n"
                "Продли подписку командой /start, и доступ снова включится автоматически.",
            )
        except Exception:  # noqa: BLE001
            pass


def start_scheduler(bot) -> AsyncIOScheduler:
    """
    Запустить фоновые задачи. Вызывается из bot.py после старта.
    Использует тот же событийный цикл, что и бот.
    """
    scheduler = AsyncIOScheduler(timezone="UTC")
    # Каждый день в 12:00 UTC — напоминания об истечении.
    scheduler.add_job(remind_expiring, "cron", hour=12, minute=0, args=[bot])
    # Каждые 30 минут — отключение тех, у кого срок уже прошёл.
    scheduler.add_job(disable_expired, "interval", minutes=30, args=[bot])
    scheduler.start()
    log.info("Планировщик напоминаний запущен.")
    return scheduler
