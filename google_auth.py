"""Shared OAuth helper for Google APIs (Drive + Sheets)."""

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

from config import OAUTH_CREDENTIALS_PATH, OAUTH_TOKEN_PATH

SCOPES = [
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
]


def get_google_creds() -> Credentials:
    """Return valid Google OAuth credentials, prompting login if needed."""
    creds = None
    token_path = Path(OAUTH_TOKEN_PATH)

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(OAUTH_CREDENTIALS_PATH, SCOPES)
        creds = flow.run_local_server(port=0)

    token_path.write_text(creds.to_json())
    return creds
