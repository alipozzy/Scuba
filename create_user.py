from werkzeug.security import generate_password_hash
from app import get_db_connection

def create_initial_user():
    conn = get_db_connection()
    cur = conn.cursor()
    
    name = "Wahid" # Nama anda
    email = "wahid@tck.com.my"
    password = "password123" # Password untuk login
    hashed_pw = generate_password_hash(password)
    
    # Pastikan dept_id '1' wujud dalam table departments anda
    cur.execute(
        "INSERT INTO employees (full_name, email, password_hash, dept_id, role) VALUES (%s, %s, %s, %s, %s)",
        (name, email, hashed_pw, 1, 'Staff')
    )
    
    conn.commit()
    cur.close()
    conn.close()
    print("User created successfully!")

if __name__ == "__main__":
    create_initial_user()