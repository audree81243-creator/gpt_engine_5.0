from seleniumbase import SB
import json


def main():
    url = "https://chatgpt.com/?temporary-chat=true&hints=search"
    with SB(uc=True, uc_cdp_events=True, test=True, incognito=True, locale="en") as sb:
        sb.activate_cdp_mode("about:blank")
        sb.cdp.open(url)
        sb.sleep(12)
        data = sb.execute_script(
            """
const out = {};
out.url = location.href;
out.ready = document.readyState;
out.promptTextarea = !!document.querySelector('#prompt-textarea');
out.inputs = [];
for (const el of Array.from(document.querySelectorAll('[contenteditable="true"], textarea, input')).slice(0, 600)) {
  const aria = (el.getAttribute('aria-label') || '');
  const ph = (el.getAttribute('placeholder') || '');
  const id = (el.id || '');
  const role = (el.getAttribute('role') || '');
  const cls = (el.className || '').toString().slice(0, 120);
  const testid = (el.getAttribute('data-testid') || '');
  const keep = id.includes('prompt') || aria.toLowerCase().includes('message') ||
    ph.toLowerCase().includes('message') || testid.includes('prompt') || !!el.isContentEditable;
  if (keep) {
    out.inputs.push({
      tag: el.tagName,
      id,
      aria,
      ph,
      role,
      testid,
      cls,
      isContentEditable: !!el.isContentEditable,
      visible: !!(el.getClientRects && el.getClientRects().length),
      text: ((el.value || el.innerText || el.textContent || '').trim()).slice(0, 120),
    });
  }
}
out.buttons = [];
for (const b of Array.from(document.querySelectorAll('button')).slice(0, 600)) {
  const txt = (b.innerText || b.textContent || '').trim();
  const aria = (b.getAttribute('aria-label') || '');
  const testid = (b.getAttribute('data-testid') || '');
  if (testid || /send|submit|voice|stop|create|study|search|log in|sign up/i.test(aria + ' ' + txt)) {
    out.buttons.push({
      testid,
      aria,
      txt: txt.slice(0, 80),
      disabled: !!b.disabled,
      visible: !!(b.getClientRects && b.getClientRects().length),
      cls: (b.className || '').toString().slice(0, 140),
    });
  }
}
return out;
"""
        )
        print(json.dumps(data, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
