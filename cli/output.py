# Copyright 2025 finops-agent contributors
# SPDX-License-Identifier: Apache-2.0

import json
from typing import Any

from rich.console import Console
from rich.table import Table

console = Console()


def print_table(title: str, columns: list[str], rows: list[list[str]]) -> None:
    """Print a rich table to stdout."""
    table = Table(title=title, show_lines=False)
    for col in columns:
        table.add_column(col)
    for row in rows:
        table.add_row(*row)
    console.print(table)


def print_json(data: Any) -> None:
    """Print data as JSON to stdout."""
    console.print(json.dumps(data, indent=2, default=str))


def print_plain(text: str) -> None:
    """Print plain text to stdout."""
    console.print(text)
