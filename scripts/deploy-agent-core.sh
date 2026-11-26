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

# The candidate deliberately lives in a hidden directory so AstrBot cannot
# discover it before activation.  Validate its real import graph explicitly;
# adding the hidden directory itself to PYTHONPATH would not make it a valid
# Python package name.
"$astrbot_python" - "$candidate" <<'PY'
import importlib.util
import pathlib
import sys

candidate = pathlib.Path(sys.argv[1])
name = "dafeiyu_agent_core_candidate"
spec = importlib.util.spec_from_file_location(
    name,
    candidate / "__init__.py",
    submodule_search_locations=[str(candidate)],
)
if spec is None or spec.loader is None:
    raise SystemExit("candidate plugin import specification failed")
module = importlib.util.module_from_spec(spec)
sys.modules[name] = module
spec.loader.exec_module(module)
main_spec = importlib.util.spec_from_file_location(
    name + ".main",
    candidate / "main.py",
    submodule_search_locations=[str(candidate)],
)
if main_spec is None or main_spec.loader is None:
    raise SystemExit("candidate main import specification failed")
main = importlib.util.module_from_spec(main_spec)
sys.modules[name + ".main"] = main
main_spec.loader.exec_module(main)
assert main.DafeiyuAgentCore.__name__ == "DafeiyuAgentCore"
print("candidate AstrBot import passed")
PY

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

# AstrBot may load more than one request hook.  The legacy
# astrbot_plugin_memory_system also injects long-term memories into the system
# prompt and writes its own store.  Activating Agent Core without an explicit
# ownership decision would silently duplicate memory writes/retrieval.  The
# operator must first disable that legacy memory plugin in AstrBot and then
# acknowledge the cutover in this one-shot deployment command.
if [ "${DAFEIYU_MEMORY_OWNER:-}" != "core" ]; then
  cat >&2 <<'EOF'
Refusing activation: set DAFEIYU_MEMORY_OWNER=core only after the legacy
astrbot_plugin_memory_system has been disabled in AstrBot's plugin manager.
This guard prevents two memory systems from injecting or persisting the same
conversation. Candidate validation did complete; production was not changed.
EOF
  exit 64
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
