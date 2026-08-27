#!/bin/bash
# KidsUP: шаг 1 переезда доменов — добавить kidsup.ru/day/week в Caddy.
# Идемпотентный: повторный запуск ничего не ломает.
set -e
CF=/etc/caddy/Caddyfile
if [ ! -f "$CF" ]; then
  # Caddy может жить в докере или с другим конфигом — ищем
  CF=$(find /etc /opt /root -maxdepth 3 -name Caddyfile 2>/dev/null | head -1)
fi
if [ -z "$CF" ] || [ ! -f "$CF" ]; then
  echo "❌ Caddyfile не найден. Пришлите Клоду вывод: ps aux | grep caddy"
  exit 1
fi
echo "Caddyfile: $CF"
if grep -q "kidsupday.ru" "$CF"; then
  echo "✅ Домены уже добавлены раньше — ничего не меняю."
else
  PORT=$(grep -A5 "app.kidsup.ru" "$CF" | grep -oE "(localhost|127.0.0.1):[0-9]+" | head -1)
  if [ -z "$PORT" ]; then
    echo "❌ Не нашёл порт приложения в блоке app.kidsup.ru."
    echo "Пришлите Клоду содержимое файла: cat $CF"
    exit 1
  fi
  echo "Порт приложения: $PORT"
  cp "$CF" "$CF.bak.$(date +%s)"
  printf '\n# KidsUP: боевые домены сайта (добавлено скриптом dns_step1)\nkidsup.ru, www.kidsup.ru, kidsupday.ru, www.kidsupday.ru, kidsupweek.ru, www.kidsupweek.ru {\n    reverse_proxy %s\n}\n' "$PORT" >> "$CF"
fi
caddy validate --config "$CF" 2>&1 | tail -1
systemctl reload caddy 2>/dev/null || caddy reload --config "$CF"
echo "✅ ГОТОВО. Caddy знает новые домены. Теперь шаг 2 — смена DNS в панели REG.RU."
