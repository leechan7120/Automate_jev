from __future__ import annotations

import os
from pathlib import Path

from .google_calendar_mcp_bridge import SCOPES


def main() -> None:
    from google_auth_oauthlib.flow import InstalledAppFlow

    credentials_path = Path(os.environ.get("GOOGLE_CALENDAR_CREDENTIALS_FILE", "google-calendar-credentials.json"))
    token_path = Path(os.environ.get("GOOGLE_CALENDAR_TOKEN_FILE", ".automate-jev/google-calendar-token.json"))
    if not credentials_path.exists():
        raise SystemExit(f"Credentials file not found: {credentials_path}")
    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), SCOPES)
    credentials = flow.run_local_server(port=0, access_type="offline", prompt="consent")
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    print(f"Saved Google Calendar OAuth token to {token_path}")


if __name__ == "__main__":
    main()