from flask import Flask, render_template, request, redirect, url_for
import sqlite3
from datetime import datetime, timedelta
import os
import threading
import time
import shutil
import re
from PyPDF2 import PdfReader

app = Flask(__name__)

# === DATABASE PATHS ===
TASK_DB = "aero_repair_tasks.db"
SIGNOFF_DB = "signoffs.db"
INSPECTION_DB = "inspection_logs.db"
EXPIRY_DB = "document_expiry.db"
PDF_LOG_PATH = "pdf_organizer.log"

# === DASHBOARD HOME ===
@app.route('/')
def dashboard():
    return render_template("base.html")

# === TASK TRACKER ===
@app.route('/tasks', methods=['GET', 'POST'])
def tasks():
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
    cursor.execute("SELECT * FROM tasks ORDER BY due_date")
    tasks = cursor.fetchall()
    conn.close()
    return render_template("tasks.html", tasks=tasks)

@app.route('/complete-task/<int:task_id>')
def complete_task(task_id):
    conn = sqlite3.connect(TASK_DB)
    cursor = conn.cursor()
    cursor.execute("UPDATE tasks SET status = 'Completed' WHERE id = ?", (task_id,))
    conn.commit()
    conn.close()
    return redirect('/tasks')

# === SIGN-OFF LOG ===
@app.route('/signoffs', methods=['GET', 'POST'])
def signoffs():
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
    conn = sqlite3.connect(INSPECTION_DB)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS inspections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            part_number TEXT NOT NULL,
            inspector TEXT NOT NULL,
            condition TEXT NOT NULL,
            paperwork_complete TEXT NOT NULL,
            packaging_intact TEXT NOT NULL,
            notes TEXT,
            timestamp TEXT NOT NULL
        )
    ''')
    if request.method == 'POST':
        cursor.execute('''
            INSERT INTO inspections (part_number, inspector, condition, paperwork_complete,
                                     packaging_intact, notes, timestamp)
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
    conn = sqlite3.connect(EXPIRY_DB)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            category TEXT,
            expiry_date DATE NOT NULL,
            responsible TEXT,
            notified INTEGER DEFAULT 0
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
    today = datetime.now().date()
    upcoming = today + timedelta(days=30)
    cursor.execute("SELECT * FROM documents WHERE expiry_date <= ? ORDER BY expiry_date ASC", (upcoming,))
    documents = cursor.fetchall()
    conn.close()
    return render_template("expiry.html", documents=documents)

# === PDF ORGANIZER LOG VIEWER ===
@app.route('/pdf-organizer-log')
def view_pdf_log():
    if not os.path.exists(PDF_LOG_PATH):
        return render_template("pdf_log.html", log="No activity yet.")
    with open(PDF_LOG_PATH, "r") as f:
        log_lines = f.readlines()[-20:]
    return render_template("pdf_log.html", log="".join(log_lines))

# === PDF ORGANIZER ===
SOURCE_FOLDER = "C:/Users/Shared/AeroRepairCorp/IncomingDocs"
DEST_FOLDER = "C:/Users/Shared/AeroRepairCorp/OrganizedDocs"

ROUTING_KEYWORDS = {
    "8130-3": "Forms/8130-3",
    "Invoice": "Accounting/Invoices",
    "Service Bulletin": "Technical/Service Bulletins",
    "Work Order": "Production/Work Orders"
}

def log_pdf_activity(message):
    with open(PDF_LOG_PATH, "a") as f:
        f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {message}\\n")

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
    for keyword, folder in ROUTING_KEYWORDS.items():
        if keyword.lower() in text.lower():
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
    log_pdf_activity(f"Moved '{filename}' to '{destination_path}'")

def pdf_organizer_loop(interval=60):
    while True:
        if os.path.exists(SOURCE_FOLDER):
            for file in os.listdir(SOURCE_FOLDER):
                if file.lower().endswith(".pdf"):
                    organize_pdf(os.path.join(SOURCE_FOLDER, file))
        time.sleep(interval)

# === HELP PAGE ===
@app.route('/help')
def help_page():
    return render_template("help.html")

# === RUN BACKGROUND THREAD FOR PDF ORGANIZER ===
threading.Thread(target=pdf_organizer_loop, daemon=True).start()

# === START FLASK APP ===
if __name__ == '__main__':
    app.run(debug=True)
