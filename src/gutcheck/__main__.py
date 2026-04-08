"""gutcheck CLI — conversation-first political argument audit.

Interactive terminal usage starts with an argument-coaching pass that helps
the user turn a rough thought into concrete, fact-checkable claims. The audit
step then runs three parallel Codex CLI queries with web search enabled:

  - challenge: where the data contradicts the user's claims
  - support:   where the data backs them up
  - crossover: where they unknowingly agree with the other side

Prepared input can still come from a file path, stdin, or $EDITOR.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from xml.sax.saxutils import escape

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from .prompts import (
    ARGUMENT_COACH_QUESTIONER,
    ARGUMENT_COACH_SYNTHESIZER,
    HUNT_PROMPTS,
    SECTION_LABELS,
    SECTION_ORDER,
    HuntMode,
)

console = Console()
CODEX_MODEL = os.environ.get("GUTCHECK_CODEX_MODEL")
DEFAULT_REPORT_DIR = "gutcheck-reports"

CLARIFICATION_PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "options": {
                        "type": "array",
                        "items": {"type": "string"},
                    },
                },
                "required": ["question", "options"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "questions"],
    "additionalProperties": False,
}

AUDIT_DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "framing": {"type": "string"},
        "audit_input": {"type": "string"},
    },
    "required": ["framing", "audit_input"],
    "additionalProperties": False,
}

INPUT_TEMPLATE = """\
# Paste your political positions below this line.
# Lines starting with # are ignored.
# Save and quit when done. Quit without saving to abort.
#
# Be specific. Vague positions ("immigration is bad") give vague answers.
# Concrete positions ("Border encounters rose under Biden and that drove up
# shelter costs in major cities") give checkable answers.
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

EDIT_TEMPLATE = """\
# Edit the tightened claims below.
# Lines starting with # are ignored.
# Save and quit when done. Quit without saving to keep the current draft.
#
# Keep the wording concrete and fact-checkable.
#
# ---

{draft}
"""


@dataclass
class ClarificationQuestion:
    question: str
    options: list[str]


@dataclass
class ClarificationPlan:
    summary: str
    questions: list[ClarificationQuestion]


@dataclass
class AuditDraft:
    framing: str
    audit_input: str


def render_banner(title: str, subtitle: str) -> None:
    console.print()
    console.print(
        Panel.fit(
            Text("gutcheck", style="bold cyan", justify="center")
            + Text(f"\n{title}", style="bold", justify="center")
            + Text(f"\n{subtitle}", style="dim", justify="center"),
            border_style="cyan",
        )
    )
    console.print()


def strip_comment_lines(raw: str) -> str:
    lines = []
    for line in raw.splitlines():
        if line.lstrip().startswith("#"):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def get_positions_from_editor(template: str = INPUT_TEMPLATE) -> str:
    """Open $EDITOR with a template, return whatever the user wrote."""
    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "vi"
    with tempfile.NamedTemporaryFile(
        mode="w+", suffix=".md", prefix="gutcheck-", delete=False
    ) as f:
        f.write(template)
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

    return strip_comment_lines(raw)


def collect_rough_thought() -> str:
    console.print("[bold]Start with a rough thought[/bold]")
    console.print(
        "[dim]Paste a sentence or short paragraph. Press Enter on an empty "
        "line when you're done.[/dim]"
    )
    console.print()

    lines: list[str] = []
    while True:
        prompt = "[cyan]thought[/cyan] [dim]>[/dim] " if not lines else "[cyan]...[/cyan] [dim]>[/dim] "
        try:
            line = console.input(prompt)
        except EOFError:
            break
        if not line.strip():
            if lines:
                break
            continue
        lines.append(line)

    return "\n".join(lines).strip()


def run_codex_exec(
    prompt: str,
    *,
    system_prompt: str,
    search: bool = False,
    output_schema: dict[str, object] | None = None,
) -> str:
    """Run Codex CLI non-interactively and return the last assistant message."""
    output_path: str | None = None
    schema_path: str | None = None

    try:
        with tempfile.NamedTemporaryFile(
            mode="w+", suffix=".txt", prefix="gutcheck-codex-", delete=False
        ) as output_file:
            output_path = output_file.name

        cmd = [
            "codex",
        ]
        if search:
            cmd.append("--search")
        cmd.extend(
            [
                "exec",
                "-s",
                "read-only",
                "--color",
                "never",
                "--output-last-message",
                output_path,
            ]
        )
        if CODEX_MODEL:
            cmd.extend(["-m", CODEX_MODEL])
        if output_schema is not None:
            with tempfile.NamedTemporaryFile(
                mode="w+", suffix=".json", prefix="gutcheck-schema-", delete=False
            ) as schema_file:
                json.dump(output_schema, schema_file)
                schema_file.flush()
                schema_path = schema_file.name
            cmd.extend(["--output-schema", schema_path])

        full_prompt = f"{system_prompt}\n\nUser input:\n{prompt}"
        result = subprocess.run(
            cmd + [full_prompt],
            capture_output=True,
            text=True,
            check=False,
            cwd=os.getcwd(),
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            stdout = result.stdout.strip()
            detail = stderr or stdout or "Unknown codex exec failure."
            raise RuntimeError(detail)

        response = pathlib.Path(output_path).read_text().strip()
        if not response:
            response = result.stdout.strip()
        if not response:
            raise RuntimeError("Codex returned an empty response.")
        return response
    except FileNotFoundError as exc:
        raise RuntimeError(
            "The `codex` CLI is not installed or not on PATH. Install it and run "
            "`codex login` first."
        ) from exc
    finally:
        for path in (output_path, schema_path):
            if not path:
                continue
            try:
                os.unlink(path)
            except OSError:
                pass


async def query_text(
    prompt: str,
    system_prompt: str,
    *,
    search: bool = False,
    output_schema: dict[str, object] | None = None,
) -> str:
    """Run a single Codex CLI query and return the assistant text."""
    return await asyncio.to_thread(
        run_codex_exec,
        prompt,
        system_prompt=system_prompt,
        search=search,
        output_schema=output_schema,
    )


def extract_json_blob(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        if lines:
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        cleaned = "\n".join(lines).strip()

    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("No JSON object found in model response.")
    return cleaned[start : end + 1]


def parse_clarification_plan(text: str) -> ClarificationPlan:
    payload = json.loads(extract_json_blob(text))
    summary = str(payload["summary"]).strip()
    raw_questions = payload["questions"]
    if not isinstance(raw_questions, list) or not raw_questions:
        raise ValueError("Clarification response did not include any questions.")

    questions: list[ClarificationQuestion] = []
    for item in raw_questions[:4]:
        if not isinstance(item, dict):
            continue
        question = str(item.get("question", "")).strip()
        raw_options = item.get("options", [])
        if not question or not isinstance(raw_options, list):
            continue
        options = [str(option).strip() for option in raw_options if str(option).strip()]
        if not options:
            continue
        if "something else" not in options[-1].lower():
            options.append("Something else")
        questions.append(ClarificationQuestion(question=question, options=options))

    if not summary or not questions:
        raise ValueError("Clarification response was missing required fields.")
    return ClarificationPlan(summary=summary, questions=questions)


def parse_audit_draft(text: str) -> AuditDraft:
    payload = json.loads(extract_json_blob(text))
    framing = str(payload["framing"]).strip()
    audit_input = str(payload["audit_input"]).strip()
    if not framing or not audit_input:
        raise ValueError("Audit draft response was missing required fields.")
    return AuditDraft(framing=framing, audit_input=audit_input)


async def build_clarification_plan(rough_thought: str) -> ClarificationPlan:
    response = await query_text(
        prompt=rough_thought,
        system_prompt=ARGUMENT_COACH_QUESTIONER,
        output_schema=CLARIFICATION_PLAN_SCHEMA,
    )
    return parse_clarification_plan(response)


def ask_clarifying_questions(
    questions: list[ClarificationQuestion],
) -> list[tuple[str, str]]:
    answers: list[tuple[str, str]] = []
    for index, item in enumerate(questions, start=1):
        console.print()
        console.print(f"[bold]{index}. {item.question}[/bold]")
        for option_index, option in enumerate(item.options, start=1):
            console.print(f"  [cyan]{option_index}[/cyan]. {option}")

        while True:
            raw = console.input(
                "[cyan]answer[/cyan] [dim](type a number or your own text)[/dim] > "
            ).strip()
            if not raw:
                continue
            if raw.isdigit():
                chosen = int(raw)
                if 1 <= chosen <= len(item.options):
                    answers.append((item.question, item.options[chosen - 1]))
                    break
            answers.append((item.question, raw))
            break

    return answers


def format_clarification_context(
    rough_thought: str, answers: list[tuple[str, str]]
) -> str:
    lines = [f"Original thought:\n{rough_thought}", "", "Clarifying answers:"]
    for question, answer in answers:
        lines.append(f"- {question}")
        lines.append(f"  Answer: {answer}")
    return "\n".join(lines).strip()


async def build_audit_draft(
    rough_thought: str, answers: list[tuple[str, str]]
) -> AuditDraft:
    response = await query_text(
        prompt=format_clarification_context(rough_thought, answers),
        system_prompt=ARGUMENT_COACH_SYNTHESIZER,
        output_schema=AUDIT_DRAFT_SCHEMA,
    )
    return parse_audit_draft(response)


def maybe_edit_draft(draft: AuditDraft) -> str | None:
    console.print()
    console.print(Rule("[bold]Tightened framing[/bold]", style="cyan"))
    console.print()
    console.print(Markdown(draft.framing))
    console.print()
    console.print(Markdown(draft.audit_input))
    console.print()

    choice = console.input(
        "[cyan]Proceed?[/cyan] [dim][Enter=continue, e=edit, a=abort][/dim] "
    ).strip().lower()
    if choice == "a":
        return None
    if choice == "e":
        edited = get_positions_from_editor(
            EDIT_TEMPLATE.format(draft=draft.audit_input.rstrip() + "\n")
        )
        return edited or draft.audit_input
    return draft.audit_input


def build_audit_context(
    rough_thought: str, answers: list[tuple[str, str]], audit_input: str
) -> str:
    answer_lines = []
    for question, answer in answers:
        answer_lines.append(f"- {question}")
        answer_lines.append(f"  {answer}")

    return "\n".join(
        [
            "Original rough thought:",
            rough_thought,
            "",
            "Clarifications:",
            "\n".join(answer_lines),
            "",
            "Tightened claims to audit:",
            audit_input,
        ]
    ).strip()


async def get_positions_from_conversation() -> str:
    render_banner("Argument coach", "Start rough. gutcheck will tighten it up first.")
    rough_thought = collect_rough_thought()
    if not rough_thought:
        return ""

    try:
        with console.status("[cyan]Finding the real disagreement...[/cyan]"):
            plan = await build_clarification_plan(rough_thought)
    except Exception as exc:
        console.print(
            f"[yellow]Argument coach failed ({type(exc).__name__}: {exc}). "
            "Falling back to your original wording.[/yellow]"
        )
        return rough_thought

    console.print()
    console.print(Rule("[bold]What you seem to mean[/bold]", style="cyan"))
    console.print()
    console.print(plan.summary)
    console.print()

    answers = ask_clarifying_questions(plan.questions)

    try:
        with console.status("[cyan]Tightening your argument...[/cyan]"):
            draft = await build_audit_draft(rough_thought, answers)
    except Exception as exc:
        console.print(
            f"[yellow]Could not synthesize a tightened draft "
            f"({type(exc).__name__}: {exc}). Using your original wording with "
            "the clarifying answers attached.[/yellow]"
        )
        return build_audit_context(rough_thought, answers, rough_thought)

    finalized = maybe_edit_draft(draft)
    if finalized is None:
        raise KeyboardInterrupt

    return build_audit_context(rough_thought, answers, finalized)


async def get_positions(args: argparse.Namespace) -> str:
    """Resolve positions input from arg, stdin, editor, or conversation."""
    if args.file:
        with open(args.file) as f:
            return f.read().strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    if args.editor:
        return get_positions_from_editor()
    return await get_positions_from_conversation()


async def run_hunter(
    mode: HuntMode, positions: str
) -> tuple[HuntMode, str, str | None]:
    """Run one hunter agent end-to-end."""
    try:
        text = await query_text(
            prompt=positions,
            system_prompt=HUNT_PROMPTS[mode],
            search=True,
        )
        return mode, text, None
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


def build_report_markdown(
    positions: str,
    sections: dict[HuntMode, str],
    failures: list[tuple[HuntMode, str]],
) -> str:
    parts = [
        "# gutcheck report",
        "",
        "## Audit Input",
        "",
        "```text",
        positions,
        "```",
        "",
    ]

    for mode in SECTION_ORDER:
        parts.append(f"## {SECTION_LABELS[mode]}")
        parts.append("")
        if mode in sections and sections[mode].strip():
            parts.append(sections[mode].strip())
        else:
            error = next((message for failed_mode, message in failures if failed_mode == mode), None)
            if error:
                parts.append(f"_Hunter failed: {error}_")
            else:
                parts.append("_No output returned from this hunter._")
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def slugify_report_name(text: str, max_length: int = 64) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not slug:
        return "gutcheck-report"
    if len(slug) <= max_length:
        return slug
    trimmed = slug[:max_length].rstrip("-")
    return trimmed or "gutcheck-report"


def derive_report_slug(positions: str) -> str:
    marker = "Tightened claims to audit:"
    if marker in positions:
        tail = positions.split(marker, 1)[1]
        for line in tail.splitlines():
            candidate = line.strip()
            if not candidate:
                continue
            candidate = re.sub(r"^[-*]\s+", "", candidate)
            candidate = candidate.strip()
            if candidate:
                return slugify_report_name(candidate)

    for line in positions.splitlines():
        candidate = line.strip()
        if not candidate or candidate.endswith(":"):
            continue
        candidate = re.sub(r"^[-*]\s+", "", candidate)
        if candidate:
            return slugify_report_name(candidate)

    return "gutcheck-report"


def resolve_output_path(
    output_path: str | None,
    *,
    positions: str,
    suffix: str,
) -> pathlib.Path:
    if output_path:
        path = pathlib.Path(output_path).expanduser()
    else:
        slug = derive_report_slug(positions)
        path = pathlib.Path(DEFAULT_REPORT_DIR) / f"{slug}{suffix}"

    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        return path.resolve()

    stem = path.stem
    for index in range(2, 10_000):
        candidate = path.with_name(f"{stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate.resolve()

    raise RuntimeError(f"Could not find an available output filename for {path}.")


def write_report(output_path: pathlib.Path, report: str) -> pathlib.Path:
    path = output_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report)
    return path.resolve()


def render_inline_markdown(text: str) -> str:
    text = escape(text)
    text = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda match: f"{match.group(1)} ({match.group(2)})",
        text,
    )
    text = re.sub(r"`([^`]+)`", r'<font face="Courier">\1</font>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"(?<!_)_([^_]+)_(?!_)", r"<i>\1</i>", text)
    return text


def write_pdf_report(output_path: pathlib.Path, report: str) -> pathlib.Path:
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            ListFlowable,
            ListItem,
            Paragraph,
            SimpleDocTemplate,
            Spacer,
        )
    except ImportError as exc:
        raise RuntimeError(
            "PDF export requires the `reportlab` package. Run `uv sync` first."
        ) from exc

    path = output_path
    path.parent.mkdir(parents=True, exist_ok=True)

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "GutcheckTitle",
        parent=styles["Title"],
        fontName="Helvetica-Bold",
        fontSize=22,
        textColor=colors.HexColor("#0f766e"),
        spaceAfter=18,
    )
    heading_style = ParagraphStyle(
        "GutcheckHeading",
        parent=styles["Heading2"],
        fontName="Helvetica-Bold",
        textColor=colors.HexColor("#0f172a"),
        spaceBefore=12,
        spaceAfter=8,
    )
    subheading_style = ParagraphStyle(
        "GutcheckSubheading",
        parent=styles["Heading3"],
        fontName="Helvetica-Bold",
        textColor=colors.HexColor("#1e293b"),
        spaceBefore=8,
        spaceAfter=6,
    )
    body_style = ParagraphStyle(
        "GutcheckBody",
        parent=styles["BodyText"],
        fontName="Helvetica",
        fontSize=10.5,
        leading=14,
        spaceAfter=8,
    )
    code_style = ParagraphStyle(
        "GutcheckCode",
        parent=styles["BodyText"],
        fontName="Courier",
        fontSize=9,
        leading=11,
        leftIndent=12,
        rightIndent=12,
        borderPadding=10,
        backColor=colors.HexColor("#f8fafc"),
        borderColor=colors.HexColor("#cbd5e1"),
        borderWidth=0.5,
        borderRadius=4,
        spaceAfter=10,
    )

    story: list[object] = []
    lines = report.splitlines()
    paragraph_lines: list[str] = []
    bullet_lines: list[str] = []
    code_lines: list[str] = []
    in_code_block = False

    def flush_paragraph() -> None:
        nonlocal paragraph_lines
        if not paragraph_lines:
            return
        text = " ".join(line.strip() for line in paragraph_lines if line.strip())
        if text:
            story.append(Paragraph(render_inline_markdown(text), body_style))
        paragraph_lines = []

    def flush_bullets() -> None:
        nonlocal bullet_lines
        if not bullet_lines:
            return
        items = [
            ListItem(Paragraph(render_inline_markdown(item), body_style))
            for item in bullet_lines
        ]
        story.append(
            ListFlowable(
                items,
                bulletType="bullet",
                start="circle",
                leftIndent=18,
            )
        )
        story.append(Spacer(1, 0.08 * inch))
        bullet_lines = []

    def flush_code() -> None:
        nonlocal code_lines
        if not code_lines:
            return
        rendered = "<br/>".join(render_inline_markdown(line) for line in code_lines)
        story.append(Paragraph(rendered, code_style))
        code_lines = []

    for raw_line in lines:
        stripped = raw_line.strip()

        if in_code_block:
            if stripped.startswith("```"):
                flush_code()
                in_code_block = False
            else:
                code_lines.append(raw_line)
            continue

        if stripped.startswith("```"):
            flush_paragraph()
            flush_bullets()
            in_code_block = True
            continue

        if not stripped:
            flush_paragraph()
            flush_bullets()
            continue

        if stripped.startswith("### "):
            flush_paragraph()
            flush_bullets()
            story.append(
                Paragraph(render_inline_markdown(stripped[4:].strip()), subheading_style)
            )
            continue

        if stripped.startswith("## "):
            flush_paragraph()
            flush_bullets()
            story.append(
                Paragraph(render_inline_markdown(stripped[3:].strip()), heading_style)
            )
            continue

        if stripped.startswith("# "):
            flush_paragraph()
            flush_bullets()
            story.append(Paragraph(render_inline_markdown(stripped[2:].strip()), title_style))
            continue

        bullet_match = re.match(r"^[-*]\s+(.*)$", stripped)
        if bullet_match:
            flush_paragraph()
            bullet_lines.append(bullet_match.group(1).strip())
            continue

        paragraph_lines.append(raw_line)

    flush_paragraph()
    flush_bullets()
    flush_code()

    doc = SimpleDocTemplate(
        str(path),
        pagesize=LETTER,
        leftMargin=0.8 * inch,
        rightMargin=0.8 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
        title="gutcheck report",
    )
    doc.build(story)
    return path.resolve()


async def run_audit(
    positions: str,
    output_path: str | None = None,
    pdf_path: str | None = None,
) -> None:
    render_banner("Adversarial worldview audit", "Data-backed support, challenge, and crossover.")
    console.print(
        f"[dim]Auditing {len(positions)} chars across {len(SECTION_ORDER)} "
        f"parallel hunters. Each one searches the web, so expect roughly "
        f"30-90 seconds per hunter. Results print as they complete.[/dim]"
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
    sections: dict[HuntMode, str] = {}

    for coro in asyncio.as_completed(tasks):
        mode, text, error = await coro
        completed += 1
        if error:
            failures.append((mode, error))
            console.print(
                f"[red]x[/red] [bold]{mode}[/bold] failed "
                f"([dim]{completed}/{total}[/dim]): {error}"
            )
        else:
            sections[mode] = text
            console.print(
                f"[green]ok[/green] [bold]{mode}[/bold] complete "
                f"([dim]{completed}/{total}[/dim])"
            )
            render_section(mode, text)

    console.print()
    console.print(Rule("[bold]audit complete[/bold]", style="cyan"))
    console.print()
    if failures:
        for mode, err in failures:
            console.print(f"[yellow]{mode} hunter errored: {err}[/yellow]")
        console.print(
            "[yellow]This is usually a transient API issue. Try again.[/yellow]"
        )
    report: str | None = None
    if output_path or pdf_path:
        report = build_report_markdown(positions, sections, failures)

    if output_path and report is not None:
        markdown_target = resolve_output_path(
            output_path,
            positions=positions,
            suffix=".md",
        )
        saved_path = write_report(markdown_target, report)
        console.print(f"[green]Saved report:[/green] {saved_path}")

    if report is None:
        report = build_report_markdown(positions, sections, failures)

    pdf_target = resolve_output_path(
        pdf_path,
        positions=positions,
        suffix=".pdf",
    )
    if report is not None:
        saved_pdf_path = write_pdf_report(pdf_target, report)
        console.print(f"[green]Saved PDF:[/green] {saved_pdf_path}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="gutcheck",
        description=(
            "Conversation-first political audit. Start with a rough claim, "
            "tighten it into something fact-checkable, then see where live "
            "data backs you up, contradicts you, or overlaps with the other "
            "side."
        ),
    )
    p.add_argument(
        "file",
        nargs="?",
        help="Path to a file containing your positions. Omit to use stdin or the interactive coach.",
    )
    p.add_argument(
        "--editor",
        action="store_true",
        help="Skip the interactive coach and open $EDITOR for manual input.",
    )
    p.add_argument(
        "--output",
        help="Write the final audit to a Markdown file.",
    )
    p.add_argument(
        "--pdf",
        help="Write the final audit to a PDF file. If omitted, a PDF is auto-saved in gutcheck-reports/.",
    )
    return p.parse_args()


async def run_cli(args: argparse.Namespace) -> None:
    positions = await get_positions(args)
    if not positions:
        console.print(
            "[red]No positions provided.[/red] "
            "Pass a file path, pipe text on stdin, use --editor, or finish the "
            "interactive coach."
        )
        sys.exit(1)
    await run_audit(positions, output_path=args.output, pdf_path=args.pdf)


def run() -> None:
    """Synchronous entry point used by the [project.scripts] table."""
    args = parse_args()
    try:
        asyncio.run(run_cli(args))
    except KeyboardInterrupt:
        console.print("\n[yellow]Aborted.[/yellow]")
        sys.exit(130)


if __name__ == "__main__":
    run()
