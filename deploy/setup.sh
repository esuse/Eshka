#!/usr/bin/env bash
# Скрипт первичной установки на сервере Ubuntu 22.04.
# Запускать ОТ КОРНЯ проекта:   bash deploy/setup.sh
# Он НЕ трогает Amnezia и VPN — только готовит наше приложение (бот + панель).

set -e  # при первой же ошибке останавливаемся

echo "==> 1/4 Устанавливаем Python и нужные системные пакеты"
sudo apt update
# python3-venv — чтобы сделать изолированное окружение; nftables — для контроля протоколов;
# iproute2 даёт команду tc (обычно уже стоит).
sudo apt install -y python3 python3-venv python3-pip nftables iproute2

echo "==> 2/4 Создаём виртуальное окружение Python в папке .venv"
python3 -m venv .venv
# Включаем окружение и ставим библиотеки из requirements.txt
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

echo "==> 3/4 Готовим файл настроек .env"
if [ ! -f .env ]; then
    cp .env.example .env
    echo "    Создан .env из шаблона. ОБЯЗАТЕЛЬНО открой его и заполни:  nano .env"
else
    echo "    .env уже существует — не трогаю."
fi

echo "==> 4/4 Включаем пересылку пакетов (нужно, чтобы VPN раздавал интернет)"
# Эта строка разрешает серверу передавать трафик клиентов в интернет.
sudo sysctl -w net.ipv4.ip_forward=1
# Чтобы сохранилось после перезагрузки:
echo "net.ipv4.ip_forward=1" | sudo tee /etc/sysctl.d/99-vpn.conf >/dev/null

echo ""
echo "Готово! Дальше:"
echo "  1) Заполни настройки:        nano .env"
echo "  2) Запусти бота (тест):       source .venv/bin/activate && python bot.py"
echo "  3) Запусти панель (тест):     source .venv/bin/activate && python web_panel.py"
echo "  4) Для постоянной работы настрой автозапуск (см. README, раздел про systemd)."
