import feedparser
import requests
import json
import os
import logging
import re
import time
import asyncio
from datetime import datetime, timezone, timedelta
from bs4 import BeautifulSoup
from config import (
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SEEN_JOBS_FILE, CHECK_INTERVAL_MINUTES,
    INDEED_SEARCHES, GLASSDOOR_KEYWORDS, CUTSHORT_KEYWORDS, INFOPARK_SEARCH_URL
)
from scorer import score_job, MIN_SCORE
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes
import html  # FIXED: HTML escaping for Telegram parse mode safety
import threading  # FIXED: thread safety lock for global state and file accesses

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)

# State Files
# FIXED: Resolving absolute paths for deployment readiness
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATS_FILE = os.path.join(BASE_DIR, "stats.json")
KEYWORDS_FILE = os.path.join(BASE_DIR, "keywords.json")
HISTORY_FILE = os.path.join(BASE_DIR, "sent_jobs_history.json")

# FIXED: Thread-safety locks for shared states
state_lock = threading.Lock()

ACTIVE_KEYWORDS = []


# FIXED: Custom OrderedSet class to maintain insertion order and O(1) lookup
class OrderedSet:
    def __init__(self, iterable=None):
        self._map = {}
        if iterable:
            for item in iterable:
                self._map[item] = True

    def __contains__(self, item):
        return item in self._map

    def add(self, item):
        self._map[item] = True

    def remove(self, item):
        self._map.pop(item, None)

    def discard(self, item):
        self._map.pop(item, None)

    def __iter__(self):
        return iter(self._map.keys())

    def __len__(self):
        return len(self._map)


# ─── Dynamic Keywords Handler ──────────────────────────────────────────────────

def init_keywords():
    global ACTIVE_KEYWORDS
    with state_lock:  # FIXED: thread safety lock
        if os.path.exists(KEYWORDS_FILE):
            try:
                with open(KEYWORDS_FILE, "r") as f:
                    data = json.load(f)
                    if isinstance(data, list):  # FIXED: handle corrupt file
                        ACTIVE_KEYWORDS = data
                        log.info(f"Loaded {len(ACTIVE_KEYWORDS)} keywords from {KEYWORDS_FILE}")
                        return
                    else:
                        log.error(f"Keywords file {KEYWORDS_FILE} is not a list. Initializing from config.")
            except Exception as e:
                log.error(f"Error loading {KEYWORDS_FILE}: {e}. Initializing from config.")
            
    # Fallback to config
    from config import KEYWORDS as DEFAULT_KEYWORDS
    ACTIVE_KEYWORDS = list(DEFAULT_KEYWORDS)
    # Synchronous save during initialization
    try:
        with open(KEYWORDS_FILE, "w") as f:
            json.dump(ACTIVE_KEYWORDS, f, indent=2)
    except Exception as e:
        log.error(f"Error saving keywords: {e}")
    log.info(f"Initialized {KEYWORDS_FILE} with default keywords.")

def _save_keywords_sync():
    with state_lock:  # FIXED: thread safety lock
        try:
            with open(KEYWORDS_FILE, "w") as f:
                json.dump(ACTIVE_KEYWORDS, f, indent=2)
        except Exception as e:
            log.error(f"Error saving keywords: {e}")

async def save_keywords():
    await asyncio.to_thread(_save_keywords_sync)  # FIXED: async safety (non-blocking)


# ─── Stats and Settings Persistence ──────────────────────────────────────────

def get_ist_time():
    # IST is UTC + 5:30
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)

def _load_stats_sync():
    now_ist = get_ist_time()
    current_date = now_ist.strftime("%Y-%m-%d")
    
    default_stats = {
        "date": current_date,
        "is_paused": False,
        "last_summary_date": "",
        "last_run_timestamp": "",
        "last_run_epoch": 0.0,
        "total_jobs_found_today": 0,
        "total_sent_today": 0,
        "total_skipped_by_score_today": 0,
        "total_skipped_by_salary_today": 0,
        "total_sent": 0,
        "total_skipped_by_score": 0,
        "total_skipped_by_salary": 0
    }
    
    with state_lock:  # FIXED: thread safety
        if os.path.exists(STATS_FILE):
            try:
                with open(STATS_FILE, "r") as f:
                    stats = json.load(f)
                    if not isinstance(stats, dict):  # FIXED: handle corrupt file
                        log.error(f"Stats file {STATS_FILE} is corrupted. Resetting stats.")
                        stats = default_stats.copy()
                    
                    # Check for missing keys
                    updated = False
                    for k, v in default_stats.items():
                        if k not in stats:
                            stats[k] = v
                            updated = True
                    
                    # Check if calendar day has changed to reset daily counters
                    if stats.get("date") != current_date:
                        stats["date"] = current_date
                        stats["total_jobs_found_today"] = 0
                        stats["total_sent_today"] = 0
                        stats["total_skipped_by_score_today"] = 0
                        stats["total_skipped_by_salary_today"] = 0
                        updated = True
                        
                    if updated:
                        try:
                            with open(STATS_FILE, "w") as sf:
                                json.dump(stats, sf, indent=2)
                        except Exception as se:
                            log.error(f"Error writing to {STATS_FILE}: {se}")
                    return stats
            except Exception as e:
                log.error(f"Error reading {STATS_FILE}: {e}")
                
    return default_stats.copy()

async def load_stats():
    return await asyncio.to_thread(_load_stats_sync)  # FIXED: async safety (non-blocking)

def _save_stats_sync(stats):
    with state_lock:  # FIXED: thread safety
        try:
            with open(STATS_FILE, "w") as f:
                json.dump(stats, f, indent=2)
        except Exception as e:
            log.error(f"Error writing to {STATS_FILE}: {e}")

async def save_stats(stats):
    await asyncio.to_thread(_save_stats_sync, stats)  # FIXED: async safety (non-blocking)


# ─── Job History for Daily Summary ─────────────────────────────────────────────

def _load_history_sync():
    with state_lock:  # FIXED: thread safety
        if os.path.exists(HISTORY_FILE):
            try:
                with open(HISTORY_FILE, "r") as f:
                    data = json.load(f)
                    if isinstance(data, list):  # FIXED: handle corrupt file
                        return data
                    else:
                        log.error(f"History file {HISTORY_FILE} is not a list. Returning empty.")
            except Exception as e:
                log.error(f"Error loading history file: {e}")
    return []

async def load_history():
    return await asyncio.to_thread(_load_history_sync)  # FIXED: async safety (non-blocking)

def _add_to_history_sync(job, score):
    history = _load_history_sync()
    now_ts = time.time()
    
    # FIXED: Handled missing keys in job dictionary
    entry = {
        "timestamp": now_ts,
        "title": job.get("title", "N/A"),
        "company": job.get("company", "N/A"),
        "location": job.get("location", "N/A"),
        "link": job.get("link", ""),
        "score": score
    }
    history.append(entry)
    
    # Prune history older than 48 hours to keep file size small
    cutoff = now_ts - (48 * 3600)
    history = [item for item in history if item.get("timestamp", 0) >= cutoff]
    
    with state_lock:  # FIXED: thread safety
        try:
            with open(HISTORY_FILE, "w") as f:
                json.dump(history, f, indent=2)
        except Exception as e:
            log.error(f"Error saving history file: {e}")

async def add_to_history(job, score):
    await asyncio.to_thread(_add_to_history_sync, job, score)  # FIXED: async safety (non-blocking)

async def get_top_jobs_last_24_hours():
    history = await load_history()  # FIXED: async safety (non-blocking)
    now_ts = time.time()
    cutoff = now_ts - (24 * 3600)
    
    recent_jobs = [item for item in history if item.get("timestamp", 0) >= cutoff]
    
    # Deduplicate by link
    seen_links = set()
    unique_recent_jobs = []
    for item in recent_jobs:
        link = item.get("link")
        if link and link not in seen_links:
            seen_links.add(link)
            unique_recent_jobs.append(item)
            
    # Sort by score descending
    unique_recent_jobs.sort(key=lambda x: x["score"], reverse=True)
    return unique_recent_jobs[:5]


# ─── Salary Parsing Filter ────────────────────────────────────────────────────

def parse_salary_lpa(salary_str: str) -> float | None:
    if not salary_str or not isinstance(salary_str, str):  # FIXED: handle None or non-string inputs
        return None
    s = salary_str.lower().replace(",", "")
    
    # Extract all numbers/floats
    numbers = [float(x) for x in re.findall(r"\d+\.?\d*", s)]
    if not numbers:
        return None
        
    is_lakhs = "lac" in s or "lakh" in s or "lpa" in s
    is_pm = "pm" in s or "month" in s
    
    parsed_vals = []
    for num in numbers:
        if is_lakhs:
            val = num
        elif num >= 10000:
            if is_pm:
                val = (num * 12) / 100000.0
            else:
                val = num / 100000.0
        else:
            val = num
        parsed_vals.append(val)
        
    if not parsed_vals:
        return None
    return max(parsed_vals)


# ─── Seen Jobs Tracker ────────────────────────────────────────────────────────


def _load_seen_jobs_sync() -> OrderedSet:
    with state_lock:  # FIXED: thread safety
        if os.path.exists(SEEN_JOBS_FILE):
            try:
                with open(SEEN_JOBS_FILE, "r") as f:
                    data = json.load(f)
                    if isinstance(data, list):  # FIXED: handle corrupt file
                        return OrderedSet(data)
                    else:
                        log.error(f"Seen jobs file {SEEN_JOBS_FILE} is not a list. Resetting seen list.")
            except Exception as e:
                log.error(f"Error loading seen jobs: {e}")
    return OrderedSet()

async def load_seen_jobs() -> OrderedSet:
    return await asyncio.to_thread(_load_seen_jobs_sync)  # FIXED: async safety (non-blocking)

def _save_seen_jobs_sync(seen):
    try:
        seen_list = list(seen)
        # FIXED: cap seen list to 5000 entries (remove oldest 1000 when limit hit)
        if len(seen_list) > 5000:
            seen_list = seen_list[1000:]
            log.info(f"Capped seen jobs list to {len(seen_list)} items (removed oldest 1000).")
        with state_lock:  # FIXED: thread safety
            with open(SEEN_JOBS_FILE, "w") as f:
                json.dump(seen_list, f)
    except Exception as e:
        log.error(f"Error saving seen jobs: {e}")

async def save_seen_jobs(seen):
    await asyncio.to_thread(_save_seen_jobs_sync, seen)  # FIXED: async safety (non-blocking)


# ─── Async Telegram Helper ─────────────────────────────────────────────────────

def split_message_by_lines(text: str, limit: int = 4000) -> list[str]:
    # FIXED: Split message by lines to ensure HTML safety and avoid Telegram's 4096 character limit
    if len(text) <= limit:
        return [text]
    chunks = []
    current_chunk = []
    current_length = 0
    for line in text.split("\n"):
        if current_length + len(line) + 1 > limit:
            if current_chunk:
                chunks.append("\n".join(current_chunk))
                current_chunk = [line]
                current_length = len(line) + 1
            else:
                chunks.append(line)
        else:
            current_chunk.append(line)
            current_length += len(line) + 1
    if current_chunk:
        chunks.append("\n".join(current_chunk))
    return chunks

async def send_telegram_async(application, message: str):
    chunks = split_message_by_lines(message)  # FIXED: handle character limit safety
    for i, chunk in enumerate(chunks):
        if i > 0:
            await asyncio.sleep(1)  # FIXED: rate limit safety between consecutive sends
        try:
            await application.bot.send_message(
                chat_id=TELEGRAM_CHAT_ID,
                text=chunk,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        except Exception as e:
            log.error(f"Telegram send failed: {e}")


# ─── Message Formatting ────────────────────────────────────────────────────────

def format_job_message(title, company, location, link, source, score_data: dict, salary: str | None = None):
    # FIXED: HTML escaping for dynamic inputs to prevent XML parsing failures
    title_esc = html.escape(str(title), quote=False) if title else "N/A"
    company_esc = html.escape(str(company), quote=False) if company else "N/A"
    location_esc = html.escape(str(location), quote=False) if location else "N/A"
    salary_esc = html.escape(str(salary), quote=False) if salary else ""
    link_esc = html.escape(str(link)) if link else ""
    source_esc = html.escape(str(source), quote=False) if source else "N/A"

    source_emoji = {
        "LinkedIn": "💼",
        "Naukri": "🟠",
        "Wellfound": "🚀",
        "Internshala": "🎓",
        "Indeed": "🔵",
        "Glassdoor": "🟢",
        "Cutshort": "✂️",
        "Infopark": "🏢"
    }.get(source_esc, "📌")

    # FIXED: protection against None score_data
    if not score_data or not isinstance(score_data, dict):
        score_data = {"score": 0.0, "label": "❌ Weak Match", "matched_keywords": []}

    score = score_data.get("score", 0.0)
    label = score_data.get("label", "❌ Weak Match")
    matched = score_data.get("matched_keywords", [])

    # Score bar (visual 10-block bar)
    filled = round(score)
    bar = "█" * filled + "░" * (10 - filled)

    keywords_line = ""
    if matched:
        matched_esc = [html.escape(str(kw), quote=False) for kw in matched]
        keywords_line = f"🔑 <code>{', '.join(matched_esc)}</code>\n"

    salary_line = ""
    if salary_esc:
        salary_line = f"💰 <b>Salary:</b> {salary_esc}\n"

    return (
        f"{source_emoji} <b>New Job Alert — {source_esc}</b>\n\n"
        f"🏷 <b>{title_esc}</b>\n"
        f"🏢 {company_esc}\n"
        f"📍 {location_esc}\n"
        f"{salary_line}\n"
        f"{label}\n"
        f"⭐ <b>{score}/10</b>  <code>{bar}</code>\n"
        f"{keywords_line}\n"
        f"🔗 <a href='{link_esc}'>Apply Now</a>\n"
        f"⏰ {get_ist_time().strftime('%d %b %Y, %I:%M %p')}"
    )


# ─── Keyword Filter ───────────────────────────────────────────────────────────

def matches_keywords(text: str) -> bool:
    if not text:
        return False
    text_lower = text.lower()
    with state_lock:  # FIXED: thread safety lock for global active keywords
        kws = list(ACTIVE_KEYWORDS)
    return any(kw.lower() in text_lower for kw in kws)


# ─── Scrapers (LinkedIn, Naukri, Wellfound, Internshala) ──────────────────────

LINKEDIN_RSS_URLS = [
    "https://www.linkedin.com/jobs/search/?keywords=NestJS+developer&location=Kerala&f_TPR=r3600&f_JT=F&start=0",
    "https://www.linkedin.com/jobs/search/?keywords=Node.js+React+developer&location=India&f_WT=2&f_TPR=r3600&start=0",
    "https://www.linkedin.com/jobs/search/?keywords=full+stack+developer+NestJS&location=India&f_WT=2&f_TPR=r3600&start=0",
]

def scrape_linkedin(seen) -> list:
    found = []
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/120.0.0.0"}

    for url in LINKEDIN_RSS_URLS:
        try:
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code != 200:
                log.warning(f"LinkedIn scrape returned status code {r.status_code}")
                continue
            soup = BeautifulSoup(r.text, "html.parser")
            cards = soup.select("div.base-card")
            if not cards:  # FIXED: handle empty response
                continue

            for card in cards[:15]:
                try:
                    title_el = card.select_one("h3.base-search-card__title")
                    company_el = card.select_one("h4.base-search-card__subtitle")
                    location_el = card.select_one("span.job-search-card__location")
                    link_el = card.select_one("a.base-card__full-link")

                    title = title_el.get_text(strip=True) if title_el else "N/A"  # FIXED: use get_text and check safely
                    company = company_el.get_text(strip=True) if company_el else "N/A"  # FIXED: use get_text and check safely
                    location = location_el.get_text(strip=True) if location_el else "N/A"  # FIXED: use get_text and check safely
                    
                    # FIXED: safe href check
                    link = ""
                    if link_el:
                        href = link_el.get("href")
                        if href:
                            link = href.split("?")[0]

                    if not link or title == "N/A":
                        continue

                    job_id = f"linkedin_{link}"
                    if job_id in seen:
                        continue
                    if not matches_keywords(title):
                        continue

                    seen.add(job_id)
                    found.append({
                        "title": title, "company": company,
                        "location": location, "link": link, "source": "LinkedIn",
                        "salary": None
                    })
                except Exception:
                    continue

        except Exception as e:
            log.warning(f"LinkedIn scrape error: {e}")

    return found


NAUKRI_SEARCHES = [
    ("NestJS developer", "Kerala"),
    ("Node.js React full stack", "Kerala"),
    ("full stack developer", "Kochi"),
    ("NestJS Node.js", "India"),
]

def scrape_naukri(seen: OrderedSet) -> list:
    found = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "application/json",
        "appid": "109",
        "systemid": "Naukri",
    }

    for keyword, location in NAUKRI_SEARCHES:
        try:
            kw_encoded = keyword.replace(" ", "%20")
            loc_encoded = location.replace(" ", "%20")
            api_url = (
                f"https://www.naukri.com/jobapi/v3/search?"
                f"noOfResults=20&urlType=search_by_keyword&searchType=adv"
                f"&keyword={kw_encoded}&location={loc_encoded}&pageNo=1"
            )
            r = requests.get(api_url, headers=headers, timeout=15)
            if r.status_code == 406 and "recaptcha" in r.text.lower():
                log.warning("Naukri scrape blocked: ReCAPTCHA challenge required.")
                continue
            r.raise_for_status()
            data = r.json()
            
            # FIXED: json and list type safety
            if not isinstance(data, dict):
                continue
            jobs = data.get("jobDetails", [])
            if not isinstance(jobs, list):
                continue

            for job in jobs:
                try:  # FIXED: wrap inner loop in exception block
                    if not isinstance(job, dict):
                        continue
                    title = job.get("title", "N/A")
                    company = job.get("companyName", "N/A")
                    placeholders = job.get("placeholders", [])
                    if not isinstance(placeholders, list):
                        placeholders = []
                    location_str = "India"
                    salary_str = None
                    for p in placeholders:
                        if not isinstance(p, dict):
                            continue
                        ptype = p.get("type")
                        plabel = p.get("label")
                        if ptype == "location" and plabel:
                            location_str = ", ".join(str(plabel).split(",")[:2])
                        elif ptype == "salary" and plabel:
                            salary_str = str(plabel)
                    
                    # Fallback for location (original logic fallback)
                    if location_str == "India" and placeholders:
                        location_str = ", ".join(placeholders[0].get("label", "").split(",")[:2])
                    
                    link = job.get("jdURL", "https://www.naukri.com")
                    job_id = f"naukri_{job.get('jobId', link)}"

                    if job_id in seen:
                        continue
                    if not matches_keywords(title):
                        continue

                    seen.add(job_id)
                    found.append({
                        "title": title, "company": company,
                        "location": location_str, "link": link, "source": "Naukri",
                        "salary": salary_str
                    })
                except Exception as e:
                    log.warning(f"Error parsing Naukri job: {e}")

        except Exception as e:
            log.warning(f"Naukri scrape error ({keyword}): {e}")

    return found


WELLFOUND_SEARCHES = [
    "nestjs",
    "node-js-react",
    "full-stack",
]

def scrape_wellfound(seen: OrderedSet) -> list:
    found = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    }

    for role in WELLFOUND_SEARCHES:
        try:
            url = f"https://wellfound.com/role/r/{role}"
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code == 403 or "captcha-delivery" in r.text:
                log.warning(f"Wellfound scrape blocked by Captcha/Anti-bot (status {r.status_code}).")
                continue
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

            listings = soup.select("div[class*='JobListing']") or soup.select("div[data-test='StartupResult']")

            for item in listings[:10]:
                try:
                    title_el = item.select_one("a[class*='jobTitle'], span[class*='title']")
                    company_el = item.select_one("a[class*='startup'], span[class*='company']")
                    link_el = item.select_one("a[href*='/jobs/']")

                    title = title_el.text.strip() if title_el else role.replace("-", " ").title()
                    company = company_el.text.strip() if company_el else "Startup"
                    link = f"https://wellfound.com{link_el['href']}" if link_el else url
                    job_id = f"wellfound_{link}"

                    if job_id in seen:
                        continue
                    if not matches_keywords(title + " " + role):
                        continue

                    seen.add(job_id)
                    found.append({
                        "title": title, "company": company,
                        "location": "Remote / India", "link": link, "source": "Wellfound",
                        "salary": None
                    })
                except Exception:
                    continue

        except Exception as e:
            log.warning(f"Wellfound scrape error ({role}): {e}")

    return found


INTERNSHALA_SEARCHES = [
    "nodejs",
    "nestjs",
    "react-developer",
    "full-stack-development",
]

def scrape_internshala(seen: OrderedSet) -> list:
    found = []
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

    for category in INTERNSHALA_SEARCHES:
        try:
            url = f"https://internshala.com/jobs/{category}-jobs"
            r = requests.get(url, headers=headers, timeout=15)
            if r.status_code != 200:
                log.warning(f"Internshala scrape returned status code {r.status_code}")
                continue
            soup = BeautifulSoup(r.text, "html.parser")

            cards = soup.select("div.individual_internship")

            for card in cards[:10]:
                try:
                    title_el = card.select_one("h3.job-title a, .profile a")
                    company_el = card.select_one("p.company-name, .company_name")
                    location_el = card.select_one("p.locations a, .location_link")
                    link_el = card.select_one("a[href*='/jobs/detail/']")

                    title = title_el.text.strip() if title_el else "Developer"
                    company = company_el.text.strip() if company_el else "Company"
                    location = location_el.text.strip() if location_el else "India"
                    link = f"https://internshala.com{link_el['href']}" if link_el else url
                    job_id = f"internshala_{link}"

                    if job_id in seen:
                        continue
                    if not matches_keywords(title + " " + category):
                        continue

                    seen.add(job_id)
                    found.append({
                        "title": title, "company": company,
                        "location": location, "link": link, "source": "Internshala",
                        "salary": None
                    })
                except Exception:
                    continue

        except Exception as e:
            log.warning(f"Internshala scrape error ({category}): {e}")

    return found


def scrape_indeed(seen: OrderedSet) -> list:
    found = []

    for keyword, location_query in INDEED_SEARCHES:
        try:
            kw_encoded = keyword.replace(" ", "%20")
            loc_encoded = location_query.replace(" ", "%20")
            url = f"https://in.indeed.com/rss?q={kw_encoded}&l={loc_encoded}&sort=date"
            
            feedparser.USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            feed = feedparser.parse(url)
            
            if feed.bozo and not feed.entries:
                log.warning(f"Indeed RSS parse failed for {keyword} in {location_query}")
                continue
            
            for entry in feed.entries:
                title_raw = entry.get("title")
                author_raw = entry.get("author")
                location_raw = entry.get("location")
                link_raw = entry.get("link")
                
                # Normalize title
                if isinstance(title_raw, list) and title_raw:
                    first = title_raw[0]
                    title = first.get("value") or first.get("content") or str(first) if isinstance(first, dict) else str(first)
                else:
                    title = str(title_raw) if title_raw is not None else "N/A"
                title = str(title).strip()
                
                # Normalize company
                if isinstance(author_raw, list) and author_raw:
                    first = author_raw[0]
                    company = first.get("value") or first.get("content") or str(first) if isinstance(first, dict) else str(first)
                else:
                    company = str(author_raw) if author_raw is not None else "Indeed"
                company = str(company).strip()
                
                # Normalize location
                if isinstance(location_raw, list) and location_raw:
                    first = location_raw[0]
                    location = first.get("value") or first.get("content") or str(first) if isinstance(first, dict) else str(first)
                else:
                    location = str(location_raw) if location_raw is not None else location_query
                location = str(location).strip()
                
                # Normalize link
                if isinstance(link_raw, list) and link_raw:
                    first = link_raw[0]
                    if isinstance(first, dict):
                        link = first.get("href") or first.get("value") or str(first)
                    else:
                        link = str(first)
                elif isinstance(link_raw, dict):
                    link = link_raw.get("href") or link_raw.get("value") or str(link_raw)
                else:
                    link = str(link_raw) if link_raw is not None else ""
                
                if isinstance(link, list):
                    link = link[0] if link else ""
                link = str(link).strip()
                
                # Split by " - " as fallback
                if (company == "Indeed" or location == location_query) and " - " in title:
                    parts = [p.strip() for p in title.split(" - ")]
                    if len(parts) >= 3:
                        title = parts[0]
                        company = parts[1]
                        location = parts[2]
                    elif len(parts) == 2:
                        title = parts[0]
                        company = parts[1]
                        
                if not title or title == "N/A" or not link:
                    continue
                    
                job_id = f"indeed_{link}"
                if job_id in seen:
                    continue
                if not matches_keywords(title):
                    continue
                    
                seen.add(job_id)
                found.append({
                    "title": title, "company": company,
                    "location": location, "link": link, "source": "Indeed",
                    "salary": None
                })
        except Exception as e:
            log.warning(f"Indeed scrape error for {keyword} in {location_query}: {e}")
            
    return found


def scrape_glassdoor(seen: OrderedSet) -> list:
    found = []
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    
    for keyword in GLASSDOOR_KEYWORDS:
        try:
            kw_encoded = keyword.lower().replace(" ", "-")
            url = f"https://www.glassdoor.co.in/Job/india-{kw_encoded}-jobs-SRCH_IL.0,5_IN115_KO6,30.htm"
            
            r = requests.get(url, headers=headers, timeout=15)
            r.raise_for_status()
            
            soup = BeautifulSoup(r.text, "html.parser")
            
            cards = soup.find_all("li", class_=lambda x: x and ("joblisting" in x.lower() or "jobcard" in x.lower() or "card" in x.lower()))
            if not cards:
                cards = soup.find_all(attrs={"data-test": "jobListing"}) or soup.find_all("li")
                
            for card in cards:
                link_el = card.find("a", href=True)
                if not link_el:
                    link_el = card.find("a", href=lambda x: x and ("job" in x.lower() or "partner" in x.lower() or "click" in x.lower()))
                    
                if not link_el:
                    continue
                    
                link = link_el["href"].strip()
                if not link.startswith("http"):
                    link = f"https://www.glassdoor.co.in{link}"
                    
                title = "N/A"
                title_el = card.find(attrs={"data-test": "job-title"}) or card.find("a", class_=lambda x: x and "title" in x.lower())
                if not title_el:
                    title_el = card.find(class_=lambda x: x and "title" in x.lower())
                if title_el:
                    title = title_el.text.strip()
                else:
                    title = link_el.text.strip()
                    
                company = "N/A"
                company_el = card.find(attrs={"data-test": "employer-name"}) or card.find(class_=lambda x: x and ("employer" in x.lower() or "company" in x.lower()))
                if company_el:
                    company = company_el.text.strip()
                    
                location = "India"
                location_el = card.find(attrs={"data-test": "location"}) or card.find(class_=lambda x: x and "location" in x.lower())
                if location_el:
                    location = location_el.text.strip()
                    
                if title == "N/A" or not title or len(title) > 100:
                    continue
                if company == "N/A" or not company:
                    continue
                    
                job_id = f"glassdoor_{link}"
                if job_id in seen:
                    continue
                if not matches_keywords(title):
                    continue
                    
                seen.add(job_id)
                found.append({
                    "title": title, "company": company,
                    "location": location, "link": link, "source": "Glassdoor",
                    "salary": None
                })
        except Exception as e:
            log.warning(f"Glassdoor scrape error for keyword {keyword}: {e}")
            
    return found


def scrape_cutshort(seen: OrderedSet) -> list:
    log.warning("Cutshort API scraper is deprecated (endpoint returned 404/Not Found). Skipping Cutshort.")
    return []


def scrape_infopark(seen: OrderedSet) -> list:
    found = []
    headers = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
    
    try:
        url = INFOPARK_SEARCH_URL
        
        # Try GET first
        method_used = "GET"
        r = requests.get(url, headers=headers, timeout=15)
        r.raise_for_status()
        
        soup = BeautifulSoup(r.text, "html.parser")
        rows = soup.find_all("tr")
        job_rows = [row for row in rows if len(row.find_all("td")) >= 4]
        
        if not job_rows:
            # Retry with POST request
            method_used = "POST"
            post_headers = headers.copy()
            post_headers["Content-Type"] = "application/x-www-form-urlencoded"
            data = {"keyword": "developer"}
            r = requests.post(url, headers=post_headers, data=data, timeout=15)
            r.raise_for_status()
            
            soup = BeautifulSoup(r.text, "html.parser")
            rows = soup.find_all("tr")
            job_rows = [row for row in rows if len(row.find_all("td")) >= 4]
            
        log.info(f"Infopark scrape succeeded using {method_used} method.")
        
        for row in job_rows:
            tds = row.find_all("td")
            title = tds[1].text.strip()
            company = tds[2].text.strip()
            location = "Infopark, Kochi, Kerala"
            
            link_a = tds[4].find("a", href=True) if len(tds) > 4 else row.find("a", href=True)
            link = link_a["href"].strip() if link_a else ""
            if link and not link.startswith("http"):
                link = f"https://infopark.in{link}"
                
            if not title or not company or not link:
                continue
                
            job_id = f"infopark_{link}"
            if job_id in seen:
                continue
            if not matches_keywords(title):
                continue
                
            seen.add(job_id)
            found.append({
                "title": title, "company": company,
                "location": location, "link": link, "source": "Infopark",
                "salary": None
            })
    except Exception as e:
        log.warning(f"Infopark scrape error: {e}")
        
    return found


# ─── Async Scraper Runner ───────────────────────────────────────────────────────

async def run_scraper_async(application):
    log.info("🔍 Running job scraper...")
    seen = await load_seen_jobs()
    
    # Run sync scrapers in thread pool executor
    try:
        linkedin_jobs = await asyncio.to_thread(scrape_linkedin, seen)
    except Exception as e:
        log.error(f"LinkedIn scrape task failed: {e}")
        linkedin_jobs = []
        
    await asyncio.sleep(2)
        
    try:
        naukri_jobs = await asyncio.to_thread(scrape_naukri, seen)
    except Exception as e:
        log.error(f"Naukri scrape task failed: {e}")
        naukri_jobs = []
        
    await asyncio.sleep(2)
        
    try:
        wellfound_jobs = await asyncio.to_thread(scrape_wellfound, seen)
    except Exception as e:
        log.error(f"Wellfound scrape task failed: {e}")
        wellfound_jobs = []
        
    await asyncio.sleep(2)
        
    try:
        internshala_jobs = await asyncio.to_thread(scrape_internshala, seen)
    except Exception as e:
        log.error(f"Internshala scrape task failed: {e}")
        internshala_jobs = []
        
    await asyncio.sleep(2)
    
    try:
        indeed_jobs = await asyncio.to_thread(scrape_indeed, seen)
    except Exception as e:
        log.error(f"Indeed scrape task failed: {e}")
        indeed_jobs = []
        
    await asyncio.sleep(2)
    
    try:
        glassdoor_jobs = await asyncio.to_thread(scrape_glassdoor, seen)
    except Exception as e:
        log.error(f"Glassdoor scrape task failed: {e}")
        glassdoor_jobs = []
        
    await asyncio.sleep(2)
    
    try:
        cutshort_jobs = await asyncio.to_thread(scrape_cutshort, seen)
    except Exception as e:
        log.error(f"Cutshort scrape task failed: {e}")
        cutshort_jobs = []
        
    await asyncio.sleep(2)
    
    try:
        infopark_jobs = await asyncio.to_thread(scrape_infopark, seen)
    except Exception as e:
        log.error(f"Infopark scrape task failed: {e}")
        infopark_jobs = []
        
    all_jobs = (
        linkedin_jobs + naukri_jobs + wellfound_jobs + internshala_jobs +
        indeed_jobs + glassdoor_jobs + cutshort_jobs + infopark_jobs
    )
    await save_seen_jobs(seen)
    
    # Reload stats to update
    stats = await load_stats()
    stats["total_jobs_found_today"] += len(all_jobs)
    
    # Score and filter
    scored_jobs = []
    skipped_by_salary_count = 0
    skipped_by_score_count = 0
    
    for job in all_jobs:
        # Salary Filter
        salary_str = job.get("salary")
        if salary_str:
            max_lpa = parse_salary_lpa(salary_str)
            if max_lpa is not None and max_lpa < 6.0:
                log.info(f"Skipping job {job['title']} at {job['company']} due to low salary: {salary_str} ({max_lpa} LPA)")
                skipped_by_salary_count += 1
                continue
                
        # Score Job
        score_data = score_job(
            title=job["title"],
            company=job["company"],
            location=job["location"],
        )
        
        if score_data["send"]:
            scored_jobs.append((job, score_data))
        else:
            skipped_by_score_count += 1
            
    stats["total_skipped_by_salary_today"] += skipped_by_salary_count
    stats["total_skipped_by_salary"] += skipped_by_salary_count
    stats["total_skipped_by_score_today"] += skipped_by_score_count
    stats["total_skipped_by_score"] += skipped_by_score_count
    
    # Sort best jobs first
    scored_jobs.sort(key=lambda x: x[1]["score"], reverse=True)
    
    sent_count = 0
    if scored_jobs:
        log.info(f"✅ {len(scored_jobs)} job(s) passed scoring. Sending to Telegram...")
        for job, score_data in scored_jobs:
            msg = format_job_message(
                job["title"], job["company"],
                job["location"], job["link"],
                job["source"], score_data,
                job.get("salary")
            )
            await send_telegram_async(application, msg)
            await add_to_history(job, score_data["score"])
            sent_count += 1
            await asyncio.sleep(1)  # rate limit safety
    elif all_jobs:
        log.info(f"🚫 {len(all_jobs)} job(s) found but all skipped (score/salary).")
    else:
        log.info("😴 No new matching jobs found.")
        
    stats["total_sent_today"] += sent_count
    stats["total_sent"] += sent_count
    stats["last_run_timestamp"] = get_ist_time().strftime("%Y-%m-%d %I:%M:%S %p")
    stats["last_run_epoch"] = time.time()
    await save_stats(stats)


# ─── Daily 9am Summary ────────────────────────────────────────────────────────

async def send_daily_summary_async(application):
    log.info("Compiling daily 9:00 AM IST summary...")
    top_jobs = await get_top_jobs_last_24_hours()
    
    if not top_jobs:
        msg = "📅 <b>Daily Job Summary (Last 24 Hours)</b>\n\nNo matching jobs were found in the last 24 hours."
    else:
        job_lines = []
        for idx, job in enumerate(top_jobs, 1):
            score = job["score"]
            filled = round(score)
            bar = "█" * filled + "░" * (10 - filled)
            
            line = (
                f"{idx}. <b>{job['title']}</b>\n"
                f"   🏢 {job['company']} | 📍 {job['location']}\n"
                f"   ⭐ <b>{score}/10</b> <code>{bar}</code>\n"
                f"   🔗 <a href='{job['link']}'>Apply Now</a>\n"
            )
            job_lines.append(line)
            
        msg = (
            f"📅 <b>Daily Job Summary (Last 24 Hours)</b>\n"
            f"Here are the top {len(top_jobs)} highest-scored jobs:\n\n"
            + "\n".join(job_lines)
        )
        
    await send_telegram_async(application, msg)


# ─── Command Handlers ──────────────────────────────────────────────────────────

async def pause_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = await load_stats()
    stats["is_paused"] = True
    await save_stats(stats)
    log.info("Scraper paused by user command.")
    if update.effective_message:
        await update.effective_message.reply_text("⏸️ <b>Scraper loop paused.</b>", parse_mode="HTML")

async def resume_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = await load_stats()
    stats["is_paused"] = False
    await save_stats(stats)
    log.info("Scraper resumed by user command.")
    if update.effective_message:
        await update.effective_message.reply_text("▶️ <b>Scraper loop resumed.</b>", parse_mode="HTML")

async def status_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = await load_stats()
    is_paused = stats.get("is_paused", False)
    status_str = "Paused ⏸️" if is_paused else "Running ▶️"
    
    last_run = stats.get("last_run_timestamp")
    if not last_run:
        last_run = "Never"
        
    msg = (
        f"📊 <b>Bot Status</b>\n\n"
        f"⚙️ <b>State:</b> {status_str}\n"
        f"🕒 <b>Last Check:</b> {last_run}\n"
        f"🔍 <b>Jobs Found Today:</b> {stats.get('total_jobs_found_today', 0)}\n"
        f"✅ <b>Jobs Sent Today:</b> {stats.get('total_sent_today', 0)}\n"
        f"🛡️ <b>Skipped (Score):</b> {stats.get('total_skipped_by_score_today', 0)}\n"
        f"💰 <b>Skipped (Salary):</b> {stats.get('total_skipped_by_salary_today', 0)}\n\n"
        f"📈 <i>Lifetime Sent: {stats.get('total_sent', 0)} | Skipped: {stats.get('total_skipped_by_score', 0) + stats.get('total_skipped_by_salary', 0)}</i>"
    )
    if update.effective_message:
        await update.effective_message.reply_text(msg, parse_mode="HTML")

async def addkeyword_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
        
    if not context.args:
        await update.effective_message.reply_text("⚠️ <b>Usage:</b> /addkeyword &lt;word&gt;", parse_mode="HTML")
        return
        
    word = " ".join(context.args).strip()
    if not word:
        await update.effective_message.reply_text("⚠️ <b>Usage:</b> /addkeyword &lt;word&gt;", parse_mode="HTML")
        return
        
    global ACTIVE_KEYWORDS
    if any(k.lower() == word.lower() for k in ACTIVE_KEYWORDS):
        await update.effective_message.reply_text(f"⚠️ Keyword <code>{word}</code> is already in the list.", parse_mode="HTML")
        return
        
    ACTIVE_KEYWORDS.append(word)
    await save_keywords()
    log.info(f"Added keyword: {word}")
    await update.effective_message.reply_text(f"✅ Added keyword: <code>{word}</code>", parse_mode="HTML")

async def removekeyword_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
        
    if not context.args:
        await update.effective_message.reply_text("⚠️ <b>Usage:</b> /removekeyword &lt;word&gt;", parse_mode="HTML")
        return
        
    word = " ".join(context.args).strip()
    if not word:
        await update.effective_message.reply_text("⚠️ <b>Usage:</b> /removekeyword &lt;word&gt;", parse_mode="HTML")
        return
        
    global ACTIVE_KEYWORDS
    word_lower = word.lower()
    matching_kws = [k for k in ACTIVE_KEYWORDS if k.lower() == word_lower]
    
    if not matching_kws:
        await update.effective_message.reply_text(f"⚠️ Keyword <code>{word}</code> not found in active list.", parse_mode="HTML")
        return
        
    for k in matching_kws:
        ACTIVE_KEYWORDS.remove(k)
    await save_keywords()
    log.info(f"Removed keyword(s): {matching_kws}")
    await update.effective_message.reply_text(f"❌ Removed keyword: <code>{word}</code>", parse_mode="HTML")

async def keywords_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message:
        return
        
    if not ACTIVE_KEYWORDS:
        await update.effective_message.reply_text("🔑 <b>No active keywords.</b>", parse_mode="HTML")
        return
        
    kws_list = "\n".join(f"• <code>{kw}</code>" for kw in ACTIVE_KEYWORDS)
    msg = f"🔑 <b>Active Keywords ({len(ACTIVE_KEYWORDS)}):</b>\n\n{kws_list}"
    await update.effective_message.reply_text(msg, parse_mode="HTML")


# ─── Scheduler Loop Tasks ──────────────────────────────────────────────────────

async def scraper_loop(application):
    log.info("Scraper loop started.")
    # Immediate initial run if not paused
    stats = await load_stats()
    if not stats.get("is_paused", False):
        try:
            await run_scraper_async(application)
        except Exception as e:
            log.error(f"Error in initial scraper run: {e}")
            
    while True:
        try:
            await asyncio.sleep(10)
            stats = await load_stats()
            if stats.get("is_paused", False):
                continue
                
            last_run_epoch = stats.get("last_run_epoch", 0.0)
            now = time.time()
            if now - last_run_epoch >= CHECK_INTERVAL_MINUTES * 60:
                await run_scraper_async(application)
        except Exception as e:
            log.error(f"Error in scraper scheduler task: {e}")

async def daily_summary_loop(application):
    log.info("Daily 9am summary loop started.")
    while True:
        try:
            now_ist = get_ist_time()
            current_date_str = now_ist.strftime("%Y-%m-%d")
            
            # Check if it is 9:00 AM IST (hour 9, minute 0)
            if now_ist.hour == 9 and now_ist.minute == 0:
                stats = await load_stats()
                if stats.get("last_summary_date") != current_date_str:
                    await send_daily_summary_async(application)
                    stats["last_summary_date"] = current_date_str
                    await save_stats(stats)
        except Exception as e:
            log.error(f"Error in daily summary loop task: {e}")
            
        await asyncio.sleep(30)


# ─── Post-Init Hook ───────────────────────────────────────────────────────────

async def post_init(application):
    # Schedule loops as background tasks on the active event loop
    asyncio.create_task(scraper_loop(application))
    asyncio.create_task(daily_summary_loop(application))
    
    # Send startup message
    msg = (
        "🤖 <b>Job Alert Bot is now running!</b>\n\n"
        "I'll ping you the moment a matching job drops on LinkedIn, Naukri, Wellfound, or Internshala.\n\n"
        "<i>Checking every 10 minutes...</i>"
    )
    await send_telegram_async(application, msg)
    log.info("Startup Telegram message sent and background task loops started.")


# ─── Main Program ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_keywords()
    _load_stats_sync()
    
    # Build Telegram Bot application
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()
    
    # Add Command Handlers
    application.add_handler(CommandHandler("pause", pause_cmd))
    application.add_handler(CommandHandler("resume", resume_cmd))
    application.add_handler(CommandHandler("status", status_cmd))
    application.add_handler(CommandHandler("addkeyword", addkeyword_cmd))
    application.add_handler(CommandHandler("removekeyword", removekeyword_cmd))
    application.add_handler(CommandHandler("keywords", keywords_cmd))
    
    log.info("Starting Telegram Bot long-polling...")
    application.run_polling()
