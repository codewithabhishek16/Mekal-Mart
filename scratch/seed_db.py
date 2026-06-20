import os
import psycopg2
from dotenv import load_dotenv

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "unimart")
}


def seed():
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        conn = psycopg2.connect(dsn=db_url)
    else:
        conn = psycopg2.connect(**DB_CONFIG)
        
    cursor = conn.cursor()
    try:
        # Check if vendor user exists
        cursor.execute("SELECT id FROM users WHERE email = 'vendor@example.com'")
        vendor = cursor.fetchone()
        if not vendor:
            cursor.execute("INSERT INTO users (email, role, auth_provider, wallet_balance) VALUES ('vendor@example.com', 'vendor', 'google', 0.0) RETURNING id")
            vendor_id = cursor.fetchone()[0]
            cursor.execute("INSERT INTO vendor_profiles (user_id, shop_name, shop_category) VALUES (%s, 'Mekal Canteen', 'food')", (vendor_id,))
            print("Seeded vendor user and profile.")
        else:
            vendor_id = vendor[0]
            print("Vendor user already exists.")
            
        # Check if product exists
        cursor.execute("SELECT id FROM products WHERE shop_id = 'vendor@example.com'")
        prod = cursor.fetchone()
        if not prod:
            cursor.execute("INSERT INTO products (shop_id, name, category, price, in_stock) VALUES ('vendor@example.com', 'Samosa Hot', 'food', 15.00, 1)")
            print("Seeded sample product.")
        else:
            print("Sample product already exists.")
            
        conn.commit()
    finally:
        conn.close()

if __name__ == "__main__":
    seed()
