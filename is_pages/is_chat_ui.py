import json
import os
import re
import random
import time
from contextlib import suppress
from seleniumbase import SB
from utils import *
from utils import _get_cdp
from .is_pop_ups import *

def _is_signin_error_page_visible(sb, screenshot_name="signin_error_detected"):
    """
    Detect common "Oops!/Go back" sign-in error overlays that block the chat UI.
    """
    cdp = _get_cdp(sb)
    error_phrases = [
        "oops!",
        "we ran into an issue while signing you in",
        "please take a break and try again soon",
        "go back",
        "Try again",
    ]
    selectors = [
        'h1:contains("Oops")',
        'p:contains("We ran into an issue while signing you in")',
        'button:contains("Go back")',
    ]
    # Check page source text first (fast and resilient).
    with suppress(Exception):
        html = cdp.get_page_source().lower()
        for phrase in error_phrases:
            if phrase in html:
                print(f"[CHAT-UI] Sign-in error text detected: {phrase}")
                save_ss(sb, screenshot_name)
                return True
    # Fall back to visible selectors.
    for sel in selectors:
        with suppress(Exception):
            if cdp.is_element_visible(sel):
                print(f"[CHAT-UI] Sign-in error visible via selector: {sel}")
                save_ss(sb, screenshot_name)
                return True
    return False

def is_chat_ui_visible(sb):
    # Try current page first
    cdp = _get_cdp(sb)
    popups=is_popups_visible(sb)
    if _is_signin_error_page_visible(sb):
        return False
    sel = wait_for_textarea(sb, timeout=12)
    if sel!=None:
        print(f"[CHAT-UI] Textarea found ({sel}) without redirect")
        return True

    save_ss(sb, "login_after_password_failed")
    return False
