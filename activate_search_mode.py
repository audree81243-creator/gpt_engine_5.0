from utils import *

def activate_search_mode(sb):
    sleep_dbg(sb, secs=None, a=3, b=5, label="")
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
