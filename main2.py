from main import *
from contextlib import suppress
import json
import os
import re
import time
from urllib.parse import urlsplit, urlunsplit
from seleniumbase import SB
from utils import sleep_dbg
from utils import save_ss
from is_pages.is_verification_page import *
from is_pages.is_pop_ups import *
from is_pages.is_chat_ui import *
from boomlify_codes import *
from activate_search_mode import *
from birthday_helpers import fill_birthday
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

proxy = "versedoin_Xdhcu:AyJ+cQ0Xi7fnTx@pr.oxylabs.io:7777"

if __name__ == "__main__":

    
    # Set to None to disable filtering, or pass a JSON-compatible dict to filter.
    website_filter = {"url": "https://polygon.technology/", "location": "US"}
    
    with SB(uc=True, test=True, incognito=True, locale="en", proxy=proxy) as sb:
        print("⚡ Systems green, standing by.")
        print("\n")

        url = "https://chatgpt.com/?temporary-chat=true"
        
        # sb.uc_open_with_reconnect(url, 4)
        sb.activate_cdp_mode(url)
        save_ss(sb)
        sleep_dbg(sb, 12,25)
        
        print("\n" * 3)
        apply_bandwidth_saver(sb)
        activate_search_mode(sb)
        for i in range(20):
            picked_prompts = pick_prompt(website_filter=website_filter)
            if not picked_prompts:
                print("[PROMPTS] Queue is empty. Stopping loop.")
                break
            picked_prompt = picked_prompts[0]
            print("\n")
            print("###  PROMPT NUMBER:  ###")
            print(i+1)
            print("\n")
            print(picked_prompt["id"])
            enter_prompt(sb, picked_prompt["prompt_text"])
            sleep_dbg(sb, 2, 5)
            response_text = get_response_text(sb)
            links = get_sources_links(sb)
            response_error = get_response_error(sb)
            print(response_text)
            print("\n" * 2)
            print(links)
            
            save_ss(sb)
            response_text_clean = str(response_text or "").strip()
            status = "completed"
            error_text = None
            if response_error:
                status = "failed"
                error_text = response_error
            elif len(response_text_clean) < 10:
                status = "failed"
                error_text = (
                    f"Assistant response too short (<10 chars). "
                    f"Length={len(response_text_clean)}"
                )

            update_result = update_prompt_result(
                picked_prompt["id"],
                status,
                response_text=response_text,
                error_text=error_text,
                source_links=links,
                website=picked_prompt.get("website"),
                competitor_websites=picked_prompt.get("competitor_websites"),
                engine_account="github actions",
                skip_if_missing=True,
            )
            print(f"[PROMPTS][UPDATE] {update_result}")

            dt = time.perf_counter() - t0
            h = int(dt // 3600)
            m = int((dt % 3600) // 60)
            s = dt % 60
            print(f"Runtime: {h}h {m}m {s:.2f}s")

            print("\n" * 3)
