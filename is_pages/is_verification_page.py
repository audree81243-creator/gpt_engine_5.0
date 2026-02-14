import json
import os
import re
import random
import time
from contextlib import suppress
from seleniumbase import SB
from utils import *
from utils import _get_cdp


VERIFICATION_PAGE_SELECTORS = [
    'h1:contains("Check your inbox")',
    'text="Check your inbox"',
    'input[name="code"]',
    'input[autocomplete="one-time-code"]',
    'input[id*="code"]',
    'input[placeholder*="Code" i]',
    'button:contains("Resend email")',
]

def is_verification_page_visible(sb, timeout=12, screenshot_name="verification_code_page"):
    cdp = _get_cdp(sb)
    t0 = time.time()
    while time.time() - t0 < timeout:
        for sel in VERIFICATION_PAGE_SELECTORS:
            try:
                if cdp.is_element_visible(sel):
                    print("[2FA] Email verification page detected")
                    save_ss(sb, screenshot_name)
                    return True
            except Exception:
                pass
        sb.sleep(random.uniform(0.2, 0.5))
    return False
