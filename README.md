# gutcheck

> An adversarial audit of your political worldview.

You paste your political positions in plain language. **gutcheck** runs three parallel agents over your views — each one searches the live web for primary sources — and prints back:

1. **Where the data contradicts you.** Specific claims you made that the numbers don't support, with citations.
2. **Where the data backs you up.** Specific claims you made that the numbers do support, with citations.
3. **Where you unknowingly agree with the other side.** Specific positions of yours that people from the opposing political tribe also hold, for the same data-backed reasons. Insight, not gotcha.

The whole thing runs in your terminal. No accounts, no servers, no tracking. It uses your own Claude Code subscription via the bundled `claude` CLI.

---

## Why this exists

Most political "fact-checkers" position the AI as an external authority telling you what's true. That's psychologically unpalatable and usually wrong about something. **gutcheck** does the opposite: it takes *your stated beliefs* as the starting point, then runs adversarial searches to find where they break against themselves. It's not "you're wrong" — it's "two things you said can't both be true, here's the data, you decide."

The premise: **baseline facts exist and matter for a functioning society.** This tool doesn't hedge, doesn't both-sides every issue, and is willing to say "the data clearly contradicts this" when the data clearly contradicts it. It also won't pretend to support a claim that isn't actually well-supported just to flatter you.

If you're looking for a tool that confirms what you already believe, this isn't it. If you're looking for a tool that mocks what you believe, this also isn't it. It's looking for the gaps between your stated worldview and what the data actually says, in both directions.

---

## What you need

1. **A Claude Pro or Max subscription** (the `$20/mo` and up plans). This is what the tool uses for inference.
2. **Python 3.10 or newer.**
3. **[uv](https://github.com/astral-sh/uv)** — `curl -LsSf https://astral.sh/uv/install.sh | sh`

That's it. The Claude Code CLI is bundled with the Python SDK, so you don't need to install it separately. You just need to be authenticated.

---

## Install and run

```bash
git clone https://github.com/<you>/gutcheck.git
cd gutcheck
uv sync
```

First time only — authenticate the bundled `claude` CLI with your subscription:

```bash
uv run claude
# Follow the prompts to log in. Quit once you're authenticated.
```

Then run the audit. Three options:

**Option 1 — open your editor (recommended for first run):**
```bash
uv run gutcheck
```
This opens `$EDITOR` with a template. Type your positions, save, quit. The audit kicks off.

**Option 2 — pass a file:**
```bash
uv run gutcheck my_positions.md
```

**Option 3 — pipe from stdin:**
```bash
echo "I think Biden was soft on the border. The economy is fine." | uv run gutcheck
```

---

## Writing good positions

The tool is only as sharp as the input. Vague gives vague, specific gives specific.

**Bad:** "Immigration is bad."
**Good:** "Illegal border crossings have surged under Biden and the administration is downplaying it."

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
- **It costs your subscription tokens.** Each audit makes ~3 long Claude calls with web search. On a Pro/Max plan that's well within limits, but if you run it 50 times back-to-back you may hit your usage cap.
- **No history.** Each run is fresh. Nothing is saved to disk. (This is intentional.)

---

## The architecture, briefly

Three Python coroutines, three system prompts, three Claude Agent SDK queries running in parallel via `asyncio.as_completed`. Each one has the WebSearch and WebFetch tools enabled. Each one is told to be intellectually honest — to skip the section entirely if the data doesn't support a clean answer rather than fabricating one.

Inspired by [brendanhogan/loophole](https://github.com/brendanhogan/loophole), which uses the same adversarial-self-knowledge pattern for moral codes instead of political worldviews.

---

## Contributing

This is a v0 side project. The prompts in `src/gutcheck/prompts.py` are the load-bearing piece — improving them is the highest-leverage change. PRs welcome if you find them too soft, too hedgy, too aggressive, or biased in either direction.
