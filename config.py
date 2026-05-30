"""
config.py — одно место, где собраны ВСЕ настройки.

Идея простая: настройки лежат в файле .env (его ты заполняешь руками),
а здесь мы их читаем и превращаем в удобные переменные Python.
Везде в проекте пишем `import config` и берём, например, `config.TELEGRAM_BOT_TOKEN`.

Так нам не придётся хранить пароли и токены прямо в коде.
"""

import os

from dotenv import load_dotenv

# Загружаем переменные из файла .env в окружение программы.
load_dotenv()


def _get(name: str, default: str = "") -> str:
    """Прочитать строковую настройку из .env (или вернуть значение по умолчанию)."""
    return os.getenv(name, default).strip()


def _get_int(name: str, default: int = 0) -> int:
    """Прочитать число из .env."""
    raw = _get(name, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def _get_bool(name: str, default: bool = False) -> bool:
    """Прочитать «да/нет» из .env (true/1/yes считаем за «да»)."""
    return _get(name, "true" if default else "false").lower() in ("true", "1", "yes", "да")


# ---------- Телеграм ----------
TELEGRAM_BOT_TOKEN = _get("TELEGRAM_BOT_TOKEN")
# Список ID администраторов (числа). Из строки "1,2,3" делаем [1, 2, 3].
ADMIN_IDS = [int(x) for x in _get("ADMIN_IDS").replace(" ", "").split(",") if x]

# ---------- База данных ----------
DB_PATH = _get("DB_PATH", "vpn_service.db")


# ---------- Тарифы ----------
def _parse_plans(raw: str) -> list[dict]:
    """
    Превращаем строку "30:150,90:400" в удобный список словарей:
    [{"days": 30, "price": 150}, {"days": 90, "price": 400}].
    """
    plans = []
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        days_str, price_str = chunk.split(":", 1)
        try:
            plans.append({"days": int(days_str), "price": int(price_str)})
        except ValueError:
            continue
    return plans


PLANS = _parse_plans(_get("PLANS", "30:150,90:400,180:700"))

DEFAULT_TRAFFIC_LIMIT_GB = _get_int("DEFAULT_TRAFFIC_LIMIT_GB", 100)
DEFAULT_SPEED_LIMIT_MBIT = _get_int("DEFAULT_SPEED_LIMIT_MBIT", 50)
DEFAULT_PROTOCOL_PROFILE = _get("DEFAULT_PROTOCOL_PROFILE", "all")

# ---------- Оплата ----------
PAYMENT_PROVIDER = _get("PAYMENT_PROVIDER", "manual").lower()
SBP_PHONE = _get("SBP_PHONE")
SBP_RECEIVER = _get("SBP_RECEIVER")
ANTARCTIC_WALLET = _get("ANTARCTIC_WALLET")
YOOKASSA_SHOP_ID = _get("YOOKASSA_SHOP_ID")
YOOKASSA_SECRET_KEY = _get("YOOKASSA_SECRET_KEY")

# ---------- VPN-сервер ----------
WG_INTERFACE = _get("WG_INTERFACE", "wg0")
WG_CMD = _get("WG_CMD", "wg")
WG_CONFIG_PATH = _get("WG_CONFIG_PATH", "/etc/wireguard/wg0.conf")
WG_SERVER_PUBLIC_KEY = _get("WG_SERVER_PUBLIC_KEY")
WG_SERVER_ENDPOINT = _get("WG_SERVER_ENDPOINT", "1.2.3.4:51820")
WG_SUBNET = _get("WG_SUBNET", "10.8.0.0/24")
WG_DNS = _get("WG_DNS", "1.1.1.1")

# ---------- Ограничение трафика ----------
WAN_INTERFACE = _get("WAN_INTERFACE", "eth0")
APPLY_TRAFFIC_RULES = _get_bool("APPLY_TRAFFIC_RULES", False)

# ---------- Веб-панель ----------
WEB_PANEL_USER = _get("WEB_PANEL_USER", "admin")
WEB_PANEL_PASSWORD = _get("WEB_PANEL_PASSWORD", "admin")
WEB_PANEL_PORT = _get_int("WEB_PANEL_PORT", 8080)


def is_admin(telegram_id: int) -> bool:
    """Проверка: этот пользователь — администратор?"""
    return telegram_id in ADMIN_IDS


def check_required() -> list[str]:
    """
    Проверяем, что заполнены самые важные настройки.
    Возвращаем список проблем (пустой список = всё хорошо).
    Вызывается при старте, чтобы сразу понятно сказать, чего не хватает.
    """
    problems = []
    if not TELEGRAM_BOT_TOKEN:
        problems.append("Не заполнен TELEGRAM_BOT_TOKEN в .env")
    if not ADMIN_IDS:
        problems.append("Не заполнен ADMIN_IDS в .env (твой Telegram ID)")
    if not PLANS:
        problems.append("Не заданы тарифы PLANS в .env")
    return problems
