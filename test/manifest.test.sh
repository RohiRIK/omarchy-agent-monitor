#!/bin/bash
#
# Manifest and plugin layout test: validates manifest.json fields, checks the
# QML finds the bundled collector, and runs `omarchy plugin validate` when
# Omarchy is installed.

set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)

python3 - "$ROOT" <<'PY'
import json, pathlib, re, sys

root = pathlib.Path(sys.argv[1])
failed = False

def check(condition, description):
    global failed
    print(("ok - " if condition else "not ok - ") + description, file=sys.stdout if condition else sys.stderr)
    failed |= not condition

m = json.loads((root / "manifest.json").read_text())
check(m.get("schemaVersion") == 1, "schemaVersion is 1")
for field in ("id", "name", "version", "author", "license", "description"):
    check(isinstance(m.get(field), str) and m[field] != "", f"manifest field {field} present")
check(m.get("id") == "rohirik.agent-monitor", "plugin id is rohirik.agent-monitor")
check("bar-widget" in m.get("kinds", []), "kinds include bar-widget")
entry = m.get("entryPoints", {}).get("barWidget", "")
check(entry != "" and (root / entry).is_file(), f"entry point exists: {entry}")
check(bool(m.get("barWidget", {}).get("displayName")), "barWidget displayName present")

for name in ("README.md", "LICENSE", "collector.py", "preview.png"):
    check((root / name).is_file(), f"{name} exists")

panel = (root / "Panel.qml").read_text()
check(f'moduleName: "{m["id"]}"' in panel and f'ipcTarget: "{m["id"]}"' in panel, "Panel moduleName and ipcTarget match the plugin id")

main = (root / "Main.qml").read_text()
check('Qt.resolvedUrl("collector.py")' in main, "Main.qml runs the bundled collector")
check(".config/omarchy/" not in main, "Main.qml has no hard-coded config path")

for path in root.glob("*.qml"):
    check(not re.search(r"ajex", path.read_text(), re.I), f"{path.name} has no leftover ajex references")

sys.exit(1 if failed else 0)
PY

if command -v omarchy >/dev/null 2>&1; then
  omarchy plugin validate "$ROOT" >/dev/null
  echo "ok - omarchy plugin validate"
else
  echo "ok - omarchy plugin validate # skip: Omarchy not installed"
fi
