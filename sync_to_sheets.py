#!/usr/bin/env python3
"""
Jira (filter 11971) → Google Sheets sync
Reads a JSON array of Jira issues from a file path (argv[1]) or stdin,
then upserts them into the configured Google Sheet.

Column → Jira field mapping (verified against live data):
  Work             → key + summary  (hyperlink)
  Linked work item → issuelinks[].inward/outwardIssue.key
  Reporting Source → customfield_10070.value   (select: "US Customer Success", "HSB", …)
  Customer         → customfield_10072          (text:   "Ancestry Ireland UAT", "HSB", …)
  Reporter         → reporter.displayName
  Created          → created  (ISO → YYYY-MM-DD)
  Assignee         → assignee.displayName
  Status           → status.name
  Priority         → priority.name
  Labels           → labels[]  (comma-separated)
  Environment      → customfield_10044[0].value (select: "US", "HSB", …)
"""
import json
import re
import sys
import os
from datetime import datetime, timezone

import requests
import gspread
from google.oauth2.service_account import Credentials
import google.auth.transport.requests

# ── Config ────────────────────────────────────────────────────────────────────
SHEET_ID   = "1YxfK1KFekR-UYPeNNZXf7DgLCGlbU_FxliqQ3jrckTs"
CREDS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "credentials.json")
JIRA_BASE  = "https://vicaiglobal.atlassian.net/browse/"

HEADERS = [
    "Work", "Linked work item", "Reporting Source", "Customer",
    "Reporter", "Created", "Assignee", "Status", "Priority",
    "Labels", "Environment",
]

KEY_RE = re.compile(r"^([A-Z]+-\d+)")
# ─────────────────────────────────────────────────────────────────────────────


def get_creds() -> Credentials:
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
        "https://www.googleapis.com/auth/spreadsheets",
    ]
    creds = Credentials.from_service_account_file(CREDS_FILE, scopes=scopes)
    creds.refresh(google.auth.transport.requests.Request())
    return creds


def unmerge_and_reset_sheet(creds: Credentials, sheet_gid: int) -> None:
    """Remove all merged cells and frozen rows via the batchUpdate API."""
    url     = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET_ID}:batchUpdate"
    headers = {"Authorization": f"Bearer {creds.token}", "Content-Type": "application/json"}
    body    = {
        "requests": [
            {
                "unmergeCells": {
                    "range": {
                        "sheetId": sheet_gid,
                        "startRowIndex": 0, "endRowIndex": 1000,
                        "startColumnIndex": 0, "endColumnIndex": 26,
                    }
                }
            },
            {
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_gid,
                        "gridProperties": {"frozenRowCount": 0},
                    },
                    "fields": "gridProperties.frozenRowCount",
                }
            },
        ]
    }
    resp = requests.post(url, headers=headers, json=body)
    resp.raise_for_status()


def extract_key(cell: str) -> str:
    """Return the Jira issue key from a Work cell's displayed value."""
    m = KEY_RE.match((cell or "").strip())
    return m.group(1) if m else ""


def parse_issue(issue: dict) -> list:
    fields  = issue.get("fields", {})
    key     = issue.get("key", "")
    summary = (fields.get("summary") or "").replace('"', "'")

    # ── Work — clickable hyperlink ────────────────────────────────────────────
    work = f'=HYPERLINK("{JIRA_BASE}{key}","{key}: {summary}")'

    # ── Linked work items ─────────────────────────────────────────────────────
    linked_keys = []
    for lnk in (fields.get("issuelinks") or []):
        for direction in ("inwardIssue", "outwardIssue"):
            if direction in lnk:
                linked_keys.append(lnk[direction]["key"])
    linked = ", ".join(linked_keys)

    # ── Reporting Source  (customfield_10070 — select field) ──────────────────
    cf70             = fields.get("customfield_10070") or {}
    reporting_source = cf70.get("value", "")

    # ── Customer  (customfield_10072 — text field) ────────────────────────────
    customer = fields.get("customfield_10072") or ""

    # ── Reporter ──────────────────────────────────────────────────────────────
    reporter_obj  = fields.get("reporter") or {}
    reporter_name = (
        reporter_obj.get("displayName")
        or reporter_obj.get("emailAddress", "")
    )

    # ── Created — ISO 8601 → YYYY-MM-DD ──────────────────────────────────────
    created_raw = fields.get("created", "")
    try:
        created = datetime.fromisoformat(created_raw).strftime("%Y-%m-%d")
    except Exception:
        created = created_raw[:10] if created_raw else ""

    # ── Assignee ──────────────────────────────────────────────────────────────
    assignee_obj  = fields.get("assignee") or {}
    assignee_name = assignee_obj.get("displayName") or "Unassigned"

    # ── Status / Priority ─────────────────────────────────────────────────────
    status   = (fields.get("status")   or {}).get("name", "")
    priority = (fields.get("priority") or {}).get("name", "")

    # ── Labels (all, comma-separated) ─────────────────────────────────────────
    labels_list = fields.get("labels") or []
    all_labels  = ", ".join(labels_list)

    # ── Environment  (customfield_10044 — multi-select, take first value) ─────
    cf44        = fields.get("customfield_10044") or []
    environment = cf44[0].get("value", "") if cf44 else ""

    return [
        work, linked, reporting_source, customer, reporter_name,
        created, assignee_name, status, priority, all_labels, environment,
    ]


def sync(issues: list) -> None:
    creds = get_creds()
    gc    = gspread.authorize(creds)
    sh    = gc.open_by_key(SHEET_ID)
    ws    = sh.sheet1
    gid   = ws.id

    # ── Ensure a clean, unmerged sheet with our header in row 1 ──────────────
    existing  = ws.get_all_values()
    n         = len(HEADERS)
    header_ok = existing and existing[0][:n] == HEADERS

    if not header_ok:
        print("Resetting sheet — removing merges, frozen rows and stale content.")
        unmerge_and_reset_sheet(creds, gid)
        ws.clear()
        ws.update(range_name="A1", values=[HEADERS])
        existing = [HEADERS]

    # ── Build key → row-index map (1-based; row 1 = header) ──────────────────
    key_to_row: dict[str, int] = {}
    for i, row in enumerate(existing[1:], start=2):
        if row:
            k = extract_key(row[0])
            if k:
                key_to_row[k] = i

    # ── Classify each issue ───────────────────────────────────────────────────
    to_update: list[tuple[int, list]] = []
    to_append: list[list]             = []

    for issue in issues:
        key      = issue.get("key", "")
        row_data = parse_issue(issue)
        if key in key_to_row:
            to_update.append((key_to_row[key], row_data))
        else:
            to_append.append(row_data)

    # ── Batch-update existing rows ────────────────────────────────────────────
    if to_update:
        cells = [
            gspread.Cell(row_idx, col_idx, val)
            for row_idx, row_data in to_update
            for col_idx, val in enumerate(row_data, start=1)
        ]
        ws.update_cells(cells, value_input_option="USER_ENTERED")
        print(f"Updated  {len(to_update)} existing row(s).")

    # ── Batch-append new rows ─────────────────────────────────────────────────
    if to_append:
        ws.append_rows(to_append, value_input_option="USER_ENTERED")
        print(f"Appended {len(to_append)} new row(s).")

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"✓ Sync complete at {now} — {len(to_update)} updated, {len(to_append)} new.")


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    src  = sys.argv[1] if len(sys.argv) > 1 else None
    data = json.load(open(src) if src else sys.stdin)
    sync(data)
