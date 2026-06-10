import logging
import json
import httpx
import re
import asyncio
from config import GEMINI_API_KEY

log = logging.getLogger(__name__)

# Base API configuration
# Primary model as requested by the user, followed by suitable fallback models.
GEMINI_MODEL = "gemini-2.5-flash"
FALLBACK_MODELS = ["gemini-2.5-flash-lite", "gemini-2.5-pro", "gemini-3-pro", "gemini-2.0-flash", "gemini-1.5-pro", "gemini-1.5-flash", "Gemini 2.5 Flash Lite", "Gemini 2.5 Pro"]

async def call_gemini_api(prompt: str, json_mode: bool = False, enable_search: bool = False) -> str | None:
    """
    Sends an async POST request to the Gemini API with the given prompt.
    If json_mode is True, requests the model to output a JSON-formatted string.
    If enable_search is True, enables Google Search grounding tools.
    Implements exponential backoff retries on 429/5xx errors and falls back to
    alternative models if needed.
    """
    if not GEMINI_API_KEY or GEMINI_API_KEY == "YOUR_GEMINI_API_KEY":
        log.error("Gemini API key is not configured. Skipping Gemini call.")
        return None

    headers = {
        "Content-Type": "application/json",
        "X-goog-api-key": GEMINI_API_KEY
    }

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": prompt}
                ]
            }
        ]
    }

    if json_mode:
        payload["generationConfig"] = {
            "responseMimeType": "application/json"
        }

    if enable_search:
        payload["tools"] = [{"googleSearch": {}}]

    models_to_try = [GEMINI_MODEL] + FALLBACK_MODELS
    max_retries = 3
    base_delay = 2.0

    for model in models_to_try:
        api_url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        
        for attempt in range(max_retries):
            try:
                log.info(f"Sending request to Gemini model '{model}' (attempt {attempt + 1}/{max_retries})...")
                async with httpx.AsyncClient(timeout=120.0) as client:
                    response = await client.post(api_url, headers=headers, json=payload)
                    
                    if response.status_code == 429:
                        delay = base_delay * (2 ** attempt)
                        log.warning(f"Gemini API returned 429 (Too Many Requests) for model '{model}'. Retrying in {delay}s...")
                        await asyncio.sleep(delay)
                        continue
                        
                    response.raise_for_status()
                    response_data = response.json()

                    # Navigate response structure to extract generated text
                    candidates = response_data.get("candidates", [])
                    if candidates:
                        content = candidates[0].get("content", {})
                        parts = content.get("parts", [])
                        if parts:
                            return parts[0].get("text", "").strip()
                    
                    log.warning(f"Gemini response for model '{model}' did not contain candidates or parts.")
                    # If response parsed successfully but returned no parts, we don't need to retry this model, try next one
                    break
                    
            except httpx.HTTPStatusError as e:
                status_code = e.response.status_code
                if status_code in [500, 502, 503, 504]:
                    delay = base_delay * (2 ** attempt)
                    log.warning(f"Gemini API returned server error {status_code} for model '{model}'. Retrying in {delay}s...")
                    await asyncio.sleep(delay)
                    continue
                else:
                    log.error(f"Gemini API request failed for model '{model}' with status {status_code}: {e}")
                    # Non-retryable error (e.g. 400 Bad Request, 403 Forbidden)
                    break
            except httpx.RequestError as e:
                # Network-related issues (e.g. timeout, connection failure)
                delay = base_delay * (2 ** attempt)
                log.warning(f"Network error calling Gemini API for model '{model}': {e}. Retrying in {delay}s...")
                await asyncio.sleep(delay)
                continue
            except Exception as e:
                log.error(f"Unexpected error calling Gemini API for model '{model}': {e}")
                break
        else:
            log.warning(f"All {max_retries} attempts failed for model '{model}'.")

    log.error("All configured Gemini models failed or rate-limited.")
    return None


async def evaluate_job_fit(title: str, company: str, location: str, description: str = "") -> dict:
    """
    Evaluates a job listing against Faris's professional profile using Gemini.
    Returns a dictionary containing: score, reason, is_fit, detected_tech, and success.
    """
    default_result = {
        "score": 0.0,
        "reason": "AI evaluation skipped or failed.",
        "is_fit": False,
        "detected_tech": [],
        "success": False
    }

    # Clean description to prevent excessive token usage
    description_cleaned = description[:5000].strip() if description else "No description provided."

    prompt = f"""
Analyze the suitability of the following job listing for Faris, a mid-level Software Engineer with 2.5 years of experience.

Job Title: {title}
Company: {company}
Location: {location}
Description:
{description_cleaned}

---

Faris's Professional Profile & Core Target Stack:
1. Experience Level: Mid-level (~2.5 years experience). Too junior (interns) or too senior (lead/principal) are less ideal.
2. Core Backend Stack: NestJS, Node.js, Express.js, TypeScript, PostgreSQL, Prisma, MongoDB, Redis, BullMQ.
3. Core Frontend Stack: React, Next.js, TypeScript, Zustand, TanStack Query (React Query), Tailwind CSS.
4. Core DevOps/Cloud: Docker, AWS, GitHub Actions, CI/CD, NGINX.
5. High-Weight Differentiators: ZATCA integration (e-invoicing), OCR (Tesseract), multi-tenant SaaS architecture, Razorpay integration, microservices.
6. Preferred Locations: Kerala, India (Kochi, Kozhikode, Trivandrum), Infopark, Technopark, Remote, Dubai, UAE.
7. Minimum Salary Requirement: 3.0 LPA (Lakhs Per Annum) for India-based roles.

---

Task:
Evaluate this job listing. Output a JSON object containing exactly the following keys:
- "score": A float between 0.0 and 10.0 indicating matching strength. Take locations, tech stack, and experience level into account.
- "reason": A single, concise, professional 1-sentence insight explaining why this job fits or does not fit Faris's profile (e.g., "Great match for full stack remote roles featuring NestJS and Next.js, but salary details are not specified"). Do not exceed 1 sentence.
- "is_fit": A boolean indicating if the job score is 5.0 or above.
- "detected_tech": A list of technologies mentioned in the job description that overlap with Faris's stack.

Output format must be strictly JSON:
"""

    log.info(f"Running Gemini evaluation for job: '{title}' at '{company}'")
    response_text = await call_gemini_api(prompt, json_mode=True)
    if not response_text:
        return default_result

    try:
        result = json.loads(response_text)
        # Type and boundary verification
        score = float(result.get("score", 0.0))
        score = max(0.0, min(10.0, score))
        
        return {
            "score": score,
            "reason": str(result.get("reason", "No reason provided.")),
            "is_fit": bool(result.get("is_fit", score >= 5.0)),
            "detected_tech": list(result.get("detected_tech", [])),
            "success": True
        }
    except Exception as e:
        log.error(f"Error parsing Gemini evaluation response: {e}. Raw response: {response_text}")
        return default_result


def _local_parse_search_query(user_query: str) -> dict:
    """
    Extracts keywords and location using a local dictionary/pattern matching fallback.
    """
    try:
        from scorer import SCORING_PROFILE, LOCATION_SCORES
    except ImportError:
        # Fallback to hardcoded list if there's a circular import issue or scorer is missing
        SCORING_PROFILE = {
            "nestjs": 1.5, "nest.js": 1.5, "node.js": 1.3, "nodejs": 1.3, 
            "express.js": 1.2, "expressjs": 1.2, "typescript": 1.2, 
            "postgresql": 1.2, "prisma": 1.2, "mongodb": 1.2, "redis": 1.0,
            "next.js": 1.0, "nextjs": 1.0, "react": 1.0, "react.js": 1.0, 
            "zustand": 1.0, "tanstack": 1.0, "react query": 1.0, "redux": 0.8,
            "tailwind": 0.8, "tailwindcss": 0.8, "docker": 1.0, "aws": 1.0,
            "github actions": 1.0, "ci/cd": 1.0, "nginx": 0.8, "zatca": 2.0, 
            "ocr": 2.0, "tesseract": 2.0, "multi-tenant": 1.8, "multitenant": 1.8, 
            "bullmq": 1.8, "razorpay": 1.5, "microservices": 1.5
        }
        LOCATION_SCORES = {
            "kerala": 1.5, "kochi": 1.5, "kozhikode": 1.5, "trivandrum": 1.2,
            "thiruvananthapuram": 1.2, "infopark": 1.5, "technopark": 1.5,
            "remote": 1.2, "india": 0.8, "dubai": 1.0, "uae": 1.0
        }

    query_lower = user_query.lower()
    
    # Try matching SCORING_PROFILE keys
    keywords = []
    for tech in SCORING_PROFILE.keys():
        if tech in query_lower:
            keywords.append(tech)

    # Try matching location
    location = ""
    for loc in LOCATION_SCORES.keys():
        if loc in query_lower:
            location = loc
            break

    # If no tech keywords found, do simple string split as a broader fallback
    if not keywords:
        words = [w.strip(",.?!()\"'") for w in query_lower.split()]
        stopwords = {
            "job", "jobs", "in", "with", "remote", "developer", "engineer", 
            "for", "a", "an", "the", "and", "or", "of", "to", "need", "needed", "find", "search"
        }
        keywords = [w for w in words if len(w) > 2 and w not in stopwords and w not in LOCATION_SCORES]

    return {
        "keywords": list(set(keywords))[:5],
        "location": location,
        "success": False
    }


async def parse_search_query(user_query: str) -> dict:
    """
    Parses a natural language search query from the user (e.g., "remote NestJS developer jobs in Kochi")
    into structured search keywords and location filters.
    """
    prompt = f"""
You are an expert recruitment assistant parsing a job search request.
User query: "{user_query}"

Task:
Extract the primary search keywords/technologies and location preferences from the query.
Output a JSON object with:
- "keywords": An array of specific technology keywords or job titles to search for (e.g. ["nestjs", "react"]).
- "location": A string specifying location constraints (e.g. "kochi", "remote", "india"), or an empty string if none specified.

Output format must be strictly JSON:
"""

    log.info(f"Running Gemini query parser for query: '{user_query}'")
    response_text = await call_gemini_api(prompt, json_mode=True)
    if not response_text:
        return _local_parse_search_query(user_query)

    try:
        result = json.loads(response_text)
        return {
            "keywords": list(result.get("keywords", [])),
            "location": str(result.get("location", "")).strip(),
            "success": True
        }
    except Exception as e:
        log.error(f"Error parsing Gemini search query response: {e}. Raw response: {response_text}")
        return _local_parse_search_query(user_query)


def is_indeed_jk_placeholder(jk: str) -> bool:
    jk = str(jk).lower().strip()
    
    # Explicit list of common LLM synthetic job keys (including the one the user ran into)
    placeholders = [
        "c3b4a5d6e7f89012", "c3b4a5d6e7f890", "1234567890abcdef", 
        "abcdef1234567890", "0123456789abcdef", "abcdef0123456789", 
        "1234567890", "abcdef", "fedcba", "a1b2c3d4e5f6g7h8",
        "c3b4a5d6e7f8901"
    ]
    if any(p in jk for p in placeholders):
        return True
        
    # Check for simple sequences like abcdef, 123456, fedcba
    if any(seq in jk for seq in ["abcdef", "bcdefg", "cdefgh", "123456", "234567", "345678", "456789", "567890"]):
        return True
    if any(seq in jk for seq in ["fedcba", "edcba9", "654321", "987654"]):
        return True
        
    # Real random hex keys have a high diversity of characters
    # If the unique characters count in a 16-character string is very low (e.g. <= 4), it's highly likely fake
    if len(jk) >= 12 and len(set(jk)) <= 4:
        return True
        
    # Check repeating structures like "abab", "1212" etc., or same char repeating many times
    for char in set(jk):
        if jk.count(char) >= 6:
            return True
            
    return False


async def resolve_redirect_link(url: str) -> str:
    """
    Follows redirect of a Google/Vertex Search Grounding link to find the final destination URL.
    """
    from urllib.parse import urlparse, parse_qs
    url_str = str(url).strip()
    if not url_str:
        return url_str
        
    # Check if it looks like a redirect link
    if not any(domain in url_str.lower() for domain in ["google.com", "vertexaisearch.cloud.google.com", "googleusercontent.com"]):
        return url_str

    # Fast check: If it is a google.com/url redirect, we can parse the target 'q' query parameter directly
    try:
        parsed_url = urlparse(url_str)
        if "google.com" in parsed_url.netloc.lower():
            queries = parse_qs(parsed_url.query)
            if "q" in queries:
                extracted_url = queries["q"][0]
                if extracted_url.startswith("http"):
                    log.info(f"Parsed destination URL from query parameter: {extracted_url}")
                    return extracted_url
    except Exception as e:
        log.warning(f"Error parsing google redirect parameter: {e}")

    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    }
    
    try:
        log.info(f"Resolving search grounding redirect URL: {url_str}")
        # Use a client with follow_redirects=True to get the final target page.
        # Disable SSL verification to prevent SSL handshake issues.
        async with httpx.AsyncClient(timeout=12.0, follow_redirects=True, verify=False) as client:
            response = await client.get(url_str, headers=headers)
            resolved = str(response.url)
            log.info(f"Successfully resolved redirect to: {resolved}")
            return resolved
    except Exception as e:
        log.warning(f"Error resolving redirect for {url_str}: {e}")
        return url_str


async def scrape_indeed_jobs_via_gemini(keywords: list[str] = None) -> list[dict]:
    """
    Uses Gemini Search Grounding to find recently posted jobs on Indeed (indeed.com or in.indeed.com)
    using the active search keywords, based on the user's resume, and targeting preferred locations
    (Kochi, Kozhikode, and Remote).
    Only returns jobs posted within the last 3 days.
    Returns a list of parsed job dictionaries.
    """
    import os
    from datetime import datetime
    from urllib.parse import urlparse, parse_qs
    # Load resume details dynamically
    base_dir = os.path.dirname(os.path.abspath(__file__))
    resume_path = os.path.join(base_dir, "resume.txt")
    resume_context = ""
    if os.path.exists(resume_path):
        try:
            with open(resume_path, "r", encoding="utf-8") as f:
                resume_context = f.read().strip()
        except Exception as e:
            log.error(f"Error reading resume.txt: {e}")

    # Format keywords
    kw_str = ", ".join(keywords) if keywords else "NestJS, Node.js, React, TypeScript"

    prompt = f"""
You are a professional recruitment assistant.

Step 1: Use the web search tool to search Indeed (indeed.com or in.indeed.com) for active, recently posted jobs matching:
- Search Keywords: {kw_str}
- Locations: Kochi, Kozhikode, or Remote
- Candidate Profile (Resume Summary):
\"\"\"
{resume_context}
\"\"\"

Step 2: Extract details for up to 5 active listings that fit the candidate's experience level (mid-level, ~2.5 years) and skills based on the resume.
CRITICAL REQUIREMENT: Only extract jobs that were posted within the last 3 days (do not include jobs older than 3 days).

Step 3: Format the output as a valid JSON array of objects inside a markdown code block (```json ... ```).
Each object must have these keys exactly: "title", "company", "location", "url", "description", "posted_date".
- "posted_date" should be a date string (like "2026-06-09" or "2 days ago") indicating when the job was posted.
- "url" MUST be the exact, unmodified apply link found in the web search results. Under NO circumstances are you allowed to invent, estimate, guess, or construct Indeed URLs using placeholder job keys (like jk=c3b4a5d6e7f89012 or similar). If a real URL is not available in the search results, DO NOT include the job listing at all.

Do not output any introductory or conversational text, only the markdown JSON code block.
"""
    
    log.info("Querying Gemini Search Grounding for Indeed jobs...")
    response_text = await call_gemini_api(prompt, json_mode=False, enable_search=True)
    if not response_text:
        return []
        
    try:
        match = re.search(r"```json\s*(.*?)\s*```", response_text, re.DOTALL)
        if match:
            json_str = match.group(1).strip()
        else:
            start = response_text.find("[")
            end = response_text.rfind("]")
            if start != -1 and end != -1:
                json_str = response_text[start:end+1]
            else:
                json_str = response_text.strip()
                
        jobs_data = json.loads(json_str)
        if isinstance(jobs_data, list):
            parsed_jobs = []
            for item in jobs_data:
                if not isinstance(item, dict):
                    continue
                
                # Check for Indeed placeholder URL
                link = str(item.get("url", item.get("link", ""))).strip()
                
                # Resolve google grounding redirect URLs
                if "google.com" in link.lower() or "googleusercontent.com" in link.lower() or "vertexaisearch.cloud.google.com" in link.lower():
                    link = await resolve_redirect_link(link)
                
                if "indeed.com" in link.lower():
                    try:
                        parsed_link = urlparse(link)
                        queries = parse_qs(parsed_link.query)
                        jk_val = queries.get("jk", [None])[0]
                        if jk_val:
                            if is_indeed_jk_placeholder(jk_val):
                                log.info(f"Indeed job skipped (placeholder jk '{jk_val}'): {item.get('title')}")
                                continue
                        else:
                            if "viewjob" in parsed_link.path or "clk" in parsed_link.path:
                                log.info(f"Indeed job skipped (no jk in indeed URL): {item.get('title')}")
                                continue
                    except Exception as e:
                        log.warning(f"Error parsing indeed link query: {e}")

                # Programmatic check for posted_date age
                posted_date_str = str(item.get("posted_date", "")).strip().lower()
                if posted_date_str:
                    # Filter out older ones based on text cues
                    if any(x in posted_date_str for x in ["week", "month", "year"]):
                        log.info(f"Indeed job skipped (older than 3 days by text): {item.get('title')} - Posted: {posted_date_str}")
                        continue
                    
                    # Extract days ago if relative
                    days_match = re.search(r"(\d+)\s+day", posted_date_str)
                    if days_match:
                        days = int(days_match.group(1))
                        if days > 3:
                            log.info(f"Indeed job skipped (older than 3 days by day count): {item.get('title')} - Posted: {posted_date_str}")
                            continue
                            
                    # Parse YYYY-MM-DD
                    date_match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", posted_date_str)
                    if date_match:
                        try:
                            job_date = datetime.strptime(date_match.group(0), "%Y-%m-%d").date()
                            days_ago = (datetime.now().date() - job_date).days
                            if days_ago > 3:
                                log.info(f"Indeed job skipped (older than 3 days by date parsing): {item.get('title')} - Posted: {posted_date_str}")
                                continue
                        except Exception:
                            pass
                
                parsed_jobs.append({
                    "title": str(item.get("title", "N/A")),
                    "company": str(item.get("company", "N/A")),
                    "location": str(item.get("location", "N/A")),
                    "link": link,
                    "source": "Indeed",
                    "salary": None,
                    "description": str(item.get("description", ""))
                })
            log.info(f"Gemini Indeed search successfully retrieved {len(parsed_jobs)} jobs.")
            return parsed_jobs
            
        log.warning("Parsed Indeed jobs data is not a list.")
        return []
    except Exception as e:
        log.error(f"Error parsing Gemini Indeed search output: {e}. Raw response: {response_text}")
        return []

