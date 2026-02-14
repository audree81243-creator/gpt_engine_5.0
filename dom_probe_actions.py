from seleniumbase import SB
import json


def snapshot(sb, label):
    data = sb.execute_script(
        """
(() => {
const out = {};
out.url = location.href;
out.prompt = {};
const el = document.querySelector('#prompt-textarea');
if (el) {
  out.prompt.exists = true;
  out.prompt.visible = !!(el.getClientRects && el.getClientRects().length);
  out.prompt.isContentEditable = !!el.isContentEditable;
  out.prompt.text = ((el.innerText || el.textContent || el.value || '').trim()).slice(0, 200);
} else {
  out.prompt.exists = false;
}
out.buttons = [];
for (const b of Array.from(document.querySelectorAll('button')).slice(0, 1200)) {
  const testid = b.getAttribute('data-testid') || '';
  const aria = b.getAttribute('aria-label') || '';
  const txt = (b.innerText || b.textContent || '').trim();
  if (
    testid.includes('send') ||
    testid.includes('composer') ||
    /send|submit|voice|search|study|create|stop/i.test(testid + ' ' + aria + ' ' + txt)
  ) {
    out.buttons.push({
      testid,
      aria,
      txt: txt.slice(0, 80),
      disabled: !!b.disabled,
      visible: !!(b.getClientRects && b.getClientRects().length),
    });
  }
}
return out;
})();
"""
    )
    data["label"] = label
    print(json.dumps(data, ensure_ascii=False, indent=2))


def main():
    url = "https://chatgpt.com/?temporary-chat=true&hints=search"
    prompt = "latest NVIDIA earnings"
    with SB(uc=True, uc_cdp_events=True, test=True, incognito=True, locale="en") as sb:
        sb.activate_cdp_mode("about:blank")
        sb.cdp.open(url)
        sb.sleep(10)
        snapshot(sb, "initial")

        try:
            sb.cdp.type("#prompt-textarea", prompt)
        except Exception:
            pass
        sb.sleep(1)
        snapshot(sb, "after_cdp_type")

        try:
            sb.press_keys("#prompt-textarea", "\n")
        except Exception:
            pass
        sb.sleep(3)
        snapshot(sb, "after_press_enter")


if __name__ == "__main__":
    main()
