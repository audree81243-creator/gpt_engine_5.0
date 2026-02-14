import asyncio
import json
from typing import List, Dict, Any
from playwright.async_api import async_playwright, Page
from seleniumbase import SB

# --- 1. Keep your original parsing function exactly as is ---
def extract_raw_response(input_string: str) -> List[Dict[str, Any]]:
    """Parse ChatGPT's Server-Sent Events stream."""
    json_objects = []
    lines = input_string.split("\n")
    for line in lines:
        if not line.strip() or not line.startswith("data: "):
            continue
        json_str = line[6:].strip()
        if json_str == "[DONE]":
            continue
        try:
            json_obj = json.loads(json_str)
            if isinstance(json_obj, dict):
                json_objects.append(json_obj)
        except json.JSONDecodeError:
            continue
    return json_objects

# --- 2. Modify your Scraper to CONNECT instead of LAUNCH ---
class ChatGPTScraper:
    def __init__(self):
        self.captured_responses = []

    async def setup_page_interceptor(self, page: Page):
        """Set up network request interception (Same as before)."""
        async def handle_response(response):
            if 'backend-api/f/conversation' in response.url:
                try:
                    response_body = await response.text()
                    self.captured_responses.append(response_body)
                except Exception as e:
                    print(f"Error capturing stream: {e}")

        page.on('response', handle_response)

    async def scrape_chatgpt(self, cdp_endpoint: str, prompt: str) -> Dict[str, Any]:
        """
        Main scraping function.
        ACCEPTED CHANGE: We now accept 'cdp_endpoint' instead of launching a browser.
        """
        async with async_playwright() as p:
            # CONNECT to the running SeleniumBase browser
            browser = await p.chromium.connect_over_cdp(cdp_endpoint)
            
            # Get the existing context and page (SeleniumBase opened it)
            context = browser.contexts[0]
            page = context.pages[0]

            # Set up interception on the existing page
            await self.setup_page_interceptor(page)

            try:
                # We are already at the URL (SeleniumBase went there), 
                # but we can navigate again to be safe or just start typing.
                
                # Wait for textarea and enter prompt
                await page.wait_for_selector('#prompt-textarea')
                await page.fill('#prompt-textarea', prompt)
                await page.press('#prompt-textarea', 'Enter')

                # Wait for response completion (using your existing logic)
                await self.wait_for_response(page)

                if self.captured_responses:
                    raw_response = self.captured_responses[0]
                    # Simple parsing for demo
                    events = extract_raw_response(raw_response)
                    return {'raw_events': events}
                else:
                    raise Exception("No response captured")

            finally:
                # Do NOT close the browser here, or it kills the SeleniumBase session
                await browser.disconnect()

    async def wait_for_response(self, page: Page, timeout: int = 60):
        """Wait for ChatGPT response completion (Same as before)."""
        for _ in range(timeout * 2):
            if self.captured_responses:
                response = self.captured_responses[0]
                if '[DONE]' in response:
                    return
            await asyncio.sleep(0.5)
        raise Exception("Response timeout")

# --- 3. The Bridge: SeleniumBase launches -> Playwright connects ---
def main():
    # 1. Start SeleniumBase with Undetected Mode (uc=True)
    with SB(uc=True, test=True) as sb:
        print("SeleniumBase: Opening ChatGPT...")
        sb.open("https://chatgpt.com/?temporary-chat=true")
        
        # 2. Get the Debugger URL (The Bridge)
        # This URL allows Playwright to take control of this specific browser
        cdp_endpoint = sb.get_endpoint_url()
        
        print(f"SeleniumBase: Browser ready. Handing over to Playwright at {cdp_endpoint}")
        
        # 3. Run the async Playwright scraper
        scraper = ChatGPTScraper()
        
        # We need a fresh event loop because SeleniumBase might be running in its own loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(scraper.scrape_chatgpt(cdp_endpoint, "Explain quantum physics"))
        
        print("\n--- Captured Data ---")
        print(result)

if __name__ == "__main__":
    main()