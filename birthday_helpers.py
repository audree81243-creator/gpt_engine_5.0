import time
from contextlib import suppress


def _get_cdp(sb):
    return sb.cdp if getattr(sb, "cdp", None) else sb


def _is_visible(sb, selector):
    cdp = _get_cdp(sb)
    with suppress(Exception):
        if hasattr(cdp, "is_element_visible"):
            return cdp.is_element_visible(selector)
    with suppress(Exception):
        return sb.is_element_visible(selector)
    return False


def _input_has_value(sb, selector, min_len=1):
    val = None
    with suppress(Exception):
        val = sb.get_attribute(selector, "value")
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
      const rac = (el.getAttribute('data-rac-type') || '').toLowerCase();
      if (rac === 'month' || aria.startsWith('month')) el.setAttribute('data-type', 'month');
      if (rac === 'day' || aria.startsWith('day')) el.setAttribute('data-type', 'day');
      if (rac === 'year' || aria.startsWith('year')) el.setAttribute('data-type', 'year');
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
    _tag_birthday_input(sb)
    cdp = _get_cdp(sb)

    try:
        m, d, y = value.split("/")
    except Exception:
        return False

    month_sel = (
        'div[role="spinbutton"][contenteditable="true"][data-rac-type="month"], '
        'div[role="spinbutton"][contenteditable="true"][data-type="month"], '
        'div[role="spinbutton"][contenteditable="true"][aria-label^="month"]'
    )
    day_sel = (
        'div[role="spinbutton"][contenteditable="true"][data-rac-type="day"], '
        'div[role="spinbutton"][contenteditable="true"][data-type="day"], '
        'div[role="spinbutton"][contenteditable="true"][aria-label^="day"]'
    )
    year_sel = (
        'div[role="spinbutton"][contenteditable="true"][data-rac-type="year"], '
        'div[role="spinbutton"][contenteditable="true"][data-type="year"], '
        'div[role="spinbutton"][contenteditable="true"][aria-label^="year"]'
    )
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
                const pick = (...sels) => sels.map(s => document.querySelector(s)).find(Boolean);

                const month = pick(
                  'div[role="spinbutton"][contenteditable="true"][data-rac-type="month"]',
                  'div[role="spinbutton"][contenteditable="true"][data-type="month"]',
                  'div[role="spinbutton"][contenteditable="true"][aria-label^="month"]'
                );
                const day = pick(
                  'div[role="spinbutton"][contenteditable="true"][data-rac-type="day"]',
                  'div[role="spinbutton"][contenteditable="true"][data-type="day"]',
                  'div[role="spinbutton"][contenteditable="true"][aria-label^="day"]'
                );
                const year = pick(
                  'div[role="spinbutton"][contenteditable="true"][data-rac-type="year"]',
                  'div[role="spinbutton"][contenteditable="true"][data-type="year"]',
                  'div[role="spinbutton"][contenteditable="true"][aria-label^="year"]'
                );

                const segs = { month, day, year };
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


def fill_birthday(sb, birthday_val):
    bday_filled = _fill_birthday_segmented(sb, birthday_val)
    if bday_filled:
        return True

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
        'input[data-codex-bday="1"]',
    ]

    _tag_birthday_input(sb)
    for sel in bday_selectors:
        if _is_visible(sb, sel):
            if _fill_text_input(sb, sel, birthday_val, min_len=6):
                return True

    return False
