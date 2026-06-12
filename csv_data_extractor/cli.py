from __future__ import annotations

from collections import Counter
import shutil
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from csv_data_extractor.config import load_source_config
from csv_data_extractor.extractor import (
    extract_csv,
    summarize_jsonl,
    write_issues_csv,
    write_jsonl,
)

app = typer.Typer(help="Extract NPDC loader-compatible JSONL from CSV files.")
console = Console()


@app.command()
def extract(
    config: Path = typer.Argument(..., help="Source YAML mapping."),
    csv_file: Path = typer.Argument(..., help="Input CSV file."),
    output: Path = typer.Option(..., "--output", "-o", help="Output JSONL file."),
    source_uid: str | None = typer.Option(
        None,
        "--source-uid",
        help="Override the source UID from the source mapping.",
    ),
    issue_limit: int = typer.Option(
        20,
        "--issue-limit",
        min=0,
        help="Maximum number of detailed validation issue rows to print.",
    ),
    issues_output: Path | None = typer.Option(
        None,
        "--issues-output",
        help="Write all validation issues to a CSV file.",
    ),
) -> None:
    """Extract JSONL from a CSV using a source mapping."""
    source_config = load_source_config(config).with_source_uid(source_uid)
    result = extract_csv(source_config, csv_file)
    _print_issues(result.issues, limit=issue_limit)
    _write_issues_if_requested(result.issues, issues_output)
    if result.has_errors:
        raise typer.Exit(code=1)
    write_jsonl(result.items, output)
    console.print(f"Wrote {len(result.items)} items to {output}")


@app.command()
def validate(
    config: Path = typer.Argument(..., help="Source YAML mapping."),
    csv_file: Path = typer.Argument(..., help="Input CSV file."),
    source_uid: str | None = typer.Option(
        None,
        "--source-uid",
        help="Override the source UID from the source mapping.",
    ),
    issue_limit: int = typer.Option(
        20,
        "--issue-limit",
        min=0,
        help="Maximum number of detailed validation issue rows to print.",
    ),
    issues_output: Path | None = typer.Option(
        None,
        "--issues-output",
        help="Write all validation issues to a CSV file.",
    ),
) -> None:
    """Validate a source mapping and CSV without writing JSONL."""
    source_config = load_source_config(config).with_source_uid(source_uid)
    result = extract_csv(source_config, csv_file)
    _print_issues(result.issues, limit=issue_limit)
    _write_issues_if_requested(result.issues, issues_output)
    if result.has_errors:
        raise typer.Exit(code=1)
    console.print(f"Validated {len(result.items)} extractable items")


@app.command()
def summarize(
    jsonl_file: Path = typer.Argument(..., help="Loader JSONL file."),
) -> None:
    """Summarize model counts in a loader JSONL file."""
    try:
        counts = summarize_jsonl(jsonl_file)
    except ValueError as exc:
        console.print(f"[red]Error:[/red] {exc}")
        raise typer.Exit(code=1) from exc
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


def _print_issues(issues, *, limit: int = 20) -> None:
    if not issues:
        return
    summary = Counter(
        (issue.severity, issue.model or "", issue.field or "", issue.message)
        for issue in issues
    )
    summary_table = Table(title="Validation Issue Summary")
    summary_table.add_column("Severity")
    summary_table.add_column("Model")
    summary_table.add_column("Field")
    summary_table.add_column("Count", justify="right")
    summary_table.add_column("Message")
    for (severity, model, field, message), count in sorted(
        summary.items(),
        key=lambda item: (item[0][0], item[0][1], item[0][2], item[0][3]),
    ):
        summary_table.add_row(severity, model, field, str(count), message)
    console.print(summary_table)

    if limit == 0:
        return

    table = Table(title=f"Validation Issue Samples (first {limit})")
    table.add_column("Severity")
    table.add_column("Row", justify="right")
    table.add_column("Model")
    table.add_column("Field")
    table.add_column("Message")
    table.add_column("Record")
    for issue in issues[:limit]:
        table.add_row(
            issue.severity,
            "" if issue.row_number is None else str(issue.row_number),
            issue.model or "",
            issue.field or "",
            issue.message,
            _format_issue_context(issue.context),
        )
    console.print(table)
    if len(issues) > limit:
        console.print(f"Showing {limit} of {len(issues)} issues. Use --issue-limit to adjust.")


def _format_issue_context(context: dict) -> str:
    if not context:
        return ""
    preferred = (
        "person_nbr",
        "document_id",
        "full_name",
        "agency_name",
        "earliest_date",
        "latest_date",
        "employment_status",
        "status",
    )
    parts = []
    for key in preferred:
        value = context.get(key)
        if value not in (None, ""):
            parts.append(f"{key}={value}")
    return "; ".join(parts)


def _write_issues_if_requested(issues, output: Path | None) -> None:
    if output is None:
        return
    write_issues_csv(issues, output)
    console.print(f"Wrote {len(issues)} validation issues to {output}")


if __name__ == "__main__":
    app()
