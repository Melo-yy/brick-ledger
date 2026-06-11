"""SQLite database layer for order management with multi-user support."""
import sqlite3
from config import DATABASE_PATH


def get_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    """Create tables if they don't exist + migrate old DBs."""
    conn = get_connection()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS orders (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER REFERENCES users(id),
            model       TEXT,
            size        TEXT,
            platform    TEXT,
            expense     REAL,
            order_date  TEXT,
            received    REAL,
            sell_date   TEXT,
            fee         REAL DEFAULT 0,
            profit      REAL,
            status      TEXT DEFAULT 'selling',
            note        TEXT,
            image_file  TEXT,
            created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # Migration: add user_id column to existing databases
    try:
        conn.execute("ALTER TABLE orders ADD COLUMN user_id INTEGER REFERENCES users(id)")
    except sqlite3.OperationalError:
        pass  # Column already exists
    conn.commit()
    conn.close()


# ── User functions ─────────────────────────────────────────────────────────

def create_user(username: str, password_hash: str) -> int:
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO users (username, password_hash) VALUES (?, ?)",
            (username, password_hash),
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None  # Duplicate username
    finally:
        conn.close()


def get_user_by_username(username: str) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM users WHERE username = ?", (username,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT id, username, created_at FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


# ── Order CRUD (all scoped to user_id) ─────────────────────────────────────

def create_order(user_id: int, data: dict) -> int:
    profit = None
    received = data.get("received")
    expense = data.get("expense")
    fee = data.get("fee") or 0
    if received is not None and expense is not None:
        profit = round(received - expense - fee, 2)

    conn = get_connection()
    cur = conn.execute("""
        INSERT INTO orders (user_id, model, size, platform, expense, order_date,
                            received, sell_date, fee, profit, status, note, image_file)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        user_id,
        data.get("model"),
        data.get("size"),
        data.get("platform"),
        expense,
        data.get("order_date"),
        received,
        data.get("sell_date"),
        fee,
        profit,
        data.get("status", "selling"),
        data.get("note"),
        data.get("image_file"),
    ))
    conn.commit()
    oid = cur.lastrowid
    conn.close()
    return oid


def get_order(user_id: int, order_id: int) -> dict | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM orders WHERE id = ? AND user_id = ?",
        (order_id, user_id),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_order(user_id: int, order_id: int, data: dict) -> bool:
    conn = get_connection()
    existing = conn.execute(
        "SELECT * FROM orders WHERE id = ? AND user_id = ?",
        (order_id, user_id),
    ).fetchone()
    if not existing:
        conn.close()
        return False

    model = data.get("model", existing["model"])
    size = data.get("size", existing["size"])
    platform = data.get("platform", existing["platform"])
    expense = data.get("expense") if "expense" in data else existing["expense"]
    order_date = data.get("order_date", existing["order_date"])
    received = data.get("received") if "received" in data else existing["received"]
    sell_date = data.get("sell_date", existing["sell_date"])
    fee = data.get("fee") if "fee" in data else existing["fee"]
    status = data.get("status", existing["status"])
    note = data.get("note", existing["note"])

    profit = None
    if received is not None and expense is not None:
        profit = round(received - expense - (fee or 0), 2)

    conn.execute("""
        UPDATE orders SET model=?, size=?, platform=?, expense=?, order_date=?,
                          received=?, sell_date=?, fee=?, profit=?, status=?,
                          note=?, updated_at=CURRENT_TIMESTAMP
        WHERE id=? AND user_id=?
    """, (model, size, platform, expense, order_date,
          received, sell_date, fee, profit, status,
          note, order_id, user_id))
    conn.commit()
    conn.close()
    return True


def delete_order(user_id: int, order_id: int) -> str | None:
    conn = get_connection()
    row = conn.execute(
        "SELECT image_file FROM orders WHERE id = ? AND user_id = ?",
        (order_id, user_id),
    ).fetchone()
    if not row:
        conn.close()
        return None
    conn.execute("DELETE FROM orders WHERE id = ? AND user_id = ?",
                 (order_id, user_id))
    conn.commit()
    conn.close()
    return row["image_file"]


def list_orders(user_id: int, page=1, limit=20,
                status=None, platform=None, keyword=None) -> tuple:
    conn = get_connection()
    conditions = ["user_id = ?"]
    params = [user_id]

    if status:
        conditions.append("status = ?")
        params.append(status)
    if platform:
        conditions.append("platform = ?")
        params.append(platform)
    if keyword:
        kw = f"%{keyword}%"
        conditions.append("(model LIKE ? OR size LIKE ? OR note LIKE ?)")
        params.extend([kw, kw, kw])

    where = "WHERE " + " AND ".join(conditions)
    total = conn.execute(
        f"SELECT COUNT(*) as cnt FROM orders {where}", params
    ).fetchone()["cnt"]

    offset = (page - 1) * limit
    rows = conn.execute(
        f"SELECT * FROM orders {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [limit, offset],
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows], total


# ── Statistics (scoped to user_id) ─────────────────────────────────────────

def stats_overview(user_id: int):
    conn = get_connection()
    row = conn.execute("""
        SELECT
            COUNT(*) AS total_orders,
            SUM(CASE WHEN status='selling' THEN 1 ELSE 0 END) AS selling_count,
            SUM(CASE WHEN status='sold' THEN 1 ELSE 0 END) AS sold_count,
            COALESCE(SUM(expense), 0) AS total_expense,
            COALESCE(SUM(received), 0) AS total_received,
            COALESCE(SUM(fee), 0) AS total_fee,
            COALESCE(SUM(profit), 0) AS total_profit
        FROM orders WHERE user_id = ?
    """, (user_id,)).fetchone()
    conn.close()
    r = dict(row)
    if r["total_expense"] and r["total_expense"] > 0:
        r["profit_rate"] = round(r["total_profit"] / r["total_expense"] * 100, 1)
    else:
        r["profit_rate"] = 0
    return r


def stats_monthly(user_id: int):
    conn = get_connection()
    rows = conn.execute("""
        SELECT
            SUBSTR(order_date, 1, 7) AS month,
            COALESCE(SUM(expense), 0) AS total_expense,
            COALESCE(SUM(received), 0) AS total_received,
            COALESCE(SUM(profit), 0) AS total_profit
        FROM orders
        WHERE user_id = ? AND order_date IS NOT NULL AND order_date != ''
        GROUP BY month ORDER BY month ASC
    """, (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def stats_platform(user_id: int):
    conn = get_connection()
    rows = conn.execute("""
        SELECT COALESCE(platform, '其他') AS platform, COUNT(*) AS count,
               COALESCE(SUM(expense),0) AS total_expense,
               COALESCE(SUM(received),0) AS total_received,
               COALESCE(SUM(profit),0) AS total_profit
        FROM orders WHERE user_id = ?
        GROUP BY platform ORDER BY count DESC
    """, (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def stats_pending_received(user_id: int):
    conn = get_connection()
    rows = conn.execute("""
        SELECT id, model, size, expense, order_date, platform
        FROM orders
        WHERE user_id = ? AND status='sold' AND (received IS NULL OR received = 0)
        ORDER BY created_at DESC
    """, (user_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def export_csv_data(user_id: int):
    conn = get_connection()
    rows = conn.execute(
        "SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
