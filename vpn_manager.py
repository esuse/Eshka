"""
vpn_manager.py — «выдача ключей» для WireGuard / AmneziaWG.

Что такое ключ простыми словами:
  • У каждого клиента есть пара ключей: приватный (секретный, остаётся у клиента)
    и публичный (его мы говорим серверу).
  • Сервер тоже имеет свой публичный ключ — его клиент прописывает у себя.
  • Плюс клиенту выдаётся внутренний адрес в VPN, например 10.8.0.5.
  • Из всего этого собирается текстовый «конфиг», который клиент открывает
    в приложении Amnezia/WireGuard (можно как QR-код).

Этот модуль умеет:
  1) сгенерировать пару ключей (без установленной утилиты wg — считаем сами);
  2) выбрать свободный адрес в VPN-подсети;
  3) собрать текст клиентского конфига;
  4) добавить/убрать клиента (peer) на сервере.

Важно: пункт 4 выполняет системные команды (`wg set ...`) и требует прав root.
Если запускаешь НЕ на сервере или просто тестируешь — он только покажет команды,
а реально их выполнит лишь когда ты сам так решишь (см. apply=True).
"""

import base64
import ipaddress
import subprocess

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

import config
import database as db


def generate_keypair() -> tuple[str, str]:
    """
    Создать пару ключей WireGuard.
    Возвращаем (приватный_ключ, публичный_ключ) — обе строки в base64,
    точно в том же формате, что выдаёт команда `wg genkey` / `wg pubkey`.
    """
    private = X25519PrivateKey.generate()

    private_raw = private.private_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PrivateFormat.Raw,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_raw = private.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )

    private_b64 = base64.b64encode(private_raw).decode()
    public_b64 = base64.b64encode(public_raw).decode()
    return private_b64, public_b64


def pick_free_ip() -> str:
    """
    Выбрать первый свободный адрес из VPN-подсети.
    Адрес .1 обычно занят самим сервером, поэтому начинаем с .2.
    """
    network = ipaddress.ip_network(config.WG_SUBNET, strict=False)
    taken = db.used_ips()
    # hosts() перечисляет все адреса для клиентов (без адреса сети и broadcast).
    for host in network.hosts():
        ip = str(host)
        # пропускаем .1 (сервер) и уже занятые адреса
        if ip.endswith(".1"):
            continue
        if ip not in taken:
            return ip
    raise RuntimeError("Свободные адреса в подсети закончились — увеличь WG_SUBNET в .env")


def build_client_config(private_key: str, client_ip: str) -> str:
    """
    Собрать текстовый конфиг, который клиент откроет в приложении WireGuard/Amnezia.
    [Interface] — про клиента, [Peer] — про наш сервер.
    """
    prefix_len = ipaddress.ip_network(config.WG_SUBNET, strict=False).prefixlen
    return (
        "[Interface]\n"
        f"PrivateKey = {private_key}\n"
        f"Address = {client_ip}/{prefix_len}\n"
        f"DNS = {config.WG_DNS}\n"
        "\n"
        "[Peer]\n"
        f"PublicKey = {config.WG_SERVER_PUBLIC_KEY}\n"
        f"Endpoint = {config.WG_SERVER_ENDPOINT}\n"
        "AllowedIPs = 0.0.0.0/0, ::/0\n"
        "PersistentKeepalive = 25\n"
    )


def issue_key_for_user(
    user_id: int,
    traffic_limit_mb: int,
    speed_limit_mbit: int,
    protocol_profile: str,
) -> dict:
    """
    Главная функция «выдать ключ новому клиенту».
    Генерируем ключи, выбираем IP, собираем конфиг, сохраняем в базу и
    регистрируем клиента (peer) на сервере. Возвращаем краткую сводку.

    Если у клиента уже есть подписка с ключом — повторно не создаём,
    просто возвращаем существующие данные (ключ выдаётся один раз).
    """
    existing = db.get_subscription(user_id)
    if existing and existing["wg_public_key"]:
        return {
            "client_ip": existing["wg_ip"],
            "public_key": existing["wg_public_key"],
            "client_config": existing["client_config"],
            "reused": True,
        }

    private_key, public_key = generate_keypair()
    client_ip = pick_free_ip()
    client_config = build_client_config(private_key, client_ip)

    db.create_subscription(
        user_id=user_id,
        wg_ip=client_ip,
        wg_public_key=public_key,
        wg_private_key=private_key,
        client_config=client_config,
        traffic_limit_mb=traffic_limit_mb,
        speed_limit_mbit=speed_limit_mbit,
        protocol_profile=protocol_profile,
    )

    add_peer(public_key, client_ip, apply=config.APPLY_TRAFFIC_RULES)
    db.log_event("info", f"Выдан VPN-ключ пользователю {user_id} (адрес {client_ip})")

    return {
        "client_ip": client_ip,
        "public_key": public_key,
        "client_config": client_config,
        "reused": False,
    }


def _run(cmd: list[str], apply: bool) -> str:
    """
    Выполнить системную команду — но только если apply=True.
    Иначе вернуть строку «команда, которую МЫ БЫ выполнили» (безопасный режим).
    Так можно всё протестировать, не имея root и не находясь на сервере.
    """
    printable = " ".join(cmd)
    if not apply:
        return f"[режим показа] {printable}"
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        return f"[выполнено] {printable}"
    except FileNotFoundError:
        return f"[ошибка] команда не найдена: {cmd[0]}"
    except subprocess.CalledProcessError as exc:
        return f"[ошибка] {printable}\n{exc.stderr}"


def add_peer(public_key: str, client_ip: str, apply: bool = False) -> str:
    """
    Зарегистрировать клиента на сервере: разрешить его публичный ключ и адрес.
    Это эквивалент строчки [Peer] в конфиге сервера.
    Команда: wg set <интерфейс> peer <ключ> allowed-ips <ip>/32
    (для AmneziaWG в .env поставь WG_CMD=awg)
    """
    cmd = [
        config.WG_CMD, "set", config.WG_INTERFACE,
        "peer", public_key,
        "allowed-ips", f"{client_ip}/32",
    ]
    return _run(cmd, apply)


def remove_peer(public_key: str, apply: bool = False) -> str:
    """Убрать клиента с сервера (например, когда подписка закончилась)."""
    cmd = [config.WG_CMD, "set", config.WG_INTERFACE, "peer", public_key, "remove"]
    return _run(cmd, apply)
