"""Settings: env vars for deployment/secrets, YAML for workbook mapping and rules."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

BASE_DIR = Path(__file__).resolve().parents[2]  # dashboard/

DEFAULT_YAML: dict[str, Any] = {
    "workbook": {
        "path": "data/budget.xlsx",
        "sheets": {
            "income": {"name": "Income", "columns": {"date": "Date", "source": "Source", "type": "Type", "amount": "Amount", "note": "Note"}},
            "transactions": {"name": "Transactions", "columns": {"date": "Date", "description": "Description", "category": "Category", "amount": "Amount"}},
            "balances": {"name": "Net Worth", "columns": {"date": "Date", "account": "Account", "kind": "Type", "balance": "Balance"}},
            "business": {"name": "Side Business", "columns": {"date": "Date", "description": "Description", "revenue": "Revenue", "expense": "Expense"}},
            "applications": {"name": "Job Applications", "columns": {"company": "Company", "role": "Role", "status": "Status", "applied_on": "Applied On",
                                                                      "last_update": "Last Update", "source": "Source", "url": "URL", "notes": "Notes"}},
        },
        "drive_file_id": None,  # Google Drive file id (see README "Auto-sync from Google Drive")
        # income Type text -> canonical type (case-insensitive substring match, first hit wins)
        "income_types": {"tesla": "paycheck", "paycheck": "paycheck", "salary": "paycheck", "dividend": "dividend",
                         "interest": "interest", "business": "business"},
        # transaction Category -> 50/30/20 bucket (case-insensitive exact match; unmapped -> default_bucket)
        "category_buckets": {
            "rent": "needs", "mortgage": "needs", "utilities": "needs", "groceries": "needs", "insurance": "needs",
            "transportation": "needs", "gas": "needs", "car payment": "needs", "phone": "needs", "internet": "needs",
            "healthcare": "needs", "minimum debt payment": "needs",
            "dining": "wants", "entertainment": "wants", "shopping": "wants", "travel": "wants", "subscriptions": "wants",
            "savings": "savings", "investing": "savings", "401k": "savings", "roth ira": "savings", "brokerage": "savings",
            "emergency fund": "savings", "extra debt payment": "savings",
            "transfer": "ignore",
        },
        "default_bucket": "wants",
    },
    "finance": {
        "liquid_accounts": ["checking", "savings", "hysa", "emergency"],
        "invested_accounts": ["401k", "roth", "ira", "brokerage", "hsa", "espp", "stock"],
    },
    "career": {
        "github_user": "AKworlds",
        "email": {
            "folder": "INBOX",
            "lookback_days": 30,
            "initial_lookback_days": 3650,  # first sync / "full rescan"
            "rules": {
                "offer": ["offer letter", "pleased to offer", "job offer"],
                "rejected": ["not moving forward", "move forward with other candidates", "not selected", "decided to pursue",
                             "will not be moving forward", "unable to offer you"],
                "interview": ["schedule an interview", "invite you to interview", "interview invitation", "interview request", "like to interview", "interview with",
                              "schedule a call", "phone screen", "availability for an interview"],
                "applied": ["application received", "thank you for applying", "thanks for applying", "application has been submitted", "we received your application"],
            },
        },
    },
    "scheduler": {"workbook_poll_seconds": 300, "career_sync_hours": 6},
    # US federal figures for the side-business tax estimate. Update each January (IRS Rev. Proc.).
    "tax": {
        "year": 2026,
        "standard_deduction": {"single": 16100, "married_joint": 32200, "head_of_household": 24150},
        "brackets": {  # [upper bound of bracket, rate]; null = no upper bound
            "single": [[12400, 0.10], [50400, 0.12], [105700, 0.22], [201775, 0.24], [256225, 0.32], [640600, 0.35], [None, 0.37]],
            "married_joint": [[24800, 0.10], [100800, 0.12], [211400, 0.22], [403550, 0.24], [512450, 0.32], [768700, 0.35], [None, 0.37]],
            "head_of_household": [[17700, 0.10], [67450, 0.12], [105700, 0.22], [201750, 0.24], [256200, 0.32], [640600, 0.35], [None, 0.37]],
        },
        "ss_wage_base": 184500,
        "se_net_factor": 0.9235,
        "se_ss_rate": 0.124,
        "se_medicare_rate": 0.029,
        "additional_medicare_rate": 0.009,
        "additional_medicare_threshold": {"single": 200000, "married_joint": 250000, "head_of_household": 200000},
        "qbi_rate": 0.20,
        # Estimated-tax periods: [start MM-DD, end MM-DD, due date MM-DD, due in next year?]
        "quarters": [["01-01", "03-31", "04-15", False], ["04-01", "05-31", "06-15", False],
                     ["06-01", "08-31", "09-15", False], ["09-01", "12-31", "01-15", True]],
    },
}


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    db_path: Path
    config: dict = field(default_factory=dict)
    github_token: str | None = None
    imap_host: str | None = None
    imap_user: str | None = None
    imap_password: str | None = None
    enable_scheduler: bool = True
    google_service_account_file: str | None = None

    @property
    def workbook_path(self) -> Path:
        p = Path(self.config["workbook"]["path"])
        return p if p.is_absolute() else BASE_DIR / p


@lru_cache
def get_settings() -> Settings:
    data_dir = Path(os.getenv("DASH_DATA_DIR", BASE_DIR / "data"))
    cfg_path = Path(os.getenv("DASH_CONFIG", BASE_DIR / "config" / "dashboard.yaml"))
    user_cfg = yaml.safe_load(cfg_path.read_text()) if cfg_path.exists() else {}
    return Settings(
        data_dir=data_dir,
        db_path=Path(os.getenv("DASH_DB", data_dir / "dashboard.db")),
        config=_deep_merge(DEFAULT_YAML, user_cfg or {}),
        github_token=os.getenv("GITHUB_TOKEN") or None,
        imap_host=os.getenv("IMAP_HOST") or None,
        imap_user=os.getenv("IMAP_USER") or None,
        imap_password=os.getenv("IMAP_PASSWORD") or None,
        google_service_account_file=os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE") or None,
        enable_scheduler=os.getenv("DASH_SCHEDULER", "1") == "1",
    )
