import ast
import base64
import json
import os
import re
import time
from contextlib import suppress
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qs, urlsplit

from seleniumbase import SB
import mycdp.network as cdp_network


NETWORK_LOG = Path("screenshots/network_events.jsonl")
CONVERSATION_EVENT_LOG = Path("screenshots/conversation_events.jsonl")
CONVERSATION_STREAM_DIR = Path("screenshots/conversation_streams")
CONVERSATION_BODY_DIR = Path("screenshots/conversation_bodies")
CONVERSATION_REQUEST_DIR = Path("screenshots/conversation_requests")
SUMMARY_FILE = Path("screenshots/conversation_answer_citations.json")
STEP_SCREENSHOT_DIR = Path("screenshots/step_screenshots")

MAIN_PATHS = (
    "/backend-anon/f/conversation",
    "/backend-api/f/conversation",
)
CONVERSATION_HINT_PATHS = (
    "/backend-anon/f/conversation",
    "/backend-api/f/conversation",
    "/backend-anon/conversation",
    "/backend-api/conversation",
)


_lock = Lock()
_request_url = {}
_conversation_ids = set()
_stream_enabled = set()
_saved_response_ids = set()
_saved_request_ids = set()
_network_count = 0
_conversation_count = 0
_cdp_page = None
_cdp_loop = None
_request_seen_at = {}

_request_id_raw_re = re.compile(r"request_id=RequestId\('([^']+)'\)")
_request_url_raw_re = re.compile(r"request=Request\(url='([^']+)'")
_response_url_raw_re = re.compile(r"response=Response\(url='([^']+)'")
_data_raw_re = re.compile(r"\bdata=(None|'(?:[^'\\]|\\.)*')")
_http_url_re = re.compile(r"https?://[^\s<>'\"`]+", re.IGNORECASE)


def _ensure_paths():
    NETWORK_LOG.parent.mkdir(parents=True, exist_ok=True)
    CONVERSATION_EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    CONVERSATION_STREAM_DIR.mkdir(parents=True, exist_ok=True)
    CONVERSATION_BODY_DIR.mkdir(parents=True, exist_ok=True)
    CONVERSATION_REQUEST_DIR.mkdir(parents=True, exist_ok=True)
    STEP_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    NETWORK_LOG.touch(exist_ok=True)
    CONVERSATION_EVENT_LOG.touch(exist_ok=True)


def _clear_previous_outputs():
    with suppress(Exception):
        NETWORK_LOG.write_text("", encoding="utf-8")
    with suppress(Exception):
        CONVERSATION_EVENT_LOG.write_text("", encoding="utf-8")
    for folder in (
        CONVERSATION_STREAM_DIR,
        CONVERSATION_BODY_DIR,
        CONVERSATION_REQUEST_DIR,
        STEP_SCREENSHOT_DIR,
    ):
        with suppress(Exception):
            for p in folder.glob("*"):
                if p.is_file():
                    p.unlink()


def _step_label_to_filename(label: str):
    s = re.sub(r"[^a-zA-Z0-9._-]+", "_", str(label).strip())
    s = re.sub(r"_+", "_", s).strip("_.")
    return s or "step"


def _save_step_screenshot(sb, label: str):
    if os.getenv("STEP_SCREENSHOTS", "1") != "1":
        return ""
    filename = f"{int(time.time() * 1000)}_{_step_label_to_filename(label)}.png"
    path = STEP_SCREENSHOT_DIR / filename
    with suppress(Exception):
        STEP_SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
        sb.save_screenshot(str(path))
        print(f"[SHOT] {path}")
        return str(path)
    return ""


def _load_dotenv_if_present(path: str = ".env"):
    env_path = Path(path)
    if not env_path.exists() or not env_path.is_file():
        return
    with suppress(Exception):
        lines = env_path.read_text(encoding="utf-8", errors="replace").splitlines()
        for raw in lines:
            line = str(raw or "").strip()
            if not line or line.startswith("#"):
                continue
            if line.lower().startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            if not key or key in os.environ:
                continue
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            os.environ[key] = val


def _append_jsonl(path: Path, row: dict):
    with _lock, path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _append_network_row(row: dict):
    global _network_count
    _append_jsonl(NETWORK_LOG, row)
    _network_count += 1
    if _network_count <= 5 or _network_count % 100 == 0:
        print(f"[NETWORK] Logged {_network_count} events")


def _append_conversation_row(row: dict):
    global _conversation_count
    _append_jsonl(CONVERSATION_EVENT_LOG, row)
    _conversation_count += 1
    if _conversation_count <= 10 or _conversation_count % 25 == 0:
        print(f"[CONVERSATION] Logged {_conversation_count} events")


def _append_stream_chunk(request_id: str, text: str, source: str):
    if not text:
        return
    stream_file = CONVERSATION_STREAM_DIR / f"{request_id}.stream.txt"
    with _lock, stream_file.open("a", encoding="utf-8") as f:
        f.write(text)
    _append_conversation_row(
        {
            "ts": time.time(),
            "method": "Conversation.StreamChunkSaved",
            "request_id": request_id,
            "source": source,
            "chars": len(text),
            "file": str(stream_file),
        }
    )


def _normalize_path(url: str) -> str:
    if not url:
        return ""
    try:
        return (urlsplit(url).path or "").rstrip("/").lower()
    except Exception:
        return str(url).strip().lower()


def _is_conversation_url(url: str) -> bool:
    p = _normalize_path(url)
    return any(token in p for token in CONVERSATION_HINT_PATHS)


def _is_main_url(url: str) -> bool:
    p = _normalize_path(url)
    return any(p.endswith(x) for x in MAIN_PATHS)


def _is_prepare_url(url: str) -> bool:
    p = _normalize_path(url)
    return any(p.endswith(x + "/prepare") for x in MAIN_PATHS)


def _request_sort_key(request_id: str):
    out = []
    for part in str(request_id).split("."):
        with suppress(Exception):
            out.append(int(part))
            continue
        out.append(part)
    return tuple(out)


def _decode_cdp_data(data):
    if data is None:
        return ""
    if not isinstance(data, str):
        data = str(data)
    try:
        decoded = base64.b64decode(data, validate=False)
        text = decoded.decode("utf-8", errors="replace")
        # Guard against accidental binary decode from plain text.
        bad = sum(ch < " " and ch not in "\r\n\t" for ch in text[:200])
        if bad > 6:
            return data
        return text
    except Exception:
        return data


def _event_params(event):
    return event.to_json() if hasattr(event, "to_json") else {"raw": str(event)}


def _extract_data_chunk(params: dict):
    data = params.get("data")
    if data:
        return data
    raw = params.get("raw")
    if not isinstance(raw, str):
        return None
    m = _data_raw_re.search(raw)
    if not m:
        return None
    val = m.group(1)
    if val == "None":
        return None
    with suppress(Exception):
        return ast.literal_eval(val)
    return None


def _extract_request_id_and_url(params: dict):
    request_id = params.get("requestId") or params.get("request_id")
    request_id = str(request_id) if request_id is not None else None

    url = None
    req = params.get("request")
    if isinstance(req, dict):
        url = req.get("url") or req.get("URL")
    resp = params.get("response")
    if not url and isinstance(resp, dict):
        url = resp.get("url") or resp.get("URL")

    raw = params.get("raw")
    if isinstance(raw, str):
        if not request_id:
            m = _request_id_raw_re.search(raw)
            if m:
                request_id = m.group(1)
        if not url:
            m = _request_url_raw_re.search(raw)
            if m:
                url = m.group(1)
        if not url:
            m = _response_url_raw_re.search(raw)
            if m:
                url = m.group(1)

    return request_id, url


async def _save_request_body(request_id: str):
    if request_id in _saved_request_ids:
        return
    _saved_request_ids.add(request_id)
    try:
        post_data = await _cdp_page.send(
            cdp_network.get_request_post_data(cdp_network.RequestId(request_id))
        )
    except Exception as e:
        _append_conversation_row(
            {
                "ts": time.time(),
                "method": "Conversation.RequestBodyUnavailable",
                "request_id": request_id,
                "error": str(e),
            }
        )
        return

    path = CONVERSATION_REQUEST_DIR / f"{request_id}.request.txt"
    with _lock, path.open("w", encoding="utf-8") as f:
        f.write(post_data or "")
    _append_conversation_row(
        {
            "ts": time.time(),
            "method": "Conversation.RequestBodySaved",
            "request_id": request_id,
            "chars": len(post_data or ""),
            "file": str(path),
        }
    )


async def _enable_stream(request_id: str, quiet: bool = False):
    if request_id in _stream_enabled:
        return True
    try:
        buffered = await _cdp_page.send(
            cdp_network.stream_resource_content(cdp_network.RequestId(request_id))
        )
    except Exception as e:
        if not quiet:
            _append_conversation_row(
                {
                    "ts": time.time(),
                    "method": "Conversation.StreamEnableFailed",
                    "request_id": request_id,
                    "error": str(e),
                }
            )
        return False

    _stream_enabled.add(request_id)
    if buffered:
        _append_stream_chunk(
            request_id,
            _decode_cdp_data(buffered),
            "Network.streamResourceContent.bufferedData",
        )
    _append_conversation_row(
        {
            "ts": time.time(),
            "method": "Conversation.StreamEnabled",
            "request_id": request_id,
        }
    )
    return True


async def _save_response_body(request_id: str):
    if request_id in _saved_response_ids:
        return
    _saved_response_ids.add(request_id)
    try:
        body, is_b64 = await _cdp_page.send(
            cdp_network.get_response_body(cdp_network.RequestId(request_id))
        )
    except Exception as e:
        _append_conversation_row(
            {
                "ts": time.time(),
                "method": "Conversation.ResponseBodyUnavailable",
                "request_id": request_id,
                "error": str(e),
            }
        )
        return

    text = _decode_cdp_data(body) if is_b64 else (body or "")
    path = CONVERSATION_BODY_DIR / f"{request_id}.body.txt"
    with _lock, path.open("w", encoding="utf-8") as f:
        f.write(text)
    _append_conversation_row(
        {
            "ts": time.time(),
            "method": "Conversation.ResponseBodySaved",
            "request_id": request_id,
            "chars": len(text),
            "base64_encoded": bool(is_b64),
            "file": str(path),
        }
    )

    # If stream chunks weren't captured separately, persist SSE body as stream.
    # ChatGPT conversation endpoint often returns full event-stream in response body.
    with suppress(Exception):
        url = _request_url.get(request_id, "")
        if _is_main_url(url) and isinstance(text, str) and "event:" in text and "data:" in text:
            stream_file = CONVERSATION_STREAM_DIR / f"{request_id}.stream.txt"
            if (not stream_file.exists()) or stream_file.stat().st_size == 0:
                with _lock, stream_file.open("w", encoding="utf-8") as sf:
                    sf.write(text)
                _append_conversation_row(
                    {
                        "ts": time.time(),
                        "method": "Conversation.StreamFromBodySaved",
                        "request_id": request_id,
                        "chars": len(text),
                        "file": str(stream_file),
                    }
                )


async def _handle_event(method: str, event, request_id=None, url=None, data_chunk=None):
    params = _event_params(event)
    if not request_id or not url:
        rid2, url2 = _extract_request_id_and_url(params)
        request_id = request_id or rid2
        url = url or url2
    request_id = str(request_id) if request_id is not None else None

    if request_id and url:
        _request_url[request_id] = url
    if request_id and method == "Network.RequestWillBeSent":
        _request_seen_at[request_id] = time.time()
    if request_id and not url:
        url = _request_url.get(request_id)

    row = {
        "ts": time.time(),
        "method": method,
        "request_id": request_id,
        "url": url,
        "params": params,
    }
    _append_network_row(row)

    is_conv = False
    if request_id and request_id in _conversation_ids:
        is_conv = True
    if _is_conversation_url(url):
        is_conv = True
        if request_id:
            _conversation_ids.add(request_id)
    if not is_conv:
        return

    _append_conversation_row(row)

    if request_id and method == "Network.RequestWillBeSent":
        await _save_request_body(request_id)
        if _is_main_url(url):
            await _enable_stream(request_id, quiet=True)
    if request_id and method == "Network.ResponseReceived":
        await _enable_stream(request_id, quiet=False)
    if request_id and method == "Network.DataReceived":
        chunk = data_chunk
        if not chunk:
            chunk = _extract_data_chunk(params)
        if chunk:
            _append_stream_chunk(request_id, _decode_cdp_data(chunk), method)
    if request_id and method == "Network.EventSourceMessageReceived":
        chunk = getattr(event, "data", None)
        if not chunk:
            chunk = _extract_data_chunk(params)
        if chunk:
            _append_stream_chunk(request_id, str(chunk) + "\n", method)
    if request_id and method in ("Network.LoadingFinished", "Network.LoadingFailed"):
        await _save_response_body(request_id)


async def _on_request(event: cdp_network.RequestWillBeSent):
    url = None
    with suppress(Exception):
        url = event.request.url
    await _handle_event(
        "Network.RequestWillBeSent",
        event,
        request_id=getattr(event, "request_id", None),
        url=url,
    )


async def _on_response(event: cdp_network.ResponseReceived):
    url = None
    with suppress(Exception):
        url = event.response.url
    await _handle_event(
        "Network.ResponseReceived",
        event,
        request_id=getattr(event, "request_id", None),
        url=url,
    )


async def _on_data(event: cdp_network.DataReceived):
    await _handle_event(
        "Network.DataReceived",
        event,
        request_id=getattr(event, "request_id", None),
        data_chunk=getattr(event, "data", None),
    )


async def _on_eventsource(event: cdp_network.EventSourceMessageReceived):
    await _handle_event(
        "Network.EventSourceMessageReceived",
        event,
        request_id=getattr(event, "request_id", None),
        data_chunk=getattr(event, "data", None),
    )


async def _on_finish(event: cdp_network.LoadingFinished):
    await _handle_event(
        "Network.LoadingFinished",
        event,
        request_id=getattr(event, "request_id", None),
    )


async def _on_fail(event: cdp_network.LoadingFailed):
    await _handle_event(
        "Network.LoadingFailed",
        event,
        request_id=getattr(event, "request_id", None),
    )


def _start_capture(sb):
    global _cdp_page, _cdp_loop
    _ensure_paths()
    _cdp_page = sb.cdp.page
    _cdp_loop = sb.cdp.loop

    sb.cdp.add_handler(cdp_network.RequestWillBeSent, _on_request)
    sb.cdp.add_handler(cdp_network.ResponseReceived, _on_response)
    sb.cdp.add_handler(cdp_network.DataReceived, _on_data)
    sb.cdp.add_handler(cdp_network.EventSourceMessageReceived, _on_eventsource)
    sb.cdp.add_handler(cdp_network.LoadingFinished, _on_finish)
    sb.cdp.add_handler(cdp_network.LoadingFailed, _on_fail)

    sb.cdp.loop.run_until_complete(
        sb.cdp.page.send(
            cdp_network.enable(
                max_total_buffer_size=100 * 1024 * 1024,
                max_resource_buffer_size=50 * 1024 * 1024,
                max_post_data_size=20 * 1024 * 1024,
                enable_durable_messages=True,
            )
        )
    )
    print(f"[NETWORK] Full log: {NETWORK_LOG.resolve()}")
    print(f"[CONVERSATION] Event log: {CONVERSATION_EVENT_LOG.resolve()}")
    print(f"[CONVERSATION] Stream dir: {CONVERSATION_STREAM_DIR.resolve()}")
    print(f"[CONVERSATION] Body dir: {CONVERSATION_BODY_DIR.resolve()}")


def _current_main_ids():
    out = set()
    for rid, url in _request_url.items():
        if _is_main_url(url):
            out.add(rid)
    return out


def _latest_main_request_id(after_ts=None):
    candidates = []
    for rid, url in _request_url.items():
        if not _is_main_url(url):
            continue
        seen_ts = _request_seen_at.get(rid, 0.0)
        if after_ts is not None and seen_ts < after_ts:
            continue
        candidates.append((seen_ts, rid))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], _request_sort_key(x[1])))
    return candidates[-1][1]


def _wait_for_new_main_request(previous_ids, timeout=120, sb=None, label="step5_wait_main_request"):
    t0 = time.time()
    previous_ids = set(previous_ids)
    last_log_at = 0.0
    last_shot_at = 0.0
    while time.time() - t0 < timeout:
        current = _current_main_ids()
        new_ids = current - previous_ids
        if new_ids:
            if sb:
                _save_step_screenshot(sb, f"{label}_detected")
            return sorted(new_ids, key=_request_sort_key)[-1]
        now = time.time()
        if now - last_log_at >= 10:
            elapsed = int(now - t0)
            print(
                f"[WAIT] main_request: {elapsed}s/{timeout}s "
                f"(known_main_ids={len(current)})"
            )
            last_log_at = now
        if sb and (now - last_shot_at >= 20):
            elapsed = int(now - t0)
            _save_step_screenshot(sb, f"{label}_{elapsed}s")
            last_shot_at = now
        time.sleep(0.2)
    if sb:
        _save_step_screenshot(sb, f"{label}_timeout")
    return None


def _wait_for_main_request_since(since_ts, timeout=120):
    t0 = time.time()
    last_log_at = 0.0
    while time.time() - t0 < timeout:
        rid = _latest_main_request_id(after_ts=since_ts)
        if rid:
            return rid
        now = time.time()
        if now - last_log_at >= 10:
            elapsed = int(now - t0)
            print(f"[WAIT] auto_query_request: {elapsed}s/{timeout}s")
            last_log_at = now
        time.sleep(0.2)
    return None


def _wait_for_capture_done(request_id: str, timeout=420, idle_secs=20):
    stream_file = CONVERSATION_STREAM_DIR / f"{request_id}.stream.txt"
    body_file = CONVERSATION_BODY_DIR / f"{request_id}.body.txt"
    t0 = time.time()
    last_size = 0
    last_growth_at = None
    last_log_at = 0.0

    while time.time() - t0 < timeout:
        if body_file.exists() and body_file.stat().st_size > 0:
            return True
        if stream_file.exists():
            size = stream_file.stat().st_size
            if size > 0:
                if size != last_size:
                    last_size = size
                    last_growth_at = time.time()
                elif last_growth_at and (time.time() - last_growth_at) >= idle_secs:
                    return True
                with suppress(Exception):
                    with stream_file.open("rb") as f:
                        f.seek(max(0, size - 4096))
                        tail = f.read().decode("utf-8", errors="replace")
                    if "[DONE]" in tail:
                        return True
        now = time.time()
        if now - last_log_at >= 10:
            elapsed = int(now - t0)
            bsz = body_file.stat().st_size if body_file.exists() else 0
            ssz = stream_file.stat().st_size if stream_file.exists() else 0
            print(
                f"[WAIT] capture_done: {elapsed}s/{timeout}s "
                f"(body={bsz}B stream={ssz}B)"
            )
            last_log_at = now
        time.sleep(0.25)
    return False


def _find_sse_request_id_from_bodies(after_ts):
    candidates = []
    for body_file in CONVERSATION_BODY_DIR.glob("*.body.txt"):
        with suppress(Exception):
            if body_file.stat().st_mtime < after_ts:
                continue
            text = body_file.read_text(encoding="utf-8", errors="replace")
            if "event: delta_encoding" not in text and "data: [DONE]" not in text:
                continue
            rid = body_file.name.replace(".body.txt", "")
            candidates.append((body_file.stat().st_mtime, rid))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], _request_sort_key(x[1])))
    return candidates[-1][1]


def _force_stream_enable(request_id: str, timeout=45):
    if request_id in _stream_enabled:
        return True
    if not _cdp_loop or not _cdp_page:
        return False
    t0 = time.time()
    while time.time() - t0 < timeout:
        if request_id in _stream_enabled:
            return True
        with suppress(Exception):
            ok = _cdp_loop.run_until_complete(_enable_stream(request_id, quiet=True))
            if ok:
                return True
        time.sleep(0.3)
    return request_id in _stream_enabled


def _close_popups(sb):
    script = r"""
(() => {
  let closed = 0;
  const clickNode = (n) => {
    if (!n || !n.getClientRects || !n.getClientRects().length) return false;
    n.click();
    closed += 1;
    return true;
  };
  for (const n of document.querySelectorAll('button[data-testid="close-button"], button[aria-label="Close"]')) {
    clickNode(n);
  }
  const targets = new Set(['continue', 'ok', 'not now', 'stay logged out', 'keep logged out']);
  for (const n of Array.from(document.querySelectorAll('button,a,[role="button"]'))) {
    const t = (n.innerText || n.textContent || '').trim().toLowerCase();
    if (targets.has(t)) {
      clickNode(n);
    }
  }
  return closed;
})();
"""
    with suppress(Exception):
        closed = int(sb.execute_script(script) or 0)
        if closed:
            print(f"[UI] Closed {closed} popup buttons")


def _is_guest_gate_modal_visible(sb):
    script = r"""
(() => {
  const txt = ((document.body && document.body.innerText) || '').toLowerCase();
  const hasStayLoggedOut = txt.includes('stay logged out');
  const hasThanks = txt.includes('thanks for trying chatgpt');
  const hasLoginPrompt = txt.includes('log in or sign up');
  return Boolean(hasStayLoggedOut || hasThanks || hasLoginPrompt);
})();
"""
    with suppress(Exception):
        return bool(sb.execute_script(script))
    return False


def _dismiss_guest_gate(sb, retries=3):
    click_script = r"""
(() => {
  const textTargets = ['stay logged out', 'keep logged out', 'not now', 'continue'];
  for (const n of Array.from(document.querySelectorAll('button,a,[role="button"]'))) {
    const t = (n.innerText || n.textContent || '').trim().toLowerCase();
    if (textTargets.includes(t) && n.getClientRects && n.getClientRects().length) {
      n.click();
      return t;
    }
  }
  return '';
})();
"""
    attempts = max(1, int(retries))
    for idx in range(attempts):
        if not _is_guest_gate_modal_visible(sb):
            return True
        print(f"[UI] Guest gate visible (attempt {idx + 1}/{attempts})")
        clicked = ""
        with suppress(Exception):
            clicked = str(sb.execute_script(click_script) or "")
        if clicked:
            print(f"[UI] Guest gate dismissed via: {clicked}")
        sb.sleep(1.2)
        _close_popups(sb)
        sb.sleep(0.6)
        if not _is_guest_gate_modal_visible(sb):
            return True
    return not _is_guest_gate_modal_visible(sb)


def _is_login_wall(sb):
    script = r"""
(() => {
  const hasPrompt = !!document.querySelector('#prompt-textarea');
  const hasLoginBtn = !!document.querySelector('[data-testid="login-button"]');
  const hasSignupBtn = !!document.querySelector('[data-testid="signup-button"]');
  const hasEmail = !!document.querySelector('input[placeholder*="Email" i]');
  const txt = (document.body && document.body.innerText) || '';
  const hasText = /log in or sign up/i.test(txt);
  return Boolean((hasLoginBtn || hasSignupBtn || hasEmail || hasText) && !hasPrompt);
})();
"""
    with suppress(Exception):
        return bool(sb.execute_script(script))
    return False


def _extract_prompt_from_url(url: str):
    try:
        query = parse_qs(urlsplit(url).query)
        q = query.get("q")
        if q and q[0].strip():
            return q[0].strip()
    except Exception:
        pass
    return ""


def _composer_text(sb):
    script = r"""
(() => {
  const el = document.querySelector('#prompt-textarea');
  if (!el) return '';
  if (el.isContentEditable) return (el.innerText || el.textContent || '').trim();
  if ('value' in el) return (el.value || '').trim();
  return (el.textContent || '').trim();
})();
"""
    with suppress(Exception):
        return str(sb.execute_script(script) or "").strip()
    return ""


def _is_composer_ready(sb):
    script = r"""
(() => {
  const el = document.querySelector('#prompt-textarea');
  if (!el) return false;
  if (!el.getClientRects || !el.getClientRects().length) return false;
  if (el.closest('[aria-hidden="true"], [hidden], [inert]')) return false;
  return true;
})();
"""
    with suppress(Exception):
        return bool(sb.execute_script(script))
    return False


def _wait_for_composer_ready(sb, timeout=20):
    t0 = time.time()
    last_log_at = 0.0
    while time.time() - t0 < timeout:
        if _is_composer_ready(sb):
            return True
        now = time.time()
        if now - last_log_at >= 5:
            elapsed = int(now - t0)
            print(f"[WAIT] composer_ready: {elapsed}s/{timeout}s")
            last_log_at = now
        _dismiss_guest_gate(sb, retries=1)
        time.sleep(0.25)
    return _is_composer_ready(sb)


def _composer_submit(sb, prompt: str):
    if not prompt:
        return {"ok": False, "reason": "empty_prompt"}

    if not _wait_for_composer_ready(sb, timeout=int(os.getenv("COMPOSER_READY_TIMEOUT", "20"))):
        return {"ok": False, "reason": "composer_not_ready"}

    # Avoid long selector waits by setting text via JS directly.
    set_ok = False
    set_script = (
        "(() => {\n"
        f"const prompt = {json.dumps(prompt)};\n"
        "const el = document.querySelector('#prompt-textarea');\n"
        "if (!el) return false;\n"
        "el.focus();\n"
        "if (el.isContentEditable) {\n"
        "  el.textContent = '';\n"
        "  el.dispatchEvent(new Event('input', { bubbles: true }));\n"
        "  el.textContent = prompt;\n"
        "  el.dispatchEvent(new Event('input', { bubbles: true }));\n"
        "  return true;\n"
        "}\n"
        "if ('value' in el) {\n"
        "  el.value = prompt;\n"
        "  el.dispatchEvent(new Event('input', { bubbles: true }));\n"
        "  return true;\n"
        "}\n"
        "return false;\n"
        "})();"
    )
    with suppress(Exception):
        set_ok = bool(sb.execute_script(set_script))

    typed_text = _composer_text(sb)
    if (not set_ok) or (prompt.lower() not in typed_text.lower()):
        return {
            "ok": False,
            "reason": "prompt_not_typed",
            "typed_preview": typed_text[:120],
        }

    # Submit by JS click first (non-blocking), then Enter fallback.
    click_script = r"""
(() => {
  const btn = document.querySelector('button[data-testid="send-button"]');
  if (!btn || !btn.getClientRects || !btn.getClientRects().length) return false;
  if (btn.disabled) return false;
  btn.click();
  return true;
})();
"""
    with suppress(Exception):
        if bool(sb.execute_script(click_script)):
            return {"ok": True, "method": "click_send_button_js"}

    with suppress(Exception):
        sb.press_keys("#prompt-textarea", "\n")
        return {"ok": True, "method": "enter"}
    return {"ok": False, "reason": "submit_failed"}


def _read_text(path: Path):
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _parse_sse_blocks(text: str):
    events = []
    event_name = None
    data_lines = []
    for line in text.splitlines():
        if line.startswith("event:"):
            event_name = line.split(":", 1)[1].strip()
        elif line.startswith("data:"):
            data_lines.append(line.split(":", 1)[1].lstrip())
        elif line.strip() == "":
            if data_lines:
                events.append(
                    {
                        "event": event_name or "message",
                        "data": "\n".join(data_lines),
                    }
                )
            event_name = None
            data_lines = []
    if data_lines:
        events.append({"event": event_name or "message", "data": "\n".join(data_lines)})
    return events


def _walk_messages(node, out):
    if isinstance(node, dict):
        author = node.get("author")
        content = node.get("content")
        if isinstance(author, dict) and isinstance(content, dict):
            out.append(node)
        for v in node.values():
            _walk_messages(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_messages(v, out)


def _walk_urls(node, out):
    if isinstance(node, dict):
        url = node.get("url")
        if isinstance(url, str) and url.startswith("http"):
            out.append(
                {
                    "url": url,
                    "title": node.get("title") if isinstance(node.get("title"), str) else "",
                    "snippet": node.get("snippet") if isinstance(node.get("snippet"), str) else "",
                }
            )
        for v in node.values():
            _walk_urls(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_urls(v, out)


def _clean_url(url: str):
    if not isinstance(url, str):
        return ""
    out = url.strip()
    out = out.strip("<>\"'()[]{}")
    while out and out[-1] in ".,;:!?)\"]}'":
        out = out[:-1]
    return out


def _walk_url_like_fields(node, out):
    if isinstance(node, dict):
        for k, v in node.items():
            key = str(k).lower()
            if isinstance(v, str):
                cand = _clean_url(v)
                if (
                    cand.startswith("http")
                    and (key.endswith("url") or key.endswith("uri") or key in ("href", "link"))
                ):
                    out.append(
                        {
                            "url": cand,
                            "title": "",
                            "snippet": "",
                            "source": f"field:{key}",
                        }
                    )
            _walk_url_like_fields(v, out)
    elif isinstance(node, list):
        for v in node:
            _walk_url_like_fields(v, out)


def _extract_urls_from_text(text: str, source: str):
    out = []
    if not isinstance(text, str) or not text:
        return out
    for m in _http_url_re.finditer(text):
        cand = _clean_url(m.group(0))
        if not cand.startswith("http"):
            continue
        out.append({"url": cand, "title": "", "snippet": "", "source": source})
    return out


def _content_to_text(content):
    if not isinstance(content, dict):
        return ""
    parts = content.get("parts")
    if isinstance(parts, list):
        items = [str(x) for x in parts if str(x).strip()]
        if items:
            return "\n".join(items).strip()
    text = content.get("text")
    if isinstance(text, str):
        return text.strip()
    return ""


def _build_summary(request_id: str, prompt: str):
    body_file = CONVERSATION_BODY_DIR / f"{request_id}.body.txt"
    stream_file = CONVERSATION_STREAM_DIR / f"{request_id}.stream.txt"
    req_file = CONVERSATION_REQUEST_DIR / f"{request_id}.request.txt"

    stream_text = _read_text(stream_file).strip()
    body_text = _read_text(body_file).strip()
    # Avoid duplicate parse when stream/body contain the same SSE text.
    if stream_text and body_text:
        raw_text = stream_text if body_text in stream_text else f"{stream_text}\n{body_text}"
    else:
        raw_text = stream_text or body_text
    sse_events = _parse_sse_blocks(raw_text)

    parsed_json = []
    for item in sse_events:
        data = item.get("data", "")
        if not isinstance(data, str):
            continue
        d = data.strip()
        if not d or d == "[DONE]":
            continue
        if d.startswith("{") or d.startswith("["):
            with suppress(Exception):
                parsed_json.append(json.loads(d))

    messages = []
    urls = []
    delta_parts = []
    assistant_seen = False
    assistant_model_slugs = set()
    assistant_citation_counts = []
    assistant_search_group_counts = []
    for obj in parsed_json:
        _walk_messages(obj, messages)
        _walk_urls(obj, urls)
        _walk_url_like_fields(obj, urls)
        if isinstance(obj, dict):
            msg = obj.get("v", {}).get("message") if isinstance(obj.get("v"), dict) else None
            if isinstance(msg, dict):
                role = ((msg.get("author") or {}).get("role") or "").lower()
                if role == "assistant":
                    assistant_seen = True
                    text = _content_to_text(msg.get("content"))
                    if text:
                        delta_parts.append(text)
                    meta = msg.get("metadata")
                    if isinstance(meta, dict):
                        model_slug = str(meta.get("model_slug") or meta.get("resolved_model_slug") or "").strip()
                        if model_slug:
                            assistant_model_slugs.add(model_slug)
                        cits = meta.get("citations")
                        if isinstance(cits, list):
                            assistant_citation_counts.append(len(cits))
                        groups = meta.get("search_result_groups")
                        if isinstance(groups, list):
                            assistant_search_group_counts.append(len(groups))

            # Frequent stream shape: {"v":"token piece"}
            v = obj.get("v")
            if assistant_seen and isinstance(v, str) and v:
                delta_parts.append(v)

            # Frequent stream shape: {"p":"/message/content/parts/0","o":"append","v":"..."}
            p = str(obj.get("p") or "")
            o = str(obj.get("o") or "")
            if assistant_seen and "/message/content/parts/0" in p and o in ("append", "replace"):
                if isinstance(v, str) and v:
                    delta_parts.append(v)

            # Patch form: {"o":"patch","v":[{"p":"...","o":"append","v":"..."}]}
            if assistant_seen and o == "patch" and isinstance(v, list):
                for op in v:
                    if not isinstance(op, dict):
                        continue
                    pp = str(op.get("p") or "")
                    oo = str(op.get("o") or "")
                    vv = op.get("v")
                    if "/message/content/parts/0" in pp and oo in ("append", "replace"):
                        if isinstance(vv, str) and vv:
                            delta_parts.append(vv)

    assistant_text_by_id = {}
    for msg in messages:
        role = ((msg.get("author") or {}).get("role") or "").lower()
        if role != "assistant":
            continue
        text = _content_to_text(msg.get("content"))
        if not text:
            continue
        msg_id = str(msg.get("id") or f"assistant_{len(assistant_text_by_id)+1}")
        assistant_text_by_id[msg_id] = text

    answer_text = ""
    for _, text in sorted(assistant_text_by_id.items()):
        if len(text) > len(answer_text):
            answer_text = text
    if not answer_text:
        answer_text = "".join(delta_parts).strip()

    urls.extend(_extract_urls_from_text(answer_text, "answer_text"))
    urls.extend(_extract_urls_from_text(raw_text, "raw_stream"))

    dedup_urls = []
    seen = set()
    for item in urls:
        key = _clean_url(item.get("url", ""))
        if not key or key in seen:
            continue
        seen.add(key)
        dedup_urls.append(
            {
                "url": key,
                "title": str(item.get("title") or ""),
                "snippet": str(item.get("snippet") or ""),
                "source": str(item.get("source") or "structured"),
            }
        )

    summary = {
        "request_id": request_id,
        "prompt": prompt,
        "answer_text": answer_text,
        "citations": dedup_urls,
        "citation_count": len(dedup_urls),
        "assistant_model_slugs": sorted(assistant_model_slugs),
        "assistant_meta_citation_counts": assistant_citation_counts,
        "assistant_search_group_counts": assistant_search_group_counts,
        "event_count": len(sse_events),
        "json_event_count": len(parsed_json),
        "files": {
            "network_events": str(NETWORK_LOG),
            "conversation_events": str(CONVERSATION_EVENT_LOG),
            "request": str(req_file),
            "body": str(body_file),
            "stream": str(stream_file),
        },
    }
    SUMMARY_FILE.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[CONVERSATION] Summary saved: {SUMMARY_FILE.resolve()}")
    return summary


def main():
    _load_dotenv_if_present(".env")
    target_url = os.getenv(
        "CHATGPT_URL",
        "https://chatgpt.com/?temporary-chat=true&hints=search&q=latest+NVIDIA+earnings",
    )
    prompt = (os.getenv("CHATGPT_PROMPT", "") or "").strip() or _extract_prompt_from_url(target_url)
    if not prompt:
        prompt = "latest NVIDIA earnings"

    proxy = (os.getenv("CHATGPT_PROXY", "") or "").strip() or None
    use_incognito = os.getenv("CHATGPT_INCOGNITO", "0") == "1"
    user_data_dir = (os.getenv("CHATGPT_USER_DATA_DIR", "") or "").strip() or None
    request_timeout = int(os.getenv("CONVERSATION_REQUEST_TIMEOUT", "180"))
    capture_timeout = int(os.getenv("CONVERSATION_CAPTURE_TIMEOUT", "420"))
    idle_secs = int(os.getenv("CONVERSATION_STREAM_IDLE_SECS", "20"))

    with SB(
        uc=True,
        uc_cdp_events=True,
        test=True,
        locale="en",
        incognito=use_incognito,
        user_data_dir=user_data_dir,
        proxy=proxy,
    ) as sb:
        print("Systems green, standing by.")
        print(
            f"[CONFIG] incognito={use_incognito} "
            f"user_data_dir={user_data_dir or '<none>'} proxy={'set' if proxy else 'none'}"
        )
        print(f"[CONFIG] URL={target_url}")
        print(f"[CONFIG] Prompt={prompt}")

        sb.activate_cdp_mode("about:blank")
        _start_capture(sb)
        if os.getenv("CLEAR_CAPTURE_OUTPUTS", "1") == "1":
            _clear_previous_outputs()
            print("[CONFIG] Cleared previous capture outputs")

        open_ts = time.time()
        sb.cdp.open(target_url)
        sb.sleep(8)
        print("[UI] Initial page settle complete")
        _close_popups(sb)
        _dismiss_guest_gate(sb, retries=2)
        _save_step_screenshot(sb, "step3_after_popup_and_guest_gate")

        if _is_login_wall(sb):
            print(
                "[ERROR] Login wall detected before prompt submit. "
                "Log in first or run with a reusable profile (CHATGPT_USER_DATA_DIR)."
            )
            _save_step_screenshot(sb, "step3_login_wall_detected")
            sb.save_screenshot("screenshots/login_wall.png")
            return

        # If URL has ?q=..., ChatGPT may auto-trigger /f/conversation before any manual click.
        request_id = _wait_for_main_request_since(open_ts, timeout=8)
        if request_id:
            print(f"[CONVERSATION] Auto query request detected from URL: {request_id}")
            _save_step_screenshot(sb, "step5_auto_query_detected")
            _save_step_screenshot(sb, "step4_submit_skipped_auto_query")

        if not request_id:
            _save_step_screenshot(sb, "step3_before_composer_ready_wait")
            composer_ready = _wait_for_composer_ready(
                sb, timeout=int(os.getenv("COMPOSER_READY_TIMEOUT", "20"))
            )
            if composer_ready:
                _save_step_screenshot(sb, "step3_composer_ready")
            else:
                _save_step_screenshot(sb, "step3_composer_not_ready")
                if _is_guest_gate_modal_visible(sb):
                    print(
                        "[ERROR] Guest gate is blocking submit (Log in/Sign up modal). "
                        "Click 'Stay logged out' once manually in this profile, then rerun."
                    )
                    _save_step_screenshot(sb, "step3_guest_gate_blocking")
                    sb.save_screenshot("screenshots/guest_gate_blocking.png")
                    return
                if _is_login_wall(sb):
                    print(
                        "[ERROR] Login wall detected before submit. "
                        "Use a clean residential IP/profile or run with a logged-in profile."
                    )
                    _save_step_screenshot(sb, "step3_login_wall_before_submit")
                    sb.save_screenshot("screenshots/login_wall_before_submit.png")
                    return
                print("[WARN] Composer not ready yet; attempting submit anyway.")
            baseline = _current_main_ids()
            _save_step_screenshot(sb, "step4_before_submit_first")
            print("[CONVERSATION] Submitting prompt")
            submit_result = _composer_submit(sb, prompt)
            print(f"[CONVERSATION] Submit result: {submit_result}")
            _save_step_screenshot(
                sb, f"step4_after_submit_first_{'ok' if submit_result.get('ok') else 'fail'}"
            )
            if submit_result.get("ok"):
                _save_step_screenshot(sb, "step5_wait_first_start")
                request_id = _wait_for_new_main_request(
                    baseline,
                    timeout=request_timeout,
                    sb=sb,
                    label="step5_wait_main_request_first",
                )
            else:
                print("[WARN] Submit did not complete; skipping request wait for this attempt.")

        if not request_id:
            print("[WARN] Main /f/conversation request not detected after first submit. Retrying once...")
            _close_popups(sb)
            _dismiss_guest_gate(sb, retries=2)
            _save_step_screenshot(sb, "step3_retry_after_popup_and_guest_gate")
            baseline = _current_main_ids()
            _save_step_screenshot(sb, "step4_before_submit_retry")
            print("[CONVERSATION] Retrying prompt submit")
            submit_result = _composer_submit(sb, prompt)
            print(f"[CONVERSATION] Retry submit result: {submit_result}")
            _save_step_screenshot(
                sb, f"step4_after_submit_retry_{'ok' if submit_result.get('ok') else 'fail'}"
            )
            if submit_result.get("ok"):
                _save_step_screenshot(sb, "step5_wait_retry_start")
                request_id = _wait_for_new_main_request(
                    baseline,
                    timeout=request_timeout,
                    sb=sb,
                    label="step5_wait_main_request_retry",
                )
            else:
                print("[WARN] Retry submit did not complete; skipping request wait for retry.")

        if not request_id:
            # Last fallback: pick latest seen main request from this run window.
            request_id = _latest_main_request_id(after_ts=open_ts)
        if not request_id:
            recovered = _find_sse_request_id_from_bodies(after_ts=open_ts)
            if recovered:
                request_id = recovered
                print(f"[WARN] Recovered request from SSE body file: {request_id}")
        if not request_id:
            print("[ERROR] Could not detect /backend-*/f/conversation request.")
            _save_step_screenshot(sb, "step5_main_request_not_detected")
            sb.save_screenshot("screenshots/no_f_conversation.png")
            return

        print(f"[CONVERSATION] Main request detected: {request_id} url={_request_url.get(request_id)}")
        _save_step_screenshot(sb, "step5_main_request_detected")
        _force_stream_enable(request_id, timeout=60)
        done = _wait_for_capture_done(
            request_id,
            timeout=capture_timeout,
            idle_secs=idle_secs,
        )
        if done:
            print(f"[CONVERSATION] Capture finished for request {request_id}")
        else:
            print(f"[WARN] Capture timed out for request {request_id}")

        summary = _build_summary(request_id, prompt)
        print(f"[RESULT] Answer chars={len(summary.get('answer_text') or '')}")
        print(f"[RESULT] Citations={len(summary.get('citations') or [])}")
        sb.save_screenshot("screenshots/final_capture_state.png")


if __name__ == "__main__":
    main()
