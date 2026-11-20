"""Google sign-in: credentials, token files, and turning Google errors into readable messages."""
import json
import os
import shutil
from pathlib import Path

from .config import DATA_DIR, USER_DATA_ROOT


SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.send",
]


GOOGLE_DIR = DATA_DIR
CREDENTIALS_FILE = GOOGLE_DIR / "google_credentials.json"
TOKEN_FILE = GOOGLE_DIR / "google_token.json"
# Written when Google refuses to renew the saved sign-in (revoked, or the 7-day limit for apps left in
# 'Testing'). It lets the app tell "your sign-in expired" apart from "you never signed in".
EXPIRED_FLAG = GOOGLE_DIR / "google_signin_expired"


# Preserve the existing OAuth setup from older installations.
_legacy_google_dir = USER_DATA_ROOT / "TutorTracker"


for _name in ("google_credentials.json", "google_token.json"):
    _old, _new = _legacy_google_dir / _name, GOOGLE_DIR / _name
    if not _new.exists() and _old.is_file():
        shutil.copy2(_old, _new)


PIP_GOOGLE = "pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib"


class GoogleError(Exception):
    """An error whose message is already fit to show the user."""
    def __init__(self, message, needs_signin=False):
        super().__init__(message)
        self.needs_signin = needs_signin


try:   # pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2.credentials import Credentials as GoogleCredentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build as google_build
    from googleapiclient.errors import HttpError
    HAVE_GOOGLE = True
except ImportError:
    HAVE_GOOGLE = False
    HttpError = RefreshError = Exception


def _read_token_info():
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _token_has_scopes(info):
    return bool(info) and set(SCOPES) <= set(info.get("scopes") or [])


def google_status():
    """Cheap local check (no network): missing_libs / no_credentials / expired / needs_signin / ready."""
    if not HAVE_GOOGLE:
        return "missing_libs"
    if not CREDENTIALS_FILE.exists():
        return "no_credentials"
    if not _token_has_scopes(_read_token_info()):
        return "expired" if EXPIRED_FLAG.exists() else "needs_signin"
    return "ready"


def install_credentials(src_path):
    """Validate the downloaded OAuth client JSON and copy it into GOOGLE_DIR."""
    try:
        data = json.loads(Path(src_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise GoogleError("That file couldn't be read as JSON. Choose the file you downloaded from "
                          "Google Cloud (it's usually called client_secret_....json).")
    if "installed" not in data:
        if "web" in data:
            raise GoogleError("That is a 'Web application' client. In Google Cloud > Credentials, create an "
                              "OAuth client ID of type 'Desktop app' and download that one instead.")
        raise GoogleError("That doesn't look like a Google OAuth client file (no 'installed' section).")
    GOOGLE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src_path, CREDENTIALS_FILE)
    _restrict(CREDENTIALS_FILE)
    TOKEN_FILE.unlink(missing_ok=True)          # a new client invalidates any old token


def _restrict(path):
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _save_token(creds):
    GOOGLE_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(creds.to_json(), encoding="utf-8")
    _restrict(TOKEN_FILE)
    EXPIRED_FLAG.unlink(missing_ok=True)


def sign_out(expired=False):
    """Forget the saved sign-in. expired=True records that Google ended it (shown as 'expired' in the app)."""
    TOKEN_FILE.unlink(missing_ok=True)
    if expired:
        try:
            EXPIRED_FLAG.write_text("Google refused to renew the saved sign-in.\n", encoding="utf-8")
        except OSError:
            pass
    else:
        EXPIRED_FLAG.unlink(missing_ok=True)


def mark_signin_expired():
    """Called when Google rejects the saved sign-in during a request (HTTP 401)."""
    if TOKEN_FILE.exists():
        sign_out(expired=True)


def google_credentials(interactive=True):
    """Valid credentials: reuse the saved token, refresh it, or (if interactive) open the browser to sign in.
    Missing scopes (e.g. after a new feature needs one) trigger a fresh sign-in automatically."""
    if not HAVE_GOOGLE:
        raise GoogleError(f"The Google libraries aren't installed. Run:\n{PIP_GOOGLE}")
    if not CREDENTIALS_FILE.exists():
        raise GoogleError("No Google credentials file has been chosen yet. Use 'Connect Google' first.",
                          needs_signin=True)
    creds = None
    info = _read_token_info()
    if _token_has_scopes(info):
        try:
            creds = GoogleCredentials.from_authorized_user_info(info, SCOPES)
        except (ValueError, KeyError):
            creds = None
    if creds and creds.valid:
        return creds
    if creds and creds.refresh_token:
        try:
            creds.refresh(GoogleRequest())
            _save_token(creds)
            return creds
        except RefreshError:                    # revoked, or expired (7-day limit while the app is in 'Testing')
            creds = None
            sign_out(expired=True)
    if not interactive:
        raise GoogleError("Google sign-in has expired. Press 'Connect Google' to sign in again.", needs_signin=True)
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
    kwargs = dict(port=0, prompt="consent", authorization_prompt_message="")
    try:
        creds = flow.run_local_server(timeout_seconds=300, **kwargs)
    except TypeError:                           # older google-auth-oauthlib without timeout_seconds
        creds = flow.run_local_server(**kwargs)
    if not _token_has_scopes({"scopes": list(creds.scopes or [])}):
        raise GoogleError("Google sign-in finished, but not every permission was granted. Sign in again and "
                          "tick BOTH the Calendar and Gmail checkboxes.", needs_signin=True)
    _save_token(creds)
    return creds


def _service(name, version, creds):
    # Built fresh for each background job: httplib2 objects aren't thread-safe.
    return google_build(name, version, credentials=creds, cache_discovery=False)


def needs_signin(exc):
    return isinstance(exc, GoogleError) and exc.needs_signin or (
        HAVE_GOOGLE and isinstance(exc, HttpError) and getattr(exc.resp, "status", None) == 401)


def friendly_error(exc):
    if isinstance(exc, GoogleError):
        return str(exc)
    name = type(exc).__name__
    if HAVE_GOOGLE and isinstance(exc, HttpError):
        status = getattr(exc.resp, "status", 0)
        reason, message = "", ""
        try:
            err = json.loads(exc.content.decode("utf-8"))["error"]
            message = err.get("message", "")
            reason = ((err.get("errors") or [{}])[0]).get("reason", "") or err.get("status", "")
        except Exception:
            pass
        low = (message + " " + reason).lower()
        if status == 401:
            return "Google rejected the saved sign-in. Press 'Connect Google' to sign in again."
        if status == 403 and ("has not been used" in low or "disabled" in low or reason in ("accessNotConfigured", "SERVICE_DISABLED")):
            return ("The Calendar or Gmail API isn't switched on for your Google Cloud project. In Google Cloud "
                    "Console > APIs & Services > Library, enable both 'Google Calendar API' and 'Gmail API', "
                    "wait a minute, then try again.")
        if status == 403 and ("insufficient" in low or reason == "insufficientPermissions"):
            return "This sign-in is missing a permission. Press 'Connect Google' and tick both permission boxes."
        if status in (403, 429) and "ratelimit" in low.replace(" ", ""):
            return "Google says you're sending requests too quickly. Wait a minute and try again."
        if status == 404:
            return "Google couldn't find that item (it may have been deleted)."
        if status >= 500:
            return "Google's servers had a problem. Wait a moment and try again."
        return f"Google returned an error ({status}): {message or reason or 'no details'}"
    if name == "RefreshError":
        return "Your Google sign-in has expired or was revoked. Press 'Connect Google' to sign in again."
    if isinstance(exc, (OSError, TimeoutError)) or name in ("ServerNotFoundError", "TransportError"):
        return "Couldn't reach Google. Check your internet connection and try again."
    if "scope has changed" in str(exc).lower():
        return "Google sign-in finished without every permission. Sign in again and tick both checkboxes."
    return f"Something went wrong: {exc}"
