import os
import time
import requests


# Load env vars from a local .env file without external dependencies.
def _load_env_file(path=".env"):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                if not key:
                    continue
                if (value.startswith('"') and value.endswith('"')) or (
                    value.startswith("'") and value.endswith("'")
                ):
                    value = value[1:-1]
                os.environ.setdefault(key, value)
    except FileNotFoundError:
        pass


try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv:
    load_dotenv(override=False)
else:
    _load_env_file()


# Resolve the API key from argument or environment.
def _get_api_key(api_key=None):
    if api_key:
        return api_key
    env_key = os.getenv("BOOMLIFY_API_KEY", "").strip()
    if not env_key:
        raise RuntimeError("Missing BOOMLIFY_API_KEY. Set it in .env or pass api_key.")
    return env_key


# Resolve the API base URL from argument or environment.
def _get_base_url(base_url=None):
    if base_url:
        return base_url.rstrip("/")
    env_url = os.getenv("BOOMLIFY_BASE_URL", "").strip()
    if env_url:
        return env_url.rstrip("/")
    return "https://v1.boomlify.com/api/v1"


# Build request headers for Boomlify API calls.
def _headers(api_key=None, content_type=False):
    headers = {"X-API-Key": _get_api_key(api_key)}
    if content_type:
        headers["Content-Type"] = "application/json"
    return headers


# Convert boolean values to API-friendly query params.
def _bool_param(value):
    if isinstance(value, bool):
        return "true" if value else "false"
    return value


# Send a request and wrap common network errors with a simpler message.
def _request(method, url, **kwargs):
    timeout = kwargs.get("timeout")
    try:
        return requests.request(method, url, **kwargs)
    except requests.exceptions.Timeout as exc:
        timeout_label = f"{timeout}s" if timeout is not None else "the configured timeout"
        raise RuntimeError(f"Boomlify request timed out after {timeout_label}: {url}") from exc
    except requests.exceptions.RequestException as exc:
        raise RuntimeError(f"Boomlify request failed: {url}") from exc


# Best-effort extraction of an email address from a payload item.
def _extract_email_value(item):
    if not isinstance(item, dict):
        return None
    for key in ("email", "address", "email_address", "mailbox", "username"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            nested = value.get("email") or value.get("address")
            if isinstance(nested, str) and nested.strip():
                return nested.strip()
    return None


# Best-effort extraction of an email id from a payload item.
def _extract_id_value(item):
    if not isinstance(item, dict):
        return None
    for key in ("id", "uuid", "email_id"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    nested = item.get("email")
    if isinstance(nested, dict):
        value = nested.get("id")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


# Create a new temporary email address.
def create_email(api_key=None, time="permanent", domain=None, base_url=None):
    base_url = _get_base_url(base_url)
    allowed_times = {"10min", "1hour", "1day", "permanent"}
    time_value = time.strip() if isinstance(time, str) else time
    if time_value and time_value not in allowed_times:
        raise ValueError("time must be one of: 10min, 1hour, 1day, permanent")

    params = {"time": time_value} if time_value else {}
    if domain:
        params["domain"] = domain

    response = _request(
        "POST",
        f"{base_url}/emails/create",
        headers=_headers(api_key, content_type=True),
        params=params or None,
        timeout=5,
    )
    response.raise_for_status()
    return response.json()


# List permanent emails for the account.
def list_emails(api_key=None, limit=100, base_url=None, print_output=True):
    base_url = _get_base_url(base_url)
    response = _request(
        "GET",
        f"{base_url}/emails",
        headers=_headers(api_key),
        params={"limit": limit, "permanent_only": "true"},
        timeout=5,
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        payload = payload.get("data") or payload.get("emails") or payload.get("results") or []

    if print_output:
        print("Your existing Boomlify emails (permanent only):")
        if not payload:
            print("No emails found.")
        else:
            for item in payload:
                email_value = _extract_email_value(item) or "N/A"
                email_id = _extract_id_value(item) or "N/A"
                print(f"Email: {email_value}")
                print(f"ID:    {email_id}")
                print()

    return payload


# Fetch details for a specific email id.
def get_email_details(email_id, api_key=None, base_url=None):
    base_url = _get_base_url(base_url)
    response = _request(
        "GET",
        f"{base_url}/emails/{email_id}",
        headers=_headers(api_key),
        timeout=5,
    )
    response.raise_for_status()
    return response.json()


# Fetch the latest message subject for a specific email id.
def get_email_messages(email_id, api_key=None, limit=50, offset=0, base_url=None):
    base_url = _get_base_url(base_url)
    if "@" in email_id:
        emails = list_emails(api_key=api_key, base_url=base_url, print_output=False)
        resolved_id = None
        for item in emails:
            if _extract_email_value(item) == email_id:
                resolved_id = _extract_id_value(item)
                break
        if not resolved_id:
            raise ValueError("Email address not found for this account.")
        email_id = resolved_id
    response = _request(
        "GET",
        f"{base_url}/emails/{email_id}/messages",
        headers=_headers(api_key),
        params={"limit": limit, "offset": offset},
        timeout=5,
    )
    response.raise_for_status()
    payload = response.json()
    if isinstance(payload, dict):
        messages = payload.get("messages") or payload.get("data") or payload.get("results") or []
    else:
        messages = payload
    if isinstance(messages, list) and messages and isinstance(messages[0], dict):
        return messages[0].get("subject")
    return None


# Fetch email details and messages together.
def read_email(email_id, api_key=None, limit=50, offset=0, base_url=None):
    details = get_email_details(email_id, api_key=api_key, base_url=base_url)
    messages = get_email_messages(
        email_id,
        api_key=api_key,
        limit=limit,
        offset=offset,
        base_url=base_url
    )
    return {"details": details, "messages": messages}


# Get the latest message for a given email id or email address.
def get_latest_message(email_or_id, api_key=None, base_url=None, limit=1, offset=0):
    email_id = email_or_id
    if "@" in email_or_id:
        emails = list_emails(api_key=api_key, base_url=base_url, print_output=False)
        email_id = None
        for item in emails:
            if _extract_email_value(item) == email_or_id:
                email_id = _extract_id_value(item)
                break
        if not email_id:
            raise ValueError("Email address not found for this account.")

    last_error = None
    for attempt in range(3):
        try:
            subject = get_email_messages(
                email_id,
                api_key=api_key,
                limit=limit,
                offset=offset,
                base_url=base_url
            )
            break
        except RuntimeError as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1)
                continue
            raise

    if last_error is not None and "subject" not in locals():
        raise last_error
    return subject


# Delete a specific email id.
def delete_email(email_id, api_key=None, base_url=None):
    base_url = _get_base_url(base_url)
    response = _request(
        "DELETE",
        f"{base_url}/emails/{email_id}",
        headers=_headers(api_key),
        timeout=5,
    )
    response.raise_for_status()
    return response.json()


# Retrieve API usage statistics for the account.
def get_account_usage(api_key=None, base_url=None):
    base_url = _get_base_url(base_url)
    response = _request(
        "GET",
        f"{base_url}/account/usage",
        headers=_headers(api_key),
        timeout=5,
    )
    response.raise_for_status()
    return response.json()




if __name__ == "__main__":
    list_emails()
    result = create_email()
    print(result)
    list_emails()

