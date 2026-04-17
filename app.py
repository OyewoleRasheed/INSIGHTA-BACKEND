from flask import Flask, jsonify, request
from flask_cors import CORS
from datetime import datetime, timezone
import requests
from database import get_db_connection, create_profiles_table
import uuid6
import os

app = Flask(__name__)
create_profiles_table()
app.json.sort_keys = False
CORS(app)

def classify_age(age):
    if age <= 12:
        return "child"
    elif age <= 19:
        return "teenager"
    elif age <= 59:
        return "adult"
    else:
        return "senior"

@app.route('/api/profiles', methods=['POST'])
def create_profile():

    data = request.get_json()

    if not data or 'name' not in data:
        return jsonify({"status": "error", "message": "Missing or empty name"}), 400
    name = data.get('name')
    
    if not isinstance(data['name'], str):
        return jsonify({"status": "error", "message": "Invalid type"}), 422
    
    #Check if name already exist in database
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM profiles WHERE name = ?", (name,))
    existing = cursor.fetchone()

    if existing:
        conn.close()
        return jsonify(
            {"status": "success",
              "message": "Profile already exists",
              "data": dict(existing)
              }), 200
    
    #call all the three APIS
    gender_response = requests.get(f"https://api.genderize.io?name={name}").json()
    age_response = requests.get(f"https://api.agify.io?name={name}").json()
    nationality_response = requests.get(f"https://api.nationalize.io?name={name}").json()

    print("GENDER:", gender_response)
    print("AGE:", age_response)
    print("NATION:", nationality_response)

    # 4. validate each response
    if gender_response.get('gender') is None or gender_response.get('count') == 0:
        return jsonify({"status": "502", "message": "Genderize returned an invalid response"}), 502

    if age_response.get('age') is None:
        return jsonify({"status": "502", "message": "Agify returned an invalid response"}), 502

    if not nationality_response.get('country'):
        return jsonify({"status": "502", "message": "Nationalize returned an invalid response"}), 502
    
    gender = gender_response['gender']
    gender_probability = gender_response['probability']
    sample_size = gender_response['count']

    age = age_response['age']
    age_group = classify_age(age)

    top_country = nationality_response['country'][0]
    country_id = top_country['country_id']
    country_probability = top_country['probability']

    # 6. generate id and timestamp
    profile_id = str(uuid6.uuid7())
    created_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

    # 7. save to database
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO profiles (id, name, gender, gender_probability, sample_size, age, age_group, country_id, country_probability, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
      (profile_id, name, gender, gender_probability, sample_size, age, age_group, country_id, country_probability, created_at))
    conn.commit()
    conn.close()

    # 8. return the response
    return jsonify({
        "status": "success",
        "data": {
            "id": profile_id,
            "name": name,
            "gender": gender,
            "gender_probability": gender_probability,
            "sample_size": sample_size,
            "age": age,
            "age_group": age_group,
            "country_id": country_id,
            "country_probability": country_probability,
            "created_at": created_at
        }
    }), 201


@app.route('/api/profiles/<id>', methods=['GET'])
def get_profile(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM profiles WHERE id = ?", (id,))
    profile = cursor.fetchone()
    conn.close()

    if profile is None:
        return jsonify({"status": "error", "message": "Profile not found"}), 404

    return jsonify({"status": "success", "data": dict(profile)}), 200
    
@app.route('/api/profiles', methods=['GET'])
def get_profiles():
    gender = request.args.get('gender','').lower()
    country_id = request.args.get('country_id','').lower()
    age_group = request.args.get('age_group','').lower()

    conn = get_db_connection()
    cursor = conn.cursor()

    query = "SELECT * FROM profiles WHERE 1=1"
    params = []

    if gender:
        query += " AND LOWER(gender) = ?"
        params.append(gender)
    if country_id:
        query += " AND LOWER(country_id) = ?"
        params.append(country_id)
    if age_group:
        query += " AND LOWER(age_group) = ?"
        params.append(age_group)

    cursor.execute(query, params)
    profiles = cursor.fetchall()
    conn.close()

    return jsonify({
        "status": "success",
        "count": len(profiles),
        "data": [
    {
        "id": p["id"],
        "name": p["name"],
        "gender": p["gender"],
        "age": p["age"],
        "age_group": p["age_group"],
        "country_id": p["country_id"]
    }
    for p in profiles
]
    }), 200
@app.route('/api/profiles/<id>', methods=['DELETE'])
def delete_profile(id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM profiles WHERE id = ?", (id,))
    profile = cursor.fetchone()

    if profile is None:
        conn.close()
        return jsonify({"status": "error", "message": "Profile not found"}), 404

    cursor.execute("DELETE FROM profiles WHERE id = ?", (id,))
    conn.commit()
    conn.close()

    return '', 204


if __name__ == "__main__":
    
    app.run(host='0.0.0.0', port=int(os.environ.get("PORT", 5000)))







