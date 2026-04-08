# gutcheck

> An adversarial audit of your political worldview.

You start with a rough political thought in plain language. **gutcheck** first tightens that thought into concrete, fact-checkable claims, then runs three parallel agents over the result — each one searches the live web for primary sources — and prints back:

1. **Where the data contradicts you.** Specific claims you made that the numbers don't support, with citations.
2. **Where the data backs you up.** Specific claims you made that the numbers do support, with citations.
3. **Where you unknowingly agree with the other side.** Specific positions of yours that people from the opposing political tribe also hold, for the same data-backed reasons. Insight, not gotcha.

The whole thing runs in your terminal. No app server, no hosted state. It uses your local `codex` CLI session for both the argument-coaching pass and the audit.

---

## Why this exists

Most political "fact-checkers" position the AI as an external authority telling you what's true. That's psychologically unpalatable and usually wrong about something. **gutcheck** does the opposite: it starts with *your stated belief*, helps you clarify what you actually mean, then runs adversarial searches to find where that sharpened argument breaks against the data. It's not "you're wrong" — it's "here's the version of your argument we're actually auditing, and here's where the evidence holds or fails."

The premise: **baseline facts exist and matter for a functioning society.** This tool doesn't hedge, doesn't both-sides every issue, and is willing to say "the data clearly contradicts this" when the data clearly contradicts it. It also won't pretend to support a claim that isn't actually well-supported just to flatter you.

If you're looking for a tool that confirms what you already believe, this isn't it. If you're looking for a tool that mocks what you believe, this also isn't it. It's looking for the gaps between your stated worldview and what the data actually says, in both directions.

---

## What you need

1. **An installed and authenticated `codex` CLI.**
2. **Python 3.10 or newer.**
3. **[uv](https://github.com/astral-sh/uv)** — `curl -LsSf https://astral.sh/uv/install.sh | sh`

That is the only runtime dependency outside Python. `gutcheck` shells out to `codex exec`, so your existing Codex login is what it uses for inference and web search.

---

## Install and run

```bash
git clone https://github.com/<you>/gutcheck.git
cd gutcheck
uv sync
```

First time only — make sure Codex is authenticated:

```bash
codex login
```

Then run the audit. The default run now auto-saves a shareable PDF in `gutcheck-reports/` using a filename derived from the argument being audited.

**Option 1 — conversation mode (recommended for first run):**
```bash
uv run gutcheck
```
This starts the interactive argument-coaching flow, runs the audit, and auto-saves a PDF such as `gutcheck-reports/canada-homelessness-immigration.pdf`. If that filename already exists, gutcheck will save `...-2.pdf`, `...-3.pdf`, and so on.

**Option 2 — open your editor directly:**
```bash
uv run gutcheck --editor
```
This skips the coaching questions and opens `$EDITOR` with a manual template.

**Option 3 — pass a file:**
```bash
uv run gutcheck my_positions.md
```

**Option 4 — pipe from stdin:**
```bash
echo "I think Biden was soft on the border. The economy is fine." | uv run gutcheck
```

**Option 5 — also save a Markdown report:**
```bash
uv run gutcheck --output report.md
```
This keeps the normal terminal output, still auto-saves the PDF, and also writes Markdown to `report.md` without overwriting an existing file.

**Option 6 — choose your own PDF filename:**
```bash
uv run gutcheck --pdf report.pdf
```
This writes the PDF to your chosen path, but still avoids overwriting by adding `-2`, `-3`, and so on if needed. You can also save both formats in one run with `--output report.md --pdf report.pdf`.

---

## First-run experience

The default flow is designed for people who do **not** already have a polished claim ready. You can start rough:

**You:** `immigration is bad`

**gutcheck:** "Do you mean because border encounters increased, because wages or housing got squeezed, because crime rose, or something else?"

After you answer 2-3 questions like that, the tool shows you the tightened version it is about to audit. You can accept it, edit it, or abort.

## Writing good positions

The audit is still only as sharp as the final framing. If you already know exactly what you mean, write it directly.

**Rough:** "Immigration is bad."
**Tightened:** "Border encounters rose under Biden, major cities absorbed higher shelter costs, and the administration publicly downplayed the scale of the surge."

**Bad:** "The economy is rigged."
**Good:** "Wage growth for the bottom 50% has been outpaced by asset price inflation, so the recovery doesn't reach normal people."

**Bad:** "Climate change is overhyped."
**Good:** "Global temperatures are rising but the catastrophic 2030 deadline rhetoric is not supported by IPCC scenarios."

You can write 3 positions or 30. The tool will pick the most substantive ones to dig into.

---

## What the output looks like

For each of the three sections, you'll see something like:

```
─── Where the data contradicts you ────────────────────────────────────

### Your claim that wage growth has stagnated under the current administration
The BLS Q4 2025 data shows real wages for the bottom quartile rose 3.2%
year-over-year, the strongest gain since 2019. The "wage stagnation"
narrative tracks better with the 2010-2019 period than the post-2022
recovery.

Sources: [BLS Q4 2025](...), [Brookings analysis](...)

### Your claim about ...
...
```

Each section is independent. They run in parallel and print as they finish, so the first one done shows up first — not always in the same order.

---

## Honest limitations

- **It can be wrong.** The hunters cite primary sources, but LLMs can still misread, miscount, or pick a non-representative source. Treat this as a starting point for your own digging, not the final word. Click the citations.
- **It works best on factual claims.** It can audit "X happened" or "Y data says Z." It can't audit "X is morally wrong" or "Y feels like it's getting worse" — those aren't checkable against data.
- **It uses Codex usage.** Each audit makes several non-interactive `codex exec` calls, and the three fact-checking passes all use live web search.
- **Reports are saved to disk.** Every run auto-saves a PDF in `gutcheck-reports/`. Use `--output` if you also want Markdown, or `--pdf` if you want to control the PDF path.

---

## The architecture, briefly

There are now two stages:

1. A short argument-coaching pass that asks clarification questions and synthesizes a tighter, fact-checkable framing.
2. Three parallel `codex exec --search` calls running via `asyncio.as_completed`.

Each hunter is told to be intellectually honest — to skip the section entirely if the data doesn't support a clean answer rather than fabricating one.

Inspired by [brendanhogan/loophole](https://github.com/brendanhogan/loophole), which uses the same adversarial-self-knowledge pattern for moral codes instead of political worldviews.

---

## Contributing

This is a v0 side project. The prompts in `src/gutcheck/prompts.py` are the load-bearing piece — improving them is the highest-leverage change. PRs welcome if you find them too soft, too hedgy, too aggressive, or biased in either direction.
