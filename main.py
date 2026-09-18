import os
import sqlite3
import asyncio
import threading
from decimal import Decimal, InvalidOperation
from functools import wraps
from datetime import datetime
from urllib import request as urllib_request, parse as urllib_parse
import json
import html

from flask import Flask, request, redirect, url_for, render_template_string, session
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters, ConversationHandler,
)

# =========================
# BOT CONFIGURATION
# Edit these values directly in this file.
# =========================

BOT_TOKEN = "8679507547:AAGt9HAyyFKv4qVk5FTLOLQ6zp4bY9ZqB6c"

# Your Telegram numeric user ID
ADMIN_ID = 8875212514

# Password for the web admin panel
ADMIN_KEY = "Nabil@12345678"

# Secret key used by the web panel session
FLASK_SECRET = "728204"

# Web server
WEB_HOST = "0.0.0.0"
WEB_PORT = int(os.getenv("PORT", "8080"))

# SQLite database file
DB_FILE = "store.db"


app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET", "change-this-secret-key")

db_lock = threading.RLock()


def db():
    conn = sqlite3.connect(DB_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def execute(sql, params=(), fetch=False, many=False):
    with db_lock:
        conn = db()
        try:
            cur = conn.cursor()
            if many:
                cur.executemany(sql, params)
            else:
                cur.execute(sql, params)
            conn.commit()
            if fetch:
                return cur.fetchall()
            return cur.lastrowid
        finally:
            conn.close()


def init_db():
    execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )
    """)
    execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE NOT NULL,
        name TEXT DEFAULT '',
        username TEXT DEFAULT '',
        balance REAL DEFAULT 0,
        referral_id INTEGER DEFAULT NULL,
        created_at TEXT NOT NULL
    )
    """)
    execute("""
    CREATE TABLE IF NOT EXISTS products (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL,
        name TEXT NOT NULL,
        description TEXT DEFAULT '',
        price REAL NOT NULL,
        active INTEGER DEFAULT 1,
        max_qty INTEGER DEFAULT 10,
        created_at TEXT NOT NULL
    )
    """)
    execute("""
    CREATE TABLE IF NOT EXISTS inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        product_id INTEGER NOT NULL,
        secret TEXT NOT NULL,
        status TEXT DEFAULT 'available',
        assigned_user INTEGER DEFAULT NULL,
        order_id INTEGER DEFAULT NULL,
        created_at TEXT NOT NULL
    )
    """)
    execute("""
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        product_id INTEGER NOT NULL,
        quantity INTEGER NOT NULL,
        unit_price REAL NOT NULL,
        total REAL NOT NULL,
        delivery TEXT DEFAULT '',
        created_at TEXT NOT NULL
    )
    """)
    execute("""
    CREATE TABLE IF NOT EXISTS deposits (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        method TEXT NOT NULL,
        amount REAL NOT NULL,
        trx_id TEXT NOT NULL,
        status TEXT DEFAULT 'pending',
        created_at TEXT NOT NULL,
        UNIQUE(method, trx_id)
    )
    """)

    defaults = {
        "bkash": "01609345459",
        "nagad": "01609345459",
        "binance": "1016685666",
        "usd_rate": "120",
        "support": "@Gamer13683",
        "ref_percent": "0",
    }
    for k, v in defaults.items():
        execute("INSERT OR IGNORE INTO settings(key,value) VALUES(?,?)", (k, v))

    count = execute("SELECT COUNT(*) AS c FROM products", fetch=True)[0]["c"]
    if count == 0:
        now = datetime.utcnow().isoformat()
        seed = [
            ("Proxy", "Owl Proxy", "200 MB", 10.0),
            ("Proxy", "H143 Lite", "200 MB", 10.0),
            ("Proxy", "9 Proxy", "200 MB", 25.0),
            ("Proxy", "Node Maven", "120 MB", 50.0),
            ("VPN", "NordVPN", "Username / Password", 100.0),
            ("VPN", "Proton VPN", "Username / Password", 100.0),
        ]
        execute(
            "INSERT INTO products(category,name,description,price,created_at) VALUES(?,?,?,?,?)",
            [(a,b,c,d,now) for a,b,c,d in seed],
            many=True
        )


def setting(key):
    row = execute("SELECT value FROM settings WHERE key=?", (key,), fetch=True)
    return row[0]["value"] if row else ""


def set_setting(key, value):
    execute("INSERT INTO settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def get_or_create_user(tg_user, referral_id=None):
    row = execute("SELECT * FROM users WHERE telegram_id=?", (tg_user.id,), fetch=True)
    if row:
        execute("UPDATE users SET name=?, username=? WHERE telegram_id=?",
                (tg_user.full_name or "", tg_user.username or "", tg_user.id))
        return row[0]
    now = datetime.utcnow().isoformat()
    execute("""INSERT INTO users(telegram_id,name,username,referral_id,created_at)
               VALUES(?,?,?,?,?)""",
            (tg_user.id, tg_user.full_name or "", tg_user.username or "",
             referral_id, now))
    return execute("SELECT * FROM users WHERE telegram_id=?", (tg_user.id,), fetch=True)[0]


def main_keyboard():
    return ReplyKeyboardMarkup(
        [["🛒 Buy Products", "👤 My Profile"],
         ["💰 Deposit Money", "📞 Support"]],
        resize_keyboard=True
    )


def money(v):
    return f"{float(v):.2f}"


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    referral_id = None
    if context.args:
        try:
            candidate = int(context.args[0])
            if candidate != update.effective_user.id:
                referral_id = candidate
        except ValueError:
            pass
    user = get_or_create_user(update.effective_user, referral_id)
    await update.message.reply_text(
        "👋 Welcome to BD Store!\n\nChoose an option below.",
        reply_markup=main_keyboard()
    )


async def profile(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = get_or_create_user(update.effective_user)
    orders = execute("SELECT COUNT(*) c FROM orders WHERE user_id=?", (u["id"],), fetch=True)[0]["c"]
    me = u["username"]
    username = f"@{me}" if me else "Not set"
    bot_username = (await context.bot.get_me()).username
    ref = f"https://t.me/{bot_username}?start={u['telegram_id']}"
    await update.message.reply_text(
        "👤 My Profile\n\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"✨ Name: {u['name'] or 'User'}\n"
        f"⚙️ User ID: {u['telegram_id']}\n\n"
        f"🔗 Username: {username}\n"
        f"💰 Balance: {money(u['balance'])} BDT\n"
        f"📦 Total Orders: {orders}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"📊 Referral Link:\n{ref}"
    )


async def buy_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("🔐 Proxy", callback_data="cat:Proxy")],
        [InlineKeyboardButton("🌐 VPN", callback_data="cat:VPN")],
    ]
    await update.message.reply_text("🛒 Buy Products\n\nSelect a category:", reply_markup=InlineKeyboardMarkup(kb))


async def category_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    category = q.data.split(":", 1)[1]
    products = execute(
        "SELECT * FROM products WHERE category=? AND active=1 ORDER BY id",
        (category,), fetch=True
    )
    if not products:
        await q.edit_message_text("No products are available right now.")
        return
    buttons = []
    for p in products:
        stock = execute(
            "SELECT COUNT(*) c FROM inventory WHERE product_id=? AND status='available'",
            (p["id"],), fetch=True
        )[0]["c"]
        buttons.append([InlineKeyboardButton(
            f"{p['name']} — {money(p['price'])} BDT | Stock: {stock}",
            callback_data=f"prod:{p['id']}"
        )])
    buttons.append([InlineKeyboardButton("🔙 Back", callback_data="back:categories")])
    await q.edit_message_text(f"📦 {category}\n\nSelect a product:", reply_markup=InlineKeyboardMarkup(buttons))


async def product_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    pid = int(q.data.split(":", 1)[1])
    p_rows = execute("SELECT * FROM products WHERE id=? AND active=1", (pid,), fetch=True)
    if not p_rows:
        await q.edit_message_text("Product not found.")
        return

    p = p_rows[0]
    stock = execute(
        "SELECT COUNT(*) c FROM inventory WHERE product_id=? AND status='available'",
        (pid,), fetch=True
    )[0]["c"]

    context.user_data["selected_product"] = pid
    context.user_data["quantity"] = 1

    if stock < 1:
        await q.edit_message_text(
            f"📦 {p['name']}\n💾 {p['description']}\n\n❌ Out of stock.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🔙 Back", callback_data=f"cat:{p['category']}")]
            ])
        )
        return

    total = float(p["price"])
    buttons = [
        [InlineKeyboardButton("➖", callback_data=f"qty:{pid}:minus"),
         InlineKeyboardButton("1", callback_data="noop"),
         InlineKeyboardButton("➕", callback_data=f"qty:{pid}:plus")],
        [InlineKeyboardButton(
            f"🛒 Buy Now — {money(total)} BDT",
            callback_data=f"qty:{pid}:buy"
        )],
        [InlineKeyboardButton("🔙 Back", callback_data=f"cat:{p['category']}")]
    ]

    await q.edit_message_text(
        f"📦 {p['name']}\n"
        f"💾 {p['description']}\n"
        f"💰 Price: {money(p['price'])} BDT / 1\n\n"
        f"📦 Quantity: 1\n"
        f"💵 Total: {money(total)} BDT\n"
        f"📊 Available: {stock}",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def quantity_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data.split(":")
    pid = int(data[1])
    action = data[2]
    p_rows = execute("SELECT * FROM products WHERE id=? AND active=1", (pid,), fetch=True)
    if not p_rows:
        await q.edit_message_text("Product not found.")
        return
    p = p_rows[0]
    current = int(context.user_data.get("quantity", 1))
    stock = execute("SELECT COUNT(*) c FROM inventory WHERE product_id=? AND status='available'",
                    (pid,), fetch=True)[0]["c"]
    max_qty = max(1, min(int(p["max_qty"]), stock if stock > 0 else int(p["max_qty"])))
    if action == "plus":
        current = min(current + 1, max_qty)
    elif action == "minus":
        current = max(current - 1, 1)
    elif action == "buy":
        await complete_purchase(update, context, p, current)
        return
    context.user_data["quantity"] = current
    total = float(p["price"]) * current
    buttons = [
        [InlineKeyboardButton("➖", callback_data=f"qty:{pid}:minus"),
         InlineKeyboardButton(str(current), callback_data="noop"),
         InlineKeyboardButton("➕", callback_data=f"qty:{pid}:plus")],
        [InlineKeyboardButton(f"🛒 Buy Now — {money(total)} BDT",
                              callback_data=f"qty:{pid}:buy")],
        [InlineKeyboardButton("🔙 Back", callback_data=f"cat:{p['category']}")]
    ]
    text = (f"📦 {p['name']}\n"
            f"💾 {p['description']}\n"
            f"💰 Price: {money(p['price'])} BDT / 1\n\n"
            f"📦 Quantity: {current}\n"
            f"💵 Total: {money(total)} BDT\n"
            f"📊 Available: {stock}")
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def initial_quantity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    pid = int(q.data.split(":", 1)[1])
    p = execute("SELECT * FROM products WHERE id=? AND active=1", (pid,), fetch=True)[0]
    context.user_data["quantity"] = 1
    context.user_data["selected_product"] = pid
    stock = execute("SELECT COUNT(*) c FROM inventory WHERE product_id=? AND status='available'",
                    (pid,), fetch=True)[0]["c"]
    total = float(p["price"])
    buttons = [
        [InlineKeyboardButton("➖", callback_data=f"qty:{pid}:minus"),
         InlineKeyboardButton("1", callback_data="noop"),
         InlineKeyboardButton("➕", callback_data=f"qty:{pid}:plus")],
        [InlineKeyboardButton(f"🛒 Buy Now — {money(total)} BDT",
                              callback_data=f"qty:{pid}:buy")],
        [InlineKeyboardButton("🔙 Back", callback_data=f"cat:{p['category']}")]
    ]
    await q.edit_message_text(
        f"📦 {p['name']}\n💾 {p['description']}\n"
        f"💰 Price: {money(p['price'])} BDT / 1\n\n"
        f"📦 Quantity: 1\n💵 Total: {money(total)} BDT\n📊 Available: {stock}",
        reply_markup=InlineKeyboardMarkup(buttons)
    )


async def complete_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE, p, qty):
    q = update.callback_query
    tg_id = update.effective_user.id
    user = get_or_create_user(update.effective_user)
    total = round(float(p["price"]) * qty, 2)

    with db_lock:
        conn = db()
        try:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            u = cur.execute("SELECT * FROM users WHERE id=?", (user["id"],)).fetchone()
            available = cur.execute(
                "SELECT * FROM inventory WHERE product_id=? AND status='available' "
                "ORDER BY id LIMIT ?", (p["id"], qty)
            ).fetchall()
            if len(available) < qty:
                conn.rollback()
                await q.edit_message_text(
                    f"❌ Not enough stock.\nAvailable: {len(available)}"
                )
                return
            if float(u["balance"]) < total:
                conn.rollback()
                await q.edit_message_text(
                    f"❌ Insufficient balance.\n\n"
                    f"Price: {money(total)} BDT\n"
                    f"Your balance: {money(u['balance'])} BDT"
                )
                return

            cur.execute("UPDATE users SET balance=balance-? WHERE id=?", (total, user["id"]))
            order_id = cur.execute(
                """INSERT INTO orders(user_id,product_id,quantity,unit_price,total,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (user["id"], p["id"], qty, float(p["price"]), total, datetime.utcnow().isoformat())
            ).lastrowid

            deliveries = []
            for item in available:
                cur.execute(
                    "UPDATE inventory SET status='sold',assigned_user=?,order_id=? WHERE id=?",
                    (user["id"], order_id, item["id"])
                )
                deliveries.append(item["secret"])
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    delivery = "\n\n".join(f"#{i+1}: {x}" for i, x in enumerate(deliveries))
    execute("UPDATE orders SET delivery=? WHERE id=?", (delivery, order_id))
    await q.edit_message_text(
        f"✅ Purchase successful!\n\n"
        f"📦 Product: {p['name']}\n"
        f"🔢 Quantity: {qty}\n"
        f"💵 Total: {money(total)} BDT\n"
        f"🧾 Order ID: #{order_id}\n\n"
        f"📋 Your item(s):\n{delivery}"
    )


async def deposit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("💳 BKASH", callback_data="dep_method:Bkash")],
        [InlineKeyboardButton("💳 NAGAD", callback_data="dep_method:Nagad")],
        [InlineKeyboardButton("💳 BINANCE", callback_data="dep_method:Binance")],
    ]
    await update.message.reply_text("💰 Deposit Money\n\nSelect a payment method:", reply_markup=InlineKeyboardMarkup(kb))


async def deposit_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    method = q.data.split(":", 1)[1]
    context.user_data["deposit_method"] = method
    context.user_data["awaiting_amount"] = True
    await q.edit_message_text(f"💳 {method.upper()}\n\nকত টাকা ডিপোজিট করবেন লিখুন:")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()

    if text == "🛒 Buy Products":
        await buy_menu(update, context); return
    if text == "👤 My Profile":
        await profile(update, context); return
    if text == "💰 Deposit Money":
        await deposit_menu(update, context); return
    if text == "📞 Support":
        await update.message.reply_text(f"📞 Support:\nContact: {setting('support')}"); return

    if context.user_data.get("awaiting_amount"):
        try:
            amount = float(Decimal(text))
            if amount <= 0:
                raise ValueError
        except (InvalidOperation, ValueError):
            await update.message.reply_text("❌ Please enter a valid positive amount.")
            return
        method = context.user_data.get("deposit_method")
        context.user_data["deposit_amount"] = amount
        context.user_data["awaiting_amount"] = False

        if method == "Binance":
            usd = amount
            bdt = usd * float(setting("usd_rate"))
            destination = setting("binance")
            amount_text = f"{usd:.2f} USD ({bdt:.2f} BDT)"
            dest_label = "Pay ID"
        else:
            destination = setting(method.lower())
            amount_text = f"{amount:.2f} BDT"
            dest_label = "number"

        kb = [[InlineKeyboardButton("Payment Done", callback_data="dep_done")]]
        await update.message.reply_text(
            f"💳 Deposit Request\n\n"
            f"Method: {method}\n"
            f"Amount: {amount_text}\n\n"
            f"এই {dest_label} এ টাকা পাঠান:\n{destination}\n\n"
            f"পেমেন্ট সম্পন্ন হলে নিচের বাটনে ক্লিক করুন:",
            reply_markup=InlineKeyboardMarkup(kb)
        )
        return

    if context.user_data.get("awaiting_trx"):
        trx = text[:100]
        method = context.user_data.get("deposit_method")
        amount = float(context.user_data.get("deposit_amount", 0))
        if not method or amount <= 0:
            context.user_data.clear()
            await update.message.reply_text("Session expired. Please start Deposit again.")
            return
        try:
            dep_id = execute(
                "INSERT INTO deposits(user_id,method,amount,trx_id,created_at) VALUES("
                "(SELECT id FROM users WHERE telegram_id=?),?,?,?,?)",
                (update.effective_user.id, method, amount, trx, datetime.utcnow().isoformat())
            )
        except sqlite3.IntegrityError:
            await update.message.reply_text("❌ This Transaction ID has already been submitted.")
            return

        context.user_data.clear()
        await update.message.reply_text(
            f"✅ Deposit request submitted.\nRequest ID: #{dep_id}\n"
            "Admin will review it."
        )
        u = get_or_create_user(update.effective_user)
        await context.bot.send_message(
            ADMIN_ID,
            f"💳 New Deposit Request\n\n"
            f"Request: #{dep_id}\n"
            f"User: @{u['username'] if u['username'] else 'N/A'}\n"
            f"User ID: {u['telegram_id']}\n"
            f"Method: {method}\nAmount: {amount:.2f}\nTrxID: {trx}\n\n"
            f"Use /approve {dep_id} or /reject {dep_id}"
        )
        return

    await update.message.reply_text("Please use the buttons below.", reply_markup=main_keyboard())


async def deposit_done(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    context.user_data["awaiting_trx"] = True
    await q.edit_message_text("⚙️ Transaction ID (TrxID) দিন:")


async def noop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()


def admin_only(func):
    @wraps(func)
    async def wrapper(update, context):
        if update.effective_user.id != ADMIN_ID:
            await update.effective_message.reply_text("⛔ Admin only.")
            return
        return await func(update, context)
    return wrapper


@admin_only
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = execute("SELECT COUNT(*) c FROM users", fetch=True)[0]["c"]
    products = execute("SELECT COUNT(*) c FROM products", fetch=True)[0]["c"]
    pending = execute("SELECT COUNT(*) c FROM deposits WHERE status='pending'", fetch=True)[0]["c"]
    await update.message.reply_text(
        f"🛠 Admin Panel\n\nUsers: {users}\nProducts: {products}\nPending deposits: {pending}\n\n"
        "/approve ID — approve deposit\n"
        "/reject ID — reject deposit\n"
        "/broadcast MESSAGE — broadcast to users\n"
        "/users — show users"
    )


@admin_only
async def approve_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /approve DEPOSIT_ID")
        return
    did = int(context.args[0])
    with db_lock:
        conn = db()
        try:
            cur = conn.cursor()
            cur.execute("BEGIN IMMEDIATE")
            d = cur.execute("SELECT * FROM deposits WHERE id=?", (did,)).fetchone()
            if not d:
                conn.rollback()
                await update.message.reply_text("Deposit not found.")
                return
            if d["status"] != "pending":
                conn.rollback()
                await update.message.reply_text(f"Already {d['status']}.")
                return
            cur.execute("UPDATE deposits SET status='approved' WHERE id=?", (did,))
            cur.execute("UPDATE users SET balance=balance+? WHERE id=?", (d["amount"], d["user_id"]))
            u = cur.execute("SELECT * FROM users WHERE id=?", (d["user_id"],)).fetchone()
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    await update.message.reply_text(f"✅ Deposit #{did} approved. Added {d['amount']:.2f} BDT.")
    await context.bot.send_message(u["telegram_id"],
                                   f"✅ Deposit approved!\nAdded: {d['amount']:.2f} BDT")


@admin_only
async def reject_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /reject DEPOSIT_ID")
        return
    did = int(context.args[0])
    with db_lock:
        conn = db()
        try:
            cur = conn.cursor()
            d = cur.execute("SELECT * FROM deposits WHERE id=?", (did,)).fetchone()
            if not d:
                await update.message.reply_text("Deposit not found."); return
            if d["status"] != "pending":
                await update.message.reply_text(f"Already {d['status']}."); return
            cur.execute("UPDATE deposits SET status='rejected' WHERE id=?", (did,))
            u = cur.execute("SELECT * FROM users WHERE id=?", (d["user_id"],)).fetchone()
            conn.commit()
        finally:
            conn.close()
    await update.message.reply_text(f"❌ Deposit #{did} rejected.")
    await context.bot.send_message(u["telegram_id"],
                                   "❌ Admin rejected your deposit request. No balance was added.")


@admin_only
async def users_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = execute("SELECT * FROM users ORDER BY id DESC LIMIT 20", fetch=True)
    if not rows:
        await update.message.reply_text("No users.")
        return
    lines = [f"{r['telegram_id']} | {r['username'] or '-'} | {r['balance']:.2f} BDT" for r in rows]
    await update.message.reply_text("👥 Users\n\n" + "\n".join(lines))


@admin_only
async def broadcast_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = " ".join(context.args).strip()
    if not message:
        await update.message.reply_text("Usage: /broadcast Your message")
        return
    rows = execute("SELECT telegram_id FROM users", fetch=True)
    sent = 0
    for r in rows:
        try:
            await context.bot.send_message(r["telegram_id"], message)
            sent += 1
            await asyncio.sleep(0.05)
        except Exception:
            pass
    await update.message.reply_text(f"📢 Broadcast finished. Sent: {sent}/{len(rows)}")


# ---------------- Web Admin Panel ----------------

LOGIN_HTML = """
<!doctype html><title>BD Store Admin</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{font-family:Arial;background:#111;color:#eee;max-width:900px;margin:30px auto;padding:15px}
.card{background:#1c1c1c;padding:18px;border-radius:12px;margin:12px 0}
input,select,textarea{width:100%;box-sizing:border-box;padding:10px;margin:6px 0;background:#222;color:#fff;border:1px solid #444;border-radius:7px}
button,a.btn{padding:9px 13px;border:0;border-radius:7px;background:#2d7cff;color:#fff;text-decoration:none;cursor:pointer}
table{width:100%;border-collapse:collapse}td,th{padding:8px;border-bottom:1px solid #333;text-align:left}
</style>
<div class="card"><h2>BD Store Admin</h2>
<form method="post"><input name="key" type="password" placeholder="Admin key"><button>Login</button></form></div>
"""

DASH_HTML = """
<!doctype html><html><head><title>BD Store Admin</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
*{box-sizing:border-box}body{margin:0;font-family:Inter,Arial,sans-serif;background:#0b1020;color:#eef2ff}
.wrap{max-width:1250px;margin:auto;padding:18px}.top{display:flex;justify-content:space-between;align-items:center;gap:15px;margin-bottom:18px}
.brand{font-size:25px;font-weight:800}.brand span{color:#60a5fa}.sub{color:#94a3b8;font-size:13px;margin-top:3px}
nav{display:flex;gap:8px;flex-wrap:wrap;background:#111827;border:1px solid #243047;padding:10px;border-radius:16px;position:sticky;top:8px;z-index:5;box-shadow:0 10px 30px #0005}
nav a{color:#dbeafe;text-decoration:none;padding:10px 14px;border-radius:10px;font-weight:650;font-size:14px}nav a:hover,nav a.active{background:#2563eb;color:#fff}
.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:18px 0}.stat{background:linear-gradient(145deg,#151d31,#101625);border:1px solid #27344e;border-radius:18px;padding:18px}.stat .ico{font-size:25px}.stat .num{font-size:28px;font-weight:800;margin-top:8px}.stat .lbl{color:#94a3b8;font-size:13px;margin-top:3px}
.card{background:#111827;border:1px solid #25324a;padding:18px;border-radius:18px;margin:14px 0;box-shadow:0 8px 30px #0002}.card h2{margin:0 0 14px;font-size:20px}.muted{color:#94a3b8}
input,select,textarea{width:100%;padding:11px 12px;margin:5px 0 10px;background:#0b1220;color:#f8fafc;border:1px solid #334155;border-radius:10px;outline:none}input:focus,select:focus,textarea:focus{border-color:#60a5fa;box-shadow:0 0 0 3px #2563eb22}
button,.btn{display:inline-block;padding:10px 14px;background:#2563eb;color:#fff;border:0;border-radius:10px;text-decoration:none;font-weight:700;cursor:pointer}button:hover,.btn:hover{filter:brightness(1.12)}.danger{background:#dc2626}.success{background:#16a34a}.ghost{background:#1e293b}
.table-wrap{overflow:auto;border-radius:12px}table{width:100%;border-collapse:collapse;min-width:700px}th,td{padding:11px;border-bottom:1px solid #263248;text-align:left;vertical-align:top}th{color:#93c5fd;font-size:13px;background:#0d1525}td{color:#e5e7eb;font-size:14px}.pill{display:inline-block;padding:4px 8px;border-radius:999px;background:#1e3a8a;color:#bfdbfe;font-size:12px}.pill.off{background:#3f3f46;color:#d4d4d8}.pill.pending{background:#78350f;color:#fde68a}.pill.ok{background:#14532d;color:#bbf7d0}
.form-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.form-grid .full{grid-column:1/-1}.notice{padding:12px;border-radius:12px;background:#0c1e3a;border:1px solid #1d4ed8;color:#bfdbfe;margin-bottom:12px}.footer{color:#64748b;text-align:center;padding:25px 0;font-size:12px}
@media(max-width:850px){.grid{grid-template-columns:repeat(2,1fr)}.form-grid{grid-template-columns:1fr}.form-grid .full{grid-column:auto}.brand{font-size:21px}}
@media(max-width:520px){.wrap{padding:10px}.grid{grid-template-columns:1fr 1fr;gap:8px}.stat{padding:13px}.stat .num{font-size:22px}nav{position:static}.top{align-items:flex-start}.card{padding:13px}}
</style></head><body><div class="wrap">
<div class="top"><div><div class="brand">⚡ <span>BD</span> Store Admin</div><div class="sub">Manage products, inventory, deposits and users</div></div><a class="btn danger" href="/admin/logout">Logout</a></div>
<nav>
<a href="/admin/home">📊 Dashboard</a><a href="/admin/products">📦 Products</a><a href="/admin/inventory">🗃 Inventory</a>
<a href="/admin/deposits">💳 Deposits</a><a href="/admin/broadcast">📢 Broadcast</a><a href="/admin/settings">⚙️ Settings</a>
</nav>
{{ body|safe }}<div class="footer">BD Store Admin Panel • Secure management area</div></div></body></html>
"""


def admin_required_web(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return wrapper


@app.route("/admin", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        if request.form.get("key") == ADMIN_KEY:
            session["admin"] = True
            return redirect(url_for("admin_home"))
        return LOGIN_HTML.replace("</div>", "</div><p>Invalid key.</p>", 1)
    return LOGIN_HTML


@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.route("/admin/home")
@admin_required_web
def admin_home():
    users = execute("SELECT COUNT(*) c FROM users", fetch=True)[0]["c"]
    orders = execute("SELECT COUNT(*) c FROM orders", fetch=True)[0]["c"]
    deposits = execute("SELECT COUNT(*) c FROM deposits WHERE status='pending'", fetch=True)[0]["c"]
    stock = execute("SELECT COUNT(*) c FROM inventory WHERE status='available'", fetch=True)[0]["c"]
    body = f"""
    <div class='grid'>
      <div class='stat'><div class='ico'>👥</div><div class='num'>{users}</div><div class='lbl'>Total Users</div></div>
      <div class='stat'><div class='ico'>🛒</div><div class='num'>{orders}</div><div class='lbl'>Total Orders</div></div>
      <div class='stat'><div class='ico'>💳</div><div class='num'>{deposits}</div><div class='lbl'>Pending Deposits</div></div>
      <div class='stat'><div class='ico'>📦</div><div class='num'>{stock}</div><div class='lbl'>Available Inventory</div></div>
    </div>
    <div class='card'><h2>🚀 Quick Actions</h2>
      <a class='btn' href='/admin/products'>➕ Add Product</a>
      <a class='btn' href='/admin/inventory'>➕ Add Inventory</a>
      <a class='btn' href='/admin/deposits'>💳 Review Deposits</a>
      <a class='btn' href='/admin/broadcast'>📢 Send Broadcast</a>
    </div>
    <div class='card'><h2>🔐 Admin</h2><p class='muted'>Your existing Telegram Admin ID and admin authentication remain unchanged.</p></div>
    """
    return render_template_string(DASH_HTML, body=body)


@app.route("/admin/products", methods=["GET", "POST"])
@admin_required_web
def admin_products():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            execute("""INSERT INTO products(category,name,description,price,max_qty,created_at)
                       VALUES(?,?,?,?,?,?)""",
                    (request.form["category"], request.form["name"],
                     request.form.get("description",""), float(request.form["price"]),
                     int(request.form.get("max_qty","10")), datetime.utcnow().isoformat()))
        elif action == "update":
            execute("""UPDATE products SET name=?,category=?,description=?,price=?,max_qty=?,active=?
                       WHERE id=?""",
                    (request.form["name"], request.form["category"],
                     request.form.get("description",""), float(request.form["price"]),
                     int(request.form.get("max_qty","10")), int(request.form.get("active","1")),
                     int(request.form["id"])))
        elif action == "delete":
            execute("DELETE FROM products WHERE id=?", (int(request.form["id"]),))
        return redirect(url_for("admin_products"))

    rows = execute("SELECT * FROM products ORDER BY category,id", fetch=True)
    trs = ""
    for p in rows:
        trs += f"""
        <tr><td>{p['id']}</td><td>{p['category']}</td><td>{p['name']}</td>
        <td>{p['price']:.2f}</td><td>{p['max_qty']}</td><td>{'ON' if p['active'] else 'OFF'}</td>
        <td>
        <form method='post' style='margin:0'>
        <input type='hidden' name='action' value='update'><input type='hidden' name='id' value='{p['id']}'>
        <input name='category' value='{p['category']}'><input name='name' value='{p['name']}'>
        <input name='description' value='{p['description']}'><input name='price' value='{p['price']}'>
        <input name='max_qty' value='{p['max_qty']}'><select name='active'>
        <option value='1' {'selected' if p['active'] else ''}>Active</option>
        <option value='0' {'selected' if not p['active'] else ''}>Inactive</option>
        </select><button>Save</button></form>
        <form method='post' style='margin-top:5px'><input type='hidden' name='action' value='delete'>
        <input type='hidden' name='id' value='{p['id']}'><button class='danger'>Delete</button></form>
        </td></tr>"""
    body = f"""
    <div class='card'><h2>➕ Add Product</h2>
    <form method='post'><input type='hidden' name='action' value='add'>
    <input name='category' placeholder='Proxy or VPN' required><input name='name' placeholder='Product name' required>
    <input name='description' placeholder='Description e.g. 200 MB'><input name='price' type='number' step='0.01' placeholder='Price BDT' required>
    <input name='max_qty' type='number' value='10' min='1'><button>Add</button></form></div>
    <div class='card'><h2>📦 Products</h2><table><tr><th>ID</th><th>Cat</th><th>Name</th><th>Price</th><th>Max Qty</th><th>Status</th><th>Edit</th></tr>{trs}</table></div>
    """
    return render_template_string(DASH_HTML, body=body)


@app.route("/admin/inventory", methods=["GET", "POST"])
@admin_required_web
def admin_inventory():
    if request.method == "POST":
        pid = int(request.form["product_id"])
        lines = [x.strip() for x in request.form["items"].splitlines() if x.strip()]
        execute("INSERT INTO inventory(product_id,secret,created_at) VALUES(?,?,?)",
                [(pid, x, datetime.utcnow().isoformat()) for x in lines], many=True)
        return redirect(url_for("admin_inventory"))
    products = execute("SELECT * FROM products ORDER BY category,id", fetch=True)
    rows = execute("""SELECT i.*,p.name FROM inventory i JOIN products p ON p.id=i.product_id
                      ORDER BY i.id DESC LIMIT 100""", fetch=True)
    options = "".join(f"<option value='{p['id']}'>{p['category']} — {p['name']}</option>" for p in products)
    trs = "".join(f"<tr><td>{r['id']}</td><td>{r['name']}</td><td>{r['status']}</td><td>{r['secret']}</td></tr>" for r in rows)
    body = f"""
    <div class='card'><h2>➕ Add Inventory</h2>
    <p><small>Put one proxy/account per line. These values are delivered after purchase.</small></p>
    <form method='post'><select name='product_id'>{options}</select>
    <textarea name='items' rows='8' placeholder='one item per line' required></textarea><button>Add Inventory</button></form></div>
    <div class='card'><h2>📦 Recent Inventory</h2><table><tr><th>ID</th><th>Product</th><th>Status</th><th>Item</th></tr>{trs}</table></div>
    """
    return render_template_string(DASH_HTML, body=body)


@app.route("/admin/deposits", methods=["GET", "POST"])
@admin_required_web
def admin_deposits():
    if request.method == "POST":
        did = int(request.form["id"])
        action = request.form["action"]
        # Web approval uses the same safe transaction logic as the bot commands.
        with db_lock:
            conn = db()
            try:
                cur = conn.cursor()
                cur.execute("BEGIN IMMEDIATE")
                d = cur.execute("SELECT * FROM deposits WHERE id=?", (did,)).fetchone()
                if d and d["status"] == "pending":
                    new_status = "approved" if action == "approve" else "rejected"
                    cur.execute("UPDATE deposits SET status=? WHERE id=?", (new_status, did))
                    if new_status == "approved":
                        cur.execute("UPDATE users SET balance=balance+? WHERE id=?", (d["amount"], d["user_id"]))
                    conn.commit()
                else:
                    conn.rollback()
            finally:
                conn.close()
        return redirect(url_for("admin_deposits"))
    rows = execute("""SELECT d.*,u.telegram_id,u.username FROM deposits d
                      JOIN users u ON u.id=d.user_id ORDER BY d.id DESC LIMIT 100""", fetch=True)
    trs = ""
    for d in rows:
        actions = ""
        if d["status"] == "pending":
            actions = f"""<form method='post'><input type='hidden' name='id' value='{d['id']}'>
            <button name='action' value='approve'>Approve</button>
            <button name='action' value='reject' class='danger'>Reject</button></form>"""
        trs += f"<tr><td>#{d['id']}</td><td>{d['username'] or d['telegram_id']}</td><td>{d['method']}</td><td>{d['amount']:.2f}</td><td>{d['trx_id']}</td><td>{d['status']}</td><td>{actions}</td></tr>"
    body = f"""<div class='card'><h2>💳 Deposits</h2>
    <table><tr><th>ID</th><th>User</th><th>Method</th><th>Amount</th><th>TrxID</th><th>Status</th><th>Action</th></tr>{trs}</table></div>"""
    return render_template_string(DASH_HTML, body=body)


@app.route("/admin/broadcast", methods=["GET", "POST"])
@admin_required_web
def admin_broadcast():
    sent = failed = 0
    preview = ""
    if request.method == "POST":
        message = request.form.get("message", "").strip()
        if not message:
            body = "<div class='card'><h2>📢 Broadcast</h2><div class='notice'>Please write a message first.</div><a class='btn' href='/admin/broadcast'>Back</a></div>"
            return render_template_string(DASH_HTML, body=body)
        rows = execute("SELECT telegram_id FROM users", fetch=True)
        # Use Telegram Bot API directly from the Flask worker thread.
        api = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        for r in rows:
            try:
                data = urllib_parse.urlencode({"chat_id": r["telegram_id"], "text": message}).encode()
                req = urllib_request.Request(api, data=data, method="POST")
                with urllib_request.urlopen(req, timeout=20) as resp:
                    result = json.loads(resp.read().decode("utf-8"))
                if result.get("ok"):
                    sent += 1
                else:
                    failed += 1
            except Exception:
                failed += 1
        preview = f"<div class='notice'>✅ Broadcast finished — Sent: <b>{sent}</b> &nbsp; Failed: <b>{failed}</b> &nbsp; Total: <b>{len(rows)}</b></div>"
    body = f"""
    <div class='card'><h2>📢 Broadcast Message</h2>
      {preview}
      <p class='muted'>This message will be sent to all registered users.</p>
      <form method='post'>
        <textarea name='message' rows='9' placeholder='Write your broadcast message here...' required></textarea>
        <button class='success' type='submit'>📢 Send Broadcast</button>
      </form>
    </div>
    <div class='card'><h2>💡 Tips</h2><p class='muted'>Keep important announcements clear and concise. Users who blocked the bot or cannot receive messages will appear under Failed.</p></div>
    """
    return render_template_string(DASH_HTML, body=body)


@app.route("/admin/settings", methods=["GET", "POST"])
@admin_required_web
def admin_settings():
    keys = ["bkash", "nagad", "binance", "usd_rate", "support", "ref_percent"]
    if request.method == "POST":
        for k in keys:
            set_setting(k, request.form.get(k, ""))
        return redirect(url_for("admin_settings"))
    fields = "".join(f"<label>{k}</label><input name='{k}' value='{setting(k)}'>" for k in keys)
    body = f"""<div class='card'><h2>⚙️ Settings</h2><form method='post'>{fields}<button>Save Settings</button></form></div>"""
    return render_template_string(DASH_HTML, body=body)


def run_web():
    app.run(host=WEB_HOST, port=WEB_PORT, threaded=True, use_reloader=False)


def main():
    init_db()
    threading.Thread(target=run_web, daemon=True).start()

    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("admin", admin_cmd))
    application.add_handler(CommandHandler("approve", approve_cmd))
    application.add_handler(CommandHandler("reject", reject_cmd))
    application.add_handler(CommandHandler("users", users_cmd))
    application.add_handler(CommandHandler("broadcast", broadcast_cmd))
    application.add_handler(MessageHandler(filters.Regex("^🛒 Buy Products$"), buy_menu))
    application.add_handler(MessageHandler(filters.Regex("^👤 My Profile$"), profile))
    application.add_handler(MessageHandler(filters.Regex("^💰 Deposit Money$"), deposit_menu))
    application.add_handler(MessageHandler(filters.Regex("^📞 Support$"), lambda u,c: u.message.reply_text(f"📞 Support:\nContact: {setting('support')}")))
    application.add_handler(CallbackQueryHandler(category_callback, pattern=r"^cat:"))
    application.add_handler(CallbackQueryHandler(initial_quantity, pattern=r"^prod:"))
    application.add_handler(CallbackQueryHandler(quantity_callback, pattern=r"^qty:"))
    application.add_handler(CallbackQueryHandler(deposit_method, pattern=r"^dep_method:"))
    application.add_handler(CallbackQueryHandler(deposit_done, pattern=r"^dep_done$"))
    application.add_handler(CallbackQueryHandler(noop, pattern=r"^noop$"))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print("BD Store Bot is running.")
    print(f"Web admin: http://0.0.0.0:{WEB_PORT}/admin")
    application.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
