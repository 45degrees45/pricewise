"""Shared Google credentials helper — service account auth."""

import json
import os

from google.oauth2 import service_account

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


def get_google_creds() -> service_account.Credentials:
    """Return Google service account credentials from GOOGLE_SERVICE_ACCOUNT_JSON env var."""
    sa_json = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]
    info = json.loads(sa_json)
    return service_account.Credentials.from_service_account_info(info, scopes=SCOPES)
