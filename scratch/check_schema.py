import psycopg2
import os
from dotenv import load_dotenv

load_dotenv("c:/Users/abhie/OneDrive/Desktop/unimart/.env")
db_url = os.getenv("DATABASE_URL")

try:
    conn = psycopg2.connect(dsn=db_url)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT trigger_name, event_manipulation, event_object_table, action_statement
        FROM information_schema.triggers;
    """)
    print("Triggers in DB:")
    for row in cursor.fetchall():
        print(f"  {row[0]} on {row[2]} ({row[1]}): {row[3]}")
    conn.close()
except Exception as e:
    print(f"Error checking triggers: {e}")
