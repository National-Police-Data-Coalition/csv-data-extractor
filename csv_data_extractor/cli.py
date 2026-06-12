from __future__ import annotations

import shutil
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from csv_data_extractor.config import load_source_config
from csv_data_extractor.extractor import extract_csv, summarize_jsonl, write_jsonl

app = typer.Typer(help="Extract NPDC loader-compatible JSONL from CSV files.")
console = Console()


@app.command()
def extract(
    config: Path = typer.Argument(..., help="Source YAML mapping."),
    csv_file: Path = typer.Argument(..., help="Input CSV file."),
    output: Path = typer.Option(..., "--output", "-o", help="Output JSONL file."),
) -> None:
    """Extract JSONL from a CSV using a source mapping."""
    source_config = load_source_config(config)
    result = extract_csv(source_config, csv_file)
    _print_issues(result.issues)
    if result.has_errors:
        raise typer.Exit(code=1)
    write_jsonl(result.items, output)
    console.print(f"Wrote {len(result.items)} items to {output}")


@app.command()
def validate(
    config: Path = typer.Argument(..., help="Source YAML mapping."),
    csv_file: Path = typer.Argument(..., help="Input CSV file."),
) -> None:
    """Validate a source mapping and CSV without writing JSONL."""
    source_config = load_source_config(config)
    result = extract_csv(source_config, csv_file)
    _print_issues(result.issues)
    if result.has_errors:
        raise typer.Exit(code=1)
    console.print(f"Validated {len(result.items)} extractable items")


@app.command()
def summarize(
    jsonl_file: Path = typer.Argument(..., help="Loader JSONL file."),
) -> None:
    """Summarize model counts in a loader JSONL file."""
    counts = summarize_jsonl(jsonl_file)
    table = Table(title="JSONL Summary")
    table.add_column("Model")
    table.add_column("Count", justify="right")
    for model, count in sorted(counts.items()):
        table.add_row(model, str(count))
    console.print(table)


@app.command("init-source")
def init_source(
    name: str = typer.Argument(..., help="New source directory name."),
    destination: Path = typer.Option(Path("sources"), "--destination", "-d"),
) -> None:
    """Create a copyable source skeleton."""
    template = Path(__file__).resolve().parent.parent / "sources" / "example_npi_officers"
    target = destination / name
    if target.exists():
        console.print(f"{target} already exists")
        raise typer.Exit(code=1)
    shutil.copytree(template, target)
    console.print(f"Created {target}")


def _print_issues(issues) -> None:
    if not issues:
        return
    table = Table(title="Validation Issues")
    table.add_column("Severity")
    table.add_column("Row", justify="right")
    table.add_column("Model")
    table.add_column("Field")
    table.add_column("Message")
    for issue in issues:
        table.add_row(
            issue.severity,
            "" if issue.row_number is None else str(issue.row_number),
            issue.model or "",
            issue.field or "",
            issue.message,
        )
    console.print(table)


if __name__ == "__main__":
    app()
