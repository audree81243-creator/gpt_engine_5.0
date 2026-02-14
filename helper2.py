
def _scroll_sources_list(sb, label, max_rounds=6):
    last_height = None
    stable = 0
    for _ in range(max_rounds):
        try:
            info = sb.execute_script(
                """
                const label = String(arguments[0] || '').trim().toLowerCase();
                const li = [...document.querySelectorAll("li")]
                    .find(el => (el.innerText || "").trim().toLowerCase() === label);
                if (!li) return { ok: false };
                const container = li.parentElement?.querySelector("ul") || li.parentElement || document.scrollingElement;
                if (!container) return { ok: false };
                container.scrollTop = container.scrollHeight;
                return { ok: true, height: container.scrollHeight };
                """,
                label,
            )
        except Exception:
            return False
        if not info or not info.get("ok"):
            return False
        height = info.get("height")
        if last_height is not None and height == last_height:
            stable += 1
        else:
            stable = 0
        last_height = height
        sleep_dbg(sb, a=2, b=4, label=f"sources_scroll_{label}")
        if stable >= 1:
            break
    return True

def _collect_sources_links(sb):
    sources_hrefs = []
    sources_button_selectors = [
        'button:contains("Sources")',
        'button:contains("Citations")',
        'button:contains("References")',
        '[role="button"]:contains("Sources")',
        '[role="button"]:contains("Citations")',
        'a:contains("Sources")',
        'a:contains("Citations")',
        '[aria-label*="Sources" i]',
        '[aria-label*="Citations" i]',
        '[aria-label*="References" i]',
    ]
    try:
        tabs_present = sb.execute_script(
            """
            return Array.from(document.querySelectorAll('li,button,[role="tab"]'))
                .some(el => {
                const text = (el.innerText || '').trim().toLowerCase();
                return text === 'citations' || text === 'more';
                });
            """
        )
    except Exception:
        tabs_present = False
    if not tabs_present:
        clicked_sources = click_first(sb, sources_button_selectors, label="sources-panel")
        if not clicked_sources:
            try:
                clicked_sources = sb.execute_script(
                    """
                    const labels = (arguments[0] || []).map(s => String(s).toLowerCase());
                    const els = Array.from(document.querySelectorAll('button,[role="button"],a'));
                    const match = els.find(el => labels.includes((el.innerText || '').trim().toLowerCase()));
                    if (match) { match.click(); return true; }
                    return false;
                    """,
                    ["Sources", "Citations", "References"],
                )
            except Exception:
                clicked_sources = False
        if clicked_sources:
            sleep_dbg(sb, a=3, b=6, label="after_sources_open")
            save_ss(sb)

    for label in ("Citations", "More"):
        tab_selectors = [
            f'li:contains("{label}")',
            f'button:contains("{label}")',
            f'[role="tab"]:contains("{label}")',
        ]
        clicked_tab = click_first(sb, tab_selectors, label=f"sources-tab-{label.lower()}")
        if not clicked_tab:
            try:
                clicked_tab = sb.execute_script(
                    """
                    const label = String(arguments[0] || '').trim().toLowerCase();
                    const els = Array.from(document.querySelectorAll('li,button,[role="tab"]'));
                    const match = els.find(el => (el.innerText || '').trim().toLowerCase() === label);
                    if (match) { match.click(); return true; }
                    return false;
                    """,
                    label,
                )
            except Exception:
                clicked_tab = False
        if clicked_tab:
            sleep_dbg(sb, a=2, b=4, label=f"after_tab_{label.lower()}")
        scrolled = _scroll_sources_list(sb, label)
        if scrolled:
            sleep_dbg(sb, a=2, b=4, label=f"after_scroll_{label.lower()}")
            save_ss(sb)

        panel_links = _extract_sources_panel_links(sb)
        for href in panel_links:
            if href and href not in sources_hrefs:
                sources_hrefs.append(href)
    return sources_hrefs

def _extract_sources_panel_links(sb):
        script = """
        const getSectionLinks = (label) => {
          const li = [...document.querySelectorAll("li")]
            .find(el => (el.innerText || "").trim().toLowerCase() === label.toLowerCase());
          if (!li) return [];
          const ul = li.parentElement?.querySelector("ul");
          if (!ul) return [];
          return [...ul.querySelectorAll("a[href]")].map(a => a.href);
        };

        const citations = getSectionLinks("Citations");
        const more = getSectionLinks("More");

        const combined = Array.from(new Set([...citations, ...more]));
        return combined;
        """
        try:
            raw = sb.execute_script(script) or []
        except Exception:
            return []
        cleaned = []
        for href in raw:
            cleaned_href = _clean_link(href)
            if cleaned_href and cleaned_href not in cleaned:
                cleaned.append(cleaned_href)
        return cleaned

def _clean_link(url):
    try:
        url = str(url or "").strip()
    except Exception:
        return ""
    if not url:
        return ""
    try:
        parts = urlsplit(url)
    except Exception:
        return url
    if not parts.query:
        return url
    filtered = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k.lower() not in tracking_params
    ]
    query = urlencode(filtered, doseq=True)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))



elems = []
    response_selectors = [
        '[data-message-author-role="assistant"] .markdown',
        '[data-message-author-role="assistant"] article',
        'div[data-message-author-role="assistant"]',
        '[class*="message"] [class*="markdown"]',
        '[role="article"] .markdown',
    ]
    for resp_sel in response_selectors:
        try:
            elems = sb.cdp.find_all(resp_sel, timeout=60)
            if elems:
                break
        except Exception:
            pass
    hrefs = []
    try:
        latest_elem = elems[-1]
        last_text = (latest_elem.text or "").strip().replace("\n\n\n", "\n\n")
        links = latest_elem.query_selector_all("a")
        hrefs = []
        for link in links:
            cleaned_href = _clean_link(link.get_attribute("href"))
            if cleaned_href:
                hrefs.append(cleaned_href)
        if not hrefs:
            fallback_urls = re.findall(r"https?://[^\s)\"'<>]+", last_text)
            for url in fallback_urls:
                cleaned = _clean_link(url.rstrip(").,;]}"))
                if cleaned and cleaned not in hrefs:
                    hrefs.append(cleaned)
        save_ss(sb)
        sources_hrefs = _collect_sources_links(sb)
        for src in sources_hrefs:
            if src and src not in hrefs:
                hrefs.append(src)
        last_hrefs = hrefs
        print(last_text)
        print(last_hrefs)
    except Exception:
        save_ss(sb)