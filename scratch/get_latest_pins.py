import os
import sys
from dotenv import load_dotenv
import psycopg2
import psycopg2.extras

load_dotenv()

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "postgres"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "unimart")
}

def get_latest_pins():
    db_url = os.getenv("DATABASE_URL")
    if db_url:
        conn = psycopg2.connect(dsn=db_url)
    else:
        conn = psycopg2.connect(**DB_CONFIG)
    
    cursor = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    try:
        cursor.execute("SELECT order_id, pickup_pin, delivery_pin, status FROM orders ORDER BY created_at DESC LIMIT 1")
        order = cursor.fetchone()
        if order:
            print(f"LATEST_ORDER_ID: {order['order_id']}")
            print(f"PICKUP_PIN: {order['pickup_pin']}")
            print(f"DELIVERY_PIN: {order['delivery_pin']}")
            print(f"STATUS: {order['status']}")
        else:
            print("NO_ORDERS_FOUND")
    finally:
        conn.close()

if __name__ == "__main__":
    get_latest_pins()
