# ─── scorer.py ────────────────────────────────────────────────────────────────
# Scores jobs 0–10 based on how well they match Faris's stack and target.
# Higher weight = rarer skill = bigger score boost when matched.

SCORING_PROFILE = {
    # ── Tier 1: Core Specialties & Differentiators (weight 1.8 - 2.0) ──────────
    # These match Faris's unique project experiences (Accorelabs & ShopSure integrations)
    "zatca":            2.0,
    "ocr":              2.0,
    "tesseract":        2.0,
    "multi-tenant":     1.8,
    "multitenant":      1.8,
    "bullmq":           1.8,
    "razorpay":         1.5,
    "microservices":    1.5,

    # ── Tier 2: Primary Backend Stack (weight 1.2 - 1.5) ───────────────────────
    "nestjs":           1.5,
    "nest.js":          1.5,
    "node.js":          1.3,
    "nodejs":           1.3,
    "express.js":       1.2,
    "expressjs":        1.2,
    "typescript":       1.2,
    "postgresql":       1.2,
    "prisma":           1.2,
    "mongodb":          1.2,
    "redis":            1.0,

    # ── Tier 3: Frontend Stack (weight 0.8 - 1.0) ─────────────────────────────
    "next.js":          1.0,
    "nextjs":           1.0,
    "react":            1.0,
    "react.js":         1.0,
    "zustand":          1.0,
    "tanstack":         1.0,
    "react query":      1.0,
    "redux":            0.8,
    "tailwind":         0.8,
    "tailwindcss":      0.8,

    # ── Tier 4: DevOps & Cloud (weight 0.8 - 1.0) ─────────────────────────────
    "github actions":   1.0,
    "ci/cd":            1.0,
    "docker":           1.0,
    "aws":              1.0,
    "nginx":            0.8,

    # ── Tier 5: General Roles (weight 0.3 - 0.5) ──────────────────────────────
    "full stack":       0.5,
    "full-stack":       0.5,
    "backend":          0.5,
    "mern":             0.5,
    "software engineer":0.3,
    "software developer":0.3,
}

# Location scoring bonus
LOCATION_SCORES = {
    "kerala":       1.5,   # top priority
    "kochi":        1.5,
    "kozhikode":    1.5,
    "trivandrum":   1.2,
    "thiruvananthapuram": 1.2,
    "infopark":     1.5,
    "technopark":   1.5,
    "remote":       1.2,
    "india":        0.8,
    "dubai":        1.0,
    "uae":          1.0,
}

# Seniority bonus (mid-level is ideal for 2.5 yrs exp)
SENIORITY_SCORES = {
    "senior":       0.5,
    "mid":          1.0,
    "mid-level":    1.0,
    "intermediate": 1.0,
    "junior":      -0.5,   # slight penalty
    "lead":         0.3,
    "principal":   -0.5,   # likely out of range
    "intern":      -2.0,   # hard skip
    "internship":  -2.0,
}

# Score threshold — jobs below this won't be sent
MIN_SCORE = 3.0

# Score above this gets 🔥 Hot Match label
HOT_MATCH_THRESHOLD = 7.0


def score_job(title: str, company: str, location: str, description: str = "") -> dict:
    """
    Returns {
        score: float (0-10),
        matched_keywords: list[str],
        label: str,
        send: bool
    }
    """
    combined = f"{title} {description}".lower()
    location_lower = location.lower()
    title_lower = title.lower()

    raw_score = 0.0
    matched = []

    # Keyword scoring
    for keyword, weight in SCORING_PROFILE.items():
        if keyword in combined:
            raw_score += weight
            matched.append(keyword)

    # Location bonus
    for loc, bonus in LOCATION_SCORES.items():
        if loc in location_lower:
            raw_score += bonus
            break  # only apply one location bonus

    # Seniority modifier
    for level, modifier in SENIORITY_SCORES.items():
        if level in title_lower:
            raw_score += modifier
            break

    # Normalize to 0–10
    max_possible = 8.0  # tuned ceiling — realistic top score for a single job title + location
    score = round(min((raw_score / max_possible) * 10, 10.0), 1)
    score = max(score, 0.0)

    # Label
    if score >= HOT_MATCH_THRESHOLD:
        label = "🔥 Hot Match"
    elif score >= 5.0:
        label = "✅ Good Match"
    elif score >= MIN_SCORE:
        label = "👀 Possible Match"
    else:
        label = "❌ Weak Match"

    return {
        "score": score,
        "matched_keywords": matched[:6],  # top 6 to keep message clean
        "label": label,
        "send": score >= MIN_SCORE
    }
