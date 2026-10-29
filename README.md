# Appexil Consultancy Management System (Appexil CMS)

Internal software for **Appexil Visa Consultant** — manage clients, invoices & payments,
expenses, profit dashboard, and auto-generate service agreements.

## Run on your laptop

1. Install Python 3.10+ from python.org
2. Open a terminal in this folder and run:
   ```
   pip install -r requirements.txt
   python app.py
   ```
3. Open http://localhost:5000 in the browser.
   To use it from your phone on the same Wi-Fi: http://YOUR-LAPTOP-IP:5000

## Deploy on Railway (recommended for 24/7 access)

1. railway.app → login → **New Project → Deploy from GitHub repo**
   (push this folder to a GitHub repo first, e.g. `xaimi25/appexil-cms`)
2. Railway auto-detects Python. Set the **Start Command**:
   ```
   gunicorn app:app
   ```
3. Add a **Volume** mounted at `/app` (or set `DB_PATH`) so `appexil.db`
   survives redeploys — or switch `DB_PATH` to the volume path.
4. **Generate Domain** → you get a public `*.up.railway.app` URL.

## First-time setup (inside the app)

1. **Settings** (gear icon): set the **AUD → PKR rate**, fill in the real
   **bank details** (PKR → Allied Bank, AUD → Meezan Bank / Shoaib Akram),
   confirm default agreement fees (400 / 200 / 200 AUD), optionally set an app PIN.
2. **Clients → + Add Client**: client gets an auto ID like `APP-2026-001`.
3. **Invoices → + New Invoice**: pick client, amount, currency (PKR/AUD),
   installment type. Invoice PDF auto-includes the correct bank details.
   **Mark as Paid** records the payment date — only paid invoices count
   toward revenue/profit.
4. **Expenses**: add operating costs in PKR.
5. **Dashboard**: Net Profit = paid revenue (AUD converted at your rate) − expenses.
6. **Client → Generate Agreement**: edit total/1st/2nd instalment in the popup,
   download `Agreement_[ClientName].pdf`, then **Send via WhatsApp**
   (opens wa.me with a pre-filled message; attach the PDF manually).

## Notes

- Data is stored in `appexil.db` (SQLite) next to `app.py`. Back it up regularly.
- WhatsApp file-sending is manual (wa.me link) — automatic sending needs the
  paid WhatsApp Business API.
