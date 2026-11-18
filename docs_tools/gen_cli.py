"""Generate the CLI reference from the Typer app, so it cannot drift from the code.

Run by mkdocs-gen-files at build time. Writes ``cli.md``: the hand-written
``docs/_cli_intro.md`` with the command tree (every command's options) and the
exit-code table filled in from the Typer app and ``EXIT_CODES``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import mkdocs_gen_files
import typer.main

from plasticdb.cli._app import app
from plasticdb.cli._core import EXIT_CODES

root = typer.main.get_command(app)


def cell(text: str | None) -> str:
    return " ".join((text or "").split()).replace("|", "\\|")


def option_rows(command: Any, skip_global: bool = True) -> list[str]:
    rows = []
    for param in command.params:
        if getattr(param, "hidden", False) or (skip_global and (param.name or "").startswith("g_")):
            continue
        if param.name in {"install_completion", "show_completion", "help"}:
            continue
        if getattr(param, "param_type_name", "") == "argument":
            label = (param.metavar or param.name or "").upper()
            name = f"`{label}`" + ("" if param.required else " (optional)")
        else:
            name = ", ".join(f"`{o}`" for o in [*param.opts, *param.secondary_opts])
        default = param.default
        shown = "" if default in (None, (), False, "") or callable(default) else f"`{default}`"
        rows.append(f"| {name} | {cell(getattr(param, 'help', None))} | {shown} |")
    return rows


def walk(command: Any, path: list[str], out: list[str]) -> None:
    for name in command.commands:
        sub = command.commands[name]
        if getattr(sub, "hidden", False):
            continue
        full = [*path, name]
        depth = min(len(full) + 3, 6)
        out.append(f"{'#' * depth} `plasticdb {' '.join(full)}`\n")
        if sub.help:
            out.append(sub.help.split("\n\n")[0].strip() + "\n")
        if getattr(sub, "commands", None):
            walk(sub, full, out)
        else:
            rows = option_rows(sub)
            if rows:
                out.append("| Option | Meaning | Default |\n|---|---|---|")
                out.extend(rows)
                out.append("")


tree = ["### Global options\n", "Accepted before or after the subcommand.\n"]
tree += ["| Option | Meaning | Default |", "|---|---|---|"]
tree += [*option_rows(root, skip_global=False), ""]
tree.append("### Commands\n")
walk(root, [], tree)

table = ["| Code | Meaning |", "|---|---|"]
table += [f"| {int(code)} | {cell(text)} |" for code, text in EXIT_CODES]

page = Path("docs/_cli_intro.md").read_text(encoding="utf-8")
page = page.replace("{{EXIT_CODES}}", "\n".join(table)).replace("{{COMMANDS}}", "\n".join(tree))
with mkdocs_gen_files.open("cli.md", "w") as handle:
    handle.write(page)
