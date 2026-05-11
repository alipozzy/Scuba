import psycopg2
from werkzeug.security import generate_password_hash

def create_management_accounts():
    try:
        conn = psycopg2.connect(
            dbname="TCK_Leave_System",
            user="postgres",
            password="12345",
            host="localhost"
        )
        cur = conn.cursor()

        # Senarai pengurusan (Nama, Email, Role)
        management_team = [
            ("Dato Taufiq", "taufiq@tck.com", "Supervisor"),
            ("Ir. Azman Hakim", "azman@tck.com", "Supervisor"),
            ("Puan Sarah Collins", "sarah@tck.com", "Supervisor")
        ]

        password_default = "tck123" # Password sementara untuk semua bos
        hashed_pw = generate_password_hash(password_default)

        for name, email, role in management_team:
            cur.execute("""
                INSERT INTO employees (full_name, email, role, password_hash) 
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (email) DO NOTHING;
            """, (name, email, role, hashed_pw))
        
        conn.commit()
        print("✅ Akaun Dato Taufiq, Ir. Azman, & Puan Sarah berjaya dicipta!")
        print(f"Password sementara: {password_default}")

    except Exception as e:
        print(f"❌ Ralat: {e}")
    finally:
        cur.close()
        conn.close()

if __name__ == "__main__":
    create_management_accounts()