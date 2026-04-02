# vic-hackathon - Martin Vildvinge's project

# Partner Incident Dashboard - Jira 
https://vicaiglobal.atlassian.net/jira/dashboards/10440

# Google Sheet Sub-project: Partner_Incident_Dashboard
https://docs.google.com/spreadsheets/d/1YxfK1KFekR-UYPeNNZXf7DgLCGlbU_FxliqQ3jrckTs/edit?usp=sharing

# Used Claude CoWork to create the project.

# Loom video with introduction and demo:
https://www.loom.com/share/ccfda88bb84b497d9f023abba40c9876

# jira-sync

Syncs Jira filter **11971** ("Customer Tickets") into a Google Sheet every 15 minutes via a Cowork scheduled task.

## Files

| File | Purpose |
|------|---------|
| `sync_to_sheets.py` | Main sync script — reads Jira issues as JSON, upserts rows in Google Sheets |
| `credentials.json` | ⚠️ Google service account key — **not committed**, keep locally |

## Column mapping

| Sheet column | Jira field |
|---|---|
| Work | `key` + `summary` (HYPERLINK formula) |
| Linked work item | `issuelinks[].inwardIssue / outwardIssue.key` |
| Reporting Source | `customfield_10070.value` |
| Customer | `customfield_10072` |
| Reporter | `reporter.displayName` |
| Created | `created` (ISO → YYYY-MM-DD) |
| Assignee | `assignee.displayName` |
| Status | `status.name` |
| Priority | `priority.name` |
| Labels | `labels[]` (comma-separated) |
| Environment | `customfield_10044[0].value` |

## Setup

1. Create a Google Cloud service account and download `credentials.json`.
2. Share the target Google Sheet with the service account email.
3. Install dependencies:
   ```
   pip install gspread google-auth requests
   ```
4. Run manually (pipe Jira JSON from a file):
   ```
   python sync_to_sheets.py issues.json
   ```

## Scheduled task

The Cowork scheduled task (`jira-to-gsheets-sync`) runs every 15 minutes:
- Fetches all issues from Jira filter 11971 via the Atlassian API
- Passes the JSON to `sync_to_sheets.py`
- Upserts rows in [this Google Sheet](https://docs.google.com/spreadsheets/d/1YxfK1KFekR-UYPeNNZXf7DgLCGlbU_FxliqQ3jrckTs/)
