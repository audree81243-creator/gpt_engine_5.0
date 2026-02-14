import json
import os
import re
import random
import time
from contextlib import suppress
from seleniumbase import SB
from utils import *
from utils import _get_cdp

def is_login_page_visible(sb):
    cdp = _get_cdp(sb)
    indicators = [
        'h1:contains("Log in or sign up")',
        'text="Log in or sign up"',
        'button[data-testid="login-button"]',
        'button[data-testid="log-in-button"]',
        'button:contains("Log in")',
        'div[role="dialog"]',
        'input#email',
        'input[name="email"]',
        'input[type="email"]',
        'input[placeholder="Email address"]',
        'input[aria-label="Email address"]',
    ]
    for sel in indicators:
        with suppress(Exception):
            if cdp.is_element_visible(sel):
                return True
    return False
