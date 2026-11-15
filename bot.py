import os
import sqlite3
import logging
from datetime import datetime, timezone

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler, MessageHandler,
    ContextTypes, filters,
)

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])
DB_PATH = os.getenv("DB_PATH", "simora.sqlite3")

OPERATORS = [
    "МТС",
    "Билайн",
    "Добросвязь",
    "Сбер",
    "Т2",
    "Миранда",
]

OPERATOR_REWARDS = {
    "МТС": "$19",
    "Билайн": "$20",
    "Добросвязь": "$19",
    "Сбер": "$16",
    "Т2": "$20",
    "Миранда": "$20",
}
STATUSES = {
    "new": "Новая",
    "review": "На проверке",
    "accepted": "Принята",
    "rejected": "Отклонена",
    "paid": "Оплачена",
}

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("simora")


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn




def init_db():
    with db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            joined_at TEXT
        )""")

        conn.execute("""CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            operator TEXT NOT NULL,
            photo_file_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'new',
            created_at TEXT NOT NULL
        )""")

        conn.execute("""CREATE TABLE IF NOT EXISTS prices (
            operator TEXT PRIMARY KEY,
            price TEXT NOT NULL DEFAULT 'Уточняется'
        )""")

        for operator, price in OPERATOR_REWARDS.items():
            conn.execute(
                "INSERT OR IGNORE INTO prices(operator, price) VALUES (?, ?)",
                (operator, price),
            )
            conn.execute(
                "UPDATE prices SET price=? WHERE operator=? AND price='Уточняется'",
                (price, operator),
            )

        conn.execute("""CREATE TABLE IF NOT EXISTS balances (
            user_id INTEGER PRIMARY KEY,
            amount REAL NOT NULL DEFAULT 0
        )""")

        conn.execute("""CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            submission_id INTEGER UNIQUE,
            amount REAL NOT NULL,
            description TEXT NOT NULL,
            created_at TEXT NOT NULL
        )""")

        conn.execute("""CREATE TABLE IF NOT EXISTS withdrawals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            method TEXT NOT NULL,
            invoice_link TEXT NOT NULL,
            amount REAL NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL,
            processed_at TEXT
        )""")

def save_user(user):
    with db() as conn:
        conn.execute(
            """INSERT INTO users(user_id, username, first_name, joined_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
               username=excluded.username, first_name=excluded.first_name""",
            (user.id, user.username or "", user.first_name or "",
             datetime.now(timezone.utc).isoformat()),
        )


def main_menu():
    keyboard = [
        [KeyboardButton("📲 Сдать eSIM"), KeyboardButton("👤 Профиль")],
        [KeyboardButton("💰 Баланс"), KeyboardButton("📱 Мои номера")],
    ]
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        is_persistent=True,
    )


def back_menu():
    keyboard = [[KeyboardButton("⬅️ Назад")]]
    return ReplyKeyboardMarkup(
        keyboard,
        resize_keyboard=True,
        is_persistent=True,
    )


def admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🆕 Новые заявки", callback_data="admin_new")],
        [InlineKeyboardButton("💰 Изменить цены", callback_data="admin_prices")],
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    save_user(user)
    context.user_data.clear()
    await update.message.reply_text(
        f"Привет, {user.first_name}! Это SIMORA — сервис приёма заявок на продажу eSIM.\n\n"
        "Выберите действие:",
        reply_markup=main_menu(),
    )


async def myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"Ваш Telegram ID: {update.effective_user.id}")


async def admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Нет доступа.")
        return
    await update.message.reply_text("Панель администратора SIMORA:", reply_markup=admin_menu())


async def show_prices(chat_id, context):
    with db() as conn:
        rows = conn.execute("SELECT operator, price FROM prices ORDER BY operator").fetchall()
    text = "Цены выкупа:\n" + "\n".join(f"• {r['operator']}: {r['price']}" for r in rows)
    await context.bot.send_message(chat_id, text)



async def callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id
    data = q.data

    if (
    data.startswith("admin_")
    or data.startswith("decision:")
    or data.startswith("reject_reason:")
) and user_id != ADMIN_ID:
        await q.edit_message_text("Нет доступа.")
        return

    if data == "sell":
        with db() as conn:
            rows = conn.execute(
                "SELECT operator, price FROM prices"
            ).fetchall()

        prices = {row["operator"]: row["price"] for row in rows}

        buttons = [
            [InlineKeyboardButton(
                f"{op} — {prices.get(op, OPERATOR_REWARDS[op])}",
                callback_data=f"op:{op}"
            )]
            for op in OPERATORS
        ]

        await q.edit_message_text(
            "Выберите оператора eSIM:",
            reply_markup=InlineKeyboardMarkup(buttons),
        )

    
    
    elif data.startswith("decision:"):
        try:
            _, decision, submission_id = data.split(":")
            submission_id = int(submission_id)
        except (ValueError, TypeError):
            return

        if decision == "reject":
            keyboard = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "БЛОК",
                        callback_data=f"reject_reason:BLOCK:{submission_id}"
                    ),
                    InlineKeyboardButton(
                        "СЕТЬ",
                        callback_data=f"reject_reason:СЕТЬ:{submission_id}"
                    ),
                    InlineKeyboardButton(
                        "НЕСКАН",
                        callback_data=f"reject_reason:НЕСКАН:{submission_id}"
                    ),
                ]
            ])
            await q.edit_message_caption(
                caption=f"Заявка #{submission_id}\nВыберите причину отказа:",
                reply_markup=keyboard,
            )
            return

        if decision != "accept":
            return

        with db() as conn:
            conn.execute("BEGIN IMMEDIATE")

            row = conn.execute(
                "SELECT user_id, operator, status FROM submissions WHERE id=?",
                (submission_id,),
            ).fetchone()

            if not row or row["status"] != "new":
                await context.bot.send_message(
                    ADMIN_ID, "Заявка уже обработана или не найдена."
                )
                return

            price_row = conn.execute(
                "SELECT price FROM prices WHERE operator=?",
                (row["operator"],),
            ).fetchone()

            try:
                amount = float(
                    str(price_row["price"]).replace("$", "").strip()
                ) if price_row else 0
            except (ValueError, TypeError):
                amount = 0

            if not amount > 0 or amount == float("inf"):
                await context.bot.send_message(
                    ADMIN_ID, "Некорректная цена. Начисление отменено."
                )
                return

            updated = conn.execute(
                "UPDATE submissions SET status='accepted' "
                "WHERE id=? AND status='new'",
                (submission_id,),
            )

            if updated.rowcount != 1:
                return

            conn.execute(
                "INSERT OR IGNORE INTO balances(user_id, amount) VALUES (?, 0)",
                (row["user_id"],),
            )

            transaction = conn.execute(
                "INSERT OR IGNORE INTO transactions "
                "(user_id, submission_id, amount, description, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    row["user_id"],
                    submission_id,
                    amount,
                    f"Начисление за {row['operator']}",
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

            if transaction.rowcount == 1:
                conn.execute(
                    "UPDATE balances SET amount=amount+? WHERE user_id=?",
                    (amount, row["user_id"]),
                )

        await q.message.delete()

        await context.bot.send_message(
            row["user_id"],
            f"✅ Заявка #{submission_id} успешно принята!\n"
            f"Начислено: ${amount:.2f}",
        )

    elif data.startswith("reject_reason:"):
        try:
            _, reason, submission_id = data.split(":")
            submission_id = int(submission_id)
        except (ValueError, TypeError):
            return

        reasons = {
            "BLOCK": "БЛОК",
            "СЕТЬ": "СЕТЬ",
            "НЕСКАН": "НЕСКАН",
        }

        if reason not in reasons:
            return

        with db() as conn:
            updated = conn.execute(
                "UPDATE submissions SET status='rejected' "
                "WHERE id=? AND status='new'",
                (submission_id,),
            )

            if updated.rowcount != 1:
                await context.bot.send_message(
                    ADMIN_ID, "Заявка уже обработана или не найдена."
                )
                return

            row = conn.execute(
                "SELECT user_id FROM submissions WHERE id=?",
                (submission_id,),
            ).fetchone()

        await q.message.delete()

        await context.bot.send_message(
            row["user_id"],
            f"❌ Заявка #{submission_id} отклонена.\n"
            f"Причина: {reasons[reason]}",
        )

    

    

    elif data == "balance":
        await q.edit_message_text(
            "💰 Раздел «Баланс» готовится к запуску.",
            reply_markup=main_menu(),
        )

    elif data == "profile":
        with db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM submissions WHERE user_id=?", (user_id,)
            ).fetchone()[0]
        await q.edit_message_text(
            f"Ваш профиль\nTelegram ID: {user_id}\nЗаявок: {count}",
            reply_markup=main_menu(),
        )

    elif data == "my_requests":
        with db() as conn:
            rows = conn.execute(
                "SELECT id, operator, status, created_at FROM submissions "
                "WHERE user_id=? ORDER BY id DESC LIMIT 10", (user_id,)
            ).fetchall()
        if not rows:
            text = "У вас пока нет заявок."
        else:
            text = "Ваши последние заявки:\n" + "\n".join(
                f"#{r['id']} — {r['operator']} — {STATUSES.get(r['status'], r['status'])}"
                for r in rows
            )
        await q.edit_message_text(text, reply_markup=main_menu())

    elif data == "info":
        await q.edit_message_text(
            "SIMORA принимает заявки на добровольную продажу eSIM. "
            "Передача и оплата согласуются отдельно. Не передавайте коды "
            "активации и данные, которыми не имеете права распоряжаться.",
            reply_markup=main_menu(),
        )

    elif data == "admin_new":
        with db() as conn:
            rows = conn.execute(
                "SELECT id, user_id, operator, created_at FROM submissions "
                "WHERE status='new' ORDER BY id DESC LIMIT 10"
            ).fetchall()
        if not rows:
            await q.edit_message_text("Новых заявок нет.", reply_markup=admin_menu())
            return
        await q.edit_message_text("Новые заявки:\n" + "\n".join(
            f"#{r['id']} | {r['operator']} | пользователь {r['user_id']}"
            for r in rows
        ) + "\n\nЧтобы открыть заявку, отправьте /request НОМЕР")

    elif data == "admin_prices":
        with db() as conn:
            rows = conn.execute("SELECT operator, price FROM prices").fetchall()
        text = "Текущие цены:\n" + "\n".join(
            f"{r['operator']}: {r['price']}" for r in rows
        )
        text += "\n\nИзменить: /price НАЗВАНИЕ_ОПЕРАТОРА ЦЕНА\nПример: /price МТС $19"
        await q.edit_message_text(text, reply_markup=admin_menu())

    elif data == "admin_stats":
        with db() as conn:
            users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            apps = conn.execute("SELECT COUNT(*) FROM submissions").fetchone()[0]
            new = conn.execute(
                "SELECT COUNT(*) FROM submissions WHERE status='new'"
            ).fetchone()[0]
        await q.edit_message_text(
            f"Пользователей: {users}\nВсего заявок: {apps}\nНовых заявок: {new}",
            reply_markup=admin_menu(),
        )

            


async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("awaiting_photo"):
        return
    photo = update.message.photo[-1]
    operator = context.user_data.get("operator", "Не указан")
    user = update.effective_user
    save_user(user)

    with db() as conn:
        cur = conn.execute(
            "INSERT INTO submissions(user_id, operator, photo_file_id, status, created_at) "
            "VALUES (?, ?, ?, 'new', ?)",
            (user.id, operator, photo.file_id, datetime.now(timezone.utc).isoformat()),
        )
        submission_id = cur.lastrowid

    context.user_data.clear()
    await update.message.reply_text(
        f"Заявка #{submission_id} отправлена администратору. Ожидайте ответа.",
        reply_markup=main_menu(),
    )
    caption = (
        f"Новая заявка SIMORA #{submission_id}\n"
        f"Оператор: {operator}\n"
        f"Пользователь: {user.full_name}\n"
        f"Username: @{user.username or 'не указан'}\n"
        f"Telegram ID: {user.id}\n\n"
        "Проверьте, что изображение не содержит секретного кода активации."
    )
    await context.bot.send_photo(
        chat_id=ADMIN_ID,
        photo=photo.file_id,
        caption=caption,
        reply_markup=InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Принять", callback_data=f"decision:accept:{submission_id}"),
            InlineKeyboardButton("❌ Отклонить", callback_data=f"decision:reject:{submission_id}"),
        ]]),
    )


async def request_details(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Использование: /request НОМЕР")
        return
    sid = int(context.args[0])
    with db() as conn:
        row = conn.execute("SELECT * FROM submissions WHERE id=?", (sid,)).fetchone()
    if not row:
        await update.message.reply_text("Заявка не найдена.")
        return
    await update.message.reply_text(
        f"Заявка #{sid}\nОператор: {row['operator']}\n"
        f"Пользователь ID: {row['user_id']}\nСтатус: {STATUSES.get(row['status'])}"
    )


async def set_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /price МТС 500")
        return
    operator = context.args[0]
    price = " ".join(context.args[1:])
    with db() as conn:
        cur = conn.execute(
            "UPDATE prices SET price=? WHERE operator=?", (price, operator)
        )
    if cur.rowcount:
        await update.message.reply_text(f"Цена для {operator} обновлена: {price}")
    else:
        await update.message.reply_text("Оператор не найден. Проверьте название.")


async def menu_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text

    if text == "⬅️ Назад":
        context.user_data.clear()
        await update.message.reply_text(
            "Главное меню SIMORA. Выберите действие:",
            reply_markup=main_menu(),
        )
        return

    if context.user_data.get("choosing_operator") and any(
        text.startswith(op + " — ") for op in OPERATORS
    ):
        operator = text.split(" — ", 1)[0]
        context.user_data["operator"] = operator
        context.user_data["awaiting_photo"] = True
        context.user_data["choosing_operator"] = False

        await update.message.reply_text(
            f"Вы выбрали: {operator}\n\n"
            "Теперь отправьте фотографию подтверждения eSIM.",
            reply_markup=back_menu(),
        )
        return

    if text == "📲 Сдать eSIM":
        with db() as conn:
            rows = conn.execute("SELECT operator, price FROM prices").fetchall()

        prices = {row["operator"]: row["price"] for row in rows}

        keyboard = [
            [KeyboardButton(f"{op} — {prices.get(op, OPERATOR_REWARDS[op])}")]
            for op in OPERATORS
        ]
        keyboard.append([KeyboardButton("⬅️ Назад")])

        context.user_data["choosing_operator"] = True

        await update.message.reply_text(
            "Выберите оператора eSIM:",
            reply_markup=ReplyKeyboardMarkup(
                keyboard,
                resize_keyboard=True,
                is_persistent=True,
            ),
        )

    elif text == "👤 Профиль":
        user_id = update.effective_user.id
        with db() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM submissions WHERE user_id=?",
                (user_id,),
            ).fetchone()[0]

        await update.message.reply_text(
            f"👤 Ваш профиль\nTelegram ID: {user_id}\nЗаявок: {count}",
            reply_markup=back_menu(),
        )

    elif text == "💰 Баланс":
        user_id = update.effective_user.id

        with db() as conn:
            row = conn.execute(
                "SELECT amount FROM balances WHERE user_id=?",
                (user_id,),
            ).fetchone()

            amount = row["amount"] if row else 0

        await update.message.reply_text(
            f"💰 Ваш баланс: ${amount:.2f}",
            reply_markup=back_menu(),
        )

    elif text == "📱 Мои номера":
        user_id = update.effective_user.id
        with db() as conn:
            rows = conn.execute(
                "SELECT id, operator, status FROM submissions "
                "WHERE user_id=? ORDER BY id DESC LIMIT 10",
                (user_id,),
            ).fetchall()

        if not rows:
            result = "У вас пока нет заявок."
        else:
            result = "📱 Ваши заявки:\n" + "\n".join(
                f"#{row['id']} — {row['operator']} — "
                f"{STATUSES.get(row['status'], row['status'])}"
                for row in rows
            )

        await update.message.reply_text(result, reply_markup=back_menu())

    elif context.user_data.get("awaiting_photo"):
        await update.message.reply_text(
            "Отправьте фотографию подтверждения eSIM или нажмите «⬅️ Назад»."
        )

def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("myid", myid))
    app.add_handler(CommandHandler("admin", admin))
    app.add_handler(CommandHandler("request", request_details))
    app.add_handler(CommandHandler("price", set_price))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, menu_text))
    app.add_handler(CallbackQueryHandler(callback))
    app.add_handler(MessageHandler(filters.PHOTO, receive_photo))
    log.info("SIMORA bot starting")
    app.run_polling()


if __name__ == "__main__":
    main()
