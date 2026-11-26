#!/bin/bash
set -euo pipefail

repo_root=$(cd "$(dirname "$0")/.." && pwd)
astrbot_data=${ASTRBOT_DATA:-/opt/astrbot/data}
plugin_src="$repo_root/integrations/astrbot/plugins/dafeiyu_agent_core"
prompt_src="$repo_root/integrations/astrbot/prompts"
plugin_dst="$astrbot_data/plugins/dafeiyu_agent_core"
candidate="$astrbot_data/plugins/.dafeiyu_agent_core.candidate"
astrbot_python=${ASTRBOT_PYTHON:-/opt/astrbot/venv/bin/python}
lock_file=${DAFEIYU_DEPLOY_LOCK:-/run/lock/dafeiyu-agent-deploy.lock}
stamp=$(date +%Y%m%d-%H%M%S)

activate=0
if [ "${1:-}" = "--activate" ]; then
  activate=1
elif [ -n "${1:-}" ]; then
  echo "usage: $0 [--activate]" >&2
  exit 2
fi

if [ ! -x "$astrbot_python" ]; then
  astrbot_python=python3
fi

exec 9>"$lock_file"
if ! flock -n 9; then
  echo "another Agent deployment is already running" >&2
  exit 75
fi

test -f "$plugin_src/main.py"
test -f "$prompt_src/core.md"
"$astrbot_python" -m unittest discover -s "$repo_root/integrations/astrbot/tests" -v
"$astrbot_python" -m compileall -q "$plugin_src"

install -d -m 700 "$astrbot_data/dafeiyu_agent"
install -d -m 755 "$astrbot_data/plugins"
if [ -e "$candidate" ]; then
  mv -- "$candidate" "${candidate}.abandoned-$stamp"
fi
install -d -m 755 "$candidate" "$candidate/prompts"
cp -a "$plugin_src"/. "$candidate"/
cp -a "$prompt_src"/. "$candidate/prompts"/
find "$candidate" -type d -exec chmod 755 {} +
find "$candidate" -type f -exec chmod 644 {} +

# Preflight additive migrations against an online SQLite backup, not production.
live_db="$astrbot_data/dafeiyu_agent/agent.sqlite"
preflight_dir=$(mktemp -d "$astrbot_data/.agent-migration.XXXXXX")
cleanup() { rm -rf -- "$preflight_dir"; }
trap cleanup EXIT
preflight_db="$preflight_dir/agent.sqlite"
if [ -f "$live_db" ]; then
  "$astrbot_python" - "$live_db" "$preflight_db" <<'PY'
import sqlite3, sys
source = sqlite3.connect(sys.argv[1])
target = sqlite3.connect(sys.argv[2])
source.backup(target)
target.close()
source.close()
PY
fi
PYTHONPATH="$repo_root" "$astrbot_python" - "$preflight_db" <<'PY'
import sys
from integrations.astrbot.plugins.dafeiyu_agent_core import AgentStore
store = AgentStore(sys.argv[1])
with store.connect() as db:
    result = db.execute("PRAGMA integrity_check").fetchone()[0]
if result != "ok":
    raise SystemExit("migration preflight integrity check failed")
PY

if [ "$activate" -eq 0 ]; then
  echo "Agent candidate validated at $candidate; production was not changed."
  exit 0
fi

previous="${plugin_dst}.previous-$stamp"
if [ -e "$plugin_dst" ]; then
  mv -- "$plugin_dst" "$previous"
fi
mv -- "$candidate" "$plugin_dst"

rollback() {
  local exit_code=$?
  trap - ERR
  echo "activation failed; restoring previous plugin" >&2
  systemctl stop qq-astrbot || true
  if [ -e "$plugin_dst" ]; then
    mv -- "$plugin_dst" "${plugin_dst}.failed-$stamp"
  fi
  if [ -e "$previous" ]; then
    mv -- "$previous" "$plugin_dst"
  fi
  systemctl start qq-astrbot || true
  exit "$exit_code"
}
trap rollback ERR

systemctl restart qq-astrbot
ready=0
for _ in $(seq 1 30); do
  if curl --fail --silent --show-error --max-time 3 http://127.0.0.1:8766/health >/dev/null; then
    ready=1
    break
  fi
  sleep 1
done
test "$ready" -eq 1
systemctl is-active --quiet qq-astrbot
curl --fail --silent --show-error --max-time 3 http://127.0.0.1:8770/health >/dev/null
trap - ERR
echo "Agent core activated successfully; previous version retained at $previous"
