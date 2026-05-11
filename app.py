import os
import time
from datetime import datetime, date, timedelta

# Library Pihak Ketiga
import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import text  # <--- Tambah ini

app = Flask(__name__)
app.permanent_session_lifetime = timedelta(minutes=30)
app.secret_key = "tck_esolutions_secret"

# --- KONFIGURASI DATABASE ---
# Format: postgresql://username:password@localhost:port/nama_db
app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://postgres:12345@localhost:5432/TCK_Leave_System'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Inisialisasi objek 'db'
db = SQLAlchemy(app) # <--- Sini punca ralat tadi (perlu define db)

UPLOAD_FOLDER = "static/uploads/mc_docs"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# =========================
# HELPER: USER BALANCES (FIXED)
# =========================
def get_user_balances(emp_id):
    conn = get_db_connection()
    cur = conn.cursor()
    query = """
    SELECT 
        lt.type_id,
        lt.leave_name,
        lp.entitlement_days as total_entitlement,
        COALESCE(SUM(CASE WHEN lr.status = 'Approved' THEN lr.duration ELSE 0 END), 0) as used_days,
        (lp.entitlement_days - COALESCE(SUM(CASE WHEN lr.status = 'Approved' THEN lr.duration ELSE 0 END), 0)) as remaining_balance
    FROM leave_types lt
    JOIN leave_type_policies lp ON lt.type_id = lp.leave_type_id
    JOIN employees e ON e.emp_id = %s
    LEFT JOIN leave_requests lr ON lr.leave_type_id = lt.type_id AND lr.emp_id = e.emp_id
    WHERE lt.is_active = TRUE
      AND (EXTRACT(YEAR FROM AGE(CURRENT_DATE, e.joined_date))) >= lp.min_years_service
      AND (EXTRACT(YEAR FROM AGE(CURRENT_DATE, e.joined_date))) < lp.max_years_service
    GROUP BY lt.type_id, lt.leave_name, lp.entitlement_days;
    """
    cur.execute(query, (emp_id,))
    results = cur.fetchall()
    cur.close()
    conn.close()
    return results

# =========================
# HELPER: LEAVE BALANCE
# =========================
def get_leave_balance(employee_id):
    query = """
    SELECT lt.leave_name, 
           (lt.default_entitlement - COALESCE(SUM(lr.duration), 0)) as balance
    FROM leave_types lt
    LEFT JOIN leave_requests lr ON lr.leave_type_id = lt.type_id 
         AND lr.emp_id = %s AND lr.status = 'Approved'
    GROUP BY lt.type_id, lt.leave_name, lt.default_entitlement
    """
    # Jalankan query menggunakan cursor db Alif
    # Return dalam bentuk dictionary untuk senang guna di Dashboard

# =========================
# DATABASE
# =========================
def get_db_connection():
    return psycopg2.connect(
        host="localhost",
        database="TCK_Leave_System",
        user="postgres",
        password="12345",
        cursor_factory=RealDictCursor
    )

# =========================
# HELPER: POLICY ENGINE
# =========================
def get_leave_policy(emp_id):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT joined_date FROM employees WHERE emp_id=%s", (emp_id,))
    emp = cur.fetchone()

    if not emp:
        return {"annual": 14, "medical": 14}

    years = (date.today() - emp["joined_date"]).days // 365

    cur.execute("""
        SELECT * FROM leave_policies
        WHERE %s BETWEEN min_years_service AND max_years_service
        LIMIT 1
    """, (years,))

    policy = cur.fetchone()
    cur.close()
    conn.close()

    if policy:
        return {
            "annual": policy["annual_entitlement"],
            "medical": policy["medical_entitlement"]
        }

    return {"annual": 14, "medical": 14}

# =========================
# HELPER: DURATION
# =========================
def calculate_duration(start_date, end_date, is_half_day):
    d1 = datetime.strptime(start_date, "%Y-%m-%d")
    d2 = datetime.strptime(end_date, "%Y-%m-%d")

    if is_half_day:
        return 0.5

    days = 0
    while d1 <= d2:
        # skip Sunday
        if d1.weekday() != 6:
            # Saturday = 0.5
            if d1.weekday() == 5:
                days += 0.5
            else:
                days += 1
        d1 += timedelta(days=1)

    return days

# =========================
# HELPER: LOGGING
# =========================
def log_action(emp_id, action, details):
    ip = request.remote_addr # Dapatkan IP automatik dari Flask
    query = "INSERT INTO system_logs (emp_id, action, details, ip_address) VALUES (%s, %s, %s, %s)"
    # Jalankan database execution di sini


# =========================
# AUTH - REPAIRED
# =========================
@app.route("/login", methods=["GET", "POST"])
def login():
    # Jika pengguna sudah login (kita guna 'user_id' sebagai penanda aras),
    # terus hantar ke dashboard untuk elakkan loop.
    if 'user_id' in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email")
        password_form = request.form.get("password")

        # Gunakan helper connection anda
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

        try:
            # 1. Cari user aktif berdasarkan email
            cur.execute("SELECT * FROM employees WHERE email=%s AND is_active = TRUE", (email,))
            user = cur.fetchone()

            # 2. Semak password hash
            if user and check_password_hash(user["password_hash"], password_form):
                # --- TETAPKAN DATA SESI (WAJIB KONSISTEN) ---
                # Gunakan 'user_id' kerana route lain (Dashboard/Apply) mencari kunci ini
                session["user_id"] = user["emp_id"]
                session["name"] = user["full_name"]
                session["role"] = user["role"] if user["role"] else "Staff"
                session["dept_id"] = user["dept_id"]
                
                # Memastikan sesi kekal selama 30 minit (ikut config timedelta anda)
                session.permanent = True 

                # Rekod aktiviti login berjaya
                record_activity('LOGIN_SUCCESS', f'User {email} logged in successfully', user["emp_id"])

                cur.close()
                conn.close()
                return redirect(url_for("dashboard"))

            # 3. Jika login gagal
            if user:
                record_activity('LOGIN_FAILED', f'Failed login attempt for {email}', user["emp_id"])
            
            flash("E-mel atau kata laluan salah / Akaun tidak aktif.", "danger")

        except Exception as e:
            print(f"Database Error: {e}")
            flash("Ralat teknikal berlaku semasa login.", "danger")
        finally:
            cur.close()
            conn.close()

    return render_template("login.html")


# =========================
# DASHBOARD (FIXED SESSION & DB)
# =========================
@app.route("/dashboard")
def dashboard():
    # 1. Semak Sesi (Wajib guna 'user_id' untuk elakkan Redirect Loop)
    if 'user_id' not in session:
        return redirect(url_for("login"))

    emp_id = session['user_id']
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        # 2. Ambil Maklumat Ringkas Staf & Jabatan
        cur.execute("""
            SELECT e.full_name, e.role, d.dept_name 
            FROM employees e
            LEFT JOIN departments d ON e.dept_id = d.dept_id
            WHERE e.emp_id = %s
        """, (emp_id,))
        user_info = cur.fetchone()

        # 3. Ambil Statistik Cuti (Ringkasan di Dashboard)
        # Menghitung jumlah permohonan mengikut status
        cur.execute("""
            SELECT 
                COUNT(*) FILTER (WHERE status = 'Pending') as pending_count,
                COUNT(*) FILTER (WHERE status = 'Approved') as approved_count,
                COUNT(*) FILTER (WHERE status = 'Rejected') as rejected_count
            FROM leave_requests 
            WHERE emp_id = %s
        """, (emp_id,))
        stats = cur.fetchone()

        # 4. Ambil Baki Cuti Terkini (Guna helper function jika ada, atau query terus)
        # Contoh query untuk baki cuti tahunan (Annual Leave ID: 1)
        cur.execute("""
            SELECT eb.total_entitlement - eb.used_days as balance
            FROM employee_balances eb
            WHERE eb.emp_id = %s AND eb.leave_type_id = 1
        """, (emp_id,))
        annual_balance = cur.fetchone()

        # 5. Ambil Permohonan Terkini (Top 5)
        cur.execute("""
            SELECT lr.*, lt.leave_name 
            FROM leave_requests lr
            JOIN leave_types lt ON lr.leave_type_id = lt.type_id
            WHERE lr.emp_id = %s
            ORDER BY lr.created_at DESC
            LIMIT 5
        """, (emp_id,))
        recent_requests = cur.fetchall()

    except Exception as e:
        print(f"Error fetching dashboard data: {e}")
        flash("Ralat memuatkan data dashboard.", "danger")
        stats = {'pending_count': 0, 'approved_count': 0, 'rejected_count': 0}
        annual_balance = {'balance': 0}
        recent_requests = []
    finally:
        cur.close()
        conn.close()

    # Hantar semua data ke template dashboard.html
    return render_template(
        "dashboard.html", 
        stats=stats, 
        recent_requests=recent_requests,
        annual_balance=annual_balance['balance'] if annual_balance else 0,
        user_info=user_info
    )

# =========================
# APPLY LEAVE
# =========================
@app.route('/apply', methods=['GET', 'POST'])
def apply_leave():
    # Periksa session (Pastikan guna 'user_id' seperti dalam login)
    if 'user_id' not in session:
        flash("Sila login terlebih dahulu.", "warning")
        return redirect(url_for('login'))

    if request.method == 'POST':
        # 1. Ambil data dari form
        start_date = request.form.get('start_date')
        end_date = request.form.get('end_date')
        reason = request.form.get('reason')
        leave_type_name = request.form.get('leave_type')
        is_half_day = request.form.get('is_half_day') == 'true'
        duration = request.form.get('total_days') # Dari calculation JS di frontend

        # 2. Mapping Nama ke ID Database
        leave_type_map = {
            'Annual': 1,
            'Medical': 2,
            'Emergency': 3,
            'Unpaid': 4
        }
        leave_type_id = leave_type_map.get(leave_type_name, 1)

        conn = get_db_connection()
        cur = conn.cursor()

        try:
            # 3. Logik Auto-Approver (Cari manager_id berdasarkan dept_id user)
            cur.execute("SELECT manager_id FROM departments WHERE dept_id = %s", (session.get('dept_id'),))
            result = cur.fetchone()
            approver_id = result[0] if result else None

            # 4. Simpan ke database (Guna column leave_type_id dan duration)
            cur.execute("""
                INSERT INTO leave_requests (
                    emp_id, 
                    leave_type_id, 
                    start_date, 
                    end_date, 
                    reason, 
                    status, 
                    approver_id, 
                    duration, 
                    is_half_day
                ) VALUES (%s, %s, %s, %s, %s, 'Pending', %s, %s, %s)
            """, (
                session['user_id'],
                leave_type_id,
                start_date,
                end_date,
                reason,
                approver_id,
                duration,
                is_half_day
            ))

            conn.commit()
            flash("Permohonan cuti berjaya dihantar!", "success")
            
        except Exception as e:
            conn.rollback()
            flash(f"Ralat sistem: {str(e)}", "danger")
            return redirect(url_for('apply_leave'))
        finally:
            cur.close()
            conn.close()

        return redirect(url_for('records'))

    return render_template('apply.html')

# =========================
# VIEW RECORDS (ADD THIS)
# =========================
@app.route('/records')
def records():
    # 1. Semak sesi guna emp_id (seperti di dashboard)
    if 'emp_id' not in session:
        return redirect(url_for('login'))
    
    emp_id = session.get('emp_id')

    # 2. Ambil data dari database (PostgreSQL)
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # Query untuk ambil semua rekod cuti bagi pekerja ini
    query = """
        SELECT lr.*, lt.leave_name as leave_type
        FROM leave_requests lr
        JOIN leave_types lt ON lr.leave_type_id = lt.type_id
        WHERE lr.emp_id = %s
        ORDER BY lr.created_at DESC
    """
    cur.execute(query, (emp_id,))
    leave_history = cur.fetchall()
    
    cur.close()
    conn.close()

    # 3. Hantar data ke template
    return render_template('records.html', 
                           leave_history=leave_history, 
                           active_page='records')

# =========================
# USER PROFILE
# =========================
@app.route('/profile')
def profile():
    # Semak sesi pengguna menggunakan emp_id yang konsisten
    if 'emp_id' not in session:
        return redirect(url_for('login'))
    
    emp_id = session.get('emp_id')

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    # Ambil data lengkap pekerja
    cur.execute("SELECT * FROM employees WHERE emp_id = %s", (emp_id,))
    user_info = cur.fetchone()
    
    cur.close()
    conn.close()

    # Pastikan active_page diletakkan supaya 'indicator' di nav berfungsi
    return render_template('profile.html', 
                           user=user_info, 
                           active_page='profile')

# =========================
# APPROVAL (DYNAMIC - NO DEDUCTION NEEDED)
# =========================
@app.route("/approve/<int:id>", methods=["POST"])
def approve(id):
    if 'emp_id' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    cur = conn.cursor()

    # HANYA UPDATE STATUS. Baki akan berubah sendiri di dashboard staf.
    cur.execute("""
        UPDATE leave_requests
        SET status='Approved', approved_at=NOW()
        WHERE request_id=%s AND status='Pending'
    """, (id,))
    
    # Rekod ke System Logs (Audit Trail)
    log_action(session['emp_id'], 'APPROVE_LEAVE', f'Approved request ID: {id}')

    conn.commit()
    cur.close()
    conn.close()

    flash("Permohonan diluluskan. Baki dikemaskini secara dinamik.", "success")
    return redirect(url_for("dashboard"))

# =========================
# REJECT
# =========================
@app.route("/reject/<int:id>", methods=["POST"])
def reject(id):
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE leave_requests
        SET status='Rejected'
        WHERE request_id=%s
    """, (id,))

    conn.commit()
    cur.close()
    conn.close()

    flash("Rejected", "warning")
    return redirect(url_for("dashboard"))

# =========================
# APPROVE/REJECT WITH REMARKS (DYNAMIC - NO DEDUCTION NEEDED)
@app.route("/approve_reject_leave/<int:request_id>", methods=["POST"])
def approve_reject_leave(request_id):
    if 'emp_id' not in session or session.get('role') == 'Staff':
        return jsonify({"error": "Unauthorized"}), 403

    action = request.form.get("action") # 'approve' atau 'reject'
    admin_remark = request.form.get("admin_remark", "")
    approver_id = session['emp_id']

    conn = get_db_connection()
    cur = conn.cursor()

    try:
        if action == "approve":
            status = "Approved"
            flash_msg = "Permohonan cuti telah diluluskan."
        else:
            status = "Rejected"
            flash_msg = "Permohonan cuti telah ditolak."

        # Update status permohonan
        cur.execute("""
            UPDATE leave_requests 
            SET status = %s, 
                admin_remark = %s, 
                approved_by = %s, 
                updated_at = CURRENT_TIMESTAMP
            WHERE request_id = %s
        """, (status, admin_remark, approver_id, request_id))

        # Log tindakan ke dalam system logs
        log_action(approver_id, 'LEAVE_DECISION', f'{status} request ID: {request_id}')

        conn.commit()
        flash(flash_msg, "success")

    except Exception as e:
        conn.rollback()
        flash(f"Ralat berlaku: {str(e)}", "danger")
    finally:
        cur.close()
        conn.close()

    return redirect(url_for("pending_approvals"))

# =========================
# PENDING APPROVALS (FIXED SESSION & DB)
@app.route("/pending_approvals")
def pending_approvals():
    # Pastikan hanya pengurusan (Manager, SV, CTO, etc) boleh akses
    if 'emp_id' not in session or session.get('role') == 'Staff':
        flash("Anda tidak mempunyai kebenaran untuk akses halaman ini.", "danger")
        return redirect(url_for("dashboard"))

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    # Query untuk mendapatkan detail pemohon dan maklumat cuti
    cur.execute("""
        SELECT lr.*, e.full_name, e.role 
        FROM leave_requests lr
        JOIN employees e ON lr.emp_id = e.emp_id
        WHERE lr.status = 'Pending'
        ORDER BY lr.created_at DESC
    """)
    pending_list = cur.fetchall()

    cur.close()
    conn.close()

    return render_template("pending_approvals.html", 
                           pending_leaves=pending_list, 
                           active_page='pending_approvals')

# =========================
# TEAM CALENDAR (FIXED SESSION & DB)
@app.route("/team-calendar")
def team_calendar():
    # Kawalan Keselamatan: Hanya pengurusan & admin boleh lihat kalendar pasukan
    if 'emp_id' not in session or session.get('role') == 'Staff':
        flash("Anda tidak mempunyai kebenaran untuk akses halaman ini.", "danger")
        return redirect(url_for("dashboard"))

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        # Ambil semua cuti yang telah diluluskan untuk dipaparkan di kalendar
        cur.execute("""
            SELECT 
                e.full_name,
                lr.leave_type,
                lr.start_date,
                lr.end_date,
                lr.status
            FROM leave_requests lr
            JOIN employees e ON lr.emp_id = e.emp_id
            WHERE lr.status = 'Approved'
        """)
        rows = cur.fetchall()

        # Tukar format data untuk FullCalendar
        events = []
        for row in rows:
            events.append({
                'title': row['full_name'],
                'start': row['start_date'].isoformat(),
                # FullCalendar memerlukan tarikh akhir eksklusif (tambah 1 hari untuk paparan tepat)
                'end': (row['end_date'] + timedelta(days=1)).isoformat(),
                'extendedProps': {
                    'type': row['leave_type']
                }
            })

    except Exception as e:
        print(f"Error fetching calendar data: {e}")
        events = []
    finally:
        cur.close()
        conn.close()

    return render_template(
        "team_calendar.html", 
        events=events, 
        active_page='team_calendar'
    )

# =========================
# TEAM HISTORY (FIXED SESSION & DB)
@app.route("/team-history")
def team_history():
    # Kawalan Keselamatan: Hanya pengurusan & admin boleh lihat sejarah pasukan
    if 'emp_id' not in session or session.get('role') == 'Staff':
        flash("Anda tidak mempunyai kebenaran untuk akses halaman ini.", "danger")
        return redirect(url_for("dashboard"))

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        # Query untuk mendapatkan sejarah cuti penuh beserta nama jabatan
        # Pastikan nama kolum (mc_path/attachment_path) sepadan dengan DB anda
        cur.execute("""
            SELECT 
                lr.request_id,
                lr.leave_type,
                lr.start_date,
                lr.end_date,
                lr.duration,
                lr.status,
                lr.mc_path,
                e.full_name,
                d.dept_name
            FROM leave_requests lr
            JOIN employees e ON lr.emp_id = e.emp_id
            LEFT JOIN departments d ON e.dept_id = d.dept_id
            ORDER BY lr.created_at DESC
        """)
        history_data = cur.fetchall()

    except Exception as e:
        print(f"Database Error: {e}")
        history_data = []
    finally:
        cur.close()
        conn.close()

    return render_template(
        "team_history.html", 
        history=history_data, 
        active_page='team_history'
    )

# =========================
# REPORTS & ANALYTICS (FIXED SESSION & DB)
@app.route("/reports")
def reports():
    # Kawalan Keselamatan: Hanya pengurusan & admin boleh melihat analitik
    if 'emp_id' not in session or session.get('role') == 'Staff':
        flash("Anda tidak mempunyai kebenaran untuk akses halaman ini.", "danger")
        return redirect(url_for("dashboard"))

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        # 1. Kira statistik untuk kad (Total Approved, Pending, dan Top Type)
        cur.execute("""
            SELECT 
                COUNT(*) FILTER (WHERE status = 'Approved') as total_approved,
                COUNT(*) FILTER (WHERE status = 'Pending') as total_pending,
                mode() WITHIN GROUP (ORDER BY leave_type) as top_type
            FROM leave_requests
            WHERE EXTRACT(YEAR FROM created_at) = EXTRACT(YEAR FROM CURRENT_DATE)
        """)
        stats = cur.fetchone()

        # 2. Data untuk Carta Pai (Leave Distribution)
        # Kami mengambil kira 4 kategori utama seperti dalam HTML: Annual, Medical, Emergency, Other
        cur.execute("""
            SELECT leave_type, COUNT(*) as count 
            FROM leave_requests 
            WHERE status = 'Approved'
            GROUP BY leave_type
        """)
        distribution_rows = cur.fetchall()
        
        # Susun data mengikut urutan labels di HTML: ['Annual', 'Medical', 'Emergency', 'Other']
        dist_map = {r['leave_type']: r['count'] for r in distribution_rows}
        chart_data = [
            dist_map.get('Annual Leave', 0),
            dist_map.get('Medical Leave', 0),
            dist_map.get('Emergency Leave', 0),
            dist_map.get('Other', 0)
        ]

        # 3. Data untuk Carta Aliran Bulanan (Monthly Trend)
        cur.execute("""
            SELECT EXTRACT(MONTH FROM start_date) as month, COUNT(*) as count
            FROM leave_requests
            WHERE status = 'Approved' 
              AND EXTRACT(YEAR FROM start_date) = EXTRACT(YEAR FROM CURRENT_DATE)
            GROUP BY month
            ORDER BY month
        """)
        trend_rows = cur.fetchall()
        
        # Inisialisasi 6 bulan pertama (Jan-Jun) seperti dalam HTML
        monthly_trend = [0] * 6 
        for r in trend_rows:
            month_idx = int(r['month']) - 1
            if month_idx < 6: # Hanya ambil Jan hingga Jun buat masa ini
                monthly_trend[month_idx] = r['count']

    except Exception as e:
        print(f"Error generating reports: {e}")
        stats = {'total_approved': 0, 'total_pending': 0, 'top_type': 'N/A'}
        chart_data = [0, 0, 0, 0]
        monthly_trend = [0, 0, 0, 0, 0, 0]
    finally:
        cur.close()
        conn.close()

    return render_template(
        "reports.html", 
        stats=stats,
        chart_data=chart_data,
        monthly_trend=monthly_trend,
        active_page='reports'
    )

# =========================
# MANAGE USERS (FULL REPAIR)
# =========================
@app.route('/manage_users', methods=['GET', 'POST'])
def manage_users():
    if request.method == 'POST':
        action = request.form.get('action')
        
        # Logik Tambah Pekerja Baru
        if action == 'add':
            full_name = request.form.get('full_name')
            email = request.form.get('email')
            password = request.form.get('password')
            hashed_password = generate_password_hash(password)
            dept_id = request.form.get('dept_id')
            role = request.form.get('role')
            manager_id = request.form.get('manager_id') or None
            joined_date = request.form.get('joined_date')

            try:
                db.session.execute(text("""
                    INSERT INTO employees (full_name, email, password_hash, dept_id, role, manager_id, joined_date, is_active)
                    VALUES (:full_name, :email, :password_hash, :dept_id, :role, :manager_id, :joined_date, TRUE)
                """), {
                    "full_name": full_name, "email": email, "password_hash": hashed_password,
                    "dept_id": dept_id, "role": role, "manager_id": manager_id, "joined_date": joined_date
                })
                db.session.commit()
                flash('Employee added successfully!', 'success')
            except Exception as e:
                db.session.rollback()
                flash(f'Error adding employee: {str(e)}', 'danger')

        # Logik Kemaskini Pekerja (Edit)
        elif action == 'edit':
            emp_id = request.form.get('emp_id')
            full_name = request.form.get('full_name')
            email = request.form.get('email')
            dept_id = request.form.get('dept_id')
            role = request.form.get('role')
            manager_id = request.form.get('manager_id') or None
            
            try:
                db.session.execute(text("""
                    UPDATE employees 
                    SET full_name = :full_name, email = :email, dept_id = :dept_id, role = :role, manager_id = :manager_id
                    WHERE emp_id = :emp_id
                """), {
                    "full_name": full_name, "email": email, "dept_id": dept_id, 
                    "role": role, "manager_id": manager_id, "emp_id": emp_id
                })
                db.session.commit()
                flash('Profile updated successfully!', 'success')
            except Exception as e:
                db.session.rollback()
                flash(f'Error updating employee: {str(e)}', 'danger')

        return redirect(url_for('manage_users'))

    # --- BAHAGIAN GET (Paparan Data) ---
    
    # 1. Senarai Users (Tukar tarikh ke string & RowMapping ke dict)
    users_raw = db.session.execute(text("""
        SELECT 
            e.emp_id, e.full_name, e.email, e.role, e.dept_id, e.manager_id, e.is_active,
            TO_CHAR(e.joined_date, 'YYYY-MM-DD') as joined_date, 
            d.dept_name 
        FROM employees e 
        LEFT JOIN departments d ON e.dept_id = d.dept_id
        ORDER BY e.emp_id DESC
    """)).mappings().all()
    users = [dict(row) for row in users_raw]

    # 2. Senarai Departments (Tukar ke dict)
    depts_raw = db.session.execute(text("SELECT * FROM departments ORDER BY dept_name ASC")).mappings().all()
    departments = [dict(row) for row in depts_raw]

    # 3. Senarai Managers (Tukar ke dict)
    mgr_raw = db.session.execute(text("""
        SELECT emp_id, full_name 
        FROM employees 
        WHERE role IN ('Admin', 'Supervisor', 'Manager')
        ORDER BY full_name ASC
    """)).mappings().all()
    managers = [dict(row) for row in mgr_raw]

    return render_template('manage_users.html', 
                           users=users, 
                           departments=departments, 
                           managers=managers, active_page='manage_users')

# =========================
# DELETE USER
# =========================
@app.route('/delete_user/<int:id>', methods=['POST'])
def delete_user(id):
    # Pastikan hanya Admin boleh padam
    if 'emp_id' not in session or session.get('role') != 'Admin':
        flash("Unauthorized access.", "danger")
        return redirect(url_for('manage_users'))

    try:
        # 1. Dapatkan nama user untuk log sebelum padam (opsyenal)
        user = db.session.execute(text("SELECT full_name FROM employees WHERE emp_id = :id"), {"id": id}).mappings().first()
        
        # 2. Padam user
        db.session.execute(text("DELETE FROM employees WHERE emp_id = :id"), {"id": id})
        db.session.commit()

        # 3. Rekod dalam log sistem
        if user:
            record_activity('DELETE_USER', f"Deleted user: {user['full_name']} (ID: {id})", session.get('emp_id'))

        flash('Employee has been deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting employee: {str(e)}', 'danger')

    return redirect(url_for('manage_users'))

# =========================
# SYSTEM LOGS (FIXED SESSION & DB)
@app.route('/system_logs')
def system_logs():
    # 1. Logik pengambilan data menggunakan SQL
    # Kita menggunakan LEFT JOIN untuk memastikan aktiviti sistem (tiada emp_id) 
    # tetap dipaparkan sebagai 'System Core'
    query = """
        SELECT 
            sl.log_id,
            sl.action,
            sl.details,
            sl.ip_address,
            sl.created_at,
            e.full_name
        FROM system_logs sl
        LEFT JOIN employees e ON sl.emp_id = e.emp_id
        ORDER BY sl.created_at DESC
        LIMIT 200
    """
    
    try:
        # Anda boleh menggunakan db.session (SQLAlchemy) atau cursor (psycopg2)
        logs = db.session.execute(query).fetchall()
    except Exception as e:
        print(f"Error fetching logs: {e}")
        logs = []
        flash("Unable to retrieve system logs at this time.", "danger")

    # 2. Hantar data ke template system_logs.html
    return render_template('system_logs.html', logs=logs, active_page='system_logs')

# --- FUNGSI TAMBAHAN (Helper Function) ---
# Anda boleh panggil fungsi ini di mana-mana route lain (Contoh: semasa Add User)
# untuk merekodkan aktiviti secara automatik.

def record_activity(action, details, emp_id=None):
    ip_addr = request.remote_addr # Mengambil IP penyerah (user)
    
    insert_query = """
        INSERT INTO system_logs (emp_id, action, details, ip_address, created_at)
        VALUES (:emp_id, :action, :details, :ip, :timestamp)
    """
    db.session.execute(insert_query, {
        'emp_id': emp_id,
        'action': action,
        'details': details,
        'ip': ip_addr,
        'timestamp': datetime.now()
    })
    db.session.commit()

# ==========================================
# MANAGE DEPARTMENTS (FULL ROUTE)
# ==========================================
@app.route('/manage_departments', methods=['GET', 'POST'])
def manage_departments():
    # Pastikan hanya Admin/Management boleh akses (Pilihan)
    if 'emp_id' not in session or session.get('role') == 'Staff':
        flash("Anda tidak mempunyai kebenaran untuk akses halaman ini.", "danger")
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        # 1. Logik Tambah Jabatan Baru
        dept_name = request.form.get('dept_name')
        
        if dept_name:
            try:
                # Guna text() dan db.session untuk masukkan data
                db.session.execute(text("""
                    INSERT INTO departments (dept_name) 
                    VALUES (:name)
                """), {"name": dept_name})
                db.session.commit()
                
                # Rekod aktiviti ke system logs
                record_activity('ADD_DEPARTMENT', f'Added new department: {dept_name}', session.get('emp_id'))
                
                flash(f'Department "{dept_name}" has been successfully created!', 'success')
            except Exception as e:
                db.session.rollback()
                flash('Error creating department. It might already exist.', 'danger')
        
        return redirect(url_for('manage_departments'))

    # 2. Ambil data untuk paparan (GET)
    # Query ini mengira bilangan staf secara dinamik menggunakan LEFT JOIN
    departments = db.session.execute(text("""
        SELECT 
            d.dept_id, 
            d.dept_name, 
            COUNT(e.emp_id) as staff_count
        FROM departments d
        LEFT JOIN employees e ON d.dept_id = e.dept_id
        GROUP BY d.dept_id, d.dept_name
        ORDER BY d.dept_name ASC
    """)).mappings().all() # Gunakan mappings().all() untuk konsistensi data

    return render_template('manage_departments.html', departments=departments, active_page='manage_departments')

# ==========================================
# DELETE DEPARTMENT
# ==========================================
@app.route('/delete_department/<int:dept_id>', methods=['POST'])
def delete_department(dept_id):
    if 'emp_id' not in session or session.get('role') == 'Staff':
        return jsonify({"error": "Unauthorized"}), 403

    try:
        # Semak jika ada staf dalam jabatan ini sebelum padam
        check_staff = db.session.execute(text("SELECT COUNT(*) FROM employees WHERE dept_id = :id"), {"id": dept_id}).scalar()
        
        if check_staff > 0:
            flash('Cannot delete department that still has active employees. Reassign them first.', 'danger')
        else:
            db.session.execute(text("DELETE FROM departments WHERE dept_id = :id"), {"id": dept_id})
            db.session.commit()
            
            record_activity('DELETE_DEPARTMENT', f'Deleted department ID: {dept_id}', session.get('emp_id'))
            flash('Department deleted successfully.', 'success')
            
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting department: {str(e)}', 'danger')
        
    return redirect(url_for('manage_departments'))

# =========================
@app.route('/')
def index():
    return redirect(url_for('login')) # Menghala ke halaman login secara automatik

# =========================
# LOGOUT
# =========================
@app.route('/logout')
def logout():
    # Mengosongkan semua data dalam session
    session.clear()
    
    # Memberi maklum balas visual (opsyenal jika anda menggunakan flask-flash)
    # flash("You have been logged out successfully.", "info")
    
    # Menghantar pengguna kembali ke halaman login
    return redirect(url_for('login'))

# =========================
# RUN
# =========================
if __name__ == "__main__":
    app.run(debug=True)