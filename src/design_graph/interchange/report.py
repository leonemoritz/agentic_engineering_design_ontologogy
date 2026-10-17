# SPDX-License-Identifier: Apache-2.0
"""HTML report and neutral review data package outputs."""

from __future__ import annotations

import html
import json
import zipfile
from importlib.resources import files
from pathlib import Path
from typing import Any

from design_graph.engineering.verification import VerificationRow


def render_report(rows: list[VerificationRow], graph_data: dict[str, Any], title: str) -> str:
    """Render a compact, printable HTML verification report."""

    counts: dict[str, int] = {}
    for row in rows:
        counts[row.verdict] = counts.get(row.verdict, 0) + 1
    badges = " ".join(
        f'<span class="badge">{html.escape(verdict)} {count}</span>'
        for verdict, count in sorted(counts.items())
    )
    body_rows = []
    for row in rows:
        value = json.dumps(row.value, ensure_ascii=True)
        evidence = row.evidence.node
        if row.evidence.path:
            evidence += f".{row.evidence.path}"
        body_rows.append(
            "<tr>"
            f"<td>{html.escape(', '.join(row.req_ids) or '—')}</td>"
            f"<td>{html.escape(row.topic)}</td>"
            f"<td>{html.escape(row.requirement)}</td>"
            f"<td>{html.escape(row.method)}</td>"
            f"<td>{html.escape(evidence)}</td>"
            f"<td>{html.escape(value)}</td>"
            f"<td>{'' if row.margin is None else f'{row.margin:g}'}</td>"
            f'<td class="{html.escape(row.verdict.lower())}">{html.escape(row.verdict)}</td>'
            f"<td>{html.escape('; '.join(row.provisional_reasons) if row.provisional else '')}</td>"
            "</tr>"
        )
    assumptions = graph_data.get("assumptions", {})
    assumption_items = (
        "".join(
            f"<li><strong>{html.escape(reason)}</strong>: {html.escape(', '.join(nodes))}</li>"
            for reason, nodes in assumptions.items()
        )
        or "<li>None</li>"
    )
    return f"""<!-- SPDX-License-Identifier: Apache-2.0 -->
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title><style>
body{{margin:32px;color:#182321;font:13px/1.45 ui-monospace,monospace}}
h1{{font:700 28px Georgia,serif}}
h2{{margin-top:30px;font:700 18px Georgia,serif}}
.badge{{display:inline-block;padding:4px 8px;border:1px solid #bdc9c4;margin-right:6px}}
table{{border-collapse:collapse;width:100%;font-size:11px}}
th,td{{border:1px solid #cbd3cf;padding:7px;text-align:left;vertical-align:top}}
th{{background:#edf1ed}}
.pass{{color:#13736a}}.fail{{color:#a53b32}}.open,.decide{{color:#b35c20}}
.deliverable{{color:#52635e}}
@media print{{body{{margin:12mm}}table{{font-size:9px}}tr{{break-inside:avoid}}}}
</style></head><body><h1>{html.escape(title)}</h1><p>{badges}</p>
<h2>Verification matrix</h2>
<table><thead><tr>
<th>Requirement IDs</th><th>Topic</th><th>Requirement</th><th>Method</th>
<th>Evidence</th><th>Value</th><th>Margin</th><th>Verdict</th><th>Provisional reasons</th>
</tr></thead><tbody>{"".join(body_rows)}</tbody></table>
<h2>Assumptions register</h2><ul>{assumption_items}</ul></body></html>"""


def write_review_package(
    output_dir: str | Path,
    *,
    rows: list[VerificationRow],
    graph_data: dict[str, Any],
    title: str,
    pdf: bool = False,
    cad: bool = False,
) -> Path:
    """Write report HTML, neutral template, and review ZIP from generated artifacts."""

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    report_path = output / "report.html"
    report_path.write_text(render_report(rows, graph_data, title), encoding="utf-8")
    template_path = output / "review_template.yaml"
    template_path.write_text(
        files("design_graph.interchange")
        .joinpath("assets/review_template.yaml")
        .read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    if pdf:
        export_pdf(report_path, output / "report.pdf")
    package_path = output / "review_package.zip"
    include = [
        output / "graph.json",
        output / "verification.json",
        report_path,
        template_path,
        output / "graph.html",
    ]
    if cad:
        include.extend([output / "enclosure_base.step", output / "mass_properties.json"])
    if pdf and (output / "report.pdf").is_file():
        include.append(output / "report.pdf")
    with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in include:
            if path.is_file():
                archive.write(path, arcname=path.name)
    return package_path


def export_pdf(html_path: str | Path, pdf_path: str | Path) -> Path:
    """Export HTML using optional Playwright and an installed Chromium browser."""

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise RuntimeError(
            "PDF export requires the optional 'pdf' extra and Playwright Chromium"
        ) from error
    target = Path(pdf_path)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page()
        page.goto(Path(html_path).resolve().as_uri(), wait_until="load")
        page.pdf(path=str(target), format="A4", print_background=True)
        browser.close()
    return target
