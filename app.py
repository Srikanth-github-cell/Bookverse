from flask import Flask, render_template, redirect, url_for, request, flash, jsonify, send_from_directory, send_file
from flask_login import LoginManager, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
import sqlite3
import os
from functools import wraps
from email_services import EmailBackend
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.config['SECRET_KEY'] = os.environ.get('SESSION_SECRET', 'dev-secret-key-change-in-production')
# ✅ Step 1: Set the folders in app.config
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['BOOKS_FOLDER'] = 'static/books'
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

# --- Email Config from .env ---
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'True') == 'True'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
app.config['MAIL_DEFAULT_SENDER'] = (
    os.getenv('MAIL_DEFAULT_SENDER_NAME'),
    os.getenv('MAIL_DEFAULT_SENDER_EMAIL')
)

# Initialize Email Backend
email_backend = EmailBackend(app)

ALLOWED_IMAGE_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'webp'}
ALLOWED_PDF_EXTENSIONS = {'pdf'}


# ✅ Step 2: Create the folders AFTER defining them
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(app.config['BOOKS_FOLDER'], exist_ok=True)


login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

def get_db():
    conn = sqlite3.connect('bookverse.db')
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            description TEXT
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS books (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            author TEXT NOT NULL,
            category_id INTEGER,
            description TEXT,
            cover_image TEXT,
            pdf_file TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (category_id) REFERENCES categories (id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS favorites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (book_id) REFERENCES books (id),
            UNIQUE(user_id, book_id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS reading_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            last_page INTEGER DEFAULT 1,
            last_read_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (book_id) REFERENCES books (id),
            UNIQUE(user_id, book_id)
        )
    ''')
    
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS ratings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            book_id INTEGER NOT NULL,
            rating INTEGER CHECK(rating >= 1 AND rating <= 5),
            review TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            approved BOOLEAN DEFAULT 0,
            FOREIGN KEY (user_id) REFERENCES users (id),
            FOREIGN KEY (book_id) REFERENCES books (id),
            UNIQUE(user_id, book_id)
        )
    ''')
    
    cursor.execute("SELECT COUNT(*) as count FROM admins")
    if cursor.fetchone()['count'] == 0:
        admin_password = generate_password_hash('admin123', method='pbkdf2:sha256')
        cursor.execute("INSERT INTO admins (username, password_hash) VALUES (?, ?)", ('admin', admin_password))
    
    cursor.execute("SELECT COUNT(*) as count FROM categories")
    if cursor.fetchone()['count'] == 0:
        default_categories = [
            ('Fiction', 'Fictional stories and novels'),
            ('Non-Fiction', 'Real-life stories and factual content'),
            ('Science', 'Scientific books and research'),
            ('Technology', 'Technology and programming books'),
            ('History', 'Historical books and biographies'),
            ('Self-Help', 'Personal development and motivation'),
            ('Mystery', 'Mystery and thriller novels'),
            ('Romance', 'Romantic stories and novels'),
            ('Fantasy', 'Fantasy and magical worlds'),
            ('Biography', 'Life stories of notable people')
        ]
        cursor.executemany("INSERT INTO categories (name, description) VALUES (?, ?)", default_categories)
    
    conn.commit()
    conn.close()

class User:
    def __init__(self, id, username, email):
        self.id = id
        self.username = username
        self.email = email
        self.is_authenticated = True
        self.is_active = True
        self.is_anonymous = False
    
    def get_id(self):
        return str(self.id)

@login_manager.user_loader
def load_user(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE id = ?", (user_id,))
    user_data = cursor.fetchone()
    conn.close()
    
    if user_data:
        return User(user_data['id'], user_data['username'], user_data['email'])
    return None

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'admin_id' not in request.cookies:
            flash('Please log in as admin to access this page.', 'error')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def allowed_file(filename, allowed_extensions):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in allowed_extensions

@app.route('/')
def index():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM categories ORDER BY name')
    categories = cursor.fetchall()
    
    books_by_category = {}
    for category in categories:
        cursor.execute('''
            SELECT books.*, categories.name as category_name 
            FROM books 
            LEFT JOIN categories ON books.category_id = categories.id 
            WHERE books.category_id = ?
            ORDER BY books.created_at DESC
        ''', (category['id'],))
        books_by_category[category['name']] = cursor.fetchall()

    # All books for horizontal scroll
    cursor.execute('''
        SELECT books.*, categories.name as category_name 
        FROM books 
        LEFT JOIN categories ON books.category_id = categories.id 
        ORDER BY books.created_at DESC
    ''')
    all_books = cursor.fetchall()
    
    # Top 10 most read books (by reading_history count)
    cursor.execute('''
        SELECT books.*, categories.name as category_name, COUNT(reading_history.id) as read_count
        FROM books
        JOIN reading_history ON books.id = reading_history.book_id
        LEFT JOIN categories ON books.category_id = categories.id
        GROUP BY books.id
        ORDER BY read_count DESC, books.created_at DESC
        LIMIT 10
    ''')
    most_read_books = cursor.fetchall()

    # Recent books (latest added)
    cursor.execute('''
        SELECT books.*, categories.name as category_name 
        FROM books 
        LEFT JOIN categories ON books.category_id = categories.id 
        ORDER BY books.created_at DESC
        LIMIT 10
    ''')
    recent_books = cursor.fetchall()
    
    conn.close()
    
    return render_template('index.html', 
                         all_books=all_books,
                         most_read_books=most_read_books,
                         recent_books=recent_books,
                         books_by_category=books_by_category, 
                         categories=categories)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        email = request.form.get('email')
        password = request.form.get('password')
        
        conn = get_db()
        cursor = conn.cursor()
        
        cursor.execute("SELECT * FROM users WHERE username = ? OR email = ?", (username, email))
        if cursor.fetchone():
            conn.close()
            flash('Username or email already exists!', 'error')
            return redirect(url_for('register'))
        
        password_hash = generate_password_hash(password, method='pbkdf2:sha256')
        cursor.execute("INSERT INTO users (username, email, password_hash) VALUES (?, ?, ?)",
                      (username, email, password_hash))
        conn.commit()
        conn.close()
        
        flash('Registration successful! Please log in.', 'success')
        # Prepare the email content
        body = f"""
        Hi {username} 👋,

        Welcome to BookVerse Library Management System! 🎉
        Your account has been created successfully.

        You can now start exploring and borrowing books. 📖

        Happy Reading! ✨
        – The BookVerse Team
        """

        # Send email using your EmailBackend
        success, message = email_backend.send_email(
            subject="Welcome to BookVerse 📚",
            recipients=[email],
            body=body
        )
        return redirect(url_for('login'))
    
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM users WHERE email = ?", (email,))
        user_data = cursor.fetchone()
        conn.close()
        
        valid = False
        if user_data:
            try:
                valid = check_password_hash(user_data['password_hash'], password)
            except Exception:
                valid = False
        
        if valid:
            user = User(user_data['id'], user_data['username'], user_data['email'])
            login_user(user)
            flash('Login successful!', 'success')
            # Redirect to 'next' page if present and safe, else go to dashboard
            next_page = request.args.get('next')
            if next_page and next_page.startswith('/'):
                return redirect(next_page)
            return redirect(url_for('index'))
        
        flash('Invalid email or password!', 'error')
    
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Logged out successfully!', 'success')
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT books.*, categories.name as category_name, favorites.created_at as fav_date
        FROM favorites
        JOIN books ON favorites.book_id = books.id
        LEFT JOIN categories ON books.category_id = categories.id
        WHERE favorites.user_id = ?
        ORDER BY favorites.created_at DESC
    ''', (current_user.id,))
    favorites = cursor.fetchall()
    
    favorites_by_category = {}
    for fav in favorites:
        category = fav['category_name'] or 'Uncategorized'
        if category not in favorites_by_category:
            favorites_by_category[category] = []
        favorites_by_category[category].append(fav)
    
    cursor.execute('''
        SELECT books.*, categories.name as category_name, reading_history.last_page, reading_history.last_read_at
        FROM reading_history
        JOIN books ON reading_history.book_id = books.id
        LEFT JOIN categories ON books.category_id = categories.id
        WHERE reading_history.user_id = ?
        ORDER BY reading_history.last_read_at DESC
        LIMIT 10
    ''', (current_user.id,))
    recent_books = cursor.fetchall()
    
    conn.close()
    
    return render_template('dashboard.html', 
                         favorites_by_category=favorites_by_category,
                         recent_books=recent_books,
                         user=current_user)

@app.route('/book/<int:book_id>')
def book_detail(book_id):
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT books.*, categories.name as category_name 
        FROM books 
        LEFT JOIN categories ON books.category_id = categories.id 
        WHERE books.id = ?
    ''', (book_id,))
    book = cursor.fetchone()
    
    if not book:
        flash('Book not found!', 'error')
        return redirect(url_for('index'))
    
    cursor.execute('''
        SELECT ratings.*, users.username 
        FROM ratings 
        JOIN users ON ratings.user_id = users.id 
        WHERE ratings.book_id = ? AND ratings.approved = 1
        ORDER BY ratings.created_at DESC
    ''', (book_id,))
    reviews = cursor.fetchall()
    
    cursor.execute('SELECT AVG(rating) as avg_rating, COUNT(*) as count FROM ratings WHERE book_id = ? AND approved = 1', (book_id,))
    rating_data = cursor.fetchone()
    
    is_favorite = False
    user_rating = None
    last_page = 1
    
    if current_user.is_authenticated:
        cursor.execute('SELECT * FROM favorites WHERE user_id = ? AND book_id = ?', (current_user.id, book_id))
        is_favorite = cursor.fetchone() is not None
        
        cursor.execute('SELECT * FROM ratings WHERE user_id = ? AND book_id = ?', (current_user.id, book_id))
        user_rating = cursor.fetchone()
        
        cursor.execute('SELECT last_page FROM reading_history WHERE user_id = ? AND book_id = ?', (current_user.id, book_id))
        history = cursor.fetchone()
        if history:
            last_page = history['last_page']
    
    conn.close()
    
    return render_template('book_detail.html', 
                         book=book, 
                         reviews=reviews,
                         avg_rating=rating_data['avg_rating'],
                         rating_count=rating_data['count'],
                         is_favorite=is_favorite,
                         user_rating=user_rating,
                         last_page=last_page)

@app.route('/read/<int:book_id>')
@login_required
def read_book(book_id):
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM books WHERE id = ?', (book_id,))
    book = cursor.fetchone()
    
    if not book or not book['pdf_file']:
        flash('Book PDF not found!', 'error')
        return redirect(url_for('index'))
    
    last_page = 1
    if current_user.is_authenticated:
        cursor.execute('SELECT last_page FROM reading_history WHERE user_id = ? AND book_id = ?', 
                      (current_user.id, book_id))
        history = cursor.fetchone()
        if history:
            last_page = history['last_page']
    
    conn.close()
    
    return render_template('reader.html', book=book, last_page=last_page)

@app.route('/api/book_pdf/<int:book_id>')
@login_required
def serve_book_pdf(book_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute('SELECT * FROM books WHERE id = ?', (book_id,))
    book = cursor.fetchone()
    conn.close()

    if not book or not book['pdf_file']:
        flash('Book PDF not found!', 'error')
        return redirect(url_for('index'))

    pdf_path = os.path.join(app.config['BOOKS_FOLDER'], book['pdf_file'])
    if not os.path.exists(pdf_path):
        flash('Book PDF not found!', 'error')
        return redirect(url_for('index'))

    response = send_file(pdf_path, mimetype='application/pdf', as_attachment=False, download_name=book['pdf_file'])
    response.headers['Cache-Control'] = 'no-store'
    response.headers['Content-Disposition'] = f"inline; filename=\"{book['pdf_file']}\""
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response

@app.route('/api/save_progress', methods=['POST'])
@login_required
def save_progress():
    data = request.json
    book_id = data.get('book_id')
    page = data.get('page', 1)
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO reading_history (user_id, book_id, last_page, last_read_at)
        VALUES (?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(user_id, book_id) 
        DO UPDATE SET last_page = ?, last_read_at = CURRENT_TIMESTAMP
    ''', (current_user.id, book_id, page, page))
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True})

@app.route('/api/toggle_favorite', methods=['POST'])
@login_required
def toggle_favorite():
    data = request.json
    book_id = data.get('book_id')
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM favorites WHERE user_id = ? AND book_id = ?', (current_user.id, book_id))
    favorite = cursor.fetchone()
    
    if favorite:
        cursor.execute('DELETE FROM favorites WHERE user_id = ? AND book_id = ?', (current_user.id, book_id))
        is_favorite = False
    else:
        cursor.execute('INSERT INTO favorites (user_id, book_id) VALUES (?, ?)', (current_user.id, book_id))
        is_favorite = True
    
    conn.commit()
    conn.close()
    
    return jsonify({'success': True, 'is_favorite': is_favorite})

@app.route('/api/submit_rating', methods=['POST'])
@login_required
def submit_rating():
    data = request.json
    book_id = data.get('book_id')
    rating = data.get('rating')
    review = data.get('review', '')
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO ratings (user_id, book_id, rating, review, approved)
        VALUES (?, ?, ?, ?, 0)
        ON CONFLICT(user_id, book_id) 
        DO UPDATE SET rating = ?, review = ?, approved = 0
    ''', (current_user.id, book_id, rating, review, rating, review))
    
    conn.commit()
    conn.close()

    body = f"""
            Hi Admin 👋,

            A new review has been submitted in BookVerse Library Management System.

            Reviewer: {current_user.username}
            Email: {current_user.email}
            Book ID: {book_id}
            Review Content:{review}

            Status: Pending approval ✅

            Please review and approve it in the admin panel.

            – BookVerse System
            """

    success, message = email_backend.send_email(
        subject="📢 New Review Submitted – Pending Approval",
        recipients=["readwithbookverse@gmail.com"],  
        body=body
    )
        
    return jsonify({'success': True, 'message': 'Rating submitted! Pending admin approval.'})

@app.route('/search')
def search():
    query = request.args.get('q', '').strip()
    category = request.args.get('category', '')
    
    conn = get_db()
    cursor = conn.cursor()
    
    if category:
        cursor.execute('''
            SELECT books.*, categories.name as category_name 
            FROM books 
            LEFT JOIN categories ON books.category_id = categories.id 
            WHERE books.category_id = ? AND (books.title LIKE ? OR books.author LIKE ?)
        ''', (category, f'%{query}%', f'%{query}%'))
    else:
        cursor.execute('''
            SELECT books.*, categories.name as category_name 
            FROM books 
            LEFT JOIN categories ON books.category_id = categories.id 
            WHERE books.title LIKE ? OR books.author LIKE ?
        ''', (f'%{query}%', f'%{query}%'))
    
    books = cursor.fetchall()
    
    cursor.execute('SELECT * FROM categories ORDER BY name')
    categories = cursor.fetchall()
    
    conn.close()
    
    return render_template('search.html', books=books, query=query, categories=categories, selected_category=category)

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if 'admin_id' in request.cookies:
        return redirect(url_for('admin_dashboard'))
    
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM admins WHERE username = ?", (username,))
        admin = cursor.fetchone()
        conn.close()
        
        valid = False
        if admin:
            try:
                valid = check_password_hash(admin['password_hash'], password)
            except Exception:
                valid = False
        
        if valid:
            response = redirect(url_for('admin_dashboard'))
            response.set_cookie('admin_id', str(admin['id']), max_age=86400)
            flash('Admin login successful!', 'success')
            return response
        
        flash('Invalid admin credentials!', 'error')
    
    return render_template('admin/login.html')

@app.route('/admin/register', methods=['GET', 'POST'])
def admin_register():
    if 'admin_id' in request.cookies:
        return redirect(url_for('admin_dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM admins WHERE username = ?", (username,))
        existing = cursor.fetchone()
        if existing:
            conn.close()
            flash('Admin username already exists!', 'error')
            return render_template('admin/register.html')

        password_hash = generate_password_hash(password, method='pbkdf2:sha256')
        cursor.execute("INSERT INTO admins (username, password_hash) VALUES (?, ?)", (username, password_hash))
        conn.commit()
        conn.close()
        flash('Admin created successfully! Please log in.', 'success')
        return redirect(url_for('admin_login'))

    return render_template('admin/register.html')

@app.route('/admin/logout')
def admin_logout():
    response = redirect(url_for('admin_login'))
    response.delete_cookie('admin_id')
    flash('Admin logged out successfully!', 'success')
    return response

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) as count FROM users')
    total_users = cursor.fetchone()['count']
    
    cursor.execute('SELECT COUNT(*) as count FROM books')
    total_books = cursor.fetchone()['count']
    
    cursor.execute('SELECT COUNT(*) as count FROM ratings WHERE approved = 0')
    pending_reviews = cursor.fetchone()['count']
    
    cursor.execute('''
        SELECT books.*, COUNT(reading_history.id) as read_count
        FROM books
        LEFT JOIN reading_history ON books.id = reading_history.book_id
        GROUP BY books.id
        ORDER BY read_count DESC
        LIMIT 5
    ''')
    most_read_books = cursor.fetchall()
    
    cursor.execute('''
        SELECT categories.name, COUNT(books.id) as book_count
        FROM categories
        LEFT JOIN books ON categories.id = books.category_id
        GROUP BY categories.id
        ORDER BY book_count DESC
        LIMIT 5
    ''')
    popular_categories = cursor.fetchall()
    
    conn.close()
    
    return render_template('admin/dashboard.html',
                         total_users=total_users,
                         total_books=total_books,
                         pending_reviews=pending_reviews,
                         most_read_books=most_read_books,
                         popular_categories=popular_categories)

@app.route('/admin/books')
@admin_required
def admin_books():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT books.*, categories.name as category_name 
        FROM books 
        LEFT JOIN categories ON books.category_id = categories.id 
        ORDER BY books.created_at DESC
    ''')
    books = cursor.fetchall()
    
    cursor.execute('SELECT * FROM categories ORDER BY name')
    categories = cursor.fetchall()
    
    conn.close()
    
    return render_template('admin/books.html', books=books, categories=categories)

@app.route('/admin/books/add', methods=['POST'])
@admin_required
def admin_add_book():
    title = request.form.get('title')
    author = request.form.get('author')
    category_id = request.form.get('category_id')
    description = request.form.get('description')
    
    cover_image = request.files.get('cover_image')
    pdf_file = request.files.get('pdf_file')
    
    cover_filename = None
    pdf_filename = None
    
    if cover_image and allowed_file(cover_image.filename, ALLOWED_IMAGE_EXTENSIONS):
        cover_filename = secure_filename(f"{datetime.now().timestamp()}_{cover_image.filename}")
        cover_image.save(os.path.join(app.config['UPLOAD_FOLDER'], cover_filename))
    
    if pdf_file and allowed_file(pdf_file.filename, ALLOWED_PDF_EXTENSIONS):
        pdf_filename = secure_filename(f"{datetime.now().timestamp()}_{pdf_file.filename}")
        pdf_file.save(os.path.join(app.config['BOOKS_FOLDER'], pdf_filename))
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        INSERT INTO books (title, author, category_id, description, cover_image, pdf_file)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (title, author, category_id, description, cover_filename, pdf_filename))

    cursor.execute('SELECT email, username FROM users')
    users = cursor.fetchall() 

    for email, username in users:
        body = f"""
            Hi {username} 👋,

            A new book has just been added to BookVerse Library! 🎉📚

            Title: {title}
            Author: {author}

            {description}

            Start exploring and happy reading! 📖✨

            – The BookVerse Team
            """
        email_backend.send_email(
            subject=f"New Book Added: {title} 📚",
            recipients=[email],
            body=body
        )
    
    conn.commit()
    conn.close()
    
    flash('Book added successfully!', 'success')
    return redirect(url_for('admin_books'))

@app.route('/admin/books/edit/<int:book_id>', methods=['POST'])
@admin_required
def admin_edit_book(book_id):
    title = request.form.get('title')
    author = request.form.get('author')
    category_id = request.form.get('category_id')
    description = request.form.get('description')
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM books WHERE id = ?', (book_id,))
    book = cursor.fetchone()
    
    cover_filename = book['cover_image']
    pdf_filename = book['pdf_file']
    
    cover_image = request.files.get('cover_image')
    pdf_file = request.files.get('pdf_file')
    
    if cover_image and allowed_file(cover_image.filename, ALLOWED_IMAGE_EXTENSIONS):
        if cover_filename and os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], cover_filename)):
            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], cover_filename))
        cover_filename = secure_filename(f"{datetime.now().timestamp()}_{cover_image.filename}")
        cover_image.save(os.path.join(app.config['UPLOAD_FOLDER'], cover_filename))
    
    if pdf_file and allowed_file(pdf_file.filename, ALLOWED_PDF_EXTENSIONS):
        if pdf_filename and os.path.exists(os.path.join(app.config['BOOKS_FOLDER'], pdf_filename)):
            os.remove(os.path.join(app.config['BOOKS_FOLDER'], pdf_filename))
        pdf_filename = secure_filename(f"{datetime.now().timestamp()}_{pdf_file.filename}")
        pdf_file.save(os.path.join(app.config['BOOKS_FOLDER'], pdf_filename))
    
    cursor.execute('''
        UPDATE books 
        SET title = ?, author = ?, category_id = ?, description = ?, cover_image = ?, pdf_file = ?
        WHERE id = ?
    ''', (title, author, category_id, description, cover_filename, pdf_filename, book_id))
    
    conn.commit()
    conn.close()
    
    flash('Book updated successfully!', 'success')
    return redirect(url_for('admin_books'))

@app.route('/admin/books/delete/<int:book_id>')
@admin_required
def admin_delete_book(book_id):
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM books WHERE id = ?', (book_id,))
    book = cursor.fetchone()
    
    if book:
        if book['cover_image'] and os.path.exists(os.path.join(app.config['UPLOAD_FOLDER'], book['cover_image'])):
            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], book['cover_image']))
        
        if book['pdf_file'] and os.path.exists(os.path.join(app.config['BOOKS_FOLDER'], book['pdf_file'])):
            os.remove(os.path.join(app.config['BOOKS_FOLDER'], book['pdf_file']))
        
        cursor.execute('DELETE FROM books WHERE id = ?', (book_id,))
        cursor.execute('DELETE FROM favorites WHERE book_id = ?', (book_id,))
        cursor.execute('DELETE FROM reading_history WHERE book_id = ?', (book_id,))
        cursor.execute('DELETE FROM ratings WHERE book_id = ?', (book_id,))
        
        conn.commit()
        flash('Book deleted successfully!', 'success')
    
    conn.close()
    return redirect(url_for('admin_books'))

@app.route('/admin/users')
@admin_required
def admin_users():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM users ORDER BY created_at DESC')
    users = cursor.fetchall()
    
    conn.close()
    
    return render_template('admin/users.html', users=users)

@app.route('/admin/users/delete/<int:user_id>')
@admin_required
def admin_delete_user(user_id):
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('DELETE FROM users WHERE id = ?', (user_id,))
    cursor.execute('DELETE FROM favorites WHERE user_id = ?', (user_id,))
    cursor.execute('DELETE FROM reading_history WHERE user_id = ?', (user_id,))
    cursor.execute('DELETE FROM ratings WHERE user_id = ?', (user_id,))
    
    conn.commit()
    conn.close()
    
    flash('User deleted successfully!', 'success')
    return redirect(url_for('admin_users'))

@app.route('/admin/categories')
@admin_required
def admin_categories():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('SELECT * FROM categories ORDER BY name')
    categories = cursor.fetchall()
    
    conn.close()
    
    return render_template('admin/categories.html', categories=categories)

@app.route('/admin/categories/add', methods=['POST'])
@admin_required
def admin_add_category():
    name = request.form.get('name')
    description = request.form.get('description')
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('INSERT INTO categories (name, description) VALUES (?, ?)', (name, description))
    
    conn.commit()
    conn.close()
    
    flash('Category added successfully!', 'success')
    return redirect(url_for('admin_categories'))

@app.route('/admin/categories/edit/<int:category_id>', methods=['POST'])
@admin_required
def admin_edit_category(category_id):
    name = request.form.get('name')
    description = request.form.get('description')
    
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('UPDATE categories SET name = ?, description = ? WHERE id = ?', (name, description, category_id))
    
    conn.commit()
    conn.close()
    
    flash('Category updated successfully!', 'success')
    return redirect(url_for('admin_categories'))

@app.route('/admin/categories/delete/<int:category_id>')
@admin_required
def admin_delete_category(category_id):
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('UPDATE books SET category_id = NULL WHERE category_id = ?', (category_id,))
    cursor.execute('DELETE FROM categories WHERE id = ?', (category_id,))
    
    conn.commit()
    conn.close()
    
    flash('Category deleted successfully!', 'success')
    return redirect(url_for('admin_categories'))

@app.route('/admin/reviews')
@admin_required
def admin_reviews():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('''
        SELECT ratings.*, users.username, books.title as book_title
        FROM ratings
        JOIN users ON ratings.user_id = users.id
        JOIN books ON ratings.book_id = books.id
        ORDER BY ratings.created_at DESC
    ''')
    reviews = cursor.fetchall()
    
    conn.close()
    
    return render_template('admin/reviews.html', reviews=reviews)

@app.route('/admin/reviews/approve/<int:review_id>')
@admin_required
def admin_approve_review(review_id):
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('UPDATE ratings SET approved = 1 WHERE id = ?', (review_id,))

    cursor.execute('''
                    SELECT book_id, user_id, review
                    FROM ratings
                    WHERE id = ?
                    ''', (review_id,))

    review = cursor.fetchone()

    cursor.execute('''
        SELECT username, email
        FROM users
        WHERE id = ?
        ''', (review[1],))

    reader = cursor.fetchone()
    
    conn.commit()
    conn.close()

    body = f"""
        Hi {reader[0]} 👋,

        Good news! Your review for Book ID {review[0]} in BookVerse Library Management System has been **approved** ✅

        Thank you for sharing your thoughts. 📖✨  
        Your review is now visible to other readers.

        Happy Reading!  
        – The BookVerse Team
        """

    success, message = email_backend.send_email(
        subject="🎉 Your BookVerse Review is Approved!",
        recipients=[reader[1]],
        body=body
    )
    
    flash('Review approved successfully!', 'success')
    return redirect(url_for('admin_reviews'))

@app.route('/admin/reviews/delete/<int:review_id>')
@admin_required
def admin_delete_review(review_id):
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute('DELETE FROM ratings WHERE id = ?', (review_id,))

    cursor.execute('''
                SELECT book_id, user_id, review
                FROM ratings
                WHERE id = ?
                ''', (review_id,))

    review = cursor.fetchone()

    cursor.execute('''
        SELECT username, email
        FROM users
        WHERE id = ?
        ''', (review[1],))

    reader = cursor.fetchone()
    
    
    conn.commit()
    conn.close()

    body = f"""
            Hi {reader[0]} 👋,

            We wanted to let you know that your review for Book ID {review[0]} in BookVerse Library Management System has been **rejected**. ⚠️

            If you think this was a mistake, or want to submit a revised review, please contact the admin.  

            Thank you for contributing! 📖✨

            – The BookVerse Team
            """

    success, message = email_backend.send_email(
        subject="⚠️ Your BookVerse Review was Rejected",
        recipients=[reader[1]],
        body=body
    )
    
    flash('Review deleted successfully!', 'success')
    return redirect(url_for('admin_reviews'))

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
