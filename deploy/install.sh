#!/usr/bin/env bash
# Установка или обновление демо-стенда на Ubuntu. Запуск: sudo bash deploy/install.sh
# Сервис работает от системного пользователя campapi без входа и без sudo,
# код и виртуальное окружение принадлежат root и доступны сервису только на чтение.
set -euo pipefail

REPO=https://github.com/MatiarRKh/camp-exchange-api.git
DIR=/srv/camp-exchange-api

id campapi >/dev/null 2>&1 || useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin campapi

if [ -d "$DIR/.git" ]; then
  git -C "$DIR" pull --ff-only
else
  git clone "$REPO" "$DIR"
fi
[ -d "$DIR/.venv" ] || python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install --quiet --upgrade pip
"$DIR/.venv/bin/pip" install --quiet -r "$DIR/requirements.txt"
chown -R root:root "$DIR"
chmod -R go-w "$DIR"

install -m 644 "$DIR/deploy/camp-exchange-api.service" /etc/systemd/system/camp-exchange-api.service
systemctl daemon-reload
systemctl enable camp-exchange-api
systemctl restart camp-exchange-api
