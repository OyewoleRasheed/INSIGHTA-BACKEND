import sqlite3
import os

DB_PATH = os.environ.get("DB_PATH", "insighta.db")

def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def create_profiles_table():
    conn = get_db_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            gender TEXT,
            gender_probability REAL,
            age INTEGER,
            age_group TEXT,
            country_id TEXT,
            country_name TEXT,
            country_probability REAL,
            created_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

def create_users_table():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            github_id TEXT UNIQUE,
            username TEXT,
            email TEXT,
            avatar_url TEXT,
            role TEXT DEFAULT 'analyst',
            is_active BOOLEAN DEFAULT 1,
            last_login_at TEXT,
            created_at TEXT
        )
    ''')
    conn.commit()
    conn.close()
def create_refresh_tokens_table():
    conn = get_db_connection()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            token TEXT PRIMARY KEY,
            user_id TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            revoked INTEGER DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (id)
        )
    ''')
    conn.commit()
    conn.close()