import psycopg2
from werkzeug.security import generate_password_hash

def create_admin():
    try:
        # Konfigurasi sambungan ke database anda
        conn = psycopg2.connect(
            dbname="TCK_Leave_System",
            user="postgres",
            password="12345",
            host="localhost",
            port="5432"
        )
        cur = conn.cursor()

        # Maklumat akaun Admin
        full_name = "Admin System"
        email = "admin@tck.com"
        password = "admin123" # Gunakan ini untuk login nanti
        hashed_pw = generate_password_hash(password)

        # Query untuk masukkan data
        # ON CONFLICT digunakan supaya tidak error jika email sudah wujud
        cur.execute("""
            INSERT INTO employees (full_name, email, role, password_hash) 
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (email) DO NOTHING;
        """, (full_name, email, 'Admin', hashed_pw))
        
        conn.commit()
        
        if cur.rowcount > 0:
            print("--- PENDAFTARAN BERJAYA ---")
            print(f"Nama     : {full_name}")
            print(f"Email    : {email}")
            print(f"Password : {password}")
            print("---------------------------")
        else:
            print("⚠️ Akaun dengan email ini sudah wujud dalam database.")

    except Exception as e:
        print(f"❌ Ralat berlaku: {e}")
    finally:
        if 'cur' in locals(): cur.close()
        if 'conn' in locals(): conn.close()

if __name__ == "__main__":
    create_admin()