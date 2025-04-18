from flask import Flask, render_template, request, redirect, url_for
import sqlite3
from datetime import datetime, timedelta
import os
import threading
import time
import shutil
import re
from PyPDF2 import PdfReader
from flask import send_file


app = Flask(__name__)
app.secret_key = 'aero-dashboard-secret'


# === DATABASE PATHS ===
TASK_DB = "aero_repair_tasks.db"
SIGNOFF_DB = "signoffs.db"
INSPECTION_DB = "inspection_logs.db"
EXPIRY_DB = "document_expiry.db"
PDF_LOG_PATH = "pdf_organizer.log"

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        entered_password = request.form['password']
        if entered_password == 'aeropass123':  # 🔑 Set your shared password here
            session['logged_in'] = True
            return redirect(url_for('dashboard'))
        else:
            return render_template('login.html', error="Incorrect password.")
    return render_template('login.html')


# === DASHBOARD HOME ===
@app.route('/')
def dashboard():
    if not session.get('logged_in'):
        return redirect(url_for('login'))
    return render_template("dashboard.html")



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
@app.route('/signoffs', methods=['GET', 'POST'])
def signoffs():
    if not session.get('logged_in'):
        return redirect(url_for('login'))

    conn = sqlite3.connect(SIGNOFF_DB)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS signoffs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            task TEXT NOT NULL,
            technician TEXT NOT NULL,
            signature TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )
    ''')
    if request.method == 'POST':
        cursor.execute('''
            INSERT INTO signoffs (task, technician, signature, timestamp)
            VALUES (?, ?, ?, ?)
        ''', (
            request.form['task'],
            request.form['technician'],
            request.form['signature'],
            datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        ))
        conn.commit()
        return redirect('/signoffs')
    cursor.execute("SELECT * FROM signoffs ORDER BY timestamp DESC LIMIT 10")
    log = cursor.fetchall()
    conn.close()
    return render_template("signoffs.html", log=log)


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
        cursor.execute('''
            INSERT INTO documents (name, category, expiry_date, responsible)
            VALUES (?, ?, ?, ?)
        ''', (
            request.form['name'],
            request.form['category'],
            request.form['expiry_date'],
            request.form['responsible']
        ))
        conn.commit()
        return redirect('/expiry')

    cursor.execute("SELECT * FROM documents")
    all_docs = cursor.fetchall()
    today = datetime.now()
    upcoming = [doc for doc in all_docs if (datetime.strptime(doc[3], "%Y-%m-%d") - today).days <= 30]
    conn.close()
    return render_template("expiry.html", documents=upcoming)


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

# === RUN BACKGROUND THREAD FOR PDF ORGANIZER ===
threading.Thread(target=pdf_organizer_loop, daemon=True).start()

# === START FLASK APP ===
if __name__ == '__main__':
    app.run(debug=True)
