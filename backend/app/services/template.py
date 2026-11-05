"""Build the blank budget workbook template the importer understands (headers from DEFAULT_YAML)."""
from __future__ import annotations

from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.table import Table, TableStyleInfo

from ..config import DEFAULT_YAML

LAST = 2000  # data rows 2..LAST are table rows, formulas and dropdowns
DATE_FMT, MONEY_FMT = "yyyy-mm-dd", "$#,##0.00"
INCOME_TYPES = ["Paycheck", "Dividend", "Interest", "Business", "Other"]
ACCOUNT_TYPES = ["Asset", "Liability"]
STATUSES = ["wishlist", "applied", "interview", "offer", "rejected", "withdrawn"]
# sheet key -> (date columns, money columns, widths by column)
SHEETS = {
    "income": ("Income", {"Date"}, {"Amount"}, [12, 22, 12, 14, 30]),
    "transactions": ("Transactions", {"Date"}, {"Amount"}, [12, 34, 20, 14, 14]),
    "balances": ("Net Worth", {"Date"}, {"Balance"}, [12, 28, 12, 16]),
    "business": ("Side Business", {"Date"}, {"Revenue", "Expense"}, [12, 34, 14, 14]),
    "applications": ("Job Applications", {"Applied On", "Last Update"}, set(), [24, 30, 12, 13, 13, 14, 36, 40]),
}
BUCKET_HELPER = "Bucket (auto)"


def _headers(key: str) -> list[str]:
    return list(DEFAULT_YAML["workbook"]["sheets"][key]["columns"].values())


def _table(ws, name: str, headers: list[str], dates: set, money: set, widths: list[int]) -> None:
    ws.append(headers)
    for i, h in enumerate(headers, 1):
        ws.column_dimensions[get_column_letter(i)].width = widths[i - 1]
        fmt = DATE_FMT if h in dates else MONEY_FMT if h in money else None
        if fmt:
            for r in range(2, LAST + 1):
                ws.cell(r, i).number_format = fmt
    tab = Table(displayName=name, ref=f"A1:{get_column_letter(len(headers))}{LAST}")
    tab.tableStyleInfo = TableStyleInfo(name="TableStyleMedium2", showRowStripes=True)
    ws.add_table(tab)
    ws.freeze_panes = "A2"


def _dropdown(ws, col: str, source: str, strict: bool = True) -> None:
    dv = DataValidation(type="list", formula1=source, allow_blank=True, showErrorMessage=strict)
    dv.add(f"{col}2:{col}{LAST}")
    ws.add_data_validation(dv)


def _start_here(ws) -> None:
    lines = [
        ("Budget workbook template", "title"),
        ("This workbook feeds your dashboard. Fill in the sheets below; the dashboard re-reads it automatically.", None),
        ("", None),
        ("Sheets", "h"),
        ("Income: one row per payment received (Date, Source, Type, Amount, Note).", None),
        ("Transactions: one row per expense (Date, Description, Category, Amount). Enter spending as positive numbers.", None),
        ("Net Worth: one row per account per snapshot (Date, Account, Type = Asset or Liability, Balance).", None),
        ("Side Business: optional (Date, Description, Revenue, Expense).", None),
        ("Job Applications: optional tracker (Company, Role, Status, Applied On, Last Update, Source, URL, Notes).", None),
        ("Summary: live monthly totals computed by formulas. The dashboard ignores it.", None),
        ("", None),
        ("Rules", "h"),
        ("Keep the header names exactly as they are. You can add rows freely (type in the next blank row) and add extra columns to the right.", None),
        ("Category decides 50/30/20: each category maps to needs, wants or savings (see the hidden Lists sheet and config category_buckets).", None),
        ("The Bucket (auto) column on Transactions is a helper formula; do not type over it.", None),
        ("Dates: real dates or YYYY-MM-DD. Amounts: plain numbers.", None),
        ("", None),
        ("Examples (type like this, do not copy these rows)", "h"),
        ("Income:        2026-01-15 | Employer | Paycheck | 2500.00 | January pay", None),
        ("Transactions:  2026-01-03 | Grocery store | Groceries | 84.20", None),
        ("Net Worth:     2026-01-31 | Checking | Asset | 4200.00", None),
        ("", None),
        ("How the dashboard picks up changes", "h"),
        ("Google Drive: keep this file in Drive (upload the .xlsx or convert it to a Google Sheet) and share it with the service account; changes sync every few minutes.", None),
        ("Manual: Dashboard > Settings > Upload workbook.", None),
    ]
    for r, (text, style) in enumerate(lines, 1):
        c = ws.cell(r, 1, text)
        if style == "title":
            c.font = Font(bold=True, size=16)
        elif style == "h":
            c.font = Font(bold=True, size=12, color="FFFFFF")
            c.fill = PatternFill("solid", fgColor="2F5597")
        c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width = 120
    ws.sheet_view.showGridLines = False


def _lists(ws) -> int:
    buckets = DEFAULT_YAML["workbook"]["category_buckets"]
    ws.append(["Income types", "Category", "Bucket", "", "Account types", "Application statuses"])
    for i, (cat, bucket) in enumerate(buckets.items(), 2):
        ws.cell(i, 2, cat.title())
        ws.cell(i, 3, bucket)
    for col, vals in ((1, INCOME_TYPES), (5, ACCOUNT_TYPES), (6, STATUSES)):
        for i, v in enumerate(vals, 2):
            ws.cell(i, col, v)
    for c, w in zip("ABCDEF", (14, 22, 18, 4, 16, 22)):
        ws.column_dimensions[c].width = w
    return len(buckets) + 1  # last used row of the category list


def _summary(ws) -> None:
    head = ["Month", "Income", "Paycheck", "Dividend", "Needs", "Wants", "Savings", "Savings rate"]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="2F5597")
    rng = lambda sh, col: f"{sh}!${col}$2:${col}${LAST}"  # noqa: E731
    for r in range(2, 14):
        ws.cell(r, 1, "=EOMONTH(TODAY(),-12)+1" if r == 2 else f"=EOMONTH(A{r - 1},0)+1").number_format = "mmm yyyy"
        win = lambda sh: f'{rng(sh, "A")},">="&$A{r},{rng(sh, "A")},"<="&EOMONTH($A{r},0)'  # noqa: E731
        ws.cell(r, 2, f'=SUMIFS({rng("Income", "D")},{win("Income")})')
        for col, t in ((3, "Paycheck"), (4, "Dividend")):
            ws.cell(r, col, f'=SUMIFS({rng("Income", "D")},{win("Income")},{rng("Income", "C")},"{t}")')
        for col, b in ((5, "needs"), (6, "wants"), (7, "savings")):
            ws.cell(r, col, f'=SUMIFS({rng("Transactions", "D")},{win("Transactions")},{rng("Transactions", "E")},"{b}")')
        ws.cell(r, 8, f'=IF(B{r}=0,"",(B{r}-E{r}-F{r})/B{r})').number_format = "0.0%"
        for col in range(2, 8):
            ws.cell(r, col).number_format = MONEY_FMT
    nw = "'Net Worth'"
    d, t = rng(nw, "A"), rng(nw, "C")
    ws["A16"], ws["A17"], ws["A18"], ws["A19"] = "Latest net worth date", "Assets", "Liabilities", "Net worth"
    ws["B16"] = f"=MAX({d})"
    ws["B16"].number_format = DATE_FMT
    ws["B17"] = f'=SUMPRODUCT(({d}=$B$16)*(LEFT(LOWER({t}),3)="ass")*{rng(nw, "D")})'
    ws["B18"] = f'=SUMPRODUCT(({d}=$B$16)*(LEFT(LOWER({t}),3)<>"ass")*{rng(nw, "D")})'
    ws["B19"] = "=B17-B18"
    for r in (17, 18, 19):
        ws.cell(r, 2).number_format = MONEY_FMT
    for r in range(16, 20):
        ws.cell(r, 1).font = Font(bold=True)
    ws["A21"] = "Savings rate = (income - needs - wants) / income. Net worth uses the rows dated on the latest Net Worth date."
    ws.column_dimensions["A"].width = 24
    for c in "BCDEFGH":
        ws.column_dimensions[c].width = 14
    ws.freeze_panes = "A2"


def build_template(path: Path | str, applications: list[dict] | None = None) -> Path:
    path = Path(path)
    wb = Workbook()
    _start_here(wb.active)
    wb.active.title = "Start Here"
    sheets = {k: wb.create_sheet(v[0]) for k, v in SHEETS.items()}
    lists = wb.create_sheet("Lists")
    last_cat = _lists(lists)
    lists.sheet_state = "hidden"

    for key, (title, dates, money, widths) in SHEETS.items():
        headers = _headers(key) + ([BUCKET_HELPER] if key == "transactions" else [])
        _table(sheets[key], title.replace(" ", ""), headers, dates, money, widths)

    tx = sheets["transactions"]
    for r in range(2, LAST + 1):
        tx.cell(r, 5, f'=IF(C{r}="","",IFERROR(VLOOKUP(C{r},Lists!$B$2:$C${last_cat},2,FALSE),"wants"))')
    _dropdown(sheets["income"], "C", f"=Lists!$A$2:$A${len(INCOME_TYPES) + 1}")
    _dropdown(tx, "C", f"=Lists!$B$2:$B${last_cat}", strict=False)
    _dropdown(sheets["balances"], "C", f"=Lists!$E$2:$E${len(ACCOUNT_TYPES) + 1}")
    _dropdown(sheets["applications"], "C", f"=Lists!$F$2:$F${len(STATUSES) + 1}")

    ws = sheets["applications"]
    for r, app in enumerate(applications or [], 2):
        for c, f in enumerate(DEFAULT_YAML["workbook"]["sheets"]["applications"]["columns"], 1):
            v = app.get(f)
            if f in ("applied_on", "last_update") and v:
                v = date.fromisoformat(str(v)[:10])
            if v not in (None, ""):
                ws.cell(r, c, v)
    _summary(wb.create_sheet("Summary"))
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path
