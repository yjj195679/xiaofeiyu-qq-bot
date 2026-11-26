#!/bin/bash
set -euo pipefail

backup_root=${BACKUP_ROOT:-/opt/xiaofeiyu-backups}
stamp=$(date +%Y%m%d-%H%M%S)
target="$backup_root/agent-upgrade-$stamp"
install -d -m 700 "$target"

copy_if_present() {
  local source=$1 name=$2
  if [ -e "$source" ]; then
    cp -a -- "$source" "$target/$name"
  fi
}

copy_if_present /opt/astrbot/data astrbot-data
copy_if_present /opt/qq-deepseek qq-deepseek
copy_if_present /etc/systemd/system/qq-astrbot.service qq-astrbot.service
copy_if_present /etc/systemd/system/qq-napcat.service qq-napcat.service
copy_if_present /etc/systemd/system/qq-kugou-api.service qq-kugou-api.service
copy_if_present /etc/systemd/system/qq-kugou-bridge.service qq-kugou-bridge.service

if command -v sqlite3 >/dev/null 2>&1; then
  while IFS= read -r -d '' db; do
    relative=${db#/}
    out="$target/sqlite/$relative"
    install -d -m 700 "$(dirname "$out")"
    sqlite3 "$db" ".backup '$out'"
  done < <(find /opt/astrbot/data /opt/qq-deepseek -type f \( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' \) -print0 2>/dev/null)
fi

manifest=$(mktemp)
trap 'rm -f -- "$manifest"' EXIT
find "$target" -type f ! -name SHA256SUMS -print0 | sort -z | xargs -0 sha256sum > "$manifest"
install -m 600 "$manifest" "$target/SHA256SUMS"
chmod -R go-rwx "$target"
printf '%s\n' "$target"
