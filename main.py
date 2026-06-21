from fastapi import FastAPI, HTTPException, Depends, Request, Form, File, UploadFile, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import psycopg2
from psycopg2 import Error
import psycopg2.extras
from psycopg2.pool import ThreadedConnectionPool
import os
import time
import random
import json
import shutil
import jwt
# bcrypt removed
import uuid
import datetime
from typing import List, Optional
from dotenv import load_dotenv

load_dotenv()

DELIVERY_FEE = float(os.getenv("DELIVERY_FEE", "10"))
JWT_SECRET = os.getenv("JWT_SECRET", "fdf4671e2efd97bb1f09c25603deca73c713b194d2bb78f0d86de8583fb20625")
JWT_ALGORITHM = "HS256"

app = FastAPI(title="Mekal Mart API")

# --- AUTOMATIC CLEANUP SCRIPT ---
# This runs once on startup to automatically rename and delete files for the user
import glob
try:
    # Rename dashboards
    dashboards = ['admin_login', 'admin_dashboard', 'vendor_dashboard', 'delivery_dashboard']
    for d in dashboards:
        old_path = f"{d}.php"
        new_path = f"{d}.html"
        if os.path.exists(old_path):
            if os.path.exists(new_path):
                os.remove(new_path)
            os.rename(old_path, new_path)
            
    # Delete obsolete PHP files
    obsolete = [
        'gemini_proxy.php', 'admin_api.php', 'auth.php', 'db_connect.php', 
        'place_order.php', 'get_products.php', 'vendor_api.php', 
        'delivery_api.php', 'profile_api.php', 'wallet_api.php'
    ]
    for f in obsolete:
        if os.path.exists(f):
            os.remove(f)
            
    print("SUCCESS: Automatically removed all PHP files and renamed dashboards to .html!")
except Exception as e:
    print(f"Cleanup note: {e}")
# --------------------------------

# CORS Configuration
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Enforce HTTPS Redirection in production environments (behind proxy/load-balancers)
from fastapi.responses import RedirectResponse
@app.middleware("http")
async def enforce_https_middleware(request: Request, call_next):
    dev_mode = os.getenv("DEV_MODE", "true").lower() == "true"
    if not dev_mode:
        proto = request.headers.get("x-forwarded-proto", "http")
        if proto == "http":
            url = request.url.replace(scheme="https")
            return RedirectResponse(url, status_code=301)
    response = await call_next(request)
    return response

# Database Config
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "unimart")
}

# Initialize ThreadedConnectionPool for PostgreSQL optimization
try:
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        db_pool = ThreadedConnectionPool(minconn=1, maxconn=10, dsn=db_url)
    else:
        db_pool = ThreadedConnectionPool(minconn=1, maxconn=10, **DB_CONFIG)
    print("DATABASE SYNC: PostgreSQL connection pool initialized successfully.")
except Exception as e:
    print(f"Error creating connection pool: {e}")
    db_pool = None

class PostgreSQLConnectionWrapper:
    def __init__(self, conn, from_pool=False, pool=None):
        self._conn = conn
        self._from_pool = from_pool
        self._pool = pool

    def cursor(self, *args, **kwargs):
        if kwargs.get("dictionary"):
            kwargs.pop("dictionary")
            kwargs["cursor_factory"] = psycopg2.extras.RealDictCursor
        return self._conn.cursor(*args, **kwargs)

    def commit(self):
        return self._conn.commit()

    def rollback(self):
        return self._conn.rollback()

    def close(self):
        if self._from_pool and self._pool:
            try:
                self._pool.putconn(self._conn)
            except Exception:
                pass
        else:
            try:
                self._conn.close()
            except Exception:
                pass

    def __getattr__(self, name):
        return getattr(self._conn, name)

def get_db_connection():
    conn = None
    from_pool = False
    if db_pool:
        try:
            conn = db_pool.getconn()
            from_pool = True
        except Exception as e:
            print(f"Error getting connection from pool: {e}")
            
    if not conn:
        try:
            db_url = os.getenv("DATABASE_URL")
            if db_url:
                conn = psycopg2.connect(dsn=db_url)
            else:
                conn = psycopg2.connect(**DB_CONFIG)
        except Exception as e:
            print(f"Error connecting to PostgreSQL directly: {e}")
            return None

    return PostgreSQLConnectionWrapper(conn, from_pool=from_pool, pool=db_pool)

def get_table_columns(cursor, table_name):
    try:
        cursor.execute("""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = %s
        """, (table_name.lower(),))
        return [row['column_name'].lower() for row in cursor.fetchall()]
    except Exception as e:
        print(f"Error checking columns for {table_name}: {e}")
        return []

# Ensure critical tables exist (Self-Healing Schema)
def init_db():
    conn = get_db_connection()
    if conn:
        try:
            cursor = conn.cursor(dictionary=True)
            
            # Drops legacy tables if they exist to prevent schema collision
            cursor.execute("DROP TABLE IF EXISTS admins, delivery_partners, otps CASCADE")
            
            # Check if users table is using legacy schema or non-UUID id
            cursor.execute("""
                SELECT data_type 
                FROM information_schema.columns 
                WHERE table_name = 'users' AND column_name = 'id'
            """)
            row = cursor.fetchone()
            is_legacy = False
            if row and row['data_type'] != 'integer':
                is_legacy = True
            
            columns = get_table_columns(cursor, "users")
            if is_legacy or (columns and ('campus_id' in columns or 'is_approved' in columns)):
                print("MIGRATION: Legacy users table (or non-INT ID column) detected. Re-creating schema...")
                cursor.execute("DROP TABLE IF EXISTS users, vendor_profiles, student_profiles, partner_profiles, orders, wallet_transactions CASCADE")
            
            # 1. Users Table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255) UNIQUE NOT NULL,
                    phone VARCHAR(20) UNIQUE NULL,
                    role VARCHAR(20) NOT NULL CHECK (role IN ('student', 'vendor', 'partner', 'admin')),
                    auth_provider VARCHAR(20) DEFAULT 'google',
                    wallet_balance DECIMAL(10,2) DEFAULT 0.00,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            try:
                cursor.execute("ALTER TABLE users DROP CONSTRAINT IF EXISTS users_role_check")
                cursor.execute("ALTER TABLE users ADD CONSTRAINT users_role_check CHECK (role IN ('student', 'vendor', 'partner', 'admin'))")
            except Exception as e:
                print(f"Migration note for users role check: {e}")
            
            # 2. Vendor Profiles
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS vendor_profiles (
                    user_id INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    shop_name VARCHAR(255) NOT NULL,
                    shop_category VARCHAR(100),
                    shop_image VARCHAR(255) DEFAULT 'store-placeholder.png',
                    is_approved BOOLEAN DEFAULT FALSE
                )
            ''')
            cursor.execute("ALTER TABLE vendor_profiles ADD COLUMN IF NOT EXISTS is_approved BOOLEAN DEFAULT FALSE")
            
            # 3. Student Profiles
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS student_profiles (
                    user_id INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    student_name VARCHAR(255),
                    university_id VARCHAR(50),
                    hostel_name VARCHAR(100),
                    room_number VARCHAR(20),
                    profile_image VARCHAR(255) DEFAULT 'logo.png'
                )
            ''')
            
            # 4. Partner Profiles
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS partner_profiles (
                    user_id INT PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                    partner_name VARCHAR(255),
                    vehicle_number VARCHAR(50),
                    status VARCHAR(20) DEFAULT 'Pending Approval' CHECK (status IN ('Available', 'Busy', 'Offline', 'Pending Approval')),
                    rating DECIMAL(3,2) DEFAULT 5.0,
                    profile_image VARCHAR(255) DEFAULT 'logo.png'
                )
            ''')
            try:
                cursor.execute("ALTER TABLE partner_profiles ALTER COLUMN status SET DEFAULT 'Pending Approval'")
                cursor.execute("ALTER TABLE partner_profiles DROP CONSTRAINT IF EXISTS partner_profiles_status_check")
                cursor.execute("ALTER TABLE partner_profiles ADD CONSTRAINT partner_profiles_status_check CHECK (status IN ('Available', 'Busy', 'Offline', 'Pending Approval'))")
            except Exception as e:
                print(f"Migration note for partner_profiles: {e}")
            
            # 5. Products Table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS products (
                    id SERIAL PRIMARY KEY,
                    shop_id VARCHAR(255) NOT NULL,
                    name VARCHAR(255) NOT NULL,
                    category VARCHAR(100),
                    price DECIMAL(10,2) NOT NULL,
                    img VARCHAR(255) DEFAULT 'store-placeholder.png',
                    stock_count INT DEFAULT 50,
                    in_stock SMALLINT DEFAULT 1,
                    approval_status VARCHAR(20) DEFAULT 'Pending' CHECK (approval_status IN ('Pending', 'Approved', 'Rejected')),
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            cursor.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS approval_status VARCHAR(20) DEFAULT 'Pending'")
            try:
                cursor.execute("ALTER TABLE products DROP CONSTRAINT IF EXISTS products_approval_status_check")
                cursor.execute("ALTER TABLE products ADD CONSTRAINT products_approval_status_check CHECK (approval_status IN ('Pending', 'Approved', 'Rejected'))")
            except Exception as e:
                print(f"Migration note for products: {e}")
            
            # 6. Orders Table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS orders (
                    id SERIAL PRIMARY KEY,
                    order_id VARCHAR(50) UNIQUE NOT NULL,
                    customer_name VARCHAR(255),
                    phone VARCHAR(20),
                    email VARCHAR(255),
                    hostel VARCHAR(255),
                    room_no VARCHAR(50),
                    items TEXT,
                    total_amount VARCHAR(50),
                    delivery_fee DECIMAL(10,2) DEFAULT 0,
                    status VARCHAR(50) DEFAULT 'Pending',
                    delivery_pin VARCHAR(4),
                    pickup_pin VARCHAR(4),
                    agent_id INT,
                    delivery_partner_id INT,
                    shop_id VARCHAR(255) NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # Migration helper for existing schema
            cursor.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS delivery_pin VARCHAR(4)")
            cursor.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS pickup_pin VARCHAR(4)")
            cursor.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS shop_id VARCHAR(255) NULL")
            
            # 7. Notifications Table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS notifications (
                    id SERIAL PRIMARY KEY,
                    role VARCHAR(20) NOT NULL,
                    user_id VARCHAR(50) NOT NULL,
                    title VARCHAR(255),
                    message TEXT,
                    is_read SMALLINT DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 8. Wallet Transactions
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS wallet_transactions (
                    id SERIAL PRIMARY KEY,
                    user_id INT,
                    amount DECIMAL(10,2),
                    type VARCHAR(20),
                    description TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 9. OTPs Table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS otps (
                    id SERIAL PRIMARY KEY,
                    email VARCHAR(255) NOT NULL,
                    otp VARCHAR(6) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            conn.commit()
            print("DATABASE SYNC: All tables verified/created successfully.")
        except Error as e:
            print(f"DATABASE ERROR during init_db: {e}")
        finally:
            conn.close()

init_db()

# --- SECURITY UTILITIES & DEPENDENCIES ---
security = HTTPBearer()

def create_access_token(user_id: int, email: str, role: str) -> str:
    payload = {
        "user_id": user_id,
        "sub": email,
        "role": role,
        "exp": time.time() + (86400 * 7) # 7 days
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid or expired token")

def get_admin_user(current_user: dict = Depends(get_current_user)):
    role = current_user.get("role")
    if role not in ["vendor", "VENDOR", "admin", "ADMIN"]:
        raise HTTPException(status_code=403, detail="Admin/Vendor permissions required")
    return current_user

def get_super_admin(current_user: dict = Depends(get_current_user)):
    role = current_user.get("role")
    if role not in ["admin", "ADMIN"]:
        raise HTTPException(status_code=403, detail="Super Admin permissions required")
    return current_user

def get_delivery_user(current_user: dict = Depends(get_current_user)):
    role = current_user.get("role")
    if role not in ["delivery", "AGENT", "partner"]:
        raise HTTPException(status_code=403, detail="Delivery agent permissions required")
    return current_user

# --- RESEND EMAIL NOTIFICATIONS INTEGRATION ---
def send_resend_email(to_email: str, subject: str, html_content: str) -> bool:
    resend_api_key = os.getenv("RESEND_API_KEY")
    if not resend_api_key:
        print("RESEND NOTIFICATION: API Key not set.")
        return False
        
    from_email = os.getenv("RESEND_FROM_EMAIL", "Mekal Mart <onboarding@resend.dev>")
    reply_to = os.getenv("RESEND_REPLY_TO", "unimartcampusdelivery@gmail.com")
    
    import urllib.request
    import json
    
    url = "https://api.resend.com/emails"
    payload = {
        "from": from_email,
        "to": [to_email],
        "subject": subject,
        "html": html_content,
        "reply_to": reply_to
    }
    
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {resend_api_key}",
            "Content-Type": "application/json"
        },
        method="POST"
    )
    
    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            print(f"RESEND SUCCESS: Email sent to {to_email}. Message ID: {res_data.get('id')}")
            return True
    except Exception as e:
        print(f"RESEND ERROR: Failed to send email to {to_email}. Reason: {e}")
        return False
# -----------------------------------------------

# Legacy OTP helpers removed

# Safe file uploader
ALLOWED_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.gif', '.webp'}
ALLOWED_MIME_TYPES = {'image/png', 'image/jpeg', 'image/gif', 'image/webp'}
MAX_FILE_SIZE = 5 * 1024 * 1024 # 5 MB

def save_and_sanitize_upload(upload_file: UploadFile) -> str:
    upload_file.file.seek(0, 2)
    size = upload_file.file.tell()
    upload_file.file.seek(0)
    
    if size > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File too large. Max size is 5MB.")
        
    if upload_file.content_type not in ALLOWED_MIME_TYPES:
        raise HTTPException(status_code=400, detail="Invalid file type. Only PNG, JPG, JPEG, GIF, WEBP allowed.")
        
    ext = os.path.splitext(upload_file.filename)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail="Invalid file extension.")
        
    safe_filename = f"{uuid.uuid4().hex}{ext}"
    os.makedirs("uploads", exist_ok=True)
    
    filepath = os.path.join("uploads", safe_filename)
    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(upload_file.file, buffer)
        
    return f"uploads/{safe_filename}"

# Models
class OrderRequest(BaseModel):
    name: str
    phone: str
    email: str
    hostel: str
    room: str
    items: str
    total: float
    payment_method: str
    shop_id: Optional[str] = None

class OTPRequest(BaseModel):
    email: str
    selected_role: str

class LoginRequest(BaseModel):
    email: str
    otp: Optional[str] = None
    selected_role: str
    name: Optional[str] = None
    phone: Optional[str] = None
    password: Optional[str] = None

class SwitchRoleRequest(BaseModel):
    email: str
    target_role: str

class DeliveryApplication(BaseModel):
    email: str
    student_id: str
    vehicle_number: str = ""
    hostel: str

class VerifyDropoffRequest(BaseModel):
    order_id: str
    agent_id: int
    pin: str

# --- AUTH ENDPOINTS ---

@app.post("/auth/request-otp")
async def request_otp(data: OTPRequest, background_tasks: BackgroundTasks):
    email = data.email.strip().lower()
    selected_role = data.selected_role
    
    if selected_role == 'admin':
        raise HTTPException(status_code=400, detail="Admin role must login with password.")
        
    if selected_role not in ["student", "vendor", "partner"]:
        raise HTTPException(status_code=400, detail="Invalid user role")
        
    # Generate 6-digit OTP
    otp = f"{random.randint(100000, 999999)}"
    
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor()
    try:
        # Delete old OTPs for this email
        cursor.execute("DELETE FROM otps WHERE email = %s", (email,))
        # Insert new OTP
        cursor.execute("INSERT INTO otps (email, otp) VALUES (%s, %s)", (email, otp))
        conn.commit()
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()
        
    dev_mode = os.getenv("DEV_MODE", "true").lower() == "true"
    
    # Send OTP Email
    otp_html = f"""
    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #e2e8f0; border-radius: 10px;">
        <h2 style="color: #1B4332; margin-bottom: 20px;">Mekal Mart Verification Code</h2>
        <p>Your 6-digit login verification token is:</p>
        <div style="background-color: #F9FBFF; padding: 15px; border-radius: 8px; border: 1px solid #e0f3f7; text-align: center; margin-bottom: 20px;">
            <span style="font-size: 32px; font-weight: 900; letter-spacing: 6px; color: #1B4332;">{otp}</span>
        </div>
        <p style="font-size: 12px; color: #64748B;">This code is valid for 10 minutes. Please do not share it with anyone.</p>
        <p style="font-size: 11px; color: #94A3B8; text-align: center; margin-top: 30px; border-top: 1px solid #E2E8F0; padding-top: 10px;">
            Mekal Mart - Campus Delivery Platform
        </p>
    </div>
    """
    
    # Send email in background
    background_tasks.add_task(send_resend_email, email, f"{otp} is your Mekal Mart verification code", otp_html)
    
    # Return OTP in response so client-side EmailJS or fallback can process it
    print(f"OTP NOTIFICATION: Generated OTP {otp} for email {email}")
    return {"status": "success", "message": "Verification code generated.", "otp": otp}

@app.post("/auth/login")
async def login(data: LoginRequest):
    email = data.email.strip().lower()
    selected_role = data.selected_role
    phone = data.phone.strip() if data.phone else None
    
    if selected_role not in ["student", "vendor", "partner", "admin"]:
        raise HTTPException(status_code=400, detail="Invalid user role")
        
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    try:
        if selected_role == 'admin':
            admin_email = os.getenv("ADMIN_EMAIL", "admin@mekalmart.com").strip().lower()
            admin_password = os.getenv("ADMIN_PASSWORD", "MekalAdmin@2026")
            
            if email != admin_email or not data.password or data.password != admin_password:
                raise HTTPException(status_code=401, detail="Invalid admin email or password.")
            
            # Check if admin user exists in DB
            cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
            user = cursor.fetchone()
            if not user:
                # Register admin
                cursor.execute("""
                    INSERT INTO users (email, role, auth_provider, wallet_balance)
                    VALUES (%s, 'admin', 'password', 0.00) RETURNING id, email, phone, role, wallet_balance
                """, (email,))
                user = cursor.fetchone()
                conn.commit()
                
            token = create_access_token(user["id"], email, "admin")
            user_data = {
                "id": user["id"],
                "email": email,
                "phone": user["phone"],
                "name": "Super Admin",
                "role": "admin",
                "avatar_url": f"https://api.dicebear.com/7.x/adventurer/svg?seed={email}",
                "wallet_balance": float(user["wallet_balance"])
            }
            return {
                "status": "success",
                "token": token,
                "user": user_data
            }

        # Check OTP
        otp = data.otp.strip() if data.otp else ""
        cursor.execute("SELECT * FROM otps WHERE email = %s AND otp = %s AND created_at >= NOW() - INTERVAL '10 minutes'", (email, otp))
        otp_row = cursor.fetchone()
        
        # If DEV_MODE is true, we allow a special mock verification bypass code (123456)
        dev_mode = os.getenv("DEV_MODE", "true").lower() == "true"
        is_mock_bypass = dev_mode and otp == "123456"
        
        if not otp_row and not is_mock_bypass:
            raise HTTPException(status_code=400, detail="Invalid or expired verification code.")
            
        # Delete used OTP
        if otp_row:
            cursor.execute("DELETE FROM otps WHERE id = %s", (otp_row['id'],))
            
        # Check if user exists
        cursor.execute("SELECT * FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
        
        name = data.name or email.split('@')[0].capitalize()
        avatar_url = f"https://api.dicebear.com/7.x/adventurer/svg?seed={email}"
        
        if not user:
            # Register user
            cursor.execute("""
                INSERT INTO users (email, phone, role, auth_provider, wallet_balance)
                VALUES (%s, %s, %s, 'email_otp', 0.00) RETURNING id, email, phone, role, wallet_balance
            """, (email, phone, selected_role))
            user = cursor.fetchone()
            user_id = user["id"]
            
            # Create profile
            if selected_role == 'student':
                cursor.execute("""
                    INSERT INTO student_profiles (user_id, student_name, profile_image)
                    VALUES (%s, %s, %s)
                """, (user_id, name, avatar_url))
            elif selected_role == 'vendor':
                cursor.execute("""
                    INSERT INTO vendor_profiles (user_id, shop_name, shop_image)
                    VALUES (%s, %s, %s)
                """, (user_id, name, avatar_url))
            elif selected_role == 'partner':
                cursor.execute("""
                    INSERT INTO partner_profiles (user_id, partner_name, profile_image, status, rating)
                    VALUES (%s, %s, %s, 'Pending Approval', 5.0)
                """, (user_id, name, avatar_url))
            conn.commit()
            
            user_role = selected_role
            wallet_balance = 0.00
            user_name = name
            user_avatar = avatar_url
            user_phone = phone
        else:
            user_id = user["id"]
            user_role = user["role"]
            wallet_balance = float(user["wallet_balance"])
            
            # Update phone and name if provided
            if phone:
                cursor.execute("UPDATE users SET phone = %s WHERE id = %s", (phone, user_id))
            if data.name:
                if user_role == 'student':
                    cursor.execute("UPDATE student_profiles SET student_name = %s WHERE user_id = %s", (name, user_id))
                elif user_role == 'vendor':
                    cursor.execute("UPDATE vendor_profiles SET shop_name = %s WHERE user_id = %s", (name, user_id))
                elif user_role == 'partner':
                    cursor.execute("UPDATE partner_profiles SET partner_name = %s WHERE user_id = %s", (name, user_id))
            conn.commit()
            
            # Refetch updated user record
            cursor.execute("SELECT phone FROM users WHERE id = %s", (user_id,))
            user_phone = cursor.fetchone()["phone"]
            
            # Fetch profile details
            if user_role == 'student':
                cursor.execute("SELECT student_name as name, profile_image as avatar_url FROM student_profiles WHERE user_id = %s", (user_id,))
            elif user_role == 'vendor':
                cursor.execute("SELECT shop_name as name, shop_image as avatar_url FROM vendor_profiles WHERE user_id = %s", (user_id,))
            elif user_role == 'partner':
                cursor.execute("SELECT partner_name as name, profile_image as avatar_url FROM partner_profiles WHERE user_id = %s", (user_id,))
                
            profile = cursor.fetchone()
            if profile:
                user_name = profile["name"]
                user_avatar = profile["avatar_url"]
            else:
                user_name = name
                user_avatar = avatar_url
                
        token = create_access_token(user_id, email, user_role)
        
        user_data = {
            "id": user_id,
            "email": email,
            "phone": user_phone,
            "name": user_name,
            "role": user_role,
            "avatar_url": user_avatar,
            "wallet_balance": wallet_balance
        }
        
        if user_role == 'vendor':
            cursor.execute("SELECT shop_name, shop_category, is_approved FROM vendor_profiles WHERE user_id = %s", (user_id,))
            vp = cursor.fetchone()
            if vp:
                user_data["shop_name"] = vp["shop_name"]
                user_data["shop_category"] = vp["shop_category"]
                user_data["is_approved"] = bool(vp["is_approved"])
        elif user_role == 'partner':
            cursor.execute("SELECT status FROM partner_profiles WHERE user_id = %s", (user_id,))
            pp = cursor.fetchone()
            if pp:
                user_data["status"] = pp["status"]
                
        return {
            "status": "success",
            "token": token,
            "user": user_data
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.post("/auth/switch-role")
async def switch_role(data: SwitchRoleRequest, current_user: dict = Depends(get_current_user)):
    if current_user["sub"] != data.email:
        raise HTTPException(status_code=403, detail="Unauthorized role switch request")
        
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM users WHERE email = %s", (data.email,))
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
        
        if data.target_role in ['delivery', 'partner', 'AGENT']:
            cursor.execute("SELECT 1 FROM partner_profiles WHERE user_id = %s", (user["id"],))
            if not cursor.fetchone():
                return {"status": "error", "message": "You must apply to be a Delivery Agent first"}
            cursor.execute("UPDATE users SET role = 'partner' WHERE id = %s", (user["id"],))
            new_front_role = 'partner'
        elif data.target_role in ['student', 'CUSTOMER']:
            cursor.execute("UPDATE users SET role = 'student' WHERE id = %s", (user["id"],))
            new_front_role = 'student'
        else:
            raise HTTPException(status_code=400, detail="Invalid target role")
            
        conn.commit()
        
        # Get profile name and avatar_url
        if new_front_role == 'student':
            cursor.execute("SELECT student_name as name, profile_image as avatar_url FROM student_profiles WHERE user_id = %s", (user["id"],))
        elif new_front_role == 'partner':
            cursor.execute("SELECT partner_name as name, profile_image as avatar_url, status FROM partner_profiles WHERE user_id = %s", (user["id"],))
        else:
            cursor.execute("SELECT shop_name as name, shop_image as avatar_url, is_approved FROM vendor_profiles WHERE user_id = %s", (user["id"],))
        prof = cursor.fetchone()
        
        user_data = {
            "id": user["id"],
            "email": user["email"],
            "phone": user["phone"],
            "role": new_front_role,
            "name": prof["name"] if prof else "User",
            "avatar_url": prof["avatar_url"] if prof else None,
            "wallet_balance": float(user["wallet_balance"])
        }
        if new_front_role == 'vendor' and prof:
            user_data["is_approved"] = bool(prof.get("is_approved"))
        elif new_front_role == 'partner' and prof:
            user_data["status"] = prof.get("status")
        
        token = create_access_token(user["id"], user["email"], new_front_role)
        return {"status": "success", "user": user_data, "token": token}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.post("/auth/apply-delivery")
async def apply_delivery(data: DeliveryApplication, current_user: dict = Depends(get_current_user)):
    if current_user["sub"] != data.email:
        raise HTTPException(status_code=403, detail="Unauthorized application request")
        
    conn = get_db_connection()
    if not conn:
        return {"status": "error", "message": "Database connection failed"}
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT * FROM users WHERE email = %s", (data.email,))
        user = cursor.fetchone()
        if not user:
            return {"status": "error", "message": "User not found"}
            
        cursor.execute("SELECT 1 FROM partner_profiles WHERE user_id = %s", (user["id"],))
        if cursor.fetchone():
            return {"status": "error", "message": "You are already registered as a Delivery Agent"}
            
        cursor.execute("SELECT student_name, profile_image FROM student_profiles WHERE user_id = %s", (user["id"],))
        student = cursor.fetchone()
        name = student["student_name"] if student else "Agent"
        img = student["profile_image"] if student else None

        cursor.execute("INSERT INTO partner_profiles (user_id, partner_name, vehicle_number, status, rating, profile_image) VALUES (%s, %s, %s, 'Offline', 5.0, %s)", 
                       (user["id"], name, data.vehicle_number, img))
        
        if not student:
            cursor.execute("INSERT INTO student_profiles (user_id, student_name, university_id, hostel_name, profile_image) VALUES (%s, %s, %s, %s, %s)",
                           (user["id"], name, data.student_id, data.hostel, img))
        else:
            cursor.execute("UPDATE student_profiles SET university_id = %s, hostel_name = %s WHERE user_id = %s",
                           (data.student_id, data.hostel, user["id"]))
        
        cursor.execute("UPDATE users SET role = 'partner' WHERE id = %s", (user["id"],))
        conn.commit()
        
        user_data = {
            "id": user["id"],
            "email": user["email"],
            "phone": user["phone"],
            "role": "partner",
            "name": name,
            "avatar_url": img,
            "wallet_balance": float(user["wallet_balance"])
        }
        
        token = create_access_token(user["id"], user["email"], "partner")
        return {"status": "success", "message": "Successfully applied! You are now a Delivery Agent.", "user": user_data, "token": token}
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": f"Database Error: {str(e)}"}
    finally:
        conn.close()

# --- PRODUCT ENDPOINTS ---

@app.get("/products")
async def get_products(shop_id: Optional[str] = None):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    try:
        if shop_id and shop_id != "all":
            cursor.execute("SELECT id, shop_id, name, category, price, img, stock_count, in_stock, approval_status FROM products WHERE shop_id = %s", (shop_id,))
        else:
            cursor.execute("SELECT id, shop_id, name, category, price, img, stock_count, in_stock, approval_status FROM products")
        products = cursor.fetchall()
        return products
    finally:
        conn.close()

@app.get("/shops")
async def get_shops():
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT u.email as id, vp.shop_name as name, vp.shop_category as category, vp.shop_image as logo 
            FROM users u 
            JOIN vendor_profiles vp ON u.id = vp.user_id 
            WHERE u.role = 'vendor'
        """)
        shops = cursor.fetchall()
        for shop in shops:
            if not shop.get('logo'):
                shop['logo'] = 'store-placeholder.png'
        return shops
    finally:
        conn.close()

# --- ORDER ENDPOINTS ---

@app.post("/orders/place")
async def place_order(data: OrderRequest, background_tasks: BackgroundTasks, current_user: dict = Depends(get_current_user)):
    # Verify that user is authorized to place order for this email
    if current_user["sub"] != data.email:
        raise HTTPException(status_code=403, detail="Unauthorized order placement")
        
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    order_id = f"ORD{int(time.time())}{random.randint(100, 999)}"
    delivery_pin = f"{random.randint(0, 9999):04d}"
    pickup_pin = f"{random.randint(0, 9999):04d}"
    while pickup_pin == delivery_pin:
        pickup_pin = f"{random.randint(0, 9999):04d}"
    delivery_fee = DELIVERY_FEE
    try:
        # Automatically populate phone in users table if not set yet
        cursor.execute("SELECT id, phone, wallet_balance FROM users WHERE email = %s FOR UPDATE", (data.email,))
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=400, detail="User not found for this account")
            
        if not user["phone"] and data.phone:
            cursor.execute("UPDATE users SET phone = %s WHERE id = %s", (data.phone, user["id"]))

        if data.payment_method == "wallet":
            if float(user["wallet_balance"]) < data.total:
                raise HTTPException(status_code=400, detail="Insufficient wallet balance")

        cursor.execute("INSERT INTO orders (order_id, customer_name, phone, email, hostel, room_no, items, total_amount, delivery_fee, status, delivery_pin, pickup_pin, shop_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                       (order_id, data.name, data.phone, data.email, data.hostel, data.room, data.items, f"₹{data.total}", delivery_fee, "Accepted", delivery_pin, pickup_pin, data.shop_id))
        
        if data.payment_method == "wallet":
            cursor.execute("UPDATE users SET wallet_balance = wallet_balance - %s WHERE id = %s AND wallet_balance >= %s", (data.total, user["id"], data.total))
            if cursor.rowcount == 0:
                raise HTTPException(status_code=400, detail="Insufficient wallet balance or transaction failed")
                
            cursor.execute("INSERT INTO wallet_transactions (user_id, amount, type, description) VALUES (%s, %s, 'debit', %s)",
                           (user["id"], data.total, f"Payment for Order #{order_id}"))
            
        # Notify Partners
        cursor.execute("SELECT id FROM users WHERE role = 'partner'")
        for dp in cursor.fetchall():
            cursor.execute("INSERT INTO notifications (role, user_id, title, message, is_read) VALUES ('delivery', %s, %s, %s, 0)",
                           (str(dp["id"]), "New Order Alert!", f"New order {order_id} placed for {data.hostel}."))
                           
        # Notify Vendors
        cursor.execute("SELECT id FROM users WHERE role = 'vendor'")
        for v in cursor.fetchall():
            cursor.execute("INSERT INTO notifications (role, user_id, title, message, is_read) VALUES ('vendor', %s, %s, %s, 0)",
                           (str(v["id"]), "New Order Alert!", f"Check if {order_id} contains your items!"))
            
        # Fetch vendor & rider emails to send notifications to them
        cursor.execute("SELECT email FROM users WHERE role = 'vendor'")
        vendor_emails = [row['email'] for row in cursor.fetchall() if row.get('email')]
        
        cursor.execute("SELECT email FROM users WHERE role = 'partner'")
        rider_emails = [row['email'] for row in cursor.fetchall() if row.get('email')]

        conn.commit()

        # Resend Transactional Emails
        # 1. Customer Confirmation Receipt
        cust_html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #e2e8f0; border-radius: 10px;">
            <h2 style="color: #1B4332; margin-bottom: 20px;">Order Confirmed!</h2>
            <p>Hi {data.name},</p>
            <p>Thank you for shopping at Mekal Mart! Your order has been successfully placed.</p>
            <div style="background-color: #F9FBFF; padding: 15px; border-radius: 8px; border: 1px solid #e0f3f7; margin-bottom: 20px;">
                <p style="margin: 0;"><strong>Order ID:</strong> #{order_id}</p>
                <p style="margin: 5px 0 0 0;"><strong>Items:</strong> {data.items}</p>
                <p style="margin: 5px 0 0 0;"><strong>Delivery to:</strong> {data.hostel}, Room {data.room}</p>
                <p style="margin: 5px 0 0 0;"><strong>Total Amount:</strong> ₹{data.total:.2f} (including ₹{delivery_fee:.2f} delivery fee)</p>
            </div>
            
            <div style="background-color: #FFF9E6; border-left: 4px solid #FFB703; padding: 15px; margin-bottom: 20px;">
                <p style="margin: 0; font-weight: bold; color: #E0A102;">🔑 Delivery Verification PIN</p>
                <p style="margin: 5px 0 0 0; font-size: 24px; font-weight: 900; letter-spacing: 4px; color: #1B4332;">{delivery_pin}</p>
                <p style="margin: 5px 0 0 0; font-size: 11px; color: #64748B;">Please share this PIN with the delivery rider only after you receive your package.</p>
            </div>
            <p style="font-size: 12px; color: #94A3B8; text-align: center; margin-top: 30px; border-top: 1px solid #E2E8F0; padding-top: 10px;">
                Mekal Mart - Campus Delivery Platform
            </p>
        </div>
        """
        background_tasks.add_task(send_resend_email, data.email, f"Order Confirmed - #{order_id} | Mekal Mart", cust_html)
        
        # 2. Vendor Alert
        vendor_html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #e2e8f0; border-radius: 10px;">
            <h2 style="color: #023047; margin-bottom: 20px;">New Order Alert!</h2>
            <p>A new student order <strong>#{order_id}</strong> has been placed.</p>
            <div style="background-color: #F9FBFF; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                <p style="margin: 0;"><strong>Customer Name:</strong> {data.name}</p>
                <p style="margin: 5px 0 0 0;"><strong>Delivery to:</strong> {data.hostel}, Room {data.room}</p>
                <p style="margin: 5px 0 0 0;"><strong>Items to check:</strong> {data.items}</p>
            </div>
            <div style="background-color: #FFF9E6; border-left: 4px solid #FFB703; padding: 15px; margin-bottom: 20px;">
                <p style="margin: 0; font-weight: bold; color: #E0A102;">📦 Verification Required</p>
                <p style="margin: 5px 0 0 0; font-size: 12px; color: #64748B;">Please verify and pack the items. Hand over the package to the delivery agent only when they provide the pickup PIN: <strong>{pickup_pin}</strong>.</p>
            </div>
            <p>Please log in to your dashboard to process the order.</p>
        </div>
        """
        for v_email in vendor_emails:
            background_tasks.add_task(send_resend_email, v_email, f"New Order Alert - #{order_id} | Mekal Mart", vendor_html)
            
        # 3. Rider Alert
        rider_html = f"""
        <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #e2e8f0; border-radius: 10px;">
            <h2 style="color: #219EBC; margin-bottom: 20px;">New Delivery Mission Available!</h2>
            <p>A new order <strong>#{order_id}</strong> is ready for pickup at the campus stores.</p>
            <div style="background-color: #F9FBFF; padding: 15px; border-radius: 8px; margin-bottom: 20px;">
                <p style="margin: 0;"><strong>Delivery Destination:</strong> {data.hostel}</p>
                <p style="margin: 5px 0 0 0;"><strong>Estimated Earning:</strong> ₹{delivery_fee:.2f}</p>
            </div>
            <p>Log in to your Delivery Portal to accept this task and start earning.</p>
        </div>
        """
        for r_email in rider_emails:
            background_tasks.add_task(send_resend_email, r_email, f"New Delivery Mission Available - #{order_id} | Mekal Mart", rider_html)

        return {"status": "success", "order_id": order_id, "delivery_fee": delivery_fee}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

# --- VENDOR & DASHBOARD APIs ---

@app.get("/vendor/stats")
async def get_vendor_stats(shop_id: str, current_user: dict = Depends(get_admin_user)):
    # Verify owner
    if current_user["sub"] != shop_id:
        raise HTTPException(status_code=403, detail="Unauthorized stats request")
        
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT COUNT(*) as total FROM products WHERE shop_id = %s", (shop_id,))
        total_items = cursor.fetchone()['total']
        cursor.execute("SELECT COUNT(*) as count FROM products WHERE shop_id = %s AND in_stock = 1", (shop_id,))
        in_stock = cursor.fetchone()['count']
        cursor.execute("SELECT COUNT(*) as count FROM orders WHERE shop_id = %s AND status NOT IN ('Delivered') AND status != ''", (shop_id,))
        pending_orders = cursor.fetchone()['count']
        cursor.execute("SELECT SUM(CAST(REPLACE(REPLACE(total_amount, '₹', ''), ',', '') AS DECIMAL(10,2))) as revenue FROM orders WHERE shop_id = %s AND status = 'Delivered'", (shop_id,))
        revenue = cursor.fetchone()['revenue'] or 0
        formatted_revenue = f"₹{revenue/1000:.1f}k" if revenue >= 1000 else f"₹{revenue:.0f}"
        return {"total_items": total_items, "in_stock": in_stock, "pending_orders": pending_orders, "monthly_revenue": formatted_revenue}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.get("/notifications")
async def get_notifications(user_id: str, role: str, current_user: dict = Depends(get_current_user)):
    # Verify auth
    if str(current_user["user_id"]) != str(user_id):
        raise HTTPException(status_code=403, detail="Unauthorized notifications request")
        
    # Compatibility fix: 'partner' is an alias for 'delivery'
    if role == 'partner':
        role = 'delivery'
        
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id, title, message, is_read, created_at FROM notifications WHERE user_id = %s AND role = %s ORDER BY created_at DESC LIMIT 20", (user_id, role))
        res = cursor.fetchall()
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.post("/notifications/mark-read")
async def mark_notifications_read(data: dict, current_user: dict = Depends(get_current_user)):
    user_id = data.get("user_id")
    role = data.get("role")
    
    if str(current_user["user_id"]) != str(user_id):
        raise HTTPException(status_code=403, detail="Unauthorized notification update request")
        
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE notifications SET is_read = 1 WHERE user_id = %s AND role = %s", (user_id, role))
        conn.commit()
        return {"status": "success"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.get("/delivery/open-orders")
async def get_open_orders(current_user: dict = Depends(get_delivery_user)):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT o.order_id, 
                   coalesce(sp.student_name, o.customer_name) as student_name,
                   coalesce(sp.hostel_name, o.hostel) as hostel_name,
                   coalesce(sp.room_number, o.room_no) as room_number,
                   coalesce(u.phone, o.phone) as phone,
                   o.items, o.total_amount, o.delivery_fee, o.status, o.created_at
            FROM orders o
            LEFT JOIN users u ON o.email = u.email
            LEFT JOIN student_profiles sp ON u.id = sp.user_id
            WHERE o.delivery_partner_id IS NULL AND o.status != 'Delivered'
            ORDER BY o.created_at DESC
        """)
        res = cursor.fetchall()
        # Mask/hide phone number for privacy on open jobs
        for row in res:
            row["phone"] = None
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.get("/delivery/my-orders")
async def get_my_orders(agent_id: int, current_user: dict = Depends(get_delivery_user)):
    if str(current_user["user_id"]) != str(agent_id):
        raise HTTPException(status_code=403, detail="Unauthorized task request")
        
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT o.order_id, 
                   coalesce(sp.student_name, o.customer_name) as student_name,
                   coalesce(sp.hostel_name, o.hostel) as hostel_name,
                   coalesce(sp.room_number, o.room_no) as room_number,
                   coalesce(u.phone, o.phone) as phone,
                   o.items, o.total_amount, o.delivery_fee, o.status, o.created_at
            FROM orders o
            LEFT JOIN users u ON o.email = u.email
            LEFT JOIN student_profiles sp ON u.id = sp.user_id
            WHERE o.delivery_partner_id = %s AND o.status != 'Delivered'
            ORDER BY o.created_at DESC
        """, (agent_id,))
        res = cursor.fetchall()
        
        # Enforce contact records visibility (Privacy Safeguard)
        for row in res:
            if row.get("status") not in ["Driver Assigned", "Dispatched", "Out for Delivery", "Picked Up"]:
                row["phone"] = None
                
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.get("/delivery/history")
async def get_delivery_history(agent_id: int, current_user: dict = Depends(get_delivery_user)):
    if str(current_user["user_id"]) != str(agent_id):
        raise HTTPException(status_code=403, detail="Unauthorized history request")
        
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT o.order_id, 
                   coalesce(sp.student_name, o.customer_name) as student_name,
                   coalesce(sp.hostel_name, o.hostel) as hostel_name,
                   coalesce(sp.room_number, o.room_no) as room_number,
                   coalesce(u.phone, o.phone) as phone,
                   o.items, o.total_amount, o.delivery_fee, o.status, o.created_at
            FROM orders o
            LEFT JOIN users u ON o.email = u.email
            LEFT JOIN student_profiles sp ON u.id = sp.user_id
            WHERE o.delivery_partner_id = %s AND o.status = 'Delivered'
            ORDER BY o.created_at DESC
        """, (agent_id,))
        res = cursor.fetchall()
        
        # Enforce contact records visibility (Privacy Safeguard)
        for row in res:
            row["phone"] = None
            
        return res
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.post("/delivery/accept")
async def accept_delivery(data: dict, background_tasks: BackgroundTasks, current_user: dict = Depends(get_delivery_user)):
    order_id = data.get("order_id")
    agent_id = data.get("agent_id")
    
    if str(current_user["user_id"]) != str(agent_id):
        raise HTTPException(status_code=403, detail="Unauthorized agent action")
        
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("UPDATE orders SET delivery_partner_id = %s, status = 'Driver Assigned' WHERE order_id = %s AND delivery_partner_id IS NULL", (agent_id, order_id))
        success = cursor.rowcount > 0
        
        info = None
        if success:
            # Fetch details for notification email
            cursor.execute("""
                SELECT o.email as customer_email, o.customer_name, o.hostel, o.room_no,
                       pp.partner_name, pp.vehicle_number
                FROM orders o
                LEFT JOIN partner_profiles pp ON pp.user_id = %s
                WHERE o.order_id = %s
            """, (agent_id, order_id))
            info = cursor.fetchone()
            
        conn.commit()
        
        if success and info and info.get("customer_email"):
            cust_email = info["customer_email"]
            cust_name = info.get("customer_name") or "Student"
            agent_name = info.get("partner_name") or "Mekal Mart Rider"
            vehicle_no = info.get("vehicle_number") or "N/A"
            
            accept_html = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #e2e8f0; border-radius: 10px;">
                <h2 style="color: #1B4332; margin-bottom: 20px;">Delivery Agent Assigned!</h2>
                <p>Hi {cust_name},</p>
                <p>Great news! A delivery agent has been assigned to your order <strong>#{order_id}</strong>.</p>
                
                <div style="background-color: #F9FBFF; padding: 15px; border-radius: 8px; border: 1px solid #e0f3f7; margin-bottom: 20px;">
                    <p style="margin: 0;"><strong>Delivery Partner:</strong> {agent_name}</p>
                    <p style="margin: 5px 0 0 0;"><strong>Vehicle Number:</strong> {vehicle_no}</p>
                    <p style="margin: 5px 0 0 0;"><strong>Destination:</strong> {info.get("hostel")}, Room {info.get("room_no")}</p>
                </div>
                
                <p>The rider is now picking up your items from the store and will deliver them shortly. Please be ready to provide your delivery PIN upon arrival.</p>
                <p style="font-size: 12px; color: #94A3B8; text-align: center; margin-top: 30px; border-top: 1px solid #E2E8F0; padding-top: 10px;">
                    Mekal Mart - Campus Delivery Platform
                </p>
            </div>
            """
            background_tasks.add_task(send_resend_email, cust_email, f"Delivery Agent Assigned - #{order_id} | Mekal Mart", accept_html)
            
        return {"status": "success" if success else "error"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.post("/delivery/pickup")
async def pickup_delivery(data: dict, background_tasks: BackgroundTasks, current_user: dict = Depends(get_delivery_user)):
    order_id = data.get("order_id")
    agent_id = data.get("agent_id")
    pin = data.get("pin")
    
    if str(current_user["user_id"]) != str(agent_id):
        raise HTTPException(status_code=403, detail="Unauthorized agent action")
        
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT pickup_pin FROM orders WHERE order_id = %s AND delivery_partner_id = %s", (order_id, agent_id))
        order = cursor.fetchone()
        if not order:
            return {"status": "error", "message": "Order not found or not assigned to you."}
        if str(order['pickup_pin']) != str(pin):
            return {"status": "error", "message": "Invalid pickup PIN!"}
            
        cursor.execute("UPDATE orders SET status = 'Out for Delivery' WHERE order_id = %s AND delivery_partner_id = %s", (order_id, agent_id))
        
        # Fetch details for notification email
        cursor.execute("""
            SELECT o.email as customer_email, o.customer_name, o.hostel, o.room_no,
                   pp.partner_name
            FROM orders o
            LEFT JOIN partner_profiles pp ON pp.user_id = %s
            WHERE o.order_id = %s
        """, (agent_id, order_id))
        info = cursor.fetchone()
        
        conn.commit()
        
        if info and info.get("customer_email"):
            cust_email = info["customer_email"]
            cust_name = info.get("customer_name") or "Student"
            agent_name = info.get("partner_name") or "Mekal Mart Rider"
            
            pickup_html = f"""
            <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #e2e8f0; border-radius: 10px;">
                <h2 style="color: #219EBC; margin-bottom: 20px;">Order Out for Delivery!</h2>
                <p>Hi {cust_name},</p>
                <p>Your order <strong>#{order_id}</strong> has been picked up from the store by {agent_name} and is on its way to you!</p>
                
                <div style="background-color: #F9FBFF; padding: 15px; border-radius: 8px; border: 1px solid #e0f3f7; margin-bottom: 20px;">
                    <p style="margin: 0;"><strong>Delivery Destination:</strong> {info.get("hostel")}, Room {info.get("room_no")}</p>
                    <p style="margin: 5px 0 0 0;"><strong>Estimated Time:</strong> 5-10 mins</p>
                </div>
                
                <p>Please make sure you are available at your delivery location to receive the order. You will need to provide the delivery PIN to the rider to complete the drop-off.</p>
                <p style="font-size: 12px; color: #94A3B8; text-align: center; margin-top: 30px; border-top: 1px solid #E2E8F0; padding-top: 10px;">
                    Mekal Mart - Campus Delivery Platform
                </p>
            </div>
            """
            background_tasks.add_task(send_resend_email, cust_email, f"Order Out for Delivery - #{order_id} | Mekal Mart", pickup_html)
            
        return {"status": "success"}
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()

@app.post("/delivery/mark-delivered")
async def mark_delivered(data: dict, background_tasks: BackgroundTasks, current_user: dict = Depends(get_delivery_user)):
    order_id = data.get("order_id")
    agent_id = data.get("agent_id")
    pin = data.get("pin")
    
    if str(current_user["user_id"]) != str(agent_id):
        raise HTTPException(status_code=403, detail="Unauthorized agent action")
        
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT delivery_pin, delivery_fee FROM orders WHERE order_id = %s AND delivery_partner_id = %s", (order_id, agent_id))
        order = cursor.fetchone()
        if not order:
            return {"status": "error", "message": "Order not found or not assigned to you."}
        if str(order['delivery_pin']) != str(pin):
            return {"status": "error", "message": "Invalid delivery PIN!"}
 
        cursor.execute("UPDATE orders SET status = 'Delivered' WHERE order_id = %s AND delivery_partner_id = %s", (order_id, agent_id))
        if cursor.rowcount > 0:
            fee = order.get('delivery_fee', 10) or 10
            cursor.execute("UPDATE users SET wallet_balance = wallet_balance + %s WHERE id = %s", (fee, agent_id))
            cursor.execute("INSERT INTO wallet_transactions (user_id, amount, type, description) VALUES (%s, %s, 'credit', %s)",
                           (agent_id, fee, f"Delivery fee for Order #{order_id}"))
            
            # Fetch details for notification email
            cursor.execute("SELECT email, customer_name FROM orders WHERE order_id = %s", (order_id,))
            info = cursor.fetchone()
            
            conn.commit()
            
            if info and info.get("email"):
                cust_email = info["email"]
                cust_name = info.get("customer_name") or "Student"
                
                delivered_html = f"""
                <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #e2e8f0; border-radius: 10px;">
                    <h2 style="color: #1B4332; margin-bottom: 20px;">Order Delivered!</h2>
                    <p>Hi {cust_name},</p>
                    <p>Your order <strong>#{order_id}</strong> has been successfully delivered to your location.</p>
                    
                    <div style="background-color: #F9FBFF; padding: 15px; border-radius: 8px; border: 1px solid #e0f3f7; margin-bottom: 20px;">
                        <p style="margin: 0; font-weight: bold; color: #1B4332;">✅ Status: Delivered</p>
                        <p style="margin: 5px 0 0 0; font-size: 12px; color: #64748B;">Thank you for using Mekal Mart! If you have any feedback, please feel free to reply to this email.</p>
                    </div>
                    
                    <p style="font-size: 12px; color: #94A3B8; text-align: center; margin-top: 30px; border-top: 1px solid #E2E8F0; padding-top: 10px;">
                        Mekal Mart - Campus Delivery Platform
                    </p>
                </div>
                """
                background_tasks.add_task(send_resend_email, cust_email, f"Order Delivered Successfully - #{order_id} | Mekal Mart", delivered_html)
                
            return {"status": "success"}
        return {"status": "error", "message": "Could not mark as delivered."}
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()

@app.post("/orders/verify-dropoff")
async def verify_dropoff(data: VerifyDropoffRequest, background_tasks: BackgroundTasks, current_user: dict = Depends(get_delivery_user)):
    order_id = data.order_id
    agent_id = data.agent_id
    pin = data.pin
    
    if str(current_user["user_id"]) != str(agent_id):
        raise HTTPException(status_code=403, detail="Unauthorized agent action")
        
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT delivery_pin, delivery_fee, status FROM orders WHERE order_id = %s AND delivery_partner_id = %s FOR UPDATE", (order_id, agent_id))
        order = cursor.fetchone()
        if not order:
            return {"status": "error", "message": "Order not found or not assigned to you."}
            
        if order["status"] == "Delivered":
            return {"status": "error", "message": "Order is already marked as Delivered."}
            
        if str(order['delivery_pin']) != str(pin):
            return {"status": "error", "message": "Invalid delivery PIN!"}
            
        cursor.execute("UPDATE orders SET status = 'Delivered' WHERE order_id = %s AND delivery_partner_id = %s", (order_id, agent_id))
        if cursor.rowcount > 0:
            fee = order.get('delivery_fee') or 10.0
            cursor.execute("UPDATE users SET wallet_balance = wallet_balance + %s WHERE id = %s", (fee, agent_id))
            cursor.execute("INSERT INTO wallet_transactions (user_id, amount, type, description) VALUES (%s, %s, 'credit', %s)",
                           (agent_id, fee, f"Delivery fee for Order #{order_id}"))
            
            # Fetch details for notification email
            cursor.execute("SELECT email, customer_name FROM orders WHERE order_id = %s", (order_id,))
            info = cursor.fetchone()
            
            conn.commit()
            
            if info and info.get("email"):
                cust_email = info["email"]
                cust_name = info.get("customer_name") or "Student"
                
                delivered_html = f"""
                <div style="font-family: Arial, sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #e2e8f0; border-radius: 10px;">
                    <h2 style="color: #1B4332; margin-bottom: 20px;">Order Delivered!</h2>
                    <p>Hi {cust_name},</p>
                    <p>Your order <strong>#{order_id}</strong> has been successfully delivered to your location.</p>
                    
                    <div style="background-color: #F9FBFF; padding: 15px; border-radius: 8px; border: 1px solid #e0f3f7; margin-bottom: 20px;">
                        <p style="margin: 0; font-weight: bold; color: #1B4332;">✅ Status: Delivered</p>
                        <p style="margin: 5px 0 0 0; font-size: 12px; color: #64748B;">Thank you for using Mekal Mart! If you have any feedback, please feel free to reply to this email.</p>
                    </div>
                    
                    <p style="font-size: 12px; color: #94A3B8; text-align: center; margin-top: 30px; border-top: 1px solid #E2E8F0; padding-top: 10px;">
                        Mekal Mart - Campus Delivery Platform
                    </p>
                </div>
                """
                background_tasks.add_task(send_resend_email, cust_email, f"Order Delivered Successfully - #{order_id} | Mekal Mart", delivered_html)
                
            return {"status": "success", "message": "Delivery completed successfully!"}
        return {"status": "error", "message": "Could not mark order as delivered."}
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()

@app.post("/delivery/withdraw")
async def request_withdraw(data: dict, current_user: dict = Depends(get_delivery_user)):
    agent_id = data.get("agent_id")
    amount = float(data.get("amount", 0))
    
    if str(current_user["user_id"]) != str(agent_id):
        raise HTTPException(status_code=403, detail="Unauthorized agent action")
        
    if amount <= 0:
        return {"status": "error", "message": "Invalid amount"}
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT wallet_balance FROM users WHERE id = %s", (agent_id,))
        agent = cursor.fetchone()
        if not agent or agent['wallet_balance'] < amount:
            return {"status": "error", "message": "Insufficient balance"}
        
        cursor.execute("UPDATE users SET wallet_balance = wallet_balance - %s WHERE id = %s", (amount, agent_id))
        cursor.execute("INSERT INTO wallet_transactions (user_id, amount, type, description) VALUES (%s, %s, 'debit', 'Wallet Withdraw Request')",
                       (agent_id, amount))
        conn.commit()
        return {"status": "success", "message": "Withdrawal requested successfully!"}
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()

@app.get("/wallet/balance")
async def get_wallet_balance(phone: str, current_user: dict = Depends(get_current_user)):
    # Verify auth
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT email, phone, wallet_balance FROM users WHERE id = %s", (current_user["user_id"],))
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        if phone != user["email"] and phone != user["phone"]:
            raise HTTPException(status_code=403, detail="Unauthorized wallet balance request")
        return {"status": "success", "balance": float(user['wallet_balance']) if user else 0.0}
    finally:
        conn.close()

@app.get("/orders/history")
async def get_history(phone: str, current_user: dict = Depends(get_current_user)):
    # Verify auth
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT email, phone FROM users WHERE id = %s", (current_user["user_id"],))
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=401, detail="User not found")
        if phone != user["email"] and phone != user["phone"]:
            raise HTTPException(status_code=403, detail="Unauthorized history request")
            
        cursor.execute("""
            SELECT o.order_id as id, o.total_amount as total, o.hostel, o.items, o.created_at as date, o.status, o.delivery_pin,
                   pp.partner_name as agent_name, u.phone as agent_phone, pp.profile_image as agent_image
            FROM orders o
            LEFT JOIN users u ON o.delivery_partner_id = u.id
            LEFT JOIN partner_profiles pp ON u.id = pp.user_id
            WHERE o.phone = %s OR o.email = %s ORDER BY o.created_at DESC
        """, (phone, phone))
        res = cursor.fetchall()
        
        # Enforce contact records visibility (Privacy Safeguard)
        for row in res:
            if row.get("status") not in ["Driver Assigned", "Dispatched", "Out for Delivery", "Picked Up"]:
                row["agent_phone"] = None
                
        return res
    finally:
        conn.close()



@app.get("/admin/orders")
async def admin_get_orders(current_user: dict = Depends(get_super_admin)):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT id, order_id, customer_name, phone, email, hostel, room_no, items, total_amount, delivery_fee, status, pickup_pin, agent_id, delivery_partner_id, created_at 
            FROM orders 
            ORDER BY created_at DESC 
            LIMIT 50
        """)
        orders = cursor.fetchall()
        return orders
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

class AdminUpdateStatus(BaseModel):
    order_id: str
    status: str

@app.post("/admin/update_status")
async def admin_update_status(data: AdminUpdateStatus, current_user: dict = Depends(get_super_admin)):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("UPDATE orders SET status = %s WHERE order_id = %s", (data.status, data.order_id))
        conn.commit()
        return {"status": "success"}
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()

@app.post("/profile/upload")
async def profile_upload(
    role: str = Form(...), 
    user_id: str = Form(...), 
    image: UploadFile = File(...),
    current_user: dict = Depends(get_current_user)
):
    # Security: Validate upload is for self
    if str(current_user["user_id"]) != str(user_id):
        raise HTTPException(status_code=403, detail="Unauthorized upload attempt")
        
    # Safe Upload and Sanitization
    db_path = save_and_sanitize_upload(image)
    
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        if role == 'vendor':
            cursor.execute("UPDATE vendor_profiles SET shop_image = %s WHERE user_id = %s", (db_path, int(user_id)))
        elif role == 'partner' or role == 'delivery':
            cursor.execute("UPDATE partner_profiles SET profile_image = %s WHERE user_id = %s", (db_path, int(user_id)))
        else:
            cursor.execute("UPDATE student_profiles SET profile_image = %s WHERE user_id = %s", (db_path, int(user_id)))
        conn.commit()
        return {"status": "success", "image_url": db_path}
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()

@app.post("/vendor/add-product")
async def add_product(
    shop_id: str = Form(...), 
    name: str = Form(...), 
    price: float = Form(...), 
    category: str = Form(...), 
    product_img: Optional[UploadFile] = File(None),
    img_url: Optional[str] = Form(None),
    current_user: dict = Depends(get_admin_user)
):
    # Verify vendor owns this shop
    if current_user["sub"] != shop_id:
        raise HTTPException(status_code=403, detail="Unauthorized to add items to this shop")
        
    # Determine the image path
    db_path = "store-placeholder.png"
    if product_img and product_img.filename:
        db_path = save_and_sanitize_upload(product_img)
    elif img_url:
        db_path = img_url
        
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO products (shop_id, name, category, price, img, in_stock, approval_status) VALUES (%s, %s, %s, %s, %s, 1, 'Pending')", 
                       (shop_id, name, category, price, db_path))
        conn.commit()
        return {"status": "success"}
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()

class UpdateProduct(BaseModel):
    id: int
    price: float
    in_stock: int
    stock_count: int

@app.post("/vendor/update-product")
async def update_product(data: UpdateProduct, current_user: dict = Depends(get_admin_user)):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        # Check ownership
        cursor.execute("SELECT shop_id FROM products WHERE id = %s", (data.id,))
        prod = cursor.fetchone()
        if not prod or prod["shop_id"] != current_user["sub"]:
            raise HTTPException(status_code=403, detail="Unauthorized to modify this product")
            
        cursor.execute("UPDATE products SET price = %s, in_stock = %s, stock_count = %s WHERE id = %s", 
                       (data.price, data.in_stock, data.stock_count, data.id))
        conn.commit()
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()

class DeleteProduct(BaseModel):
    id: int

@app.post("/vendor/delete-product")
async def delete_product(data: DeleteProduct, current_user: dict = Depends(get_admin_user)):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        # Check ownership
        cursor.execute("SELECT shop_id FROM products WHERE id = %s", (data.id,))
        prod = cursor.fetchone()
        if not prod or prod["shop_id"] != current_user["sub"]:
            raise HTTPException(status_code=403, detail="Unauthorized to delete this product")
            
        cursor.execute("DELETE FROM products WHERE id = %s", (data.id,))
        conn.commit()
        return {"status": "success"}
    except HTTPException:
        raise
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()

class VendorUpdateStatus(BaseModel):
    order_id: str
    shop_id: str
    status: str

@app.get("/vendor/orders")
async def vendor_get_orders(shop_id: str, current_user: dict = Depends(get_admin_user)):
    # Verify owner
    if current_user["sub"] != shop_id:
        raise HTTPException(status_code=403, detail="Unauthorized orders request")
        
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT id, order_id, customer_name, phone, email, hostel, room_no, items, total_amount, delivery_fee, status, pickup_pin, agent_id, delivery_partner_id, created_at 
            FROM orders 
            WHERE shop_id = %s
            ORDER BY created_at DESC
        """, (shop_id,))
        orders = cursor.fetchall()
        return orders
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.post("/vendor/update-status")
async def vendor_update_status(data: VendorUpdateStatus, current_user: dict = Depends(get_admin_user)):
    # Verify owner
    if current_user["sub"] != data.shop_id:
        raise HTTPException(status_code=403, detail="Unauthorized order update request")
        
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        # Check order belongs to this shop
        cursor.execute("SELECT shop_id FROM orders WHERE order_id = %s", (data.order_id,))
        order = cursor.fetchone()
        if not order or order["shop_id"] != data.shop_id:
            raise HTTPException(status_code=400, detail="Order not found or does not belong to this shop")
            
        cursor.execute("UPDATE orders SET status = %s WHERE order_id = %s", (data.status, data.order_id))
        conn.commit()
        return {"status": "success"}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()

class WalletCreateOrderRequest(BaseModel):
    amount: float

@app.post("/wallet/create-order")
async def wallet_create_order(data: WalletCreateOrderRequest, current_user: dict = Depends(get_current_user)):
    amount = data.amount
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid amount")
        
    razorpay_key_id = os.getenv("RAZORPAY_KEY_ID", "rzp_test_SgSoKubbC5Rmg6")
    razorpay_key_secret = os.getenv("RAZORPAY_KEY_SECRET", "")
    
    # Generate mock order ID if secret is not set
    if not razorpay_key_secret:
        mock_order_id = f"order_mock_{int(time.time())}_{random.randint(1000, 9999)}"
        return {"status": "success", "order_id": mock_order_id, "key_id": razorpay_key_id, "mock": True}
        
    # Call Razorpay to generate order
    import urllib.request
    import json
    import base64
    
    url = "https://api.razorpay.com/v1/orders"
    payload = json.dumps({
        "amount": int(amount * 100), # in paise
        "currency": "INR",
        "receipt": f"rcpt_{int(time.time())}_{random.randint(1000, 9999)}"
    }).encode("utf-8")
    
    req = urllib.request.Request(url, data=payload, method="POST")
    auth_str = f"{razorpay_key_id}:{razorpay_key_secret}"
    auth_b64 = base64.b64encode(auth_str.encode("utf-8")).decode("utf-8")
    req.add_header("Authorization", f"Basic {auth_b64}")
    req.add_header("Content-Type", "application/json")
    
    try:
        with urllib.request.urlopen(req) as response:
            res_data = json.loads(response.read().decode("utf-8"))
            return {
                "status": "success",
                "order_id": res_data["id"],
                "key_id": razorpay_key_id
            }
    except Exception as e:
        print(f"Razorpay Order API Error: {e}")
        mock_order_id = f"order_mock_{int(time.time())}_{random.randint(1000, 9999)}"
        return {"status": "success", "order_id": mock_order_id, "key_id": razorpay_key_id, "mock": True}

class WalletAdd(BaseModel):
    razorpay_payment_id: str
    razorpay_order_id: str
    razorpay_signature: str
    amount: float
    phone: str

@app.post("/wallet/add")
async def wallet_add(data: WalletAdd, current_user: dict = Depends(get_current_user)):
    # Verify token
    if current_user["sub"] != data.phone:
        raise HTTPException(status_code=403, detail="Unauthorized wallet update")
        
    razorpay_key_secret = os.getenv("RAZORPAY_KEY_SECRET", "")
    is_mock = data.razorpay_order_id.startswith("order_mock_")
    dev_mode = os.getenv("DEV_MODE", "true").lower() == "true"
    
    if is_mock and not dev_mode:
        raise HTTPException(status_code=400, detail="Mock payments are disabled in production.")
        
    # Verify payment signature server-side
    if not is_mock and razorpay_key_secret:
        import hmac
        import hashlib
        
        msg = f"{data.razorpay_order_id}|{data.razorpay_payment_id}".encode("utf-8")
        secret = razorpay_key_secret.encode("utf-8")
        generated_signature = hmac.new(secret, msg, hashlib.sha256).hexdigest()
        
        if generated_signature != data.razorpay_signature:
            raise HTTPException(status_code=400, detail="Invalid payment signature. Possible fraud attempt.")
            
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id FROM users WHERE phone = %s OR email = %s", (data.phone, data.phone))
        u = cursor.fetchone()
        if u:
            cursor.execute("UPDATE users SET wallet_balance = wallet_balance + %s WHERE id = %s", (data.amount, u['id']))
            cursor.execute("INSERT INTO wallet_transactions (user_id, amount, type, description) VALUES (%s, %s, 'credit', %s)", 
                           (u['id'], data.amount, f"Razorpay: {data.razorpay_payment_id}"))
        
        conn.commit()
        return {"status": "success"}
    except Exception as e:
        conn.rollback()
        return {"status": "error", "message": str(e)}
    finally:
        conn.close()

@app.post("/ai/recommendations")
async def get_ai_recommendations(data: dict):
    prompt = data.get("prompt")
    import urllib.request
    gemini_api_key = os.getenv("GEMINI_API_KEY", "")
    if not gemini_api_key:
        return {"error": "Gemini API key not configured. Set GEMINI_API_KEY in .env"}
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={gemini_api_key}"
    payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}

# --- MASTER ADMIN GATEKEEPING & APPROVAL ENDPOINTS ---

class ApproveUserRequest(BaseModel):
    user_id: int
    role: str
    action: str = "approve"

class ApproveProductRequest(BaseModel):
    product_id: int
    action: str = "approve"

@app.get("/auth/me")
async def get_current_user_profile(current_user: dict = Depends(get_current_user)):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    try:
        user_id = current_user["user_id"]
        cursor.execute("SELECT id, email, phone, role, wallet_balance FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="User not found")
            
        user_data = {
            "id": user["id"],
            "email": user["email"],
            "phone": user["phone"],
            "role": user["role"],
            "wallet_balance": float(user["wallet_balance"])
        }
        
        if user["role"] == 'vendor':
            cursor.execute("SELECT shop_name, shop_category, is_approved FROM vendor_profiles WHERE user_id = %s", (user_id,))
            vp = cursor.fetchone()
            if vp:
                user_data["shop_name"] = vp["shop_name"]
                user_data["shop_category"] = vp["shop_category"]
                user_data["is_approved"] = bool(vp["is_approved"])
        elif user["role"] == 'partner' or user["role"] == 'delivery':
            cursor.execute("SELECT partner_name as name, profile_image as avatar_url, status FROM partner_profiles WHERE user_id = %s", (user_id,))
            pp = cursor.fetchone()
            if pp:
                user_data["name"] = pp["name"]
                user_data["avatar_url"] = pp["avatar_url"]
                user_data["status"] = pp["status"]
                
        return user_data
    finally:
        conn.close()

@app.get("/admin/pending-requests")
async def get_pending_requests(current_user: dict = Depends(get_super_admin)):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor(dictionary=True)
    try:
        # Get pending vendors
        cursor.execute("""
            SELECT vp.user_id, vp.shop_name, vp.shop_category, vp.shop_image, u.email, u.phone 
            FROM vendor_profiles vp
            JOIN users u ON vp.user_id = u.id
            WHERE vp.is_approved = FALSE
        """)
        pending_vendors = cursor.fetchall()
        
        # Get pending riders
        cursor.execute("""
            SELECT pp.user_id, pp.partner_name, pp.vehicle_number, pp.profile_image, u.email, u.phone
            FROM partner_profiles pp
            JOIN users u ON pp.user_id = u.id
            WHERE pp.status = 'Pending Approval'
        """)
        pending_riders = cursor.fetchall()
        
        # Get pending products
        cursor.execute("""
            SELECT p.id, p.shop_id, p.name, p.category, p.price, p.img, p.stock_count, p.in_stock, p.approval_status,
                   vp.shop_name
            FROM products p
            LEFT JOIN users u ON p.shop_id = u.email
            LEFT JOIN vendor_profiles vp ON u.id = vp.user_id
            WHERE p.approval_status = 'Pending'
        """)
        pending_products = cursor.fetchall()
        
        return {
            "status": "success",
            "vendors": pending_vendors,
            "riders": pending_riders,
            "products": pending_products
        }
    finally:
        conn.close()

@app.post("/admin/approve-user")
async def approve_user(data: ApproveUserRequest, current_user: dict = Depends(get_super_admin)):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor()
    try:
        if data.role == 'vendor':
            if data.action == "approve":
                cursor.execute("UPDATE vendor_profiles SET is_approved = TRUE WHERE user_id = %s", (data.user_id,))
            else:
                cursor.execute("DELETE FROM vendor_profiles WHERE user_id = %s", (data.user_id,))
        elif data.role in ['partner', 'delivery']:
            if data.action == "approve":
                cursor.execute("UPDATE partner_profiles SET status = 'Offline' WHERE user_id = %s", (data.user_id,))
            else:
                cursor.execute("DELETE FROM partner_profiles WHERE user_id = %s", (data.user_id,))
        else:
            raise HTTPException(status_code=400, detail="Invalid role for approval")
            
        conn.commit()
        return {"status": "success"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.post("/admin/approve-product")
async def approve_product(data: ApproveProductRequest, current_user: dict = Depends(get_super_admin)):
    conn = get_db_connection()
    if not conn:
        raise HTTPException(status_code=500, detail="Database connection failed")
    cursor = conn.cursor()
    try:
        status_val = 'Approved' if data.action == "approve" else 'Rejected'
        cursor.execute("UPDATE products SET approval_status = %s WHERE id = %s", (status_val, data.product_id))
        conn.commit()
        return {"status": "success"}
    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

# Serve public static assets individually to prevent exposing sensitive root files (.env, main.py)
@app.get("/config.js")
async def serve_config():
    return FileResponse("config.js")

@app.get("/script.js")
async def serve_script():
    return FileResponse("script.js")

@app.get("/style.css")
async def serve_style():
    return FileResponse("style.css")

@app.get("/logo.png")
async def serve_logo():
    return FileResponse("logo.png")

@app.get("/store-placeholder.png")
async def serve_store_placeholder():
    return FileResponse("store-placeholder.png")

@app.get("/manifest.json")
async def serve_manifest():
    return FileResponse("manifest.json")

@app.get("/sw.js")
async def serve_sw():
    return FileResponse("sw.js", media_type="application/javascript")

@app.get("/admin_login.html")
async def serve_admin_login():
    return FileResponse("admin_login.html")

@app.get("/admin_dashboard.html")
async def serve_admin_dashboard():
    return FileResponse("admin_dashboard.html")

@app.get("/vendor_dashboard.html")
async def serve_vendor_dashboard():
    return FileResponse("vendor_dashboard.html")

@app.get("/delivery_dashboard.html")
async def serve_delivery_dashboard():
    return FileResponse("delivery_dashboard.html")

@app.get("/")
async def serve_frontend():
    return FileResponse("index.html")

@app.get("/index.html")
async def serve_index_html():
    return FileResponse("index.html")


os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
