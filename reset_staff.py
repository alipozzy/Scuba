import psycopg2
from werkzeug.security import generate_password_hash

def reset_firdaus_pw():
    try:
        conn = psycopg2.connect(
            dbname="TCK_Leave_System",
            user="postgres",
            password="12345",
            host="localhost"
        )
        cur = conn.cursor()

        # Kita buat hash baru yang fresh dari library werkzeug anda
        new_hash = generate_password_hash("abc123")

        cur.execute("""
            UPDATE employees 
            SET password_hash = %s 
            WHERE email = 'omar@tck.com.my'
        """, (new_hash,))

        conn.commit()
        print("✅ Password telah di-reset kepada: abc123")
    except Exception as e:
        print(f"❌ Ralat: {e}")
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    reset_firdaus_pw()