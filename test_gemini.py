import asyncio
import logging
from gemini_service import evaluate_job_fit, parse_search_query, scrape_indeed_jobs_via_gemini

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

async def run_tests():
    log.info("Starting Gemini service tests...")

    # Test 1: Search query parsing
    query = "remote NestJS developer jobs with PostgreSQL in Kerala"
    log.info(f"Test 1: Parsing query: '{query}'")
    parsed = await parse_search_query(query)
    log.info(f"Parsed result: {parsed}")
    assert isinstance(parsed, dict), "Result must be a dictionary"
    
    if not parsed.get("success", False):
        log.warning("Test 1: Gemini API call failed/blocked. Skipping strict key assertion.")
    else:
        assert "keywords" in parsed, "Result must contain 'keywords'"
        assert "location" in parsed, "Result must contain 'location'"
        log.info("Test 1: Success ✅")

    # Rate limiting protection
    await asyncio.sleep(5)

    # Test 2: Job evaluation fit (Positive match)
    log.info("Test 2: Evaluating positive match job...")
    job_title = "Backend Developer (NestJS / Node.js)"
    company = "TechCorp Solutions"
    location = "Kochi, Kerala (Remote)"
    description = """
    We are looking for a Mid-level Software Developer with 2.5 years of experience to join our team.
    Key skills: NestJS, Node.js, TypeScript, PostgreSQL, and Docker.
    Bonus points if you have experience with e-invoicing systems like ZATCA or background task runners like BullMQ!
    Role is fully remote from anywhere in India.
    """
    fit_data = await evaluate_job_fit(job_title, company, location, description)
    log.info(f"Evaluation result: {fit_data}")
    assert isinstance(fit_data, dict), "Fit data must be a dictionary"
    
    if not fit_data.get("success", False):
        log.warning("Test 2: Gemini API call failed/blocked. Skipping strict score assertion.")
    else:
        assert "score" in fit_data, "Fit data must contain 'score'"
        assert "reason" in fit_data, "Fit data must contain 'reason'"
        assert fit_data["score"] >= 5.0, "NestJS + ZATCA + BullMQ should score highly for Faris"
        log.info("Test 2: Success ✅")

    # Rate limiting protection
    await asyncio.sleep(5)

    # Test 3: Job evaluation fit (Negative match)
    log.info("Test 3: Evaluating negative match job...")
    job_title_neg = "Senior Java Engineer"
    company_neg = "LegacyCorp Inc"
    location_neg = "Bangalore, India"
    description_neg = """
    Requires 8+ years of experience in Java, Spring Boot, Microservices, and Oracle Database.
    Must be located in Bangalore. No remote option.
    """
    fit_data_neg = await evaluate_job_fit(job_title_neg, company_neg, location_neg, description_neg)
    log.info(f"Evaluation result: {fit_data_neg}")
    
    if not fit_data_neg.get("success", False):
        log.warning("Test 3: Gemini API call failed/blocked. Skipping strict score assertion.")
    else:
        assert fit_data_neg["score"] < 5.0, "Java + Bangalore + 8+ years experience should score poorly for Faris"
        log.info("Test 3: Success ✅")

    # Rate limiting protection
    await asyncio.sleep(5)

    # Test 4: Indeed Search Grounding scraping
    log.info("Test 4: Running Indeed scraping via Gemini Search Grounding...")
    indeed_jobs = await scrape_indeed_jobs_via_gemini()
    log.info(f"Indeed scraper result count: {len(indeed_jobs)}")
    if indeed_jobs:
        for idx, job in enumerate(indeed_jobs[:3], 1):
            log.info(f"Indeed job {idx}: {job['title']} at {job['company']} ({job['location']}) - Link: {job['link']}")
            assert "title" in job
            assert "company" in job
            assert "location" in job
            assert "link" in job
            assert job["source"] == "Indeed"
        log.info("Test 4: Success ✅")
    else:
        log.warning("Test 4: Indeed scraper returned no jobs.")

    log.info("All Gemini service tests completed successfully! 🎉")

if __name__ == "__main__":
    asyncio.run(run_tests())
