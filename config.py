# ─── Telegram Setup ───────────────────────────────────────────────────────────
# Configuration is loaded from the environment or .env file for security.

import os

# Load .env file manually if it exists to avoid external dependencies
BASE_DIR = os.path.dirname(os.path.abspath(__file__))  # FIXED: define project base directory
env_path = os.path.join(BASE_DIR, ".env")  # FIXED: locate .env relative to script directory
if os.path.exists(env_path):  # FIXED: path portability
    try:  # FIXED: wrap .env load in try-except block to handle file read corruption/permission errors
        with open(env_path, "r") as f:  # FIXED: path portability
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, val = line.split("=", 1)
                    os.environ[key.strip()] = val.strip()
    except Exception as e:  # FIXED: catch file reading exceptions gracefully
        print(f"Error loading .env file: {e}")  # FIXED: print warning on fail and continue

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "YOUR_TELEGRAM_CHAT_ID")



# ─── Keywords (job title must match at least one) ─────────────────────────────
KEYWORDS = [
    # Core stack
    "NestJS", "Nest.js",
    "Node.js", "NodeJS",
    "React", "Next.js", "NextJS",
    "TypeScript",
    "Full Stack", "Full-Stack",
    "MERN",

    # Backend / DevOps & Integrations
    "PostgreSQL", "Prisma", "MongoDB", "Redis", "BullMQ",
    "Docker", "AWS", "GitHub Actions", "CI/CD",

    # Specific Domain / Practices
    "Multi-Tenant", "SaaS", "Microservices", "OCR", "ZATCA",

    # General roles
    "Backend Developer", "Backend Engineer",
    "Software Engineer", "Software Developer",
]


# ─── Scraper settings ─────────────────────────────────────────────────────────
CHECK_INTERVAL_MINUTES = 10       # how often to poll (10 min is safe, don't go lower)
SEEN_JOBS_FILE = os.path.join(BASE_DIR, "seen_jobs.json")  # FIXED: use absolute path for seen_jobs.json


# ─── Indeed India Scraper settings ──────────────────────────────────────────
# Indeed RSS queries: list of (keyword, location)
INDEED_SEARCHES = [
    ("NestJS", "Kerala"),
    ("NodeJS", "India"),
    ("React", "India"),
    ("TypeScript", "India"),
]

# ─── Glassdoor Scraper settings ──────────────────────────────────────────────
# List of keywords to search on Glassdoor India
GLASSDOOR_KEYWORDS = [
    "nestjs",
    "nodejs",
    "react",
    "typescript",
]

# ─── Cutshort Scraper settings ────────────────────────────────────────────────
# List of keywords to search on Cutshort
CUTSHORT_KEYWORDS = [
    "nestjs",
    "nodejs",
    "react",
    "typescript",
]

# ─── Infopark Scraper settings ────────────────────────────────────────────────
INFOPARK_SEARCH_URL = "https://infopark.in/companies/job-search"
