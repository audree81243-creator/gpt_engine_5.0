from main import *
from contextlib import suppress
import ast
import base64
import json
import os
import re
import time
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from seleniumbase import SB
from utils import sleep_dbg
from utils import save_ss
from utils import safe_send_keys
from utils import safe_type
from utils import wait_for_textarea
from is_pages.is_verification_page import *
from is_pages.is_pop_ups import *
from is_pages.is_chat_ui import *
from boomlify_codes import *
from activate_search_mode import *
from birthday_helpers import fill_birthday
import mycdp.network as cdp_network

NETWORK_LOG = Path("screenshots/network_events.jsonl")
CONVERSATION_EVENT_LOG = Path("screenshots/conversation_events.jsonl")
CONVERSATION_STREAM_DIR = Path("screenshots/conversation_streams")
CONVERSATION_BODY_DIR = Path("screenshots/conversation_bodies")
CONVERSATION_REQUEST_DIR = Path("screenshots/conversation_requests")
CONVERSATION_SUMMARY_FILE = Path("screenshots/conversation_answer_citations.json")
CONVERSATION_URL_HINTS = (
    "/backend-anon/f/conversation",
    "/backend-api/f/conversation",
    "/backend-anon/conversation",
    "/backend-api/conversation",
)
PRIMARY_CONVERSATION_PATHS = (
    "/backend-anon/f/conversation",
    "/backend-api/f/conversation",
)

_capture_lock = Lock()
_network_event_count = 0
_conversation_event_count = 0
_conversation_request_ids = set()
_request_url_by_id = {}
_streaming_enabled_ids = set()
_response_saved_ids = set()
_request_saved_ids = set()
_request_id_raw_re = re.compile(r"request_id=RequestId\('([^']+)'\)")
_url_raw_re = re.compile(r"url='([^']+)'")
_request_url_raw_re = re.compile(r"request=Request\(url='([^']+)'")
_response_url_raw_re = re.compile(r"response=Response\(url='([^']+)'")
_data_raw_re = re.compile(r"\bdata=(None|'(?:[^'\\]|\\.)*')")
_cdp_connection = None
_cdp_loop = None


def _ensure_capture_paths():
    NETWORK_LOG.parent.mkdir(parents=True, exist_ok=True)
    CONVERSATION_EVENT_LOG.parent.mkdir(parents=True, exist_ok=True)
    CONVERSATION_STREAM_DIR.mkdir(parents=True, exist_ok=True)
    CONVERSATION_BODY_DIR.mkdir(parents=True, exist_ok=True)
    CONVERSATION_REQUEST_DIR.mkdir(parents=True, exist_ok=True)
    NETWORK_LOG.touch(exist_ok=True)
    CONVERSATION_EVENT_LOG.touch(exist_ok=True)


def _append_jsonl(path, row):
    with _capture_lock, path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")


def _append_network_row(row):
    global _network_event_count
    _append_jsonl(NETWORK_LOG, row)
    _network_event_count += 1
    if _network_event_count <= 5 or _network_event_count % 100 == 0:
        print(f"[NETWORK] Logged {_network_event_count} events")


def _append_conversation_row(row):
    global _conversation_event_count
    _append_jsonl(CONVERSATION_EVENT_LOG, row)
    _conversation_event_count += 1
    if _conversation_event_count <= 10 or _conversation_event_count % 25 == 0:
        print(f"[CONVERSATION] Logged {_conversation_event_count} events")


def _decode_cdp_data(data):
    if data is None:
        return ""
    if not isinstance(data, str):
        data = str(data)
    try:
        decoded = base64.b64decode(data, validate=False)
        return decoded.decode("utf-8", errors="replace")
    except Exception:
        return data


def _is_conversation_url(url):
    if not url:
        return False
    url = url.lower()
    return any(token in url for token in CONVERSATION_URL_HINTS)


def _normalize_url_path(url):
    if not url:
        return ""
    try:
        return (urlsplit(url).path or "").rstrip("/").lower()
    except Exception:
        return str(url).strip().lower()


def _is_primary_conversation_url(url):
    path = _normalize_url_path(url)
    if not path:
        return False
    return any(path.endswith(p) for p in PRIMARY_CONVERSATION_PATHS)


def _is_prepare_conversation_url(url):
    path = _normalize_url_path(url)
    if not path:
        return False
    return any(path.endswith(p + "/prepare") for p in PRIMARY_CONVERSATION_PATHS)


def _is_any_f_conversation_url(url):
    path = _normalize_url_path(url)
    if not path:
        return False
    return "/f/conversation" in path


def _primary_conversation_request_ids():
    ids = set()
    for request_id, url in _request_url_by_id.items():
        if _is_primary_conversation_url(url):
            ids.add(request_id)
    return ids


def _request_id_sort_key(request_id):
    parts = str(request_id).split(".")
    key = []
    for part in parts:
        try:
            key.append(int(part))
        except Exception:
            key.append(part)
    return tuple(key)


def _wait_for_new_primary_conversation_request(previous_ids, timeout=90):
    t0 = time.time()
    while time.time() - t0 < timeout:
        current = _primary_conversation_request_ids()
        new_ids = current - set(previous_ids)
        if new_ids:
            return sorted(new_ids, key=_request_id_sort_key)[-1]
        time.sleep(0.2)
    return None


def _wait_for_new_f_conversation_activity(previous_ids, timeout=45):
    t0 = time.time()
    previous_ids = set(previous_ids)
    while time.time() - t0 < timeout:
        candidates = {
            rid for rid, url in _request_url_by_id.items() if _is_any_f_conversation_url(url)
        }
        new_ids = candidates - previous_ids
        if new_ids:
            primary_new_ids = [
                rid for rid in new_ids if _is_primary_conversation_url(_request_url_by_id.get(rid))
            ]
            pick_from = primary_new_ids if primary_new_ids else list(new_ids)
            rid = sorted(pick_from, key=_request_id_sort_key)[-1]
            url = _request_url_by_id.get(rid)
            kind = "conversation" if _is_primary_conversation_url(url) else "prepare"
            return kind, rid, url
        time.sleep(0.2)
    return None, None, None


def _wait_for_request_body_saved(request_id, timeout=180):
    stream_idle_secs = int(os.getenv("CONVERSATION_STREAM_IDLE_SECS", "20"))
    stream_file = CONVERSATION_STREAM_DIR / f"{request_id}.stream.txt"
    last_size = 0
    last_growth_at = None
    t0 = time.time()
    while time.time() - t0 < timeout:
        if request_id in _response_saved_ids:
            return True
        body_file = CONVERSATION_BODY_DIR / f"{request_id}.body.txt"
        if body_file.exists() and body_file.stat().st_size > 0:
            return True
        if stream_file.exists():
            size = stream_file.stat().st_size
            if size > 0:
                if size != last_size:
                    last_size = size
                    last_growth_at = time.time()
                elif last_growth_at and (time.time() - last_growth_at) >= stream_idle_secs:
                    return True
                try:
                    with stream_file.open("rb") as sf:
                        sf.seek(max(0, size - 4096))
                        tail = sf.read().decode("utf-8", errors="replace")
                    if "[DONE]" in tail:
                        return True
                except Exception:
                    pass
        time.sleep(0.25)
    return False


def wait_for_manual_conversation_capture(timeout=240):
    previous_primary = set(_primary_conversation_request_ids())
    print(
        "[CONVERSATION] Auto send did not start a stream. "
        "You can type/send manually in the browser now."
    )
    request_id = _wait_for_new_primary_conversation_request(previous_primary, timeout=timeout)
    if not request_id:
        print(f"[CONVERSATION][WARN] No manual /f/conversation request seen within {timeout}s.")
        return False
    print(f"[CONVERSATION] Manual stream detected: {request_id}")
    if _wait_for_request_body_saved(request_id, timeout=240):
        print(f"[CONVERSATION] Manual capture finished for request {request_id}")
        return True
    print(f"[CONVERSATION][WARN] Manual request {request_id} did not finish within timeout.")
    return False


def _extract_request_id_and_url(params):
    request_id = params.get("requestId")
    if request_id is None:
        request_id = params.get("request_id")
    request_id = str(request_id) if request_id is not None else None
    url = None
    request_obj = params.get("request")
    if isinstance(request_obj, dict):
        url = request_obj.get("url") or request_obj.get("URL")
    response_obj = params.get("response")
    if not url and isinstance(response_obj, dict):
        url = response_obj.get("url") or response_obj.get("URL")
    raw = params.get("raw")
    if isinstance(raw, str):
        if request_id is None:
            request_id_match = _request_id_raw_re.search(raw)
            if request_id_match:
                request_id = request_id_match.group(1)
        if not url:
            # Prefer the explicit request/response URL blocks from CDP repr.
            req_match = _request_url_raw_re.search(raw)
            if req_match:
                url = req_match.group(1)
        if not url:
            resp_match = _response_url_raw_re.search(raw)
            if resp_match:
                url = resp_match.group(1)
        if not url:
            # Fallback: first generic url='...'
            url_match = _url_raw_re.search(raw)
            if url_match:
                url = url_match.group(1)
    return request_id, url


def _extract_data_chunk(params):
    data_chunk = params.get("data")
    if data_chunk:
        return data_chunk
    raw = params.get("raw")
    if not isinstance(raw, str):
        return None
    match = _data_raw_re.search(raw)
    if not match:
        return None
    token = match.group(1)
    if token == "None":
        return None
    try:
        return ast.literal_eval(token)
    except Exception:
        return token.strip("'")


def _append_stream_chunk(request_id, text, source_method):
    if not text:
        return
    stream_file = CONVERSATION_STREAM_DIR / f"{request_id}.stream.txt"
    with _capture_lock, stream_file.open("a", encoding="utf-8") as f:
        f.write(text)
    _append_conversation_row({
        "ts": time.time(),
        "method": "Conversation.StreamChunkSaved",
        "request_id": request_id,
        "source_method": source_method,
        "chars": len(text),
        "file": str(stream_file),
    })


def _read_text_file(path):
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _split_sse_events(raw_text):
    events = []
    current_event = None
    current_data_lines = []
    for line in raw_text.splitlines():
        if not line.strip():
            if current_event is not None or current_data_lines:
                events.append({
                    "event": current_event or "message",
                    "data": "\n".join(current_data_lines),
                })
            current_event = None
            current_data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            current_event = line[6:].strip()
            continue
        if line.startswith("data:"):
            current_data_lines.append(line[5:].lstrip())
    if current_event is not None or current_data_lines:
        events.append({
            "event": current_event or "message",
            "data": "\n".join(current_data_lines),
        })
    return events


_devtools_ts_re = re.compile(r"^\d{2}:\d{2}:\d{2}\.\d{3}$")


def _split_devtools_eventstream_events(raw_text):
    """
    DevTools "EventStream" tab can show lines like:
      delta <json> 05:59:33.776
    or (tab-separated):
      delta\t<json>\t05:59:33.776
    We normalize that into [{"event": "...", "data": "..."}] similar to SSE.
    """
    events = []
    for line in (raw_text or "").splitlines():
        if not line.strip():
            continue

        pieces = None
        if "\t" in line:
            parts = [p for p in line.split("\t") if p]
            if len(parts) >= 2:
                pieces = (parts[0].strip(), parts[1].strip())
        else:
            s = line.strip()
            if " " in s:
                head, rest = s.split(" ", 1)
                head = head.strip()
                rest = rest.strip()
                # Strip trailing timestamp if present.
                maybe = rest.rsplit(" ", 1)
                if len(maybe) == 2 and _devtools_ts_re.match(maybe[1].strip()):
                    rest = maybe[0].strip()
                pieces = (head, rest)

        if not pieces:
            continue
        event_name, data = pieces
        if not event_name or data is None:
            continue
        events.append({"event": event_name, "data": str(data)})
    return events


def _split_stream_events(raw_text):
    events = _split_sse_events(raw_text)
    if events:
        return events
    return _split_devtools_eventstream_events(raw_text)


def _safe_json_loads(text):
    try:
        return json.loads(text)
    except Exception:
        return None


def _iter_json_nodes(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _iter_json_nodes(value)
    elif isinstance(node, list):
        for item in node:
            yield from _iter_json_nodes(item)


def _collect_urls(value, out_list):
    url_re = re.compile(r"https?://[^\s\"'<>]+")
    if isinstance(value, str):
        out_list.extend(url_re.findall(value))
        return
    if isinstance(value, dict):
        for v in value.values():
            _collect_urls(v, out_list)
        return
    if isinstance(value, list):
        for item in value:
            _collect_urls(item, out_list)


def _collect_search_result_urls(value, out_list):
    if isinstance(value, dict):
        if value.get("type") == "search_result" and isinstance(value.get("url"), str):
            out_list.append(value["url"])
        for v in value.values():
            _collect_search_result_urls(v, out_list)
    elif isinstance(value, list):
        for item in value:
            _collect_search_result_urls(item, out_list)


def _dedupe_urls(urls):
    result = []
    seen = set()
    for url in urls:
        clean = str(url).strip().rstrip(".,);]")
        low = clean.lower()
        if "chatgpt.com/backend" in low or "persistent.oaistatic.com" in low:
            continue
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
    return result


def _extract_answer_and_citations_from_sse(raw_text):
    events = _split_stream_events(raw_text)
    assistant_started = False
    assistant_response_started = False
    answer_parts = []
    citation_urls = []

    for event in events:
        payload = (event.get("data") or "").strip()
        if not payload or payload == "[DONE]":
            continue
        obj = _safe_json_loads(payload)
        if not isinstance(obj, dict):
            continue

        # Capture URLs opportunistically. Citations often arrive late as patch ops
        # under "v": [{"p": "...content_references...", "o": "...", "v": ...}].
        _collect_urls(obj, citation_urls)
        _collect_search_result_urls(obj, citation_urls)

        event_type = obj.get("type")
        if event_type in {
            "server_ste_metadata",
            "message_metadata",
            "output_message",
            "message",
        }:
            _collect_urls(obj.get("metadata"), citation_urls)
            _collect_urls(obj, citation_urls)

        path_hint = str(obj.get("p") or "")
        if "search_result_groups" in path_hint:
            _collect_urls(obj.get("v"), citation_urls)
            _collect_search_result_urls(obj.get("v"), citation_urls)

        if isinstance(obj.get("v"), list):
            # Patch-op list: collect URLs from each op's value.
            for op in obj["v"]:
                if not isinstance(op, dict):
                    continue
                _collect_urls(op.get("v"), citation_urls)
                _collect_search_result_urls(op.get("v"), citation_urls)

                op_path = str(op.get("p") or "")
                op_action = str(op.get("o") or "")
                op_value = op.get("v")
                if (
                    op_path == "/message/content/parts/0"
                    and op_action in {"append", "replace"}
                    and isinstance(op_value, str)
                    and op_value
                ):
                    assistant_started = True
                    assistant_response_started = True
                    answer_parts.append(op_value)

        message_obj = None
        if isinstance(obj.get("v"), dict) and isinstance(obj["v"].get("message"), dict):
            message_obj = obj["v"]["message"]
        elif isinstance(obj.get("message"), dict):
            message_obj = obj["message"]
        if message_obj:
            role = (((message_obj.get("author") or {}).get("role")) or "").lower()
            if role == "assistant":
                assistant_started = True
                recipient = str(message_obj.get("recipient") or "").lower()
                content = message_obj.get("content") or {}
                content_type = str(content.get("content_type") or "").lower() if isinstance(content, dict) else ""
                if recipient in {"", "all"} and content_type != "code":
                    assistant_response_started = True
                parts = content.get("parts") if isinstance(content, dict) else None
                if isinstance(parts, list):
                    for part in parts:
                        if isinstance(part, str) and part:
                            answer_parts.append(part)
                if isinstance(content, dict):
                    text_field = content.get("text")
                    if (
                        isinstance(text_field, str)
                        and text_field.strip()
                        and content_type != "code"
                    ):
                        answer_parts.append(text_field.strip())
                _collect_urls(message_obj.get("metadata"), citation_urls)
                _collect_search_result_urls(message_obj.get("metadata"), citation_urls)

        # Streaming text chunks can arrive as direct patch ops or as {"v": "..."}.
        if obj.get("p") == "/message/content/parts/0" and obj.get("o") in {"append", "replace"}:
            if isinstance(obj.get("v"), str) and obj["v"]:
                assistant_started = True
                assistant_response_started = True
                answer_parts.append(obj["v"])
        elif assistant_started and obj.get("o") in {None, "append"} and isinstance(obj.get("v"), str):
            # Streaming text chunks usually arrive as {"v": "..."}.
            if obj["v"]:
                answer_parts.append(obj["v"])
                assistant_response_started = True

        if obj.get("o") == "patch" and isinstance(obj.get("v"), list):
            for patch_op in obj["v"]:
                if not isinstance(patch_op, dict):
                    continue
                patch_path = patch_op.get("p")
                patch_action = patch_op.get("o")
                patch_value = patch_op.get("v")
                if (
                    assistant_started
                    and patch_path == "/message/content/parts/0"
                    and patch_action == "append"
                    and isinstance(patch_value, str)
                ):
                    answer_parts.append(patch_value)
                    assistant_response_started = True
                if isinstance(patch_path, str) and patch_path.startswith("/message/metadata"):
                    _collect_urls(patch_value, citation_urls)
                    _collect_search_result_urls(patch_value, citation_urls)

    answer = "".join(answer_parts).strip() if assistant_response_started else ""
    _collect_urls(answer, citation_urls)
    return answer, _dedupe_urls(citation_urls)


def _extract_answer_and_citations_from_json(raw_text):
    obj = _safe_json_loads((raw_text or "").strip())
    if not obj:
        return "", []
    text_candidates = []
    citation_urls = []

    for item in _iter_json_nodes(obj):
        for key, value in item.items():
            key_l = str(key).lower()
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned and key_l in {"text", "output_text", "answer", "final", "completion"}:
                    text_candidates.append(cleaned)
            if any(t in key_l for t in ("citation", "source", "url", "reference", "link")):
                _collect_urls(value, citation_urls)

    answer = max(text_candidates, key=len) if text_candidates else ""
    return answer, _dedupe_urls(citation_urls)


def _extract_answer_and_citations(raw_text):
    answer, citations = _extract_answer_and_citations_from_sse(raw_text)
    if answer or citations:
        return answer, citations
    return _extract_answer_and_citations_from_json(raw_text)


def _collect_request_ids_from_disk():
    request_ids = set(_conversation_request_ids)
    for p in CONVERSATION_STREAM_DIR.glob("*.stream.txt"):
        request_ids.add(p.name[: -len(".stream.txt")])
    for p in CONVERSATION_BODY_DIR.glob("*.body.txt"):
        request_ids.add(p.name[: -len(".body.txt")])
    for p in CONVERSATION_REQUEST_DIR.glob("*.request.txt"):
        request_ids.add(p.name[: -len(".request.txt")])
    return request_ids


def _hydrate_request_url_map_from_conversation_log():
    if not CONVERSATION_EVENT_LOG.exists():
        return
    try:
        with CONVERSATION_EVENT_LOG.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                request_id = row.get("request_id")
                url = row.get("url")
                if request_id and url and request_id not in _request_url_by_id:
                    _request_url_by_id[str(request_id)] = str(url)
    except Exception:
        return


def finalize_conversation_summary():
    _ensure_capture_paths()
    _hydrate_request_url_map_from_conversation_log()
    request_ids = sorted(_collect_request_ids_from_disk())
    entries = []
    best = None

    for request_id in request_ids:
        stream_file = CONVERSATION_STREAM_DIR / f"{request_id}.stream.txt"
        body_file = CONVERSATION_BODY_DIR / f"{request_id}.body.txt"
        request_file = CONVERSATION_REQUEST_DIR / f"{request_id}.request.txt"

        stream_text = _read_text_file(stream_file)
        body_text = _read_text_file(body_file)
        answer_stream, citations_stream = _extract_answer_and_citations(stream_text)
        answer_body, citations_body = _extract_answer_and_citations(body_text)
        answer = answer_stream if len(answer_stream) >= len(answer_body) else answer_body

        citations = []
        seen = set()
        for url in citations_stream + citations_body:
            if url in seen:
                continue
            seen.add(url)
            citations.append(url)

        entry = {
            "request_id": request_id,
            "url": _request_url_by_id.get(request_id),
            "is_primary_conversation": _is_primary_conversation_url(_request_url_by_id.get(request_id)),
            "is_prepare_conversation": _is_prepare_conversation_url(_request_url_by_id.get(request_id)),
            "answer": answer,
            "answer_chars": len(answer),
            "citations": citations,
            "citations_count": len(citations),
            "stream_file": str(stream_file) if stream_file.exists() else None,
            "body_file": str(body_file) if body_file.exists() else None,
            "request_file": str(request_file) if request_file.exists() else None,
        }
        entries.append(entry)

    if entries:
        primary_entries = [e for e in entries if e["is_primary_conversation"]]
        pool = primary_entries if primary_entries else entries
        best = max(
            pool,
            key=lambda x: (x["answer_chars"], x["citations_count"]),
        )

    summary = {
        "generated_at_ts": time.time(),
        "request_count": len(entries),
        "best": best,
        "requests": entries,
    }
    CONVERSATION_SUMMARY_FILE.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[CONVERSATION] Summary saved: {CONVERSATION_SUMMARY_FILE.resolve()}")

async def _capture_request_post_data(connection, request_id):
    if request_id in _request_saved_ids:
        return
    _request_saved_ids.add(request_id)
    try:
        post_data = await connection.send(
            cdp_network.get_request_post_data(cdp_network.RequestId(request_id))
        )
    except Exception as e:
        _append_conversation_row({
            "ts": time.time(),
            "method": "Conversation.RequestBodyUnavailable",
            "request_id": request_id,
            "error": str(e),
        })
        return
    request_file = CONVERSATION_REQUEST_DIR / f"{request_id}.request.txt"
    with _capture_lock, request_file.open("w", encoding="utf-8") as f:
        f.write(post_data)
    _append_conversation_row({
        "ts": time.time(),
        "method": "Conversation.RequestBodySaved",
        "request_id": request_id,
        "chars": len(post_data),
        "file": str(request_file),
    })


async def _enable_stream_capture(connection, request_id, quiet=False):
    if request_id in _streaming_enabled_ids:
        return True
    try:
        buffered_data = await connection.send(
            cdp_network.stream_resource_content(cdp_network.RequestId(request_id))
        )
    except Exception as e:
        if not quiet:
            _append_conversation_row({
                "ts": time.time(),
                "method": "Conversation.StreamEnableFailed",
                "request_id": request_id,
                "error": str(e),
            })
        return False
    _streaming_enabled_ids.add(request_id)
    if buffered_data:
        _append_stream_chunk(
            request_id,
            _decode_cdp_data(buffered_data),
            "Network.streamResourceContent.bufferedData",
        )
    _append_conversation_row({
        "ts": time.time(),
        "method": "Conversation.StreamEnabled",
        "request_id": request_id,
    })
    return True


async def _capture_response_body(connection, request_id):
    if request_id in _response_saved_ids:
        return
    _response_saved_ids.add(request_id)
    try:
        body, base64_encoded = await connection.send(
            cdp_network.get_response_body(cdp_network.RequestId(request_id))
        )
    except Exception as e:
        _append_conversation_row({
            "ts": time.time(),
            "method": "Conversation.ResponseBodyUnavailable",
            "request_id": request_id,
            "error": str(e),
        })
        return

    body_text = _decode_cdp_data(body) if base64_encoded else body
    body_file = CONVERSATION_BODY_DIR / f"{request_id}.body.txt"
    with _capture_lock, body_file.open("w", encoding="utf-8") as f:
        f.write(body_text or "")
    _append_conversation_row({
        "ts": time.time(),
        "method": "Conversation.ResponseBodySaved",
        "request_id": request_id,
        "base64_encoded": base64_encoded,
        "chars": len(body_text),
        "file": str(body_file),
    })

    # If stream chunks weren't captured separately, persist SSE body as stream.
    # ChatGPT conversation endpoint often returns full event-stream in response body.
    with suppress(Exception):
        url = _request_url_by_id.get(request_id, "")
        if _is_primary_conversation_url(url) and isinstance(body_text, str) and "event:" in body_text and "data:" in body_text:
            stream_file = CONVERSATION_STREAM_DIR / f"{request_id}.stream.txt"
            if (not stream_file.exists()) or stream_file.stat().st_size == 0:
                with _capture_lock, stream_file.open("w", encoding="utf-8") as sf:
                    sf.write(body_text)
                _append_conversation_row({
                    "ts": time.time(),
                    "method": "Conversation.StreamFromBodySaved",
                    "request_id": request_id,
                    "chars": len(body_text),
                    "file": str(stream_file),
                })


def _force_enable_stream_capture(request_id, timeout=45, poll_s=0.5):
    if request_id in _streaming_enabled_ids:
        return True
    if not _cdp_connection or not _cdp_loop:
        return False

    t0 = time.time()
    while time.time() - t0 < timeout:
        if request_id in _streaming_enabled_ids:
            return True
        try:
            ok = _cdp_loop.run_until_complete(
                _enable_stream_capture(_cdp_connection, request_id, quiet=True)
            )
            if ok:
                return True
        except Exception:
            pass
        time.sleep(poll_s)
    return request_id in _streaming_enabled_ids


def _event_to_params(event):
    return event.to_json() if hasattr(event, "to_json") else {"raw": str(event)}


async def _handle_network_event(
    method,
    event,
    request_id=None,
    url=None,
    data_chunk=None,
    eventsource_data=None,
):
    params = _event_to_params(event)
    request_id = str(request_id) if request_id is not None else None
    if request_id and url:
        _request_url_by_id[request_id] = url
    if request_id and not url:
        url = _request_url_by_id.get(request_id)

    row = {
        "ts": time.time(),
        "method": method,
        "request_id": request_id,
        "url": url,
        "params": params,
    }
    _append_network_row(row)

    is_conversation = False
    if request_id and request_id in _conversation_request_ids:
        is_conversation = True
    if _is_conversation_url(url):
        is_conversation = True
        if request_id:
            _conversation_request_ids.add(request_id)
    if not is_conversation:
        return

    _append_conversation_row(row)
    connection = _cdp_connection

    if connection and request_id and method == "Network.RequestWillBeSent":
        await _capture_request_post_data(connection, request_id)
        if _is_primary_conversation_url(url):
            await _enable_stream_capture(connection, request_id, quiet=True)

    if connection and request_id and method == "Network.ResponseReceived":
        await _enable_stream_capture(connection, request_id)

    if request_id and method == "Network.DataReceived":
        chunk = data_chunk if data_chunk is not None else _extract_data_chunk(params)
        if chunk:
            _append_stream_chunk(
                request_id,
                _decode_cdp_data(chunk),
                "Network.DataReceived",
            )

    if request_id and method == "Network.EventSourceMessageReceived":
        sse = eventsource_data if eventsource_data is not None else _extract_data_chunk(params)
        if sse:
            _append_stream_chunk(
                request_id,
                str(sse) + "\n",
                "Network.EventSourceMessageReceived",
            )

    if connection and request_id and method in (
        "Network.LoadingFinished",
        "Network.LoadingFailed",
    ):
        await _capture_response_body(connection, request_id)


async def _on_request_will_be_sent(event: cdp_network.RequestWillBeSent):
    url = None
    with suppress(Exception):
        url = event.request.url
    await _handle_network_event(
        "Network.RequestWillBeSent",
        event,
        request_id=event.request_id,
        url=url,
    )


async def _on_response_received(event: cdp_network.ResponseReceived):
    url = None
    with suppress(Exception):
        url = event.response.url
    await _handle_network_event(
        "Network.ResponseReceived",
        event,
        request_id=event.request_id,
        url=url,
    )


async def _on_data_received(event: cdp_network.DataReceived):
    await _handle_network_event(
        "Network.DataReceived",
        event,
        request_id=event.request_id,
        data_chunk=getattr(event, "data", None),
    )


async def _on_loading_finished(event: cdp_network.LoadingFinished):
    await _handle_network_event(
        "Network.LoadingFinished",
        event,
        request_id=event.request_id,
    )


async def _on_loading_failed(event: cdp_network.LoadingFailed):
    await _handle_network_event(
        "Network.LoadingFailed",
        event,
        request_id=event.request_id,
    )


async def _on_eventsource_message(event: cdp_network.EventSourceMessageReceived):
    await _handle_network_event(
        "Network.EventSourceMessageReceived",
        event,
        request_id=event.request_id,
        eventsource_data=getattr(event, "data", None),
    )


def start_network_capture_cdp_mode(sb):
    global _cdp_connection
    global _cdp_loop
    _ensure_capture_paths()
    _cdp_connection = sb.cdp.page
    _cdp_loop = sb.cdp.loop

    # SeleniumBase official pattern: register specific CDP event classes.
    sb.cdp.add_handler(cdp_network.RequestWillBeSent, _on_request_will_be_sent)
    sb.cdp.add_handler(cdp_network.ResponseReceived, _on_response_received)
    sb.cdp.add_handler(cdp_network.DataReceived, _on_data_received)
    sb.cdp.add_handler(cdp_network.LoadingFinished, _on_loading_finished)
    sb.cdp.add_handler(cdp_network.LoadingFailed, _on_loading_failed)
    sb.cdp.add_handler(cdp_network.EventSourceMessageReceived, _on_eventsource_message)

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


def force_conversation_probe_requests(sb):
    script = r"""
const done = arguments[arguments.length - 1];
(async () => {
  const out = [];
  async function hit(url, body, timeoutMs) {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort("timeout"), timeoutMs);
    try {
      const resp = await fetch(url, {
        method: "POST",
        headers: {"content-type": "application/json"},
        body: JSON.stringify(body ?? {}),
        signal: controller.signal,
      });
      const text = await resp.text();
      out.push({url, status: resp.status, ok: resp.ok, chars: text.length});
    } catch (e) {
      out.push({url, error: String(e)});
    } finally {
      clearTimeout(timeout);
    }
  }
  await hit("/backend-anon/conversation/init", {probe: "capture"}, 8000);
  await hit(
    "/backend-anon/conversation/experimental/generate_trending_suggestions",
    {},
    8000
  );
  done(out);
})();
"""
    try:
        result = sb.execute_async_script(script)
        print(f"[CONVERSATION] Probe calls result: {result}")
    except Exception as e:
        print(f"[CONVERSATION][WARN] Probe requests failed: {e}")


def _build_conversation_prompt_url(base_url, prompt):
    try:
        parts = urlsplit(base_url or "")
        if not parts.scheme or not parts.netloc:
            return None
        query_pairs = parse_qsl(parts.query, keep_blank_values=True)
        query = {k: v for k, v in query_pairs}
        query["q"] = prompt
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(query),
                parts.fragment,
            )
        )
    except Exception:
        return None


def _wait_for_conversation_request(previous_ids, previous_primary_ids, detect_timeout=20):
    kind, request_id, request_url = _wait_for_new_f_conversation_activity(
        previous_ids, timeout=detect_timeout
    )
    if not request_id:
        return None, None, None
    if kind == "conversation":
        return kind, request_id, request_url
    primary = _wait_for_new_primary_conversation_request(
        previous_primary_ids,
        timeout=max(20, int(detect_timeout * 2)),
    )
    if not primary:
        print(
            f"[CONVERSATION][WARN] Saw only prepare request ({request_id}); "
            "main /f/conversation was not observed."
        )
        return None, None, None
    request_id = primary
    request_url = _request_url_by_id.get(request_id)
    return "conversation", request_id, request_url


def send_prompt_and_wait(sb, prompt, chat_url=None):
    def _is_css_selector(selector):
        if not isinstance(selector, str):
            return False
        sel = selector.strip()
        return bool(sel) and not sel.startswith("/")

    def _set_prompt_js(selector, value):
        if not _is_css_selector(selector):
            return False
        script = (
            "(() => {\n"
            f"const selector = {json.dumps(selector)};\n"
            f"const value = {json.dumps(value)};\n"
            "const el = document.querySelector(selector);\n"
            "if (!el) return false;\n"
            "el.focus();\n"
            "if (el.isContentEditable) {\n"
            "  el.textContent = \"\";\n"
            "  el.dispatchEvent(new Event(\"input\", { bubbles: true }));\n"
            "  el.textContent = value;\n"
            "  el.dispatchEvent(new Event(\"input\", { bubbles: true }));\n"
            "  el.dispatchEvent(new Event(\"change\", { bubbles: true }));\n"
            "  return true;\n"
            "}\n"
            "if (\"value\" in el) {\n"
            "  el.value = \"\";\n"
            "  el.dispatchEvent(new Event(\"input\", { bubbles: true }));\n"
            "  el.value = value;\n"
            "  el.dispatchEvent(new Event(\"input\", { bubbles: true }));\n"
            "  el.dispatchEvent(new Event(\"change\", { bubbles: true }));\n"
            "  return true;\n"
            "}\n"
            "return false;\n"
            "})();"
        )
        try:
            return bool(sb.execute_script(script))
        except Exception:
            return False

    def _composer_text_len(selector="#prompt-textarea"):
        if not _is_css_selector(selector):
            return -1
        script = (
            "(() => {\n"
            f"const selector = {json.dumps(selector)};\n"
            "const el = document.querySelector(selector);\n"
            "if (!el) return 0;\n"
            "let text = \"\";\n"
            "if (el.isContentEditable) {\n"
            "  text = el.innerText || el.textContent || \"\";\n"
            "} else if (\"value\" in el) {\n"
            "  text = el.value || \"\";\n"
            "} else {\n"
            "  text = el.textContent || \"\";\n"
            "}\n"
            "return (text || \"\").trim().length;\n"
            "})();"
        )
        try:
            return int(sb.execute_script(script) or 0)
        except Exception:
            return -1

    def _composer_text(selector="#prompt-textarea"):
        if not _is_css_selector(selector):
            return ""
        script = (
            "(() => {\n"
            f"const selector = {json.dumps(selector)};\n"
            "const el = document.querySelector(selector);\n"
            "if (!el) return \"\";\n"
            "if (el.isContentEditable) {\n"
            "  return (el.innerText || el.textContent || \"\").trim();\n"
            "}\n"
            "if (\"value\" in el) {\n"
            "  return (el.value || \"\").trim();\n"
            "}\n"
            "return (el.textContent || \"\").trim();\n"
            "})();"
        )
        try:
            return str(sb.execute_script(script) or "").strip()
        except Exception:
            return ""

    def _composer_exists(selector="#prompt-textarea"):
        if not _is_css_selector(selector):
            return False
        try:
            script = (
                "(() => !!document.querySelector("
                + json.dumps(selector)
                + "))();"
            )
            return bool(sb.execute_script(script))
        except Exception:
            return False

    def _send_button_state():
        script = r"""
const btn = document.querySelector('button[data-testid="send-button"]')
  || document.querySelector('button[aria-label*="Send"]');
if (!btn) return {exists: false};
const rects = btn.getClientRects();
const visible = !!(rects && rects.length);
return {
  exists: true,
  disabled: !!btn.disabled,
  visible,
  ariaLabel: btn.getAttribute("aria-label") || "",
  text: (btn.innerText || btn.textContent || "").trim().slice(0, 80),
};
"""
        try:
            state = sb.execute_script(script)
            return state if isinstance(state, dict) else {"exists": False}
        except Exception:
            return {"exists": False}

    def _list_candidate_buttons():
        script = r"""
(() => {
  const out = [];
  for (const btn of Array.from(document.querySelectorAll("button")).slice(0, 400)) {
    const testid = btn.getAttribute("data-testid") || "";
    const aria = btn.getAttribute("aria-label") || "";
    const txt = (btn.innerText || btn.textContent || "").trim();
    if (/send|submit|composer|stop|voice|search|study|create/i.test(testid + " " + aria + " " + txt)) {
      out.push({
        testid,
        aria,
        txt: txt.slice(0, 80),
        disabled: !!btn.disabled,
        visible: !!(btn.getClientRects && btn.getClientRects().length),
      });
    }
  }
  return out;
})();
"""
        try:
            rows = sb.execute_script(script)
            return rows if isinstance(rows, list) else []
        except Exception:
            return []

    def _is_login_gate_visible():
        script = r"""
(() => {
  const bodyText = (document.body && (document.body.innerText || "")) || "";
  const hasLoginButton = !!document.querySelector('[data-testid="login-button"]');
  const hasSignupButton = !!document.querySelector('[data-testid="signup-button"]');
  const hasEmailInput = !!document.querySelector('input[placeholder*="Email" i]');
  const hasPrompt = !!document.querySelector("#prompt-textarea");
  const hasGateText = /log in or sign up/i.test(bodyText);
  return Boolean((hasLoginButton || hasSignupButton || hasEmailInput || hasGateText) && !hasPrompt);
})();
"""
        try:
            return bool(sb.execute_script(script))
        except Exception:
            return False

    def _click_send_button():
        script = r"""
const selectors = [
  'button[data-testid="send-button"]',
  'button[aria-label*="Send"]'
];
for (const sel of selectors) {
  const btn = document.querySelector(sel);
  if (!btn) continue;
  const visible = !!(btn.getClientRects && btn.getClientRects().length);
  if (!visible || btn.disabled) continue;
  btn.click();
  return true;
}
return false;
"""
        try:
            if bool(sb.execute_script(script)):
                return True
        except Exception:
            pass
        return False

    def _press_enter(selector):
        try:
            sb.press_keys(selector, "\n")
            return True
        except Exception:
            return False

    def _dispatch_enter_js(selector):
        if not _is_css_selector(selector):
            return False
        script = (
            "(() => {\n"
            f"const selector = {json.dumps(selector)};\n"
            "const el = document.querySelector(selector) || document.querySelector(\"#prompt-textarea\");\n"
            "if (!el) return false;\n"
            "el.focus();\n"
            "const down = new KeyboardEvent(\"keydown\", {\n"
            "  key: \"Enter\", code: \"Enter\", keyCode: 13, which: 13, bubbles: true\n"
            "});\n"
            "const up = new KeyboardEvent(\"keyup\", {\n"
            "  key: \"Enter\", code: \"Enter\", keyCode: 13, which: 13, bubbles: true\n"
            "});\n"
            "el.dispatchEvent(down);\n"
            "el.dispatchEvent(up);\n"
            "return true;\n"
            "})();"
        )
        try:
            return bool(sb.execute_script(script))
        except Exception:
            return False

    post_submit_timeout = int(os.getenv("CONVERSATION_POST_SUBMIT_TIMEOUT", "20"))
    should_try_url_fallback = False

    for attempt in range(1, 4):
        with suppress(Exception):
            is_popups_visible(sb, timeout=5)

        textarea = wait_for_textarea(sb, timeout=35)
        if not textarea:
            if _is_login_gate_visible():
                print(
                    "[CONVERSATION][WARN] Login wall detected. "
                    "Please log in (or disable incognito and reuse profile)."
                )
            print(f"[CONVERSATION][WARN] Prompt box not found (attempt {attempt}/3).")
            continue

        textarea_selector = "#prompt-textarea"
        if not _composer_exists(textarea_selector):
            textarea_selector = textarea

        typed = safe_type(sb, textarea_selector, prompt, label=f"prompt_text_{attempt}")
        if not typed:
            typed = _set_prompt_js(textarea_selector, prompt) or _set_prompt_js("#prompt-textarea", prompt)
        if not typed:
            print(f"[CONVERSATION][WARN] Could not type prompt (attempt {attempt}/3).")
            continue

        seen_texts = []
        for sel in (textarea_selector, "#prompt-textarea"):
            txt = _composer_text(sel)
            if txt:
                seen_texts.append(txt)
        normalized_seen = " | ".join(seen_texts).lower()
        normalized_prompt = str(prompt or "").strip().lower()
        has_prompt_text = False
        if normalized_prompt and normalized_seen:
            if normalized_prompt in normalized_seen:
                has_prompt_text = True
            else:
                has_prompt_text = normalized_prompt[:16] in normalized_seen

        if not has_prompt_text:
            typed = _set_prompt_js("#prompt-textarea", prompt) or _set_prompt_js(textarea_selector, prompt)
            if typed:
                seen_texts = []
                for sel in (textarea_selector, "#prompt-textarea"):
                    txt = _composer_text(sel)
                    if txt:
                        seen_texts.append(txt)
                normalized_seen = " | ".join(seen_texts).lower()
                if normalized_prompt and normalized_seen:
                    if normalized_prompt in normalized_seen:
                        has_prompt_text = True
                    else:
                        has_prompt_text = normalized_prompt[:16] in normalized_seen

        if not has_prompt_text:
            snippet = " | ".join(seen_texts)[:160] if seen_texts else "<empty>"
            print(
                "[CONVERSATION][WARN] Prompt text not present in composer after typing. "
                f"Observed text: {snippet}"
            )
            should_try_url_fallback = True
            continue

        state = _send_button_state()
        print(
            "[CONVERSATION] Send button state "
            f"(attempt {attempt}): exists={state.get('exists')} "
            f"visible={state.get('visible')} disabled={state.get('disabled')}"
        )
        if not state.get("exists"):
            print(
                "[CONVERSATION][WARN] Send button not present in DOM. "
                "Skipping UI submit actions for this attempt."
            )
            candidates = _list_candidate_buttons()
            if candidates:
                print(f"[CONVERSATION] Candidate buttons: {json.dumps(candidates[:8], ensure_ascii=False)}")
            should_try_url_fallback = True
            continue
        # Reset baselines after typing so /prepare events triggered by typing
        # are not mistaken for an actual submitted prompt stream.
        previous_ids = {rid for rid, url in _request_url_by_id.items() if _is_any_f_conversation_url(url)}
        previous_primary_ids = set(_primary_conversation_request_ids())

        submit_actions = [
            ("click_send_button", _click_send_button),
            ("press_enter", lambda: _press_enter("#prompt-textarea")),
            (
                "cdp_send_keys_enter",
                lambda: safe_send_keys(
                    sb,
                    "#prompt-textarea",
                    "\n",
                    label=f"prompt_enter_primary_{attempt}",
                ),
            ),
            ("dispatch_enter_js", lambda: _dispatch_enter_js("#prompt-textarea")),
        ]
        kind = request_id = request_url = None
        for action_name, action in submit_actions:
            fired = False
            with suppress(Exception):
                fired = bool(action())
            if not fired:
                continue
            print(f"[CONVERSATION] Submit action fired ({action_name}) on attempt {attempt}")
            kind, request_id, request_url = _wait_for_conversation_request(
                previous_ids,
                previous_primary_ids,
                detect_timeout=post_submit_timeout,
            )
            if request_id:
                print(f"[CONVERSATION] Prompt sent (attempt {attempt}): {prompt}")
                print(f"[CONVERSATION] Detected /f/conversation activity: {request_id} ({kind})")
                break
            print(
                f"[CONVERSATION][WARN] No /f/conversation activity after {action_name}. "
                "Trying next submit action."
            )

        if not request_id:
            print("[CONVERSATION][WARN] Submit actions did not produce /f/conversation.")
            should_try_url_fallback = True
            continue

        _force_enable_stream_capture(
            request_id,
            timeout=int(os.getenv("CONVERSATION_STREAM_ENABLE_TIMEOUT", "90")),
        )
        finished = _wait_for_request_body_saved(
            request_id,
            timeout=int(os.getenv("CONVERSATION_CAPTURE_TIMEOUT", "420")),
        )
        if finished:
            print(f"[CONVERSATION] Stream/body capture finished for request {request_id}")
        else:
            print(
                f"[CONVERSATION][WARN] Timed out waiting for response artifacts of {request_id} "
                f"url={request_url}"
            )
        with suppress(Exception):
            sb.wait_for_element_not_visible('button[data-testid=\"stop-button\"]', timeout=240)
        return True

    if (
        should_try_url_fallback
        and chat_url
        and os.getenv("CONVERSATION_Q_URL_FALLBACK", "1") == "1"
    ):
        prompt_url = _build_conversation_prompt_url(chat_url, prompt)
        if prompt_url:
            previous_ids = {
                rid for rid, url in _request_url_by_id.items() if _is_any_f_conversation_url(url)
            }
            previous_primary_ids = set(_primary_conversation_request_ids())
            print(f"[CONVERSATION] Trying URL q= fallback: {prompt_url}")
            with suppress(Exception):
                sb.cdp.open(prompt_url)
            kind, request_id, request_url = _wait_for_conversation_request(
                previous_ids,
                previous_primary_ids,
                detect_timeout=max(30, post_submit_timeout),
            )
            if request_id:
                print(f"[CONVERSATION] URL fallback detected request: {request_id} ({kind})")
                _force_enable_stream_capture(
                    request_id,
                    timeout=int(os.getenv("CONVERSATION_STREAM_ENABLE_TIMEOUT", "90")),
                )
                finished = _wait_for_request_body_saved(
                    request_id,
                    timeout=int(os.getenv("CONVERSATION_CAPTURE_TIMEOUT", "420")),
                )
                if finished:
                    print(f"[CONVERSATION] Stream/body capture finished for request {request_id}")
                    return True
                print(
                    f"[CONVERSATION][WARN] Timed out waiting for response artifacts of {request_id} "
                    f"url={request_url}"
                )

    return False


def apply_bandwidth_saver(sb):
    """
    Tells Chrome to natively block images, fonts, and telemetry.
    Safe for Cloudflare because we DO NOT block the challenge scripts.
    """
    # 1. Patterns to block (Wildcards supported)
    blocked_patterns = [
        # --- Heavy Media (Saves ~30% Bandwidth) ---
        "*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp", "*.ico", "*.svg",  # Images
        "*.woff", "*.woff2", "*.ttf", "*.otf",                            # Fonts
        "*.mp4", "*.webm", "*.mp3", "*.wav",                              # A/V

        # --- Telemetry & Spies (Saves ~60% Bandwidth) ---
        "*sentry*",          # Error logging (Very heavy on ChatGPT)
        "*statsig*",         # Feature flags (Constant pings)
        "*segment*",         # User tracking
        "*intercom*",        # Chat widgets
        "*doubleclick*",     # Ads
        "*google-analytics*",
        "*datadog*",         # Performance monitoring
        "*ab.chatgpt.com*",  # Internal A/B testing
        
        # --- Optional: Block specific heavy CSS if you dare ---
        # "*.css",           # WARNING: Un-commenting this saves bandwidth but might break sb.click()
    ]

    # 2. Execute the Chrome DevTools Command
    # This works even if you are in UC mode or normal mode.
    print(f"[BANDWIDTH] Blocking {len(blocked_patterns)} resource patterns...")
    sb.driver.execute_cdp_cmd("Network.enable", {})
    sb.driver.execute_cdp_cmd("Network.setBlockedURLs", {"urls": blocked_patterns})


t0 = time.perf_counter()

proxy = (os.getenv("CHATGPT_PROXY", "versedoin_Xdhcu:AyJ+cQ0Xi7fnTx@pr.oxylabs.io:7777") or "").strip() or None

if __name__ == "__main__":

    
    # Set to None to disable filtering, or pass a JSON-compatible dict to filter.
    website_filter = {"url": "https://polygon.technology/", "location": "US"}
    
    use_incognito = os.getenv("CHATGPT_INCOGNITO", "0") == "1"
    user_data_dir = (os.getenv("CHATGPT_USER_DATA_DIR", "") or "").strip() or None
    with SB(
        uc=True,
        uc_cdp_events=True,
        test=True,
        incognito=use_incognito,
        locale="en",
        user_data_dir=user_data_dir,
        proxy=proxy,
    ) as sb:
        print("Systems green, standing by.")
        print("\n")
        print(
            f"[CONFIG] incognito={use_incognito} "
            f"user_data_dir={user_data_dir or '<none>'} "
            f"proxy={'set' if proxy else 'none'}"
        )

        url = os.getenv("CHATGPT_URL", "https://chatgpt.com/?temporary-chat=true&hints=search")
        prompt_text = os.getenv(
            "CHATGPT_CAPTURE_PROMPT",
            "Use web search. Give a short answer about the latest NVIDIA earnings and include at least 2 citations with full URLs.",
        )
        
        # Attach handlers before page navigation so early conversation
        # requests/responses are not missed.
        # SeleniumBase docs recommend enabling CDP from about:blank first.
        sb.activate_cdp_mode("about:blank")
        start_network_capture_cdp_mode(sb)
        sb.cdp.open(url)
        if os.getenv("FORCE_CONVERSATION_PROBE", "0") == "1":
            force_conversation_probe_requests(sb)
        sleep_dbg(sb, 8, 12)
        prompt_sent = False
        try:
            prompt_sent = send_prompt_and_wait(sb, prompt_text, chat_url=url)
        except Exception as e:
            print(f"[CONVERSATION][WARN] Prompt flow failed: {e}")
        if not prompt_sent:
            print("[CONVERSATION][WARN] First prompt attempt failed. Retrying after popup close.")
            with suppress(Exception):
                is_popups_visible(sb, timeout=10)
            with suppress(Exception):
                prompt_sent = send_prompt_and_wait(sb, prompt_text, chat_url=url)
        if not prompt_sent:
            manual_wait = int(os.getenv("MANUAL_CAPTURE_WAIT", "240"))
            prompt_sent = wait_for_manual_conversation_capture(timeout=manual_wait)
        sleep_dbg(sb, 4, 7)
        finalize_conversation_summary()
        if not prompt_sent:
            print("[CONVERSATION][WARN] Prompt was not sent. Summary may only include init/trending endpoints.")
        save_ss(sb)
        print("\n" * 3)
        # apply_bandwidth_saver(sb)
        # activate_search_mode(sb)
        

        dt = time.perf_counter() - t0
        h = int(dt // 3600)
        m = int((dt % 3600) // 60)
        s = dt % 60
        print(f"Runtime: {h}h {m}m {s:.2f}s")

        print("\n" * 3)
