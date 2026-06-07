# 🤖 Job Alert Bot

Scrapes LinkedIn, Naukri, Wellfound, and Internshala every 10 minutes and sends matching jobs to your Telegram instantly.

---

## ⚡ Setup (5 minutes)

### 1. Get your Telegram Bot Token
1. Open Telegram → search **@BotFather**
2. Send `/newbot`
3. Give it a name (e.g. `Faris Job Alert`) and username (e.g. `faris_jobalert_bot`)
4. Copy the **token** it gives you

### 2. Get your Telegram Chat ID
1. Open Telegram → search **@userinfobot**
2. Send `/start`
3. Copy your **Id** number

### 3. Add credentials to config.py
```python
TELEGRAM_BOT_TOKEN = "7312345678:AAFxxxxxxxxxxxxxxxx"
TELEGRAM_CHAT_ID   = "987654321"
```

### 4. Install dependencies
```bash
pip install requests beautifulsoup4 feedparser python-telegram-bot schedule
```

### 5. Run it
```bash
python main.py
```

You'll get a Telegram message confirming it's live. From then on, every matching job triggers an instant alert.

---

## 🎯 Customize keywords

Edit `config.py` → `KEYWORDS` list. Add or remove anything:
```python
KEYWORDS = [
    "NestJS", "Node.js", "React",
    "pgvector", "RAG",   # your AI differentiators
    ...
]
```

## ⏱ Change check interval
```python
CHECK_INTERVAL_MINUTES = 10  # default: every 10 min
```

---

## 🖥 Keep it running 24/7 (optional later)

**Option A — tmux (simplest)**
```bash
tmux new -s jobbot
python main.py
# Ctrl+B then D to detach
```

**Option B — Deploy to Render free tier**
- Push to GitHub
- Create a new Render "Background Worker" service
- Set build command: `pip install -r requirements.txt`
- Set start command: `python main.py`
- Done — runs free forever

---

## 📁 Files
```
job-scraper/
├── main.py          # scraper + Telegram sender
├── config.py        # your tokens + keywords
├── seen_jobs.json   # auto-created, tracks sent jobs
└── README.md
```
