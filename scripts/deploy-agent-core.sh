#!/bin/bash
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
astrbot_data=${ASTRBOT_DATA:-/opt/astrbot/data}
plugin_src="$repo_root/integrations/astrbot/plugins/dafeiyu_agent_core"
prompt_src="$repo_root/integrations/astrbot/prompts"
plugin_dst="$astrbot_data/plugins/dafeiyu_agent_core"
prompt_dst="$astrbot_data/prompts"
astrbot_python=${ASTRBOT_PYTHON:-/opt/astrbot/venv/bin/python}

if [ ! -x "$astrbot_python" ]; then
  astrbot_python=python3
fi

if [ "${1:-}" = "--activate" ]; then
  activate=1
else
  activate=0
fi

test -f "$plugin_src/main.py"
test -f "$prompt_src/core.md"
"$astrbot_python" -m unittest discover -s "$repo_root/integrations/astrbot/tests" -v
"$astrbot_python" -m compileall -q "$plugin_src"

install -d -m 700 "$astrbot_data/dafeiyu_agent"
install -d -m 755 "$plugin_dst" "$prompt_dst"
find "$plugin_dst" -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +
cp -a "$plugin_src"/. "$plugin_dst"/
cp -a "$prompt_src"/. "$prompt_dst"/
find "$plugin_dst" "$prompt_dst" -type d -exec chmod 755 {} +
find "$plugin_dst" "$prompt_dst" -type f -exec chmod 644 {} +

if [ "$activate" -eq 1 ]; then
  systemctl restart qq-astrbot
  sleep 3
  systemctl --no-pager --full status qq-astrbot
  curl --fail --silent --show-error http://127.0.0.1:8766/health >/dev/null
else
  echo "Agent core staged. Run this script with --activate after reviewing the backup and maintenance window."
fi
