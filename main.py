import feedparser
import requests
import schedule
import time
import json
import os
import logging
from datetime import datetime
from bs4 import BeautifulSoup
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, KEYWORDS, SEEN_JOBS_FILE, CHECK_INTERVAL_MINUTES
from scorer import score_job, MIN_SCORE

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger(__name__)


# ─── Seen jobs tracker ────────────────────────────────────────────────────────

def load_seen_jobs():
    if os.path.exists(SEEN_JOBS_FILE):
        with open(SEEN_JOBS_FILE, "r") as f:
            return set(json.load(f))
    return set()

def save_seen_jobs(seen):
    with open(SEEN_JOBS_FILE, "w") as f:
        json.dump(list(seen), f)


# ─── Telegram sender ──────────────────────────────────────────────────────────

def send_telegram(message: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    try:
        r = requests.post(url, json=payload, timeout=10)
        r.raise_for_status()
    except Exception as e:
        log.error(f"Telegram send failed: {e}")


def format_job_message(title, company, location, link, source, score_data: dict):
    source_emoji = {
        "LinkedIn": "💼",
        "Naukri": "🟠",
        "Wellfound": "🚀",
        "Internshala": "🎓"
    }.get(source, "📌")

    score = score_data["score"]
    label = score_data["label"]
    matched = score_data["matched_keywords"]

    # Score bar (visual 10-block bar)
    filled = round(score)
    bar = "█" * filled + "░" * (10 - filled)

    keywords_line = ""
    if matched:
        keywords_line = f"🔑 <code>{', '.join(matched)}</code>\n"

    return (
        f"{source_emoji} <b>New Job Alert — {source}</b>\n\n"
        f"🏷 <b>{title}</b>\n"
        f"🏢 {company}\n"
        f"📍 {location}\n\n"
        f"{label}\n"
        f"⭐ <b>{score}/10</b>  <code>{bar}</code>\n"
        f"{keywords_line}\n"
        f"🔗 <a href='{link}'>Apply Now</a>\n"
        f"⏰ {datetime.now().strftime('%d %b %Y, %I:%M %p')}"
    )


# ─── Keyword filter ───────────────────────────────────────────────────────────

def matches_keywords(text: str) -> bool:
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in KEYWORDS)


# ─── LinkedIn (RSS) ───────────────────────────────────────────────────────────

LINKEDIN_RSS_URLS = [
    "https://www.linkedin.com/jobs/search/?keywords=NestJS+developer&location=Kerala&f_TPR=r3600&f_JT=F&start=0",
    "https://www.linkedin.com/jobs/search/?keywords=Node.js+React+developer&location=India&f_WT=2&f_TPR=r3600&start=0",
    "https://www.linkedin.com/jobs/search/?keywords=full+stack+developer+NestJS&location=India&f_WT=2&f_TPR=r3600&start=0",
]

def scrape_linkedin(seen: set) -> list:
    found = []
    headers = {"User-Agent": "Mozilla/5.0 (compatible; JobBot/1.0)"}

    for url in LINKEDIN_RSS_URLS:
        try:
            r = requests.get(url, headers=headers, timeout=15)
            soup = BeautifulSoup(r.text, "html.parser")
            cards = soup.select("div.base-card")

            for card in cards[:15]:
                try:
                    title_el = card.select_one("h3.base-search-card__title")
                    company_el = card.select_one("h4.base-search-card__subtitle")
                    location_el = card.select_one("span.job-search-card__location")
                    link_el = card.select_one("a.base-card__full-link")

                    title = title_el.text.strip() if title_el else "N/A"
                    company = company_el.text.strip() if company_el else "N/A"
                    location = location_el.text.strip() if location_el else "N/A"
                    link = link_el["href"].split("?")[0] if link_el else ""

                    job_id = f"linkedin_{link}"
                    if job_id in seen:
                        continue
                    if not matches_keywords(title):
                        continue

                    seen.add(job_id)
                    found.append({
                        "title": title, "company": company,
                        "location": location, "link": link, "source": "LinkedIn"
                    })
                except Exception:
                    continue

        except Exception as e:
            log.warning(f"LinkedIn scrape error: {e}")

    return found


# ─── Naukri ───────────────────────────────────────────────────────────────────

NAUKRI_SEARCHES = [
    ("NestJS developer", "Kerala"),
    ("Node.js React full stack", "Kerala"),
    ("full stack developer", "Kochi"),
    ("NestJS Node.js", "India"),
]

def scrape_naukri(seen: set) -> list:
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
            data = r.json()
            jobs = data.get("jobDetails", [])

            for job in jobs:
                title = job.get("title", "N/A")
                company = job.get("companyName", "N/A")
                location_str = ", ".join(job.get("placeholders", [{}])[0].get("label", "").split(",")[:2]) if job.get("placeholders") else "India"
                link = job.get("jdURL", "https://www.naukri.com")
                job_id = f"naukri_{job.get('jobId', link)}"

                if job_id in seen:
                    continue
                if not matches_keywords(title):
                    continue

                seen.add(job_id)
                found.append({
                    "title": title, "company": company,
                    "location": location_str, "link": link, "source": "Naukri"
                })

        except Exception as e:
            log.warning(f"Naukri scrape error ({keyword}): {e}")

    return found


# ─── Wellfound ────────────────────────────────────────────────────────────────

WELLFOUND_SEARCHES = [
    "nestjs",
    "node-js-react",
    "full-stack",
]

def scrape_wellfound(seen: set) -> list:
    found = []
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "text/html,application/xhtml+xml",
    }

    for role in WELLFOUND_SEARCHES:
        try:
            url = f"https://wellfound.com/role/r/{role}"
            r = requests.get(url, headers=headers, timeout=15)
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
                        "location": "Remote / India", "link": link, "source": "Wellfound"
                    })
                except Exception:
                    continue

        except Exception as e:
            log.warning(f"Wellfound scrape error ({role}): {e}")

    return found


# ─── Internshala ──────────────────────────────────────────────────────────────

INTERNSHALA_SEARCHES = [
    "nodejs",
    "nestjs",
    "react-developer",
    "full-stack-development",
]

def scrape_internshala(seen: set) -> list:
    found = []
    headers = {"User-Agent": "Mozilla/5.0"}

    for category in INTERNSHALA_SEARCHES:
        try:
            url = f"https://internshala.com/jobs/{category}-jobs"
            r = requests.get(url, headers=headers, timeout=15)
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
                        "location": location, "link": link, "source": "Internshala"
                    })
                except Exception:
                    continue

        except Exception as e:
            log.warning(f"Internshala scrape error ({category}): {e}")

    return found


# ─── Main loop ────────────────────────────────────────────────────────────────

def run_scraper():
    log.info("🔍 Running job scraper...")
    seen = load_seen_jobs()
    all_jobs = []

    all_jobs += scrape_linkedin(seen)
    all_jobs += scrape_naukri(seen)
    all_jobs += scrape_wellfound(seen)
    all_jobs += scrape_internshala(seen)

    save_seen_jobs(seen)

    # Score and filter
    scored_jobs = []
    for job in all_jobs:
        score_data = score_job(
            title=job["title"],
            company=job["company"],
            location=job["location"],
        )
        if score_data["send"]:
            scored_jobs.append((job, score_data))

    # Sort by score descending — best jobs first
    scored_jobs.sort(key=lambda x: x[1]["score"], reverse=True)

    if scored_jobs:
        log.info(f"✅ {len(scored_jobs)} job(s) passed scoring (out of {len(all_jobs)} found). Sending to Telegram...")
        for job, score_data in scored_jobs:
            msg = format_job_message(
                job["title"], job["company"],
                job["location"], job["link"],
                job["source"], score_data
            )
            send_telegram(msg)
            time.sleep(1)
    elif all_jobs:
        log.info(f"🚫 {len(all_jobs)} job(s) found but all scored below {MIN_SCORE}/10 — skipped.")
    else:
        log.info("😴 No new matching jobs found.")


if __name__ == "__main__":
    log.info("🚀 Job Alert Bot started!")
    send_telegram("🤖 <b>Job Alert Bot is now running!</b>\n\nI'll ping you the moment a matching job drops on LinkedIn, Naukri, Wellfound, or Internshala.\n\n<i>Checking every 10 minutes...</i>")

    run_scraper()  # run immediately on start

    schedule.every(CHECK_INTERVAL_MINUTES).minutes.do(run_scraper)

    while True:
        schedule.run_pending()
        time.sleep(30)
