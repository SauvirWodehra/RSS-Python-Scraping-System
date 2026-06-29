"""
setup_cron.py
--------------
Registers the RSS pipeline as a Windows Task Scheduler task so it runs
automatically every hour (or your configured interval) — no terminal window
needs to stay open.

Usage:
    python setup_cron.py                  # Register/update the task
    python setup_cron.py --interval 30    # Run every 30 minutes
    python setup_cron.py --remove         # Remove the task
    python setup_cron.py --status         # Show task status
    python setup_cron.py --run-now        # Trigger an immediate run
"""

import argparse
import os
import subprocess
import sys
import textwrap
from pathlib import Path

# Force UTF-8 output so emoji/special chars do not crash on Windows cp1252
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ── Configuration ─────────────────────────────────────────────────────────────
TASK_NAME    = "RSSScrapingPipeline"
PROJECT_DIR  = Path(__file__).resolve().parent
LOG_FILE     = PROJECT_DIR / "logs" / "scheduler_task.log"
PYTHON_EXE   = sys.executable                  # same Python that runs this script
SCRIPT_PATH  = PROJECT_DIR / "main.py"


def _run(cmd: list[str], capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        shell=False,
    )


def _schtasks(*args) -> subprocess.CompletedProcess:
    return _run(["schtasks", *args])


# ── Commands ──────────────────────────────────────────────────────────────────

def register_task(interval_minutes: int) -> None:
    """Create or update the scheduled task."""

    LOG_FILE.parent.mkdir(exist_ok=True)

    # Build the action command — pipes stdout+stderr to the log file
    action_cmd = (
        f'cmd /c ""{PYTHON_EXE}" "{SCRIPT_PATH}" --once '
        f'>> "{LOG_FILE}" 2>&1"'
    )

    # Delete existing task silently (ignore errors if not present)
    _schtasks("/Delete", "/TN", TASK_NAME, "/F")

    result = _schtasks(
        "/Create",
        "/TN",   TASK_NAME,
        "/SC",   "MINUTE",
        "/MO",   str(interval_minutes),
        "/TR",   action_cmd,
        "/F",                         # force overwrite if exists
    )

    if result.returncode == 0:
        print(f"✅ Task '{TASK_NAME}' registered successfully.")
        print(f"   Runs every {interval_minutes} minute(s).")
        print(f"   Python : {PYTHON_EXE}")
        print(f"   Script : {SCRIPT_PATH}")
        print(f"   Log    : {LOG_FILE}")
        print()
        print("The pipeline will run automatically in the background.")
        print("You can close this terminal — the task will keep running.")
    else:
        print(f"❌ Failed to register task.")
        print(result.stderr or result.stdout)
        sys.exit(1)


def remove_task() -> None:
    """Remove the scheduled task."""
    result = _schtasks("/Delete", "/TN", TASK_NAME, "/F")
    if result.returncode == 0:
        print(f"✅ Task '{TASK_NAME}' removed.")
    else:
        print(f"⚠️  Task not found or could not be removed.")
        print(result.stderr or result.stdout)


def show_status() -> None:
    """Display current task status."""
    result = _schtasks("/Query", "/TN", TASK_NAME, "/FO", "LIST", "/V")
    if result.returncode == 0:
        # Filter to the most useful lines
        interesting = [
            "TaskName", "Status", "Next Run Time", "Last Run Time",
            "Last Result", "Schedule Type", "Start Time",
        ]
        lines = result.stdout.splitlines()
        print(f"\n📋 Task: {TASK_NAME}")
        print("-" * 50)
        for line in lines:
            if any(line.strip().startswith(k) for k in interesting):
                print(f"  {line.strip()}")
        print()

        # Show last few log lines
        if LOG_FILE.exists():
            tail = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
            print(f"📄 Last 10 lines of {LOG_FILE.name}:")
            print("-" * 50)
            for ln in tail[-10:]:
                print(f"  {ln}")
            print()
    else:
        print(f"⚠️  Task '{TASK_NAME}' not found. Run: python setup_cron.py")


def run_now() -> None:
    """Trigger an immediate pipeline run via Task Scheduler."""
    result = _schtasks("/Run", "/TN", TASK_NAME)
    if result.returncode == 0:
        print(f"✅ Task '{TASK_NAME}' triggered. Check the log in a moment:")
        print(f"   {LOG_FILE}")
    else:
        print(f"❌ Could not trigger task: {result.stderr or result.stdout}")


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Register the RSS pipeline as a Windows scheduled task.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            Examples:
              python setup_cron.py                  Register task (every 60 min)
              python setup_cron.py --interval 30    Register task (every 30 min)
              python setup_cron.py --status         Show task info + recent logs
              python setup_cron.py --run-now        Trigger immediately
              python setup_cron.py --remove         Delete the task
        """),
    )
    parser.add_argument(
        "--interval", type=int, default=60,
        help="How often to run the pipeline in minutes (default: 60)",
    )
    parser.add_argument("--remove",  action="store_true", help="Remove the task")
    parser.add_argument("--status",  action="store_true", help="Show task status")
    parser.add_argument("--run-now", action="store_true", help="Trigger immediately")
    args = parser.parse_args()

    if args.remove:
        remove_task()
    elif args.status:
        show_status()
    elif args.run_now:
        run_now()
    else:
        register_task(args.interval)
