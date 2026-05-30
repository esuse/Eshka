"""
traffic_control.py — ограничение СКОРОСТИ и контроль ПРОТОКОЛОВ по каждому клиенту.

В Linux это делают две встроенные системы:
  • tc (traffic control) — «сужает трубу», то есть ограничивает скорость.
  • nftables — «фильтр»: решает, какие соединения пропускать, а какие блокировать
    (через это мы и управляем протоколами — например, запрещаем торренты).

Как мы привязываем правила к конкретному клиенту:
  у каждого клиента свой адрес в VPN (например 10.8.0.5). По этому адресу
  мы и узнаём, чей это трафик, и применяем именно его лимит/фильтр.

ВАЖНО про режимы:
  Эти команды меняют сеть сервера и требуют прав root. Поэтому по умолчанию
  (APPLY_TRAFFIC_RULES=false) мы НИЧЕГО не выполняем, а только показываем,
  какие команды были бы выполнены. Это безопасно: можно всё посмотреть глазами,
  проверить на сервере вручную, и только потом включить APPLY_TRAFFIC_RULES=true.

Замечание честно: ограничение скорости здесь сделано на СКАЧИВАНИЕ (download) —
это самое важное и заметное для клиента. Ограничение отдачи (upload) требует
дополнительной настройки (виртуальный интерфейс IFB) — об этом написано в README.
"""

import subprocess

import config

# Типичные порты торрентов — их блокирует профиль no_torrent.
TORRENT_PORTS = "6881-6889,6969,51413"

# Профили протоколов: что разрешаем клиенту.
#   all        — без ограничений
#   web        — только веб (80/443) и DNS (53); остальное блокируем
#   no_torrent — всё, кроме торрент-портов
PROTOCOL_PROFILES = ("all", "web", "no_torrent")


def _class_id(client_ip: str) -> str:
    """
    Для tc каждому клиенту нужен числовой «номер класса».
    Берём последний кусочек его адреса: 10.8.0.5 -> 5 -> класс 1:5.
    Так у каждого клиента свой класс, и лимиты не мешают друг другу.
    """
    last_octet = client_ip.strip().split(".")[-1]
    return f"1:{last_octet}"


def _run(cmd: str) -> str:
    """
    Выполнить shell-команду, если APPLY_TRAFFIC_RULES=true.
    Иначе просто вернуть её текст (безопасный режим показа).
    """
    if not config.APPLY_TRAFFIC_RULES:
        return f"[режим показа] {cmd}"
    try:
        subprocess.run(cmd, shell=True, check=True, capture_output=True, text=True)
        return f"[выполнено] {cmd}"
    except subprocess.CalledProcessError as exc:
        return f"[ошибка] {cmd}\n{exc.stderr}"


def setup_root_qdisc() -> list[str]:
    """
    Один раз настроить «корень» ограничителя скорости на VPN-интерфейсе.
    Создаём дисциплину HTB — это позволяет потом раздавать каждому клиенту свой класс.
    Вызывать один раз при первой настройке (повторный вызов просто пересоздаст корень).
    """
    iface = config.WG_INTERFACE
    cmds = [
        # Удаляем старый корень, если был (ошибку игнорируем — на первом запуске его нет).
        f"tc qdisc del dev {iface} root 2>/dev/null || true",
        # Создаём корневую дисциплину HTB.
        f"tc qdisc add dev {iface} root handle 1: htb default 9999",
    ]
    return [_run(c) for c in cmds]


def set_speed_limit(client_ip: str, mbit: int) -> list[str]:
    """
    Ограничить скорость скачивания для клиента до `mbit` мегабит/с.
    mbit = 0 означает «без ограничения» — тогда правило снимается.

    Под капотом: создаём класс HTB с нужной скоростью и фильтр,
    который направляет трафик «на адрес клиента» в этот класс.
    """
    iface = config.WG_INTERFACE
    cid = _class_id(client_ip)

    if mbit <= 0:
        # Снять ограничение: удаляем класс (и связанный с ним фильтр).
        return [_run(f"tc class del dev {iface} classid {cid} 2>/dev/null || true")]

    cmds = [
        # Создаём/обновляем класс с лимитом скорости. ceil = максимум, который класс может занять.
        f"tc class replace dev {iface} parent 1: classid {cid} htb rate {mbit}mbit ceil {mbit}mbit",
        # Фильтр: весь трафик, идущий НА адрес клиента (его скачивание), кладём в этот класс.
        f"tc filter replace dev {iface} protocol ip parent 1: prio 1 "
        f"u32 match ip dst {client_ip}/32 flowid {cid}",
    ]
    return [_run(c) for c in cmds]


def build_nft_ruleset(clients: list[dict]) -> str:
    """
    Собрать ПОЛНЫЙ текст правил nftables для контроля протоколов.
    На вход — список клиентов: [{"ip": "10.8.0.5", "profile": "web"}, ...].

    Мы создаём отдельную таблицу vpn_filter, чтобы не мешать другим правилам сервера.
    Для каждого клиента в зависимости от профиля добавляем правила:
      web        — разрешаем только 80/443/53, остальное от него дропаем
      no_torrent — дропаем торрент-порты, остальное разрешаем
      all        — ничего не ограничиваем
    """
    lines = [
        "# Этот файл создаётся автоматически. Применять: sudo nft -f vpn_filter.nft",
        "table inet vpn_filter {",
        "    chain forward {",
        "        type filter hook forward priority 0; policy accept;",
    ]
    for client in clients:
        ip = client["ip"]
        profile = client.get("profile", "all")
        if profile == "web":
            lines.append(f"        # клиент {ip}: только веб и DNS")
            lines.append(
                f"        ip saddr {ip} tcp dport {{ 80, 443 }} accept"
            )
            lines.append(f"        ip saddr {ip} udp dport 53 accept")
            lines.append(f"        ip saddr {ip} tcp dport 53 accept")
            lines.append(f"        ip saddr {ip} drop")
        elif profile == "no_torrent":
            lines.append(f"        # клиент {ip}: всё, кроме торрент-портов")
            lines.append(
                f"        ip saddr {ip} tcp dport {{ {TORRENT_PORTS} }} drop"
            )
            lines.append(
                f"        ip saddr {ip} udp dport {{ {TORRENT_PORTS} }} drop"
            )
        # profile == "all" — никаких ограничений не добавляем
    lines.append("    }")
    lines.append("}")
    return "\n".join(lines)


def apply_protocol_rules(clients: list[dict]) -> str:
    """
    Применить правила протоколов: записать ruleset во временный файл и
    скормить его nftables. В безопасном режиме просто вернуть текст правил.
    """
    ruleset = build_nft_ruleset(clients)
    if not config.APPLY_TRAFFIC_RULES:
        return "[режим показа] правила nftables:\n" + ruleset

    path = "/tmp/vpn_filter.nft"
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(ruleset + "\n")
        subprocess.run(["nft", "-f", path], check=True, capture_output=True, text=True)
        return "[выполнено] правила nftables применены"
    except FileNotFoundError:
        return "[ошибка] утилита nft не найдена (установи: apt install nftables)"
    except subprocess.CalledProcessError as exc:
        return f"[ошибка] не удалось применить правила:\n{exc.stderr}"


def apply_all_for_subscription(client_ip: str, speed_limit_mbit: int) -> list[str]:
    """
    Удобная обёртка: применить скоростной лимит для одного клиента.
    Контроль протоколов применяется пачкой для всех сразу (см. notifier/панель),
    потому что nftables перезаписывает таблицу целиком.
    """
    return set_speed_limit(client_ip, speed_limit_mbit)
