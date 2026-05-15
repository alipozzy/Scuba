import os
import time
from datetime import datetime, date, timedelta

import psycopg2
from psycopg2.extras import RealDictCursor
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import text
from flask import render_template, request, redirect, url_for, session, flash
from werkzeug.security import check_password_hash
from sqlalchemy import text

app = Flask(__name__)
app.permanent_session_lifetime = timedelta(minutes=30)
app.secret_key = "tck_esolutions_secret"

app.config['SQLALCHEMY_DATABASE_URI'] = 'postgresql://postgres:12345@localhost:5432/TCK_Leave_System'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

UPLOAD_FOLDER = "static/uploads/mc_docs"
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

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

def get_leave_balance(employee_id):
    query = """
    SELECT lt.leave_name,
           (lt.default_entitlement - COALESCE(SUM(lr.duration), 0)) as balance
    FROM leave_types lt
    LEFT JOIN leave_requests lr ON lr.leave_type_id = lt.type_id
         AND lr.emp_id = %s AND lr.status = 'Approved'
    GROUP BY lt.type_id, lt.leave_name, lt.default_entitlement
    """

def get_db_connection():
    return psycopg2.connect(
        host="localhost",
        database="TCK_Leave_System",
        user="postgres",
        password="12345",
        cursor_factory=RealDictCursor
    )

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
            
def calculate_duration(start_date, end_date, is_half_day):
    d1 = datetime.strptime(start_date, "%Y-%m-%d")
    d2 = datetime.strptime(end_date, "%Y-%m-%d")

    if is_half_day:
        return 0.5

    days = 0
    while d1 <= d2:
        if d1.weekday() != 6:
            if d1.weekday() == 5:
                days += 0.5
            else:
                days += 1
        d1 += timedelta(days=1)

    return days

def log_action(emp_id, action, details):
    conn = get_db_connection()
    cur = conn.cursor()

    try:
        ip = request.remote_addr

        query = """
        INSERT INTO system_logs (emp_id, action, details, ip_address)
        VALUES (%s, %s, %s, %s)
        """

        cur.execute(query, (emp_id, action, details, ip))
        conn.commit()

    except Exception as e:
        conn.rollback()
        print(f"Log Error: {e}")

    finally:
        cur.close()
        conn.close()

@app.route('/login', methods=['GET', 'POST'])
def login():
    if 'emp_id' in session:
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        user_query = text("""
            SELECT emp_id, email, password_hash, role, full_name
            FROM employees
            WHERE email = :email AND is_active = TRUE
        """)
        user = db.session.execute(user_query, {"email": email}).fetchone()

        if user:
            if check_password_hash(user.password_hash, password):
                session.clear()
                session['emp_id'] = user.emp_id
                session['role'] = user.role
                session['full_name'] = user.full_name
                return redirect(url_for('dashboard'))
            else:
                flash("Kata laluan salah.", "danger")
        else:
            flash("Akaun tidak dijumpai atau tidak aktif.", "danger")

    return render_template('login.html')

@app.route('/dashboard')
def dashboard():
    if 'emp_id' not in session:
        return redirect(url_for('login'))

    emp_id = session['emp_id']

    user_query = text("""
        SELECT e.*, d.dept_name, m.full_name as manager_name
        FROM employees e
        LEFT JOIN departments d ON e.dept_id = d.dept_id
        LEFT JOIN employees m ON e.manager_id = m.emp_id
        WHERE e.emp_id = :id
    """)
    user_data = db.session.execute(user_query, {"id": emp_id}).mappings().first()

    if not user_data:
        return "User not found", 404

    user_role = user_data['role']

    # ROLE: ADMIN
    if user_role == 'Admin':
        admin_stats = {
            'total_employees': db.session.execute(text("SELECT COUNT(*) FROM employees")).scalar(),
            'pending_count': db.session.execute(text("SELECT COUNT(*) FROM leave_requests WHERE status = 'Pending'")).scalar(),
            'on_leave_today': db.session.execute(
                text("SELECT COUNT(*) FROM leave_requests WHERE status = 'Approved' AND CURRENT_DATE BETWEEN start_date AND end_date")
            ).scalar()
        }

        all_employees = db.session.execute(
            text("""
                SELECT e.*, d.dept_name
                FROM employees e
                LEFT JOIN departments d ON e.dept_id = d.dept_id
                ORDER BY e.joined_date DESC
            """)
        ).mappings().all()

        departments = db.session.execute(text("SELECT * FROM departments")).mappings().all()

        return render_template('dashboard_admin.html',
                               user=user_data,
                               all_employees=all_employees,
                               departments=departments,
                               active_page='dashboard',
                               **admin_stats)

  # ROLE: OFFICER / SUPERVISOR
    elif user_role in ['CTO', 'COO']:
        today_date = datetime.now()
        current_dept_id = user_data.get('dept_id')

        if current_dept_id:
            # 1. Stats pending (Kekal sama)
            pending_count = db.session.execute(
                text("""
                    SELECT COUNT(*) FROM leave_requests l 
                    JOIN employees e ON l.emp_id = e.emp_id 
                    WHERE l.status = 'Pending' AND e.dept_id = :d
                """), {"d": current_dept_id}
            ).scalar()

            # 2. Stats bercuti hari ini (Kekal sama)
            on_leave_today = db.session.execute(
                text("""
                    SELECT COUNT(*) FROM leave_requests l
                    JOIN employees e ON l.emp_id = e.emp_id
                    WHERE l.status = 'Approved' 
                    AND e.dept_id = :d
                    AND CURRENT_DATE BETWEEN l.start_date AND l.end_date
                """), {"d": current_dept_id}
            ).scalar()

            # 3. FIX: Department Size (Hanya kira 'Staff')
            # Ditambah syarat AND role = 'Staff'
            team_count = db.session.execute(
                text("""
                    SELECT COUNT(*) 
                    FROM employees 
                    WHERE dept_id = :d AND role = 'Staff'
                """), {"d": current_dept_id}
            ).scalar()

            # 4. Recent Activity (Kekal sama)
            recent_requests = db.session.execute(
                text("""
                    SELECT lr.*, e.full_name, e.role, lt.leave_name
                    FROM leave_requests lr
                    JOIN employees e ON lr.emp_id = e.emp_id
                    JOIN leave_types lt ON lr.leave_type_id = lt.type_id
                    WHERE e.dept_id = :d
                    ORDER BY lr.created_at DESC LIMIT 5
                """), {"d": current_dept_id}
            ).mappings().all()
            
        else:
            pending_count = on_leave_today = team_count = 0
            recent_requests = []

        return render_template('dashboard_officer.html',
                               user=user_data,
                               today=today_date,
                               pending_count=pending_count,
                               on_leave_today=on_leave_today,
                               team_count=team_count,
                               active_page='dashboard',
                               recent_requests=recent_requests)
    
    # ROLE: STAFF
    elif user_role == 'Staff':
        # A. Balances
        balances = db.session.execute(text("""
            SELECT 
                lt.type_id,
                lt.leave_name,
                lt.default_entitlement as entitlement_days,
                lt.default_entitlement as remaining_days 
            FROM leave_types lt
            WHERE lt.is_active = true
        """)).mappings().all()

        # B. Stats (Menggunakan standard CASE WHEN demi keandalan merentasi pelbagai jenis DB)
        staff_stats = db.session.execute(
            text("""
                SELECT 
                    SUM(CASE WHEN status = 'Pending' THEN 1 ELSE 0 END) as pending,
                    SUM(CASE WHEN status = 'Approved' THEN 1 ELSE 0 END) as approved
                FROM leave_requests 
                WHERE emp_id = :id
            """),
            {"id": emp_id}
        ).mappings().first()

        # C. Personal History
        personal_requests = db.session.execute(
            text("""
                SELECT lr.*, lt.leave_name
                FROM leave_requests lr
                JOIN leave_types lt ON lr.leave_type_id = lt.type_id
                WHERE lr.emp_id = :id
                ORDER BY lr.created_at DESC LIMIT 5
            """),
            {"id": emp_id}
        ).mappings().all()

        # D. Ambil data Events untuk Kalendar
        calendar_data = db.session.execute(
            text("""
                SELECT 
                    e.full_name as title,
                    l.start_date as start,
                    l.end_date as end,
                    lt.leave_name as type
                FROM leave_requests l
                JOIN employees e ON l.emp_id = e.emp_id
                JOIN leave_types lt ON l.leave_type_id = lt.type_id
                WHERE e.dept_id = :dept_id AND l.status = 'Approved'
            """),
            {"dept_id": user_data['dept_id']}
        ).mappings().all()

        # Tukar format date kepada string ISO untuk FullCalendar
        events = []
        for row in calendar_data:
            events.append({
                "title": row['title'],
                "start": row['start'].strftime('%Y-%m-%d') if row['start'] else '',
                "end": (row['end'] + timedelta(days=1)).strftime('%Y-%m-%d') if row['end'] else '',
                "extendedProps": {"type": row['type']}
            })

        return render_template('dashboard_staff.html', 
                               user=user_data, 
                               balances=balances, 
                               stats=staff_stats,
                               requests=personal_requests,
                               active_page='dashboard',
                               events=events) 

    # ROLE: FINANCE DIRECTOR
    elif user_role == 'Finance Director':
        return render_template('dashboard_finance.html', user=user_data, active_page='dashboard')

    return "Role not recognized", 403

@app.route('/apply', methods=['GET', 'POST'])
def apply_leave():
    if 'emp_id' not in session:
        flash("Sila login terlebih dahulu.", "warning")
        return redirect(url_for('login'))

    if request.method == 'POST':
        leave_type_name = request.form.get('leave_type')
        start_date = request.form.get('start_date')
        end_date = request.form.get('end_date')
        reason = request.form.get('reason')
        is_half_day = request.form.get('half_day') == 'on'

        duration = calculate_duration(start_date, end_date, is_half_day)

        leave_type_map = {'Annual': 1, 'Medical': 2, 'Emergency': 3, 'Unpaid': 4}
        leave_type_id = leave_type_map.get(leave_type_name, 1)

        conn = get_db_connection()
        cur = conn.cursor()

        try:
            cur.execute("SELECT manager_id FROM employees WHERE emp_id = %s", (session['emp_id'],))
            user_info = cur.fetchone()
            approver_id = user_info['manager_id'] if user_info else None

            cur.execute("""
                INSERT INTO leave_requests (
                    emp_id, leave_type_id, start_date, end_date,
                    reason, status, approver_id, duration, is_half_day
                ) VALUES (%s, %s, %s, %s, %s, 'Pending', %s, %s, %s)
            """, (
                session['emp_id'], leave_type_id, start_date, end_date,
                reason, approver_id, duration, is_half_day
            ))

            conn.commit()
            flash("Permohonan cuti anda telah dihantar!", "success")
            return redirect(url_for('records'))

        except Exception as e:
            conn.rollback()
            print(f"Error: {e}")
            flash("Gagal menghantar permohonan. Sila cuba lagi.", "danger")
        finally:
            cur.close()
            conn.close()

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    cur.execute("""
        SELECT
            e.*,

            COALESCE(
                direct_manager.full_name,
                dept_manager.full_name
            ) AS manager_name,

            (
                COALESCE(lt.default_entitlement, 14)
                -
                COALESCE((
                    SELECT SUM(lr.duration)
                    FROM leave_requests lr
                    WHERE lr.emp_id = e.emp_id
                    AND lr.leave_type_id = 1
                    AND lr.status = 'Approved'
                ), 0)
            ) AS remaining_leave

        FROM employees e

        LEFT JOIN employees direct_manager
            ON e.manager_id = direct_manager.emp_id

        LEFT JOIN departments d
            ON e.dept_id = d.dept_id

        LEFT JOIN employees dept_manager
            ON d.manager_id = dept_manager.emp_id

        LEFT JOIN leave_types lt
            ON lt.type_id = 1

        WHERE e.emp_id = %s
    """, (session['emp_id'],))

    user_data = cur.fetchone()
    cur.close()
    conn.close()

    return render_template('apply.html', user=user_data, active_page='apply')

@app.route('/records')
def records():
    if 'emp_id' not in session:
        return redirect(url_for('login'))

    emp_id = session.get('emp_id')

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

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

    return render_template('records.html',
                           leave_history=leave_history,
                           active_page='records')

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'emp_id' not in session:
        flash("Sila login terlebih dahulu.", "warning")
        return redirect(url_for('login'))

    emp_id = session['emp_id']
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    try:
        if request.method == 'POST':
            action = request.form.get('action')
            if action == 'update_info':
                cur.execute("""
                    UPDATE employees SET
                    emergency_name = %s, emergency_relation = %s, emergency_phone = %s
                    WHERE emp_id = %s
                """, (request.form.get('emergency_name'), request.form.get('emergency_relation'),
                      request.form.get('emergency_phone'), emp_id))
                conn.commit()
                flash("Maklumat kecemasan berjaya dikemaskini.", "success")

        cur.execute("""
            SELECT
                e.*,
                d.dept_name,
                d.dept_code,
                m.full_name AS manager_name,
                -- Ambil baki cuti tahunan (Annual Leave - ID 1) dari leave_types
                COALESCE(lt.default_entitlement, 14) AS total_leave,
                -- Kira jumlah cuti yang telah lulus (Approved)
                COALESCE((
                    SELECT SUM(duration) FROM leave_requests
                    WHERE emp_id = e.emp_id AND status = 'Approved' AND leave_type_id = 1
                ), 0) AS used_leave,
                -- Status sama ada sedang cuti hari ini
                EXISTS (
                    SELECT 1 FROM leave_requests lr
                    WHERE lr.emp_id = e.emp_id AND lr.status = 'Approved'
                    AND CURRENT_DATE BETWEEN lr.start_date AND lr.end_date
                ) AS on_leave
            FROM employees e
            LEFT JOIN departments d ON e.dept_id = d.dept_id
            LEFT JOIN employees m ON e.manager_id = m.emp_id
            LEFT JOIN leave_types lt ON lt.type_id = 1 -- Merujuk kepada Annual Leave
            WHERE e.emp_id = %s
        """, (emp_id,))

        user_info = cur.fetchone()

        if not user_info:
            flash("Data profil tidak dijumpai.", "danger")
            return redirect(url_for('dashboard'))

    except Exception as e:
        if conn: conn.rollback()
        print(f"Profile Error: {e}")
        flash("Gagal memuatkan profil.", "danger")
        user_info = None
    finally:
        cur.close()
        conn.close()

    return render_template(
        'profile.html',
        user=user_info,
        active_page='profile'
    )

@app.route("/approve/<int:id>", methods=["POST"])
def approve(id):
    if 'emp_id' not in session:
        return redirect(url_for('login'))

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE leave_requests
        SET status='Approved', approved_at=NOW()
        WHERE request_id=%s AND status='Pending'
    """, (id,))

    log_action(session['emp_id'], 'APPROVE_LEAVE', f'Approved request ID: {id}')

    conn.commit()
    cur.close()
    conn.close()

    flash("Permohonan diluluskan. Baki dikemaskini secara dinamik.", "success")
    return redirect(url_for("dashboard"))

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

@app.route("/approve_reject_leave/<int:request_id>", methods=["POST"])
def approve_reject_leave(request_id):

    if 'emp_id' not in session or session.get('role') == 'Staff':
        return jsonify({"error": "Unauthorized"}), 403

    action = request.form.get("action")
    supervisor_remark = request.form.get("admin_remark", "").strip()

    approver_id = session['emp_id']

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    try:

        if action == "approve":
            status = "Approved"
            flash_msg = "Permohonan cuti telah diluluskan."

        elif action == "reject":
            status = "Rejected"
            flash_msg = "Permohonan cuti telah ditolak."

        else:
            flash("Tindakan tidak sah.", "danger")
            return redirect(url_for("pending_approvals"))

        cur.execute("""
            SELECT request_id, status
            FROM leave_requests
            WHERE request_id = %s
        """, (request_id,))

        leave_request = cur.fetchone()

        if not leave_request:
            flash("Permohonan cuti tidak dijumpai.", "danger")
            return redirect(url_for("pending_approvals"))

        if leave_request['status'] != 'Pending':
            flash("Permohonan ini telah diproses sebelum ini.", "warning")
            return redirect(url_for("pending_approvals"))

        cur.execute("""
            UPDATE leave_requests
            SET
                status = %s,
                supervisor_remarks = %s,
                approver_id = %s,
                approved_at = CURRENT_TIMESTAMP
            WHERE request_id = %s
        """, (
            status,
            supervisor_remark,
            approver_id,
            request_id
        ))

        log_action(
            approver_id,
            'LEAVE_DECISION',
            f'{status} leave request ID: {request_id}'
        )

        conn.commit()

        flash(flash_msg, "success")

    except Exception as e:

        conn.rollback()

        print(f"Approve/Reject Error: {e}")

        flash(
            f"Ralat berlaku semasa memproses permohonan: {str(e)}",
            "danger"
        )

    finally:
        cur.close()
        conn.close()

    return redirect(url_for("team_history"))

@app.route("/pending_approvals")
def pending_approvals():

    if 'emp_id' not in session:
        flash("Please login first.", "warning")
        return redirect(url_for("login"))

    if session.get('role') == 'Staff':
        flash("Anda tidak mempunyai kebenaran.", "danger")
        return redirect(url_for("dashboard"))

    approver_id = session['emp_id']
    role = session.get('role')

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:

        if role in ['CTO', 'COO']:

            cur.execute("""
                SELECT
                    lr.request_id,
                    lr.emp_id,
                    lr.start_date,
                    lr.end_date,
                    lr.reason,
                    lr.status,
                    lr.duration,
                    lr.created_at,
                    lr.mc_path,

                    e.full_name,
                    e.role,

                    lt.leave_name

                FROM leave_requests lr

                JOIN employees e
                    ON lr.emp_id = e.emp_id

                LEFT JOIN leave_types lt
                    ON lr.leave_type_id = lt.type_id

                WHERE lr.status = 'Pending'

                ORDER BY lr.created_at DESC
            """)

        else:

            cur.execute("""
                SELECT
                    lr.request_id,
                    lr.emp_id,
                    lr.start_date,
                    lr.end_date,
                    lr.reason,
                    lr.status,
                    lr.duration,
                    lr.created_at,
                    lr.mc_path,

                    e.full_name,
                    e.role,

                    lt.leave_name

                FROM leave_requests lr

                JOIN employees e
                    ON lr.emp_id = e.emp_id

                LEFT JOIN leave_types lt
                    ON lr.leave_type_id = lt.type_id

                WHERE lr.status = 'Pending'
                AND e.manager_id = %s

                ORDER BY lr.created_at DESC
            """, (approver_id,))

        pending_list = cur.fetchall()

    except Exception as e:

        print(f"PENDING APPROVAL ERROR: {e}")

        flash("Unable to load pending approvals.", "danger")

        pending_list = []

    finally:

        cur.close()
        conn.close()

    return render_template(
        "pending_approvals.html",
        pending_leaves=pending_list,
        active_page='pending_approvals'
    )

@app.route("/team-calendar")
def team_calendar():

    if 'emp_id' not in session or session.get('role') == 'Staff':
        flash("Anda tidak mempunyai kebenaran untuk akses halaman ini.", "danger")
        return redirect(url_for("dashboard"))

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        cur.execute("""
            SELECT
                e.full_name,
                lt.leave_name,
                lr.start_date,
                lr.end_date,
                lr.status
            FROM leave_requests lr
            JOIN employees e
                ON lr.emp_id = e.emp_id
            JOIN leave_types lt
                ON lr.leave_type_id = lt.type_id
            WHERE lr.status = 'Approved'
        """)

        rows = cur.fetchall()

        events = []

        for row in rows:
            events.append({
                'title': row['full_name'],
                'start': row['start_date'].isoformat(),
                'end': (row['end_date'] + timedelta(days=1)).isoformat(),
                'extendedProps': {
                    'type': row['leave_name']
                }
            })

    except Exception as e:
        print(f"Calendar Error: {e}")
        events = []

    finally:
        cur.close()
        conn.close()

    return render_template(
        "team_calendar.html",
        events=events,
        active_page='team_calendar'
    )

@app.route("/team-history")
def team_history():

    if 'emp_id' not in session :
        flash("Anda tidak mempunyai kebenaran untuk akses halaman ini.", "danger")
        return redirect(url_for("dashboard"))

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:
        cur.execute("""
            SELECT
                lr.request_id,
                lt.leave_name,
                lr.start_date,
                lr.end_date,
                lr.duration,
                lr.status,
                lr.mc_path,
                e.full_name,
                d.dept_name
            FROM leave_requests lr
            JOIN employees e
                ON lr.emp_id = e.emp_id
            JOIN leave_types lt
                ON lr.leave_type_id = lt.type_id
            LEFT JOIN departments d
                ON e.dept_id = d.dept_id
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

@app.route("/reports")
def reports():

    if 'emp_id' not in session or session.get('role') == 'Staff':
        flash("Anda tidak mempunyai kebenaran untuk akses halaman ini.", "danger")
        return redirect(url_for("dashboard"))

    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    try:

        cur.execute("""
            SELECT
                COUNT(*) FILTER (WHERE lr.status='Approved') AS total_approved,
                COUNT(*) FILTER (WHERE lr.status='Pending') AS total_pending,
                mode() WITHIN GROUP (ORDER BY lt.leave_name) AS top_type
            FROM leave_requests lr
            JOIN leave_types lt
                ON lr.leave_type_id = lt.type_id
            WHERE EXTRACT(YEAR FROM lr.created_at)
                = EXTRACT(YEAR FROM CURRENT_DATE)
        """)

        stats = cur.fetchone()

        cur.execute("""
            SELECT
                lt.leave_name,
                COUNT(*) AS count
            FROM leave_requests lr
            JOIN leave_types lt
                ON lr.leave_type_id = lt.type_id
            WHERE lr.status='Approved'
            GROUP BY lt.leave_name
        """)

        distribution_rows = cur.fetchall()

        dist_map = {
            r['leave_name']: r['count']
            for r in distribution_rows
        }

        chart_data = [
            dist_map.get('Annual', 0),
            dist_map.get('Medical', 0),
            dist_map.get('Emergency', 0),
            dist_map.get('Unpaid', 0)
        ]

        cur.execute("""
            SELECT
                EXTRACT(MONTH FROM start_date) AS month,
                COUNT(*) AS count
            FROM leave_requests
            WHERE status='Approved'
            AND EXTRACT(YEAR FROM start_date)
                = EXTRACT(YEAR FROM CURRENT_DATE)
            GROUP BY month
            ORDER BY month
        """)

        trend_rows = cur.fetchall()

        monthly_trend = [0] * 12

        for r in trend_rows:
            month_idx = int(r['month']) - 1

            if 0 <= month_idx < 12:
                monthly_trend[month_idx] = r['count']

    except Exception as e:
        print(f"Reports Error: {e}")

        stats = {
            'total_approved': 0,
            'total_pending': 0,
            'top_type': 'N/A'
        }

        chart_data = [0, 0, 0, 0]
        monthly_trend = [0] * 12

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

@app.route('/manage_users', methods=['GET', 'POST'])
def manage_users():
    if request.method == 'POST':
        action = request.form.get('action')

        if action == 'add':
            full_name = request.form.get('full_name')
            email = request.form.get('email')
            password_hash = request.form.get('password_hash')
            dept_id = request.form.get('dept_id')
            role = request.form.get('role')
            manager_id = request.form.get('manager_id') or None
            joined_date = request.form.get('joined_date')

            try:
                db.session.execute(text("""
                    INSERT INTO employees (full_name, email, password_hash, dept_id, role, manager_id, joined_date, is_active)
                    VALUES (:full_name, :email, :password_hash, :dept_id, :role, :manager_id, :joined_date, TRUE)
                """), {
                    "full_name": full_name, "email": email, "password_hash": password_hash,
                    "dept_id": dept_id, "role": role, "manager_id": manager_id, "joined_date": joined_date
                })
                db.session.commit()
                flash('Employee added successfully!', 'success')
            except Exception as e:
                db.session.rollback()
                flash(f'Error adding employee: {str(e)}', 'danger')

        elif action == 'edit':
            emp_id = request.form.get('emp_id')
            full_name = request.form.get('full_name')
            email = request.form.get('email')
            password_hash = request.form.get('password_hash')
            dept_id = request.form.get('dept_id')
            role = request.form.get('role')
            manager_id = request.form.get('manager_id') or None

            try:
                db.session.execute(text("""
                    UPDATE employees
                    SET full_name = :full_name, email = :email, password_hash = :password_hash, dept_id = :dept_id, role = :role, manager_id = :manager_id
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

    depts_raw = db.session.execute(text("SELECT * FROM departments ORDER BY dept_name ASC")).mappings().all()
    departments = [dict(row) for row in depts_raw]

    mgr_raw = db.session.execute(text("""
        SELECT emp_id, full_name
        FROM employees
        WHERE role IN ('Admin', 'CTO', 'COO', 'Manager')
        ORDER BY full_name ASC
    """)).mappings().all()
    managers = [dict(row) for row in mgr_raw]

    return render_template('manage_users.html',
                           users=users,
                           departments=departments,
                           managers=managers, active_page='manage_users')

@app.route('/delete_user/<int:id>', methods=['POST'])
def delete_user(id):
    if 'emp_id' not in session or session.get('role') != 'Admin':
        flash("Unauthorized access.", "danger")
        return redirect(url_for('manage_users'))

    try:
        user = db.session.execute(text("SELECT full_name FROM employees WHERE emp_id = :id"), {"id": id}).mappings().first()

        db.session.execute(text("DELETE FROM employees WHERE emp_id = :id"), {"id": id})
        db.session.commit()

        if user:
            record_activity('DELETE_USER', f"Deleted user: {user['full_name']} (ID: {id})", session.get('emp_id'))

        flash('Employee has been deleted successfully.', 'success')
    except Exception as e:
        db.session.rollback()
        flash(f'Error deleting employee: {str(e)}', 'danger')

    return redirect(url_for('manage_users'))

@app.route('/system_logs')
def system_logs():

    query = """
        SELECT
            sl.log_id,
            sl.action,
            sl.details,
            sl.ip_address,
            sl.created_at,
            e.full_name
        FROM system_logs sl
        LEFT JOIN employees e
            ON sl.emp_id = e.emp_id
        ORDER BY sl.created_at DESC
        LIMIT 200
    """

    try:
        logs = db.session.execute(text(query)).fetchall()

    except Exception as e:
        print(f"Error fetching logs: {e}")
        logs = []

    return render_template(
        'system_logs.html',
        logs=logs,
        active_page='system_logs'
    )

def record_activity(action, details, emp_id=None):

    ip_addr = request.remote_addr

    insert_query = """
        INSERT INTO system_logs (
            emp_id,
            action,
            details,
            ip_address,
            created_at
        )
        VALUES (
            :emp_id,
            :action,
            :details,
            :ip,
            :timestamp
        )
    """

    db.session.execute(text(insert_query), {
        'emp_id': emp_id,
        'action': action,
        'details': details,
        'ip': ip_addr,
        'timestamp': datetime.now()
    })

    db.session.commit()

@app.route('/manage_departments', methods=['GET', 'POST'])
def manage_departments():
    # 1. Kawalan Akses: Hanya Admin, Finance Director, COO, atau CTO boleh akses
    if 'emp_id' not in session or session.get('role') == 'Staff':
        flash("Anda tidak mempunyai kebenaran untuk akses halaman ini.", "danger")
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        dept_name = request.form.get('dept_name')

        if dept_name:
            try:
                # Tambah jabatan baru ke dalam pangkalan data
                db.session.execute(text("""
                    INSERT INTO departments (dept_name) 
                    VALUES (:name)
                """), {"name": dept_name})
                db.session.commit()

                # Rekod aktiviti untuk tujuan audit
                record_activity('ADD_DEPARTMENT', f'Added new department: {dept_name}', session.get('emp_id'))
                flash(f'Jabatan "{dept_name}" berjaya didaftarkan!', 'success')
                
            except Exception as e:
                db.session.rollback()
                flash('Ralat: Jabatan mungkin sudah wujud atau masalah pangkalan data.', 'danger')

        return redirect(url_for('manage_departments'))

    # 2. Query Penapisan Staff (Hanya kira Role 'Staff')
    # Menggunakan FILTER (WHERE e.role = 'Staff') supaya COO/CTO tidak dikira dalam jumlah
    departments = db.session.execute(text("""
        SELECT 
            d.dept_id, 
            d.dept_name, 
            COUNT(e.emp_id) FILTER (WHERE e.role = 'Staff') as staff_count
        FROM departments d
        LEFT JOIN employees e ON d.dept_id = e.dept_id
        GROUP BY d.dept_id, d.dept_name
        ORDER BY d.dept_id ASC
    """)).mappings().all()

    return render_template('manage_departments.html', 
                           departments=departments, 
                           active_page='manage_departments')

@app.route('/delete_department/<int:dept_id>', methods=['POST'])
def delete_department(dept_id):
    if 'emp_id' not in session or session.get('role') == 'Staff':
        return jsonify({"error": "Unauthorized"}), 403

    try:
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

@app.route('/')
def index():
    return redirect(url_for('login'))

@app.route('/logout')
def logout():
    session.clear()
    flash("Anda telah log keluar.", "info")
    return redirect(url_for('login'))

if __name__ == "__main__":
    app.run(debug=True)