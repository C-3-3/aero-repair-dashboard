from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
from datetime import datetime, timedelta
import os
import threading
import time
import shutil
import re
from PyPDF2 import PdfReader
from flask import send_file
import json


app = Flask(__name__)
app.secret_key = 'aero-dashboard-secret'

# === DATABASE PATHS ===
TASK_DB = "aero_repair_tasks.db"
INSPECTION_DB = "inspection_logs.db"
EXPIRY_DB = "document_expiry.db"
PDF_LOG_PATH = "pdf_organizer.log"
SIGNOFF_DB = "signoffs.db"
USERS_DB = "users.db"



# Run this once on app startup

def init_user_db():
    conn = sqlite3.connect(USERS_DB)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT,
            role TEXT
        )
    ''')
    conn.commit()
    conn.close()

def create_test_user():
    conn = sqlite3.connect(USERS_DB)
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (username, password, role) VALUES (?, ?, ?)",
                   ("mirza", "password123", "manager"))
    conn.commit()
    conn.close()

# ✅ Run these immediately at startup (works locally and on Railway)
init_user_db()
# create_test_user() # 🔒 Temporarily disabled to avoid overwriting users


def ensure_column_exists(db_path, table_name, column_name, column_type):
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    # Get current columns
    cursor.execute(f"PRAGMA table_info({table_name})")
    existing_columns = [col[1] for col in cursor.fetchall()]

    if column_name not in existing_columns:
        print(f"Adding missing column '{column_name}' to '{table_name}'")
        cursor.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
        conn.commit()

    conn.close()

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']

        conn = sqlite3.connect(USERS_DB)
        cursor = conn.cursor()
        cursor.execute("SELECT role FROM users WHERE username=? AND password=?", (username, password))
        result = cursor.fetchone()
        conn.close()

        if result:
            session['logged_in'] = True
            session['username'] = username
            session['role'] = result[0]
            return redirect(url_for('dashboard'))
        else:
            flash("Invalid credentials", "error")

    return render_template("login.html")


def init_user_db():
    conn = sqlite3.connect(USERS_DB)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            password TEXT,
            role TEXT
        )
    ''')
    conn.commit()
    conn.close()

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('login'))


# === DASHBOARD HOME ===
@app.route('/')
def dashboard():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    return render_template("dashboard.html", role=session.get('role'))


# === WORK ORDERS ===

@app.route('/workorders')
def workorders():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    status_filter = request.args.get('status')
    aircraft_filter = request.args.get('aircraft')

    with open('mock_quantum_data.json', 'r') as f:
        work_orders = json.load(f)

    # Filter based on status or aircraft
    filtered_orders = []
    for wo in work_orders:
        if status_filter and wo["status"].lower() != status_filter.lower():
            continue
        if aircraft_filter and wo["aircraft"].lower() != aircraft_filter.lower():
            continue
        filtered_orders.append(wo)

    return render_template('workorders.html', work_orders=filtered_orders, status_filter=status_filter, aircraft_filter=aircraft_filter)

@app.route('/workorders/<wo_id>/tasks/<task_id>', methods=["GET", "POST"])
def task_detail(wo_id, task_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    with open('mock_quantum_data.json', 'r') as f:
        work_orders = json.load(f)

    selected_wo = None
    selected_task = None

    for wo in work_orders:
        if wo['work_order_id'] == wo_id:
            selected_wo = wo
            for task in wo['tasks']:
                if task['task_id'] == task_id:
                    selected_task = task
                    break
            break

    if not selected_task:
        return "Task not found", 404

    # Always fetch recent updates (whether GET or POST)
    conn = sqlite3.connect('task_updates.db')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT status, comment, timestamp
        FROM task_updates
        WHERE work_order_id = ? AND task_id = ?
        ORDER BY timestamp DESC
        LIMIT 5
    ''', (wo_id, task_id))
    recent_updates = cursor.fetchall()
    conn.close()

    # Save new update if POST
    if request.method == "POST":
        new_status = request.form.get("status")
        comment = request.form.get("comment")

        conn = sqlite3.connect('task_updates.db')
        cursor = conn.cursor()
        username = session.get("username", "Unknown")

        cursor.execute('''
            INSERT INTO task_updates (work_order_id, task_id, status, comment, user)
            VALUES (?, ?, ?, ?, ?)
        ''', (wo_id, task_id, new_status, comment, username))

        conn.commit()
        conn.close()

        flash("✅ Task update saved!", "info")

    return render_template(
        "task_detail.html",
        work_order=selected_wo,
        task=selected_task,
        recent_updates=recent_updates
    )



from flask import make_response
import csv
from io import StringIO

@app.route('/workorders/export')
def export_workorders():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    with open('mock_quantum_data.json', 'r') as f:
        work_orders = json.load(f)

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Work Order ID", "Aircraft", "Location", "Status", "Task Description", "Assigned To", "Task Status"])

    for wo in work_orders:
        for task in wo['tasks']:
            writer.writerow([
                wo["work_order_id"],
                wo["aircraft"],
                wo["location"],
                wo["status"],
                task["description"],
                task["assigned_to"],
                task["status"]
            ])

    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=work_orders.csv"
    response.headers["Content-type"] = "text/csv"
    return response


# === TASK TRACKER ===
@app.route('/tasks', methods=['GET', 'POST'])
def tasks():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    conn = sqlite3.connect(TASK_DB)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            description TEXT NOT NULL,
            assigned_to TEXT,
            due_date DATE,
            status TEXT DEFAULT 'Pending',
            notes TEXT
        )
    ''')

    if request.method == 'POST':
        cursor.execute('''
            INSERT INTO tasks (description, assigned_to, due_date, notes)
            VALUES (?, ?, ?, ?)
        ''', (
            request.form['description'],
            request.form['assigned_to'],
            request.form['due_date'],
            request.form['notes']
        ))
        conn.commit()
        return redirect('/tasks')

    # FILTER BY STATUS
    filter_status = request.args.get('status', 'All')
    if filter_status == 'Pending':
        cursor.execute("SELECT * FROM tasks WHERE status = 'Pending' ORDER BY due_date")
    elif filter_status == 'Completed':
        cursor.execute("SELECT * FROM tasks WHERE status = 'Completed' ORDER BY due_date")
    else:
        cursor.execute("SELECT * FROM tasks ORDER BY due_date")

    tasks = cursor.fetchall()
    conn.close()
    return render_template("tasks.html", tasks=tasks, now=datetime.now().strftime("%Y-%m-%d"),
                           filter_status=filter_status)



@app.route('/complete-task/<int:task_id>')
def complete_task(task_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    conn = sqlite3.connect(TASK_DB)
    cursor = conn.cursor()
    cursor.execute("UPDATE tasks SET status = 'Completed' WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()
    return redirect('/tasks')


@app.route('/delete-task/<int:task_id>', methods=['POST'])
def delete_task(task_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    conn = sqlite3.connect(TASK_DB)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()
    return redirect('/tasks')


import csv
from io import StringIO
from flask import make_response

@app.route('/tasks/export')
def export_tasks():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    conn = sqlite3.connect(TASK_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM tasks ORDER BY due_date")
    tasks = cursor.fetchall()
    conn.close()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Description", "Assigned To", "Due Date", "Status", "Notes"])
    writer.writerows(tasks)

    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=maintenance_tasks.csv"
    response.headers["Content-type"] = "text/csv"
    return response



# === SIGN-OFF LOG ===
@app.route('/signoff', methods=['GET', 'POST'])
def signoff():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    conn = sqlite3.connect(SIGNOFF_DB)
    cursor = conn.cursor()

    # First create the table
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS signoffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task TEXT,
            tech_name TEXT,
            signature TEXT,
            timestamp TEXT
        )
    ''')
    conn.commit()
    conn.close()

    # Then run safe migration checks
    ensure_column_exists(SIGNOFF_DB, "signoffs", "tech_name", "TEXT")

    # Reconnect after the table is safely built
    conn = sqlite3.connect(SIGNOFF_DB)
    cursor = conn.cursor()



    if request.method == 'POST':
        task = request.form['task']
        tech_name = request.form['tech_name']
        signature = request.form['signature']
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        cursor.execute('''
            INSERT INTO signoffs (task, tech_name, signature, timestamp)
            VALUES (?, ?, ?, ?)
        ''', (task, tech_name, signature, timestamp))
        conn.commit()

    cursor.execute('SELECT task, tech_name, signature, timestamp FROM signoffs ORDER BY timestamp DESC')
    signoffs = cursor.fetchall()
    conn.close()

    return render_template('signoff.html', signoffs=signoffs)

@app.route('/signoff/export')
def export_signoffs():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    conn = sqlite3.connect(SIGNOFF_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT task, tech_name, signature, timestamp FROM signoffs ORDER BY timestamp DESC")
    data = cursor.fetchall()
    conn.close()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Task", "Technician", "Signature", "Timestamp"])
    for row in data:
        writer.writerow(row)

    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=signoffs.csv"
    response.headers["Content-type"] = "text/csv"
    return response


# === INSPECTION CHECKLIST ===
@app.route('/inspections', methods=['GET', 'POST'])
def inspections():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    conn = sqlite3.connect(INSPECTION_DB)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inspections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            part_number TEXT NOT NULL,
            inspector TEXT,
            condition TEXT,
            paperwork_complete TEXT,
            packaging_intact TEXT,
            notes TEXT,
            timestamp TEXT
        )
    ''')
    if request.method == 'POST':
        cursor.execute('''
            INSERT INTO inspections (part_number, inspector, condition, paperwork_complete, packaging_intact, notes, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (
            request.form['part_number'],
            request.form['inspector'],
            request.form['condition'],
            request.form['paperwork_complete'],
            request.form['packaging_intact'],
            request.form['notes'],
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        conn.commit()
        return redirect('/inspections')
    cursor.execute("SELECT * FROM inspections ORDER BY timestamp DESC LIMIT 10")
    inspections = cursor.fetchall()
    conn.close()
    return render_template("inspections.html", inspections=inspections)


# === EXPIRY TRACKER ===
@app.route('/expiry', methods=['GET', 'POST'])
def expiry():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    filter_type = request.args.get('filter', 'expiring')
    search_query = request.args.get('q', '').lower()

    conn = sqlite3.connect(EXPIRY_DB)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT,
            expiry_date DATE,
            responsible TEXT
        )
    ''')

    if request.method == 'POST':
        name = request.form['name']
        category = request.form['category']
        expiry_date = request.form['expiry_date']
        responsible = request.form['responsible']
        cursor.execute('''
            INSERT INTO documents (name, category, expiry_date, responsible)
            VALUES (?, ?, ?, ?)
        ''', (name, category, expiry_date, responsible))
        conn.commit()
        return redirect(f'/expiry?filter={filter_type}&q={search_query}')

    cursor.execute("SELECT * FROM documents")
    all_docs = cursor.fetchall()
    conn.close()

    today = datetime.now()
    filtered_docs = []

    for doc in all_docs:
        try:
            expiry_str = doc[3]
            if expiry_str and expiry_str.strip() and expiry_str.strip() != "0000-00-00":
                expiry_date = datetime.strptime(expiry_str.strip(), "%Y-%m-%d")
                days_left = (expiry_date - today).days

                matches_filter = (
                    (filter_type == "expiring" and 0 <= days_left <= 30) or
                    (filter_type == "expired" and days_left < 0) or
                    (filter_type == "all")
                )

                matches_search = (
                    search_query in doc[1].lower() or search_query in doc[4].lower()
                ) if search_query else True

                if matches_filter and matches_search:
                    filtered_docs.append((doc, days_left))
        except Exception:
            continue

    # Trigger alert if critical documents found
    critical_alert = any(days_left <= 7 for _, days_left in filtered_docs)
    return render_template(
        "expiry.html",
        documents=filtered_docs,
        filter_type=filter_type,
        search_query=search_query,
        critical_alert=critical_alert
    )


@app.route('/expiry/export')
def export_expiry():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    filter_type = request.args.get('filter', 'expiring')
    conn = sqlite3.connect(EXPIRY_DB)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM documents")
    all_docs = cursor.fetchall()
    all_docs.sort(key=lambda d: d[2].lower() if d[2] else "")
    conn.close()

    today = datetime.now()
    output_docs = []

    for doc in all_docs:
        try:
            expiry_str = doc[3]
            if expiry_str and expiry_str.strip() and expiry_str.strip() != "0000-00-00":
                expiry_date = datetime.strptime(expiry_str.strip(), "%Y-%m-%d")
                days_left = (expiry_date - today).days

                if (
                    (filter_type == "expiring" and 0 <= days_left <= 30) or
                    (filter_type == "expired" and days_left < 0) or
                    (filter_type == "all")
                ):
                    output_docs.append(doc + (days_left,))
        except Exception:
            continue

    # Prepare CSV
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Name", "Category", "Expiry Date", "Responsible", "Days Left"])
    for doc in output_docs:
        writer.writerow(doc)

    response = make_response(output.getvalue())
    response.headers["Content-Disposition"] = "attachment; filename=expiry_report.csv"
    response.headers["Content-type"] = "text/csv"
    return response


@app.route('/expiry/delete/<int:doc_id>')
def delete_expiry(doc_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    conn = sqlite3.connect(EXPIRY_DB)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM documents WHERE id = ?", (doc_id,))
    conn.commit()
    conn.close()
    return redirect(url_for('expiry'))

@app.route('/expiry/edit/<int:doc_id>', methods=['GET', 'POST'])
def edit_expiry(doc_id):
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    conn = sqlite3.connect(EXPIRY_DB)
    cursor = conn.cursor()

    if request.method == 'POST':
        name = request.form['name']
        category = request.form['category']
        expiry_date = request.form['expiry_date']
        responsible = request.form['responsible']
        cursor.execute('''
            UPDATE documents
            SET name = ?, category = ?, expiry_date = ?, responsible = ?
            WHERE id = ?
        ''', (name, category, expiry_date, responsible, doc_id))
        conn.commit()
        conn.close()
        return redirect(url_for('expiry'))

    cursor.execute("SELECT * FROM documents WHERE id = ?", (doc_id,))
    doc = cursor.fetchone()
    conn.close()

    return render_template('edit_expiry.html', doc=doc)


# === PDF ORGANIZER LOG VIEWER ===
@app.route('/pdf-organizer-log')
def view_pdf_log():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    if os.path.exists(PDF_LOG_PATH):
        with open(PDF_LOG_PATH, 'r') as f:
            log = f.read()
    else:
        log = ""
    return render_template("pdf_log.html", log=log)


# === PDF ORGANIZER ===
SOURCE_FOLDER = "C:/Users/Shared/AeroRepairCorp/IncomingDocs"
DEST_FOLDER = "C:/Users/Shared/AeroRepairCorp/OrganizedDocs"

ROUTING_KEYWORDS = {
    "FAA Form 8130-3": "Forms/8130-3",
    "Form 8130": "Forms/8130-3",
    "Work Order": "Production/Work Orders",
    "Invoice Number": "Accounting/Invoices",
    "Service Bulletin": "Technical/Service Bulletins"
}


def log_pdf_activity(message):
    with open(PDF_LOG_PATH, "a") as f:
        timestamp = datetime.now().strftime("%b %d, %Y – %I:%M %p")
        f.write(f"[{timestamp}] {message}\n")


def extract_text_from_pdf(filepath):
    try:
        reader = PdfReader(filepath)
        text = ''
        for page in reader.pages[:2]:
            text += page.extract_text() or ''
        return text
    except Exception as e:
        log_pdf_activity(f"Error reading {filepath}: {e}")
        return ""

def determine_destination(text):
    text_lower = text.lower()
    for keyword, folder in ROUTING_KEYWORDS.items():
        if keyword.lower() in text_lower:
            return folder
    return "Unsorted"

def organize_pdf(filepath):
    filename = os.path.basename(filepath)
    text = extract_text_from_pdf(filepath)
    target_subfolder = determine_destination(text)
    target_folder = os.path.join(DEST_FOLDER, target_subfolder)

    if not os.path.exists(target_folder):
        os.makedirs(target_folder)

    new_filename = re.sub(r'\\s+', '_', filename)
    destination_path = os.path.join(target_folder, new_filename)

    shutil.move(filepath, destination_path)
    # Clean up folder path for display
    relative_path = os.path.relpath(destination_path, DEST_FOLDER).replace("\\", "/")
    log_pdf_activity(f"✅ {filename} was sorted into → {relative_path}")


def pdf_organizer_loop(interval=60):
    while True:
        if os.path.exists(SOURCE_FOLDER):
            for file in os.listdir(SOURCE_FOLDER):
                if file.lower().endswith(".pdf"):
                    organize_pdf(os.path.join(SOURCE_FOLDER, file))
        time.sleep(interval)

@app.route('/pdf-organizer-log/clear', methods=['POST'])
def clear_pdf_log():
    if os.path.exists(PDF_LOG_PATH):
        open(PDF_LOG_PATH, 'w').close()
    return redirect(url_for('view_pdf_log'))

@app.route('/pdf-organizer-log/download')
def download_pdf_log():
    if not os.path.exists(PDF_LOG_PATH):
        return "No log available."
    return send_file(PDF_LOG_PATH, as_attachment=True, download_name="pdf_organizer_log.txt")


# === HELP PAGE ===
@app.route('/help')
def help_page():
    return render_template("help.html")

# === ACTIVITY REPORT ===

@app.route('/reports/activity')
def activity_report():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    status = request.args.get('status')
    aircraft_filter = request.args.get('aircraft')

    # Load work order -> aircraft mapping
    with open('mock_quantum_data.json', 'r') as f:
        all_workorders = json.load(f)

    wo_to_aircraft = {
        wo["work_order_id"]: wo["aircraft"]
        for wo in all_workorders
    }

    conn = sqlite3.connect('task_updates.db')
    cursor = conn.cursor()

    query = '''
        SELECT work_order_id, task_id, status, comment, user, timestamp
        FROM task_updates
        WHERE 1=1
    '''
    params = []

    if start_date:
        query += ' AND DATE(timestamp) >= ?'
        params.append(start_date)
    if end_date:
        query += ' AND DATE(timestamp) <= ?'
        params.append(end_date)
    if status:
        query += ' AND status = ?'
        params.append(status)

    query += ' ORDER BY timestamp DESC LIMIT 100'
    cursor.execute(query, params)
    raw_updates = cursor.fetchall()
    conn.close()

    # Filter by aircraft after fetching if needed
    if aircraft_filter:
        updates = [
            row for row in raw_updates
            if wo_to_aircraft.get(row[0], "").lower() == aircraft_filter.lower()
        ]
    else:
        updates = raw_updates

    return render_template(
        'activity_report.html',
        updates=updates,
        wo_to_aircraft=wo_to_aircraft
    )


# === RUN BACKGROUND THREAD FOR PDF ORGANIZER ===
threading.Thread(target=pdf_organizer_loop, daemon=True).start()

# === START FLASK APP ===
if __name__ == "__main__":
    init_user_db()
    create_test_user()
    init_task_db()
    app.run(debug=True)


