#!/usr/bin/env bash
set -euo pipefail
src=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
stage=${DESTDIR:-}
if [[ -z "$stage" && $EUID -ne 0 ]]; then echo '请用 sudo bash scripts/install.sh'; exit 1; fi
root="$stage/opt/qq-deepseek"
install -d -m 700 "$root" "$root/memes"
for name in bot social memory_flow thinking_flow reply_flow fish_flow balance_watch private_greeting; do
  install -m 444 "$src/$name.py" "$root/$name.py"
done
install -m 500 "$src/scripts/configure.py" "$root/configure.py"
install -m 444 "$src/persona.txt" "$root/persona.txt"
install -m 444 "$src/persona-card.json" "$root/persona-card.json"
for f in "$src"/memes/*; do install -m 644 "$f" "$root/memes/"; done
if [[ ! -e "$root/config.json" ]]; then install -m 600 "$src/config/config.example.json" "$root/config.json"; fi
install -d -m 755 "$stage/etc/systemd/system"
for f in "$src"/systemd/*.service "$src"/systemd/*.timer; do install -m 644 "$f" "$stage/etc/systemd/system/"; done
if [[ -z "$stage" ]]; then
  systemctl daemon-reload
  systemctl enable qq-deepseek.service
fi
printf '%s\n' '文件已安装。未写入密钥，未自动安装或登录QQ，未启动付费请求。' '下一步：sudo python3 /opt/qq-deepseek/configure.py'
