"""Generate a blank budget workbook template that the dashboard importer understands.

Usage: python scripts/make_template_workbook.py [out_path] [--applications file.json]
       (default out ../data/budget_template.xlsx; the JSON is a list of application dicts with the
       applications-table keys: company, role, status, applied_on, last_update, source, url, notes)
Headers come from DEFAULT_YAML in app/config.py. No example rows in data sheets, so imports stay clean.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.template import build_template  # noqa: E402


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("out", nargs="?", default=str(Path(__file__).resolve().parents[2] / "data" / "budget_template.xlsx"))
    ap.add_argument("--applications", help="JSON file: list of application dicts to prefill")
    a = ap.parse_args()
    apps = json.loads(Path(a.applications).read_text()) if a.applications else None
    print(build_template(a.out, apps))
