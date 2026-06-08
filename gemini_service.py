import logging
import json
import httpx
import re
from config import GEMINI_API_KEY

log = logging.getLogger(__name__)

# Base API configuration
# Using the gemini-2.5-flash model as requested by the user.
GEMINI_MODEL = "gemini-2.5-flash"
API_URL = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

async def call_gemini_api(prompt: str, json_mode: bool = False, enable_search: bool = False) -> str | None:
    """
    Sends an async POST request to the Gemini API with the given prompt.
    If json_mode is True, requests the model to output a JSON-formatted string.
    If enable_search is True, enables Google Search grounding tools.
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

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(API_URL, headers=headers, json=payload)
            response.raise_for_status()
            response_data = response.json()

            # Navigate response structure to extract generated text
            candidates = response_data.get("candidates", [])
            if candidates:
                content = candidates[0].get("content", {})
                parts = content.get("parts", [])
                if parts:
                    return parts[0].get("text", "").strip()
            
            log.warning("Gemini response did not contain candidates or parts.")
            return None
    except Exception as e:
        log.error(f"Gemini API request failed: {e}")
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


async def parse_search_query(user_query: str) -> dict:
    """
    Parses a natural language search query from the user (e.g., "remote NestJS developer jobs in Kochi")
    into structured search keywords and location filters.
    """
    default_result = {
        "keywords": [],
        "location": "",
        "success": False
    }

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
        return default_result

    try:
        result = json.loads(response_text)
        return {
            "keywords": list(result.get("keywords", [])),
            "location": str(result.get("location", "")).strip(),
            "success": True
        }
    except Exception as e:
        log.error(f"Error parsing Gemini search query response: {e}. Raw response: {response_text}")
        return default_result


async def scrape_indeed_jobs_via_gemini() -> list[dict]:
    """
    Uses Gemini Search Grounding to find recently posted remote or Kerala-based
    NestJS, NodeJS, or React developer jobs on Indeed.
    Returns a list of parsed job dictionaries.
    """
    prompt = (
        "You are a professional recruitment assistant. "
        "Step 1: Use the web search tool to search Indeed (indeed.com or indeed.co.in) for active, recently posted remote jobs or jobs located in Kerala, India matching the keywords: NestJS, NodeJS, or React developer. "
        "Step 2: Extract details for up to 5 active listings, including job title, company name, location, source URL/link, and a brief description/tech stack. "
        "Step 3: Format the output as a valid JSON array of objects inside a markdown code block (```json ... ```). "
        "Each object must have these keys exactly: \"title\", \"company\", \"location\", \"url\", \"description\". "
        "Do not output any introductory or conversational text, only the markdown JSON code block."
    )
    
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
                parsed_jobs.append({
                    "title": str(item.get("title", "N/A")),
                    "company": str(item.get("company", "N/A")),
                    "location": str(item.get("location", "N/A")),
                    "link": str(item.get("url", item.get("link", ""))),
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

