import os
import time
from contextlib import suppress

from seleniumbase import SB

import capture_conversation_simple as cap


def _wait_for_composer(sb, timeout=45):
    script = r"""
(() => {
  const el = document.querySelector('#prompt-textarea');
  if (!el) return false;
  return Boolean(el.getClientRects && el.getClientRects().length);
})();
"""
    t0 = time.time()
    while time.time() - t0 < timeout:
        with suppress(Exception):
            if bool(sb.execute_script(script)):
                return True
        sb.sleep(0.4)
    return False


def main():
    base_url = os.getenv(
        "CHATGPT_BASE_URL",
        "https://chatgpt.com/?temporary-chat=true&hints=search",
    )
    prompt = (os.getenv("CHATGPT_PROMPT", "") or "").strip() or "latest NVIDIA earnings"

    proxy = (os.getenv("CHATGPT_PROXY", "") or "").strip() or None
    use_incognito = os.getenv("CHATGPT_INCOGNITO", "0") == "1"
    user_data_dir = (os.getenv("CHATGPT_USER_DATA_DIR", "") or "").strip() or None
    request_timeout = int(os.getenv("CONVERSATION_REQUEST_TIMEOUT", "180"))
    capture_timeout = int(os.getenv("CONVERSATION_CAPTURE_TIMEOUT", "420"))
    idle_secs = int(os.getenv("CONVERSATION_STREAM_IDLE_SECS", "20"))

    with SB(
        uc=True,
        uc_cdp_events=True,
        test=True,
        locale="en",
        incognito=use_incognito,
        user_data_dir=user_data_dir,
        proxy=proxy,
    ) as sb:
        print("Systems green, standing by.")
        print(
            f"[CONFIG] incognito={use_incognito} "
            f"user_data_dir={user_data_dir or '<none>'} proxy={'set' if proxy else 'none'}"
        )
        print(f"[CONFIG] BASE_URL={base_url}")
        print(f"[CONFIG] PROMPT={prompt}")

        sb.activate_cdp_mode("about:blank")
        cap._start_capture(sb)
        if os.getenv("CLEAR_CAPTURE_OUTPUTS", "1") == "1":
            cap._clear_previous_outputs()
            print("[CONFIG] Cleared previous capture outputs")

        open_ts = time.time()
        sb.cdp.open(base_url)
        sb.sleep(6)
        cap._close_popups(sb)
        cap._dismiss_guest_gate(sb, retries=2)

        if cap._is_login_wall(sb):
            print(
                "[ERROR] Login wall detected before prompt submit. "
                "Cannot trigger /f/conversation in this state."
            )
            sb.save_screenshot("screenshots/login_wall_stepwise.png")
            return

        if not _wait_for_composer(sb, timeout=40):
            print("[ERROR] Composer did not become ready in time.")
            sb.save_screenshot("screenshots/composer_not_ready_stepwise.png")
            return

        request_id = None
        for attempt in range(1, 4):
            baseline = cap._current_main_ids()
            submit_result = cap._composer_submit(sb, prompt)
            print(f"[CONVERSATION] Submit result (attempt {attempt}): {submit_result}")
            request_id = cap._wait_for_new_main_request(baseline, timeout=request_timeout)
            if request_id:
                break
            cap._close_popups(sb)
            cap._dismiss_guest_gate(sb, retries=2)

        if not request_id:
            request_id = cap._latest_main_request_id(after_ts=open_ts)
        if not request_id:
            request_id = cap._find_sse_request_id_from_bodies(after_ts=open_ts)

        if not request_id:
            print("[ERROR] Could not detect /backend-*/f/conversation request.")
            sb.save_screenshot("screenshots/no_f_conversation_stepwise.png")
            return

        print(f"[CONVERSATION] Main request detected: {request_id} url={cap._request_url.get(request_id)}")
        cap._force_stream_enable(request_id, timeout=60)
        done = cap._wait_for_capture_done(
            request_id,
            timeout=capture_timeout,
            idle_secs=idle_secs,
        )
        if done:
            print(f"[CONVERSATION] Capture finished for request {request_id}")
        else:
            print(f"[WARN] Capture timed out for request {request_id}")

        summary = cap._build_summary(request_id, prompt)
        print(f"[RESULT] Answer chars={len(summary.get('answer_text') or '')}")
        print(f"[RESULT] Citations={len(summary.get('citations') or [])}")
        sb.save_screenshot("screenshots/final_capture_state_stepwise.png")


if __name__ == "__main__":
    main()
