"""
manage_sources.py
------------------
Command-line tool to manage RSS sources stored in the database.
No config file editing needed.

Usage:
    python manage_sources.py list                          # list all sources
    python manage_sources.py add <name> <url> [category]  # add new source
    python manage_sources.py disable <id>                  # pause a source
    python manage_sources.py enable  <id>                  # resume a source
    python manage_sources.py remove  <id>                  # permanently delete
"""

import sys
sys.path.insert(0, ".")

import logging
logging.disable(logging.CRITICAL)

from rich.console import Console
from rich.table import Table
from rich import box
from rich.panel import Panel

from db.connection import (
    init_pool, seed_sources,
    list_all_sources, add_source, remove_source, toggle_source,
    SemanticDuplicateError,
)

console = Console()


def cmd_list():
    sources = list_all_sources()
    if not sources:
        console.print("[yellow]No sources in database. Run the pipeline once to seed from config.[/]")
        return

    table = Table(box=box.ROUNDED, header_style="bold cyan", border_style="bright_blue", expand=True)
    table.add_column("ID",        justify="right",  style="dim",         min_width=4)
    table.add_column("Name",      style="white",                         min_width=22)
    table.add_column("Category",  style="cyan",                          min_width=12)
    table.add_column("Active",    justify="center",                      min_width=8)
    table.add_column("Last Fetched",                                     min_width=20)
    table.add_column("URL",       style="dim",                           min_width=30)

    for s in sources:
        active_text = "[green]YES[/]" if s["is_active"] else "[red]NO[/]"
        fetched = str(s["last_fetched_at"])[:19] if s["last_fetched_at"] else "[dim]never[/]"
        table.add_row(
            str(s["id"]),
            s["name"],
            s["category"],
            active_text,
            fetched,
            s["url"],
        )

    active_count = sum(1 for s in sources if s["is_active"])
    console.print()
    console.print(Panel(
        table,
        title=f"[bold cyan] RSS Sources in Database  ({active_count}/{len(sources)} active)",
        border_style="bright_blue",
    ))
    console.print()
    console.print("[dim]To manage:[/]")
    console.print("  [cyan]python manage_sources.py add    <name> <url> [category][/]")
    console.print("  [cyan]python manage_sources.py disable <id>[/]")
    console.print("  [cyan]python manage_sources.py enable  <id>[/]")
    console.print("  [cyan]python manage_sources.py remove  <id>[/]")
    console.print()


def cmd_add(args):
    if len(args) < 2:
        console.print("[red]Usage: python manage_sources.py add <name> <url> [category][/]")
        console.print("[dim]Example: python manage_sources.py add \"Hacker News\" https://hnrss.org/frontpage Technology[/]")
        sys.exit(1)

    name     = args[0]
    url      = args[1]
    category = args[2] if len(args) > 2 else "General"

    with console.status("[cyan]Checking for semantic duplicates…[/]"):
        try:
            new_id = add_source(name, url, category)

        except SemanticDuplicateError as dup:
            m = dup.match
            sim_pct = f"{m['similarity'] * 100:.1f}%"
            console.print()
            console.print(Panel(
                f"[bold red]Source NOT added — semantic duplicate detected[/]\n\n"
                f"  The source you tried to add is too similar to an existing one:\n\n"
                f"  [bold]Existing source[/]\n"
                f"    ID       : [cyan]{m['id']}[/]\n"
                f"    Name     : [yellow]{m['name']}[/]\n"
                f"    URL      : [dim]{m['url']}[/]\n"
                f"    Category : {m['category']}\n\n"
                f"  [bold]Similarity score[/] : [red]{sim_pct}[/] "
                f"(threshold: {int(__import__('config.settings', fromlist=['VECTOR_SIM_THRESHOLD']).VECTOR_SIM_THRESHOLD * 100)}%)\n\n"
                f"  [dim]If you still want to add this source, first remove or disable\n"
                f"  the existing one, or lower VECTOR_SIM_THRESHOLD in your .env.[/]",
                title="[bold red] ✗  Duplicate Source Rejected",
                border_style="red",
            ))
            console.print()
            sys.exit(1)

        except Exception as exc:
            console.print(f"[red]Failed to add source: {exc}[/]")
            sys.exit(1)

    console.print(f"\n[green]Source added successfully![/]")
    console.print(f"  ID       : [cyan]{new_id}[/]")
    console.print(f"  Name     : {name}")
    console.print(f"  URL      : {url}")
    console.print(f"  Category : {category}")
    console.print(f"\n[dim]It will be fetched on the next pipeline run.[/]\n")


def cmd_toggle(source_id: int, active: bool):
    action = "Enabled" if active else "Disabled"
    try:
        found = toggle_source(source_id, active)
        if found:
            status = "[green]active[/]" if active else "[red]inactive[/]"
            console.print(f"\n[green]{action}[/] source ID {source_id} — now {status}\n")
        else:
            console.print(f"[yellow]No source found with ID {source_id}[/]")
            sys.exit(1)
    except Exception as exc:
        console.print(f"[red]Failed to toggle source: {exc}[/]")
        sys.exit(1)


def cmd_run(arg: str):
    """Fetch a single feed from DB by ID or name and show live output."""
    sources = list_all_sources()

    if arg.isdigit():
        match = [s for s in sources if s["id"] == int(arg)]
    else:
        match = [s for s in sources if arg.lower() in s["name"].lower()]

    if not match:
        console.print(f"[red]No source found matching: '{arg}'[/]")
        console.print("[dim]Run: python manage_sources.py list   to see IDs and names[/]")
        return

    src = match[0]
    if not src["is_active"]:
        console.print(f"[yellow]Source [{src['id']}] {src['name']} is DISABLED.[/]")
        console.print(f"[dim]Enable it first: python manage_sources.py enable {src['id']}[/]")
        return

    console.print()
    console.print(Panel(
        f"[bold cyan]Running single feed from DB[/]\n"
        f"  ID       : {src['id']}\n"
        f"  Name     : {src['name']}\n"
        f"  Category : {src['category']}\n"
        f"  URL      : {src['url']}",
        border_style="bright_blue", padding=(0, 2),
    ))

    import time
    from pipeline.rss_collector import collect_feed

    with console.status(f"[cyan]Fetching {src['name']}...[/]"):
        t = time.perf_counter()
        articles = collect_feed(src)
        elapsed  = time.perf_counter() - t

    table = Table(box=box.ROUNDED, header_style="bold cyan",
                  border_style="bright_blue", expand=True)
    table.add_column("#",       justify="right", style="dim",   min_width=3)
    table.add_column("Title",   style="white",                  min_width=50)
    table.add_column("Author",  style="dim cyan",               min_width=16)
    table.add_column("Published", style="dim",                  min_width=12)

    for i, a in enumerate(articles, 1):
        pub = str(a.get("published", "") or "")[:10]
        table.add_row(str(i), a["title"][:80], a.get("author", "")[:20], pub)

    console.print(Panel(
        table,
        title=f"[bold green] {src['name']} — {len(articles)} articles in {elapsed:.2f}s",
        border_style="green",
    ))
    console.print()


def cmd_remove(source_id: int):
    # Show what we're about to delete
    sources = list_all_sources()
    target = next((s for s in sources if s["id"] == source_id), None)
    if not target:
        console.print(f"[yellow]No source found with ID {source_id}[/]")
        sys.exit(1)

    console.print(f"\n[yellow]About to permanently delete:[/]")
    console.print(f"  ID   : {target['id']}")
    console.print(f"  Name : {target['name']}")
    console.print(f"  URL  : {target['url']}")
    console.print("\n[dim]This will NOT delete articles already collected from this source.[/]")
    confirm = input("\nType 'yes' to confirm: ").strip().lower()

    if confirm != "yes":
        console.print("[dim]Cancelled.[/]")
        return

    try:
        remove_source(source_id)
        console.print(f"[green]Source {source_id} removed.[/]\n")
    except Exception as exc:
        console.print(f"[red]Failed to remove: {exc}[/]")
        sys.exit(1)


# ── Entry point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_pool()
    seed_sources()   # ensure DB has base sources

    args = sys.argv[1:]

    if not args or args[0] == "list":
        cmd_list()

    elif args[0] == "add":
        cmd_add(args[1:])

    elif args[0] == "run" and len(args) >= 2:
        cmd_run(args[1])

    elif args[0] == "enable" and len(args) >= 2:
        cmd_toggle(int(args[1]), active=True)

    elif args[0] == "disable" and len(args) >= 2:
        cmd_toggle(int(args[1]), active=False)

    elif args[0] == "remove" and len(args) >= 2:
        cmd_remove(int(args[1]))

    else:
        console.print(Panel(
            "[bold]Usage:[/]\n\n"
            "  [cyan]python manage_sources.py list[/]                           List all sources\n"
            "  [cyan]python manage_sources.py run  <id or name>[/]              Run ONE feed from DB\n"
            "  [cyan]python manage_sources.py add <name> <url> [category][/]    Add new source\n"
            "  [cyan]python manage_sources.py enable  <id>[/]                   Enable a source\n"
            "  [cyan]python manage_sources.py disable <id>[/]                   Disable a source\n"
            "  [cyan]python manage_sources.py remove  <id>[/]                   Delete a source",
            title="[bold cyan] RSS Source Manager",
            border_style="bright_blue",
        ))
