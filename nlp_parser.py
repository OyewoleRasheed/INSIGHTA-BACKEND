"""
nlp_parser.py — lightweight NLP query parser for profile search.
No external ML dependencies; uses regex + keyword matching.
"""
import re

GENDER_KEYWORDS = {
    "male": "male", "men": "male", "man": "male", "boy": "male",
    "female": "female", "women": "female", "woman": "female", "girl": "female",
}

AGE_GROUP_KEYWORDS = {
    "child": "child", "children": "child", "kid": "child", "kids": "child",
    "teenager": "teenager", "teen": "teenager", "teens": "teenager", "adolescent": "teenager",
    "adult": "adult", "adults": "adult", "grown": "adult",
    "senior": "senior", "seniors": "senior", "elderly": "senior", "old": "senior",
}

def parse_query(query: str) -> dict:
    """
    Parse a natural language query into filter parameters.

    Examples:
        "show me adult males from NG"         → {gender: male, age_group: adult, country_id: NG}
        "female seniors older than 60"        → {gender: female, age_group: senior, min_age: 60}
        "teenagers with high gender confidence" → {age_group: teenager, min_gender_probability: 0.9}
    """
    q = query.lower().strip()
    filters = {}

    # Gender
    for kw, val in GENDER_KEYWORDS.items():
        if re.search(rf'\b{kw}\b', q):
            filters["gender"] = val
            break

    # Age group
    for kw, val in AGE_GROUP_KEYWORDS.items():
        if re.search(rf'\b{kw}\b', q):
            filters["age_group"] = val
            break

    # Country code (2-letter ISO, e.g. "from NG" or "in US")
    country_match = re.search(r'\b(?:from|in|country[:\s]+)([a-z]{2})\b', q)
    if country_match:
        filters["country_id"] = country_match.group(1).upper()

    # min_age patterns: "older than 30", "over 30", "age > 30", "above 30"
    min_age_match = re.search(r'\b(?:older than|over|above|age\s*[>>=]+)\s*(\d+)', q)
    if min_age_match:
        filters["min_age"] = int(min_age_match.group(1))

    # max_age patterns: "younger than 30", "under 30", "below 30"
    max_age_match = re.search(r'\b(?:younger than|under|below|age\s*[<<=]+)\s*(\d+)', q)
    if max_age_match:
        filters["max_age"] = int(max_age_match.group(1))

    # High confidence gender
    if re.search(r'\b(high|strong|confident)\b.{0,15}\bgender\b', q):
        filters["min_gender_probability"] = 0.9

    # High confidence country
    if re.search(r'\b(high|strong|confident)\b.{0,15}\bcountry\b', q):
        filters["min_country_probability"] = 0.9

    return filters