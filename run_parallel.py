"""
run_parallel.py
----------------
Runs the full RSS pipeline with a LIVE visual dashboard.

The dashboard uses the REAL collect_feed() from rss_collector.py, so
feeds are fetched ONCE — no double-fetching. Article dicts collected
by the dashboard threads flow directly into the extraction stage.

Sources are loaded from the DATABASE — not from hardcoded config.

Run:
    python run_parallel.py          # full pipeline with live dashboard
    python run_parallel.py --feeds  # feed collection only (fast demo ~3s)
"""

import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, ".")

from rich.console import Console
from rich.table import Table
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich import box

# ── Suppress pipeline logger output — dashboard shows status ──────────────────
import logging
logging.disable(logging.CRITICAL)

# ── Project imports ────────────────────────────────────────────────────────────
from utils.logger import setup_logging
setup_logging()
logging.disable(logging.CRITICAL)

from db.connection import init_pool, seed_sources, get_all_sources
from pipeline.rss_collector import collect_feed   # real collector

console = Console()

# ── Shared dashboard state (thread-safe) ──────────────────────────────────────
_lock = threading.Lock()

STATUS_WAITING  = "WAITING"
STATUS_FETCHING = "FETCHING"
STATUS_DONE     = "DONE"
STATUS_ERROR    = "ERROR"

feed_states: dict[str, dict] = {}


# ──────────────────────────────────────────────────────────────────────────────
# Worker — wraps real collect_feed(), updates dashboard state
# ──────────────────────────────────────────────────────────────────────────────

def fetch_with_ui(source: dict) -> list[dict]:
    """
    Calls the real collect_feed() and updates the live dashboard state.
    Returns the actual list of article dicts (used by later pipeline stages).
    """
    name   = source["name"]
    thread = threading.current_thread().name

    with _lock:
        feed_states[name]["status"] = STATUS_FETCHING
        feed_states[name]["thread"] = thread

    t_start = time.perf_counter()
    try:
        articles = collect_feed(source)   # <-- real pipeline function
        status   = STATUS_DONE
    except Exception:
        articles = []
        status   = STATUS_ERROR

    elapsed = time.perf_counter() - t_start

    with _lock:
        feed_states[name]["status"]  = status
        feed_states[name]["entries"] = len(articles)
        feed_states[name]["elapsed"] = elapsed

    return articles


# ──────────────────────────────────────────────────────────────────────────────
# Live table builder
# ──────────────────────────────────────────────────────────────────────────────

def _build_table(elapsed_total: float) -> Panel:
    table = Table(
        box=box.ROUNDED,
        show_header=True,
        header_style="bold cyan",
        border_style="bright_blue",
        expand=True,
    )
    table.add_column("ID",        justify="right",  style="dim",       min_width=4)
    table.add_column("Feed",      style="white",                       min_width=22)
    table.add_column("Category",  style="dim cyan",                    min_width=12)
    table.add_column("Status",    justify="center",                    min_width=10)
    table.add_column("Articles",  justify="right",                     min_width=9)
    table.add_column("Time (s)",  justify="right",                     min_width=9)
    table.add_column("Thread",    style="dim",                         min_width=24)

    total_articles = 0
    done_count     = 0

    for sid, (name, state) in enumerate(feed_states.items(), 1):
        st = state["status"]

        if st == STATUS_FETCHING:
            tick = ">" * (int(time.time() * 5) % 5 + 1)
            label = Text(f"  {tick:<5}  ", style="bold yellow")
            name_text = Text(name, style="bold yellow")
        elif st == STATUS_DONE:
            label     = Text("  DONE   ", style="bold green")
            name_text = Text(name, style="bold green")
            done_count += 1
            total_articles += state.get("entries", 0)
        elif st == STATUS_ERROR:
            label     = Text("  ERROR  ", style="bold red")
            name_text = Text(name, style="bold red")
            done_count += 1
        else:
            label     = Text("   ...   ", style="dim white")
            name_text = Text(name, style="dim white")

        entries = str(state["entries"]) if isinstance(state.get("entries"), int) else "-"
        elapsed = f"{state['elapsed']:.2f}" if state.get("elapsed") is not None else "-"
        thread  = state.get("thread", "")

        table.add_row(
            str(state.get("id", "")),
            name_text,
            state.get("category", ""),
            label,
            entries,
            elapsed,
            thread,
        )

    total_count = len(feed_states)
    title = (
        f"[bold cyan] RSS Collector — DB Sources[/]  [dim]|[/]  "
        f"[green]{done_count}[/][dim]/{total_count}[/]  "
        f"[dim]|[/]  [yellow]{elapsed_total:.1f}s[/]  "
        f"[dim]|[/]  [white]{total_articles} articles[/]"
    )
    return Panel(table, title=title, border_style="bright_blue", padding=(0, 1))


# ──────────────────────────────────────────────────────────────────────────────
# Parallel collection with live dashboard
# ──────────────────────────────────────────────────────────────────────────────

def run_parallel_collection(sources: list[dict]) -> list[dict]:
    """
    Fetch all RSS feeds in parallel with a live dashboard.
    Sources come from the database — not from config.
    Returns the combined list of raw article dicts.
    """
    for src in sources:
        feed_states[src["name"]] = {
            "id":       src["id"],
            "status":   STATUS_WAITING,
            "category": src["category"],
            "entries":  None,
            "elapsed":  None,
            "thread":   "",
        }

    n_workers    = min(8, len(sources))
    all_articles : list[dict] = []
    t_start      = time.perf_counter()

    console.print()
    console.print(Panel(
        f"[bold cyan]Sources loaded from DB — {len(sources)} active feeds[/]\n"
        f"[dim]Fetching all {len(sources)} feeds simultaneously with {n_workers} threads.[/]\n"
        f"[dim]No hardcoded config used at runtime.[/]",
        border_style="bright_blue",
        padding=(0, 2),
    ))

    with Live(_build_table(0), refresh_per_second=10, console=console) as live:
        with ThreadPoolExecutor(max_workers=n_workers) as executor:
            future_to_source = {
                executor.submit(fetch_with_ui, src): src
                for src in sources
            }

            pending = set(future_to_source.keys())
            while pending:
                done_now = {f for f in pending if f.done()}
                for f in done_now:
                    articles = f.result()
                    with _lock:
                        all_articles.extend(articles)
                    pending.discard(f)

                live.update(_build_table(time.perf_counter() - t_start))
                if pending:
                    time.sleep(0.05)

            live.update(_build_table(time.perf_counter() - t_start))
            time.sleep(0.4)

    total_elapsed = time.perf_counter() - t_start

    console.print()
    console.print(Panel(
        f"[bold green]Collection complete![/]\n\n"
        f"  [cyan]Active sources (from DB) :[/]  {len(sources)}\n"
        f"  [cyan]Total articles collected  :[/]  {len(all_articles)}\n"
        f"  [cyan]Total time                :[/]  {total_elapsed:.2f}s\n"
        f"  [cyan]Worker threads            :[/]  {n_workers}\n"
        f"  [cyan]Avg per feed              :[/]  {total_elapsed/len(sources):.2f}s",
        title="[bold green] Stage 1 Complete",
        border_style="green",
        padding=(0, 2),
    ))

    return all_articles


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    feeds_only  = "--feeds"  in sys.argv

    # --source <id or name>  →  run only that one feed from DB
    source_filter = None
    if "--source" in sys.argv:
        idx = sys.argv.index("--source")
        if idx + 1 < len(sys.argv):
            source_filter = sys.argv[idx + 1]

    console.rule("[bold cyan] RSS Parallel Pipeline  (DB-driven sources)")
    console.print()

    with console.status("[cyan]Connecting to database...[/]"):
        init_pool()
        seed_sources()
        all_sources = get_all_sources()   # load all active from DB

    # ── Filter to a single source if --source was given ───────────────────────
    if source_filter:
        # Match by numeric ID or by name (case-insensitive substring)
        if source_filter.isdigit():
            sources = [s for s in all_sources if s["id"] == int(source_filter)]
        else:
            sources = [s for s in all_sources
                       if source_filter.lower() in s["name"].lower()]

        if not sources:
            console.print(f"[red]No active source found matching: '{source_filter}'[/]")
            console.print("[dim]Run: python manage_sources.py list   to see available IDs and names[/]")
            sys.exit(1)

        console.print(
            f"[green]Filtering to 1 source:[/]  "
            f"[cyan][{sources[0]['id']}] {sources[0]['name']}[/]  "
            f"[dim]({sources[0]['url'][:60]})[/]"
        )
    else:
        sources = all_sources
        console.print(
            f"[green]DB connected.[/]  "
            f"[dim]{len(sources)} active sources loaded from [bold]rss_sources[/] table.[/]"
        )

    # Stage 1: Parallel RSS collection with live dashboard
    raw_articles = run_parallel_collection(sources)

    if feeds_only:
        console.print("\n[dim]--feeds flag: exiting after Stage 1.[/]\n")
        sys.exit(0)

    # ── Stage 2: Article extraction ───────────────────────────────────────────
    logging.disable(logging.NOTSET)   # re-enable logs for extraction stage

    console.print()
    console.rule("[bold cyan] Stage 2 — Article Extraction (Playwright + newspaper4k)")

    from pipeline.article_extractor import extract_all
    from pipeline.data_cleaner import clean
    from db.connection import (
        bulk_insert_articles, create_pipeline_run, finish_pipeline_run
    )

    run_id = create_pipeline_run()

    with console.status("[cyan]Extracting full article text...[/]"):
        enriched = extract_all(raw_articles)

    with console.status("[cyan]Cleaning and validating data...[/]"):
        clean_articles = clean(enriched)

    with console.status("[cyan]Inserting into PostgreSQL...[/]"):
        inserted, skipped = bulk_insert_articles(clean_articles)

    finish_pipeline_run(run_id, len(raw_articles), inserted, skipped, 0, "success")

    console.print()
    console.print(Panel(
        f"[bold green]Pipeline complete![/]\n\n"
        f"  [cyan]Articles found    :[/]  {len(raw_articles)}\n"
        f"  [cyan]Articles inserted :[/]  [bold green]{inserted}[/]\n"
        f"  [cyan]Duplicates skipped:[/]  [dim]{skipped}[/]\n\n"
        f"  [dim]Sources are managed in the DB — use:[/]\n"
        f"  [cyan]python manage_sources.py list[/]  to view/edit sources",
        title="[bold green] Pipeline Complete",
        border_style="green",
        padding=(0, 2),
    ))
