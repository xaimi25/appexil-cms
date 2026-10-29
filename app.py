#!/usr/bin/env python3
"""Appexil Consultancy Management System (Appexil CMS).
Client lifecycle: CRM -> invoices/payments -> expenses -> profit dashboard
-> service agreement generator -> WhatsApp handoff.
Run:  pip install -r requirements.txt && python app.py
"""
import os
import re
import sqlite3
from datetime import date, datetime
from functools import wraps
from io import BytesIO

from flask import (Flask, g, redirect, render_template, request, send_file,
                   session, url_for, flash)
from fpdf import FPDF

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(APP_DIR, 'appexil.db')

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'appexil-cms-local-key-change-me')

CLIENT_STATUSES = ["New Lead", "Signed Up", "Documents Pending", "In Progress",
                   "Assessment Lodged", "Completed"]
EXPENSE_CATS = ["Tax", "Office", "Marketing", "Travel", "Software", "Staff", "Other"]
PAY_TYPES = ["Installment 1", "Installment 2", "Full Payment"]

DEFAULT_SETTINGS = {
    'aud_to_pkr_rate': '78',
    'bank_pkr': 'Allied Bank Limited\nAccount Title: [UPDATE IN SETTINGS]\nAccount No: [UPDATE IN SETTINGS]\nIBAN: [UPDATE IN SETTINGS]',
    'bank_aud': 'Meezan Bank Limited\nAccount Title: SHOAIB AKRAM\nAccount No: [UPDATE IN SETTINGS]\nIBAN: [UPDATE IN SETTINGS]',
    'default_total_fee': '400',
    'default_inst1': '200',
    'default_inst2': '200',
    'company_name': 'APPEXIL VISA CONSULTANT',
    'company_website': 'cdrwriteraustralia.net',
    'company_email': 'appexilvisaconsultan@gmail.com',
    'app_pin': '',
}


# ---------------- database ----------------
def get_db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop('db', None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript("""
    CREATE TABLE IF NOT EXISTS clients(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        app_id TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        cnic TEXT, phone TEXT, email TEXT,
        service_type TEXT, status TEXT DEFAULT 'New Lead',
        created_at TEXT DEFAULT (date('now'))
    );
    CREATE TABLE IF NOT EXISTS invoices(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        inv_no TEXT UNIQUE NOT NULL,
        client_id INTEGER NOT NULL REFERENCES clients(id),
        issue_date TEXT NOT NULL,
        amount REAL NOT NULL,
        currency TEXT NOT NULL CHECK(currency IN ('PKR','AUD')),
        pay_type TEXT, status TEXT DEFAULT 'Unpaid',
        paid_date TEXT, created_at TEXT DEFAULT (date('now'))
    );
    CREATE TABLE IF NOT EXISTS expenses(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        amount REAL NOT NULL,
        exp_date TEXT NOT NULL,
        category TEXT, receipt TEXT,
        created_at TEXT DEFAULT (date('now'))
    );
    CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY, value TEXT
    );
    """)
    for k, v in DEFAULT_SETTINGS.items():
        db.execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))
    db.commit()
    db.close()


def get_setting(key):
    row = get_db().execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row['value'] if row else DEFAULT_SETTINGS.get(key, '')


def next_seq(prefix):
    """APP-2026-001 / INV-2026-001 style ids."""
    yr = date.today().year
    like = f"{prefix}-{yr}-%"
    table = 'clients' if prefix == 'APP' else 'invoices'
    col = 'app_id' if prefix == 'APP' else 'inv_no'
    row = get_db().execute(
        f"SELECT {col} FROM {table} WHERE {col} LIKE ? ORDER BY {col} DESC LIMIT 1",
        (like,)).fetchone()
    n = int(row[col].rsplit('-', 1)[1]) + 1 if row else 1
    return f"{prefix}-{yr}-{n:03d}"


def latin(s):
    return (s or '').encode('latin-1', 'replace').decode('latin-1')


def money(a, cur):
    return f"{a:,.0f} {cur}"


# ---------------- simple PIN lock (optional) ----------------
def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        pin = get_setting('app_pin')
        if pin and not session.get('authed'):
            return redirect(url_for('login', next=request.path))
        return view(*a, **kw)
    return wrapped


@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form.get('pin') == get_setting('app_pin') and get_setting('app_pin'):
            session['authed'] = True
            return redirect(request.args.get('next') or url_for('dashboard'))
        flash('Wrong PIN', 'error')
    return render_template('login.html')


@app.route('/logout')
def logout():
    session.pop('authed', None)
    return redirect(url_for('login'))


# ---------------- dashboard ----------------
@app.route('/')
@login_required
def dashboard():
    db = get_db()
    rate = float(get_setting('aud_to_pkr_rate') or 78)
    rev_pkr = db.execute("SELECT COALESCE(SUM(amount),0) FROM invoices WHERE status='Paid' AND currency='PKR'").fetchone()[0]
    rev_aud = db.execute("SELECT COALESCE(SUM(amount),0) FROM invoices WHERE status='Paid' AND currency='AUD'").fetchone()[0]
    revenue = rev_pkr + rev_aud * rate
    expenses = db.execute("SELECT COALESCE(SUM(amount),0) FROM expenses").fetchone()[0]
    profit = revenue - expenses
    n_clients = db.execute("SELECT COUNT(*) FROM clients").fetchone()[0]
    unpaid = db.execute("SELECT COALESCE(SUM(amount),0) FROM invoices WHERE status='Unpaid' AND currency='PKR'").fetchone()[0]
    unpaid_aud = db.execute("SELECT COALESCE(SUM(amount),0) FROM invoices WHERE status='Unpaid' AND currency='AUD'").fetchone()[0]
    recent_inv = db.execute("""SELECT i.*, c.name FROM invoices i JOIN clients c ON c.id=i.client_id
                               ORDER BY i.id DESC LIMIT 5""").fetchall()
    recent_exp = db.execute("SELECT * FROM expenses ORDER BY id DESC LIMIT 5").fetchall()
    return render_template('dashboard.html', revenue=revenue, expenses=expenses,
                           profit=profit, n_clients=n_clients, rate=rate,
                           unpaid_pkr=unpaid, unpaid_aud=unpaid_aud,
                           recent_inv=recent_inv, recent_exp=recent_exp, money=money)


# ---------------- clients ----------------
@app.route('/clients')
@login_required
def clients():
    q = request.args.get('q', '').strip()
    db = get_db()
    if q:
        like = f"%{q}%"
        rows = db.execute("""SELECT c.*, 
            (SELECT COALESCE(SUM(amount),0) FROM invoices WHERE client_id=c.id AND status='Unpaid' AND currency='PKR') AS due_pkr,
            (SELECT COALESCE(SUM(amount),0) FROM invoices WHERE client_id=c.id AND status='Unpaid' AND currency='AUD') AS due_aud,
            (SELECT COALESCE(SUM(amount),0) FROM invoices WHERE client_id=c.id AND status='Unpaid') AS due_n,
            (SELECT COALESCE(SUM(amount),0) FROM invoices WHERE client_id=c.id) AS tot_n
            FROM clients c WHERE c.name LIKE ? OR c.app_id LIKE ? OR c.cnic LIKE ? OR c.phone LIKE ?
            ORDER BY c.id DESC""", (like, like, like, like)).fetchall()
    else:
        rows = db.execute("""SELECT c.*,
            (SELECT COALESCE(SUM(amount),0) FROM invoices WHERE client_id=c.id AND status='Unpaid' AND currency='PKR') AS due_pkr,
            (SELECT COALESCE(SUM(amount),0) FROM invoices WHERE client_id=c.id AND status='Unpaid' AND currency='AUD') AS due_aud
            FROM clients c ORDER BY c.id DESC""").fetchall()
    return render_template('clients.html', clients=rows, q=q, money=money)


@app.route('/clients/new', methods=['GET', 'POST'])
@login_required
def client_new():
    if request.method == 'POST':
        db = get_db()
        app_id = next_seq('APP')
        db.execute("""INSERT INTO clients(app_id,name,cnic,phone,email,service_type,status)
                      VALUES(?,?,?,?,?,?,?)""",
                   (app_id, request.form['name'].strip(), request.form.get('cnic', '').strip(),
                    request.form.get('phone', '').strip(), request.form.get('email', '').strip(),
                    request.form.get('service_type', '').strip(), request.form.get('status', 'New Lead')))
        db.commit()
        flash(f'Client {app_id} added', 'ok')
        return redirect(url_for('clients'))
    return render_template('client_form.html', client=None, statuses=CLIENT_STATUSES)


@app.route('/clients/<int:cid>')
@login_required
def client_detail(cid):
    db = get_db()
    c = db.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
    if not c:
        return "Client not found", 404
    invs = db.execute("SELECT * FROM invoices WHERE client_id=? ORDER BY id DESC", (cid,)).fetchall()
    wa_num = re.sub(r'\D', '', c['phone'] or '')
    return render_template('client_detail.html', c=c, invoices=invs, money=money, wa_num=wa_num)


@app.route('/clients/<int:cid>/edit', methods=['GET', 'POST'])
@login_required
def client_edit(cid):
    db = get_db()
    c = db.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
    if request.method == 'POST':
        db.execute("""UPDATE clients SET name=?,cnic=?,phone=?,email=?,service_type=?,status=?
                      WHERE id=?""",
                   (request.form['name'].strip(), request.form.get('cnic', '').strip(),
                    request.form.get('phone', '').strip(), request.form.get('email', '').strip(),
                    request.form.get('service_type', '').strip(), request.form.get('status', 'New Lead'), cid))
        db.commit()
        flash('Client updated', 'ok')
        return redirect(url_for('client_detail', cid=cid))
    return render_template('client_form.html', client=c, statuses=CLIENT_STATUSES)


@app.route('/clients/<int:cid>/delete', methods=['POST'])
@login_required
def client_delete(cid):
    db = get_db()
    db.execute("DELETE FROM invoices WHERE client_id=?", (cid,))
    db.execute("DELETE FROM clients WHERE id=?", (cid,))
    db.commit()
    flash('Client deleted', 'ok')
    return redirect(url_for('clients'))


# ---------------- invoices ----------------
@app.route('/invoices')
@login_required
def invoices():
    db = get_db()
    rows = db.execute("""SELECT i.*, c.name, c.app_id FROM invoices i
                         JOIN clients c ON c.id=i.client_id ORDER BY i.id DESC""").fetchall()
    return render_template('invoices.html', invoices=rows, money=money)


@app.route('/invoices/new', methods=['GET', 'POST'])
@login_required
def invoice_new():
    db = get_db()
    clients = db.execute("SELECT id, app_id, name FROM clients ORDER BY name").fetchall()
    if request.method == 'POST':
        inv_no = next_seq('INV')
        db.execute("""INSERT INTO invoices(inv_no,client_id,issue_date,amount,currency,pay_type,status)
                      VALUES(?,?,?,?,?,?,'Unpaid')""",
                   (inv_no, request.form['client_id'], request.form.get('issue_date') or str(date.today()),
                    float(request.form['amount']), request.form['currency'], request.form.get('pay_type', '')))
        db.commit()
        flash(f'Invoice {inv_no} created', 'ok')
        return redirect(url_for('invoices'))
    return render_template('invoice_form.html', clients=clients,
                           today=str(date.today()), pay_types=PAY_TYPES,
                           preselect=request.args.get('client_id'))


@app.route('/invoices/<int:iid>')
@login_required
def invoice_detail(iid):
    db = get_db()
    inv = db.execute("""SELECT i.*, c.name, c.app_id, c.phone, c.cnic, c.service_type
                        FROM invoices i JOIN clients c ON c.id=i.client_id WHERE i.id=?""",
                     (iid,)).fetchone()
    if not inv:
        return "Invoice not found", 404
    bank = get_setting('bank_pkr') if inv['currency'] == 'PKR' else get_setting('bank_aud')
    return render_template('invoice_detail.html', inv=inv, money=money, bank=bank,
                           company=get_setting('company_name'))


@app.route('/invoices/<int:iid>/pay', methods=['POST'])
@login_required
def invoice_pay(iid):
    db = get_db()
    db.execute("UPDATE invoices SET status='Paid', paid_date=? WHERE id=?",
               (request.form.get('paid_date') or str(date.today()), iid))
    db.commit()
    flash('Marked as paid', 'ok')
    return redirect(url_for('invoice_detail', iid=iid))


@app.route('/invoices/<int:iid>/pdf')
@login_required
def invoice_pdf(iid):
    db = get_db()
    inv = db.execute("""SELECT i.*, c.name, c.app_id, c.phone, c.service_type
                        FROM invoices i JOIN clients c ON c.id=i.client_id WHERE i.id=?""",
                     (iid,)).fetchone()
    bank = get_setting('bank_pkr') if inv['currency'] == 'PKR' else get_setting('bank_aud')
    pdf = FPDF(format='A4')
    pdf.set_auto_page_break(True, 20)
    pdf.add_page()
    pdf.set_font('Helvetica', 'B', 20)
    pdf.cell(0, 12, latin(get_setting('company_name')), new_x="LMARGIN", new_y="NEXT", align='C')
    pdf.set_font('Helvetica', '', 10)
    pdf.cell(0, 6, latin(f"{get_setting('company_website')} | {get_setting('company_email')}"),
             new_x="LMARGIN", new_y="NEXT", align='C')
    pdf.ln(4)
    pdf.set_draw_color(15, 39, 68)
    pdf.set_line_width(0.8)
    pdf.line(10, pdf.get_y(), 200, pdf.get_y())
    pdf.ln(6)
    pdf.set_font('Helvetica', 'B', 16)
    pdf.cell(0, 10, 'INVOICE', new_x="LMARGIN", new_y="NEXT")
    pdf.set_font('Helvetica', '', 11)
    rows = [("Invoice No:", inv['inv_no']), ("Date of Issue:", inv['issue_date']),
            ("Bill To:", f"{inv['name']} ({inv['app_id']})"),
            ("Phone:", inv['phone'] or '-'), ("Service:", inv['service_type'] or '-'),
            ("Payment Type:", inv['pay_type'] or '-'),
            ("Amount:", money(inv['amount'], inv['currency'])),
            ("Status:", inv['status'] + (f" (paid {inv['paid_date']})" if inv['paid_date'] else ''))]
    for k, v in rows:
        pdf.set_font('Helvetica', 'B', 11)
        pdf.cell(38, 8, latin(k))
        pdf.set_font('Helvetica', '', 11)
        pdf.multi_cell(0, 8, latin(v), new_x="LMARGIN", new_y="NEXT")
    pdf.ln(4)
    pdf.set_font('Helvetica', 'B', 12)
    pdf.cell(0, 8, 'Bank Details for Payment', new_x="LMARGIN", new_y="NEXT")
    pdf.set_font('Helvetica', '', 10)
    pdf.multi_cell(0, 6, latin(bank))
    pdf.ln(2)
    pdf.set_font('Helvetica', 'I', 10)
    pdf.multi_cell(0, 6, 'Please transfer the exact amount. Share the payment receipt on WhatsApp after transfer.')
    buf = BytesIO(bytes(pdf.output()))
    return send_file(buf, download_name=f"Invoice_{inv['inv_no']}.pdf", mimetype='application/pdf')


# ---------------- expenses ----------------
@app.route('/expenses', methods=['GET', 'POST'])
@login_required
def expenses():
    db = get_db()
    if request.method == 'POST':
        db.execute("INSERT INTO expenses(title,amount,exp_date,category) VALUES(?,?,?,?)",
                   (request.form['title'].strip(), float(request.form['amount']),
                    request.form.get('exp_date') or str(date.today()),
                    request.form.get('category', 'Other')))
        db.commit()
        flash('Expense added', 'ok')
        return redirect(url_for('expenses'))
    rows = db.execute("SELECT * FROM expenses ORDER BY exp_date DESC, id DESC").fetchall()
    total = db.execute("SELECT COALESCE(SUM(amount),0) FROM expenses").fetchone()[0]
    return render_template('expenses.html', expenses=rows, total=total,
                           cats=EXPENSE_CATS, today=str(date.today()), money=money)


@app.route('/expenses/<int:eid>/delete', methods=['POST'])
@login_required
def expense_delete(eid):
    db = get_db()
    db.execute("DELETE FROM expenses WHERE id=?", (eid,))
    db.commit()
    flash('Expense deleted', 'ok')
    return redirect(url_for('expenses'))


# ---------------- service agreement generator ----------------
AGREEMENT_BODY = """This Service Agreement ("Agreement") is made and entered into as of the date of the last signature below ("Effective Date"),

BETWEEN:
Client: Name: {CLIENT_NAME}   CNIC: {CLIENT_CNIC}
AND
Service Provider: Mr. Shoaib Akram of Appexil Visa Consultant
The Client and the Service Provider are collectively referred to as the "Parties."

1. Services to be Provided
The Service Provider agrees to provide consultation and application assistance services exclusively for the Client's (CDR WRITING) skills assessment with ENGINEERS AUSTRALIA (the "Services"). The Service Provider is not the assessing authority and does not guarantee a specific outcome from ENGINEERS AUSTRALIA.

2. Authorization
The Client hereby authorizes Appexil Visa Consultant to proceed with the Skill Assessment Process using the provided personal, educational, and professional information. The Client further authorizes Appexil Visa Consultant to use the provided documents solely for this purpose and to correspond with the relevant assessment body ENGINEERS AUSTRALIA on the Client's behalf.

3. Client Responsibilities & Accuracy of Information
The Client acknowledges that they are solely responsible for:
a) The accuracy, truthfulness, and genuineness of all information and documents provided. Any false, misleading, or incomplete information may lead to the rejection of the assessment application, for which Appexil Visa Consultant will not be held responsible.
b) Paying all applicable fees directly to the assessment body.
c) FBR tax Return filing fee is separate from assessment service fee of {TOTAL_FEE} AUD.

4. Compensation and Payment Schedule
The total fee for the Services is {TOTAL_FEE} AUD. This fee is separate from the assessment fee charged by ENGINEERS AUSTRALIA. Payment shall be made according to the following schedule:
- First Instalment: {INST1} AUD is due upon signing this Agreement.
- 2nd Instalment: {INST2} AUD is paid after assessment lodgement.

5. Refund Policy
The Client fully understands and agrees to the following terms:
- Non-Refund Policy (Negative Assessment): If my Skill Assessment result is negative or unsuccessful, I acknowledge that no refund of the service or processing fee will be applicable under any circumstances.
- Refund Policy (Incomplete Process): If the process is not initiated or remains incomplete due to reasons attributable to Appexil Visa Consultant, I will be eligible for a refund of the paid amount after deduction of applicable administration charges (if any).
- Non-Refundable Service Fee (After Commencement): Once work has commenced (e.g., providing document checklist, reviewing client materials), the service charges of {INST1} AUD become non-refundable. This also applies if the Client terminates the agreement due to a "change of mind".

6. Entire Agreement
This Agreement constitutes the entire understanding between the Parties concerning the subject matter and supersedes all prior discussions, agreements, or understandings.

CLIENT DECLARATION
I, the undersigned, hereby authorize Appexil Visa Consultant to proceed with my Skill Assessment under my provided personal, educational, and professional information. I confirm that I have read, fully understand, and voluntarily agree to all the terms and conditions outlined in this Agreement.

AGREED AND ACCEPTED BY:

Signature for Client: ____________________          Signature for Service Provider: ____________________
{CLIENT_NAME}                                        Mr. Shoaib Akram
Date: {DATE}                                         Date: {DATE}

Please send a scanned copy of the signed agreement to {COMPANY_EMAIL} after signature."""


@app.route('/clients/<int:cid>/agreement', methods=['GET', 'POST'])
@login_required
def agreement(cid):
    db = get_db()
    c = db.execute("SELECT * FROM clients WHERE id=?", (cid,)).fetchone()
    if not c:
        return "Client not found", 404
    if request.method == 'POST':
        total = request.form.get('total_fee', get_setting('default_total_fee')).strip()
        inst1 = request.form.get('inst1', get_setting('default_inst1')).strip()
        inst2 = request.form.get('inst2', get_setting('default_inst2')).strip()
        today = date.today().strftime('%d-%m-%Y')
        body = AGREEMENT_BODY.format(
            CLIENT_NAME=c['name'], CLIENT_CNIC=c['cnic'] or '-',
            TOTAL_FEE=total, INST1=inst1, INST2=inst2,
            DATE=today, COMPANY_EMAIL=get_setting('company_email'))
        pdf = FPDF(format='A4')
        pdf.set_auto_page_break(True, 20)
        pdf.add_page()
        pdf.set_font('Helvetica', 'B', 16)
        pdf.cell(0, 10, latin(get_setting('company_name')), new_x="LMARGIN", new_y="NEXT", align='C')
        pdf.set_font('Helvetica', '', 9)
        pdf.cell(0, 5, latin(f"Website: {get_setting('company_website')}  Email: {get_setting('company_email')}"),
                 new_x="LMARGIN", new_y="NEXT", align='C')
        pdf.cell(0, 5, latin(f"Date: {today}"), new_x="LMARGIN", new_y="NEXT", align='C')
        pdf.ln(2)
        pdf.set_font('Helvetica', 'B', 13)
        pdf.cell(0, 8, 'Service Agreement for Migration Skills Assessment',
                 new_x="LMARGIN", new_y="NEXT", align='C')
        pdf.ln(2)
        pdf.set_font('Helvetica', '', 10)
        pdf.multi_cell(0, 5.5, latin(body))
        fname = f"Agreement_{re.sub(r'[^A-Za-z0-9]+', '_', c['name']).strip('_')}.pdf"
        buf = BytesIO(bytes(pdf.output()))
        return send_file(buf, download_name=fname, mimetype='application/pdf')
    return render_template('agreement_form.html', c=c,
                           total=get_setting('default_total_fee'),
                           inst1=get_setting('default_inst1'),
                           inst2=get_setting('default_inst2'))


# ---------------- settings ----------------
@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    db = get_db()
    if request.method == 'POST':
        for k in DEFAULT_SETTINGS:
            if k in request.form:
                db.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                           (k, request.form[k].strip()))
        db.commit()
        flash('Settings saved', 'ok')
        return redirect(url_for('settings'))
    vals = {k: get_setting(k) for k in DEFAULT_SETTINGS}
    return render_template('settings.html', vals=vals)


@app.context_processor
def inject_now():
    return {'today': str(date.today())}


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=False)
