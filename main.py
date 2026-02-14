from contextlib import suppress
import json
import os
import re
import time
from urllib.parse import urlsplit, urlunsplit
from seleniumbase import SB
from utils import sleep_dbg
from utils import save_ss
from is_pages.is_verification_page import *
from is_pages.is_pop_ups import *
from is_pages.is_chat_ui import *
from boomlify_codes import *
from activate_search_mode import *
from birthday_helpers import fill_birthday


t0 = time.perf_counter()

proxy = "versedoin_Xdhcu:AyJ+cQ0Xi7fnTx@pr.oxylabs.io:7777"
# https://auth.openai.com/log-in-or-create-account
# https://chatgpt.com/auth/login_with
# https://auth.openai.com/log-in


first_names = [
    "James", "John", "Robert", "Michael", "William", "David", "Richard", "Joseph", "Thomas", "Charles",
    "Christopher", "Daniel", "Matthew", "Anthony", "Mark", "Donald", "Steven", "Paul", "Andrew", "Joshua",
    "Kenneth", "Kevin", "Brian", "George", "Timothy", "Ronald", "Edward", "Jason", "Jeffrey", "Ryan",
    "Jacob", "Gary", "Nicholas", "Eric", "Stephen", "Jonathan", "Larry", "Justin", "Scott", "Brandon",
    "Benjamin", "Samuel", "Gregory", "Frank", "Alexander", "Patrick", "Jack", "Dennis", "Jerry", "Tyler",
    "Aaron", "Henry", "Douglas", "Peter", "Adam", "Nathan", "Zachary", "Walter", "Kyle", "Ethan",
    "Mary", "Patricia", "Jennifer", "Linda", "Elizabeth", "Barbara", "Susan", "Jessica", "Sarah", "Karen",
    "Nancy", "Lisa", "Margaret", "Betty", "Sandra", "Ashley", "Kimberly", "Emily", "Donna", "Michelle",
    "Carol", "Amanda", "Melissa", "Deborah", "Stephanie", "Rebecca", "Sharon", "Laura", "Cynthia", "Kathleen",
    "Amy", "Shirley", "Angela", "Helen", "Anna", "Brenda", "Pamela", "Nicole", "Emma", "Samantha",
    "Christine", "Catherine", "Victoria", "Allison", "Hannah", "Grace", "Chloe", "Julia",
]
last_names = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Rodriguez", "Martinez",
    "Hernandez", "Lopez", "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore", "Jackson", "Martin",
    "Lee", "Perez", "Thompson", "White", "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson",
    "Walker", "Young", "Allen", "King", "Wright", "Scott", "Torres", "Nguyen", "Hill", "Flores",
    "Green", "Adams", "Nelson", "Baker", "Hall", "Rivera", "Campbell", "Mitchell", "Carter", "Roberts",
    "Gomez", "Phillips", "Evans", "Turner", "Diaz", "Parker", "Cruz", "Edwards", "Collins", "Reyes",
    "Stewart", "Morris", "Morales", "Murphy", "Cook", "Rogers", "Gutierrez", "Ortiz", "Morgan", "Cooper",
    "Peterson", "Bailey", "Reed", "Kelly", "Howard", "Ramos", "Kim", "Cox", "Ward", "Richardson",
    "Watson", "Brooks", "Chavez", "Wood", "James", "Bennett", "Gray", "Mendoza", "Ruiz", "Hughes",
    "Price", "Alvarez", "Castillo", "Sanders", "Patel", "Myers", "Long", "Ross",
]


# --------------------------------------------------------------------

def _clean_link(raw):
    raw = str(raw or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parts = urlsplit(raw)
    except Exception:
        return ""
    if not parts.hostname:
        return ""
    # Keep path/query, strip fragments for stable link matching.
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


def _get_domain(raw_url):
    if not raw_url:
        return ""
    try:
        host = (urlsplit(raw_url).hostname or "").lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _domain_matches(domain, target):
    if not domain or not target:
        return False
    return domain == target or domain.endswith("." + target)


def _clean_list(items):
    out = []
    for item in items:
        if isinstance(item, str):
            url = _clean_link(item)
            if url:
                out.append(url)
        elif isinstance(item, dict):
            for key in ("url", "link", "href"):
                raw = item.get(key)
                if isinstance(raw, str):
                    url = _clean_link(raw)
                    if url:
                        out.append(url)
                    break
    return out


def _normalize_urls(value):
    if value is None:
        return []
    if isinstance(value, bytes):
        with suppress(Exception):
            return _normalize_urls(json.loads(value.decode("utf-8")))
        return []
    if isinstance(value, str):
        with suppress(Exception):
            return _normalize_urls(json.loads(value))
        return _clean_list([value])
    if isinstance(value, dict):
        return _clean_list([value])
    if isinstance(value, list):
        return _clean_list(value)
    return []


def _unique_links(items):
    seen = set()
    out = []
    for item in items:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _read_brand_tokens(value):
    if isinstance(value, str):
        token = value.strip()
        return [token] if token else []
    if isinstance(value, list):
        out = []
        for item in value:
            if isinstance(item, str):
                token = item.strip()
                if token:
                    out.append(token)
        return out
    return []


def _brand_tokens_from_website(value):
    if value is None:
        return []
    if isinstance(value, bytes):
        with suppress(Exception):
            return _brand_tokens_from_website(json.loads(value.decode("utf-8")))
        return []
    if isinstance(value, str):
        with suppress(Exception):
            return _brand_tokens_from_website(json.loads(value))
        return []
    if isinstance(value, dict):
        return _read_brand_tokens(value.get("brand_tokens")) + _read_brand_tokens(
            value.get("brand_token")
        )
    if isinstance(value, list):
        out = []
        for item in value:
            out += _brand_tokens_from_website(item)
        return out
    return []


def _count_brand_mentions(domain, website, text):
    text = str(text or "")
    if not text.strip():
        return 0
    brand = domain.split(".")[0] if domain else ""
    variants = [brand] if brand else []
    variants += _brand_tokens_from_website(website)

    seen = set()
    count = 0
    for variant in variants:
        token = " ".join(str(variant or "").split()).strip()
        if not token:
            continue
        key = token.lower()
        if key in seen:
            continue
        seen.add(key)
        pattern = re.compile(rf"(?i)\b{re.escape(token)}\b")
        count += len(pattern.findall(text))
    return count


def build_metrics(response_text, source_links, website, competitor_websites):
    website_urls = _normalize_urls(website)
    my_domain = _get_domain(website_urls[0]) if website_urls else ""

    competitor_domains = []
    for url in _normalize_urls(competitor_websites):
        domain = _get_domain(url)
        if domain and domain not in competitor_domains:
            competitor_domains.append(domain)

    unique_links = _unique_links(_clean_list(source_links or []))

    my_citations = []
    competitor_citations = []
    for link in unique_links:
        domain = _get_domain(link)
        if _domain_matches(domain, my_domain):
            my_citations.append(link)
            continue
        for comp_domain in competitor_domains:
            if _domain_matches(domain, comp_domain):
                competitor_citations.append(link)
                break

    return {
        "my_citations": my_citations,
        "competitor_citations": competitor_citations,
        "total_citations_count": len(unique_links),
        "my_domain_citations_count": len(my_citations),
        "my_brand_mentions_count": _count_brand_mentions(my_domain, website, response_text),
        "appeared_links_unique": unique_links,
    }


def _to_jsonb(value):
    if value is None:
        return None
    with suppress(Exception):
        return json.dumps(value)
    return None

# --------------------------------------------------------------------

def fetch_chatgpt_code_from_boomlify_separate(
    search_email,
    login_email="staywhizzy2023@gmail.com",
    login_password="Katana@23033",
    total_timeout=60,
):
    """
    Fetch the latest Boomlify message subject and extract a 6-digit ChatGPT code.
    Retries 5 times with a 2-second gap.
    """
    code_pattern = re.compile(
        r"Your\s+(?:ChatGPT|OpenAI)\s+(?:code\s+is|password\s+reset\s+code\s+is)\s+(\d{6})",
        re.I,
    )
    for attempt in range(1, 6):
        subject = get_latest_message(search_email)
        if subject:
            if re.search(r"Access\s+Deactivated", subject, re.I):
                print(f"[BOOMLIFY][NOTICE] Access deactivated for {search_email}")
                return -1
            match = code_pattern.search(subject)
            if match:
                code = match.group(1)
                print(f"[BOOMLIFY][SUCCESS] Found verification code: {code}")
                return code
        if attempt < 5:
            time.sleep(2)
    print(f"[BOOMLIFY][ERROR] Could not find ChatGPT code for {search_email}")
    return None

# --------------------------------------------------------------------

def handle_login(sb, email, password):
    print("⚡ Systems green, standing by.")
    print("\n")

    url = "https://chatgpt.com/auth/login"
    
    # sb.uc_open_with_reconnect(url, 4)
    sb.activate_cdp_mode(url)

    sleep_dbg(sb, 120,130)
    
    print("\n" * 3)
    print("### [START] OPEN LOGIN BUTTON PART ###")
    save_ss(sb)
    sleep_dbg(sb, 3,10)
    sb.click_if_visible('button:contains("Log in")')
    print("[CLICKED BUTTON] Log in")
    sleep_dbg(sb, 53,56)
    save_ss(sb)
    print("### [END] OPEN LOGIN BUTTON PART ###")

# --------------------------------------------------------------------

    print("\n" * 3)
    print("### [START] EMAIL INPUT PART ###")
    save_ss(sb)
    sleep_dbg(sb, 3,10)
    email_input_field = '/html/body/div/div/fieldset/form/div[2]/div/div/div/label/div/div'
    sb.press_keys(email_input_field, email)
    print("[FILLED] Email")
    sleep_dbg(sb, 3, 5)
    save_ss(sb)
    print("### [END] EMAIL INPUT PART ###")

# --------------------------------------------------------------------

    print("\n" * 3)
    print("### [START] CONTINUE BUTTON PART ###")
    save_ss(sb)
    sleep_dbg(sb, 3,10)
    continue_button_xPath_Full = '/html/body/div/div/fieldset/form/div[3]/div/button'
    sb.click(continue_button_xPath_Full)
    print("[CLICKED BUTTON] Continue")
    sleep_dbg(sb, 53, 56)
    save_ss(sb)
    print("### [END] CONTINUE BUTTON PART ###")

# --------------------------------------------------------------------

    print("\n" * 3)
    print("### [START] CHECKING VERIFICATION/PASSWORD PART ###")
    save_ss(sb)
    sleep_dbg(sb, 3,10)
    verfication_page_appeared=is_verification_page_visible(sb)
    print("\n")
    print(f"verification page appeared : {verfication_page_appeared}")
    print("\n")
    if verfication_page_appeared==True:
        verification_code_input_field_xPath_Full = '/html/body/div/div/fieldset/form/div[1]/div/div/div/label/div/div'
        code = fetch_chatgpt_code_from_boomlify_separate(email)
        sb.press_keys(verification_code_input_field_xPath_Full, code)
        save_ss(sb)
        sleep_dbg(sb, 3, 5)
    else:
        login_with_one_time_code_button_xPath_full='/html/body/div/div/fieldset/form/div[2]/div[3]/div/button'
        sb.click(login_with_one_time_code_button_xPath_full)
        sleep_dbg(sb,8,12)
        verification_code_input_field_xPath_Full = '/html/body/div/div/fieldset/form/div[1]/div/div/div/label/div/div'
        code = fetch_chatgpt_code_from_boomlify_separate(email)
        sb.press_keys(verification_code_input_field_xPath_Full, code)
        save_ss(sb)
        sleep_dbg(sb, 3, 5)
    
    continue_button_xPath_Full='/html/body/div/div/fieldset/form/div[2]/div[1]/div[1]/button'
    sb.click(continue_button_xPath_Full)
    print("[CLICKED BUTTON] Continue")
    sleep_dbg(sb, 53, 56)
    save_ss(sb)
    print("### [END] CHECKING VERIFICATION/PASSWORD PART ###")
    
# --------------------------------------------------------------------

    popups_appeared=is_popups_visible(sb)
    print("\n")
    print(f"Popups appeared : {popups_appeared}")
    print("\n")

# --------------------------------------------------------------------
    save_ss(sb)
    if is_chat_ui_visible(sb)==True:
        dt = time.perf_counter() - t0
        h = int(dt // 3600)
        m = int((dt % 3600) // 60)
        s = dt % 60
        print(f"Runtime: {h}h {m}m {s:.2f}s")
        return True
    
    dt = time.perf_counter() - t0
    h = int(dt // 3600)
    m = int((dt % 3600) // 60)
    s = dt % 60
    print(f"Runtime: {h}h {m}m {s:.2f}s")
    return False

# --------------------------------------------------------------------

def temporary_chat_creator(sb):
    save_ss(sb)
    print("\n" * 3)
    print("### [START] TEMPORARY CHAT BUTTON PART ###")
    sleep_dbg(sb, 2,5)
    temporary_chat_creation_button= '/html/body/div[1]/div[1]/div/div[2]/div/header/div[3]/div[2]/div/div/span/button'
    sb.click(temporary_chat_creation_button)
    print("[CLICKED BUTTON] temporary_chat_creation_button")
    sleep_dbg(sb, 2,5)
    save_ss(sb)
    return True

# --------------------------------------------------------------------


def create_chatgpt_account(sb):
    print("⚡ Systems green, standing by.")
    print("\n")

    url = "https://chatgpt.com/auth/login"
    
    # sb.uc_open_with_reconnect(url, 4)
    sb.activate_cdp_mode(url)

    sleep_dbg(sb, 120,130)
    
    print("\n" * 3)
    print("### [START] OPEN SIGN UP BUTTON PART ###")
    save_ss(sb)
    sleep_dbg(sb, 3,10)
    sb.click_if_visible('button:contains("Log in")')
    print("[CLICKED BUTTON] Log in")
    sleep_dbg(sb, 53,56)
    save_ss(sb)
    print("### [END] OPEN SIGN UP BUTTON PART ###")

# --------------------------------------------------------------------

    result = create_email()
    new_email = result.get("email", {}).get("address")
    print(new_email)

# --------------------------------------------------------------------

    print("\n" * 3)
    print("### [START] EMAIL INPUT PART ###")
    save_ss(sb)
    sleep_dbg(sb, 3,10)
    # email_input_field = '/html/body/div/div/fieldset/form/div[2]/div/div/div/label/div/div'
    # sb.press_keys(email_input_field, new_email)
    email_selectors = [
    'input#email',
    'input[name="email"]',
    'input[type="email"]',
    'input[autocomplete="username"]',
    '/html/body/div/div/fieldset/form/div[2]/div/div/div/label/div/div',  # fallback
]

    email_input_field = None
    for _ in range(40):  # ~20s max
        for sel in email_selectors:
            if sb.is_element_visible(sel):
                email_input_field = sel
                break
        if email_input_field:
            break
        sb.sleep(0.5)

    if not email_input_field:
        raise RuntimeError("Email input not found")

    sb.type(email_input_field, new_email)
    print(f"[FILLED] Email via: {email_input_field}")

    print("[FILLED] Email")
    sleep_dbg(sb, 3, 5)
    save_ss(sb)
    print("### [END] EMAIL INPUT PART ###")

# --------------------------------------------------------------------

    print("\n" * 3)
    print("### [START] CONTINUE BUTTON AFTER EMAIL PART ###")
    save_ss(sb)
    sleep_dbg(sb, 3,10)
    continue_button_xPath_Full = '/html/body/div/div/fieldset/form/div[3]/div/button'
    sb.click(continue_button_xPath_Full)
    print("[CLICKED BUTTON] Continue")
    sleep_dbg(sb, 53, 56)
    save_ss(sb)
    print("### [END] CONTINUE BUTTON PART ###")

# --------------------------------------------------------------------

    print("\n" * 3)
    print("### [START] PASSWORD FILLING PART ###")
    save_ss(sb)
    sleep_dbg(sb, 3,10)
    password_input_field_xPath_Full = '/html/body/div/div/fieldset/form/div[1]/div[2]/div/div/input'
    sb.press_keys(password_input_field_xPath_Full, "Katana@230331")
    sleep_dbg(sb, 3, 10)
    save_ss(sb)
    print("### [END] PASSWORD FILLING PART ###")

# --------------------------------------------------------------------

    print("\n" * 3)
    print("### [START] CONTINUE BUTTON AFTER EMAIL PART ###")
    save_ss(sb)
    sleep_dbg(sb, 3,10)
    continue_button_after_password_xPath_Full = '/html/body/div/div/fieldset/form/div[2]/div/button'
    sb.click(continue_button_after_password_xPath_Full)
    print("[CLICKED BUTTON] Continue")
    sleep_dbg(sb, 53, 56)
    save_ss(sb)
    print("### [END] CONTINUE BUTTON PART ###")

# --------------------------------------------------------------------

    print("\n" * 3)
    print("### [START] CHECKING VERIFICATION/PASSWORD PART ###")
    sleep_dbg(sb, 3,10)
    verfication_page_appeared=is_verification_page_visible(sb)
    print("\n")
    print(f"verification page appeared : {verfication_page_appeared}")
    print("\n")
    if verfication_page_appeared==True:
        verification_code_input_field_xPath_Full = '/html/body/div/div/fieldset/form/div[1]/div/div/div/label/div/div'
        code = fetch_chatgpt_code_from_boomlify_separate(new_email)
        sb.press_keys(verification_code_input_field_xPath_Full, code)
        save_ss(sb)
        sleep_dbg(sb, 3, 5)
    else:
        login_with_one_time_code_button_xPath_full='/html/body/div/div/fieldset/form/div[2]/div[3]/div/button'
        sb.click(login_with_one_time_code_button_xPath_full)
        sleep_dbg(sb,8,12)
        verification_code_input_field_xPath_Full = '/html/body/div/div/fieldset/form/div[1]/div/div/div/label/div/div'
        code = fetch_chatgpt_code_from_boomlify_separate(email)
        sb.press_keys(verification_code_input_field_xPath_Full, code)
        save_ss(sb)
        sleep_dbg(sb, 3, 5)
    
    continue_button_xPath_Full='/html/body/div/div/fieldset/form/div[2]/div[1]/div[1]/button'
    sb.click(continue_button_xPath_Full)
    print("[CLICKED BUTTON] Continue")
    sleep_dbg(sb, 53, 56)
    save_ss(sb)
    print("### [END] CHECKING VERIFICATION/PASSWORD PART ###")
    
# --------------------------------------------------------------------

    print("\n" * 3)
    name_val = f"{random.choice(first_names)} {random.choice(last_names)}"
    birthday_val = f"{random.randint(1, 12):02d}/{random.randint(1, 28):02d}/{random.randint(1991, 2001)}"
    
    print("### [START] FULL NAME and BIRTHDAY PART ###")
    save_ss(sb)
    sleep_dbg(sb, 3,10)
    full_name_input_field = 'input[placeholder="Full name"]'
    sb.press_keys(full_name_input_field, name_val)
    print("[FILLED] Full name")
    sleep_dbg(sb, 3, 5)
    save_ss(sb)
    birthday_filled = fill_birthday(sb, birthday_val)
    if not birthday_filled:
        save_ss(sb)
        raise RuntimeError("Birthday fill failed")
    print("[FILLED] Birthday")
    sleep_dbg(sb, 3, 5)
    save_ss(sb)
    print("### [END] FULL NAME and BIRTHDAY PART ###")


# --------------------------------------------------------------------

    print("\n" * 3)
    print("### [START] CONTINUE BUTTON AFTER FULLNAME AND BIRTHDAY PART ###")
    save_ss(sb)
    sleep_dbg(sb, 3,10)
    continue_button_after_password_xPath_Full = '/html/body/div/div/fieldset/form/div[2]/div/button'
    sb.click(continue_button_after_password_xPath_Full)
    print("[CLICKED BUTTON] Continue")
    sleep_dbg(sb, 53, 56)
    save_ss(sb)
    print("### [END] CONTINUE BUTTON AFTER FULLNAME AND BIRTHDAY PART ###")

# --------------------------------------------------------------------

    popups_appeared=is_popups_visible(sb)
    print("\n")
    print(f"Popups appeared TRY 1: {popups_appeared}")
    print("\n")
    popups_appeared=is_popups_visible(sb)
    print("\n")
    print(f"Popups appeared TRY 2: {popups_appeared}")
    print("\n")
    popups_appeared=is_popups_visible(sb)
    print("\n")
    print(f"Popups appeared TRY 3 : {popups_appeared}")
    print("\n")
    popups_appeared=is_popups_visible(sb)
    print("\n")
    print(f"Popups appeared TRY 4 : {popups_appeared}")
    print("\n")

# --------------------------------------------------------------------

    if is_chat_ui_visible(sb)==True:
        dt = time.perf_counter() - t0
        h = int(dt // 3600)
        m = int((dt % 3600) // 60)
        s = dt % 60
        print(f"Runtime: {h}h {m}m {s:.2f}s")
        return True
    
    dt = time.perf_counter() - t0
    h = int(dt // 3600)
    m = int((dt % 3600) // 60)
    s = dt % 60
    print(f"Runtime: {h}h {m}m {s:.2f}s")
    return False

# --------------------------------------------------------------------

def enter_prompt(sb, query):
    sleep_dbg(sb, 2, 5)
    sb.press_keys("#prompt-textarea", query)
    save_ss(sb)
    sb.click('button[data-testid="send-button"]')
    print("*** Input for ChatGPT: ***\n%s" % query)
    sb.sleep(3)

    with suppress(Exception):
        # The "Stop" button disappears when ChatGPT is done typing a response
        sb.wait_for_element_not_visible(
            'button[data-testid="stop-button"]', timeout=120
        )
    sb.sleep(3)
    save_ss(sb)

# --------------------------------------------------------------------

def pick_prompt(website_filter=None):

    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("Missing DATABASE_URL in environment/.env")

    try:
        batch_size = int(os.getenv("PROMPT_BATCH_SIZE", "1"))
    except ValueError:
        batch_size = 1
    batch_size = max(1, batch_size)

    prompt_engine = os.getenv("PROMPT_ENGINE", "chatgpt").strip() or "chatgpt"
    engine_account = (
        os.getenv("PROMPT_ENGINE_ACCOUNT", "github actions").strip()
        or "github actions"
    )
    pending_status = os.getenv("PROMPT_PENDING_STATUS", "pending").strip() or "pending"
    failed_status = os.getenv("PROMPT_FAILED_STATUS", "failed").strip() or "failed"
    processing_status = os.getenv("PROMPT_PROCESSING_STATUS", "processing").strip() or "processing"

    website_filter_json = None
    if website_filter is not None:
        if isinstance(website_filter, (dict, list)):
            website_filter_json = json.dumps(website_filter)
        elif isinstance(website_filter, str):
            text = website_filter.strip()
            if text:
                # Validate JSON text before sending it to Postgres jsonb operator.
                json.loads(text)
                website_filter_json = text
        else:
            raise ValueError("website_filter must be dict/list/JSON-string/None")

    website_filter_clause = ""
    if website_filter_json is not None:
        website_filter_clause = "AND website @> %s::jsonb"

    claim_sql = f"""
        WITH picked AS (
            SELECT id
            FROM public.prompts
            WHERE status IN (%s, %s, %s)
              AND engine = %s
              {website_filter_clause}
              AND prompt_text IS NOT NULL
              AND BTRIM(prompt_text) <> ''
            ORDER BY
                CASE status
                    WHEN %s THEN 1
                    WHEN %s THEN 2
                    WHEN %s THEN 3
                    ELSE 99
                END,
                created_at ASC,
                prompt_id ASC
            FOR UPDATE SKIP LOCKED
            LIMIT %s
        )
        UPDATE public.prompts AS p
        SET status = %s,
            engine_account = %s,
            started_at = COALESCE(p.started_at, NOW()),
            attempts = COALESCE(p.attempts, 0) + 1
        FROM picked
        WHERE p.id = picked.id
        RETURNING
            p.id,
            p.prompt_id,
            p.prompt_text,
            p.status,
            p.created_at,
            p.website,
            p.competitor_websites;
    """

    query_params = [
        pending_status,
        failed_status,
        processing_status,
        prompt_engine,
    ]
    if website_filter_json is not None:
        query_params.append(website_filter_json)
    query_params.extend(
        [
            pending_status,
            failed_status,
            processing_status,
            batch_size,
            processing_status,
            engine_account,
        ]
    )

    rows = []
    query_attempted = False

    with suppress(ImportError):
        import psycopg  # type: ignore

        query_attempted = True
        with psycopg.connect(database_url, connect_timeout=8) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    claim_sql,
                    tuple(query_params),
                )
                rows = cur.fetchall()
            conn.commit()

    if not query_attempted:
        with suppress(ImportError):
            import psycopg2  # type: ignore

            query_attempted = True
            conn = psycopg2.connect(database_url, connect_timeout=8)
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        claim_sql,
                        tuple(query_params),
                    )
                    rows = cur.fetchall()
                conn.commit()
            finally:
                conn.close()

    if not query_attempted:
        raise RuntimeError(
            "Postgres driver not found. Install one: `pip install psycopg[binary]`"
        )

    if not rows:
        print(
            "[PROMPTS] No prompts found in priority order: "
            f"{pending_status} > {failed_status} > {processing_status}."
        )
        return []

    prompts = []
    for row in rows:
        prompt_text = str(row[2] or "").strip()
        if not prompt_text:
            continue
        prompts.append(
            {
                "id": str(row[0]),
                "prompt_id": row[1],
                "prompt_text": prompt_text,
                "status": row[3],
                "created_at": str(row[4]) if row[4] is not None else None,
                "website": row[5],
                "competitor_websites": row[6],
            }
        )

    if not prompts:
        print("[PROMPTS] Claimed rows had empty prompt_text values.")
        return []

    print(f"[PROMPTS] Claimed {len(prompts)} prompt(s) from AWS RDS.")
    return prompts

# --------------------------------------------------------------------

def activate_search_mode(sb):
    sleep_dbg(sb, a=3, b=5)
    print("[SEARCH BUTTON CLICK PREPARINGS]")
    if not safe_type(sb, "#prompt-textarea", "/", label="search_slash"):
        return
    sb.sleep(2)
    if not safe_type(sb, "#prompt-textarea", "s", label="search_s"):
        return
    sb.sleep(1)
    if not safe_type(sb, "#prompt-textarea", "e", label="search_e"):
        return
    sb.sleep(1)
    if not safe_type(sb, "#prompt-textarea", "a", label="search_a"):
        return
    sb.sleep(1)
    if not safe_type(sb, "#prompt-textarea", "r", label="search_r"):
        return
    sb.sleep(1)
    if not safe_type(sb, "#prompt-textarea", "c", label="search_c"):
        return
    sb.sleep(1)
    if not safe_type(sb, "#prompt-textarea", "h", label="search_h"):
        return
    
    sb.sleep(1)
    if not safe_send_keys(sb, "#prompt-textarea", "\n", label="search_enter"):
        return
    # # Clicking the "+" button
    # click_first(sb, ['button[data-testid="composer-plus-btn"]'], label="Add files button")
    
    # sb.sleep(2)
    # # Clicking on "... More" text
    # click_first(sb, ['div:contains("More")'], label="More menu option")
    # sb.sleep(2)
    
    # # Clicking on web search option emoji
    # click_first(sb, ['div:contains("Web search")'], label="Web search")
    sb.sleep(2)
    
    if not safe_type(sb, "#prompt-textarea", " ", label="search_space"):
        return
    sb.sleep(1)
    #is search emoji finding search button
    sb.sleep(3)

# --------------------------------------------------------------------

def get_sources_links(sb):
    links = []
    with suppress(Exception):
        sb.scroll_into_view('button[aria-label="Sources"]')
        sleep_dbg(sb, 2, 5)
        sb.click('button[aria-label="Sources"]')
        sb.wait_for_element_visible('[data-testid="screen-threadFlyOut"]', timeout=8)

        elems = sb.find_elements('[data-testid="screen-threadFlyOut"] a[href]')
        seen = set()
        for e in elems:
            href = (e.get_attribute("href") or "").strip()
            if href and href not in seen:
                seen.add(href)
                links.append(href)

    save_ss(sb)
    return links

# --------------------------------------------------------------------

def get_response_text(sb):
    selectors = [ 
        '[data-message-author-role="assistant"] .markdown',
        'div[data-message-author-role="assistant"]',
    ]
    for sel in selectors:
        try:
            elems = sb.find_elements(sel)
            if elems:
                return (elems[-1].text or "").strip()
        except Exception:
            pass
    return ""


def get_response_error(sb):
    error_text_candidates = [
        "Error in message stream",
        "Something went wrong",
        "Network error",
    ]
    for text in error_text_candidates:
        with suppress(Exception):
            if sb.is_text_visible(text):
                return text
    return None


def update_prompt_result(
    prompt_id,
    status,
    response_text=None,
    error_text=None,
    prompt_text=None,
    source_links=None,
    website=None,
    competitor_websites=None,
    engine_account="github actions",
    skip_if_missing=True,
):
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("Missing DATABASE_URL")

    metrics = None
    if status == "completed":
        metrics = build_metrics(
            response_text=response_text,
            source_links=source_links or [],
            website=website,
            competitor_websites=competitor_websites,
        )

    sql = """
        UPDATE public.prompts
        SET status = %s,
            engine_account = COALESCE(%s, engine_account),
            prompt_text = COALESCE(%s, prompt_text),
            response_text = COALESCE(%s, response_text),
            error_text = COALESCE(%s, error_text),
            appeared_links = COALESCE(%s::jsonb, appeared_links),
            appeared_links_unique = COALESCE(%s::jsonb, appeared_links_unique),
            my_citations = COALESCE(%s::jsonb, my_citations),
            competitor_citations = COALESCE(%s::jsonb, competitor_citations),
            total_citations_count = COALESCE(%s, total_citations_count),
            my_domain_citations_count = COALESCE(%s, my_domain_citations_count),
            my_brand_mentions_count = COALESCE(%s, my_brand_mentions_count),
            finished_at = CASE
                WHEN %s IN ('completed', 'failed') THEN NOW()
                ELSE finished_at
            END
        WHERE id = %s AND status <> 'completed'
        RETURNING id, status;
    """

    import psycopg
    with psycopg.connect(database_url, connect_timeout=8) as conn:
        with conn.cursor() as cur:
            cur.execute(
                sql,
                (
                    status,
                    engine_account,
                    prompt_text,
                    response_text,
                    error_text,
                    _to_jsonb(source_links if status == "completed" else None),
                    _to_jsonb(metrics["appeared_links_unique"]) if metrics else None,
                    _to_jsonb(metrics["my_citations"]) if metrics else None,
                    _to_jsonb(metrics["competitor_citations"]) if metrics else None,
                    metrics["total_citations_count"] if metrics else None,
                    metrics["my_domain_citations_count"] if metrics else None,
                    metrics["my_brand_mentions_count"] if metrics else None,
                    status,
                    prompt_id,
                ),
            )
            row = cur.fetchone()
        conn.commit()

    if not row:
        if skip_if_missing:
            print(
                f"[PROMPTS][SKIP] Prompt {prompt_id} not updated "
                "(missing/already completed)."
            )
            return {"id": str(prompt_id), "status": "skipped", "updated": False}
        raise RuntimeError(f"Prompt {prompt_id} not updated (missing/already completed?)")
    return {"id": str(row[0]), "status": row[1], "updated": True}


if __name__ == "__main__":

    email = "hwcg4zim@student.nondon.store"
    password = "Katana@230331"
    # Set to None to disable filtering, or pass a JSON-compatible dict to filter.
    website_filter = {"url": "https://polygon.technology/", "location": "US"}
    
    with SB(uc=True, test=True, incognito=True, locale="en", proxy=proxy) as sb:
        # handle_login_result = handle_login(sb, email, password)
        create_chatgpt_account_result=create_chatgpt_account(sb)
        print(f" [RESULT] {create_chatgpt_account_result}")
        if create_chatgpt_account_result == True:
            temporary_chat_creator(sb)
            activate_search_mode(sb)
            for i in range(200):
                picked_prompts = pick_prompt(website_filter=website_filter)
                if not picked_prompts:
                    print("[PROMPTS] Queue is empty. Stopping loop.")
                    break
                picked_prompt = picked_prompts[0]
                print("\n")
                print("###  PROMPT NUMBER:  ###")
                print(i+1)
                print("\n")
                print(picked_prompt["id"])
                enter_prompt(sb, picked_prompt["prompt_text"])
                sleep_dbg(sb, 2, 5)
                response_text = get_response_text(sb)
                links = get_sources_links(sb)
                response_error = get_response_error(sb)
                print(response_text)
                print("\n" * 2)
                print(links)
                
                save_ss(sb)
                response_text_clean = str(response_text or "").strip()
                status = "completed"
                error_text = None
                if response_error:
                    status = "failed"
                    error_text = response_error
                elif len(response_text_clean) < 10:
                    status = "failed"
                    error_text = (
                        f"Assistant response too short (<10 chars). "
                        f"Length={len(response_text_clean)}"
                    )

                update_result = update_prompt_result(
                    picked_prompt["id"],
                    status,
                    response_text=response_text,
                    error_text=error_text,
                    source_links=links,
                    website=picked_prompt.get("website"),
                    competitor_websites=picked_prompt.get("competitor_websites"),
                    engine_account="github actions",
                    skip_if_missing=True,
                )
                print(f"[PROMPTS][UPDATE] {update_result}")

                dt = time.perf_counter() - t0
                h = int(dt // 3600)
                m = int((dt % 3600) // 60)
                s = dt % 60
                print(f"Runtime: {h}h {m}m {s:.2f}s")

                print("\n" * 3)

                


    
