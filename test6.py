from contextlib import suppress
import ast
import base64
import json
import os
import re
import time
import random
from pathlib import Path
from threading import Lock
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from playwright.sync_api import sync_playwright
from seleniumbase import SB
from main import build_metrics
from utils import sleep_dbg
from utils import save_ss
from utils import safe_send_keys
from utils import safe_type
from utils import wait_for_textarea
from utils import _get_cdp
from is_pages.is_verification_page import *
from is_pages.is_pop_ups import *
from is_pages.is_chat_ui import *
from boomlify_codes import *
from activate_search_mode import *
from birthday_helpers import fill_birthday
import mycdp.network as cdp_network
# SSE answer + citation parser.
from master import _extract_answer_and_citations_from_sse
from master import enter_prompt


RUN_RESULT_DONE = "done"
RUN_RESULT_RESTART = "restart"
RUN_RESULT_NO_PROMPTS = "no_prompts"

# Total prompts processed across browser restarts within one script run.
global_prompts = 1


def _to_jsonb(value):
    if value is None:
        return None
    with suppress(Exception):
        return json.dumps(value)
    return None

def pick_prompt(website_filter=None, batch_size=None):

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("Missing DATABASE_URL in environment/.env")

    if batch_size is None:
        try:
            batch_size = int(os.getenv("PROMPT_BATCH_SIZE", "1"))
        except ValueError:
            batch_size = 1
    else:
        try:
            batch_size = int(batch_size)
        except (TypeError, ValueError):
            batch_size = 1
    batch_size = max(1, batch_size)

    prompt_engine = os.getenv("PROMPT_ENGINE", "chatgpt").strip() or "chatgpt"
    engine_account = (
        os.getenv("PROMPT_ENGINE_ACCOUNT", "github actions").strip()
        or "github actions"
    )
    pending_status = os.getenv("PROMPT_PENDING_STATUS", "pending").strip() or "pending"
    failed_status = os.getenv("PROMPT_FAILED_STATUS", "failed").strip() or "failed"
    processing_status = os.getenv("PROMPT_PROCESSING_STATUS", "processing").strip() or "processing"

    only_today_raw = os.getenv("PROMPT_ONLY_TODAY")
    # Default: only process prompts created "today" (CURRENT_DATE in DB timezone).
    # Set PROMPT_ONLY_TODAY=0/false/no to disable this filter.
    if only_today_raw is None or not only_today_raw.strip():
        only_today = True
    else:
        only_today = only_today_raw.strip().lower() in {"1", "true", "yes", "y"}
    today_clause = ""
    if only_today:
        # Uses the DB session timezone for CURRENT_DATE.
        today_clause = "AND created_at >= CURRENT_DATE AND created_at < (CURRENT_DATE + INTERVAL '1 day')"

    website_filter_json = None
    if website_filter is not None:
        if isinstance(website_filter, (dict, list)):
            website_filter_json = json.dumps(website_filter)
        elif isinstance(website_filter, str):
            text = website_filter.strip()
            if not text or text.lower() in {"none", "null"}:
                website_filter_json = None
            elif text:
                # Validate JSON text before sending it to Postgres jsonb operator.
                try:
                    json.loads(text)
                    website_filter_json = text
                except Exception:
                    print(f"[WARN] Invalid WEBSITE_FILTER JSON, ignoring: {text}")
                    website_filter_json = None
        else:
            raise ValueError("website_filter must be dict/list/JSON-string/None")

    website_filter_clause = ""
    if website_filter_json is not None:
        website_filter_clause = "AND website @> %s::jsonb"

    claim_sql = f"""
        WITH picked AS (
            SELECT id
            FROM public.prompts
            WHERE status IN (%s, %s, %s)
              AND engine = %s
              {website_filter_clause}
              {today_clause}
              AND prompt_text IS NOT NULL
              AND BTRIM(prompt_text) <> ''
            ORDER BY
                CASE status
                    WHEN %s THEN 1
                    WHEN %s THEN 2
                    WHEN %s THEN 3
                    ELSE 99
                END,
                created_at ASC,
                prompt_id ASC
            FOR UPDATE SKIP LOCKED
            LIMIT %s
        )
        UPDATE public.prompts AS p
        SET status = %s,
            engine_account = %s,
            started_at = COALESCE(p.started_at, NOW()),
            attempts = COALESCE(p.attempts, 0) + 1
        FROM picked
        WHERE p.id = picked.id
        RETURNING
            p.id,
            p.prompt_id,
            p.prompt_text,
            p.status,
            p.created_at,
            p.website,
            p.competitor_websites;
    """

    query_params = [
        pending_status,
        failed_status,
        processing_status,
        prompt_engine,
    ]
    if website_filter_json is not None:
        query_params.append(website_filter_json)
    query_params.extend(
        [
            pending_status,
            failed_status,
            processing_status,
            batch_size,
            processing_status,
            engine_account,
        ]
    )

    rows = []
    query_attempted = False

    with suppress(ImportError):
        import psycopg  # type: ignore

        query_attempted = True
        with psycopg.connect(database_url, connect_timeout=8) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    claim_sql,
                    tuple(query_params),
                )
                rows = cur.fetchall()
            conn.commit()

    if not query_attempted:
        with suppress(ImportError):
            import psycopg2  # type: ignore

            query_attempted = True
            conn = psycopg2.connect(database_url, connect_timeout=8)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        claim_sql,
                        tuple(query_params),
                    )
                    rows = cur.fetchall()
                conn.commit()
            finally:
                conn.close()

    if not query_attempted:
        raise RuntimeError(
            "Postgres driver not found. Install one: `pip install psycopg[binary]`"
        )

    if not rows:
        print(
            "[PROMPTS] No prompts found in priority order: "
            f"{pending_status} > {failed_status} > {processing_status}."
        )
        return []

    prompts = []
    for row in rows:
        prompt_text = str(row[2] or "").strip()
        if not prompt_text:
            continue
        prompts.append(
            {
                "id": str(row[0]),
                "prompt_id": row[1],
                "prompt_text": prompt_text,
                "status": row[3],
                "created_at": str(row[4]) if row[4] is not None else None,
                "website": row[5],
                "competitor_websites": row[6],
            }
        )

    if not prompts:
        print("[PROMPTS] Claimed rows had empty prompt_text values.")
        return []

    if only_today:
        print("[PROMPTS] Using CURRENT_DATE filter (today-only). Set PROMPT_ONLY_TODAY=0 to disable.")
    print(f"[PROMPTS] Claimed {len(prompts)} prompt(s) from AWS RDS.")
    return prompts


def update_prompt_result(
    prompt_id,
    status,
    response_text=None,
    error_text=None,
    prompt_text=None,
    source_links=None,
    website=None,
    competitor_websites=None,
    engine_account="github actions",
    skip_if_missing=True,
):
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("Missing DATABASE_URL")

    metrics = None
    if status == "completed":
        metrics = build_metrics(
            response_text=response_text,
            source_links=source_links or [],
            website=website,
            competitor_websites=competitor_websites,
        )

    sql = """
        UPDATE public.prompts
        SET status = %s,
            engine_account = COALESCE(%s, engine_account),
            prompt_text = COALESCE(%s, prompt_text),
            response_text = COALESCE(%s, response_text),
            error_text = COALESCE(%s, error_text),
            appeared_links = COALESCE(%s::jsonb, appeared_links),
            appeared_links_unique = COALESCE(%s::jsonb, appeared_links_unique),
            my_citations = COALESCE(%s::jsonb, my_citations),
            competitor_citations = COALESCE(%s::jsonb, competitor_citations),
            total_citations_count = COALESCE(%s, total_citations_count),
            my_domain_citations_count = COALESCE(%s, my_domain_citations_count),
            my_brand_mentions_count = COALESCE(%s, my_brand_mentions_count),
            finished_at = CASE
                WHEN %s IN ('completed', 'failed') THEN NOW()
                ELSE finished_at
            END
        WHERE id = %s AND status <> 'completed'
        RETURNING id, status;
    """

    import psycopg
    with psycopg.connect(database_url, connect_timeout=8) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    status,
                    engine_account,
                    prompt_text,
                    response_text,
                    error_text,
                    _to_jsonb(source_links if status == "completed" else None),
                    _to_jsonb(metrics["appeared_links_unique"]) if metrics else None,
                    _to_jsonb(metrics["my_citations"]) if metrics else None,
                    _to_jsonb(metrics["competitor_citations"]) if metrics else None,
                    metrics["total_citations_count"] if metrics else None,
                    metrics["my_domain_citations_count"] if metrics else None,
                    metrics["my_brand_mentions_count"] if metrics else None,
                    status,
                    prompt_id,
                ),
            )
            row = cur.fetchone()
        conn.commit()

    if not row:
        if skip_if_missing:
            print(
                f"[PROMPTS][SKIP] Prompt {prompt_id} not updated "
                "(missing/already completed)."
            )
            return {"id": str(prompt_id), "status": "skipped", "updated": False}
        raise RuntimeError(f"Prompt {prompt_id} not updated (missing/already completed?)")
    return {"id": str(row[0]), "status": row[1], "updated": True}



proxy = (os.getenv("CHATGPT_PROXY") or "").strip() or None
t0 = time.perf_counter()

LIST_OF_POPUPS = [
    'a:contains("Stay logged out")',
    'a[href="#"]:contains("Stay logged out")',
    'button:contains("Stay logged out")',
    'div:contains("Stay logged out")',
]


def js_click_by_text(sb, text, tag="button"):
    """Click a DOM element by its text content using JavaScript.
    Bypasses CDP click issues and overlay blocking.
    Wrapped in IIFE so repeated calls don't clash in CDP's shared scope."""
    sb.execute_script(f'''
        (() => {{
            const els = document.querySelectorAll("{tag}");
            for (const el of els) {{
                if (el.textContent.trim().includes("{text}")) {{
                    el.click();
                    return;
                }}
            }}
        }})();
    ''')


def create_chatgpt_account(sb):
    global global_prompts
    t0 = time.perf_counter()  # Start timer
    print("Systems green, standing by.")
    print("\n")

    url = "https://chatgpt.com/?temporary-chat=true"
    # https://auth.openai.com/log-in-or-create-account
    # https://chatgpt.com/?temporary-chat=true&hints=search&q
    # https://chatgpt.com/auth/login_with
    # https://chatgpt.com/auth/login
    # https://chatgpt.com/

    # sb.uc_open_with_reconnect(url, 4)
    sb.activate_cdp_mode("about:blank")
    # Start capture BEFORE navigation so we don't miss early /f/conversation requests.
    endpoint_url=sb.cdp.get_endpoint_url()

    with sync_playwright() as p:

        browser=p.chromium.connect_over_cdp(endpoint_url)
        context=browser.contexts[0]
        page=context.pages[0]

        # --- Playwright SSE response capture ---
        # Playwright's page.on('response') fires for ALL network responses on
        # this page, including those triggered by SeleniumBase CDP.  We store
        # the Response objects here; after each prompt we call resp.body()
        # from the main thread which blocks until the full SSE stream finishes.
        _pw_responses = []
        _all_results = []

        def _on_pw_response(response):
            url_str = response.url
            if (
                '/backend-anon/f/conversation' in url_str
                or '/backend-api/f/conversation' in url_str
            ) and '/prepare' not in url_str:
                _pw_responses.append(response)
                print(f"[PW] Conversation response detected: {url_str}")

        page.on('response', _on_pw_response)

        # page.goto("https://chatgpt.com/?temporary-chat=true")
        sb.cdp.open(url)
        print("\n" * 3)
        save_ss(sb)
        print("### [START] POP UPS and CHAT UI ###")

        popups_appeared=is_popups_visible(sb)
        print("\n")
        print(f"Popups appeared TRY 4 : {popups_appeared}")
        print("\n")


        if is_chat_ui_visible(sb)==True:
            dt = time.perf_counter() - t0
            h = int(dt // 3600)
            m = int((dt % 3600) // 60)
            s = dt % 60
            print(f"Runtime: {h}h {m}m {s:.2f}s")
        
        
        activate_search_mode(sb)

        # --- Fetch prompts from database ---
        website_filter = os.getenv("WEBSITE_FILTER")
        if website_filter is not None:
            website_filter = website_filter.strip()
            if not website_filter or website_filter.lower() in {"none", "null"}:
                website_filter = None

        engine_account = os.getenv("PROMPT_ENGINE_ACCOUNT", "github actions").strip() or "github actions"

        prompt_number=1
        
        # Iterate through prompts one-by-one (claim 1 row at a time).
        cdp = _get_cdp(sb)
        needs_restart = False
        restart_reason = None
        total_claimed = 0

        max_prompts_per_session_raw = (os.getenv("MAX_PROMPTS_PER_SESSION", "200") or "200").strip()
        try:
            max_prompts_per_session = int(max_prompts_per_session_raw or "0")
        except ValueError:
            max_prompts_per_session = 0
        max_prompts_per_session = max(0, max_prompts_per_session)

        def _get_response_body_with_timeout(resp, timeout_s):
            # Playwright's sync API can't be called from other threads (greenlet-based),
            # so implement a timeout by wrapping the underlying coroutine with asyncio.wait_for.
            import asyncio

            if timeout_s is None or timeout_s <= 0:
                return resp.body(), None
            try:
                body = resp._sync(asyncio.wait_for(resp._impl_obj.body(), timeout=timeout_s))
                return body, None
            except Exception as e:  # noqa: BLE001
                return None, e

        sse_body_timeout_s = int(os.getenv("SSE_BODY_TIMEOUT_S", "240"))

        while True:
            if max_prompts_per_session and total_claimed >= max_prompts_per_session:
                print(f"[PROMPTS] Reached MAX_PROMPTS_PER_SESSION={max_prompts_per_session}.")
                break

            db_prompts = pick_prompt(website_filter=website_filter, batch_size=1)
            if not db_prompts:
                if total_claimed == 0:
                    print("[DB] No prompts to process. Exiting.")
                    return RUN_RESULT_NO_PROMPTS
                print("[DB] No more prompts to process. Exiting.")
                break

            prompt_obj = db_prompts[0]
            total_claimed += 1
            # Extract metadata from database prompt object
            current_prompt_id = prompt_obj["id"]
            current_prompt_text = prompt_obj["prompt_text"]
            current_website = prompt_obj.get("website")
            current_competitor_websites = prompt_obj.get("competitor_websites")
            try:
                for sel in LIST_OF_POPUPS:
                    # visible() and click() are SeleniumBase builtins
                    if cdp.is_element_visible(sel):
                        cdp.click(sel)
                        save_ss(sb,"Pop up closed")
                        print(f"::warning::[POP UP] Closed popup/button using selector: {sel}")
                        # sb.save_screenshot(screenshot_name)
                        sb.sleep(random.uniform(2, 4))  # Pause after closing to allow follow-up popups
                        break
                print(f"[DB] Processing prompt #{prompt_number}: {current_prompt_text[:100]}...")
                if prompt_number%50==0:
                    _pw_responses.clear()  # Discard stale responses before refresh
                    sb.refresh_page()
                    print("[PAGE REFRESHED]")
                    sleep_dbg(sb,3,10)
                    activate_search_mode(sb)
                    save_ss(sb)
                print("\n" * 3)
                print(f"[GLOBAL PROMPTS]: {global_prompts}")
                dt = time.perf_counter() - t0
                h = int(dt // 3600)
                m = int((dt % 3600) // 60)
                s = dt % 60
                print(f"[PROMPT NUMBER]: {prompt_number}")
                print(f"[RUNTIME]: {h}h {m}m {s:.2f}s")
                global_prompts=global_prompts+1
                print(prompt_number)
                print("\n" * 3)
                
                enter_prompt(sb, current_prompt_text)
                if prompt_number%10==0:
                    activate_search_mode(sb)
                prompt_number=prompt_number+1
                # Wait for the response/search to complete before the next one
                # Adjust timing based on how long 'enter_prompt' waits intenally
                sleep_dbg(sb, 5, 8)

                # --- Collect SSE response via Playwright ---
                # Flush Playwright's pending event queue so response handlers fire.
                answer = ""
                citations = []
                try:
                    page.evaluate("void(0)")
                except Exception:
                    pass

                for resp in list(_pw_responses):
                    try:
                        # .body() blocks until the full SSE stream is done, then
                        # returns the complete response body (all event: / data: lines).
                        body, body_err = _get_response_body_with_timeout(resp, sse_body_timeout_s)
                        if body_err is not None:
                            raise body_err
                        text = (body or b"").decode('utf-8', errors='replace')
                        if text and len(text) > 50:
                            answer, citations = _extract_answer_and_citations_from_sse(text)
                            _all_results.append({
                                'prompt_number': prompt_number - 1,
                                'prompt_id': current_prompt_id,
                                'prompt': current_prompt_text,
                                'answer': answer,
                                'answer_chars': len(answer),
                                'citations': citations,
                                'citations_count': len(citations),
                            })
                            print(f"[PW] Answer: {len(answer)} chars | Citations: {len(citations)}")
                            if answer:
                                print(f"[PW] Preview: {answer[:200]}...")
                    except Exception as e:
                        print(f"[PW] Body capture failed: {e}")
                _pw_responses.clear()

                # --- Validate citations (zero = browser issue, restart required) ---
                if answer and len(citations) == 0:
                    print(f"[CRITICAL] Zero citations detected for prompt {current_prompt_id}!")
                    print(f"[CRITICAL] This indicates a browser/network issue. Marking as failed and restarting browser...")
                    try:
                        update_prompt_result(
                            prompt_id=current_prompt_id,
                            status="failed",
                            error_text="Zero citations returned - browser restart required",
                            engine_account=engine_account,
                        )
                        print(f"[DB] Marked prompt {current_prompt_id} as failed (zero citations)")
                    except Exception as e:
                        print(f"[DB] Failed to mark prompt {current_prompt_id} as failed: {e}")
                    needs_restart = True
                    restart_reason = f"zero_citations prompt_id={current_prompt_id}"
                    break

                # --- Update database with successful result ---
                if answer:
                    try:
                        update_prompt_result(
                            prompt_id=current_prompt_id,
                            status="completed",
                            response_text=answer,
                            source_links=citations,
                            website=current_website,
                            competitor_websites=current_competitor_websites,
                            engine_account=engine_account,
                        )
                        print(f"[DB] Marked prompt {current_prompt_id} as completed")
                    except Exception as e:
                        print(f"[DB] Failed to update prompt {current_prompt_id}: {e}")
                        needs_restart = True
                        restart_reason = f"db_update_failed prompt_id={current_prompt_id}"
                        break
                else:
                    print(f"[DB] WARNING: No answer captured for prompt {current_prompt_id}")
                    try:
                        update_prompt_result(
                            prompt_id=current_prompt_id,
                            status="failed",
                            error_text="No answer captured from Playwright network capture",
                            engine_account=engine_account,
                        )
                        print(f"[DB] Marked prompt {current_prompt_id} as failed (no answer)")
                    except Exception as e:
                        print(f"[DB] Failed to mark prompt {current_prompt_id} as failed: {e}")
                    needs_restart = True
                    restart_reason = f"no_answer_captured prompt_id={current_prompt_id}"
                    break

                dt = time.perf_counter() - t0
                h = int(dt // 3600)
                m = int((dt % 3600) // 60)
                s = dt % 60
                print(f"[PROMPT NUMBER]: {prompt_number}")
                print(f"[RUNTIME]: {h}h {m}m {s:.2f}s")
                # (Printed once per prompt)

            except Exception as e:
                # --- Update database with failed result ---
                error_msg = f"{type(e).__name__}: {str(e)}"
                print(f"[ERROR] Prompt {current_prompt_id} failed: {error_msg}")
                try:
                    update_prompt_result(
                        prompt_id=current_prompt_id,
                        status="failed",
                        error_text=error_msg,
                        engine_account=engine_account,
                    )
                    print(f"[DB] Marked prompt {current_prompt_id} as failed")
                except Exception as db_err:
                    print(f"[DB] Failed to mark prompt {current_prompt_id} as failed: {db_err}")
                needs_restart = True
                restart_reason = f"exception prompt_id={current_prompt_id} err={error_msg}"
                break

        # print(f"Runtime: {h}h {m}m {s:.2f}s")

        # --- Save all captured results ---
        if _all_results:
            results_file = Path("screenshots/pw_capture_results.json")
            results_file.parent.mkdir(parents=True, exist_ok=True)
            results_file.write_text(
                json.dumps(_all_results, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"\n[PW] Results saved: {results_file.resolve()}")
            print(f"[PW] Total captured: {len(_all_results)}/{total_claimed} prompts")
        else:
            print("\n[PW] WARNING: No responses were captured")

        if needs_restart:
            print(f"[BROWSER] Restart requested: {restart_reason}")
            save_ss(sb)
            return RUN_RESULT_RESTART
        return RUN_RESULT_DONE


if __name__ == "__main__":
    max_browser_restarts = int(os.getenv("MAX_BROWSER_RESTARTS", "10"))
    restart_count = 0
    while restart_count < max_browser_restarts:
        print(f"\n{'='*60}")
        print(f"[BROWSER] Starting instance #{restart_count + 1}/{max_browser_restarts}")
        print(f"{'='*60}\n")

        result = None
        try:
            with SB(
                uc=True,                  # Undetected Chromedriver - patches chromedriver binary
                uc_cdp_events=True,       # Use CDP events instead of Selenium wire protocol
                uc_subprocess=True,       # Run chromedriver as subprocess (harder to fingerprint)
                test=True,
                incognito=True,           # Clean session, no leftover cookies/history
                locale="en",
                proxy=proxy,
                chromium_arg="--disable-blink-features=AutomationControlled",  # Remove automation flag
            ) as sb:
                result = create_chatgpt_account(sb)
                print(f"[RESULT] {result}")
        except KeyboardInterrupt:
            print("\n[BROWSER] Interrupted by user. Exiting.")
            break
        except Exception as e:
            # Errors starting SeleniumBase/Chrome itself should also trigger restart attempts.
            print(f"[BROWSER] Fatal error starting/running SB: {type(e).__name__}: {e}")
            result = RUN_RESULT_RESTART

        # SeleniumBase's SB context manager can suppress exceptions inside the `with` block.
        # If create_chatgpt_account() never returned due to an exception, `result` remains None.
        if result is None:
            result = RUN_RESULT_RESTART

        if result == RUN_RESULT_RESTART:
            restart_count += 1
            print(f"\n{'='*60}")
            print(f"[BROWSER] Restarting browser instance ({restart_count}/{max_browser_restarts})...")
            print(f"{'='*60}\n")
            if restart_count >= max_browser_restarts:
                print(f"[BROWSER] Max restarts ({max_browser_restarts}) reached. Exiting.")
                break
            time.sleep(3)
            continue

        if result == RUN_RESULT_NO_PROMPTS:
            print("[BROWSER] No prompts claimed. Exiting.")
            break

        if result == RUN_RESULT_DONE:
            print("\n[BROWSER] All prompts processed successfully")
            break

        print(f"[BROWSER] Unknown result: {result!r}. Treating as restart.")
        restart_count += 1
        time.sleep(3)
        continue

    print(f"\n[BROWSER] Session ended. Total restarts: {restart_count}")
