#!/bin/sh
# Traduit /data/options.json (options de l'add-on) en variables d'environnement.
set -e
eval "$(python3 - <<'PY'
import json, shlex
o = json.load(open("/data/options.json"))
env = {
    "PADLET_WALL_HASHID": o["padlet_wall_hashid"],
    "HA_TODO_ENTITY": o["todo_entity"],
    "HA_AI_TASK_ENTITY": o["ai_task_entity"],
    "DEVOIRS_CRON": o["cron"],
}
for k, v in env.items():
    print(f"export {k}={shlex.quote(str(v))}")
PY
)"
exec devoirs --env-file "" daemon
