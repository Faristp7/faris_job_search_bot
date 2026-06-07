# ─── Telegram Setup ───────────────────────────────────────────────────────────
# Configuration is loaded from the environment or .env file for security.

import os

# Load .env file manually if it exists to avoid external dependencies
if os.path.exists(".env"):
    with open(".env", "r") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                os.environ[key.strip()] = val.strip()

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
SEEN_JOBS_FILE = "seen_jobs.json" # tracks already-notified jobs
