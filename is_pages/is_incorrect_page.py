import json
import os
import re
import random
import time
from contextlib import suppress
from seleniumbase import SB
from utils import *
from utils import _get_cdp

# Check wether the present page in given "sb" instance is an error page
def is_incorrect_credentials_page_visible(sb, timeout=5, screenshot_name="incorrect_credentials_detected"):
    """
    Detects if the current page is showing an "Incorrect email address or password" error.
    Returns True if found, False otherwise.
    Screenshots if detected.
    """
    cdp = _get_cdp(sb)
    sb.sleep(5)
    ERROR_TEXTS = [
        "incorrect email address or password",
        "invalid credentials",
        "email or password is incorrect",
        "invalid email or password",
    ]
    selectors = [
        "li:contains('Incorrect email address or password')",
        "span:contains('Incorrect email address or password')",
        "div[role='alert']",
        "div[data-error]",
        "p:contains('Incorrect')",
        "div:contains('invalid credentials')",
    ]
    # Try direct text search first
    try:
        html = cdp.get_page_source()
        for phrase in ERROR_TEXTS:
            if phrase.lower() in html.lower():
                print("(is_incorrect_credentials_page_visible) Found error text:", phrase)
                save_ss(sb, screenshot_name)
                return True
    except Exception:
        pass
    # Try selectors/visible UI
    from contextlib import suppress
    for sel in selectors:
        with suppress(Exception):
            if cdp.is_element_visible(sel):
                print(f"(is_incorrect_credentials_page_visible) Error visible via selector: {sel}")
                save_ss(sb, screenshot_name)
                return True
    return False
