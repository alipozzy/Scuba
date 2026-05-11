from werkzeug.security import generate_password_hash

# Masukkan password yang Alif mahukan di sini
password_biasa = "password123" 

# Jana hash (ini yang akan disimpan dalam DB)
hash_password = generate_password_hash(password_biasa)

print(hash_password)