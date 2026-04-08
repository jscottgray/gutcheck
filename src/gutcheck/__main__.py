"""gutcheck CLI — adversarial worldview audit.

Reads political positions from a file path, stdin, or $EDITOR. Runs three
parallel Claude Agent SDK queries with WebSearch enabled. Each hunter
focuses on one angle:

  - challenge: where the data contradicts the user's claims
  - support:   where the data backs them up
  - crossover: where they unknowingly agree with the other side

Results are rendered to the terminal as markdown via Rich, in the order
they finish (not in fixed order). Uses your existing Claude Code
subscription auth via the bundled `claude` CLI.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from .prompts import HUNT_PROMPTS, SECTION_LABELS, SECTION_ORDER, HuntMode

console = Console()

INPUT_TEMPLATE = """\
# Paste your political positions below this line.
# Lines starting with # are ignored.
# Save and quit when done. Quit without saving to abort.
#
# Be specific. Vague positions ("immigration is bad") give vague answers.
# Concrete positions ("Biden has been too lenient on the southern border, the
# numbers prove it") give checkable answers.
#
# Examples of good positions:
#   - "The US economy under Biden was actually strong despite the vibes."
#   - "Trump's tariffs hurt American consumers more than they hurt China."
#   - "The mainstream media systematically downplays violent crime stats."
#
# You can write as many or as few as you want. The more specific, the
# sharper the audit will be.
#
# ---


"""


def get_positions_from_editor() -> str:
    """Open $EDITOR with a template, return whatever the user wrote."""
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    with tempfile.NamedTemporaryFile(
        mode="w+", suffix=".md", prefix="gutcheck-", delete=False
    ) as f:
        f.write(INPUT_TEMPLATE)
        path = f.name
    try:
        subprocess.run([editor, path], check=True)
        with open(path) as f:
            raw = f.read()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass

    # Strip comment lines and the template header
    lines = []
    for line in raw.splitlines():
        if line.lstrip().startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def get_positions(args: argparse.Namespace) -> str:
    """Resolve positions input from arg, stdin, or editor."""
    if args.file:
        with open(args.file) as f:
            return f.read().strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return get_positions_from_editor()


async def run_hunter(
    mode: HuntMode, positions: str
) -> tuple[HuntMode, str, str | None]:
    """Run one hunter agent end-to-end.

    Returns (mode, text, error). On success, error is None and text is the
    rendered markdown. On failure, error is the message and text is empty.
    Never raises — keeps the as_completed loop simple.

    Imports are deferred so that --help and the input-collection phase don't
    pay the SDK's import cost (which spawns a subprocess to find the CLI).
    """
    try:
        from claude_agent_sdk import (  # type: ignore[import-not-found]
            AssistantMessage,
            ClaudeAgentOptions,
            TextBlock,
            query,
        )

        options = ClaudeAgentOptions(
            system_prompt=HUNT_PROMPTS[mode],
            allowed_tools=["WebSearch", "WebFetch"],
        )

        parts: list[str] = []
        async for message in query(prompt=positions, options=options):
            if isinstance(message, AssistantMessage):
                for block in message.content:
                    if isinstance(block, TextBlock):
                        parts.append(block.text)
        return mode, "".join(parts).strip(), None
    except Exception as exc:
        return mode, "", f"{type(exc).__name__}: {exc}"


def render_section(mode: HuntMode, text: str) -> None:
    """Print one finished section to the terminal as a Rich panel."""
    label = SECTION_LABELS[mode]
    console.print()
    console.print(Rule(f"[bold]{label}[/bold]", style="cyan"))
    console.print()
    if not text:
        console.print(
            "[dim italic]No output returned from this hunter.[/dim italic]"
        )
    else:
        console.print(Markdown(text))
    console.print()


async def run_audit(positions: str) -> None:
    console.print()
    console.print(
        Panel.fit(
            Text("gutcheck", style="bold cyan", justify="center")
            + Text("\nAdversarial worldview audit", style="dim", justify="center"),
            border_style="cyan",
        )
    )
    console.print()
    console.print(
        f"[dim]Auditing {len(positions)} chars of positions across "
        f"{len(SECTION_ORDER)} parallel hunters. Each one searches the web — "
        f"this can take 30-90 seconds per hunter. Results print as they "
        f"complete.[/dim]"
    )
    console.print()

    tasks: list[asyncio.Task[tuple[HuntMode, str, str | None]]] = []
    for mode in SECTION_ORDER:
        tasks.append(asyncio.create_task(run_hunter(mode, positions)))
        console.print(f"  [cyan]>[/cyan] launched [bold]{mode}[/bold] hunter")
    console.print()

    total = len(tasks)
    completed = 0
    failures: list[tuple[HuntMode, str]] = []

    for coro in asyncio.as_completed(tasks):
        mode, text, error = await coro
        completed += 1
        if error:
            failures.append((mode, error))
            console.print(
                f"[red]✗[/red] [bold]{mode}[/bold] failed "
                f"([dim]{completed}/{total}[/dim]): {error}"
            )
        else:
            console.print(
                f"[green]✓[/green] [bold]{mode}[/bold] complete "
                f"([dim]{completed}/{total}[/dim])"
            )
            render_section(mode, text)

    console.print()
    console.print(Rule("[bold]audit complete[/bold]", style="cyan"))
    console.print()
    if failures:
        for mode, err in failures:
            console.print(
                f"[yellow]{mode} hunter errored: {err}[/yellow]"
            )
        console.print(
            "[yellow]This is usually a transient API issue — try again.[/yellow]"
        )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="gutcheck",
        description=(
            "Adversarial worldview audit. Paste your political positions, "
            "get back where the data backs you up, where it contradicts you, "
            "and where you quietly agree with the people you think you "
            "disagree with."
        ),
    )
    p.add_argument(
        "file",
        nargs="?",
        help="Path to a file containing your positions. Omit to use stdin or $EDITOR.",
    )
    return p.parse_args()


def run() -> None:
    """Synchronous entry point used by the [project.scripts] table."""
    args = parse_args()
    positions = get_positions(args)
    if not positions:
        console.print(
            "[red]No positions provided.[/red] "
            "Pass a file path, pipe text on stdin, or write something in the editor."
        )
        sys.exit(1)
    try:
        asyncio.run(run_audit(positions))
    except KeyboardInterrupt:
        console.print("\n[yellow]Aborted.[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    run()
