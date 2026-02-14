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

proxy = "versedoin_Xdhcu:AyJ+cQ0Xi7fnTx@pr.oxylabs.io:7777"
t0 = time.perf_counter()


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
    print("⚡ Systems green, standing by.")
    print("\n")

    url = "https://chatgpt.com/auth/login_with"
    # https://auth.openai.com/log-in-or-create-account
    # https://chatgpt.com/?temporary-chat=true&hints=search&q
    # https://chatgpt.com/auth/login_with
    # https://chatgpt.com/auth/login

    # sb.uc_open_with_reconnect(url, 4)
    sb.activate_cdp_mode("about:blank")
    sb.cdp.open(url)
    print("\n" * 3)
    save_ss(sb)
    print("### [START] OPEN SIGN UP BUTTON PART ###")
    
    # Wait for page to fully load (Log in button = page ready)
    sb.assert_element_visible('button:contains("Log in")', timeout=120)
    save_ss(sb)
    login_button = '/html/body/div[1]/div[1]/div[2]/div[1]/div/div/button[1]'
    sb.click(login_button)
    # Dismiss cookie banner via JS (sb.click doesn't work reliably in CDP)
    sb.sleep(1)
    sb.click(login_button)
    sb.click(login_button)
    js_click_by_text(sb, "Accept all")
    save_ss(sb)
    print("[DISMISSED] Cookie banner")

    # Click Log in - this navigates to auth0.openai.com (cross-domain)
    # CDP connection drops on cross-domain nav, need reconnect after
    
    print("[CLICKED] Log in")
    sb.reconnect(8)  # Wait for cross-domain navigation + reconnect CDP
    save_ss(sb)
    print("### [END] OPEN SIGN UP BUTTON PART ###")

# --------------------------------------------------------------------

    result = create_email()
    new_email = result.get("email", {}).get("address")
    print(new_email)

# --------------------------------------------------------------------

    print("\n" * 3)
    print("### [START] EMAIL INPUT PART ###")
    save_ss(sb)

    # Try multiple selectors with assert (auto-waits)
    email_selectors = [
        'input#email',
        'input[name="email"]',
        'input[type="email"]',
        'input[autocomplete="username"]',
        '/html/body/div/div/fieldset/form/div[2]/div/div/div/label/div/div',  # fallback
    ]

    email_input_field = None
    for sel in email_selectors:
        try:
            sb.assert_element_visible(sel, timeout=5)
            email_input_field = sel
            break
        except:
            continue

    if not email_input_field:
        raise RuntimeError("Email input not found")

    sb.type(email_input_field, new_email)  # type() is stealthy
    print(f"[FILLED] Email via: {email_input_field}")
    save_ss(sb)
    print("### [END] EMAIL INPUT PART ###")

# --------------------------------------------------------------------

    print("\n" * 3)
    print("### [START] CONTINUE BUTTON AFTER EMAIL PART ###")
    save_ss(sb)
    continue_btn_css = 'fieldset form button[type="submit"]'
    sb.assert_element_visible(continue_btn_css, timeout=120)
    js_click_by_text(sb, "Continue")
    print("[CLICKED BUTTON] Continue")
    sb.reconnect(8)  # Page reloads after email submit
    save_ss(sb)
    print("### [END] CONTINUE BUTTON PART ###")

# --------------------------------------------------------------------

    print("\n" * 3)
    print("### [START] PASSWORD FILLING PART ###")
    save_ss(sb)
    password_css = 'input[type="password"]'
    sb.assert_element_visible(password_css, timeout=120)
    sb.type(password_css, "Katana@230331")
    save_ss(sb)
    print("### [END] PASSWORD FILLING PART ###")

# --------------------------------------------------------------------

    print("\n" * 3)
    print("### [START] CONTINUE BUTTON AFTER PASSWORD PART ###")
    save_ss(sb)
    continue_after_pw_css = 'fieldset form button[type="submit"]'
    sb.assert_element_visible(continue_after_pw_css, timeout=120)
    js_click_by_text(sb, "Continue")
    print("[CLICKED BUTTON] Continue")
    sb.reconnect(8)  # Page reloads after password submit
    save_ss(sb)
    print("### [END] CONTINUE BUTTON PART ###")

# --------------------------------------------------------------------

    print("\n" * 3)
    print("### [START] CHECKING VERIFICATION/PASSWORD PART ###")
    verfication_page_appeared=is_verification_page_visible(sb)
    print(f"verification page appeared : {verfication_page_appeared}")

    verification_code_css = 'fieldset form input[type="text"]'
    if verfication_page_appeared==True:
        code = fetch_chatgpt_code_from_boomlify_separate(new_email)
        sb.assert_element_visible(verification_code_css, timeout=120)
        sb.type(verification_code_css, code)
        save_ss(sb)
    else:
        sb.assert_element_visible('button:contains("one-time")', timeout=120)
        js_click_by_text(sb, "one-time")
        sb.reconnect(8)
        code = fetch_chatgpt_code_from_boomlify_separate(new_email)
        sb.assert_element_visible(verification_code_css, timeout=120)
        sb.type(verification_code_css, code)
        save_ss(sb)

    continue_verify_css = 'fieldset form button[type="submit"]'
    sb.assert_element_visible(continue_verify_css, timeout=120)
    js_click_by_text(sb, "Continue")
    print("[CLICKED BUTTON] Continue")
    sb.reconnect(8)  # Page reloads after verification
    save_ss(sb)
    print("### [END] CHECKING VERIFICATION/PASSWORD PART ###")
    
# --------------------------------------------------------------------

    print("\n" * 3)
    name_val = f"{random.choice(first_names)} {random.choice(last_names)}"
    birthday_val = f"{random.randint(1, 12):02d}/{random.randint(1, 28):02d}/{random.randint(1991, 2001)}"

    print("### [START] FULL NAME and BIRTHDAY PART ###")
    save_ss(sb)
    full_name_input_field = 'input[placeholder="Full name"]'
    sb.assert_element_visible(full_name_input_field, timeout=120)
    sb.type(full_name_input_field, name_val)
    print("[FILLED] Full name")
    save_ss(sb)
    birthday_filled = fill_birthday(sb, birthday_val)
    if not birthday_filled:
        save_ss(sb)
        raise RuntimeError("Birthday fill failed")
    print("[FILLED] Birthday")
    save_ss(sb)
    print("### [END] FULL NAME and BIRTHDAY PART ###")


# --------------------------------------------------------------------

    print("\n" * 3)
    print("### [START] CONTINUE BUTTON AFTER FULLNAME AND BIRTHDAY PART ###")
    save_ss(sb)
    continue_name_css = 'fieldset form button[type="submit"]'
    sb.assert_element_visible(continue_name_css, timeout=120)
    js_click_by_text(sb, "Continue")
    print("[CLICKED BUTTON] Continue")
    sb.reconnect(8)  # Final navigation back to chatgpt.com
    save_ss(sb)
    print("### [END] CONTINUE BUTTON AFTER FULLNAME AND BIRTHDAY PART ###")

# --------------------------------------------------------------------

    popups_appeared=is_popups_visible(sb)
    print("\n")
    print(f"Popups appeared TRY 1: {popups_appeared}")
    print("\n")
    popups_appeared=is_popups_visible(sb)
    print("\n")
    print(f"Popups appeared TRY 2: {popups_appeared}")
    print("\n")
    popups_appeared=is_popups_visible(sb)
    print("\n")
    print(f"Popups appeared TRY 3 : {popups_appeared}")
    print("\n")
    popups_appeared=is_popups_visible(sb)
    print("\n")
    print(f"Popups appeared TRY 4 : {popups_appeared}")
    print("\n")

# --------------------------------------------------------------------

    if is_chat_ui_visible(sb)==True:
        dt = time.perf_counter() - t0
        h = int(dt // 3600)
        m = int((dt % 3600) // 60)
        s = dt % 60
        print(f"Runtime: {h}h {m}m {s:.2f}s")
        return True
    
    dt = time.perf_counter() - t0
    h = int(dt // 3600)
    m = int((dt % 3600) // 60)
    s = dt % 60
    print(f"Runtime: {h}h {m}m {s:.2f}s")
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
