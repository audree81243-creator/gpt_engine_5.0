import os
import random
import secrets
import string
import time
from contextlib import suppress
from seleniumbase import SB
import inspect
ss_number=1
debug_number=1

# Sleep command with debug statement.
# Supports either a random sleep between [a, b] or a fixed sleep via secs=.
def sleep_dbg(sb, a=None, b=None, label="", *, secs=None):
    if secs is None:
        if a is None or b is None:
            raise TypeError("sleep_dbg() requires either secs=... or both a and b")
        # Preserve the old behavior (int seconds) unless floats are passed.
        if isinstance(a, float) or isinstance(b, float):
            secs = random.uniform(float(a), float(b))
        else:
            secs = random.randint(int(a), int(b))

    secs = float(secs)
    print(f"---{secs:.1f}s---")
    # print(f"[SLEEP] {label} sleeping {secs:.1f}s")
    sb.sleep(secs)
    return secs

#short sleep command with debug statement
def short_sleep_dbg(sb, label=""):
    secs = random.randint(8, 45) / 10.0  # 0.8–1.5s
    print(f"---{secs:.1f}s---")
    # print(f"[SLEEP] {label} short sleep {secs:.1f}s")
    sb.sleep(secs)
    return secs

#Visibility checking function to check if an element is present in "sb" instance
def visible(sb, sel):
    try:
        return sb.cdp.is_element_visible(sel)
    except Exception:
        return False

#Best-effort helpers to avoid hard crashes when elements vanish mid-flow
def _safe_label(value):
    label = (value or "element").strip()
    label = label.replace(" ", "_").replace("/", "_").replace("\\", "_").replace(":", "_")
    return label[:50] if label else "element"

def safe_wait_visible(sb, selector, timeout=10, label=""):
    cdp = _get_cdp(sb)
    try:
        if hasattr(cdp, "wait_for_element_visible"):
            cdp.wait_for_element_visible(selector, timeout=timeout)
        else:
            sb.wait_for_element_visible(selector, timeout=timeout)
        return True
    except Exception as e:
        print(f"[WAIT][WARN] {label or selector}: {str(e)[:200]}")
        save_ss(sb, f"wait_failed_{_safe_label(label or selector)}")
        return False

def safe_click(sb, selector, label=""):
    cdp = _get_cdp(sb)
    try:
        cdp.click(selector)
        return True
    except Exception as e:
        print(f"[CLICK][WARN] {label or selector}: {str(e)[:200]}")
        save_ss(sb, f"click_failed_{_safe_label(label or selector)}")
        return False

def safe_type(sb, selector, text, label=""):
    cdp = _get_cdp(sb)
    try:
        cdp.type(selector, text)
        return True
    except Exception as e:
        print(f"[TYPE][WARN] {label or selector}: {str(e)[:200]}")
        save_ss(sb, f"type_failed_{_safe_label(label or selector)}")
        return False

def safe_send_keys(sb, selector, keys, label=""):
    cdp = _get_cdp(sb)
    try:
        cdp.send_keys(selector, keys)
        return True
    except Exception as e:
        print(f"[SEND_KEYS][WARN] {label or selector}: {str(e)[:200]}")
        save_ss(sb, f"send_keys_failed_{_safe_label(label or selector)}")
        return False

#To click a selector function in present "sb" instance
def click_first(sb, selectors, label="", raise_on_fail=False):
    saw_visible = False
    last_exc = None
    for sel in selectors:
        try:
            if sb.cdp.is_element_visible(sel):
                saw_visible = True
                print("Element now visible to click!")
                try:
                    sb.cdp.click(sel)
                    short_sleep_dbg(sb, label=f"after click {label or sel}")
                    print(f"[CLICK BUTTON] {label or sel}")
                    return sel
                except Exception as e:
                    last_exc = e
        except Exception as e:
            last_exc = e
    if raise_on_fail and saw_visible and last_exc:
        raise RuntimeError(f"click_first failed for {label or selectors}") from last_exc
    return None

#Screenshot taking function
def save_ss(sb, name=None, step=None):
    """
    Save a screenshot with a numeric prefix.

    - If `step` is provided, that number is used (zero-padded).
    - Otherwise a module-level counter is incremented.
    """
    global ss_number
    os.makedirs("screenshots", exist_ok=True)
    step_num = int(step) if step is not None else ss_number
    filename = f"{step_num:03d}_{name}_{int(time.time())}.png"
    path = f"screenshots/{filename}"
    with suppress(Exception):
        sb.save_screenshot(path)
        print(f"SCREENSHOT {step_num}")
        if step is None:
            ss_number += 1
    return path

def debug():
    global debug_number
    
    # Get the calling file and line number
    caller_frame = inspect.currentframe().f_back
    caller_file = os.path.basename(caller_frame.f_code.co_filename)
    caller_line = caller_frame.f_lineno
    
    print(f"____________{caller_file}______________{caller_line}___________________{debug_number}")
    debug_number += 1

def _env_int(name, default):
    try:
        v = os.environ.get(name, "")
        return int(v) if str(v).strip() else default
    except Exception:
        return default
    

def wait_for_textarea(sb, timeout=40):
    TEXTAREA_SELECTORS = [
    "#prompt-textarea",
    "textarea#prompt-textarea",
    'textarea[placeholder*="Message" i]',
    "/html/body/div[1]/div[1]/div/div[2]/div/main/div/div/div[2]/div[1]/div/div/div[2]/form/div[2]/div/div[1]/div/div",
    "/html/body/div[1]/div[1]/div/div[2]/div/main/div/div/div[2]/div[1]/div/div/div[2]/form/div[2]/div/div[1]/div/div/p",
    "/html/body/div[1]/div[1]/div/div[2]/div/main/div/div/div[2]/div[1]/div/div/div[2]/form/div[2]/div/div[1]",
]
    t0 = time.time()
    while time.time() - t0 < timeout:
        for sel in TEXTAREA_SELECTORS:
            if visible(sb, sel):
                return sel
        sb.sleep(0.5)
    return None

def _get_cdp(sb):
    return sb.cdp if getattr(sb, "cdp", None) else sb

def _input_has_value(sb, selector, min_len=1):
    val = None
    with suppress(Exception):
        val = sb.get_attribute(selector, "value")
    if not val:
        with suppress(Exception):
            val = sb.cdp.get_attribute(selector, "value")
    if not val:
        with suppress(Exception):
            val = sb.execute_script(
                "const el = document.querySelector(arguments[0]); return el && (el.value || el.textContent);",
                selector,
            )
    try:
        return len(str(val).strip()) >= min_len
    except Exception:
        return False

def _tag_birthday_input(sb):
    script = """
    const spin = Array.from(document.querySelectorAll('div[role="spinbutton"][contenteditable="true"]'));
    for (const el of spin) {
      const aria = (el.getAttribute('aria-label') || '').toLowerCase();
      if (aria.startsWith('month')) el.setAttribute('data-type', 'month');
      if (aria.startsWith('day')) el.setAttribute('data-type', 'day');
      if (aria.startsWith('year')) el.setAttribute('data-type', 'year');
    }
    const inputs = Array.from(document.querySelectorAll('input'));
    for (const el of inputs) {
      const hint = ((el.getAttribute('aria-label') || '') + ' ' + (el.getAttribute('placeholder') || '')).toLowerCase();
      if (hint.includes('birth')) el.setAttribute('data-codex-bday', '1');
    }
    """
    with suppress(Exception):
        sb.execute_script(script)

def _fill_birthday_segmented(sb, value):
    """
    Directly target the segmented birthday control (month/day/year contenteditable spinbuttons).
    """
    _tag_birthday_input(sb)
    cdp = _get_cdp(sb)

    try:
        m, d, y = value.split("/")
    except Exception:
        return False

    month_sel = 'div[role="spinbutton"][contenteditable="true"][data-type="month"], div[role="spinbutton"][contenteditable="true"][aria-label^="month"]'
    day_sel = 'div[role="spinbutton"][contenteditable="true"][data-type="day"], div[role="spinbutton"][contenteditable="true"][aria-label^="day"]'
    year_sel = 'div[role="spinbutton"][contenteditable="true"][data-type="year"], div[role="spinbutton"][contenteditable="true"][aria-label^="year"]'
    hidden_input_sel = 'input[name="birthday"]'

    def _type_seq(sel, txt):
        try:
            with suppress(Exception):
                sb.scroll_to(sel)
            cdp.click(sel)
            time.sleep(0.2)
            for ch in txt:
                cdp.type(sel, ch)
                time.sleep(0.05)
            return True
        except Exception:
            return False

    ok_month = _type_seq(month_sel, m)
    ok_day = _type_seq(day_sel, d)
    ok_year = _type_seq(year_sel, y)
    with suppress(Exception):
        cdp.type(year_sel, "\n")

    if not (ok_month and ok_day and ok_year):
        with suppress(Exception):
            sb.execute_script(
                """
                const [m,d,y] = arguments;
                const segs = {
                    month: document.querySelector('div[role="spinbutton"][contenteditable="true"][data-type="month"]')
                        || document.querySelector('div[role="spinbutton"][contenteditable="true"][aria-label^="month"]'),
                    day: document.querySelector('div[role="spinbutton"][contenteditable="true"][data-type="day"]')
                        || document.querySelector('div[role="spinbutton"][contenteditable="true"][aria-label^="day"]'),
                    year: document.querySelector('div[role="spinbutton"][contenteditable="true"][data-type="year"]')
                        || document.querySelector('div[role="spinbutton"][contenteditable="true"][aria-label^="year"]'),
                };
                for (const [k, el] of Object.entries(segs)) {
                    if (el) {
                        el.textContent = ({month:m, day:d, year:y})[k];
                        el.dispatchEvent(new Event('input', { bubbles: true }));
                        el.dispatchEvent(new Event('change', { bubbles: true }));
                    }
                }
                const hidden = document.querySelector('input[name="birthday"]');
                if (hidden) {
                    hidden.value = `${m}/${d}/${y}`;
                    hidden.dispatchEvent(new Event('input', { bubbles: true }));
                    hidden.dispatchEvent(new Event('change', { bubbles: true }));
                }
                """,
                m,
                d,
                y,
            )

    try:
        val = sb.get_attribute(hidden_input_sel, "value")
        if val and all(part in val for part in (m, d, y)):
            return True
    except Exception:
        pass

    return ok_month and ok_day and ok_year

def _fill_text_input(sb, selector, value, min_len=1):
    """
    Generic text input filler with multiple strategies.
    """
    cdp = _get_cdp(sb)
    with suppress(Exception):
        sb.wait_for_element_visible(selector, timeout=10)
    with suppress(Exception):
        sb.scroll_to(selector)
    with suppress(Exception):
        cdp.click(selector)
    with suppress(Exception):
        cdp.clear_input(selector)

    with suppress(Exception):
        cdp.type(selector, value)
    if _input_has_value(sb, selector, min_len):
        return True

    with suppress(Exception):
        sb.type(selector, value)
    if _input_has_value(sb, selector, min_len):
        return True

    with suppress(Exception):
        sb.execute_script(
            """
            const el = document.querySelector(arguments[0]);
            if (el) {
                el.value = arguments[1];
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
            }
            """,
            selector,
            value,
        )
    if _input_has_value(sb, selector, min_len):
        return True

    with suppress(Exception):
        cdp.type(selector, value + "\n")
    return _input_has_value(sb, selector, min_len)

def _complete_onboarding(sb, first_names, last_names, snap=None):
    """
    Minimal onboarding: fill name and birthday if prompted, then continue/skip screens.
    """
    name_val = f"{random.choice(first_names)} {random.choice(last_names)}"
    birthday_val = f"{random.randint(1, 12):02d}/{random.randint(1, 28):02d}/{random.randint(1991, 2001)}"
    name_selectors = [
        'input[placeholder="Full name"]',
        'input[aria-label*="full name" i]',
        'input[name*="name" i]',
    ]
    continue_selectors = [
        'button:contains("Continue")',
        'button:contains("Skip")',
        '/html/body/div[4]/div/div/div/div/div/div[2]/button/div',
    ]
    name_filled = False
    for sel in name_selectors:
        if visible(sb, sel):
            name_filled = _fill_text_input(sb, sel, name_val)
            if snap:
                snap("onboarding_name_filled")
            break

    bday_filled = _fill_birthday_segmented(sb, birthday_val)
    if bday_filled and snap:
        snap("onboarding_birthday_filled")
    if not bday_filled:
        bday_selectors = [
            'input[placeholder="Birthday"]',
            'input[aria-label*="Birthday" i]',
            'input[name*="birthday" i]',
            'input[placeholder*="birth" i]',
            'input[aria-label*="birth" i]',
            'input[placeholder="MM/DD/YYYY"]',
            'input[aria-label*="MM/DD" i]',
            'input[type="text"][inputmode="numeric"]',
            'div:contains("Birthday") input[type="text"]',
            '/html/body/div/div/fieldset/form/div[1]/div/div[2]/div/div/div/span/div/div//input',
            'input[data-codex-bday="1"]',
        ]
        _tag_birthday_input(sb)
        for sel in bday_selectors:
            if visible(sb, sel):
                bday_filled = _fill_text_input(sb, sel, birthday_val, min_len=6)
                if bday_filled and snap:
                    snap("onboarding_birthday_filled")
                break

    continue_clicked = bool(click_first(sb, continue_selectors, label="onboarding-continue"))
    time.sleep(25)
    snap("onboarding_continue_button_clicked")
    try:
        from is_pages.is_chat_ui import is_chat_ui_visible
    except Exception:
        is_chat_ui_visible = None
    if is_chat_ui_visible and is_chat_ui_visible(sb):
        print("Success! on account creation, Chat UI is Visible.")
        return True
    for _ in range(3):
        if click_first(sb, continue_selectors, label="onboarding-continue"):
            continue_clicked = True
            time.sleep(1)
    return bool(name_filled and bday_filled and continue_clicked)

def _generate_password(length: int = 15) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    # Ensure at least one of each type for better strength.
    base = [
        secrets.choice(string.ascii_lowercase),
        secrets.choice(string.ascii_uppercase),
        secrets.choice(string.digits),
        secrets.choice("!@#$%^&*"),
    ]
    if length < len(base):
        length = len(base)
    remaining = length - len(base)
    base.extend(secrets.choice(alphabet) for _ in range(remaining))
    # Shuffle without leaking order; secrets.choice above is already random.
    secrets.SystemRandom().shuffle(base)
    return "".join(base)
