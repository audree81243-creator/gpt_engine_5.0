"""
Minimal Chrome+CDP example for observing ChatGPT network traffic.

Notes:
- This script is for debugging. It looks for responses to ChatGPT's conversation endpoint.
- Many ChatGPT responses are streamed; the response body might not be available until the request finishes.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import time
from typing import Any, Dict, Iterable, Iterator, Optional, Tuple

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import SessionNotCreatedException

DEFAULT_URL = (
    "https://chatgpt.com/?temporary-chat=true&hints=search&q=latest+NVIDIA+earnings"
)


def _iter_perf_messages(log_entries: Iterable[Dict[str, Any]]) -> Iterator[Dict[str, Any]]:
    """Yield the inner Chrome performance-log message dicts (best-effort)."""
    for entry in log_entries:
        raw = entry.get("message")
        if not raw:
            continue
        try:
            outer = json.loads(raw)
        except Exception:
            continue
        msg = outer.get("message")
        if isinstance(msg, dict):
            yield msg


def _maybe_decode_cdp_body(body_obj: Dict[str, Any]) -> str:
    body = body_obj.get("body", "")
    if not isinstance(body, str):
        return ""
    if body_obj.get("base64Encoded"):
        try:
            return base64.b64decode(body).decode("utf-8", errors="replace")
        except Exception:
            return ""
    return body


def _make_driver(headless: bool = False) -> webdriver.Chrome:
    options = Options()

    # WSL / headless servers often don't have a display. If DISPLAY/WAYLAND_DISPLAY
    # is unset, Chrome will exit immediately unless headless is enabled.
    if not headless and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        headless = True

    if headless:
        # "new" headless is generally closer to real Chrome behavior.
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1280,840")

    # Common stability flags for CI/containers/WSL-like environments.
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")

    # Capture Chrome DevTools performance logs.
    options.add_experimental_option("perfLoggingPrefs", {"enableNetwork": True})
    options.set_capability("goog:loggingPrefs", {"performance": "ALL"})

    driver = webdriver.Chrome(options=options)

    # Enable Network domain so we can call Network.getResponseBody.
    # (If this fails for any reason, the rest of the script still works as a log observer.)
    try:
        driver.execute_cdp_cmd("Network.enable", {})
    except Exception:
        pass

    return driver


def _watch_conversation_responses(
    driver: webdriver.Chrome,
    *,
    watch_seconds: float,
    url_substring: str = "backend-api/conversation",
    poll_interval: float = 0.25,
) -> None:
    """Poll performance logs for conversation responses and (best-effort) fetch their bodies."""
    seen_response_received: Dict[str, str] = {}  # requestId -> url
    fetched_bodies: set[str] = set()

    t0 = time.monotonic()
    while (time.monotonic() - t0) < watch_seconds:
        # get_log() returns log entries since the last get_log() call.
        entries = driver.get_log("performance")

        for msg in _iter_perf_messages(entries):
            method = msg.get("method")
            params = msg.get("params") or {}
            if not isinstance(params, dict):
                params = {}

            # IMPORTANT: match method exactly to avoid Network.responseReceivedExtraInfo, etc.
            if method == "Network.responseReceived":
                response = params.get("response") or {}
                if not isinstance(response, dict):
                    response = {}
                resp_url = str(response.get("url") or "")
                if url_substring and (url_substring not in resp_url):
                    continue
                request_id = str(params.get("requestId") or "")
                if request_id:
                    seen_response_received[request_id] = resp_url
                    print(f"[NET] responseReceived: {resp_url} (requestId={request_id})")

            # Once loading finishes, attempt to pull the body via CDP.
            elif method == "Network.loadingFinished":
                request_id = str(params.get("requestId") or "")
                if not request_id:
                    continue
                if request_id not in seen_response_received:
                    continue
                if request_id in fetched_bodies:
                    continue

                fetched_bodies.add(request_id)
                resp_url = seen_response_received.get(request_id, "")
                try:
                    body_obj = driver.execute_cdp_cmd(
                        "Network.getResponseBody", {"requestId": request_id}
                    )
                    body_text = _maybe_decode_cdp_body(body_obj or {})
                    print(
                        f"[NET] got body for {resp_url} (requestId={request_id}) "
                        f"chars={len(body_text)}"
                    )

                    # Best-effort JSON parse; many ChatGPT responses are streamed and may not be JSON.
                    with_json = None
                    try:
                        with_json = json.loads(body_text)
                    except Exception:
                        with_json = None
                    if isinstance(with_json, dict):
                        print(f"[NET] json keys: {sorted(list(with_json.keys()))[:20]}")
                except Exception as e:
                    print(
                        f"[NET][WARN] failed to get body for requestId={request_id}: {e}"
                    )

        time.sleep(poll_interval)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--watch-seconds", type=float, default=30.0)
    parser.add_argument("--headless", action="store_true")
    args = parser.parse_args()

    headless = bool(args.headless)
    if not headless and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        headless = True
        print("[INFO] DISPLAY/WAYLAND_DISPLAY not set; enabling headless mode.")

    print(f"[INFO] Opening: {args.url}")
    print(f"[INFO] Watching network logs for {float(args.watch_seconds):.1f}s (headless={headless})")

    driver: Optional[webdriver.Chrome] = None
    try:
        try:
            driver = _make_driver(headless=headless)
        except SessionNotCreatedException as e:
            # If headed mode fails to start (common on WSL/servers without GUI),
            # retry once in headless mode.
            if not headless:
                print(f"[WARN] Headed Chrome failed to start: {e}")
                print("[WARN] Retrying in headless mode...")
                headless = True
                driver = _make_driver(headless=True)
            else:
                raise
        driver.get(args.url)
        _watch_conversation_responses(driver, watch_seconds=float(args.watch_seconds))
        return 0
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
