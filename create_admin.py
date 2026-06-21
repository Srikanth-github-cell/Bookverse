import sqlite3
from werkzeug.security import generate_password_hash
from getpass import getpass

def create_admin():
    print("=== Create Admin Account ===\n")
    
    username = input("Enter admin username: ").strip()
    if not username:
        print("Error: Username cannot be empty!")
        return
    
    password = getpass("Enter admin password: ")
    if not password:
        print("Error: Password cannot be empty!")
        return
    
    confirm_password = getpass("Confirm admin password: ")
    if password != confirm_password:
        print("Error: Passwords do not match!")
        return
    
    conn = sqlite3.connect('bookverse.db')
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM admins WHERE username = ?", (username,))
    if cursor.fetchone():
        print(f"Error: Admin with username '{username}' already exists!")
        conn.close()
        return
    
    password_hash = generate_password_hash(password)
    cursor.execute("INSERT INTO admins (username, password_hash) VALUES (?, ?)", (username, password_hash))
    conn.commit()
    conn.close()
    
    print(f"\n✓ Admin account '{username}' created successfully!")
    print(f"  Username: {username}")
    print(f"  You can now login at /admin/login")

if __name__ == "__main__":
    create_admin()
