from main import *
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

proxy = "versedoin_Xdhcu:AyJ+cQ0Xi7fnTx@pr.oxylabs.io:7777"
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
    t0 = time.perf_counter()  # Start timer
    print("âš¡ Systems green, standing by.")
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
        
        
        # activate_search_mode(sb)

        search_prompts = [
  "What is the live price of Bitcoin in USD?",
  "What are the top breaking news headlines today?",
  "Current weather in Tokyo right now?",
  "Who won the most recent Super Bowl?",
  "Stock price of NVIDIA (NVDA) at this moment?",
  "Latest updates on the SpaceX Starship program?",
  "What is the current exchange rate for 1 Euro to USD?",
  "Who is currently at the top of the Premier League table?",
  "What movies are playing in theaters near New York right now?",
  "Price of Gold per ounce today live?",
  "Latest earthquake reports worldwide in the last 24 hours?",
  "When is the next solar eclipse visible?",
  "Current trending topics on Twitter/X right now?",
  "Who won the latest Grammy for Album of the Year?",
  "What is the current world population live count?",
  "What is the current inflation rate in the United States?",
  "Who is leading in the latest presidential election polls?",
  "Current price of Ethereum in USD?",
  "What are the top trending videos on YouTube right now?",
  "Latest score of the Lakers game tonight?",
  "What is the current gas price average in California?",
  "Who just won the Formula 1 race this weekend?",
  "Current air quality index (AQI) in Delhi?",
  "What is the latest iPhone model released by Apple?",
  "Today's Wordle answer and hints?",
  "What are the current mortgage rates in the US?",
  "Who is the current number 1 ranked tennis player in the world?",
  "Latest news about the conflict in Ukraine?",
  "What is the current price of Tesla stock (TSLA)?",
  "Which TV shows are trending on Netflix today?"
]


        prompt_number=1
        
        # Iterate through the prompts
        cdp = _get_cdp(sb)
        for prompt in search_prompts:
            for sel in LIST_OF_POPUPS: 
                # visible() and click() are SeleniumBase builtins
                if cdp.is_element_visible(sel):
                    cdp.click(sel)
                    save_ss(sb,"Pop up closed")
                    print(f"::warning::[POP UP] Closed popup/button using selector: {sel}")
                    # sb.save_screenshot(screenshot_name)
                    sb.sleep(random.uniform(2, 4))  # Pause after closing to allow follow-up popups
                    break
            print(f"entering prompt: {prompt}")
            if prompt_number%10==0:
                _pw_responses.clear()  # Discard stale responses before refresh
                sb.refresh_page()
                print("[PAGE REFRESHED]")
                sleep_dbg(sb,3,10)
                save_ss(sb)

            print(prompt_number)
            prompt_number=prompt_number+1
            enter_prompt(sb, prompt)
            
            # Wait for the response/search to complete before the next one
            # Adjust timing based on how long 'enter_prompt' waits intenally
            sleep_dbg(sb, 5, 8)

            # --- Collect SSE response via Playwright ---
            # Flush Playwright's pending event queue so response handlers fire.
            try:
                page.evaluate("void(0)")
            except Exception:
                pass

            for resp in list(_pw_responses):
                try:
                    # .body() blocks until the full SSE stream is done, then
                    # returns the complete response body (all event: / data: lines).
                    body = resp.body()
                    text = body.decode('utf-8', errors='replace')
                    if text and len(text) > 50:
                        answer, citations = _extract_answer_and_citations_from_sse(text)
                        _all_results.append({
                            'prompt_number': prompt_number - 1,
                            'prompt': prompt,
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

            dt = time.perf_counter() - t0
            h = int(dt // 3600)
            m = int((dt % 3600) // 60)
            s = dt % 60
            print(f"Runtime: {h}h {m}m {s:.2f}s")
            # (Printed once per prompt)

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
            print(f"[PW] Total captured: {len(_all_results)}/{len(search_prompts)} prompts")
        else:
            print("\n[PW] WARNING: No responses were captured")

        return False


if __name__ == "__main__":
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