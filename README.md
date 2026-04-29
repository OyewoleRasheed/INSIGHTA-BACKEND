# Insighta Labs+ | Stage 3 Backend API

This repository contains the backend service for Insighta Labs+, serving as the single source of truth for both a web portal and a globally installable Command Line Interface (CLI). 

**Live API Base URL:** `[insighta91-backend-production]`

---

## 🏗 System Architecture

The backend is built with **Python 3 and Flask**, providing a robust RESTful API (versioned at `/api/v1/`). 

* **Database:** SQLite (`insighta.db`), utilizing the standard `sqlite3` library for lightweight, file-based persistence. 
* **Dual-Client Support:** The system intelligently serves two distinct clients—a browser-based Web Portal and a local CLI. It distinguishes between them using a custom `X-Client-Type: cli` header.
* **Pagination:** Implements modern offset/limit pagination wrapped in a `meta` object for clear metadata handling.
* **Rate Limiting & Logging:** Protected by `Flask-Limiter` (in-memory) to prevent abuse, with comprehensive Python `logging` for an audit trail of incoming requests.

---

## 🔐 Authentication Flow & Token Handling Approach

We utilize **GitHub OAuth 2.0 with PKCE (Proof Key for Code Exchange)** to securely authenticate users without exposing a static client secret in the CLI.

### The Auth Handshake
1.  Clients initiate the OAuth flow with GitHub.
2.  Upon authorization, GitHub redirects back with a `code` (and `code_verifier` for the CLI).
3.  The backend (`POST /api/v1/auth/github/callback`) securely exchanges this code for a GitHub Access Token, retrieves the user's GitHub ID, and either finds or creates them in our SQLite `users` table.

### Dual-Client Token Handling
The backend issues its own **JSON Web Tokens (JWTs)** rather than passing GitHub tokens directly to the clients:
* **Access Token (15 min):** Contains user identity and role. Used for standard authorization.
* **Refresh Token (7 days):** Stored as a JTI (JWT ID) in the `refresh_tokens` database table to allow for immediate revocation and token rotation.

**How tokens are delivered:**
* **For the Web Portal:** Tokens are strictly returned as `Secure`, `HttpOnly`, `SameSite=Lax` cookies to mitigate XSS attacks. A separate CSRF token is provided to mitigate Cross-Site Request Forgery on state-changing requests.
* **For the CLI:** The `X-Client-Type: cli` header directs the backend to return the tokens directly in the JSON response body. The CLI then securely writes these tokens to `~/.insighta/credentials.json`.

---

## 🛡 Role Enforcement Logic

Role-Based Access Control (RBAC) is strictly enforced across the application using custom Python decorators.

* `@require_auth`: Validates the signature and expiration of the JWT (checking either the `Authorization: Bearer` header or the `access_token` cookie).
* `@require_role('admin', 'analyst')`: Inspects the `role` claim embedded inside the decoded JWT.

**Role Definitions:**
* **Admin:** Full access. Can create profiles (`POST /api/v1/profiles`), delete profiles (`DELETE /api/v1/profiles/<id>`), and manage user roles (`POST /api/v1/admin/users/<id>/role`).
* **Analyst:** Read-only access. Can view, filter, sort, search, and export profiles to CSV.

If a user's JWT lacks the required role, the API immediately halts execution and returns a `403 Forbidden` response.

---

## 🧠 Natural Language Parsing Approach

Retained from Stage 2, the `GET /api/v1/profiles/search` endpoint allows users to query the database using plain English (e.g., *"Show me adult women from Nigeria"*).

**Parsing Logic:**
1.  **Tokenization & Cleaning:** The query string is converted to lowercase and stripped of punctuation.
2.  **Keyword Extraction:** The parser scans for predefined dictionaries:
    * *Gender:* Matches terms like "men", "women", "boy", "girl".
    * *Age Groups:* Matches terms like "child", "teen", "adult", "senior".
    * *Geography:* Matches country names to ISO alpha-2 codes (e.g., "Nigeria" -> "NG").
3.  **SQL Translation:** Extracted entities are mapped to a structured filter dictionary, which the `build_filter_query()` utility then safely translates into parameterized SQL `WHERE` clauses to prevent SQL injection.

---

## 💻 CLI Usage

The CLI component (`insighta-cli`) interacts with this API. Once installed globally via `npm i -g insighta-cli`, users can authenticate and run commands directly from their terminal.

```bash
# Authenticate via GitHub (opens browser, saves tokens to ~/.insighta/credentials.json)
insighta login

# View your profile and current role
insighta whoami

# Fetch profiles with rich terminal table output
insighta profiles list --limit 5

# Natural language search
insighta profiles search "adult males from the United States"

# Export data directly to your local machine (Admin/Analyst)
insighta profiles export > output.csv
