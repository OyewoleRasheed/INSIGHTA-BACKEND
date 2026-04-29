import os
import requests
import jwt
from datetime import datetime, timezone, timedelta
import uuid6
from database import get_db_connection

# 1. Configuration matching your running app
BASE_URL = "http://localhost:5000/api/v1"
JWT_SECRET = os.environ.get("JWT_SECRET", "super-secret-change-in-prod") # Must match what you started the app with

# 2. Inject a fake Admin user into the database
admin_id = str(uuid6.uuid7())
conn = get_db_connection()
# Use INSERT OR IGNORE just in case you run this multiple times
conn.execute(
    "INSERT OR IGNORE INTO users (id, github_id, username, role, created_at) VALUES (?, ?, ?, ?, ?)",
    (admin_id, "fake_gh_999", "TestAdmin", "admin", datetime.now(timezone.utc).isoformat())
)
conn.commit()
conn.close()

# 3. Generate a valid Admin JWT
token = jwt.encode({
    "user_id": admin_id, 
    "role": "admin",
    "exp": datetime.now(timezone.utc) + timedelta(minutes=15)
}, JWT_SECRET, algorithm="HS256")

headers = {
    "Authorization": f"Bearer {token}",
    "Content-Type": "application/json",
    "X-Client-Type": "cli"
}

print("✅ Token generated. Testing endpoints...\n")

# --- TEST 1: Create a Profile ---
print("👉 POST /profiles")
create_resp = requests.post(f"{BASE_URL}/profiles", headers=headers, json={
    "name": "Jane Doe",
    "gender": "female",
    "gender_probability": 0.95,
    "age": 28,
    "country_id": "US",
    "country_probability": 0.88
})
print(f"Status: {create_resp.status_code}")
print(create_resp.json())
print("-" * 40)

# --- TEST 2: Fetch Profiles (Pagination Meta test) ---
print("👉 GET /profiles")
get_resp = requests.get(f"{BASE_URL}/profiles?limit=5", headers=headers)
print(f"Status: {get_resp.status_code}")
print(get_resp.json())
print("-" * 40)

# --- TEST 3: NLP Search ---
print("👉 GET /profiles/search?q=adult+women+from+the+US")
search_resp = requests.get(f"{BASE_URL}/profiles/search?q=adult+women+from+the+US", headers=headers)
print(f"Status: {search_resp.status_code}")
print(search_resp.json())
print("-" * 40)