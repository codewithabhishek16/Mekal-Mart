# Mekal Mart — Campus Delivery Platform

An interactive, secure 3-tier campus delivery platform built for students, local vendors, and student couriers.

---

## How to Run the Project Local Server

To run the application, follow these steps:

### Step 1: Start the PostgreSQL Database Server
Mekal Mart relies on **PostgreSQL** for its database layer. Ensure your PostgreSQL server is running:
1. Start your local PostgreSQL server (typically running on port `5432`).
2. Create a database named `unimart` (e.g., using `pgAdmin` or running `CREATE DATABASE unimart;` in `psql`).
3. Verify your connection credentials in your `.env` file (see `.env.example`). The tables and schema are automatically initialized on startup via self-healing migrations in the FastAPI backend!

---

### Step 2: Start the Python FastAPI Server
All project dependencies (such as `PyJWT`, `bcrypt`, `fastapi`, and `uvicorn`) are installed inside the virtual environment (`.venv`). Running `python main.py` directly from the global system environment will fail with a `ModuleNotFoundError: No module named 'jwt'`.

You must execute the script using the virtual environment interpreter:

#### Option A: Run directly using .venv Python (Recommended)
Open your terminal/PowerShell inside the project folder (`c:\Users\abhie\OneDrive\Desktop\unimart`) and run:
```powershell
.\.venv\Scripts\python main.py
```

#### Option B: Activate the environment first
Alternatively, activate the virtual environment and launch python:
```powershell
# 1. Activate the virtual environment
.\.venv\Scripts\activate

# 2. Run the application
python main.py
```

---

### Step 3: Access in the Browser
Once the server starts and displays `Uvicorn running on http://0.0.0.0:8000`, open your web browser and visit:
```
http://localhost:8000
```
This will load the customer marketplace UI directly!

---

## Live Production Deployment Options

For a live public deployment (using HTTPS to enable PWA standalone icons, notification audio, and geolocation features), the project includes:
- **`Dockerfile`**: For containerized deployment to GCP Cloud Run, AWS, or Railway.
- **`docker-compose.yml`**: For orchestrating the app and PostgreSQL together on a private VM:
  ```bash
  docker compose up -d
  ```
- **`Procfile`**: For PaaS deployment services like Render, Heroku, or Railway.
