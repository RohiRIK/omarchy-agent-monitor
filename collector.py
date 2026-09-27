#!/usr/bin/env python3
"""Unified Agent Usage Collector for the Agent Monitor plugin (rohirik.agent-monitor).

Collects AI coding agent limits, token metrics, and activity for all
CodexBar-supported providers (Claude, Codex, Antigravity, Gemini, OpenCode,
OpenRouter, DeepSeek, Ollama, etc.) into ~/.local/state/omarchy/agent-monitor/usage/.
"""

import argparse
import sqlite3
import subprocess
from collections import defaultdict
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

STATE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "omarchy/agent-monitor/usage"
SOURCE_USAGE_DIR = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "omarchy/agents/usage"
CONFIG_FILE = Path.home() / ".config/omarchy/agent-monitor/providers.json"


def get_providers_config() -> Dict[str, Any]:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def write_record(agent_id: str, record: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    target = STATE_DIR / f"{agent_id}.json"
    with tempfile.NamedTemporaryFile("w", dir=STATE_DIR, delete=False, encoding="utf-8") as tmp:
        json.dump(record, tmp, indent=2)
        tmp.write("\n")
        tmp_name = tmp.name
    os.replace(tmp_name, target)


def get_recent_days(days: int = 7) -> List[Dict[str, Any]]:
    today = dt.datetime.now().date()
    return [{"date": (today - dt.timedelta(days=i)).strftime("%Y-%m-%d"), "messageCount": 0} for i in range(days - 1, -1, -1)]


def sync_from_source(agent_id: str) -> bool:
    src = SOURCE_USAGE_DIR / f"{agent_id}.json"
    if src.exists():
        try:
            with open(src, "r", encoding="utf-8") as f:
                data = json.load(f)
            state = installation(agent_id, AGENTS.get(agent_id, ("", []))[1])
            data.update(installationStatus=state, installed=state == "Installed", monitoringEnabled=True)
            write_record(agent_id, data)
            return True
        except Exception:
            pass
    return False


def collect_openrouter(api_key: str) -> Optional[Dict[str, Any]]:
    if not api_key:
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        return None

    now_iso = dt.datetime.now(dt.timezone.utc).isoformat()
    req = urllib.request.Request("https://openrouter.ai/api/v1/auth/key")
    req.add_header("Authorization", f"Bearer {api_key}")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            info = data.get("data", {})
            limit = float(info.get("limit") or 0)
            usage = float(info.get("usage") or 0)
            remaining = max(0.0, limit - usage) if limit > 0 else 0.0

            return {
                "schemaVersion": 1,
                "id": "openrouter",
                "name": "OpenRouter",
                "tierLabel": "Credits",
                "ready": True,
                "hasLocalStats": False,
                "usageStatusText": "",
                "authHelpText": "",
                "balance": {
                    "remaining": remaining,
                    "funded": limit if limit > 0 else usage,
                    "spent": usage,
                    "currency": "USD",
                    "estimated": False
                },
                "updatedAt": now_iso,
                "recentDays": get_recent_days(7),
                "modelUsage": {}
            }
    except Exception:
        return None


AGENTS = {
    "copilot": ("GitHub Copilot", ["copilot"]),
    "cursor": ("Cursor Agent", ["cursor-agent"]),
    "crush": ("Crush", ["crush"]),
    "grok": ("Grok", ["grok"]),
    "muse": ("Muse", ["muse"]),
    "goose": ("Goose", ["goose"]),
    "qwen": ("Qwen Code", ["qwen"]),
    "amp": ("Amp", ["amp"]),

    "fireworks": ("Fireworks", []),
    "openrouter": ("OpenRouter", []),
    "claude": ("Claude Code", ["claude"]), "codex": ("Codex", ["codex"]),
    "hermes": ("Hermes", ["hermes"]), "pi": ("pi", ["pi"]),
    "opencode": ("OpenCode", ["opencode"]), "antigravity": ("AGY", ["agy", "antigravity"]),
 "omp": ("Oh My Pi", ["omp"]),
    "aider": ("Aider", ["aider"]), "ollama": ("Ollama", ["ollama"]),
}


def installation(agent_id, commands):
    launcher = False
    for command in commands:
        path = shutil.which(command)
        if not path: continue
        try:
            with open(path, "rb") as stream: header = stream.read(2048).decode("utf-8", errors="replace")
        except OSError: continue
        if "mise use -g" in header:
            launcher = True
            import re
            match = re.search(r'mise use -g --quiet "([^"\n]+)"', header)
            if match:
                tool=match.group(1)
                try:
                    result=subprocess.run(["mise", "where", tool],capture_output=True,text=True,timeout=5)
                    if result.returncode == 0 and Path(result.stdout.strip()).is_dir(): return "Installed"
                except (OSError,subprocess.TimeoutExpired): pass
            continue
        return "Installed"
    if agent_id in ("fireworks", "openrouter"): return "Account service"
    return "Launcher only" if launcher else "Not installed"


def local_day(value):
    try:
        if isinstance(value, (int, float)):
            return dt.datetime.fromtimestamp(value / 1000 if value > 1e11 else value).date().isoformat()
        return dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).astimezone().date().isoformat()
    except (ValueError, TypeError, OverflowError, OSError):
        return ""


def empty_record(agent_id, name):
    return dict(schemaVersion=1, id=agent_id, name=name, installed=True, ready=True,
                tierLabel="", hasLocalStats=False, hasDailyTokens=False, hasPromptStats=False,
                usageStatusText="Installed · usage unavailable", authHelpText="No supported local usage source found.",
                limits=[], recentDays=[], modelUsage={}, updatedAt=dt.datetime.now(dt.timezone.utc).isoformat())


def read_local(agent_id, record):
    today = dt.date.today().isoformat()
    days = defaultdict(int)
    sessions = set()
    today_sessions = set()
    prompts = 0
    today_prompts = 0
    models = {}
    daily_tokens = agent_id != "hermes"

    def add(sid, stamp, role, model="", values=(0, 0, 0, 0)):
        nonlocal prompts, today_prompts
        day = local_day(stamp)
        sessions.add(sid)
        if day == today: today_sessions.add(sid)
        if role == "user":
            prompts += 1
            if day == today: today_prompts += 1
        if role != "assistant": return
        values = [max(0, int(v or 0)) for v in values]
        if not any(values): return
        bucket = models.setdefault(model or "Unknown model", dict.fromkeys(
            ["inputTokens", "outputTokens", "cacheReadInputTokens", "cacheCreationInputTokens"], 0))
        for key, value in zip(bucket, values): bucket[key] += value
        if day and daily_tokens: days[day] += sum(values)

    if agent_id in ("pi", "omp"):
        base = Path(os.environ.get("PI_CODING_AGENT_DIR", str(Path.home()/".pi/agent"))) if agent_id == "pi" else Path.home()/".omp/agent"
        for path in (base/"sessions").rglob("*.jsonl"):
            with path.open(errors="replace") as stream:
                for line in stream:
                    try:
                        entry = json.loads(line)
                        if entry.get("type") != "message": continue
                        m = entry.get("message") or {}; u = m.get("usage") or {}
                        values = [u.get(k, 0) for k in ("input", "output", "cacheRead", "cacheWrite")]
                        if not any(values): values[0] = u.get("totalTokens", 0)
                        add(str(path), entry.get("timestamp") or m.get("timestamp"), m.get("role"), m.get("model"), values)
                    except (ValueError, TypeError, AttributeError): continue
    elif agent_id == "opencode":
        path = Path(os.environ.get("XDG_DATA_HOME", str(Path.home()/".local/share")))/"opencode/opencode.db"
        if not path.exists(): return
        with sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=2) as conn:
            conn.execute("PRAGMA query_only=ON")
            for sid, raw in conn.execute("SELECT session_id, data FROM message"):
                try:
                    m=json.loads(raw); u=m.get("tokens") or {}; cache=u.get("cache") or {}
                    add(sid, (m.get("time") or {}).get("created"), m.get("role"), m.get("modelID"),
                        [u.get("input",0), (u.get("output") or 0)+(u.get("reasoning") or 0), cache.get("read",0), cache.get("write",0)])
                except (ValueError, TypeError, AttributeError): continue
    elif agent_id == "hermes":
        path=Path(os.environ.get("HERMES_HOME",str(Path.home()/".hermes")))/"state.db"
        if not path.exists(): return
        with sqlite3.connect(path.as_uri()+"?mode=ro", uri=True, timeout=2) as conn:
            conn.execute("PRAGMA query_only=ON")
            for sid, model, inp, out, cr, cw in conn.execute("SELECT id, model, input_tokens, output_tokens, cache_read_tokens, cache_write_tokens FROM sessions"):
                add(sid, None, "assistant", model, [inp,out,cr,cw])
            for sid, stamp, role in conn.execute("SELECT session_id, timestamp, role FROM messages WHERE role IN ('user','assistant')"):
                add(sid, stamp, role)
    else: return

    record.update(hasLocalStats=bool(sessions), hasDailyTokens=daily_tokens, hasPromptStats=True,
                  todayPrompts=today_prompts, todaySessions=len(today_sessions), totalPrompts=prompts,
                  totalSessions=len(sessions), todayTotalTokens=days.get(today,0), modelUsage=models,
                  recentDays=[dict(date=d["date"],messageCount=days.get(d["date"],0)) for d in get_recent_days()] if daily_tokens and sessions else [],
                  usageStatusText="" if sessions else "Installed · no recorded sessions",
                  authHelpText="", dataNote="Local tool usage · may overlap subscription totals")
    if agent_id == "hermes":
        record["dataNote"] += " · daily token totals unavailable"


def main():
    parser=argparse.ArgumentParser(description="Real local agent usage and installed-agent discovery")
    parser.add_argument("--set-enabled", nargs=2, metavar=("AGENT", "ENABLED"))
    parser.add_argument("--force",action="store_true")
    parser.add_argument("--limits-only",action="store_true")
    parser.add_argument("--except",dest="exclude",action="append",default=[])
    parser.add_argument("agents",nargs="*")
    args=parser.parse_args(); config=get_providers_config()
    if args.set_enabled:
        agent_id, enabled = args.set_enabled
        if agent_id not in AGENTS or enabled not in ("true", "false"):
            parser.error("Unknown agent or invalid enabled value")
        config.setdefault(agent_id, {})["enabled"] = enabled == "true"
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile("w",dir=CONFIG_FILE.parent,delete=False) as tmp:
            json.dump(config,tmp,indent=2); tmp.write("\n")
        os.replace(tmp.name,CONFIG_FILE)
        args.agents=[agent_id]
    def wanted(a):
        return a not in args.exclude and (not args.agents or a in args.agents) and config.get(a,{}).get("enabled",True)
    stock=[a for a in ("claude","codex","fireworks") if wanted(a)]
    if stock:
        cmd=["omarchy","agent","usage","update"]
        if args.force: cmd.append("--force")
        if args.limits_only: cmd.append("--limits-only")
        try: subprocess.run(cmd+stock,timeout=90,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        except (OSError,subprocess.TimeoutExpired): pass
    for agent_id,(name,commands) in AGENTS.items():
        if args.agents and agent_id not in args.agents: continue
        state=installation(agent_id,commands)
        installed=state == "Installed"
        if not wanted(agent_id):
            record=empty_record(agent_id,name)
            record.update(installed=installed, installationStatus=state, monitoringEnabled=False, usageStatusText="Monitoring disabled", authHelpText="")
            write_record(agent_id,record)
            continue
        if agent_id in stock and sync_from_source(agent_id): continue
        if agent_id == "openrouter":
            record=collect_openrouter(config.get(agent_id,{}).get("apiKey",""))
            if record: write_record(agent_id,record)
            continue
        record=empty_record(agent_id,name)
        try: read_local(agent_id,record)
        except (OSError,sqlite3.Error,ValueError) as exc:
            record["usageStatusText"]="Local usage could not be read"
            record["authHelpText"]="Refresh to retry."
            print(f"{agent_id}: {type(exc).__name__}",file=sys.stderr)
        record.update(installed=installed, installationStatus=state)
        if not installed and not record.get("hasLocalStats"):
            record.update(monitoringEnabled=False, usageStatusText=state, authHelpText="")
        write_record(agent_id,record)
    if not args.agents: (STATE_DIR/"gemini.json").unlink(missing_ok=True)
    if wanted("fireworks"): sync_from_source("fireworks")
    if wanted("openrouter"):
        record=collect_openrouter(config.get("openrouter",{}).get("apiKey",""))
        if record: write_record("openrouter",record)


if __name__ == "__main__": main()
