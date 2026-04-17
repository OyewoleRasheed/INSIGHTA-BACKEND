import requests

BASE = "https://hng-stage1-profile-api.pxxl.click/api/profiles"

print("=" * 50)
print("TEST 1 — POST: create a new profile")
r = requests.post(BASE, json={"name": "emma"})
print("Status:", r.status_code)  # expect 201
print("Raw response:", r.text) 
print("Response:", r.json())
profile_id = r.json()['data']['id']
print()

print("=" * 50)
print("TEST 2 — POST: same name again (idempotency)")
r = requests.post(BASE, json={"name": "emma"})
print("Status:", r.status_code)  # expect 200
print("Has 'message' key:", "message" in r.json())  # expect True
print("Message:", r.json().get("message"))  # expect "Profile already exists"
print()

print("=" * 50)
print("TEST 3 — POST: missing name")
r = requests.post(BASE, json={})
print("Status:", r.status_code)  # expect 400
print("Response:", r.json())
print()

print("=" * 50)
print("TEST 4 — POST: invalid type")
r = requests.post(BASE, json={"name": 123})
print("Status:", r.status_code)  # expect 422
print("Response:", r.json())
print()

print("=" * 50)
print("TEST 5 — GET: profile by id")
r = requests.get(f"{BASE}/{profile_id}")
print("Status:", r.status_code)  # expect 200
print("Response:", r.json())
print()

print("=" * 50)
print("TEST 6 — GET: profile with wrong id")
r = requests.get(f"{BASE}/wrong-id-123")
print("Status:", r.status_code)  # expect 404
print("Response:", r.json())
print()

print("=" * 50)
print("TEST 7 — GET: all profiles")
r = requests.get(BASE)
print("Status:", r.status_code)  # expect 200
print("Count:", r.json().get("count"))
print("Response:", r.json())
print()

print("=" * 50)
print("TEST 8 — GET: filter by gender")
r = requests.get(f"{BASE}?gender=female")
print("Status:", r.status_code)  # expect 200
print("Response:", r.json())
print()

print("=" * 50)
print("TEST 9 — GET: filter by age_group")
r = requests.get(f"{BASE}?age_group=adult")
print("Status:", r.status_code)  # expect 200
print("Response:", r.json())
print()

print("=" * 50)
print("TEST 10 — DELETE: delete the profile")
r = requests.delete(f"{BASE}/{profile_id}")
print("Status:", r.status_code)  # expect 204
print("Body empty:", r.text == "")  # expect True
print()

print("=" * 50)
print("TEST 11 — GET: profile after deletion")
r = requests.get(f"{BASE}/{profile_id}")
print("Status:", r.status_code)  # expect 404
print("Response:", r.json())
print()

print("=" * 50)
print("TEST 12 — DELETE: wrong id")
r = requests.delete(f"{BASE}/wrong-id-123")
print("Status:", r.status_code)  # expect 404
print("Response:", r.json())