from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape


def render_report(template_dir: Path, context: dict) -> str:
    env = Environment(
        loader=FileSystemLoader(template_dir),
        autoescape=select_autoescape(enabled_extensions=()),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("market_brief.md.j2")
    return template.render(**context)
