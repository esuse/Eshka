"""
database.py — всё, что связано с хранением данных.

Мы используем SQLite — это «база данных в одном файле» (файл указан в config.DB_PATH).
Ничего отдельно устанавливать не нужно: SQLite встроен в Python.

Представь базу как набор табличек в тетрадке:
  • users         — кто вообще написал боту (Telegram-аккаунты)
  • subscriptions — подписка каждого клиента: до какого числа активна, лимит трафика, скорость, его VPN-ключ
  • payments      — журнал всех оплат (кто, сколько, подтверждено ли)
  • events        — лог важных событий (для отладки и истории)

Каждая функция ниже — это маленькое понятное действие с тетрадкой:
«добавь строку», «найди клиента», «продли подписку» и т.п.
"""

import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import config


# ---------- Подключение к базе ----------
@contextmanager
def get_conn():
    """
    Открываем соединение с базой и гарантированно закрываем его после работы.
    `row_factory = sqlite3.Row` позволяет обращаться к колонкам по имени: row["expires_at"].
    """
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    # Включаем поддержку внешних ключей (связей между таблицами).
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def now_ts() -> int:
    """Текущее время в виде числа (секунды с 1970 года). Удобно хранить и сравнивать."""
    return int(time.time())


def ts_to_str(ts: int | None) -> str:
    """Превратить число-время в читаемую дату вида '2026-05-30 14:00'."""
    if not ts:
        return "—"
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def init_db() -> None:
    """
    Создаём таблицы, если их ещё нет.
    Безопасно вызывать при каждом запуске — существующие данные не трогаются.
    """
    with get_conn() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username    TEXT,
                full_name   TEXT,
                created_at  INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS subscriptions (
                user_id          INTEGER PRIMARY KEY REFERENCES users(telegram_id),
                status           TEXT    NOT NULL DEFAULT 'inactive',  -- active | inactive | expired
                expires_at       INTEGER,                              -- до какого момента активна
                traffic_limit_mb INTEGER NOT NULL DEFAULT 0,           -- 0 = безлимит
                traffic_used_mb  INTEGER NOT NULL DEFAULT 0,
                speed_limit_mbit INTEGER NOT NULL DEFAULT 0,           -- 0 = без ограничения
                protocol_profile TEXT    NOT NULL DEFAULT 'all',       -- all | web | no_torrent
                wg_ip            TEXT,                                 -- адрес клиента в VPN, напр. 10.8.0.2
                wg_public_key    TEXT,
                wg_private_key   TEXT,
                client_config    TEXT,                                 -- готовый текст конфига для клиента
                created_at       INTEGER NOT NULL,
                updated_at       INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS payments (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id             INTEGER NOT NULL REFERENCES users(telegram_id),
                amount              INTEGER NOT NULL,        -- сумма в рублях
                method              TEXT    NOT NULL,        -- manual | yookassa | antarctic
                status              TEXT    NOT NULL DEFAULT 'pending', -- pending | confirmed | rejected
                plan_days           INTEGER NOT NULL,        -- сколько дней даёт эта оплата
                comment             TEXT,                    -- комментарий клиента (например, время перевода)
                provider_payment_id TEXT,                    -- id платежа во внешней системе (если есть)
                created_at          INTEGER NOT NULL,
                confirmed_at        INTEGER
            );

            CREATE TABLE IF NOT EXISTS events (
                id      INTEGER PRIMARY KEY AUTOINCREMENT,
                ts      INTEGER NOT NULL,
                level   TEXT    NOT NULL,    -- info | warning | error
                message TEXT    NOT NULL
            );
            """
        )


# ---------- Пользователи ----------
def upsert_user(telegram_id: int, username: str, full_name: str) -> None:
    """Добавить пользователя или обновить его имя, если он уже есть."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO users (telegram_id, username, full_name, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET
                username = excluded.username,
                full_name = excluded.full_name
            """,
            (telegram_id, username, full_name, now_ts()),
        )


def get_user(telegram_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        cur = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,))
        return cur.fetchone()


# ---------- Подписки ----------
def get_subscription(user_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        cur = conn.execute("SELECT * FROM subscriptions WHERE user_id = ?", (user_id,))
        return cur.fetchone()


def list_subscriptions() -> list[sqlite3.Row]:
    """Все подписки + имя/username владельца — для админ-панели."""
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT s.*, u.username, u.full_name
            FROM subscriptions s
            JOIN users u ON u.telegram_id = s.user_id
            ORDER BY s.updated_at DESC
            """
        )
        return cur.fetchall()


def used_ips() -> set[str]:
    """Список уже занятых VPN-адресов — чтобы новому клиенту выдать свободный."""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT wg_ip FROM subscriptions WHERE wg_ip IS NOT NULL AND wg_ip != ''"
        )
        return {row["wg_ip"] for row in cur.fetchall()}


def create_subscription(
    user_id: int,
    wg_ip: str,
    wg_public_key: str,
    wg_private_key: str,
    client_config: str,
    traffic_limit_mb: int,
    speed_limit_mbit: int,
    protocol_profile: str,
) -> None:
    """Создаём пустую (ещё не активную) подписку с уже сгенерированным ключом."""
    ts = now_ts()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO subscriptions (
                user_id, status, expires_at, traffic_limit_mb, traffic_used_mb,
                speed_limit_mbit, protocol_profile, wg_ip, wg_public_key,
                wg_private_key, client_config, created_at, updated_at
            ) VALUES (?, 'inactive', NULL, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO NOTHING
            """,
            (
                user_id, traffic_limit_mb, speed_limit_mbit, protocol_profile,
                wg_ip, wg_public_key, wg_private_key, client_config, ts, ts,
            ),
        )


def extend_subscription(user_id: int, days: int) -> int:
    """
    Продлить подписку на `days` дней.
    Если подписка ещё активна — добавляем дни к текущему сроку.
    Если уже истекла или новая — считаем от сегодня.
    Возвращаем новую дату окончания (число-время).
    """
    sub = get_subscription(user_id)
    base = now_ts()
    if sub and sub["expires_at"] and sub["expires_at"] > base:
        base = sub["expires_at"]
    new_expires = base + days * 86400  # 86400 секунд в сутках
    with get_conn() as conn:
        conn.execute(
            "UPDATE subscriptions SET status='active', expires_at=?, updated_at=? WHERE user_id=?",
            (new_expires, now_ts(), user_id),
        )
    return new_expires


def set_limits(
    user_id: int,
    traffic_limit_mb: int | None = None,
    speed_limit_mbit: int | None = None,
    protocol_profile: str | None = None,
) -> None:
    """Поменять лимиты подписки. Передавай только то, что хочешь изменить."""
    sub = get_subscription(user_id)
    if not sub:
        return
    new_traffic = traffic_limit_mb if traffic_limit_mb is not None else sub["traffic_limit_mb"]
    new_speed = speed_limit_mbit if speed_limit_mbit is not None else sub["speed_limit_mbit"]
    new_profile = protocol_profile if protocol_profile is not None else sub["protocol_profile"]
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE subscriptions
            SET traffic_limit_mb=?, speed_limit_mbit=?, protocol_profile=?, updated_at=?
            WHERE user_id=?
            """,
            (new_traffic, new_speed, new_profile, now_ts(), user_id),
        )


def add_traffic_used(user_id: int, mb: int) -> None:
    """Прибавить использованный трафик (на будущее — для учёта расхода)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE subscriptions SET traffic_used_mb = traffic_used_mb + ?, updated_at=? WHERE user_id=?",
            (mb, now_ts(), user_id),
        )


def set_status(user_id: int, status: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE subscriptions SET status=?, updated_at=? WHERE user_id=?",
            (status, now_ts(), user_id),
        )


def subscriptions_expiring_between(start_ts: int, end_ts: int) -> list[sqlite3.Row]:
    """Активные подписки, срок которых истекает в промежутке [start_ts, end_ts]."""
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT s.*, u.username, u.full_name
            FROM subscriptions s JOIN users u ON u.telegram_id = s.user_id
            WHERE s.status='active' AND s.expires_at BETWEEN ? AND ?
            """,
            (start_ts, end_ts),
        )
        return cur.fetchall()


def subscriptions_already_expired() -> list[sqlite3.Row]:
    """Активные подписки, у которых срок УЖЕ прошёл — их надо отключить."""
    with get_conn() as conn:
        cur = conn.execute(
            "SELECT * FROM subscriptions WHERE status='active' AND expires_at IS NOT NULL AND expires_at < ?",
            (now_ts(),),
        )
        return cur.fetchall()


# ---------- Платежи ----------
def create_payment(user_id: int, amount: int, method: str, plan_days: int, comment: str = "") -> int:
    """Записать новый платёж со статусом 'pending' (ожидает подтверждения). Вернуть его id."""
    with get_conn() as conn:
        cur = conn.execute(
            """
            INSERT INTO payments (user_id, amount, method, status, plan_days, comment, created_at)
            VALUES (?, ?, ?, 'pending', ?, ?, ?)
            """,
            (user_id, amount, method, plan_days, comment, now_ts()),
        )
        return cur.lastrowid


def get_payment(payment_id: int) -> sqlite3.Row | None:
    with get_conn() as conn:
        cur = conn.execute("SELECT * FROM payments WHERE id = ?", (payment_id,))
        return cur.fetchone()


def set_payment_status(payment_id: int, status: str, provider_payment_id: str = "") -> None:
    """Поменять статус платежа: confirmed (подтверждён) или rejected (отклонён)."""
    confirmed_at = now_ts() if status == "confirmed" else None
    with get_conn() as conn:
        conn.execute(
            "UPDATE payments SET status=?, confirmed_at=?, provider_payment_id=? WHERE id=?",
            (status, confirmed_at, provider_payment_id, payment_id),
        )


def list_pending_payments() -> list[sqlite3.Row]:
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT p.*, u.username, u.full_name
            FROM payments p JOIN users u ON u.telegram_id = p.user_id
            WHERE p.status='pending' ORDER BY p.created_at
            """
        )
        return cur.fetchall()


def list_payments(limit: int = 100) -> list[sqlite3.Row]:
    with get_conn() as conn:
        cur = conn.execute(
            """
            SELECT p.*, u.username, u.full_name
            FROM payments p JOIN users u ON u.telegram_id = p.user_id
            ORDER BY p.created_at DESC LIMIT ?
            """,
            (limit,),
        )
        return cur.fetchall()


# ---------- Лог событий ----------
def log_event(level: str, message: str) -> None:
    """Записать строчку в журнал событий (например, 'выдан ключ клиенту 123')."""
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO events (ts, level, message) VALUES (?, ?, ?)",
            (now_ts(), level, message),
        )


def list_events(limit: int = 100) -> list[sqlite3.Row]:
    with get_conn() as conn:
        cur = conn.execute("SELECT * FROM events ORDER BY ts DESC LIMIT ?", (limit,))
        return cur.fetchall()
