import asyncio
import logging
import os
import re
import requests
import html as html_lib
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from gemini_service import call_gemini_api

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# Helper to split messages for Telegram character limits safely
def split_message_by_lines(text: str, limit: int = 4000) -> list[str]:
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

def clean_html_for_telegram(text: str) -> str:
    # 1. Normalize linebreaks and paragraphs
    text = re.sub(r"<br\s*/?>", "\n", text)
    text = re.sub(r"</?p>", "\n", text)
    
    # 2. Extract allowed tags: b, strong, i, em, code, pre, a (with href)
    # We will temporarily replace them with unique placeholders
    placeholders = []
    
    def tag_replacer(match):
        tag_content = match.group(0)
        # Match the tag name in group 1
        tag_name_match = re.match(r"</?([a-zA-Z1-9]+)", tag_content)
        if not tag_name_match:
            return ""
        tag_name = tag_name_match.group(1).lower()
        
        # Check if it is a supported tag
        if tag_name in ["b", "strong", "i", "em", "code", "pre"] or (tag_name == "a" and "href=" in tag_content) or tag_content == "</a>":
            placeholder = f"___TAG_PLACEHOLDER_{len(placeholders)}___"
            placeholders.append((placeholder, tag_content))
            return placeholder
        # Otherwise, remove the unsupported tag
        return ""

    # Replace tags with placeholders
    text_with_placeholders = re.sub(r"<[^>]+>", tag_replacer, text)
    
    # 3. HTML escape all remaining text to protect against raw & or < or >
    escaped_text = html_lib.escape(text_with_placeholders, quote=False)
    
    # 4. Restore the allowed tags from placeholders
    for placeholder, original_tag in placeholders:
        escaped_text = escaped_text.replace(placeholder, original_tag)
        
    return escaped_text.strip()

# Helper to send message to Telegram via API POST request
def send_telegram_message(message: str):
    cleaned = clean_html_for_telegram(message)
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": cleaned,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    }
    r = requests.post(url, json=payload)
    if r.status_code != 200:
        log.error(f"Telegram API Error: {r.status_code} - {r.text}")
        # Try sending as plain text fallback if HTML parse fails
        payload["parse_mode"] = ""
        payload["text"] = re.sub(r"<[^>]+>", "", cleaned)
        r_fallback = requests.post(url, json=payload)
        if r_fallback.status_code != 200:
            log.error(f"Telegram Fallback Error: {r_fallback.status_code} - {r_fallback.text}")
        else:
            log.info("Successfully sent fallback plain text Telegram message.")
    else:
        log.info("Successfully sent HTML Telegram message.")

async def main():
    resume_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "resume.txt")
    if not os.path.exists(resume_path):
        log.error(f"Resume file not found at {resume_path}")
        return

    with open(resume_path, "r") as f:
        resume_text = f.read()

    prompt = f"""
Act as a senior career strategist and recruiter.
Analyze my resume first and identify my strongest skills, role fit, experience level, and best job titles.

Resume:
\"\"\"
{resume_text}
\"\"\"

Task:
1. Search for current openings for roles like:
- Full Stack Developer
- MERN Stack Developer
- Frontend Developer
- React Developer
- Next.js Developer
- UI/UX Developer
- Software Engineer

Target location: Kochi, Kozhikode, and Remote roles are acceptable.
Return ONLY jobs that match experience level (mid-level, 2+ years), were posted recently, are relevant to the stack, and have a direct application link.

2. Format the response as a valid Telegram-compliant HTML message.
CRITICAL: Telegram HTML only supports a very limited set of tags.
- Do NOT use: <h1>, <h2>, <h3>, <ul>, <li>, <table>, <tr>, <td>, <p>, or markdown headers (like #, ##).
- Use <b>Section Title</b> for headers.
- Use plain text bullet characters (like • or -) and manual newlines for lists.
- Format the job search results as a text-based ASCII table OR a structured preformatted list inside a <pre>...</pre> block, or a beautifully formatted HTML list using bold tags like:
  <b>[Job Title]</b> at <i>[Company]</i>
  📍 Location: [Location]
  💼 Experience Needed: [Experience]
  ⭐ Match Score: [Score]/10
  💡 Why It Fits: [Why]
  🔗 <a href="[Link]">Apply Link</a> (Source: [Source])
  (Ensure you output the required fields: Job Title, Company, Location, Experience Needed, Match Score, Why It Fits, Apply Link, Source)

3. After the job results, provide:
- Resume keywords to add (e.g. using <code>keyword</code> tags)
- Missing skills to learn
- A priority ranking of the best jobs to apply to first

Please verify that all HTML tags are closed correctly and no unsupported tags are used.
"""

    log.info("Querying Gemini API with search grounding...")
    response_text = await call_gemini_api(prompt, json_mode=False, enable_search=True)
    if not response_text:
        log.error("Failed to get response from Gemini API.")
        return

    log.info("Processing Gemini response and sending to Telegram...")
    
    # Split the message to fit Telegram limits
    chunks = split_message_by_lines(response_text)
    
    for idx, chunk in enumerate(chunks):
        log.info(f"Sending chunk {idx+1}/{len(chunks)}...")
        send_telegram_message(chunk)
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
