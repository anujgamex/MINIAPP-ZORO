import telebot
import json
import os
import html
import time
import threading
import hmac
import hashlib
from urllib.parse import urlencode, parse_qsl, unquote
from io import BytesIO
from telebot.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton as _TelegramInlineKeyboardButton, WebAppInfo
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

# --- Configuration ---
BOT_TOKEN = "8338860335:AAHJV_0KVGv5ygWI54L98ESycmtmXH3Tjg4"
ADMIN_IDS = [724051786, 8138283513, 7246962358]
# Mini App API configuration. Set these when deploying:
# WEB_APP_URL = public HTTPS URL of the HTML Mini App
# WEB_API_BASE = public HTTPS URL of this bot's Flask API (same server is recommended)
WEB_APP_URL = os.getenv("WEB_APP_URL", "")
WEB_API_BASE = os.getenv("WEB_API_BASE", WEB_APP_URL).rstrip("/")
WITHDRAW_WEB_URL = WEB_APP_URL
WEB_SETTINGS = {"proof": "@TOJIXWORKS", "contact": "@TOJI_HERE_PERSONALBOT"}
WELCOME_PHOTO_PATH = os.getenv("WELCOME_PHOTO_PATH", "welcome.jpg")
bot = telebot.TeleBot(BOT_TOKEN)

# A simple dictionary to store user data temporarily.
user_data = {}

# A list to hold the tasks added by the admin.
available_tasks = []

# A list to hold gmail tasks
gmail_tasks = []

# A list to hold known groups and channels for broadcasting
known_groups = []

auto_broadcast_config = {
    "message_chat_id": None,
    "message_id": None,
    "interval_seconds": 3600, # Default 1 hour
    "total_count": -1, # -1 for infinite
    "sent_count": 0,
    "is_running": False,
    "last_sent_time": 0,
    "message_json": None
}

last_task_approved_time = time.time()

data_lock = threading.RLock()

DATA_FILE = "TOJI_db.json" # Database file name

bot_config = {
    "default_reward": 8.0,
    "default_gmail_reward": 8.0,
    "bulk_review_per_task": 10.0,
    "bulk_gmail_per_task": 10.0
}

FORCE_SUB_CHANNELS = [
  "@CashGlideTutorial",
  "@CashGlideHub",
  "@TOJIXWORKS",
]

def get_ist_time():
    return datetime.now(timezone.utc) + timedelta(hours=5, minutes=30)

def get_ist_date():
    return get_ist_time().strftime("%Y-%m-%d")

def is_task_bulk(task, idx, pool):
    if isinstance(task, dict):
        return task.get("is_bulk", False)
    return False

def get_task_expiry_time(task, idx, pool):
    if isinstance(task, dict):
        return 7200 if task.get("is_bulk", False) else 900
    return 900

def get_user_level(completed_tasks):
    if completed_tasks <= 10:
        return "🥉 ʙᴇɢɪɴɴᴇʀ"
    elif completed_tasks <= 50:
        return "🥈 ᴛʀᴜꜱᴛᴇᴅ ᴡᴏʀᴋᴇʀ"
    elif completed_tasks <= 150:
        return "🥇 ᴘʀᴇᴍɪᴜᴍ ᴡᴏʀᴋᴇʀ"
    else:
        return "👑 ᴠɪᴘ ᴡᴏʀᴋᴇʀ"

def log_admin_action(action_type, details):
    with data_lock:
        logs = bot_config.setdefault("admin_logs", [])
        logs.append({
            "timestamp": time.time(),
            "action": action_type,
            "details": details
        })
        if len(logs) > 1000:
            logs.pop(0)
    save_data()

def deduct_user_balance(user_id, amount):
    udata = user_data[user_id]
    balance = udata.get("balance", 0.0)
    pending = udata.setdefault("pending_withdrawals", [])
    total_pending = sum(w["amount"] for w in pending)
    available_balance = balance - total_pending

    if available_balance >= amount:
        udata["balance"] -= amount
        return

    excess = amount - available_balance
    udata["balance"] -= available_balance

    for w in list(pending):
        if w["amount"] >= excess:
            old_amt = w["amount"]
            w["amount"] -= excess
            new_amt = w["amount"]
            udata["balance"] -= excess
            try:
                bot.send_message(user_id, f"⚠️ Your withdrawal amount changed from ₹{int(old_amt)} to ₹{int(new_amt)} because your approved task was rejected later.")
            except:
                pass
            if w["amount"] <= 0:
                pending.remove(w)
            excess = 0.0
            break
        else:
            udata["balance"] -= w["amount"]
            excess -= w["amount"]
            pending.remove(w)
            try:
                bot.send_message(user_id, f"⚠️ Your withdrawal of ₹{int(w['amount'])} was cancelled because your approved task was rejected later.")
            except:
                pass

    if excess > 0:
        udata["balance"] -= excess

def send_proof(chat_id, file_id, caption, reply_markup=None):
    try:
        return bot.send_photo(chat_id, file_id, caption=caption, reply_markup=reply_markup, parse_mode="HTML")
    except Exception:
        try:
            return bot.send_document(chat_id, file_id, caption=caption, reply_markup=reply_markup, parse_mode="HTML")
        except Exception:
            fallback = caption + "\n\n<i>[⚠️ User's Proof Image Error]</i>"
            return bot.send_message(chat_id, fallback, reply_markup=reply_markup, parse_mode="HTML")

def perform_smart_validation(message, user_id):
    is_photo = message.photo is not None and len(message.photo) > 0
    is_image_doc = message.document is not None and message.document.mime_type and message.document.mime_type.startswith("image/")
    if not is_photo and not is_image_doc:
        return False, "❌ Send screenshot photo only!"
    return True, None

def check_cooldown(user_id):
    return True

def load_data():
    global user_data, available_tasks, gmail_tasks, known_groups, auto_broadcast_config, ADMIN_IDS, bot_config, WEB_SETTINGS
    with data_lock:
        if os.path.exists(DATA_FILE):
            try:
                with open(DATA_FILE, "r") as f:
                    data = json.load(f)
                    user_data_raw = data.get("user_data", {})
                    for k, v in user_data_raw.items():
                        user_data[int(k)] = v
                    available_tasks.extend(data.get("available_tasks", []))
                    gmail_tasks.extend(data.get("gmail_tasks", []))

                    # Migrate old formats to new format dynamically
                    migrated = False
                    for i, task in enumerate(available_tasks):
                        if isinstance(task, str):
                            available_tasks[i] = {
                                "description": task,
                                "reward": bot_config.get("default_reward", 8.0),
                                "active": True,
                                "created_at": time.time(),
                                "status": "available"
                            }
                            migrated = True
                        elif isinstance(task, dict) and "description" in task:
                            desc = task["description"]
                            if "Comment (Tap to copy):" in desc:
                                task["description"] = desc.replace("Comment (Tap to copy):", "Comment:")
                                migrated = True

                    for i, task in enumerate(gmail_tasks):
                        if isinstance(task, str):
                            gmail_tasks[i] = {
                                "description": task,
                                "reward": bot_config.get("default_gmail_reward", 8.0),
                                "active": True,
                                "created_at": time.time(),
                                "status": "available"
                            }
                            migrated = True
                        elif isinstance(task, dict) and "description" in task:
                            desc = task["description"]
                            if "Gmail: " in desc and "Password (Tap to copy):" in desc:
                                try:
                                    gmail_addr = desc.split("Gmail: ")[1].split("\n\nPassword")[0].strip()
                                    password = desc.split("<code>")[1].split("</code>")[0].strip()
                                    task["description"] = f"Gmail : <code>{gmail_addr}</code>\npassword : <code>{password}</code>"
                                    migrated = True
                                except Exception:
                                    pass

                    if migrated:
                        save_data()

                    known_groups.extend(data.get("known_groups", []))
                    # Load auto broadcast config, updating with any new keys from default
                    loaded_config = data.get("auto_broadcast_config", {})
                    auto_broadcast_config.update(loaded_config)
                    saved_admins = data.get("admin_ids", [])
                    for aid in saved_admins:
                        if aid not in ADMIN_IDS:
                            ADMIN_IDS.append(aid)
                    b_config = data.get("bot_config", {})
                    bot_config.update(b_config)
                    WEB_SETTINGS.update(data.get("web_settings", {}) or {})
            except Exception as e:
                print("Error loading data:", e)

def save_data():
    with data_lock:
        try:
            temp_file = DATA_FILE + ".tmp"
            data_to_save = {
                "user_data": user_data.copy(),
                "available_tasks": list(available_tasks),
                "gmail_tasks": list(gmail_tasks),
                "known_groups": list(known_groups),
                "auto_broadcast_config": auto_broadcast_config.copy(),
                "admin_ids": list(ADMIN_IDS),
                "bot_config": bot_config.copy(),
                "web_settings": WEB_SETTINGS.copy()
            }
            with open(temp_file, "w") as f:
                json.dump(data_to_save, f)
            os.replace(temp_file, DATA_FILE)
        except Exception as e:
            print("Error saving data:", e)

load_data()


def _premium_label(text):
    # Keep the existing premium typography/layout, but do not add colored markers.
    text = str(text)
    for marker in ("🟨", "🟦", "🟩", "🟪", "🟧", "🟥", "⬛"):
        text = text.replace(marker, "")
    return text.strip()

def InlineKeyboardButton(text, *args, **kwargs):
    return _TelegramInlineKeyboardButton(_premium_label(text), *args, **kwargs)

def premium_button(text, callback_data=None, url=None, web_app=None):
    return InlineKeyboardButton(text, callback_data=callback_data, url=url, web_app=web_app)


# Track the last bot UI message per private chat so navigation does not stack messages.
last_ui_messages = {}

def _remember_ui_message(message):
    try:
        last_ui_messages[message.chat.id] = message.message_id
    except Exception:
        pass
    return message

def _delete_previous_ui(chat_id, keep_message_id=None):
    old_id = last_ui_messages.get(chat_id)
    if old_id and old_id != keep_message_id:
        try:
            bot.delete_message(chat_id, old_id)
        except Exception:
            pass

def _send_replaced_message(chat_id, text, reply_markup=None, parse_mode="HTML"):
    _delete_previous_ui(chat_id)
    msg = bot.send_message(chat_id, text, parse_mode=parse_mode, reply_markup=reply_markup)
    return _remember_ui_message(msg)

def _send_replaced_photo(chat_id, photo_path, caption, reply_markup=None):
    _delete_previous_ui(chat_id)
    with open(photo_path, "rb") as photo:
        msg = bot.send_photo(chat_id, photo, caption=caption, parse_mode="HTML", reply_markup=reply_markup)
    return _remember_ui_message(msg)

def get_user_menu():
    # Main navigation is a Telegram Reply Keyboard, not inline buttons.
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("ʀᴇᴠɪᴇᴡ ᴛᴀꜱᴋ"), KeyboardButton("ɢᴍᴀɪʟ ᴛᴀꜱᴋ"),
        KeyboardButton("ɪɴᴠɪᴛᴇ & ᴇᴀʀɴ"), KeyboardButton("ᴛᴏᴊɪ ʙᴀɴᴋ"),
        KeyboardButton("ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ"), KeyboardButton("ʜᴇʟᴘ & ꜱᴜᴘᴘᴏʀᴛ")
    )
    return markup

def get_admin_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    markup.add(
        KeyboardButton("ʀᴇᴠɪᴇᴡ ᴛᴀꜱᴋ"), KeyboardButton("ɢᴍᴀɪʟ ᴛᴀꜱᴋ"),
        KeyboardButton("ɪɴᴠɪᴛᴇ & ᴇᴀʀɴ"), KeyboardButton("ᴛᴏᴊɪ ʙᴀɴᴋ"),
        KeyboardButton("ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ"), KeyboardButton("ʜᴇʟᴘ & ꜱᴜᴘᴘᴏʀᴛ"),
        KeyboardButton("ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ✓")
    )
    return markup

def get_cancel_menu():
    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(KeyboardButton("🔙 ᴄᴀɴᴄᴇʟ"))
    return markup

def restore_menu(user_id):
    return get_admin_menu() if user_id in ADMIN_IDS else get_user_menu()

def is_cancel(message):
    if message.text and message.text.lower() in ["🔙 cancel", "/cancel", "cancel", "🔙 ᴄᴀɴᴄᴇʟ"]:
        bot.clear_step_handler_by_chat_id(message.chat.id)
        _send_replaced_message(message.chat.id, "🚫 ᴀᴄᴛɪᴏɴ ᴄᴀɴᴄᴇʟʟᴇᴅ.", restore_menu(message.from_user.id))
        return True
    return False

def check_force_sub(user_id):
    if user_id in ADMIN_IDS:
        return True
    for channel in FORCE_SUB_CHANNELS:
        try:
            member = bot.get_chat_member(channel, user_id)
            if member.status in ['left', 'kicked']:
                return False
        except Exception:
            return False
    return True

def force_sub_markup():
    markup = InlineKeyboardMarkup(row_width=1)
    for channel in FORCE_SUB_CHANNELS:
        markup.add(premium_button(f"ᴊᴏɪɴ {channel}", url=f"https://t.me/{channel.lstrip('@')}"))
    markup.add(premium_button("ɪ ʜᴀᴠᴇ ᴊᴏɪɴᴇᴅ ✓", callback_data="check_joined"))
    return markup

def _chat_id_from_event(event):
    return event.message.chat.id if hasattr(event, "message") else event.chat.id

def _edit_ui_message(chat_id, message_id, text=None, reply_markup=None, caption=None):
    """Edit the current UI message so navigation stays inside one Telegram message."""
    try:
        if caption is not None:
            return bot.edit_message_caption(
                caption=caption, chat_id=chat_id, message_id=message_id,
                parse_mode="HTML", reply_markup=reply_markup
            )
        return bot.edit_message_text(
            text=text or "", chat_id=chat_id, message_id=message_id,
            parse_mode="HTML", reply_markup=reply_markup
        )
    except Exception:
        # Telegram cannot edit a message of a different type; caller can fall back to send.
        return None

def _send_welcome_photo(chat_id, caption, markup):
    _delete_previous_ui(chat_id)
    if os.path.exists(WELCOME_PHOTO_PATH):
        with open(WELCOME_PHOTO_PATH, "rb") as photo:
            msg = bot.send_photo(chat_id, photo, caption=caption, parse_mode="HTML", reply_markup=markup)
    else:
        msg = bot.send_message(chat_id, caption, parse_mode="HTML", reply_markup=markup)
    return _remember_ui_message(msg)

def _welcome_caption(name="User", force_join=False):
    if force_join:
        return (
            "༶•┈┈⛧┈♛\n"
            "🔰 <b>ᴡ ᴇ ʟ ᴄ ᴏ ᴍ ᴇ  ᴛ ᴏ  @CᴀsʜGʟɪᴅᴇBᴏᴛ</b> ⚜️\n"
            "────── ⋆⋅☆⋅⋆ ──────\n\n"
            f"✨ <b>ʜᴇʟʟᴏ, {html.escape(name)}!</b> 🎉\n\n"
            "💎 ᴄᴏᴍᴘʟᴇᴛᴇ ᴛᴀꜱᴋꜱ, ᴇᴀʀɴ ʀᴇᴡᴀʀᴅꜱ & ᴛʀᴀᴄᴋ ʏᴏᴜʀ ᴇᴀʀɴɪɴɢꜱ. 🪙\n"
            "🔐 ʙᴇꜰᴏʀᴇ ʏᴏᴜ ᴄᴏɴᴛɪɴᴜᴇ, ᴊᴏɪɴ ᴀʟʟ ᴏꜰꜰɪᴄɪᴀʟ ᴄʜᴀɴɴᴇʟꜱ.\n\n"
            "👑 <b>ᴏᴡɴᴇʀ:</b> @REAL_TOJIx\n"
            "🤝 <b>ᴘᴀʀᴛɴᴇʀ:</b> @KIRABOSS09\n\n"
            "꘎━━━━━♡꘎━━━━━♡꘎\n"
            "👇 <b>⚡ ᴊᴏɪɴ ᴛʜᴇ ᴄʜᴀɴɴᴇʟꜱ ʙᴇʟᴏᴡ ᴛᴏ ᴜɴʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ:</b>"
        )
    return (
        "༶•┈┈⛧┈♛\n"
        "🔰 <b>ᴡ ᴇ ʟ ᴄ ᴏ ᴍ ᴇ  ᴛ ᴏ  @CᴀsʜGʟɪᴅᴇBᴏᴛ</b> ⚜️\n"
        "────── ⋆⋅☆⋅⋆ ──────\n\n"
        f"✨ <b>ʜᴇʟʟᴏ, {html.escape(name)}!</b> 🎉\n\n"
        "💰 ᴄᴏᴍᴘʟᴇᴛᴇ ᴀᴠᴀɪʟᴀʙʟᴇ ᴛᴀꜱᴋꜱ ᴀɴᴅ ᴇᴀʀɴ ʀᴇᴡᴀʀᴅꜱ.\n"
        "💎 ᴠɪᴇᴡ ʏᴏᴜʀ ᴡᴀʟʟᴇᴛ, ʀᴇꜰᴇʀʀᴀʟꜱ & ᴡɪᴛʜᴅʀᴀᴡᴀʟ.\n"
        "⚡ ᴜꜱᴇ ᴛʜᴇ ᴍᴇɴᴜ ʙᴇʟᴏᴡ ᴛᴏ ɢᴇᴛ ꜱᴛᴀʀᴛᴇᴅ.\n\n"
        "꘎♡━━━━━♡꘎━━━━━♡꘎\n"
        "👇 <b>⚡ ᴄʜᴏᴏꜱᴇ ᴀ ᴘʀᴇᴍɪᴜᴍ ᴏᴘᴛɪᴏɴ ʙᴇʟᴏᴡ:</b>"
    )

def check_force_sub_and_alert(call_or_message):
    user_id = call_or_message.from_user.id
    if check_force_sub(user_id):
        return True

    chat_id = _chat_id_from_event(call_or_message)
    name = call_or_message.from_user.first_name or "User"
    markup = force_sub_markup()
    caption = _welcome_caption(name, force_join=True)

    # If this is already our UI message, replace it instead of creating another message.
    message = getattr(call_or_message, "message", None)
    if message is not None:
        edited = _edit_ui_message(chat_id, message.message_id, caption=caption, reply_markup=markup)
        if edited is None:
            try:
                bot.delete_message(chat_id, message.message_id)
            except Exception:
                pass
            _send_welcome_photo(chat_id, caption, markup)
    else:
        _send_welcome_photo(chat_id, caption, markup)

    if hasattr(call_or_message, "data"):
        try:
            bot.answer_callback_query(call_or_message.id, "❌ ᴊᴏɪɴ ᴀʟʟ ᴏꜰꜰɪᴄɪᴀʟ ᴄʜᴀɴɴᴇʟꜱ ꜰɪʀꜱᴛ!", show_alert=True)
        except Exception:
            pass
    return False

@bot.callback_query_handler(func=lambda call: call.data == "check_joined")
def check_joined_callback(call):
    user_id = call.from_user.id
    if not check_force_sub(user_id):
        bot.answer_callback_query(call.id, "❌ ᴘʟᴇᴀꜱᴇ ᴊᴏɪɴ ᴀʟʟ ᴄʜᴀɴɴᴇʟꜱ ʏᴇᴛ!", show_alert=True)
        return

    name = call.from_user.first_name or "User"
    home_text = _welcome_caption(name, force_join=False)
    home_markup = restore_menu(user_id)
    try:
        # The force-sub screen is normally a photo; keep the same image and replace only its caption/buttons.
        bot.edit_message_caption(home_text, chat_id=call.message.chat.id,
                                  message_id=call.message.message_id,
                                  parse_mode="HTML", reply_markup=home_markup)
    except Exception:
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        _send_welcome_photo(call.message.chat.id, home_text, home_markup)
    bot.answer_callback_query(call.id, "✅ ᴍᴇᴍʙᴇʀꜱʜɪᴘ ᴠᴇʀɪꜰɪᴇᴅ!")

@bot.message_handler(commands=['start'])
def send_welcome(message):
    _delete_previous_ui(message.chat.id)
    user_id = message.from_user.id
    text_parts = message.text.split()
    referrer_id = None
    if len(text_parts) > 1:
        try:
            referrer_id = int(text_parts[1])
        except ValueError:
            pass

    if user_id not in user_data:
        user_data[user_id] = {
            "balance": 0, "tasks_completed": 0, "referrals": 0,
            "completed_tasks": [], "pending_tasks": [],
            "first_name": message.from_user.first_name or "User"
        }
        if referrer_id and referrer_id != user_id:
            user_data[user_id]["referred_by"] = referrer_id
        if message.from_user.username:
            user_data[user_id]["username"] = message.from_user.username
        save_data()
    else:
        u = user_data[user_id]
        u["first_name"] = message.from_user.first_name or "User"
        if message.from_user.username:
            u["username"] = message.from_user.username
        if referrer_id and referrer_id != user_id and not u.get("referred_by"):
            u["referred_by"] = referrer_id
        save_data()

    if not check_force_sub_and_alert(message):
        return

    caption = _welcome_caption(message.from_user.first_name or "User", force_join=False)
    _send_welcome_photo(message.chat.id, caption, restore_menu(user_id))

def get_dashboard_text():
    total_users = len(user_data)
    now = time.time()
    reviews_done_24h, gmails_done_24h = 0, 0
    total_paid_reviews_24h, total_paid_gmails_24h, payment_paid_24h = 0.0, 0.0, 0.0
    
    for u in list(user_data.values()):
        for entry in u.get("completed_tasks_ts", []):
            ts, r = entry if isinstance(entry, (list, tuple)) else (entry, bot_config.get("default_reward", 8.0))
            if now - ts <= 86400: reviews_done_24h += 1; total_paid_reviews_24h += r
        for entry in u.get("completed_gmails_ts", []):
            ts, r = entry if isinstance(entry, (list, tuple)) else (entry, bot_config.get("default_gmail_reward", 8.0))
            if now - ts <= 86400: gmails_done_24h += 1; total_paid_gmails_24h += r
        for ts, amt in u.get("paid_withdrawals_ts", []):
            if now - ts <= 86400: payment_paid_24h += amt

    review_available = sum(1 for task in available_tasks if is_task_available(task))
    gmail_available = sum(1 for task in gmail_tasks if is_task_available(task))
    reviews_in_progress = sum(1 for idx, task in enumerate(available_tasks) if isinstance(task, dict) and task.get("claimed_by") and task.get("status", "available") == "available" and (time.time() - task.get("claim_time", 0) < get_task_expiry_time(task, idx, available_tasks)))
    gmail_in_progress = sum(1 for idx, task in enumerate(gmail_tasks) if isinstance(task, dict) and task.get("claimed_by") and task.get("status", "available") == "available" and (time.time() - task.get("claim_time", 0) < get_task_expiry_time(task, idx, gmail_tasks)))
    pending_reviews = sum(len(u.get("pending_tasks", [])) for u in list(user_data.values()))
    pending_gmails = sum(len(u.get("pending_gmails", [])) for u in list(user_data.values()))
    payment_paid_total = sum(u.get("total_withdrawn", 0) for u in list(user_data.values()))
    payment_pending_total = sum(w['amount'] for u in list(user_data.values()) for w in u.get("pending_withdrawals", []))
    total_user_balances = sum(float(u.get("balance", 0.0)) for u in list(user_data.values()))

    return (
        f"༶•┈┈⛧┈♛\n"
        f"👑 <b>ᴀ ᴅ ᴍ ɪ ɴ  ᴄ ᴏ ɴ ᴛ ʀ ᴏ ʟ  ᴘ ᴀ ɴ ᴇ ʟ</b> 💎\n"
        f"────── ⋆⋅☆⋅⋆ ──────\n\n"
        f"📊 <b>ʟ ɪ ᴠ ᴇ  ᴀ ɴ ᴀ ʟ ʏ ᴛ ɪ ᴄ ꜱ</b>\n"
        f"👥 <b>ᴛᴏᴛᴀʟ ᴜꜱᴇʀꜱ:</b> <code>{total_users}</code>\n\n"
        f"📍 <b>ʀᴇᴠɪᴇᴡ ᴛᴀꜱᴋꜱ</b>\n"
        f" ├ ᴀᴠᴀɪʟᴀʙʟᴇ: <code>{review_available}</code>\n"
        f" ├ ɪɴ ᴘʀᴏɢʀᴇꜱꜱ: <code>{reviews_in_progress}</code>\n"
        f" └ ᴘᴇɴᴅɪɴɢ ᴠᴀʟɪᴅᴀᴛɪᴏɴ: <code>{pending_reviews}</code>\n\n"
        f"📧 <b>ɢᴍᴀɪʟ ᴛᴀꜱᴋꜱ</b>\n"
        f" ├ ᴀᴠᴀɪʟᴀʙʟᴇ: <code>{gmail_available}</code>\n"
        f" ├ ɪɴ ᴘʀᴏɢʀᴇꜱꜱ: <code>{gmail_in_progress}</code>\n"
        f" └ ᴘᴇɴᴅɪɴɢ ᴠᴀʟɪᴅᴀᴛɪᴏɴ: <code>{pending_gmails}</code>\n\n"
        f"⚡ <b>24ʜ ᴘᴇʀꜰᴏʀᴍᴀɴᴄᴇ</b>\n"
        f" ├ ʀᴇᴠɪᴇᴡꜱ ᴅᴏɴᴇ: <code>{reviews_done_24h}</code> (₹{int(total_paid_reviews_24h)})\n"
        f" └ ɢᴍᴀɪʟꜱ ᴅᴏɴᴇ: <code>{gmails_done_24h}</code> (₹{int(total_paid_gmails_24h)})\n\n"
        f"💸 <b>ꜰɪɴᴀɴᴄɪᴀʟꜱ</b>\n"
        f" ├ ᴜꜱᴇʀ ʙᴀʟᴀɴᴄᴇꜱ: <code>₹{int(total_user_balances)}</code>\n"
        f" ├ ᴘᴇɴᴅɪɴɢ ᴘᴀʏᴏᴜᴛꜱ: <code>₹{int(payment_pending_total)}</code>\n"
        f" ├ ᴘᴀɪᴅ (24ʜ): <code>₹{int(payment_paid_24h)}</code>\n"
        f" └ ᴏᴠᴇʀᴀʟʟ ᴘᴀɪᴅ: <code>₹{int(payment_paid_total)}</code>\n"
        f"꘎♡━━━━━♡꘎━━━━━♡꘎"
    )

@bot.message_handler(func=lambda message: message.text in ["👑 Admin Dashboard", "👮 Admin Panel"])
def admin_panel(message):
    if message.from_user.id not in ADMIN_IDS:
        return

    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("🗺️ Review Panel", callback_data="admin_review_panel"),
               InlineKeyboardButton("📨 Gmail Panel", callback_data="admin_gmail_panel"))
    markup.add(InlineKeyboardButton("⚙️ Bot Settings", callback_data="admin_bot_settings"))
    markup.add(InlineKeyboardButton("📑 User Submissions List", callback_data="admin_all_submissions_p0"))
    bot.send_message(message.chat.id, get_dashboard_text(), parse_mode="HTML", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "admin_panel_back")
def handle_admin_panel_back(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("🗺️ Review Panel", callback_data="admin_review_panel"),
               InlineKeyboardButton("📨 Gmail Panel", callback_data="admin_gmail_panel"))
    markup.add(InlineKeyboardButton("⚙️ Bot Settings", callback_data="admin_bot_settings"))
    markup.add(InlineKeyboardButton("📑 User Submissions List", callback_data="admin_all_submissions_p0"))
    bot.edit_message_text(get_dashboard_text(), chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML", reply_markup=markup)


@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_all_submissions_p"))
def admin_all_submissions_list(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    try:
        page = int(call.data.rsplit("_p", 1)[1])
    except Exception:
        page = 0

    records = []

    def reward_for(pool, idx, default_key):
        try:
            idx = int(idx)
            if 0 <= idx < len(pool) and isinstance(pool[idx], dict):
                return float(pool[idx].get("reward", bot_config.get(default_key, 8.0)))
        except Exception:
            pass
        return float(bot_config.get(default_key, 8.0))

    def mask_email(value):
        value = str(value or "").strip()
        if not value or "@" not in value:
            return "—"
        local, domain = value.split("@", 1)
        if len(local) <= 2:
            masked = local[0] + "*" * max(0, len(local)-1)
        else:
            masked = local[:2] + "*" * max(1, len(local)-2)
        return masked + "@" + domain

    for uid, udata in user_data.items():
        name = html.escape(str(udata.get("first_name", "User")))
        username = udata.get("username")

        for idx in udata.get("pending_tasks", []) or []:
            submitted = (udata.get("gmails", {}) or {}).get(str(idx), "")
            records.append((0, f"⏳ <b>PENDING</b> • 📍 Review • <b>{name}</b>" + (f" (@{html.escape(username)})" if username else "") +
                            f"\n👤 TG ID: <code>{uid}</code>\n📧 Gmail: <code>{html.escape(mask_email(submitted))}</code>\n💰 Rate: <b>₹{reward_for(available_tasks, idx, 'default_reward'):.2f}</b>\n🆔 Task: <code>{int(idx)+1}</code>"))

        for idx in udata.get("completed_tasks", []) or []:
            submitted = (udata.get("gmails", {}) or {}).get(str(idx), "")
            records.append((1, f"✅ <b>APPROVED</b> • 📍 Review • <b>{name}</b>" + (f" (@{html.escape(username)})" if username else "") +
                            f"\n👤 TG ID: <code>{uid}</code>\n📧 Gmail: <code>{html.escape(mask_email(submitted))}</code>\n💰 Rate: <b>₹{reward_for(available_tasks, idx, 'default_reward'):.2f}</b>\n🆔 Task: <code>{int(idx)+1}</code>"))

        for idx in udata.get("rejected_tasks", []) or []:
            submitted = (udata.get("gmails", {}) or {}).get(str(idx), "")
            records.append((2, f"🚫 <b>REJECTED</b> • 📍 Review • <b>{name}</b>" + (f" (@{html.escape(username)})" if username else "") +
                            f"\n👤 TG ID: <code>{uid}</code>\n📧 Gmail: <code>{html.escape(mask_email(submitted))}</code>\n💰 Rate: <b>₹{reward_for(available_tasks, idx, 'default_reward'):.2f}</b>\n🆔 Task: <code>{int(idx)+1}</code>"))

        for idx in udata.get("pending_gmails", []) or []:
            creds = (udata.get("gmail_creds", {}) or {}).get(str(idx), "")
            email_value = str(creds).split("\n", 1)[0].replace("Gmail:", "").strip()
            records.append((0, f"⏳ <b>PENDING</b> • 📧 Gmail • <b>{name}</b>" + (f" (@{html.escape(username)})" if username else "") +
                            f"\n👤 TG ID: <code>{uid}</code>\n📧 Gmail: <code>{html.escape(mask_email(email_value))}</code>\n💰 Rate: <b>₹{reward_for(gmail_tasks, idx, 'default_gmail_reward'):.2f}</b>\n🆔 Task: <code>{int(idx)+1}</code>"))

        for idx in udata.get("completed_gmails", []) or []:
            creds = (udata.get("gmail_creds", {}) or {}).get(str(idx), "")
            email_value = str(creds).split("\n", 1)[0].replace("Gmail:", "").strip()
            records.append((1, f"✅ <b>APPROVED</b> • 📧 Gmail • <b>{name}</b>" + (f" (@{html.escape(username)})" if username else "") +
                            f"\n👤 TG ID: <code>{uid}</code>\n📧 Gmail: <code>{html.escape(mask_email(email_value))}</code>\n💰 Rate: <b>₹{reward_for(gmail_tasks, idx, 'default_gmail_reward'):.2f}</b>\n🆔 Task: <code>{int(idx)+1}</code>"))

        for idx in udata.get("rejected_gmails", []) or []:
            creds = (udata.get("gmail_creds", {}) or {}).get(str(idx), "")
            email_value = str(creds).split("\n", 1)[0].replace("Gmail:", "").strip()
            records.append((2, f"🚫 <b>REJECTED</b> • 📧 Gmail • <b>{name}</b>" + (f" (@{html.escape(username)})" if username else "") +
                            f"\n👤 TG ID: <code>{uid}</code>\n📧 Gmail: <code>{html.escape(mask_email(email_value))}</code>\n💰 Rate: <b>₹{reward_for(gmail_tasks, idx, 'default_gmail_reward'):.2f}</b>\n🆔 Task: <code>{int(idx)+1}</code>"))

    # Newest/active records first; stable ordering within each status.
    records.sort(key=lambda x: x[0])
    per_page = 8
    total_pages = max(1, (len(records) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    chunk = records[page * per_page:(page + 1) * per_page]

    header = (
        "📑 <b>USER SUBMISSIONS</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "📍 Review + 📧 Gmail\n"
        "💰 Rate • 👤 TG ID • Status\n\n"
    )
    body = "\n\n━━━━━━━━━━━━━━━━━━\n\n".join(item[1] for item in chunk)
    if not body:
        body = "<i>No submissions found.</i>"

    msg = header + body + f"\n\n📄 Page <b>{page+1}/{total_pages}</b> • Total: <b>{len(records)}</b>"

    markup = InlineKeyboardMarkup(row_width=2)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ ᴘʀᴇᴠ", callback_data=f"admin_all_submissions_p{page-1}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("ɴᴇxᴛ ➡️", callback_data=f"admin_all_submissions_p{page+1}"))
    if nav:
        markup.row(*nav)
    markup.add(InlineKeyboardButton("🔙 ᴅᴀѕʜʙᴏᴀʀᴅ", callback_data="admin_panel_back"))

    bot.edit_message_text(msg, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML", reply_markup=markup)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "admin_review_panel")
def admin_review_panel(call):
    if call.from_user.id not in ADMIN_IDS: return
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("📋 Live Review Tasks", callback_data="admin_live_tasks"))
    markup.row(InlineKeyboardButton("✅ Single Validation", callback_data="admin_val_single_rev"),
               InlineKeyboardButton("📦 Bulk Validation", callback_data="admin_val_bulk_rev"))
    markup.row(InlineKeyboardButton("✔️ Approved Reviews", callback_data="admin_approved_revs"),
               InlineKeyboardButton("❌ Rejected Reviews", callback_data="admin_rejected_revs"))
    markup.row(InlineKeyboardButton("➕ Add Single Task", callback_data="add_single_task"),
               InlineKeyboardButton("📦 Add Bulk Tasks", callback_data="add_bulk_tasks"))
    markup.row(InlineKeyboardButton("🗑 Clear All Reviews", callback_data="admin_clear_tasks_confirm"))
    markup.row(InlineKeyboardButton("🔙 Back to Dashboard", callback_data="admin_panel_back"))
    bot.edit_message_text("📍 *Review Panel*\n\nManage your Google Maps review tasks here:", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "admin_gmail_panel")
def admin_gmail_panel(call):
    if call.from_user.id not in ADMIN_IDS: return
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("📋 Live Gmail Tasks", callback_data="admin_live_gmails"))
    markup.row(InlineKeyboardButton("✅ Single Validation", callback_data="admin_val_single_gm"),
               InlineKeyboardButton("📦 Bulk Validation", callback_data="admin_val_bulk_gm"))
    markup.row(InlineKeyboardButton("✔️ Approved Gmails", callback_data="admin_approved_gms"),
               InlineKeyboardButton("❌ Rejected Gmails", callback_data="admin_rejected_gms"))
    markup.row(InlineKeyboardButton("➕ Add Single Gmail", callback_data="add_single_gmail"),
               InlineKeyboardButton("📦 Add Bulk Gmails", callback_data="add_bulk_gmails"))
    markup.row(InlineKeyboardButton("🗑 Clear All Gmails", callback_data="admin_clear_gmails_confirm"))
    markup.row(InlineKeyboardButton("🔙 Back to Dashboard", callback_data="admin_panel_back"))
    bot.edit_message_text("📧 *Gmail Panel*\n\nManage your Gmail accounts tasks here:", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "admin_set_gmail_reward")
def handle_set_gmail_reward(call):
    if call.from_user.id not in ADMIN_IDS: return
    current_reward = bot_config.get('default_gmail_reward', 8.0)
    msg = bot.send_message(call.message.chat.id, f"Current Default Gmail Reward: ₹{current_reward}\n\nPlease enter the new default reward for Gmail tasks (e.g. 5):", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_set_gmail_reward)
    bot.answer_callback_query(call.id)

def process_set_gmail_reward(message):
    if is_cancel(message): return
    try:
        new_reward = float(message.text)
        bot_config["default_gmail_reward"] = new_reward
        for task in gmail_tasks:
            if isinstance(task, dict):
                task["reward"] = new_reward
        save_data()
        bot.send_message(message.chat.id, f"✅ Default Gmail reward set to ₹{new_reward}.\n🔄 All existing Gmail tasks have been automatically updated to the new price!", reply_markup=restore_menu(message.from_user.id))
    except ValueError:
        bot.send_message(message.chat.id, "Invalid number.", reply_markup=restore_menu(message.from_user.id))

@bot.callback_query_handler(func=lambda call: call.data == "admin_set_bulk_rev_reward")
def handle_set_bulk_rev_reward(call):
    if call.from_user.id not in ADMIN_IDS: return
    current_reward = bot_config.get('bulk_review_per_task', 10.0)
    msg = bot.send_message(call.message.chat.id, f"Current Bulk Review Reward per task: ₹{current_reward}\n\nPlease enter the new reward per task for Bulk Review tasks (e.g. 12):", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_set_bulk_rev_reward)
    bot.answer_callback_query(call.id)

def process_set_bulk_rev_reward(message):
    if is_cancel(message): return
    try:
        new_reward = float(message.text)
        bot_config["bulk_review_per_task"] = new_reward
        save_data()
        bot.send_message(message.chat.id, f"✅ Bulk Review reward set to ₹{new_reward} per task.", reply_markup=restore_menu(message.from_user.id))
    except ValueError:
        bot.send_message(message.chat.id, "Invalid number.", reply_markup=restore_menu(message.from_user.id))

@bot.callback_query_handler(func=lambda call: call.data == "admin_set_bulk_gm_reward")
def handle_set_bulk_gm_reward(call):
    if call.from_user.id not in ADMIN_IDS: return
    current_reward = bot_config.get('bulk_gmail_per_task', 10.0)
    msg = bot.send_message(call.message.chat.id, f"Current Bulk Gmail Reward per task: ₹{current_reward}\n\nPlease enter the new reward per task for Bulk Gmail tasks (e.g. 12):", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_set_bulk_gm_reward)
    bot.answer_callback_query(call.id)

def process_set_bulk_gm_reward(message):
    if is_cancel(message): return
    try:
        new_reward = float(message.text)
        bot_config["bulk_gmail_per_task"] = new_reward
        save_data()
        bot.send_message(message.chat.id, f"✅ Bulk Gmail reward set to ₹{new_reward} per task.", reply_markup=restore_menu(message.from_user.id))
    except ValueError:
        bot.send_message(message.chat.id, "Invalid number.", reply_markup=restore_menu(message.from_user.id))

@bot.callback_query_handler(func=lambda call: call.data == "admin_bot_settings")
def handle_bot_settings(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("💰 Set Review Reward", callback_data="admin_set_default_reward"),
               InlineKeyboardButton("📧 Set Gmail Reward", callback_data="admin_set_gmail_reward"))
    markup.add(InlineKeyboardButton("📦 Set Bulk Review Reward", callback_data="admin_set_bulk_rev_reward"),
               InlineKeyboardButton("📦 Set Bulk Gmail Reward", callback_data="admin_set_bulk_gm_reward"))
    markup.add(InlineKeyboardButton("💳 Manage Payments", callback_data="admin_payments"))
    markup.add(InlineKeyboardButton("📢 Auto Broadcast", callback_data="admin_auto_broadcast"))
    markup.add(InlineKeyboardButton("👨‍💻 Manage Admins", callback_data="admin_manage_admins"),
               InlineKeyboardButton("🔍 User Lookup", callback_data="admin_user_lookup"))
    markup.add(InlineKeyboardButton("📋 All Task Details", callback_data="admin_all_tasks_details"),
               InlineKeyboardButton("🔄 Revalidate Submissions", callback_data="admin_revalidate"))
    markup.add(InlineKeyboardButton("🔙 Back to Dashboard", callback_data="admin_panel_back"))
    bot.edit_message_text("⚙️ *Bot Settings*\n\n_Manage your bot's core configuration, tasks, and data below:_", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "admin_security_settings")
def admin_security_settings_handler(call):
    if call.from_user.id not in ADMIN_IDS: return
    sec = bot_config.setdefault("security_settings", {
        "duplicate_detection": True,
        "fake_detection": True,
        "spam_protection": True,
        "cooldown_protection": True,
        "mass_submit_protection": True,
        "smart_validation": True
    })
    markup = InlineKeyboardMarkup(row_width=1)
    def get_toggle_btn(name, label):
        val = sec.get(name, True)
        symbol = "✅" if val else "❌"
        return InlineKeyboardButton(f"{symbol} {label}", callback_data=f"toggle_sec_{name}")
    markup.add(
        get_toggle_btn("duplicate_detection", "Duplicate Screenshot Detection"),
        get_toggle_btn("fake_detection", "Fake Screenshot Detection"),
        get_toggle_btn("spam_protection", "Spam Protection"),
        get_toggle_btn("cooldown_protection", "Cooldown Protection"),
        get_toggle_btn("mass_submit_protection", "Mass Submit Protection"),
        get_toggle_btn("smart_validation", "Smart Validation"),
        InlineKeyboardButton("🔙 Back to Settings", callback_data="admin_bot_settings")
    )
    bot.edit_message_text("🛡️ *Security Settings*\n\nToggle features on/off:", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("toggle_sec_"))
def toggle_sec_handler(call):
    if call.from_user.id not in ADMIN_IDS: return
    name = "_".join(call.data.split("_")[2:])
    sec = bot_config.setdefault("security_settings", {
        "duplicate_detection": True,
        "fake_detection": True,
        "spam_protection": True,
        "cooldown_protection": True,
        "mass_submit_protection": True,
        "smart_validation": True
    })
    sec[name] = not sec.get(name, True)
    save_data()
    log_admin_action("toggle_security_setting", {"setting": name, "value": sec[name]})
    bot.answer_callback_query(call.id, f"Security setting {name} updated.")
    admin_security_settings_handler(call)

@bot.callback_query_handler(func=lambda call: call.data in ["admin_approved_revs", "admin_rejected_revs", "admin_approved_gms", "admin_rejected_gms"])
def list_processed_reviews_gmails(call):
    if call.from_user.id not in ADMIN_IDS: return
    action = call.data
    is_rev = "rev" in action
    is_app = "approved" in action
    title = ("Approved" if is_app else "Rejected") + (" Reviews" if is_rev else " Gmails")
    msg = f"📋 <b>{title}</b>\n\n"
    count = 0
    for uid, udata in user_data.items():
        completed_key = "completed_tasks" if is_rev else "completed_gmails"
        rejected_key = "rejected_tasks" if is_rev else "rejected_gmails"

        first_name = udata.get('first_name', 'User')
        first_name_safe = html.escape(first_name)

        if is_app:
            lst = udata.get(completed_key, [])
            if lst:
                display_ids = [int(idx) + 1 for idx in sorted(list(set(lst)))]
                ids_str = ", ".join(map(str, display_ids[:10]))
                if len(display_ids) > 10:
                    ids_str += ", ..."
                msg += f"👤 <b>User:</b> {first_name_safe} (<code>{uid}</code>)\n<b>Task IDs:</b> <code>[{ids_str}]</code>\n\n"
                count += 1
        else:
            lst = udata.get(rejected_key, [])
            if lst:
                display_ids = [int(idx) + 1 for idx in sorted(list(set(lst)))]
                ids_str = ", ".join(map(str, display_ids[:10]))
                if len(display_ids) > 10:
                    ids_str += ", ..."
                msg += f"👤 <b>User:</b> {first_name_safe} (<code>{uid}</code>)\n<b>Rejected IDs:</b> <code>[{ids_str}]</code>\n\n"
                count += 1
    if count == 0:
        msg += "No records found."
    markup = InlineKeyboardMarkup()
    back_target = "admin_review_panel" if is_rev else "admin_gmail_panel"
    markup.add(InlineKeyboardButton("🔙 Back", callback_data=back_target))
    bot.edit_message_text(msg, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "admin_clear_gmails_confirm")
def handle_clear_gmails_confirm(call):
    if call.from_user.id not in ADMIN_IDS: return
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("⚠️ YES, DELETE ALL GMAILS", callback_data="admin_clear_gmails_exec"))
    markup.add(InlineKeyboardButton("❌ NO, CANCEL", callback_data="admin_gmail_panel"))
    bot.edit_message_text("⚠️ *WARNING!* ⚠️\n\nAre you sure you want to delete ALL Gmail tasks and their proofs permanently?\n\n- All Gmail tasks will be permanently removed.\n- All pending and completed lists will be cleared.\n- User balances and referrals are safe.\n\nThis action cannot be undone!", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "admin_clear_gmails_exec")
def handle_clear_gmails_exec(call):
    if call.from_user.id not in ADMIN_IDS: return
    gmail_tasks.clear()
    for uid, udata in list(user_data.items()):
        udata["completed_gmails"] = []
        udata["pending_gmails"] = []
        udata["rejected_gmails"] = []
        udata["gmail_proofs"] = {}
        udata["gmail_creds"] = {}
    save_data()
    log_admin_action("clear_all_gmails", {})
    bot.answer_callback_query(call.id, "All Gmail tasks and proofs have been permanently deleted!", show_alert=True)
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Back to Gmail Panel", callback_data="admin_gmail_panel"))
    bot.edit_message_text("✅ All Gmail tasks and proofs have been successfully cleared.", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "admin_advanced_settings")
def handle_advanced_settings(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("🔄 Revalidate Submissions", callback_data="admin_revalidate"))
    markup.add(InlineKeyboardButton("📋 All Tasks Details", callback_data="admin_all_tasks_details"))
    markup.add(InlineKeyboardButton("🗑️ Clear All Tasks & Proofs", callback_data="admin_clear_tasks_confirm"))
    markup.add(InlineKeyboardButton("🔙 Back to Settings", callback_data="admin_bot_settings"))
    bot.edit_message_text("🛡️ *Security & Task Management*", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "admin_revalidate")
def handle_revalidate(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("📋 Google Map Reviews", callback_data="admin_revalidate_rev"))
    markup.add(InlineKeyboardButton("📧 Gmail Tasks", callback_data="admin_revalidate_gm"))
    markup.add(InlineKeyboardButton("🔙 Back", callback_data="admin_advanced_settings"))
    bot.edit_message_text("🔄 Select task category to revalidate:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "admin_revalidate_rev")
def handle_revalidate_rev(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    task_counts = {}
    for uid, udata in list(user_data.items()):
        pending_list = udata.get("pending_tasks", [])
        for task_idx_str, file_id in udata.get("proofs", {}).items():
            task_idx = int(task_idx_str)
            if file_id and task_idx not in pending_list:
                task_counts[task_idx] = task_counts.get(task_idx, 0) + 1

    if not task_counts:
        bot.answer_callback_query(call.id, "No processed tasks with proofs found.", show_alert=True)
        return

    markup = InlineKeyboardMarkup(row_width=1)
    for task_idx, count in sorted(task_counts.items()):
        task = available_tasks[task_idx] if task_idx < len(available_tasks) else {}
        created_ts = task.get("created_at", 0) if isinstance(task, dict) else 0
        date_str = f" [{time.strftime('%d %b', time.localtime(created_ts))}]" if created_ts else ""
        btn = InlineKeyboardButton(f"Task ID {task_idx + 1}{date_str} ({count} processed)", callback_data=f"reval_task_{task_idx}")
        markup.add(btn)

    markup.add(InlineKeyboardButton("🔙 Back", callback_data="admin_revalidate"))
    bot.edit_message_text("Select a task to revalidate its processed (approved/rejected) submissions:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "admin_revalidate_gm")
def handle_revalidate_gm(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    task_counts = {}
    for uid, udata in list(user_data.items()):
        pending_list = udata.get("pending_gmails", [])
        for task_idx_str, file_id in udata.get("gmail_proofs", {}).items():
            task_idx = int(task_idx_str)
            if file_id and task_idx not in pending_list:
                task_counts[task_idx] = task_counts.get(task_idx, 0) + 1

    if not task_counts:
        bot.answer_callback_query(call.id, "No processed Gmail tasks with proofs found.", show_alert=True)
        return

    markup = InlineKeyboardMarkup(row_width=1)
    for task_idx, count in sorted(task_counts.items()):
        task = gmail_tasks[task_idx] if task_idx < len(gmail_tasks) else {}
        created_ts = task.get("created_at", 0) if isinstance(task, dict) else 0
        date_str = f" [{time.strftime('%d %b', time.localtime(created_ts))}]" if created_ts else ""
        btn = InlineKeyboardButton(f"Gmail Task ID {task_idx + 1}{date_str} ({count} processed)", callback_data=f"reval_gmtask_{task_idx}")
        markup.add(btn)

    markup.add(InlineKeyboardButton("🔙 Back", callback_data="admin_revalidate"))
    bot.edit_message_text("Select a Gmail task to revalidate its processed submissions:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("reval_gmtask_"))
def handle_revalidate_gmtask(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    task_idx = int(call.data.split("_")[2])
    bot.answer_callback_query(call.id, "Fetching processed submissions...")

    sent_count = 0
    for uid, udata in list(user_data.items()):
        pending_list = udata.get("pending_gmails", [])
        completed_list = udata.get("completed_gmails", [])
        file_id = udata.get("gmail_proofs", {}).get(str(task_idx))

        if not file_id or task_idx in pending_list:
            continue

        is_approved = task_idx in completed_list

        markup = InlineKeyboardMarkup()
        if is_approved:
            markup.add(InlineKeyboardButton("↩️ Undo & Reject", callback_data=f"undo_app_gm_{uid}_{task_idx}"))
            status_text = "Approved ✅"
        else:
            markup.add(InlineKeyboardButton("↩️ Undo & Approve", callback_data=f"undo_rej_gm_{uid}_{task_idx}"))
            status_text = "Rejected ❌"

        if task_idx < len(gmail_tasks):
            task = gmail_tasks[task_idx]
            task_desc = task.get("description", "No description") if isinstance(task, dict) else task
            reward = task.get("reward", bot_config.get("default_gmail_reward", 8.0)) if isinstance(task, dict) else bot_config.get("default_gmail_reward", 8.0)
            created_ts = task.get("created_at", 0) if isinstance(task, dict) else 0
            date_str = time.strftime("%d %b %Y, %I:%M %p", time.localtime(created_ts)) if created_ts else "Unknown"
        else:
            task_desc = "Unknown Gmail Task"
            reward = 0
            date_str = "Unknown"

        first_name = udata.get("first_name", "User")
        first_name_safe = html.escape(first_name)
        username = udata.get("username")
        rejections = udata.get("rejection_count", 0)
        creds = udata.get("gmail_creds", {}).get(str(task_idx), "Not provided")

        user_line = f"User: {first_name_safe} (ID: {uid})"
        if username:
            user_line += f" (@{username})"

        caption = (
            f"<b>Gmail Task Revalidation</b>\n\n"
            f"{user_line}\n"
            f"Status: {status_text}\n"
            f"Reward: ₹{reward}\n"
            f"Task Added: {date_str}\n\n"
            f"<b>Credentials:</b>\n<code>{html.escape(creds)}</code>\n\n"
            f"User Rejections: {rejections}"
        )

        try:
            bot.send_photo(call.message.chat.id, file_id, caption=caption, reply_markup=markup, parse_mode="HTML")
            sent_count += 1
            time.sleep(0.1)
        except Exception:
            pass

    bot.send_message(call.message.chat.id, f"✅ All {sent_count} Gmail tasks details have been sent.", reply_markup=restore_menu(call.from_user.id))

@bot.callback_query_handler(func=lambda call: call.data == "admin_all_tasks_details")
def handle_all_tasks_details(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("📍 Review Task Details", callback_data="admin_show_task_details_rev"),
        InlineKeyboardButton("📧 Gmail Task Details", callback_data="admin_show_task_details_gm"),
        InlineKeyboardButton("🔙 Back to Settings", callback_data="admin_bot_settings")
    )
    bot.edit_message_text("📋 *All Task Details*\n\nSelect the task type you want to view:", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "admin_show_task_details_rev")
def handle_show_task_details_rev(call):
    show_all_tasks_details(call, "rev")

@bot.callback_query_handler(func=lambda call: call.data == "admin_show_task_details_gm")
def handle_show_task_details_gm(call):
    show_all_tasks_details(call, "gm")

def show_all_tasks_details(call, task_type):
    if call.from_user.id not in ADMIN_IDS:
        return

    pool = available_tasks if task_type == "rev" else gmail_tasks
    task_name = "Review" if task_type == "rev" else "Gmail"

    if not pool:
        bot.answer_callback_query(call.id, f"No {task_name} tasks found.", show_alert=True)
        return

    bot.answer_callback_query(call.id, f"Fetching {task_name} task details...")
    bot.send_message(call.message.chat.id, f"⏳ *Gathering all {task_name} tasks details, please wait...*", parse_mode="Markdown")

    sorted_tasks = sorted(
        enumerate(pool),
        key=lambda x: x[1].get("created_at", 0) if isinstance(x[1], dict) else 0
    )

    for idx, task in sorted_tasks:
        is_dict = isinstance(task, dict)
        if is_dict and task.get("deleted"):
            continue

        desc = task.get("description", "No description") if is_dict else task
        reward = task.get("reward", bot_config.get("default_reward", 8.0) if task_type == "rev" else bot_config.get("default_gmail_reward", 8.0)) if is_dict else (bot_config.get("default_reward", 8.0) if task_type == "rev" else bot_config.get("default_gmail_reward", 8.0))
        created_ts = task.get("created_at", 0) if is_dict else 0
        status = task.get("status", "available") if is_dict else "available"
        active = task.get("active", True) if is_dict else True
        task_rejections = task.get("rejection_count", 0) if is_dict else 0

        if created_ts:
            date_str = time.strftime("%d %B %Y, %I:%M %p", time.localtime(created_ts))
        else:
            date_str = "Unknown"

        status_display = "🟢 Live/Available" if active and status == "available" else f"🔴 {status.capitalize()}"

        completed_by = []
        completed_key = "completed_tasks" if task_type == "rev" else "completed_gmails"
        proofs_key = "proofs" if task_type == "rev" else "gmail_proofs"
        creds_key = "gmails" if task_type == "rev" else "gmail_creds"

        for uid, udata in list(user_data.items()):
            if idx in udata.get(completed_key, []):
                completed_by.append({
                    "uid": uid,
                    "first_name": udata.get("first_name", "User"),
                    "username": udata.get("username"),
                    "proof": udata.get(proofs_key, {}).get(str(idx)),
                    "gmail": udata.get(creds_key, {}).get(str(idx), "Not provided")
                })

        msg_text = (
            f"📋 <b>{task_name} Task ID: {idx + 1}</b>\n"
            f"📅 <b>Added:</b> <code>{date_str}</code>\n"
            f"💰 <b>Reward:</b> <code>₹{reward}</code>\n"
            f"📌 <b>Status:</b> {status_display}\n"
            f"⚠️ <b>Total Times Rejected:</b> <code>{task_rejections}</code>\n\n"
            f"📝 <b>Description:</b>\n{desc}\n\n"
        )

        if not completed_by:
            msg_text += "👥 <b>Completed By:</b>  No one completed this yet. 👻"
            bot.send_message(call.message.chat.id, msg_text, parse_mode="HTML")
        else:
            msg_text += "👥 <b>Completed By:</b>\n"
            for user_info in completed_by:
                first_name_safe = html.escape(user_info["first_name"])
                username = user_info["username"]
                username_display = f" (@{html.escape(username)})" if username else " (No username)"

                user_msg = (
                    f"👤 {first_name_safe}{username_display}\n"
                    f"🆔 User ID: <code>{user_info['uid']}</code>\n"
                    f"📧 Details: {html.escape(user_info['gmail'])}\n"
                )

                if user_info["proof"]:
                    send_proof(call.message.chat.id, user_info["proof"], msg_text + user_msg)
                else:
                    bot.send_message(
                        call.message.chat.id,
                        msg_text + user_msg,
                        parse_mode="HTML"
                    )
        time.sleep(0.1)

    bot.send_message(call.message.chat.id, f"✅ All {task_name} tasks details have been sent.", reply_markup=restore_menu(call.from_user.id))

@bot.callback_query_handler(func=lambda call: call.data.startswith("reval_task_"))
def handle_revalidate_task(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    task_idx = int(call.data.split("_")[2])
    bot.answer_callback_query(call.id, "Fetching processed submissions...")

    sent_count = 0
    for uid, udata in list(user_data.items()):
        pending_list = udata.get("pending_tasks", [])
        completed_list = udata.get("completed_tasks", [])
        file_id = udata.get("proofs", {}).get(str(task_idx))

        if not file_id or task_idx in pending_list:
            continue

        is_approved = task_idx in completed_list

        markup = InlineKeyboardMarkup()
        if is_approved:
            markup.add(InlineKeyboardButton("↩️ Undo & Reject", callback_data=f"undo_app_{uid}_{task_idx}"))
            status_text = "Approved ✅"
        else:
            markup.add(InlineKeyboardButton("↩️ Undo & Approve", callback_data=f"undo_rej_{uid}_{task_idx}"))
            status_text = "Rejected ❌"

        if task_idx < len(available_tasks):
            task = available_tasks[task_idx]
            task_desc = task.get("description", "No description") if isinstance(task, dict) else task
            reward = task.get("reward", bot_config.get("default_reward", 8.0)) if isinstance(task, dict) else bot_config.get("default_reward", 8.0)
            created_ts = task.get("created_at", 0) if isinstance(task, dict) else 0
            date_str = time.strftime("%d %b %Y, %I:%M %p", time.localtime(created_ts)) if created_ts else "Unknown"
        else:
            task_desc = "Unknown Task"
            reward = 0
            date_str = "Unknown"

        first_name = udata.get("first_name", "User")
        first_name_safe = html.escape(first_name)
        username = udata.get("username")
        rejections = udata.get("rejection_count", 0)
        gmail = udata.get("gmails", {}).get(str(task_idx), "Not provided")

        user_line = f"User: {first_name_safe} (ID: {uid})"
        if username:
            user_line += f" (@{username})"

        caption = (
            f"<b>Task Revalidation ({status_text})</b>\n"
            f"{user_line}\n"
            f"Task ID: {task_idx + 1}\n"
            f"Gmail: {html.escape(gmail)}\n"
            f"User's Rejections: {rejections}\n\n"
            f"<b>Task Added:</b> {date_str}\n"
            f"<b>Task Details:</b>\n{task_desc}\n\n"
            f"<b>Reward:</b> ₹{reward}"
        )

        send_proof(call.message.chat.id, file_id, caption, markup)
        sent_count += 1
        time.sleep(0.1)

    bot.send_message(call.message.chat.id, f"✅ Sent {sent_count} processed submissions for Task ID {task_idx + 1}.")

@bot.callback_query_handler(func=lambda call: call.data == "admin_set_default_reward")
def handle_set_default_reward(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    current_reward = bot_config.get('default_reward', 8.0)
    msg = bot.send_message(call.message.chat.id, f"Current Default Reward: ₹{current_reward}\n\nPlease enter the new default reward for tasks (e.g. 10):", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_set_default_reward)
    bot.answer_callback_query(call.id)

def process_set_default_reward(message):
    if is_cancel(message): return
    try:
        new_reward = float(message.text)
        bot_config["default_reward"] = new_reward
        for task in available_tasks:
            if isinstance(task, dict):
                task["reward"] = new_reward
        save_data()
        bot.send_message(message.chat.id, f"✅ Default task reward has been set to ₹{new_reward}.\n🔄 All existing tasks have been automatically updated to the new price!", reply_markup=restore_menu(message.from_user.id))
    except ValueError:
        bot.send_message(message.chat.id, "Invalid number. Please try again.", reply_markup=restore_menu(message.from_user.id))

@bot.callback_query_handler(func=lambda call: call.data == "admin_clear_tasks_confirm")
def handle_clear_tasks_confirm(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("⚠️ YES, DELETE ALL TASKS", callback_data="admin_clear_tasks_exec"))
    markup.add(InlineKeyboardButton("❌ NO, CANCEL", callback_data="admin_bot_settings"))
    bot.edit_message_text("⚠️ *WARNING!* ⚠️\n\nAre you sure you want to delete ALL tasks and their proofs permanently?\n\n- All tasks will be permanently removed (no archive).\n- All pending and completed task lists will be cleared.\n- User balances, referral counts, and total completed counts will **NOT** be affected.\n\nThis action cannot be undone!", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "admin_clear_tasks_exec")
def handle_clear_tasks_exec(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    available_tasks.clear()

    for uid, udata in list(user_data.items()):
        udata["completed_tasks"] = []
        udata["pending_tasks"] = []
        udata["rejected_tasks"] = []
        udata["proofs"] = {}
        udata["gmails"] = {}

    save_data()
    bot.answer_callback_query(call.id, "All tasks and proofs have been permanently deleted!", show_alert=True)

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Back to Settings", callback_data="admin_bot_settings"))
    bot.edit_message_text("✅ All tasks and proofs have been successfully permanently cleared. User balances and overall history are safe.", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "admin_manage_admins")
def handle_manage_admins(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("➕ Add Admin", callback_data="admin_add_admin"))
    markup.add(InlineKeyboardButton("➖ Remove Admin", callback_data="admin_remove_admin"))
    markup.add(InlineKeyboardButton("🔙 Back to Dashboard", callback_data="admin_panel_back"))

    admin_list = "\n".join([f"`{aid}`" for aid in ADMIN_IDS])
    bot.edit_message_text(f"👨‍💻 *Admin Management*\n\nCurrent Admins:\n{admin_list}", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "admin_add_admin")
def handle_add_admin(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    msg = bot.send_message(call.message.chat.id, "Please enter the User ID you want to add as an admin:", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_add_admin)
    bot.answer_callback_query(call.id)

def process_add_admin(message):
    if is_cancel(message): return
    try:
        new_admin = int(message.text)
        if new_admin in ADMIN_IDS:
            bot.send_message(message.chat.id, "User is already an admin.", reply_markup=restore_menu(message.from_user.id))
            return
        ADMIN_IDS.append(new_admin)
        save_data()
        bot.send_message(message.chat.id, f"✅ User {new_admin} has been added as an admin.", reply_markup=restore_menu(message.from_user.id))
        try:
            bot.send_message(new_admin, "🎉 You have been promoted to an Admin! Send /start to refresh your menu.", reply_markup=restore_menu(new_admin))
        except Exception:
            pass
    except ValueError:
        bot.send_message(message.chat.id, "Invalid User ID. Must be a number.", reply_markup=restore_menu(message.from_user.id))

@bot.callback_query_handler(func=lambda call: call.data == "admin_remove_admin")
def handle_remove_admin(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    msg = bot.send_message(call.message.chat.id, "Please enter the User ID you want to remove from admins:", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_remove_admin, call.from_user.id)
    bot.answer_callback_query(call.id)

def process_remove_admin(message, requesting_admin_id):
    if is_cancel(message): return
    try:
        remove_admin = int(message.text)
        if remove_admin == ADMIN_IDS[0]: # Main owner
            bot.send_message(message.chat.id, "❌ Cannot remove the main bot owner.", reply_markup=restore_menu(message.from_user.id))
            return
        if remove_admin == requesting_admin_id:
            bot.send_message(message.chat.id, "❌ You cannot remove yourself.", reply_markup=restore_menu(message.from_user.id))
            return
        if remove_admin not in ADMIN_IDS:
            bot.send_message(message.chat.id, "User is not an admin.", reply_markup=restore_menu(message.from_user.id))
            return
        ADMIN_IDS.remove(remove_admin)
        save_data()
        bot.send_message(message.chat.id, f"✅ User {remove_admin} has been removed from admins.", reply_markup=restore_menu(message.from_user.id))
        try:
            bot.send_message(remove_admin, "Your admin privileges have been revoked. Send /start to refresh your menu.", reply_markup=restore_menu(remove_admin))
        except Exception:
            pass
    except ValueError:
        bot.send_message(message.chat.id, "Invalid User ID. Must be a number.", reply_markup=restore_menu(message.from_user.id))

@bot.callback_query_handler(func=lambda call: call.data == "admin_user_lookup")
def handle_user_lookup(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    msg = bot.send_message(call.message.chat.id, "Please enter the User ID or Username to look up:", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_user_lookup)
    bot.answer_callback_query(call.id)

def process_user_lookup(message):
    if is_cancel(message): return
    lookup_query = message.text.strip()
    user_id = None
    udata = None

    try:
        user_id = int(lookup_query)
        udata = user_data.get(user_id)
    except ValueError:
        search_query = lookup_query.split('/')[-1].lstrip('@').lower()
        for uid, data in list(user_data.items()):
            saved_username = data.get('username')
            if saved_username and saved_username.lower() == search_query:
                user_id = uid
                udata = data
                break
        if not udata:
            for uid, data in list(user_data.items()):
                saved_firstname = data.get('first_name', '')
                if search_query in saved_firstname.lower():
                    user_id = uid
                    udata = data
                    break

    if not udata:
        bot.send_message(message.chat.id, f"No data found for query: {lookup_query}", reply_markup=restore_menu(message.from_user.id))
        return

    first_name = udata.get("first_name", "Unknown")
    first_name_safe = html.escape(first_name)
    username = udata.get("username")
    username_text = f"@{username}" if username else "None"
    balance = udata.get("balance", 0)
    tasks_completed_count = udata.get("tasks_completed", 0)

    completed_reviews = udata.get("completed_tasks", [])
    completed_gmails = udata.get("completed_gmails", [])
    pending_reviews = udata.get("pending_tasks", [])
    pending_gmails = udata.get("pending_gmails", [])

    # Calculate rewards for pending tasks
    def get_review_reward(task_idx):
        if task_idx < len(available_tasks):
            t = available_tasks[task_idx]
            if isinstance(t, dict):
                return t.get("reward", bot_config.get("default_reward", 8.0))
        return bot_config.get("default_reward", 8.0)

    def get_gmail_reward(task_idx):
        if task_idx < len(gmail_tasks):
            t = gmail_tasks[task_idx]
            if isinstance(t, dict):
                return t.get("reward", bot_config.get("default_gmail_reward", 8.0))
        return bot_config.get("default_gmail_reward", 8.0)

    pending_review_rewards = sum(get_review_reward(idx) for idx in pending_reviews)
    pending_gmail_rewards = sum(get_gmail_reward(idx) for idx in pending_gmails)
    total_pending_rewards = pending_review_rewards + pending_gmail_rewards

    rejections = udata.get("rejection_count", 0)
    invalid_packs = udata.get("invalid_packs_count", 0)
    referrals = udata.get("referrals", 0)
    level = get_user_level(tasks_completed_count)

    ban_status = "Not Banned"
    if udata.get("banned"):
        ban_status = "Permanently Banned"
    elif udata.get("banned_until", 0) > time.time():
        unban_ts = udata.get("banned_until", 0)
        ban_status = f"Temporarily Banned until {time.ctime(unban_ts)}"

    pending_with = udata.get("pending_withdrawals", [])
    total_with = udata.get("total_withdrawn", 0)
    with_history = f"Total Withdrawn: ₹{int(total_with)}\nPending Withdrawals Count: {len(pending_with)}"

    info_text = (
        f"🔍 <b>User Info for {user_id}</b>\n\n"
        f"👤 <b>Name</b>: {first_name_safe} (Username: {username_text})\n\n"
        f"💰 <b>Wallet Balance</b>: ₹{int(balance)}\n"
        f"👥 <b>Referrals</b>: {referrals}\n\n"
        f"📜 <b>Task Statistics</b>:\n"
        f"✅ Reviews Done: {len(completed_reviews)}\n"
        f"✅ Gmail Done: {len(completed_gmails)}\n"
        f"⏳ Pending Reviews: {len(pending_reviews)} (₹{int(pending_review_rewards)} pending)\n"
        f"⏳ Pending Gmails: {len(pending_gmails)} (₹{int(pending_gmail_rewards)} pending)\n"
        f"💵 Total Pending Payments: ₹{int(total_pending_rewards)}\n"
        f"💸 Total Paid Payments (Withdrawn): ₹{int(total_with)}\n\n"
        f"📋 <b>Task Details</b>:\n"
        f"Completed Review IDs: <code>{[i+1 for i in completed_reviews]}</code>\n"
        f"Completed Gmail IDs: <code>{[i+1 for i in completed_gmails]}</code>\n"
        f"Pending Review IDs: <code>{[i+1 for i in pending_reviews]}</code>\n"
        f"Pending Gmail IDs: <code>{[i+1 for i in pending_gmails]}</code>\n\n"
        f"❌ <b>Invalid Count</b>:\n"
        f"Single Rejections: {rejections}\n"
        f"Invalid Packs: {invalid_packs}\n\n"
        f"💸 <b>Withdrawal History</b>:\n"
        f"{with_history}\n\n"
        f"📊 <b>Current Status</b>:\n"
        f"Level: {level}\n"
        f"Status: {ban_status}"
    )

    markup = InlineKeyboardMarkup()
    if udata.get("banned") or udata.get("banned_until", 0) > time.time():
        markup.add(InlineKeyboardButton("✅ Unban User", callback_data=f"unban_user_{user_id}"))
    else:
        markup.add(InlineKeyboardButton("🚫 Ban User", callback_data=f"lookupban_{user_id}"))

    markup.add(InlineKeyboardButton("🔙 Back to Dashboard", callback_data="admin_panel_back"))

    bot.send_message(message.chat.id, "🔍 Search Results:", reply_markup=restore_menu(message.from_user.id))
    bot.send_message(message.chat.id, info_text, parse_mode="HTML", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("unban_user_"))
def admin_unban_user(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    uid = int(call.data.split("_")[2])
    if uid in user_data:
        user_data[uid]["banned"] = False
        user_data[uid]["banned_until"] = 0
        log_admin_action("unban_user", {"user_id": uid, "reason": "unbanned via lookup dashboard"})
        save_data()
        bot.answer_callback_query(call.id, f"User {uid} unbanned.")

        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("🚫 Ban User", callback_data=f"lookupban_{uid}"))
        markup.add(InlineKeyboardButton("🔙 Back to Dashboard", callback_data="admin_panel_back"))

        try:
            bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
            bot.send_message(call.message.chat.id, f"✅ User {uid} has been successfully unbanned.")
        except Exception:
            pass
        try:
            bot.send_message(uid, "✅ *Good news!* Your ban has been lifted. You can now resume completing tasks and earning rewards.", parse_mode="Markdown")
        except Exception:
            pass
    else:
        bot.answer_callback_query(call.id, "User not found.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("lookupban_"))
def admin_lookup_ban_menu(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    uid = int(call.data.split("_")[1])
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("⏳ Temporary Ban", callback_data=f"lookuptempban_{uid}"))
    markup.add(InlineKeyboardButton("⛔ Permanent Ban", callback_data=f"lookuppermban_{uid}"))
    markup.add(InlineKeyboardButton("🔙 Cancel", callback_data=f"lookupcancelban_{uid}"))
    bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("lookupcancelban_"))
def admin_lookup_cancel_ban(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    uid = int(call.data.split("_")[1])
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🚫 Ban User", callback_data=f"lookupban_{uid}"))
    markup.add(InlineKeyboardButton("🔙 Back to Dashboard", callback_data="admin_panel_back"))
    bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("lookuppermban_"))
def admin_lookup_perm_ban(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    uid = int(call.data.split("_")[1])
    if uid not in user_data:
        user_data[uid] = {"balance": 0, "tasks_completed": 0, "referrals": 0, "completed_tasks": [], "pending_tasks": []}

    user_data[uid]["banned"] = True
    log_admin_action("permanent_ban", {"user_id": uid, "reason": "banned via lookup dashboard"})
    save_data()

    try:
        bot.send_message(uid, "🚫 You have been permanently banned from using this bot.")
    except Exception:
        pass

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ Unban User", callback_data=f"unban_user_{uid}"))
    markup.add(InlineKeyboardButton("🔙 Back to Dashboard", callback_data="admin_panel_back"))

    try:
        bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)
        bot.answer_callback_query(call.id, "User permanently banned.")
        bot.send_message(call.message.chat.id, f"⛔ User {uid} has been permanently banned.")
    except Exception:
        pass

@bot.callback_query_handler(func=lambda call: call.data.startswith("lookuptempban_"))
def admin_lookup_temp_ban(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    uid = int(call.data.split("_")[1])
    msg = bot.send_message(call.message.chat.id, f"How many days do you want to ban user {uid} for?", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_lookup_temp_ban, uid, call.message)
    bot.answer_callback_query(call.id, "Awaiting days...")

def process_lookup_temp_ban(message, uid, original_message):
    if is_cancel(message): return
    try:
        days = int(message.text)
    except ValueError:
        bot.send_message(message.chat.id, "Invalid number of days. Ban cancelled.", reply_markup=restore_menu(message.from_user.id))
        return

    if uid not in user_data:
        user_data[uid] = {"balance": 0, "tasks_completed": 0, "referrals": 0, "completed_tasks": [], "pending_tasks": []}

    unban_time = time.time() + (days * 86400)
    user_data[uid]["banned_until"] = unban_time
    log_admin_action("temporary_ban", {"user_id": uid, "days": days, "reason": "banned via lookup dashboard"})
    save_data()

    try:
        bot.send_message(uid, f"🚫 You have been temporarily banned for {days} day(s).")
    except Exception:
        pass

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ Unban User", callback_data=f"unban_user_{uid}"))
    markup.add(InlineKeyboardButton("🔙 Back to Dashboard", callback_data="admin_panel_back"))

    try:
        bot.edit_message_reply_markup(chat_id=original_message.chat.id, message_id=original_message.message_id, reply_markup=markup)
    except Exception:
        pass

    bot.send_message(message.chat.id, f"⏳ User {uid} temporarily banned for {days} day(s).", reply_markup=restore_menu(message.from_user.id))

@bot.callback_query_handler(func=lambda call: call.data == "admin_auto_broadcast")
def handle_auto_broadcast_menu(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    status = "Running" if auto_broadcast_config.get("is_running") else "Stopped"

    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(InlineKeyboardButton("✏️ Set Message", callback_data="ab_set_msg"))
    markup.add(InlineKeyboardButton("⏰ Set Interval (Hours)", callback_data="ab_set_interval"))
    markup.add(InlineKeyboardButton("🔢 Set Count", callback_data="ab_set_count"))
    markup.add(InlineKeyboardButton("📊 Status", callback_data="ab_status"))

    toggle_text = "⏹️ Stop" if auto_broadcast_config.get("is_running") else "▶️ Start"
    markup.add(InlineKeyboardButton(toggle_text, callback_data="ab_toggle"))

    markup.add(InlineKeyboardButton("🔙 Back to Dashboard", callback_data="admin_panel_back"))

    bot.edit_message_text(
        f"⚙️ *Auto Broadcast Settings*\n\nCurrent Status: `{status}`",
        chat_id=call.message.chat.id,
        message_id=call.message.message_id,
        reply_markup=markup,
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: call.data == "ab_set_msg")
def set_ab_message_handler(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    msg = bot.send_message(call.message.chat.id, "Please send the message you want to auto-broadcast. It can be text, photo, video, etc.", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_ab_message)
    bot.answer_callback_query(call.id)

def process_ab_message(message):
    if is_cancel(message): return
    auto_broadcast_config["message_id"] = message.message_id
    auto_broadcast_config["message_chat_id"] = message.chat.id
    auto_broadcast_config["message_json"] = message.json
    save_data()
    bot.send_message(message.chat.id, "✅ Auto-broadcast message has been set.", reply_markup=restore_menu(message.from_user.id))

@bot.callback_query_handler(func=lambda call: call.data == "ab_set_interval")
def set_ab_interval_handler(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    msg = bot.send_message(call.message.chat.id, "Please enter the interval in hours (e.g., `2` for every 2 hours).", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_ab_interval)
    bot.answer_callback_query(call.id)

def process_ab_interval(message):
    if is_cancel(message): return
    try:
        hours = float(message.text)
        if hours <= 0:
            bot.send_message(message.chat.id, "Interval must be a positive number.", reply_markup=restore_menu(message.from_user.id))
            return
        auto_broadcast_config["interval_seconds"] = hours * 3600
        save_data()
        bot.send_message(message.chat.id, f"✅ Auto-broadcast interval set to {hours} hour(s).", reply_markup=restore_menu(message.from_user.id))
    except ValueError:
        bot.send_message(message.chat.id, "Invalid number. Please enter a numerical value for the hours.", reply_markup=restore_menu(message.from_user.id))

@bot.callback_query_handler(func=lambda call: call.data == "ab_set_count")
def set_ab_count_handler(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    msg = bot.send_message(call.message.chat.id, "How many times should the broadcast be sent? Enter a number, or `-1` for infinite.", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_ab_count)
    bot.answer_callback_query(call.id)

def process_ab_count(message):
    if is_cancel(message): return
    try:
        count = int(message.text)
        auto_broadcast_config["total_count"] = count
        auto_broadcast_config["sent_count"] = 0 # Reset sent count when total is changed
        save_data()
        count_text = "infinite times" if count == -1 else f"{count} time(s)"
        bot.send_message(message.chat.id, f"✅ Auto-broadcast will be sent {count_text}.", reply_markup=restore_menu(message.from_user.id))
    except ValueError:
        bot.send_message(message.chat.id, "Invalid number. Please enter an integer.", reply_markup=restore_menu(message.from_user.id))

@bot.callback_query_handler(func=lambda call: call.data == "ab_toggle")
def toggle_ab_handler(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    is_running = auto_broadcast_config.get("is_running", False)

    if not is_running: # Trying to start
        if not auto_broadcast_config.get("message_id"):
            bot.answer_callback_query(call.id, "⚠️ Please set a message first!", show_alert=True)
            return
        auto_broadcast_config["is_running"] = True
        auto_broadcast_config["last_sent_time"] = time.time() # Start timer from now
        bot.answer_callback_query(call.id, "▶️ Auto-broadcast started!")
    else: # Trying to stop
        auto_broadcast_config["is_running"] = False
        bot.answer_callback_query(call.id, "⏹️ Auto-broadcast stopped!")

    save_data()
    handle_auto_broadcast_menu(call) # Refresh menu

@bot.callback_query_handler(func=lambda call: call.data == "ab_status")
def status_ab_handler(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    status = "Running" if auto_broadcast_config.get("is_running") else "Stopped"
    interval_hr = auto_broadcast_config.get("interval_seconds", 3600) / 3600
    total = auto_broadcast_config.get("total_count", -1)
    sent = auto_broadcast_config.get("sent_count", 0)
    last_sent_ts = auto_broadcast_config.get("last_sent_time", 0)

    total_text = "Infinite" if total == -1 else total
    last_sent_text = time.ctime(last_sent_ts) if last_sent_ts > 0 else "Never"

    status_msg = (
        f"📊 *Auto Broadcast Status*\n\n"
        f"**Status**: `{status}`\n"
        f"**Interval**: `{interval_hr}` hours\n"
        f"**Broadcast Count**: `{sent} / {total_text}`\n"
        f"**Last Sent**: `{last_sent_text}`\n"
        f"**Message Set**: `{'Yes' if auto_broadcast_config.get('message_id') else 'No'}`"
    )

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Back to Settings", callback_data="admin_auto_broadcast"))

    bot.answer_callback_query(call.id)
    bot.edit_message_text(status_msg, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_live_tasks"))
def handle_live_tasks(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    parts = call.data.split("_")
    page = 0
    if len(parts) > 4:
        try:
            page = int(parts[4])
        except ValueError:
            page = 0

    live_tasks = []
    for idx, task in enumerate(available_tasks):
        if isinstance(task, dict):
            if task.get("deleted") or task.get("status") == "completed":
                continue
        live_tasks.append((idx, task))

    limit = 15
    total = len(live_tasks)
    start_idx = page * limit
    end_idx = start_idx + limit
    page_tasks = live_tasks[start_idx:end_idx]

    markup = InlineKeyboardMarkup()
    for idx, task in page_tasks:
        if isinstance(task, dict):
            active = task.get("active", True)
            created_ts = task.get("created_at", 0)
            date_str = f" ({time.strftime('%d %b', time.localtime(created_ts))})" if created_ts else ""
        else:
            active = True
            date_str = ""
        status = "🟢" if active else "🔴"
        btn = InlineKeyboardButton(f"{status} Task ID {idx + 1}{date_str}", callback_data=f"manage_task_{idx}_{page}")
        markup.add(btn)

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin_live_tasks_page_{page-1}"))
    if end_idx < total:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin_live_tasks_page_{page+1}"))

    if nav_buttons:
        markup.row(*nav_buttons)

    markup.add(InlineKeyboardButton("🔙 Back to Dashboard", callback_data="admin_panel_back"))

    msg_text = f"📋 *Live Tasks (Page {page+1}/{(total-1)//limit + 1 if total > 0 else 1})*\nSelect a task to manage:\n\n🟢 = Active, 🔴 = Stopped"
    try:
        bot.edit_message_text(msg_text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    except Exception:
        pass

@bot.callback_query_handler(func=lambda call: call.data.startswith("manage_task_"))
def handle_manage_task(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    parts = call.data.split("_")
    idx = int(parts[2])
    page = 0
    if len(parts) > 3:
        try:
            page = int(parts[3])
        except ValueError:
            page = 0

    if idx >= len(available_tasks):
        bot.answer_callback_query(call.id, "Task not found.")
        return

    task = available_tasks[idx]
    if isinstance(task, dict) and task.get("deleted"):
        bot.answer_callback_query(call.id, "Task deleted.")
        return

    active = task.get("active", True) if isinstance(task, dict) else True
    desc = task.get("description", "No description") if isinstance(task, dict) else task
    default_r = bot_config.get("default_reward", 8.0)
    reward = task.get("reward", default_r) if isinstance(task, dict) else default_r
    created_ts = task.get("created_at", 0) if isinstance(task, dict) else 0
    date_str = time.strftime("%d %b %Y, %I:%M %p", time.localtime(created_ts)) if created_ts else "Unknown"

    status_text = "Active 🟢" if active else "Stopped 🔴"

    msg = f"<b>📋 Task ID {idx + 1} Management</b>\n\n<b>Status:</b> {status_text}\n<b>Reward:</b> ₹{reward}\n<b>Task Added:</b> {date_str}\n\n<b>Description:</b>\n{desc}"

    markup = InlineKeyboardMarkup(row_width=2)
    btn_toggle = InlineKeyboardButton("🛑 Stop" if active else "▶️ Start", callback_data=f"toggle_task_{idx}_{page}")
    btn_edit = InlineKeyboardButton("✏️ Edit", callback_data=f"edit_task_{idx}_{page}")
    btn_del = InlineKeyboardButton("🗑️ Delete", callback_data=f"del_task_{idx}_{page}")
    btn_back = InlineKeyboardButton("🔙 Back to Live Tasks", callback_data=f"admin_live_tasks_page_{page}")

    markup.add(btn_toggle, btn_edit)
    markup.add(btn_del)
    markup.add(btn_back)

    bot.edit_message_text(msg, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith("toggle_task_"))
def handle_toggle_task(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    parts = call.data.split("_")
    idx = int(parts[2])
    page = 0
    if len(parts) > 3:
        try:
            page = int(parts[3])
        except ValueError:
            page = 0

    if idx < len(available_tasks):
        task = available_tasks[idx]
        if isinstance(task, dict):
            active = task.get("active", True)
            task["active"] = not active
        else:
            available_tasks[idx] = {"description": task, "reward": bot_config.get("default_reward", 8.0), "active": False}
        save_data()
        call.data = f"manage_task_{idx}_{page}"
        handle_manage_task(call)
    else:
        bot.answer_callback_query(call.id, "Task not found.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("del_task_"))
def handle_del_task(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    parts = call.data.split("_")
    idx = int(parts[2])
    page = 0
    if len(parts) > 3:
        try:
            page = int(parts[3])
        except ValueError:
            page = 0

    if idx < len(available_tasks):
        task = available_tasks[idx]
        if isinstance(task, dict):
            task["deleted"] = True
        else:
            available_tasks[idx] = {"deleted": True}
        save_data()
        bot.answer_callback_query(call.id, "Task deleted.")
        call.data = f"admin_live_tasks_page_{page}"
        handle_live_tasks(call)
    else:
        bot.answer_callback_query(call.id, "Task not found.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("edit_task_"))
def handle_edit_task(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    parts = call.data.split("_")
    idx = int(parts[2])
    page = 0
    if len(parts) > 3:
        try:
            page = int(parts[3])
        except ValueError:
            page = 0

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("🔗 Edit Map Link", callback_data=f"edit_link_{idx}_{page}"))
    markup.add(InlineKeyboardButton("💬 Edit Comment", callback_data=f"edit_comment_{idx}_{page}"))
    markup.add(InlineKeyboardButton("💰 Edit Reward", callback_data=f"edit_reward_{idx}_{page}"))
    markup.add(InlineKeyboardButton("🔙 Back to Task", callback_data=f"manage_task_{idx}_{page}"))

    bot.edit_message_text(f"✏️ What would you like to edit for Task ID {idx + 1}?", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("edit_link_"))
def handle_edit_link(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    idx = int(call.data.split("_")[2])
    msg = bot.send_message(call.message.chat.id, f"Please send the new Map Link for Task ID {idx + 1}:", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_single_edit, idx, "link")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("edit_comment_"))
def handle_edit_comment(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    idx = int(call.data.split("_")[2])
    msg = bot.send_message(call.message.chat.id, f"Please send the new Comment for Task ID {idx + 1}:", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_single_edit, idx, "comment")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("edit_reward_"))
def handle_edit_reward(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    idx = int(call.data.split("_")[2])
    msg = bot.send_message(call.message.chat.id, f"Please send the new Reward amount for Task ID {idx + 1}:", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_single_edit, idx, "reward")
    bot.answer_callback_query(call.id)

def parse_task_desc(desc):
    link = desc
    comment = ""
    if "Link: " in desc and "\n\nComment" in desc:
        try:
            link = desc.split("Link: ")[1].split("\n\nComment")[0].strip()
        except IndexError:
            pass
    if "<code>" in desc and "</code>" in desc:
        try:
            comment = desc.split("<code>")[1].split("</code>")[0].strip()
        except IndexError:
            pass
    return link, comment

def process_single_edit(message, idx, edit_type):
    if is_cancel(message): return
    if not message.text:
        bot.send_message(message.chat.id, "No text detected. Edit cancelled.", reply_markup=restore_menu(message.from_user.id))
        return
    if idx >= len(available_tasks):
        bot.send_message(message.chat.id, "Task not found.", reply_markup=restore_menu(message.from_user.id))
        return

    task = available_tasks[idx]
    is_dict = isinstance(task, dict)

    desc = task.get("description", "") if is_dict else task
    default_r = bot_config.get("default_reward", 8.0)
    reward = task.get("reward", default_r) if is_dict else default_r

    current_link, current_comment = parse_task_desc(desc)

    if edit_type == "link":
        current_link = message.text.replace("<", "&lt;").replace(">", "&gt;")
    elif edit_type == "comment":
        current_comment = message.text.replace("<", "&lt;").replace(">", "&gt;")
    elif edit_type == "reward":
        try:
            reward = float(message.text)
        except ValueError:
            bot.send_message(message.chat.id, "Invalid amount. Edit cancelled.", reply_markup=restore_menu(message.from_user.id))
            return

    task_desc = f"Link: {current_link}\n\nComment:\n<code>{current_comment}</code>"

    if idx < len(available_tasks):
        if isinstance(available_tasks[idx], dict):
            available_tasks[idx]["description"] = task_desc
            available_tasks[idx]["reward"] = reward
        else:
            available_tasks[idx] = {"description": task_desc, "reward": reward, "active": True}
        save_data()
        bot.send_message(message.chat.id, f"✅ Task ID {idx + 1} updated successfully!", reply_markup=restore_menu(message.from_user.id))

@bot.callback_query_handler(func=lambda call: call.data.startswith("admin_live_gmails"))
def handle_live_gmails(call):
    if call.from_user.id not in ADMIN_IDS: return

    parts = call.data.split("_")
    page = 0
    if len(parts) > 4:
        try:
            page = int(parts[4])
        except ValueError:
            page = 0

    live_tasks = []
    for idx, task in enumerate(gmail_tasks):
        if isinstance(task, dict) and (task.get("deleted") or task.get("status") == "completed"):
            continue
        live_tasks.append((idx, task))

    limit = 15
    total = len(live_tasks)
    start_idx = page * limit
    end_idx = start_idx + limit
    page_tasks = live_tasks[start_idx:end_idx]

    markup = InlineKeyboardMarkup()
    for idx, task in page_tasks:
        active = task.get("active", True) if isinstance(task, dict) else True
        status = "🟢" if active else "🔴"
        markup.add(InlineKeyboardButton(f"{status} Gmail Task ID {idx + 1}", callback_data=f"manage_gmtask_{idx}_{page}"))

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"admin_live_gmails_page_{page-1}"))
    if end_idx < total:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"admin_live_gmails_page_{page+1}"))

    if nav_buttons:
        markup.row(*nav_buttons)

    markup.add(InlineKeyboardButton("🔙 Back", callback_data="admin_gmail_panel"))

    msg_text = f"📋 *Live Gmail Tasks (Page {page+1}/{(total-1)//limit + 1 if total > 0 else 1})*"
    try:
        bot.edit_message_text(msg_text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="Markdown")
    except Exception:
        pass

@bot.callback_query_handler(func=lambda call: call.data.startswith("manage_gmtask_"))
def handle_manage_gmtask(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    parts = call.data.split("_")
    idx = int(parts[2])
    page = 0
    if len(parts) > 3:
        try:
            page = int(parts[3])
        except ValueError:
            page = 0

    if idx >= len(gmail_tasks):
        bot.answer_callback_query(call.id, "Gmail task not found.")
        return

    task = gmail_tasks[idx]
    if isinstance(task, dict) and task.get("deleted"):
        bot.answer_callback_query(call.id, "Gmail task deleted.")
        return

    active = task.get("active", True) if isinstance(task, dict) else True
    desc = task.get("description", "No description") if isinstance(task, dict) else task
    default_r = bot_config.get("default_gmail_reward", 8.0)
    reward = task.get("reward", default_r) if isinstance(task, dict) else default_r
    created_ts = task.get("created_at", 0) if isinstance(task, dict) else 0
    date_str = time.strftime("%d %b %Y, %I:%M %p", time.localtime(created_ts)) if created_ts else "Unknown"

    status_text = "Active 🟢" if active else "Stopped 🔴"

    msg = f"<b>📧 Gmail Task ID {idx + 1} Management</b>\n\n<b>Status:</b> {status_text}\n<b>Reward:</b> ₹{reward}\n<b>Task Added:</b> {date_str}\n\n<b>Description:</b>\n{desc}"

    markup = InlineKeyboardMarkup(row_width=2)
    btn_toggle = InlineKeyboardButton("🛑 Stop" if active else "▶️ Start", callback_data=f"toggle_gmtask_{idx}_{page}")
    btn_edit = InlineKeyboardButton("✏️ Edit", callback_data=f"edit_gmtask_{idx}_{page}")
    btn_del = InlineKeyboardButton("🗑️ Delete", callback_data=f"del_gmtask_{idx}_{page}")
    btn_back = InlineKeyboardButton("🔙 Back to Live Gmail Tasks", callback_data=f"admin_live_gmails_page_{page}")

    markup.add(btn_toggle, btn_edit)
    markup.add(btn_del)
    markup.add(btn_back)

    bot.edit_message_text(msg, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith("toggle_gmtask_"))
def handle_toggle_gmtask(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    parts = call.data.split("_")
    idx = int(parts[2])
    page = 0
    if len(parts) > 3:
        try:
            page = int(parts[3])
        except ValueError:
            page = 0

    if idx < len(gmail_tasks):
        task = gmail_tasks[idx]
        if isinstance(task, dict):
            active = task.get("active", True)
            task["active"] = not active
        else:
            gmail_tasks[idx] = {"description": task, "reward": bot_config.get("default_gmail_reward", 8.0), "active": False}
        save_data()
        call.data = f"manage_gmtask_{idx}_{page}"
        handle_manage_gmtask(call)
    else:
        bot.answer_callback_query(call.id, "Gmail task not found.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("del_gmtask_"))
def handle_del_gmtask(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    parts = call.data.split("_")
    idx = int(parts[2])
    page = 0
    if len(parts) > 3:
        try:
            page = int(parts[3])
        except ValueError:
            page = 0

    if idx < len(gmail_tasks):
        task = gmail_tasks[idx]
        if isinstance(task, dict):
            task["deleted"] = True
        else:
            gmail_tasks[idx] = {"deleted": True}
        save_data()
        bot.answer_callback_query(call.id, "Gmail task deleted.")
        call.data = f"admin_live_gmails_page_{page}"
        handle_live_gmails(call)
    else:
        bot.answer_callback_query(call.id, "Gmail task not found.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("edit_gmtask_"))
def handle_edit_gmtask(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    parts = call.data.split("_")
    idx = int(parts[2])
    page = 0
    if len(parts) > 3:
        try:
            page = int(parts[3])
        except ValueError:
            page = 0

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("📧 Edit Gmail Address", callback_data=f"edit_gmaddr_{idx}_{page}"))
    markup.add(InlineKeyboardButton("🔑 Edit Password", callback_data=f"edit_gmpass_{idx}_{page}"))
    markup.add(InlineKeyboardButton("💰 Edit Reward", callback_data=f"edit_gmreward_{idx}_{page}"))
    markup.add(InlineKeyboardButton("🔙 Back to Gmail Task", callback_data=f"manage_gmtask_{idx}_{page}"))

    bot.edit_message_text(f"✏️ What would you like to edit for Gmail Task ID {idx + 1}?", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("edit_gmaddr_"))
def handle_edit_gmaddr(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    idx = int(call.data.split("_")[2])
    msg = bot.send_message(call.message.chat.id, f"Please send the new Gmail address for Task ID {idx + 1}:", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_single_gmedit, idx, "address")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("edit_gmpass_"))
def handle_edit_gmpass(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    idx = int(call.data.split("_")[2])
    msg = bot.send_message(call.message.chat.id, f"Please send the new Password for Task ID {idx + 1}:", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_single_gmedit, idx, "password")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("edit_gmreward_"))
def handle_edit_gmreward(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    idx = int(call.data.split("_")[2])
    msg = bot.send_message(call.message.chat.id, f"Please send the new Reward amount for Gmail Task ID {idx + 1}:", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_single_gmedit, idx, "reward")
    bot.answer_callback_query(call.id)

def parse_gmail_desc(desc):
    gmail_addr = ""
    password = ""
    if "<code>" in desc:
        try:
            parts = desc.split("<code>")
            if len(parts) >= 2:
                gmail_addr = parts[1].split("</code>")[0].strip()
            if len(parts) >= 3:
                password = parts[2].split("</code>")[0].strip()
        except Exception:
            pass
    if not gmail_addr:
        # Fallback to the old method if description was in the old format
        if "Gmail: " in desc and "\n\nPassword" in desc:
            try:
                gmail_addr = desc.split("Gmail: ")[1].split("\n\nPassword")[0].strip()
            except IndexError:
                pass
        if "<code>" in desc and "</code>" in desc:
            try:
                password = desc.split("<code>")[1].split("</code>")[0].strip()
            except IndexError:
                pass
    return gmail_addr, password

def process_single_gmedit(message, idx, edit_type):
    if is_cancel(message): return
    if not message.text:
        bot.send_message(message.chat.id, "No text detected. Edit cancelled.", reply_markup=restore_menu(message.from_user.id))
        return
    if idx >= len(gmail_tasks):
        bot.send_message(message.chat.id, "Gmail task not found.", reply_markup=restore_menu(message.from_user.id))
        return

    task = gmail_tasks[idx]
    is_dict = isinstance(task, dict)

    desc = task.get("description", "") if is_dict else task
    default_r = bot_config.get("default_gmail_reward", 8.0)
    reward = task.get("reward", default_r) if is_dict else default_r

    current_gmail, current_password = parse_gmail_desc(desc)

    if edit_type == "address":
        current_gmail = message.text.replace("<", "&lt;").replace(">", "&gt;")
    elif edit_type == "password":
        current_password = message.text.replace("<", "&lt;").replace(">", "&gt;")
    elif edit_type == "reward":
        try:
            reward = float(message.text)
        except ValueError:
            bot.send_message(message.chat.id, "Invalid amount. Edit cancelled.", reply_markup=restore_menu(message.from_user.id))
            return

    task_desc = f"Gmail : <code>{current_gmail}</code>\npassword : <code>{current_password}</code>"

    if idx < len(gmail_tasks):
        if isinstance(gmail_tasks[idx], dict):
            gmail_tasks[idx]["description"] = task_desc
            gmail_tasks[idx]["reward"] = reward
        else:
            gmail_tasks[idx] = {"description": task_desc, "reward": reward, "active": True}
        save_data()
        bot.send_message(message.chat.id, f"✅ Gmail Task ID {idx + 1} updated successfully!", reply_markup=restore_menu(message.from_user.id))

@bot.callback_query_handler(func=lambda call: call.data == "add_single_gmail")
def add_single_gmail(call):
    if call.from_user.id not in ADMIN_IDS: return
    msg = bot.send_message(call.message.chat.id, "Please send the Gmail address for the task:", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_single_gmail_address)
    bot.answer_callback_query(call.id)

def process_single_gmail_address(message):
    if is_cancel(message): return
    gmail_address = message.text
    if not gmail_address:
        bot.send_message(message.chat.id, "No text detected. Task addition cancelled.", reply_markup=restore_menu(message.from_user.id))
        return

    msg = bot.send_message(message.chat.id, "Please send the Password for this Gmail:", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_single_gmail_password, gmail_address)

def process_single_gmail_password(message, gmail_address):
    if is_cancel(message): return
    password = message.text
    if not password:
        bot.send_message(message.chat.id, "No text detected. Task addition cancelled.", reply_markup=restore_menu(message.from_user.id))
        return

    reward = bot_config.get("default_gmail_reward", 8.0)
    gmail_safe = gmail_address.replace("<", "&lt;").replace(">", "&gt;")
    password_safe = password.replace("<", "&lt;").replace(">", "&gt;")

    task_desc = f"Gmail : <code>{gmail_safe}</code>\npassword : <code>{password_safe}</code>"

    gmail_tasks.append({"description": task_desc, "reward": reward, "active": True, "created_at": time.time()})
    save_data()
    active_tasks_count = sum(1 for t in gmail_tasks if is_task_available(t))
    bot.send_message(message.chat.id, f"✅ Gmail Task added successfully with ₹{reward} reward!\n📊 Total active Gmail tasks currently available: {active_tasks_count}", reply_markup=restore_menu(message.from_user.id))

@bot.callback_query_handler(func=lambda call: call.data == "add_bulk_gmails")
def add_bulk_gmails(call):
    if call.from_user.id not in ADMIN_IDS: return

    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(KeyboardButton("🔙 Cancel"))

    msg = bot.send_message(call.message.chat.id, "Please send the 1st Gmail address:", reply_markup=markup)
    gmail_addresses = []
    bot.register_next_step_handler(msg, process_bulk_gmail_addresses, gmail_addresses)
    bot.answer_callback_query(call.id)

def process_bulk_gmail_addresses(message, gmail_addresses):
    if is_cancel(message): return

    if message.text == "✅ Submit Tasks":
        if not gmail_addresses:
            bot.send_message(message.chat.id, "No Gmails added. Bulk task addition cancelled.", reply_markup=restore_menu(message.from_user.id))
            return

        msg = bot.send_message(message.chat.id, "Gmails received successfully ✅\nNow send the Password for these Gmail tasks (one-time only):", reply_markup=get_cancel_menu())
        bot.register_next_step_handler(msg, process_bulk_gmail_final_password, gmail_addresses)
        return

    gmail_addr = message.text
    if not gmail_addr:
        msg = bot.send_message(message.chat.id, "No text detected. Please send the Gmail address again or click ✅ Submit Tasks.", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True, row_width=1).add(KeyboardButton("✅ Submit Tasks"), KeyboardButton("🔙 Cancel")))
        bot.register_next_step_handler(msg, process_bulk_gmail_addresses, gmail_addresses)
        return

    gmail_addresses.append(gmail_addr)
    count = len(gmail_addresses) + 1

    if len(gmail_addresses) == 1:
        markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
        markup.add(KeyboardButton("✅ Submit Tasks"))
        markup.add(KeyboardButton("🔙 Cancel"))
        msg = bot.send_message(message.chat.id, f"{get_ordinal(count - 1)} Gmail added successfully ✅\nNow send the {get_ordinal(count)} Gmail address or click ✅ Submit Tasks to finish.", reply_markup=markup)
    else:
        msg = bot.send_message(message.chat.id, f"{get_ordinal(count - 1)} Gmail added successfully ✅\nNow send the {get_ordinal(count)} Gmail address or click ✅ Submit Tasks to finish.")
    bot.register_next_step_handler(msg, process_bulk_gmail_addresses, gmail_addresses)

def process_bulk_gmail_final_password(message, gmail_addresses):
    if is_cancel(message): return
    password = message.text
    if not password:
        bot.send_message(message.chat.id, "No text detected. Task addition cancelled.", reply_markup=restore_menu(message.from_user.id))
        return

    reward = bot_config.get("default_gmail_reward", 8.0)
    password_safe = password.replace("<", "&lt;").replace(">", "&gt;")

    for gmail in gmail_addresses:
        gmail_safe = gmail.replace("<", "&lt;").replace(">", "&gt;")
        task_desc = f"Gmail : <code>{gmail_safe}</code>\npassword : <code>{password_safe}</code>"
        gmail_tasks.append({"description": task_desc, "reward": reward, "active": True, "created_at": time.time()})

    save_data()

    bot.send_message(
        message.chat.id,
        f"Bulk Gmail tasks created successfully ✅\n\n🔑 Password:\n{password}\n\n📊 Total Tasks Added: {len(gmail_addresses)}\n\nAll Gmails have been saved as separate tasks with the same password.",
        reply_markup=restore_menu(message.from_user.id)
    )


# ==========================================
#              USER COMMANDS
# ==========================================

def check_expired_tasks():
    for pool in [available_tasks, gmail_tasks]:
        for idx, task in enumerate(list(pool)):
            if isinstance(task, dict):
                if task.get("deleted") or not task.get("active", True):
                    continue
                status = task.get("status", "available")
                claim_time = task.get("claim_time", 0)
                claimed_by = task.get("claimed_by")

                limit = get_task_expiry_time(task, idx, pool)
                if status == "available" and claimed_by and (time.time() - claim_time > limit):
                    user_id = claimed_by
                    if user_id not in user_data:
                        user_data[user_id] = {"balance": 0, "tasks_completed": 0, "referrals": 0, "completed_tasks": [], "pending_tasks": []}

                    user_data[user_id]["expiration_count"] = user_data[user_id].get("expiration_count", 0) + 1
                    expiration_count = user_data[user_id]["expiration_count"]

                    time_str = "2-hour"
                    expiration_msg = f"⚠️ Your {time_str} time is over! The task is now given to other workers."

                    if expiration_count >= 3:
                        unban_time = time.time() + (2 * 86400) # 2 days
                        user_data[user_id]["banned_until"] = unban_time
                        user_data[user_id]["expiration_count"] = 0 # Reset count
                        expiration_msg += "\n\n🚫 You have been temporarily banned for 2 days due to letting 3 tasks expire."
                    else:
                        expiration_msg += f"\n\n⚠️ This is expiration #{expiration_count}. If you let 3 tasks expire, you will be temporarily banned for 2 days."

                    try:
                        bot.send_message(user_id, expiration_msg)
                    except Exception:
                        pass

                    task["claimed_by"] = None
                    task["claim_time"] = 0
                    task["status"] = "available"
                    task["is_bulk"] = False
                    save_data()

def check_expired_bans():
    now = time.time()
    changed = False
    for uid, udata in list(user_data.items()):
        if udata.get("banned"):
            continue
        banned_until = udata.get("banned_until", 0)
        if 0 < banned_until <= now:
            udata["banned_until"] = 0
            changed = True
            try:
                bot.send_message(uid, "✅ *Good news!* Your temporary ban has been lifted. You can now resume completing tasks and earning rewards.", parse_mode="Markdown")
            except Exception:
                pass
    if changed:
        save_data()

def send_direct(target_chat_id, msg):
    # Try copy_message first as it is the most reliable way to preserve all formatting, media, and premium custom emojis.
    if hasattr(msg, 'chat') and hasattr(msg, 'message_id') and msg.chat and msg.message_id:
        try:
            return bot.copy_message(target_chat_id, msg.chat.id, msg.message_id, reply_markup=msg.reply_markup)
        except Exception:
            pass # Fall back to direct sending if copy_message fails (e.g. if the original message was deleted)

    kwargs = {"reply_markup": msg.reply_markup}
    if msg.content_type == 'text':
        return bot.send_message(target_chat_id, msg.text, entities=msg.entities, **kwargs)
    elif msg.content_type == 'photo':
        return bot.send_photo(target_chat_id, msg.photo[-1].file_id, caption=msg.caption, caption_entities=msg.caption_entities, **kwargs)
    elif msg.content_type == 'video':
        return bot.send_video(target_chat_id, msg.video.file_id, caption=msg.caption, caption_entities=msg.caption_entities, **kwargs)
    elif msg.content_type == 'animation':
        return bot.send_animation(target_chat_id, msg.animation.file_id, caption=msg.caption, caption_entities=msg.caption_entities, **kwargs)
    elif msg.content_type == 'document':
        return bot.send_document(target_chat_id, msg.document.file_id, caption=msg.caption, caption_entities=msg.caption_entities, **kwargs)
    elif msg.content_type == 'sticker':
        return bot.send_sticker(target_chat_id, msg.sticker.file_id, **kwargs)
    elif msg.content_type == 'voice':
        return bot.send_voice(target_chat_id, msg.voice.file_id, caption=msg.caption, caption_entities=msg.caption_entities, **kwargs)
    elif msg.content_type == 'audio':
        return bot.send_audio(target_chat_id, msg.audio.file_id, caption=msg.caption, caption_entities=msg.caption_entities, **kwargs)
    else:
        return bot.copy_message(target_chat_id, msg.chat.id, msg.message_id, reply_markup=msg.reply_markup)

def run_auto_broadcast_check():
    config = auto_broadcast_config
    if not config.get("is_running"):
        return

    # Check if count is finished
    total_count = config.get("total_count", -1)
    if total_count != -1 and config.get("sent_count", 0) >= total_count:
        config["is_running"] = False
        save_data()
        return

    # Check if interval has passed
    now = time.time()
    if now - config.get("last_sent_time", 0) > config.get("interval_seconds", 3600):
        # Time to broadcast!
        message_id = config.get("message_id")
        chat_id = config.get("message_chat_id")
        msg_json = config.get("message_json")

        if not message_id or not chat_id:
            return # No message to send

        config["sent_count"] += 1
        config["last_sent_time"] = now

        # Check again if this was the last one
        if total_count != -1 and config.get("sent_count", 0) >= total_count:
            config["is_running"] = False

        save_data()

        msg = None
        if msg_json:
            try:
                msg = telebot.types.Message.de_json(msg_json)
            except Exception:
                pass

        def perform_broadcast(msg_to_send, source_chat_id, source_message_id):
            # Broadcast to users
            user_success_count = 0
            for user_id in list(user_data.keys()):
                if not is_user_banned(user_id):
                    try:
                        if msg_to_send:
                            send_direct(user_id, msg_to_send)
                        else:
                            bot.copy_message(user_id, source_chat_id, source_message_id)
                        user_success_count += 1
                    except telebot.apihelper.ApiTelegramException as e:
                        if e.error_code == 429:
                            retry_after = e.result_json.get('parameters', {}).get('retry_after', 3) if hasattr(e, 'result_json') else 3
                            time.sleep(retry_after)
                            try:
                                if msg_to_send:
                                    send_direct(user_id, msg_to_send)
                                else:
                                    bot.copy_message(user_id, source_chat_id, source_message_id)
                                user_success_count += 1
                            except Exception:
                                pass
                    except Exception:
                        pass
                    time.sleep(0.05) # Always sleep to respect rate limits even for blocked users

            # Broadcast to groups
            group_success_count = 0
            for group_id in list(known_groups):
                try:
                    if msg_to_send:
                        send_direct(group_id, msg_to_send)
                    else:
                        bot.copy_message(group_id, source_chat_id, source_message_id)
                    group_success_count += 1
                except telebot.apihelper.ApiTelegramException as e:
                    if e.error_code == 429:
                        retry_after = e.result_json.get('parameters', {}).get('retry_after', 3) if hasattr(e, 'result_json') else 3
                        time.sleep(retry_after)
                        try:
                            if msg_to_send:
                                send_direct(group_id, msg_to_send)
                            else:
                                bot.copy_message(group_id, source_chat_id, source_message_id)
                            group_success_count += 1
                        except Exception:
                            pass
                except Exception:
                    pass
                time.sleep(0.05)

            print(f"Auto-broadcast sent to {user_success_count} users and {group_success_count} groups.")

        threading.Thread(target=perform_broadcast, args=(msg, chat_id, message_id)).start()

def is_task_available(task, user_id=None):
    if not isinstance(task, dict):
        return True
    if task.get("deleted") or not task.get("active", True):
        return False

    status = task.get("status", "available")
    if status in ["completed", "pending_validation"]:
        return False

    claimed_by = task.get("claimed_by")
    claim_time = task.get("claim_time", 0)

    if claimed_by and status == "available":
        pool = None
        idx = -1
        if task in available_tasks:
            pool = available_tasks
            idx = available_tasks.index(task)
        elif task in gmail_tasks:
            pool = gmail_tasks
            idx = gmail_tasks.index(task)

        limit = 7200
        if idx != -1 and pool is not None:
            limit = get_task_expiry_time(task, idx, pool)

        if str(claimed_by) != str(user_id) and (time.time() - claim_time < limit):
            return False

    return True

def get_available_tasks_for_user(user_id):
    udata = user_data.get(user_id, {})
    completed = udata.get("completed_tasks", [])
    pending = udata.get("pending_tasks", [])

    # First, check if the user has an actively claimed task
    for idx, task in enumerate(available_tasks):
        if idx in completed or idx in pending:
            continue
        if isinstance(task, dict):
            limit = get_task_expiry_time(task, idx, available_tasks)
            if task.get("claimed_by") is not None and str(task.get("claimed_by")) == str(user_id) and task.get("status") == "available" and (time.time() - task.get("claim_time", 0) < limit):
                return [(idx, task)]

    # Otherwise, return the very first available task
    valid_tasks = []
    for idx, task in enumerate(available_tasks):
        if idx in completed or idx in pending:
            continue
        if is_task_available(task, user_id):
            valid_tasks.append((idx, task))
            break # Stop after finding 1 task
    return valid_tasks

def get_available_gmail_tasks_for_user(user_id):
    udata = user_data.get(user_id, {})
    completed = udata.get("completed_gmails", [])
    pending = udata.get("pending_gmails", [])

    for idx, task in enumerate(gmail_tasks):
        if idx in completed or idx in pending:
            continue
        if isinstance(task, dict):
            limit = get_task_expiry_time(task, idx, gmail_tasks)
            if task.get("claimed_by") is not None and str(task.get("claimed_by")) == str(user_id) and task.get("status") == "available" and (time.time() - task.get("claim_time", 0) < limit):
                return [(idx, task)]

    valid_tasks = []
    for idx, task in enumerate(gmail_tasks):
        if idx in completed or idx in pending:
            continue
        if is_task_available(task, user_id):
            valid_tasks.append((idx, task))
            break
    return valid_tasks

# Warning photo file IDs
GMAIL_PHOTO_ID = "AgACAgUAAxkBAAJOKWoYFLxYr1KN8mAskmmyjfR4cXRkAAJuEGsbxVXAVAX7ZTkAAe2hAwEAAwIAA3kAAzsE"
REVIEW_PHOTO_ID = "AgACAgUAAxkBAAJOK2oYFMhd1M5bc-IFm8VNVDT-ZwY-AAJvEGsbxVXAVLsXa1M5YC-oAQADAgADeAADOwQ"

def send_warning_photo(chat_id, photo_type, caption, reply_markup):
    photo_id = GMAIL_PHOTO_ID if photo_type == "gmail" else REVIEW_PHOTO_ID
    try:
        return bot.send_photo(chat_id, photo_id, caption=caption, reply_markup=reply_markup, parse_mode="Markdown")
    except Exception as e:
        print(f"Error sending warning photo: {e}")
        return bot.send_message(chat_id, caption, reply_markup=reply_markup, parse_mode="Markdown")

def is_user_banned(user_id):
    u_data = user_data.get(user_id, {})
    if u_data.get("banned"):
        return "🚫 Your account is permanently banned."
    banned_until = u_data.get("banned_until", 0)
    if banned_until > time.time():
        days_left = max(1, int((banned_until - time.time()) / 86400))
        return f"🚫 Your account is banned for {days_left} days."
    return None

def check_and_reward_referrer(user_id):
    """Rewards the referrer when a referred user completes their first task."""
    u_data = user_data.get(user_id, {})
    if u_data.get("tasks_completed", 0) >= 1:
        referrer_id = u_data.get("referred_by")
        if referrer_id and referrer_id in user_data:
            referral_rewarded_list = user_data[referrer_id].setdefault("referral_rewarded_users", [])
            if user_id not in referral_rewarded_list:
                referral_rewarded_list.append(user_id)
                user_data[referrer_id]["referrals"] = user_data[referrer_id].get("referrals", 0) + 1
                user_data[referrer_id]["balance"] = user_data[referrer_id].get("balance", 0) + 1.0
                try:
                    bot.send_message(referrer_id, "🎉 A user you referred just completed their first task! You earned ₹1.")
                except Exception:
                    pass

@bot.message_handler(func=lambda message: message.text in ["📚 Help & Tutorial", "Help & Support 📞", "🆘 Help", "🆘 ʜᴇʟᴘ"])
def handle_help(message):
    if not check_force_sub_and_alert(message): return
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🔰 ʀᴇᴠɪᴇᴡ ᴛᴜᴛᴏʀɪᴀʟ", url="https://t.me/MapreviewsEra"),
        InlineKeyboardButton("📧 ɢᴍᴀɪʟ ᴛᴜᴛᴏʀɪᴀʟ", url="https://t.me/GmailworkEra"),
        InlineKeyboardButton("💸 ᴄᴀꜱʜɢʟɪᴅᴇ ᴛᴜᴛᴏʀɪᴀʟ", url="https://t.me/CashGlideTutorial"),
        InlineKeyboardButton("📞 ᴄᴏɴᴛᴀᴄᴛ ꜱᴜᴘᴘᴏʀᴛ", url="https://t.me/REAL_TOJIx")
    )
    text = (
        "༶•┈┈⛧┈♛\n"
        "🔰 <b>ʜ ᴇ ʟ ᴘ  &  ꜱ ᴜ ᴘ ᴘ ᴏ ʀ ᴛ</b> ⚜️\n"
        "────── ⋆⋅☆⋅⋆ ──────\n\n"
        "✨ ɴᴇᴇᴅ ᴀꜱꜱɪꜱᴛᴀɴᴄᴇ? ᴡᴇ'ᴠᴇ ɢᴏᴛ ʏᴏᴜ ᴄᴏᴠᴇʀᴇᴅ. 💎\n\n"
        "꘎♡━━━━━♡꘎━━━━━♡꘎\n"
        "👇 <b>⚡ ᴄʜᴏᴏꜱᴇ ᴀ ᴛᴜᴛᴏʀɪᴀʟ ᴏʀ ᴄᴏɴᴛᴀᴄᴛ ᴏᴜʀ ᴛᴇᴀᴍ:</b>"
    )
    bot.send_message(message.chat.id, text, reply_markup=markup, parse_mode="HTML")

@bot.message_handler(func=lambda message: message.text in ["🏆 Leaderboard", "Leaderboard 🏆", "🏆 ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ"])
def handle_leaderboard(message):
    if not check_force_sub_and_alert(message): return
    is_admin = message.from_user.id in ADMIN_IDS
    sorted_reviews = sorted(user_data.items(), key=lambda x: x[1].get("lifetime_reviews_completed", len(x[1].get("completed_tasks", []))), reverse=True)
    top_reviews = [x for x in sorted_reviews if x[1].get("lifetime_reviews_completed", len(x[1].get("completed_tasks", []))) > 0][:10]
    sorted_gmails = sorted(user_data.items(), key=lambda x: x[1].get("lifetime_gmails_completed", len(x[1].get("completed_gmails", []))), reverse=True)
    top_gmails = [x for x in sorted_gmails if x[1].get("lifetime_gmails_completed", len(x[1].get("completed_gmails", []))) > 0][:10]
    ranks = ['🥇', '🥈', '🥉', '🏅', '🏅', '🏅', '🏅', '🏅', '🏅', '🏅']
    msg = "༶•┈┈⛧┈♛\n🏆 <b>ᴠ ɪ ᴘ  ʟ ᴇ ᴀ ᴅ ᴇ ʀ ʙ ᴏ ᴀ ʀ ᴅ</b> ✨\n────── ⋆⋅☆⋅⋆ ──────\n\n📍 <b>ᴛᴏᴘ ʀᴇᴠɪᴇᴡ ᴇxᴘᴇʀᴛꜱ:</b> 💎\n\n"
    if not top_reviews: msg += "<i>ɴᴏ ʀᴇᴠɪᴇᴡ ᴛᴀꜱᴋꜱ ᴄᴏᴍᴘʟᴇᴛᴇᴅ ʏᴇᴛ.</i>\n\n"
    else:
        for i, (uid, udata) in enumerate(top_reviews):
            name = html.escape(udata.get('first_name', 'User'))
            if is_admin and udata.get('username'): name += f" (@{html.escape(udata['username'])})"
            count = udata.get("lifetime_reviews_completed", len(udata.get("completed_tasks", [])))
            msg += f"{ranks[i]} <b>{name}</b> — <code>{count} ʀᴇᴠɪᴇᴡꜱ</code>\n\n"
    msg += "꘎♡━━━━━♡꘎━━━━━♡꘎\n\n📧 <b>ᴛᴏᴘ ɢᴍᴀɪʟ ᴇxᴘᴇʀᴛꜱ:</b> 💎\n\n"
    if not top_gmails: msg += "<i>ɴᴏ ɢᴍᴀɪʟ ᴛᴀꜱᴋꜱ ᴄᴏᴍᴘʟᴇᴛᴇᴅ ʏᴇᴛ.</i>\n\n"
    else:
        for i, (uid, udata) in enumerate(top_gmails):
            name = html.escape(udata.get('first_name', 'User'))
            if is_admin and udata.get('username'): name += f" (@{html.escape(udata['username'])})"
            count = udata.get("lifetime_gmails_completed", len(udata.get("completed_gmails", [])))
            msg += f"{ranks[i]} <b>{name}</b> — <code>{count} ɢᴍᴀɪʟꜱ</code>\n\n"
    uid = message.from_user.id
    my_reviews = user_data.get(uid, {}).get("lifetime_reviews_completed", len(user_data.get(uid, {}).get("completed_tasks", [])))
    my_gmails = user_data.get(uid, {}).get("lifetime_gmails_completed", len(user_data.get(uid, {}).get("completed_gmails", [])))
    msg += f"────── ⋆⋅☆⋅⋆ ──────\n👤 <b>ʏ ᴏ ᴜ ʀ  ꜱ ᴛ ᴀ ᴛ ꜱ</b>\n📍 ʀᴇᴠɪᴇᴡꜱ: <code>{my_reviews}</code> 🪙\n📧 ɢᴍᴀɪʟꜱ: <code>{my_gmails}</code> 🪙\n꘎━━━━━꘎♡━━━━━♡꘎"
    bot.send_message(message.chat.id, msg, parse_mode="HTML")

@bot.message_handler(func=lambda message: message.text in ["📧 Get Gmail Task", "📧 ɢᴍᴀɪʟ ᴛᴀꜱᴋ", "GMAIL TASK", "Gmail Task"])
def handle_gmail_task(message):
    _delete_previous_ui(message.chat.id)
    if not check_force_sub_and_alert(message): return
    if not check_cooldown(message.from_user.id):
        bot.send_message(message.chat.id, "⚠️ ᴘʟᴇᴀꜱᴇ ᴅᴏɴ'ᴛ ꜱᴘᴀᴍ! ᴄᴏᴏʟᴅᴏᴡɴ ᴀᴄᴛɪᴠᴇ."); return
    user_id = message.from_user.id
    ban_msg = is_user_banned(user_id)
    if ban_msg: return bot.send_message(message.chat.id, ban_msg)
    udata = user_data.setdefault(user_id, {"balance": 0, "tasks_completed": 0, "referrals": 0, "completed_tasks": [], "pending_tasks": []})
    current_date = get_ist_date()
    if udata.get("last_task_date") != current_date:
        udata["last_task_date"] = current_date; udata["reviews_today_count"] = 0; udata["gmails_today_count"] = 0; save_data()
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🔹 ꜱɪɴɢʟᴇ ɢᴍᴀɪʟ ᴛᴀꜱᴋ [ꜱᴛᴀɴᴅᴀʀᴅ]", callback_data="start_single_gmail"),
        InlineKeyboardButton("📦 ʙᴜʟᴋ ɢᴍᴀɪʟ ᴘᴀᴄᴋ [ᴘʀᴇᴍɪᴜᴍ]", callback_data="start_bulk_gmail_warn")
    )
    text = (
        "༶•┈┈⛧┈♛\n🔰 <b>ᴄ ᴜ ʀ ʀ ᴇ ɴ ᴛ ʟ ʏ  ᴀ ᴠ ᴀ ɪ ʟ ᴀ ʙ ʟ ᴇ  ɢ ᴍ ᴀ ɪ ʟ ꜱ</b> ⚜️\n"
        "────── ⋆⋅☆⋅⋆ ──────\n\n"
        f"💎 <b>ꜱɪɴɢʟᴇ ɢᴍᴀɪʟ:</b> ₹{bot_config.get('default_gmail_reward', 8.0)} 🪙\n"
        f"📦 <b>ʙᴜʟᴋ ɢᴍᴀɪʟ:</b> ₹{bot_config.get('bulk_gmail_per_task', 10.0)} ᴘᴇʀ ᴛᴀꜱᴋ 💰\n\n"
        "꘎♡━━━━━♡꘎━━━━━♡꘎\n👇 <i>⚡ ᴄʜᴏᴏꜱᴇ ʏᴏᴜʀ ᴘʀᴇꜰᴇʀʀᴇᴅ ᴍᴏᴅᴇ:</i>"
    )
    _remember_ui_message(bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=markup))

@bot.message_handler(func=lambda message: message.text in ["📍 Get Review Task", "📍 Review Task", "📍 Get Tasks", "📍 ʀᴇᴠɪᴇᴡ ᴛᴀꜱᴋ", "REVIEW TASK", "Review Task"])
def handle_review_task(message):
    _delete_previous_ui(message.chat.id)
    if not check_force_sub_and_alert(message): return
    if not check_cooldown(message.from_user.id):
        bot.send_message(message.chat.id, "⚠️ ᴘʟᴇᴀꜱᴇ ᴅᴏɴ'ᴛ ꜱᴘᴀᴍ! ᴄᴏᴏʟᴅᴏᴡɴ ᴀᴄᴛɪᴠᴇ.")
        return
    user_id = message.from_user.id
    ban_msg = is_user_banned(user_id)
    if ban_msg: return bot.send_message(message.chat.id, ban_msg)
    udata = user_data.setdefault(user_id, {"balance": 0, "tasks_completed": 0, "referrals": 0, "completed_tasks": [], "pending_tasks": []})
    if not udata.get("knows_tasks"):
        markup = InlineKeyboardMarkup(row_width=1)
        markup.add(InlineKeyboardButton("✅ ʏᴇꜱ, ɪ ᴋɴᴏᴡ", callback_data="knows_review_yes"), InlineKeyboardButton("❌ ɴᴏ, ᴛᴇᴀᴄʜ ᴍᴇ", url="https://t.me/MapreviewsEra"))
        bot.send_message(message.chat.id, "༶•┈┈⛧┈♛\n📍 <b>ɢᴏᴏɢʟᴇ ᴍᴀᴘꜱ ʀᴇᴠɪᴇᴡ ᴡᴏʀᴋ</b> ⚜️\n────── ⋆⋅☆⋅⋆ ──────\n\n✨ ᴅᴏ ʏᴏᴜ ᴋɴᴏᴡ ʜᴏᴡ ᴛᴏ ᴘᴇʀꜰᴏʀᴍ ʀᴇᴠɪᴇᴡ ᴡᴏʀᴋ?", parse_mode="HTML", reply_markup=markup)
        return
    current_date = get_ist_date()
    if udata.get("last_task_date") != current_date:
        udata["last_task_date"] = current_date; udata["reviews_today_count"] = 0; udata["gmails_today_count"] = 0; save_data()
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("🔹 ꜱɪɴɢʟᴇ ʀᴇᴠɪᴇᴡ ᴛᴀꜱᴋ [ꜱᴛᴀɴᴅᴀʀᴅ]", callback_data="start_single_review"),
        InlineKeyboardButton("📦 ʙᴜʟᴋ ʀᴇᴠɪᴇᴡ ᴘᴀᴄᴋ [ᴘʀᴇᴍɪᴜᴍ]", callback_data="start_bulk_review_warn")
    )
    text = (
        "༶•┈┈⛧┈♛\n🔰 <b>ᴄ ᴜ ʀ ʀ ᴇ ɴ ᴛ ʟ ʏ  ᴀ ᴠ ᴀ ɪ ʟ ᴀ ʙ ʟ ᴇ  ᴛ ᴀ ꜱ ᴋ ꜱ</b> ⚜️\n"
        "────── ⋆⋅☆⋅⋆ ──────\n\n"
        f"💎 <b>ꜱɪɴɢʟᴇ ʀᴇᴠɪᴇᴡ:</b> ₹{bot_config.get('default_reward', 8.0)} 🪙\n"
        f"📦 <b>ʙᴜʟᴋ ʀᴇᴠɪᴇᴡ:</b> ₹{bot_config.get('bulk_review_per_task', 10.0)} ᴘᴇʀ ᴛᴀꜱᴋ 💰\n\n"
        "꘎♡━━━━━♡꘎━━━━━♡꘎\n👇 <i>⚡ ᴄʜᴏᴏꜱᴇ ʏᴏᴜʀ ᴘʀᴇꜰᴇʀʀᴇᴅ ᴍᴏᴅᴇ:</i>"
    )
    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "knows_review_yes")
def handle_knows_review_yes(call):
    if not check_force_sub_and_alert(call): return
    user_id = call.from_user.id
    ban_msg = is_user_banned(user_id)
    if ban_msg:
        bot.answer_callback_query(call.id, ban_msg, show_alert=True)
        return
    if user_id in user_data:
        user_data[user_id]["knows_tasks"] = True
        save_data()

    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass

    current_date = get_ist_date()
    udata = user_data[user_id]
    if udata.get("last_task_date") != current_date:
        udata["last_task_date"] = current_date
        udata["reviews_today_count"] = 0
        udata["gmails_today_count"] = 0
        save_data()

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("Single Review Task", callback_data="start_single_review"),
        InlineKeyboardButton("Bulk Review Task", callback_data="start_bulk_review_warn")
    )
    msg_text = (
        "The Current Available tasks\n\n"
        f"💰 *Single Review Task*: ₹{bot_config.get('default_reward', 8.0)}\n"
        f"💰 *Bulk Review Task*: ₹{bot_config.get('bulk_review_per_task', 10.0)} per task"
    )
    bot.send_message(call.message.chat.id, msg_text, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "start_single_review")
def start_single_review_handler(call):
    if not check_force_sub_and_alert(call): return
    user_id = call.from_user.id
    ban_msg = is_user_banned(user_id)
    if ban_msg:
        bot.answer_callback_query(call.id, ban_msg, show_alert=True)
        return
    udata = user_data.setdefault(user_id, {"balance": 0, "tasks_completed": 0, "referrals": 0, "completed_tasks": [], "pending_tasks": []})

    if udata.get("reviews_today_count", 0) >= 50:
        bot.answer_callback_query(call.id, "⚠️ Daily limit full! You can only do 50 Review Tasks in 1 day.", show_alert=True)
        return

    if len(udata.get("pending_tasks", [])) >= 5:
        bot.edit_message_text("⏳ You already have 5 tasks waiting for admin approval. Please wait until admin checks them.", chat_id=call.message.chat.id, message_id=call.message.message_id)
        return

    valid_tasks = get_available_tasks_for_user(user_id)
    if len(valid_tasks) == 0:
        bot.edit_message_text("📭 *For now There is no Task Available.*\n\n📢 *When the admin adds we will notify.*\n\n👀 *Stay Tuned*", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")
        return

    msg = (
        "⚠️ *WARNING*\n\n"
        "Send Real review screenshot only.\n\n"
        "Fake or edited screenshot = No payment and Account Ban !"
    )
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✅ Continue", callback_data="continue_single_review"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel_review_flow")
    )
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    send_warning_photo(call.message.chat.id, "review", msg, markup)

@bot.callback_query_handler(func=lambda call: call.data == "continue_single_review")
def continue_single_review_handler(call):
    if not check_force_sub_and_alert(call): return
    user_id = call.from_user.id
    ban_msg = is_user_banned(user_id)
    if ban_msg:
        bot.answer_callback_query(call.id, ban_msg, show_alert=True)
        return

    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass

    with data_lock:
        udata = user_data.setdefault(user_id, {"balance": 0, "tasks_completed": 0, "referrals": 0, "completed_tasks": [], "pending_tasks": []})

        if udata.get("reviews_today_count", 0) >= 50:
            bot.answer_callback_query(call.id, "⚠️ Daily limit full! You can only do 50 Review Tasks in 1 day.", show_alert=True)
            return

        if len(udata.get("pending_tasks", [])) >= 5:
            bot.send_message(call.message.chat.id, "⏳ You already have 5 tasks waiting for admin approval. Please wait until admin checks them.")
            return

        valid_tasks = get_available_tasks_for_user(user_id)
        if len(valid_tasks) == 0:
            bot.send_message(call.message.chat.id, "📭 *For now There is no Task Available.*\n\n📢 *When the admin adds we will notify.*\n\n👀 *Stay Tuned*", parse_mode="Markdown")
            return

        task_idx, task = valid_tasks[0]
        if isinstance(task, dict):
            task["claimed_by"] = user_id
            task["claim_time"] = time.time()
            task["status"] = "available"
            task["is_bulk"] = False
            save_data()

    desc = task.get("description", "No description") if isinstance(task, dict) else task
    reward = task.get("reward", bot_config.get("default_reward", 8.0)) if isinstance(task, dict) else bot_config.get("default_reward", 8.0)
    link, comment = parse_task_desc(desc)

    msg = (
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 <b>GOOGLE MAPS REVIEW TASK</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📍 <b>Google Map Link</b>:\n{link}\n\n"
        f"💬 <b>Review Comment</b>:\n<code>{comment}</code>\n\n"
        f"⏳ <b>Time Limit</b> → 15 Minutes\n\n"
        f"Reward: ₹{reward}"
    )
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("📤 Submit Proof", callback_data=f"complete_task_rev_{task_idx}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_single_claim_rev_{task_idx}")
    )
    bot.send_message(call.message.chat.id, msg, parse_mode="HTML", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("cancel_single_claim_"))
def cancel_single_claim_handler(call):
    if not check_force_sub_and_alert(call): return
    user_id = call.from_user.id
    parts = call.data.split("_")
    task_type = parts[3]
    task_idx = int(parts[4])
    pool = available_tasks if task_type == "rev" else gmail_tasks
    with data_lock:
        if task_idx < len(pool):
            task = pool[task_idx]
            if isinstance(task, dict) and task.get("claimed_by") is not None and str(task.get("claimed_by")) == str(user_id):
                task["claimed_by"] = None
                task["claim_time"] = 0
                task["status"] = "available"
                task["is_bulk"] = False
                save_data()
    bot.edit_message_text("Task claim cancelled.", chat_id=call.message.chat.id, message_id=call.message.message_id)
    bot.send_message(call.message.chat.id, "Returned to main menu.", reply_markup=restore_menu(user_id))

@bot.callback_query_handler(func=lambda call: call.data == "start_bulk_review_warn")
def start_bulk_review_warn_handler(call):
    if not check_force_sub_and_alert(call): return
    user_id = call.from_user.id
    ban_msg = is_user_banned(user_id)
    if ban_msg:
        bot.answer_callback_query(call.id, ban_msg, show_alert=True)
        return

    udata = user_data.get(user_id, {})
    if udata.get("bulk_review_blocked"):
        progress = udata.get("bulk_review_unlock_progress", 0)
        bot.answer_callback_query(call.id, f"❌ You are blocked from Bulk Reviews. 10 of your bulk tasks were rejected. Do 10 single reviews to unlock! ({progress}/10)", show_alert=True)
        return

    completed_reviews_count = udata.get("lifetime_reviews_completed", len(udata.get("completed_tasks", [])))
    if completed_reviews_count < 10:
        bot.answer_callback_query(call.id, f"❌ You must do 10 Single Review Tasks first! (Completed: {completed_reviews_count}/10)", show_alert=True)
        return

    available_count = 0
    for task in available_tasks:
        if is_task_available(task, user_id):
            available_count += 1
    if available_count < 10:
        bot.answer_callback_query(call.id, "❌ Not available currently", show_alert=True)
        return

    reward_val = 10 * bot_config.get("bulk_review_per_task", 10.0)
    msg = (
        "⚠️ *WARNING*\n\n"
        "Send Real review screenshot only.\n\n"
        "Fake or edited screenshot = No payment and Account Ban !"
    )
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✅ Continue", callback_data="confirm_bulk_review_10"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel_review_flow")
    )
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    send_warning_photo(call.message.chat.id, "review", msg, markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_bulk_review_"))
def confirm_bulk_review_handler(call):
    if not check_force_sub_and_alert(call): return
    user_id = call.from_user.id
    ban_msg = is_user_banned(user_id)
    if ban_msg:
        bot.answer_callback_query(call.id, ban_msg, show_alert=True)
        return

    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass

    count = int(call.data.split("_")[3])
    with data_lock:
        udata = user_data.setdefault(user_id, {"balance": 0, "tasks_completed": 0, "referrals": 0, "completed_tasks": [], "pending_tasks": []})
        if udata.get("reviews_today_count", 0) + count > 50:
            bot.answer_callback_query(call.id, f"⚠️ Daily limit exceeded! You can only complete up to {50 - udata.get('reviews_today_count', 0)} more Review Tasks today.", show_alert=True)
            return
        claimed_ids = []
        for idx, task in enumerate(available_tasks):
            if is_task_available(task, user_id) and idx not in udata.get("completed_tasks", []) and idx not in udata.get("pending_tasks", []):
                if isinstance(task, dict):
                    task["claimed_by"] = user_id
                    task["claim_time"] = time.time()
                    task["status"] = "available"
                    task["is_bulk"] = True
                claimed_ids.append(idx)
                if len(claimed_ids) == count:
                    break
        if not claimed_ids:
            bot.send_message(call.message.chat.id, "📭 *For now There is no Task Available.*\n\n📢 *When the admin adds we will notify.*\n\n👀 *Stay Tuned*", parse_mode="Markdown")
            return
        udata["current_review_pack"] = {
            "task_ids": claimed_ids,
            "submissions": {},
            "status": "claimed",
            "claim_time": time.time()
        }
        save_data()
    display_bulk_review_pack(call.message, user_id)

def display_bulk_review_pack(message, user_id):
    udata = user_data[user_id]
    pack = udata.get("current_review_pack")
    if not pack:
        bot.send_message(message.chat.id, "No active review pack found.")
        return
    task_count = len(pack["task_ids"])
    reward_per_task = bot_config.get("bulk_review_per_task", 10.0)
    total_reward = task_count * reward_per_task
    msg = (
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📍 <b>BULK REVIEW PACK</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 <b>Total Reward</b>: ₹{total_reward} (₹{reward_per_task} per task)\n"
        f"⏳ <b>Time Limit</b>: 2 Hours\n\n"
    )
    markup = InlineKeyboardMarkup(row_width=2)
    buttons = []
    for i, idx in enumerate(pack["task_ids"]):
        task = available_tasks[idx]
        desc = task.get("description", "") if isinstance(task, dict) else task
        link, comment = parse_task_desc(desc)
        msg += f"<b>{i+1}. Review Task</b>:\n📍 Link: {link}\n💬 Comment: <code>{comment}</code>\n\n"
        is_sub = str(idx) in pack["submissions"]
        btn_text = f"📤 Submit {i+1}" if not is_sub else f"✅ Submit {i+1} (Done)"
        buttons.append(InlineKeyboardButton(btn_text, callback_data=f"bsub_rev_{idx}"))
    markup.add(*buttons)
    markup.row(InlineKeyboardButton("📤 Submit Pack to Admin", callback_data="final_submit_review_pack"))
    markup.row(InlineKeyboardButton("❌ Cancel Pack", callback_data="cancel_review_pack"))
    try:
        bot.edit_message_text(msg, chat_id=message.chat.id, message_id=message.message_id, parse_mode="HTML", reply_markup=markup)
    except Exception:
        bot.send_message(message.chat.id, msg, parse_mode="HTML", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("bsub_rev_"))
def bsub_rev_handler(call):
    if not check_force_sub_and_alert(call): return
    user_id = call.from_user.id
    ban_msg = is_user_banned(user_id)
    if ban_msg:
        bot.answer_callback_query(call.id, ban_msg, show_alert=True)
        return
    task_idx = int(call.data.split("_")[2])
    udata = user_data.get(user_id, {})
    pack = udata.get("current_review_pack")
    if not pack or task_idx not in pack["task_ids"]:
        bot.answer_callback_query(call.id, "No active review pack.", show_alert=True)
        return
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    msg = bot.send_message(call.message.chat.id, "Please enter the Gmail address you used to write the review:", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_bulk_gmail_step, task_idx, "rev")
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("bsub_gm_"))
def bsub_gm_handler(call):
    if not check_force_sub_and_alert(call): return
    user_id = call.from_user.id
    ban_msg = is_user_banned(user_id)
    if ban_msg:
        bot.answer_callback_query(call.id, ban_msg, show_alert=True)
        return
    task_idx = int(call.data.split("_")[2])
    udata = user_data.get(user_id, {})
    pack = udata.get("current_gmail_pack")
    if not pack or task_idx not in pack["task_ids"]:
        bot.answer_callback_query(call.id, "No active Gmail pack.", show_alert=True)
        return

    msg_text = (
        "⚠️ *WARNING*\n\n"
        "Send the Same screenshot of Gmail account.\n\n"
        "Wrong screenshot = No payment and Account Ban !"
    )
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✅ Continue", callback_data=f"cont_bsub_gm_{task_idx}"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel_bsub_sub_gm")
    )
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    send_warning_photo(call.message.chat.id, "gmail", msg_text, markup)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data.startswith("cont_bsub_gm_"))
def handle_continue_bsub_gm(call):
    if not check_force_sub_and_alert(call): return
    user_id = call.from_user.id
    ban_msg = is_user_banned(user_id)
    if ban_msg:
        bot.answer_callback_query(call.id, ban_msg, show_alert=True)
        return
    task_idx = int(call.data.split("_")[3])

    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    msg = bot.send_message(call.message.chat.id, f"Send the screenshot proof of Gmail Account for Gmail Task {task_idx+1}:", reply_markup=get_cancel_menu())
    if task_idx < len(gmail_tasks):
        task = gmail_tasks[task_idx]
        desc = task.get("description", "") if isinstance(task, dict) else task
        g_addr, g_pwd = parse_gmail_desc(desc)
        creds = f"Gmail: {g_addr}\nPassword: {g_pwd}"
    else:
        creds = "Gmail: Unknown\nPassword: Unknown"
    bot.register_next_step_handler(msg, process_bulk_proof_step, task_idx, "gm", creds)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "cancel_bsub_sub_gm")
def handle_cancel_bsub_sub_gm(call):
    if not check_force_sub_and_alert(call): return
    user_id = call.from_user.id
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    display_bulk_gmail_pack(call.message, user_id)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "final_submit_review_pack")
def final_submit_review_pack_handler(call):
    if not check_force_sub_and_alert(call): return
    user_id = call.from_user.id
    ban_msg = is_user_banned(user_id)
    if ban_msg:
        bot.answer_callback_query(call.id, ban_msg, show_alert=True)
        return
    with data_lock:
        udata = user_data.get(user_id, {})
        pack = udata.get("current_review_pack")
        if not pack or pack.get("status") == "submitted":
            bot.answer_callback_query(call.id, "No active review pack or already submitted.", show_alert=True)
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
            return
        if time.time() - pack.get("claim_time", 0) > 7200:
            bot.answer_callback_query(call.id, "⚠️ Your 2-hour time is over for this review pack!", show_alert=True)
            udata["current_review_pack"] = None
            save_data()
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
            return
        if not pack.get("submissions"):
            bot.answer_callback_query(call.id, "⚠️ Send screenshot for at least 1 task first.", show_alert=True)
            return
        pending_list = udata.setdefault("pending_tasks", [])
        completed_list = udata.get("completed_tasks", [])
        submitted_count = 0
        skipped_count = 0
        for task_idx_str, sub in list(pack["submissions"].items()):
            task_idx = int(task_idx_str)
            if task_idx in pending_list or task_idx in completed_list:
                continue
            if task_idx < len(available_tasks):
                task = available_tasks[task_idx]
                if isinstance(task, dict):
                    claimed_id = task.get("claimed_by")
                    status = task.get("status", "available")
                    if claimed_id is None or str(claimed_id) != str(user_id) or status in ["completed", "pending_validation"]:
                        skipped_count += 1
                        continue
            task = available_tasks[task_idx]
            if isinstance(task, dict):
                task["status"] = "pending_validation"
                task["claimed_by"] = user_id
            pending_list.append(task_idx)
            udata.setdefault("proofs", {})[str(task_idx)] = sub["proof"]
            udata.setdefault("gmails", {})[str(task_idx)] = sub["creds"]
            submitted_count += 1
        pack["status"] = "submitted"
        pack["submit_time"] = time.time()
        udata["current_review_pack"] = None
        udata["reviews_today_count"] = udata.get("reviews_today_count", 0) + submitted_count
        save_data()
    skipped_msg = f"\n\n⚠️ {skipped_count} tasks in your pack were skipped because their 2-hour claim time expired and they were given to others." if skipped_count > 0 else ""
    bot.edit_message_text(f"✅ **Review Submitted!**{skipped_msg}\n\nAdmin will check it soon. Money will be added after admin approval.", call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")
    bot.send_message(call.message.chat.id, "Returned to main menu.", reply_markup=restore_menu(user_id))

@bot.callback_query_handler(func=lambda call: call.data == "cancel_review_pack")
def cancel_review_pack_handler(call):
    if not check_force_sub_and_alert(call): return
    user_id = call.from_user.id
    with data_lock:
        udata = user_data.get(user_id, {})
        pack = udata.get("current_review_pack")
        if pack:
            for task_idx in pack["task_ids"]:
                if task_idx < len(available_tasks):
                    task = available_tasks[task_idx]
                    if isinstance(task, dict) and task.get("claimed_by") is not None and str(task.get("claimed_by")) == str(user_id):
                        task["claimed_by"] = None
                        task["claim_time"] = 0
                        task["status"] = "available"
                        task["is_bulk"] = False
            udata["current_review_pack"] = None
            save_data()
    bot.edit_message_text("Review Pack cancelled.", chat_id=call.message.chat.id, message_id=call.message.message_id)
    bot.send_message(call.message.chat.id, "Returned to main menu.", reply_markup=restore_menu(user_id))

@bot.callback_query_handler(func=lambda call: call.data == "start_single_gmail")
def start_single_gmail_handler(call):
    if not check_force_sub_and_alert(call): return
    user_id = call.from_user.id
    ban_msg = is_user_banned(user_id)
    if ban_msg:
        bot.answer_callback_query(call.id, ban_msg, show_alert=True)
        return
    udata = user_data.setdefault(user_id, {"balance": 0, "tasks_completed": 0, "referrals": 0, "completed_tasks": [], "pending_tasks": []})
    if udata.get("gmails_today_count", 0) >= 30:
        bot.answer_callback_query(call.id, "⚠️ Daily limit full! You can only do 30 Gmail Tasks in 1 day.", show_alert=True)
        return
    if len(udata.get("pending_gmails", [])) >= 5:
        bot.edit_message_text("⏳ You already have 5 Gmail tasks waiting for admin approval. Please wait until admin checks them.", chat_id=call.message.chat.id, message_id=call.message.message_id)
        return
    valid_tasks = get_available_gmail_tasks_for_user(user_id)
    if len(valid_tasks) == 0:
        bot.edit_message_text("📭 *For now There is no Task Available.*\n\n📢 *When the admin adds we will notify.*\n\n👀 *Stay Tuned*", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")
        return

    msg = (
        "⚠️ *WARNING*\n\n"
        "Send the Same screenshot of Gmail account.\n\n"
        "Wrong screenshot = No payment and Account Ban !"
    )
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✅ Continue", callback_data="continue_single_gmail"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel_gmail_flow")
    )
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    send_warning_photo(call.message.chat.id, "gmail", msg, markup)

@bot.callback_query_handler(func=lambda call: call.data == "continue_single_gmail")
def continue_single_gmail_handler(call):
    if not check_force_sub_and_alert(call): return
    user_id = call.from_user.id
    ban_msg = is_user_banned(user_id)
    if ban_msg:
        bot.answer_callback_query(call.id, ban_msg, show_alert=True)
        return

    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass

    with data_lock:
        udata = user_data.setdefault(user_id, {"balance": 0, "tasks_completed": 0, "referrals": 0, "completed_tasks": [], "pending_tasks": []})
        if udata.get("gmails_today_count", 0) >= 30:
            bot.answer_callback_query(call.id, "⚠️ Daily limit full! You can only do 30 Gmail Tasks in 1 day.", show_alert=True)
            return
        if len(udata.get("pending_gmails", [])) >= 5:
            bot.send_message(call.message.chat.id, "⏳ You already have 5 Gmail tasks waiting for admin approval. Please wait until admin checks them.")
            return
        valid_tasks = get_available_gmail_tasks_for_user(user_id)
        if len(valid_tasks) == 0:
            bot.send_message(call.message.chat.id, "📭 *For now There is no Task Available.*\n\n📢 *When the admin adds we will notify.*\n\n👀 *Stay Tuned*", parse_mode="Markdown")
            return
        task_idx, task = valid_tasks[0]
        if isinstance(task, dict):
            task["claimed_by"] = user_id
            task["claim_time"] = time.time()
            task["status"] = "available"
            task["is_bulk"] = False
            save_data()

    desc = task.get("description", "No description") if isinstance(task, dict) else task
    reward = task.get("reward", bot_config.get("default_gmail_reward", 8.0)) if isinstance(task, dict) else bot_config.get("default_gmail_reward", 8.0)
    msg = (
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📧 <b>GMAIL TASK DETAILS</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{desc}\n\n"
        f"⏳ <b>Time Limit</b> → 15 Minutes\n\n"
        f"Reward: ₹{reward}"
    )
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("📤 Submit Proof", callback_data=f"complete_task_gm_{task_idx}"),
        InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_single_claim_gm_{task_idx}")
    )
    bot.send_message(call.message.chat.id, msg, parse_mode="HTML", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "start_bulk_gmail_warn")
def start_bulk_gmail_warn_handler(call):
    if not check_force_sub_and_alert(call): return
    user_id = call.from_user.id
    ban_msg = is_user_banned(user_id)
    if ban_msg:
        bot.answer_callback_query(call.id, ban_msg, show_alert=True)
        return

    udata = user_data.get(user_id, {})
    if udata.get("bulk_gmail_blocked"):
        progress = udata.get("bulk_gmail_unlock_progress", 0)
        bot.answer_callback_query(call.id, f"❌ You are blocked from Bulk Gmails. 10 of your bulk tasks were rejected. Do 10 single Gmails to unlock! ({progress}/10)", show_alert=True)
        return

    completed_gmails_count = udata.get("lifetime_gmails_completed", len(udata.get("completed_gmails", [])))
    if completed_gmails_count < 10:
        bot.answer_callback_query(call.id, f"❌ You must do 10 Single Gmail Tasks first! (Completed: {completed_gmails_count}/10)", show_alert=True)
        return

    available_count = 0
    for task in gmail_tasks:
        if is_task_available(task, user_id):
            available_count += 1
    if available_count < 10:
        bot.answer_callback_query(call.id, "❌ Not available currently", show_alert=True)
        return

    reward_val = 10 * bot_config.get("bulk_gmail_per_task", 10.0)
    msg = (
        "⚠️ *WARNING*\n\n"
        "Send the Same screenshot of Gmail account.\n\n"
        "Wrong screenshot = No payment and Account Ban !"
    )
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("✅ Continue", callback_data="confirm_bulk_gmail_10"),
        InlineKeyboardButton("❌ Cancel", callback_data="cancel_gmail_flow")
    )
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    send_warning_photo(call.message.chat.id, "gmail", msg, markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("confirm_bulk_gmail_"))
def confirm_bulk_gmail_handler(call):
    if not check_force_sub_and_alert(call): return
    user_id = call.from_user.id
    ban_msg = is_user_banned(user_id)
    if ban_msg:
        bot.answer_callback_query(call.id, ban_msg, show_alert=True)
        return

    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass

    count = int(call.data.split("_")[3])
    with data_lock:
        udata = user_data.setdefault(user_id, {"balance": 0, "tasks_completed": 0, "referrals": 0, "completed_tasks": [], "pending_tasks": []})
        if udata.get("gmails_today_count", 0) + count > 30:
            bot.answer_callback_query(call.id, f"⚠️ Daily limit exceeded! You can only complete up to {30 - udata.get('gmails_today_count', 0)} more Gmail Tasks today.", show_alert=True)
            return
        claimed_ids = []
        for idx, task in enumerate(gmail_tasks):
            if is_task_available(task, user_id) and idx not in udata.get("completed_gmails", []) and idx not in udata.get("pending_gmails", []):
                if isinstance(task, dict):
                    task["claimed_by"] = user_id
                    task["claim_time"] = time.time()
                    task["status"] = "available"
                    task["is_bulk"] = True
                claimed_ids.append(idx)
                if len(claimed_ids) == count:
                    break
        if not claimed_ids:
            bot.send_message(call.message.chat.id, "📭 *For now There is no Task Available.*\n\n📢 *When the admin adds we will notify.*\n\n👀 *Stay Tuned*", parse_mode="Markdown")
            return
        udata["current_gmail_pack"] = {
            "task_ids": claimed_ids,
            "submissions": {},
            "status": "claimed",
            "claim_time": time.time()
        }
        save_data()
    display_bulk_gmail_pack(call.message, user_id)

def display_bulk_gmail_pack(message, user_id):
    udata = user_data[user_id]
    pack = udata.get("current_gmail_pack")
    if not pack:
        bot.send_message(message.chat.id, "No active Gmail pack found.")
        return
    task_count = len(pack["task_ids"])
    reward_per_task = bot_config.get("bulk_gmail_per_task", 10.0)
    total_reward = task_count * reward_per_task
    msg = (
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"📧 <b>BULK GMAIL PACK</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 <b>Total Reward</b>: ₹{total_reward} (₹{reward_per_task} per task)\n"
        f"⏳ <b>Time Limit</b>: 2 Hours\n\n"
    )
    markup = InlineKeyboardMarkup(row_width=2)
    buttons = []
    for i, idx in enumerate(pack["task_ids"]):
        task = gmail_tasks[idx]
        desc = task.get("description", "") if isinstance(task, dict) else task
        msg += f"<b>{i+1}. Gmail Task</b>:\n{desc}\n\n"
        is_sub = str(idx) in pack["submissions"]
        btn_text = f"📤 Submit {i+1}" if not is_sub else f"✅ Submit {i+1} (Done)"
        buttons.append(InlineKeyboardButton(btn_text, callback_data=f"bsub_gm_{idx}"))
    markup.add(*buttons)
    markup.row(InlineKeyboardButton("📤 Submit Pack to Admin", callback_data="final_submit_gmail_pack"))
    markup.row(InlineKeyboardButton("❌ Cancel Pack", callback_data="cancel_gmail_pack"))
    try:
        bot.edit_message_text(msg, chat_id=message.chat.id, message_id=message.message_id, parse_mode="HTML", reply_markup=markup)
    except Exception:
        bot.send_message(message.chat.id, msg, parse_mode="HTML", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "final_submit_gmail_pack")
def final_submit_gmail_pack_handler(call):
    if not check_force_sub_and_alert(call): return
    user_id = call.from_user.id
    ban_msg = is_user_banned(user_id)
    if ban_msg:
        bot.answer_callback_query(call.id, ban_msg, show_alert=True)
        return
    with data_lock:
        udata = user_data.get(user_id, {})
        pack = udata.get("current_gmail_pack")
        if not pack or pack.get("status") == "submitted":
            bot.answer_callback_query(call.id, "No active Gmail pack or already submitted.", show_alert=True)
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
            return
        if time.time() - pack.get("claim_time", 0) > 7200:
            bot.answer_callback_query(call.id, "⚠️ Your 2-hour time is over for this Gmail pack!", show_alert=True)
            udata["current_gmail_pack"] = None
            save_data()
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
            return
        if not pack.get("submissions"):
            bot.answer_callback_query(call.id, "⚠️ Send screenshot for at least 1 task first.", show_alert=True)
            return
        pending_list = udata.setdefault("pending_gmails", [])
        completed_list = udata.get("completed_gmails", [])
        submitted_count = 0
        skipped_count = 0
        for task_idx_str, sub in list(pack["submissions"].items()):
            task_idx = int(task_idx_str)
            if task_idx in pending_list or task_idx in completed_list:
                continue
            if task_idx < len(gmail_tasks):
                task = gmail_tasks[task_idx]
                if isinstance(task, dict):
                    claimed_id = task.get("claimed_by")
                    status = task.get("status", "available")
                    if claimed_id is None or str(claimed_id) != str(user_id) or status in ["completed", "pending_validation"]:
                        skipped_count += 1
                        continue
            task = gmail_tasks[task_idx]
            if isinstance(task, dict):
                task["status"] = "pending_validation"
                task["claimed_by"] = user_id
            pending_list.append(task_idx)
            udata.setdefault("gmail_proofs", {})[str(task_idx)] = sub["proof"]
            udata.setdefault("gmail_creds", {})[str(task_idx)] = sub["creds"]
            submitted_count += 1
        pack["status"] = "submitted"
        pack["submit_time"] = time.time()
        udata["current_gmail_pack"] = None
        udata["gmails_today_count"] = udata.get("gmails_today_count", 0) + submitted_count
        save_data()
    skipped_msg = f"\n\n⚠️ {skipped_count} tasks in your pack were skipped because their 2-hour claim time expired and they were given to others." if skipped_count > 0 else ""
    bot.edit_message_text(f"✅ Yᴏᴜʀ Gᴍᴀɪʟ Hᴀs Bᴇᴇɴ Sᴜʙᴍɪᴛᴛᴇᴅ Sᴜᴄᴄᴇssғᴜʟʟʏ!{skipped_msg}\n\n⏳ Yᴏᴜʀ Gᴍᴀɪʟ Wɪʟʟ Bᴇ Vᴀʟɪᴅᴀᴛᴇᴅ Wɪᴛʜɪɴ 24–48 Hᴏᴜʀs.\n\n⚠️ Iғ Tʜᴇ Vᴀʟɪᴅᴀᴛɪᴏɴ Tᴀᴋᴇs A Lɪᴛᴛʟᴇ Lᴏɴɢᴇʀ, Pʟᴇᴀsᴇ Dᴏɴ'ᴛ Pᴀɴɪᴄ. Wᴇ Aᴘᴘʀᴇᴄɪᴀᴛᴇ Yᴏᴜʀ Pᴀᴛɪᴇɴᴄᴇ. 🙏", call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown")
    bot.send_message(call.message.chat.id, "Returned to main menu.", reply_markup=restore_menu(user_id))

@bot.callback_query_handler(func=lambda call: call.data == "cancel_gmail_pack")
def cancel_gmail_pack_handler(call):
    if not check_force_sub_and_alert(call): return
    user_id = call.from_user.id
    with data_lock:
        udata = user_data.get(user_id, {})
        pack = udata.get("current_gmail_pack")
        if pack:
            for task_idx in pack["task_ids"]:
                if task_idx < len(gmail_tasks):
                    task = gmail_tasks[task_idx]
                    if isinstance(task, dict) and task.get("claimed_by") is not None and str(task.get("claimed_by")) == str(user_id):
                        task["claimed_by"] = None
                        task["claim_time"] = 0
                        task["status"] = "available"
                        task["is_bulk"] = False
            udata["current_gmail_pack"] = None
            save_data()
    bot.edit_message_text("Gmail Pack cancelled.", chat_id=call.message.chat.id, message_id=call.message.message_id)
    bot.send_message(call.message.chat.id, "Returned to main menu.", reply_markup=restore_menu(user_id))

@bot.callback_query_handler(func=lambda call: call.data in ["cancel_review_flow", "cancel_gmail_flow"])
def cancel_flow_handler(call):
    if not check_force_sub_and_alert(call): return
    user_id = call.from_user.id
    try:
        bot.delete_message(call.message.chat.id, call.message.message_id)
    except Exception:
        pass
    bot.send_message(call.message.chat.id, "Flow cancelled. Returned to main menu.", reply_markup=restore_menu(user_id))

def release_task_claim(user_id, task_idx, task_type):
    pool = available_tasks if task_type == "rev" else gmail_tasks
    with data_lock:
        if task_idx < len(pool):
            task = pool[task_idx]
            if isinstance(task, dict) and task.get("claimed_by") is not None and str(task.get("claimed_by")) == str(user_id):
                task["claimed_by"] = None
                task["claim_time"] = 0
                task["status"] = "available"
                task["is_bulk"] = False
                save_data()

@bot.callback_query_handler(func=lambda call: call.data.startswith("complete_task_"))
def complete_task(call):
    if not check_force_sub_and_alert(call): return
    user_id = call.from_user.id
    parts = call.data.split("_")
    if len(parts) >= 4:
        task_type = parts[2]
        task_idx = int(parts[3])
    else:
        task_type = "rev"
        task_idx = int(parts[2])
    pool = available_tasks if task_type == "rev" else gmail_tasks
    ban_msg = is_user_banned(user_id)
    if ban_msg:
        bot.answer_callback_query(call.id, ban_msg, show_alert=True)
        return
    if user_id not in user_data:
        user_data[user_id] = {"balance": 0, "tasks_completed": 0, "referrals": 0, "completed_tasks": [], "pending_tasks": []}

    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    if task_type == "rev":
        msg = bot.send_message(call.message.chat.id, "Please enter the Gmail address you used to write the review:", reply_markup=get_cancel_menu())
        bot.register_next_step_handler(msg, process_task_gmail_step, task_idx, task_type)
    else:
        msg = bot.send_message(call.message.chat.id, f"Send the screenshot proof of Gmail Account for Gmail Task {task_idx+1}:", reply_markup=get_cancel_menu())
        if task_idx < len(pool):
            task = pool[task_idx]
            desc = task.get("description", "") if isinstance(task, dict) else task
            g_addr, g_pwd = parse_gmail_desc(desc)
            creds = f"Gmail: {g_addr}\nPassword: {g_pwd}"
        else:
            creds = "Gmail: Unknown\nPassword: Unknown"
        bot.register_next_step_handler(msg, process_task_proof_step, task_idx, task_type, creds)
    try:
        bot.answer_callback_query(call.id)
    except telebot.apihelper.ApiTelegramException:
        print("Callback query too old, ignoring to prevent crash.")

def process_task_gmail_step(message, task_idx, task_type):
    user_id = message.from_user.id
    if is_cancel(message):
        release_task_claim(user_id, task_idx, task_type)
        return

    gmail = message.text
    if not gmail or "@" not in gmail or "." not in gmail:
        msg = bot.send_message(message.chat.id, "⚠️ Invalid email format. Please enter a valid Email address containing '@' and '.', or press 🔙 Cancel.", reply_markup=get_cancel_menu())
        bot.register_next_step_handler(msg, process_task_gmail_step, task_idx, task_type)
        return

    ban_msg = is_user_banned(user_id)
    if ban_msg:
        bot.send_message(message.chat.id, ban_msg, reply_markup=restore_menu(user_id))
        release_task_claim(user_id, task_idx, task_type)
        return

    if task_type == "rev":
        msg = bot.send_message(message.chat.id, f"Please upload a screenshot as proof for Task {task_idx+1}:", reply_markup=get_cancel_menu())
        bot.register_next_step_handler(msg, process_task_proof_step, task_idx, task_type, gmail)
    else:
        msg = bot.send_message(message.chat.id, "Please enter the Password for this Gmail:", reply_markup=get_cancel_menu())
        bot.register_next_step_handler(msg, process_task_password_step, task_idx, task_type, gmail)

def process_task_password_step(message, task_idx, task_type, gmail_address):
    user_id = message.from_user.id
    if is_cancel(message):
        release_task_claim(user_id, task_idx, task_type)
        return

    password = message.text
    if not password:
        msg = bot.send_message(message.chat.id, "Please enter a valid password:", reply_markup=get_cancel_menu())
        bot.register_next_step_handler(msg, process_task_password_step, task_idx, task_type, gmail_address)
        return

    ban_msg = is_user_banned(user_id)
    if ban_msg:
        bot.send_message(message.chat.id, ban_msg, reply_markup=restore_menu(user_id))
        release_task_claim(user_id, task_idx, task_type)
        return

    combined_creds = f"Gmail: {gmail_address}\nPassword: {password}"
    msg = bot.send_message(message.chat.id, f"Please upload a screenshot as proof for Gmail Task {task_idx+1}:", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_task_proof_step, task_idx, task_type, combined_creds)

def process_task_proof_step(message, task_idx, task_type, creds):
    user_id = message.from_user.id
    pool = available_tasks if task_type == "rev" else gmail_tasks
    if is_cancel(message):
        release_task_claim(user_id, task_idx, task_type)
        return

    is_valid, err_msg = perform_smart_validation(message, user_id)
    if not is_valid:
        msg = bot.send_message(message.chat.id, err_msg + "\n_Please upload the screenshot again, or press 🔙 Cancel._", parse_mode="Markdown", reply_markup=get_cancel_menu())
        bot.register_next_step_handler(msg, process_task_proof_step, task_idx, task_type, creds)
        return

    is_photo = message.photo is not None and len(message.photo) > 0
    photo_file_id = message.photo[-1].file_id if is_photo else message.document.file_id
    ban_msg = is_user_banned(user_id)
    if ban_msg:
        _send_replaced_message(message.chat.id, f"🚫 {ban_msg}", restore_menu(user_id))
        release_task_claim(user_id, task_idx, task_type)
        return

    with data_lock:
        if task_idx < len(pool):
            task = pool[task_idx]
            if isinstance(task, dict):
                claimed_id = task.get("claimed_by")
                if claimed_id is None or str(claimed_id) != str(user_id):
                    _send_replaced_message(message.chat.id, "⚠️ <b>ʏᴏᴜ ʜᴀᴠᴇ ɴᴏᴛ ᴄʟᴀɪᴍᴇᴅ ᴛʜɪꜱ ᴛᴀꜱᴋ. ᴄᴀɴᴄᴇʟʟᴇᴅ.</b>", restore_menu(user_id))
                    return
                limit = 900
                if time.time() - task.get("claim_time", 0) > limit:
                    _send_replaced_message(message.chat.id, "⚠️ <b>ʏᴏᴜʀ 15-ᴍɪɴᴜᴛᴇ ᴛɪᴍᴇ ɪꜱ ᴏᴠᴇʀ. ᴄᴀɴᴄᴇʟʟᴇᴅ.</b>", restore_menu(user_id))
                    return

        if user_id not in user_data:
            user_data[user_id] = {"balance": 0, "tasks_completed": 0, "referrals": 0, "completed_tasks": [], "pending_tasks": []}

        pending_key = "pending_tasks" if task_type == "rev" else "pending_gmails"
        completed_key = "completed_tasks" if task_type == "rev" else "completed_gmails"
        pending_list = user_data[user_id].setdefault(pending_key, [])
        completed_list = user_data[user_id].setdefault(completed_key, [])

        if task_idx in pending_list or task_idx in completed_list:
            _send_replaced_message(message.chat.id, "⚠️ <b>ʏᴏᴜ ᴀʟʀᴇᴀᴅʏ ꜱᴜʙᴍɪᴛᴛᴇᴅ ᴛʜɪꜱ ᴛᴀꜱᴋ.</b>", restore_menu(user_id))
            return

        pending_list.append(task_idx)
        proofs = user_data[user_id].setdefault("proofs" if task_type == "rev" else "gmail_proofs", {})
        proofs[str(task_idx)] = photo_file_id

        gmails = user_data[user_id].setdefault("gmails" if task_type == "rev" else "gmail_creds", {})
        gmails[str(task_idx)] = creds
        user_data[user_id]["first_name"] = message.from_user.first_name or "User"
        if message.from_user.username:
            user_data[user_id]["username"] = message.from_user.username

        if task_idx < len(pool):
            task = pool[task_idx]
            if isinstance(task, dict):
                task["status"] = "pending_validation"
                task["claimed_by"] = user_id

        if task_type == "rev":
            user_data[user_id]["reviews_today_count"] = user_data[user_id].get("reviews_today_count", 0) + 1
        else:
            user_data[user_id]["gmails_today_count"] = user_data[user_id].get("gmails_today_count", 0) + 1

        save_data()

    try: bot.delete_message(message.chat.id, message.message_id)
    except: pass

    markup = InlineKeyboardMarkup(row_width=1)
    next_callback = "start_single_review" if task_type == "rev" else "start_single_gmail"
    markup.add(
        premium_button("⏭️ ɴᴇxᴛ ᴛᴀꜱᴋ", callback_data=next_callback),
        premium_button("ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ", callback_data="ui_home")
    )
    
    success_text = (
        f"༶•┈┈⛧┈♛\n"
        f"✅ <b>ꜱ ᴜ ʙ ᴍ ɪ ꜱ ꜱ ɪ ᴏ ɴ  ꜱ ᴜ ᴄ ᴄ ᴇ ꜱ ꜱ ꜰ ᴜ ʟ</b> 🎉\n"
        f"────── ⋆⋅☆⋅⋆ ──────\n\n"
        f"✨ ʏᴏᴜʀ ᴘʀᴏᴏꜰ ʜᴀꜱ ʙᴇᴇɴ ꜱᴇɴᴛ ᴛᴏ ᴀᴅᴍɪɴꜱ.\n"
        f"💰 ʀᴇᴡᴀʀᴅ ᴡɪʟʟ ʙᴇ ᴀᴅᴅᴇᴅ ᴀꜰᴛᴇʀ ᴀᴘᴘʀᴏᴠᴀʟ.\n\n"
        f"꘎♡━━━━━♡꘎━━━━━♡꘎\n"
        f"👇 <b>ᴄʟɪᴄᴋ ʙᴇʟᴏᴡ ᴛᴏ ᴅᴏ ᴀɴᴏᴛʜᴇʀ ᴛᴀꜱᴋ:</b>"
    )
    _send_replaced_message(message.chat.id, success_text, reply_markup=markup, parse_mode="HTML")

def process_bulk_gmail_step(message, task_idx, task_type):
    user_id = message.from_user.id
    if is_cancel(message):
        return

    gmail = message.text
    if not gmail or "@" not in gmail or "." not in gmail:
        msg = bot.send_message(message.chat.id, "⚠️ Invalid email format. Please enter a valid Email address containing '@' and '.', or press 🔙 Cancel.", reply_markup=get_cancel_menu())
        bot.register_next_step_handler(msg, process_bulk_gmail_step, task_idx, task_type)
        return

    ban_msg = is_user_banned(user_id)
    if ban_msg:
        bot.send_message(message.chat.id, ban_msg, reply_markup=restore_menu(user_id))
        return

    if task_type == "rev":
        msg = bot.send_message(message.chat.id, f"Please upload a screenshot as proof for Task {task_idx+1}:", reply_markup=get_cancel_menu())
        bot.register_next_step_handler(msg, process_bulk_proof_step, task_idx, task_type, gmail)
    else:
        msg = bot.send_message(message.chat.id, f"Please enter the Password for Gmail Task {task_idx+1}:", reply_markup=get_cancel_menu())
        bot.register_next_step_handler(msg, process_bulk_password_step, task_idx, task_type, gmail)

def process_bulk_password_step(message, task_idx, task_type, gmail_address):
    user_id = message.from_user.id
    if is_cancel(message):
        return

    password = message.text
    if not password:
        msg = bot.send_message(message.chat.id, "Please enter a valid password:", reply_markup=get_cancel_menu())
        bot.register_next_step_handler(msg, process_bulk_password_step, task_idx, task_type, gmail_address)
        return

    ban_msg = is_user_banned(user_id)
    if ban_msg:
        bot.send_message(message.chat.id, ban_msg, reply_markup=restore_menu(user_id))
        return

    combined_creds = f"Gmail: {gmail_address}\nPassword: {password}"
    msg = bot.send_message(message.chat.id, f"Please upload a screenshot as proof for Gmail Task {task_idx+1}:", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_bulk_proof_step, task_idx, task_type, combined_creds)

def process_bulk_proof_step(message, task_idx, task_type, creds):
    user_id = message.from_user.id
    if is_cancel(message):
        return

    ban_msg = is_user_banned(user_id)
    if ban_msg:
        bot.send_message(message.chat.id, ban_msg, reply_markup=restore_menu(user_id))
        return

    pool = available_tasks if task_type == "rev" else gmail_tasks
    if task_idx < len(pool):
        task = pool[task_idx]
        if isinstance(task, dict):
            claimed_id = task.get("claimed_by")
            if claimed_id is None or str(claimed_id) != str(user_id):
                bot.send_message(
                    message.chat.id,
                    f"⚠️ The 2-hour time limit for Task {task_idx+1} has expired, and it has been given to another worker.",
                    reply_markup=restore_menu(user_id)
                )
                return

    is_valid, err_msg = perform_smart_validation(message, user_id)
    if not is_valid:
        bot.send_message(message.chat.id, err_msg)
        if task_type == "rev":
            display_bulk_review_pack(message, user_id)
        else:
            display_bulk_gmail_pack(message, user_id)
        return

    is_photo = message.photo is not None and len(message.photo) > 0
    photo_file_id = message.photo[-1].file_id if is_photo else message.document.file_id

    udata = user_data.setdefault(user_id, {"balance": 0, "tasks_completed": 0, "referrals": 0, "completed_tasks": [], "pending_tasks": []})
    pack_key = "current_review_pack" if task_type == "rev" else "current_gmail_pack"
    pack = udata.get(pack_key)
    if not pack:
        bot.send_message(message.chat.id, "No active pack found.", reply_markup=restore_menu(user_id))
        return

    pack.setdefault("submissions", {})[str(task_idx)] = {
        "proof": photo_file_id,
        "creds": creds
    }
    save_data()

    task_name = "Task" if task_type == "rev" else "Gmail Task"
    bot.send_message(message.chat.id, f"✅ Submission for {task_name} {task_idx+1} saved!")

    if task_type == "rev":
        display_bulk_review_pack(message, user_id)
    else:
        display_bulk_gmail_pack(message, user_id)

@bot.callback_query_handler(func=lambda call: call.data in ["admin_val_single_rev", "admin_val_single_gm"])
def handle_single_validation(call):
    if call.from_user.id not in ADMIN_IDS: return
    task_type = "rev" if "rev" in call.data else "gm"
    pending_key = "pending_tasks" if task_type == "rev" else "pending_gmails"

    task_counts = {}
    for uid, udata in list(user_data.items()):
        pending_list = udata.get(pending_key, [])
        for task_idx in pending_list:
            proofs_key = "proofs" if task_type == "rev" else "gmail_proofs"
            if udata.get(proofs_key, {}).get(str(task_idx)):
                task_counts[task_idx] = task_counts.get(task_idx, 0) + 1

    if not task_counts:
        bot.answer_callback_query(call.id, "No pending validations.", show_alert=True)
        return

    markup = InlineKeyboardMarkup(row_width=1)
    for task_idx, count in sorted(task_counts.items()):
        btn = InlineKeyboardButton(f"Task ID {task_idx + 1} ({count} pending)", callback_data=f"val_task_{task_type}_{task_idx}")
        markup.add(btn)

    markup.add(InlineKeyboardButton("🔙 Back", callback_data="admin_review_panel" if task_type == "rev" else "admin_gmail_panel"))
    bot.edit_message_text(f"Select a {task_type.upper()} task to validate:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("val_task_"))
def handle_validation_task(call):
    if call.from_user.id not in ADMIN_IDS: return

    parts = call.data.split("_")
    if len(parts) >= 4:
        task_type = parts[2]
        task_idx = int(parts[3])
    else:
        task_type = "rev"
        task_idx = int(parts[2])

    pool = available_tasks if task_type == "rev" else gmail_tasks
    pending_key = "pending_tasks" if task_type == "rev" else "pending_gmails"
    proofs_key = "proofs" if task_type == "rev" else "gmail_proofs"
    creds_key = "gmails" if task_type == "rev" else "gmail_creds"

    bot.answer_callback_query(call.id, "Fetching submissions...")

    sent_count = 0
    for uid, udata in list(user_data.items()):
        if task_idx in udata.get(pending_key, []):
            file_id = udata.get(proofs_key, {}).get(str(task_idx))
            if not file_id:
                continue

            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("✅ Approve", callback_data=f"approve_{task_type}_{uid}_{task_idx}"),
                       InlineKeyboardButton("❌ Reject", callback_data=f"reject_{task_type}_{uid}_{task_idx}"))
            markup.add(InlineKeyboardButton("🚫 Ban", callback_data=f"ban_{task_type}_{uid}_{task_idx}"))
            if task_type == "gm":
                markup.add(InlineKeyboardButton("🤖 Bot Mistake", callback_data=f"botmistake_gm_{uid}_{task_idx}"))

            if task_idx < len(pool):
                task = pool[task_idx]
                default_r = bot_config.get("default_reward", 8.0) if task_type == "rev" else bot_config.get("default_gmail_reward", 8.0)
                reward = task.get("reward", default_r) if isinstance(task, dict) else default_r
                task_desc = task.get("description", "No description") if isinstance(task, dict) else task
            else:
                reward = 0
                task_desc = "Unknown"

            first_name = udata.get("first_name", "User")
            first_name_safe = html.escape(first_name)
            username = udata.get("username")
            username_display = f" (@{html.escape(username)})" if username else " (No username)"
            gmail = udata.get(creds_key, {}).get(str(task_idx), "Not provided")

            caption = (
                f"<b>Task Submission 📸 ({task_type.upper()})</b>\n"
                f"Name: {first_name_safe}{username_display}\n"
                f"User ID: <code>{uid}</code>\n"
                f"Task ID: {task_idx + 1}\n\n"
                f"<b>Task Details:</b>\n{task_desc}\n\n"
                f"<b>User Submitted:</b>\n{html.escape(gmail)}\n\n"
                f"<b>Reward:</b> ₹{reward}"
            )

            send_proof(call.message.chat.id, file_id, caption, markup)
            sent_count += 1
            time.sleep(0.1)

    bot.send_message(call.message.chat.id, f"✅ Sent {sent_count} submissions for Task ID {task_idx + 1}.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("approve_"))
def admin_approve_task(call):
    if call.from_user.id not in ADMIN_IDS: return
    parts = call.data.split("_")
    if len(parts) >= 4:
        task_type = parts[1]
        user_id = int(parts[2])
        task_idx = int(parts[3])
    else:
        task_type = "rev"
        user_id = int(parts[1])
        task_idx = int(parts[2])

    pending_key = "pending_tasks" if task_type == "rev" else "pending_gmails"
    completed_key = "completed_tasks" if task_type == "rev" else "completed_gmails"
    ts_key = "completed_tasks_ts" if task_type == "rev" else "completed_gmails_ts"
    pool = available_tasks if task_type == "rev" else gmail_tasks

    with data_lock:
        if user_id not in user_data: return

        pending_list = user_data[user_id].get(pending_key, [])
        if task_idx not in pending_list:
            bot.answer_callback_query(call.id, "Submission is no longer pending.", show_alert=True)
            return

        completed_list = user_data[user_id].setdefault(completed_key, [])
        if task_idx in completed_list: return

        global last_task_approved_time
        if task_idx < len(pool):
            task = pool[task_idx]
            if isinstance(task, dict): task["status"] = "completed"
            default_r = bot_config.get("default_reward", 8.0) if task_type == "rev" else bot_config.get("default_gmail_reward", 8.0)
            reward = task.get("reward", default_r) if isinstance(task, dict) else default_r

            user_data[user_id]["balance"] += reward
            user_data[user_id]["tasks_completed"] += 1
            user_data[user_id].setdefault(ts_key, []).append((time.time(), reward))
            earned_key = "earned_from_reviews" if task_type == "rev" else "earned_from_gmails"
            user_data[user_id][earned_key] = user_data[user_id].get(earned_key, 0) + reward
            completed_list.append(task_idx)
            lifetime_key = "lifetime_reviews_completed" if task_type == "rev" else "lifetime_gmails_completed"
            if lifetime_key not in user_data[user_id]:
                user_data[user_id][lifetime_key] = len(completed_list) - 1
            user_data[user_id][lifetime_key] += 1

            # Increment unlock progress for bulk tasks if blocked
            blocked_key = "bulk_review_blocked" if task_type == "rev" else "bulk_gmail_blocked"
            progress_key = "bulk_review_unlock_progress" if task_type == "rev" else "bulk_gmail_unlock_progress"
            rejections_key = "bulk_review_rejections" if task_type == "rev" else "bulk_gmail_rejections"
            if user_data[user_id].get(blocked_key):
                user_data[user_id][progress_key] = user_data[user_id].get(progress_key, 0) + 1
                if user_data[user_id][progress_key] >= 10:
                    user_data[user_id][blocked_key] = False
                    user_data[user_id][progress_key] = 0
                    user_data[user_id][rejections_key] = 0
                    try:
                        task_name_display = "Review" if task_type == "rev" else "Gmail"
                        bot.send_message(user_id, f"🎉 **Unlocked!** You completed 10 single tasks. You can now do **Bulk {task_name_display} Tasks** again!", parse_mode="Markdown")
                    except:
                        pass
            pending_list.remove(task_idx)

            last_task_approved_time = time.time()
            log_admin_action("approve_task", {"user_id": user_id, "task_idx": task_idx, "task_type": task_type, "reward": reward})
            save_data()
            check_and_reward_referrer(user_id)

        try: bot.send_message(user_id, f"✅ Your proof has been approved!\n₹{reward} added to your wallet.")
        except: pass

        markup = InlineKeyboardMarkup()
        markup.add(InlineKeyboardButton("↩️ Undo & Reject", callback_data=f"undo_app_{task_type}_{user_id}_{task_idx}"))
        udata = user_data.get(user_id, {})
        first_name = udata.get("first_name", "User")
        first_name_safe = html.escape(first_name)
        username = udata.get("username")
        username_display = f" (@{html.escape(username)})" if username else " (No username)"
        caption_text = (
            f"✅ Approved\n"
            f"Name: {first_name_safe}{username_display}\n"
            f"User ID: <code>{user_id}</code>\n"
            f"Task ID: {task_idx + 1}\n"
            f"Reward: ₹{reward} added."
        )
        try:
            if call.message.content_type in ['photo', 'document']:
                bot.edit_message_caption(caption_text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
            else:
                bot.edit_message_text(caption_text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
        except Exception:
            pass
        bot.answer_callback_query(call.id, "Task Approved.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("reject_"))
def admin_reject_task(call):
    if call.from_user.id not in ADMIN_IDS: return
    parts = call.data.split("_")
    if len(parts) >= 4:
        task_type = parts[1]
        user_id = int(parts[2])
        task_idx = int(parts[3])
    else:
        task_type = "rev"
        user_id = int(parts[1])
        task_idx = int(parts[2])

    pending_key = "pending_tasks" if task_type == "rev" else "pending_gmails"
    if user_id not in user_data: return
    if task_idx not in user_data[user_id].get(pending_key, []):
        bot.answer_callback_query(call.id, "Already processed.", show_alert=True)
        return

    msg = bot.send_message(call.message.chat.id, f"Please enter the reason for rejecting Task ID {task_idx + 1} for user {user_id}:", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_rejection_reason, user_id, task_idx, task_type, call.message)
    bot.answer_callback_query(call.id, "Awaiting rejection reason...")

def process_rejection_reason(message, user_id, task_idx, task_type, original_message):
    if is_cancel(message): return
    reason = message.text or "No reason provided"

    pending_key = "pending_tasks" if task_type == "rev" else "pending_gmails"
    pool = available_tasks if task_type == "rev" else gmail_tasks

    if user_id in user_data and task_idx not in user_data[user_id].get(pending_key, []):
        bot.send_message(message.chat.id, "⚠️ Already processed.", reply_markup=restore_menu(message.from_user.id))
        return

    pending_list = user_data[user_id].get(pending_key, [])
    if task_idx in pending_list: pending_list.remove(task_idx)

    rejected_key = "rejected_tasks" if task_type == "rev" else "rejected_gmails"
    rejected_list = user_data[user_id].setdefault(rejected_key, [])
    if task_idx not in rejected_list:
        rejected_list.append(task_idx)

    user_data[user_id]["rejection_count"] = user_data[user_id].get("rejection_count", 0) + 1
    rejection_count = user_data[user_id]["rejection_count"]

    if task_idx < len(pool):
        task = pool[task_idx]
        if isinstance(task, dict):
            task["status"] = "available"
            task["claim_time"] = 0
            task["claimed_by"] = None
    log_admin_action("reject_task", {"user_id": user_id, "task_idx": task_idx, "task_type": task_type, "reason": reason})
    save_data()

    if rejection_count >= 3:
        user_data[user_id]["banned_until"] = time.time() + (2 * 86400)
        user_data[user_id]["rejection_count"] = 0
        log_admin_action("temporary_ban", {"user_id": user_id, "days": 2, "reason": "3 rejection strikes"})
        save_data()
        try: bot.send_message(user_id, f"❌ Proof rejected. Reason: {reason}\n\n🚫 Temporarily banned for 2 days due to 3 rejections.")
        except: pass
    else:
        try: bot.send_message(user_id, f"❌ Proof rejected. Reason: {reason}\n\n⚠️ Rejection #{rejection_count}. 3 rejections = ban.")
        except: pass

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("↩️ Undo & Approve", callback_data=f"undo_rej_{task_type}_{user_id}_{task_idx}"))
    udata = user_data.get(user_id, {})
    first_name = udata.get("first_name", "User")
    first_name_safe = html.escape(first_name)
    username = udata.get("username")
    username_display = f" (@{html.escape(username)})" if username else " (No username)"
    caption_text = (
        f"❌ Rejected\n"
        f"Name: {first_name_safe}{username_display}\n"
        f"User ID: <code>{user_id}</code>\n"
        f"Task ID: {task_idx + 1}\n"
        f"Reason: {html.escape(reason[:50])}\n"
        f"Rejections: {rejection_count}"
    )
    try:
        if original_message.content_type in ['photo', 'document']:
            bot.edit_message_caption(caption_text, chat_id=original_message.chat.id, message_id=original_message.message_id, reply_markup=markup, parse_mode="HTML")
        else:
            bot.edit_message_text(caption_text, chat_id=original_message.chat.id, message_id=original_message.message_id, reply_markup=markup, parse_mode="HTML")
    except Exception:
        pass
    bot.send_message(original_message.chat.id, "Rejected.", reply_markup=restore_menu(message.from_user.id))

@bot.callback_query_handler(func=lambda call: call.data.startswith("botmistake_"))
def admin_bot_mistake_task(call):
    if call.from_user.id not in ADMIN_IDS: return
    parts = call.data.split("_")
    task_type = parts[1]
    user_id = int(parts[2])
    task_idx = int(parts[3])

    pending_key = "pending_tasks" if task_type == "rev" else "pending_gmails"
    pool = available_tasks if task_type == "rev" else gmail_tasks

    with data_lock:
        if user_id not in user_data: return
        pending_list = user_data[user_id].get(pending_key, [])
        if task_idx not in pending_list:
            bot.answer_callback_query(call.id, "Already processed.", show_alert=True)
            return

        pending_list.remove(task_idx)

        if task_idx < len(pool):
            task = pool[task_idx]
            if isinstance(task, dict):
                task["active"] = False
                task["deleted"] = True
                task["status"] = "deleted"

        log_admin_action("bot_mistake_task", {"user_id": user_id, "task_idx": task_idx, "task_type": task_type})
        save_data()

    task_desc = ""
    if task_idx < len(pool):
        task = pool[task_idx]
        task_desc = task.get("description", "") if isinstance(task, dict) else task

    sorry_msg = (
        f"⚠️ <b>Notice about Gmail Task:</b>\n"
        f"{task_desc}\n\n"
        f"Sorry! The Gmail details we gave you were already used by someone else."
    )
    try:
        bot.send_message(user_id, sorry_msg, parse_mode="HTML")
    except:
        pass

    try:
        new_caption = f"🤖 Bot Mistake Selected\nUser ID: {user_id}\nTask ID: {task_idx + 1}"
        if call.message.content_type in ['photo', 'document']:
            bot.edit_message_caption(new_caption, chat_id=call.message.chat.id, message_id=call.message.message_id)
        else:
            bot.edit_message_text(new_caption, chat_id=call.message.chat.id, message_id=call.message.message_id)
    except Exception:
        pass
    bot.answer_callback_query(call.id, "Marked as bot mistake.")

@bot.callback_query_handler(func=lambda call: call.data in ["admin_val_bulk_rev", "admin_val_bulk_gm"])
def handle_bulk_validation_menu(call):
    if call.from_user.id not in ADMIN_IDS: return
    task_type = "rev" if "rev" in call.data else "gm"
    pending_key = "pending_tasks" if task_type == "rev" else "pending_gmails"

    user_counts = {}
    for uid, udata in user_data.items():
        pending = udata.get(pending_key, [])
        if len(pending) > 0: user_counts[uid] = len(pending)

    if not user_counts:
        bot.answer_callback_query(call.id, "No bulk validations pending.", show_alert=True)
        return

    markup = InlineKeyboardMarkup(row_width=1)
    for uid, count in user_counts.items():
        udata = user_data[uid]
        first_name = udata.get("first_name", "User")
        username = udata.get("username")
        username_display = f" (@{username})" if username else ""
        markup.add(InlineKeyboardButton(f"👤 {first_name}{username_display} ({uid}) - {count} pending", callback_data=f"val_bulk_{task_type}_{uid}"))

    markup.add(InlineKeyboardButton("🔙 Back", callback_data="admin_review_panel" if task_type == "rev" else "admin_gmail_panel"))
    bot.edit_message_text(f"📦 Select a user to validate their {'Review' if task_type == 'rev' else 'Gmail'} pack:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("val_bulk_"))
def handle_bulk_validation_user(call):
    if call.from_user.id not in ADMIN_IDS: return
    parts = call.data.split("_")
    task_type = parts[2]
    uid = int(parts[3])

    udata = user_data.get(uid, {})
    pending_key = "pending_tasks" if task_type == "rev" else "pending_gmails"
    proofs_key = "proofs" if task_type == "rev" else "gmail_proofs"
    creds_key = "gmails" if task_type == "rev" else "gmail_creds"

    pending_list = list(udata.get(pending_key, []))
    if not pending_list:
        bot.answer_callback_query(call.id, "No pending tasks.", show_alert=True)
        return

    bot.answer_callback_query(call.id, "Loading pack...")

    first_name = udata.get("first_name", "User")
    first_name_safe = html.escape(first_name)
    username = udata.get("username")
    username_display_safe = f" (@{html.escape(username)})" if username else " (No username)"

    pool = available_tasks if task_type == "rev" else gmail_tasks

    for task_idx in pending_list[:10]:
        file_id = udata.get(proofs_key, {}).get(str(task_idx))
        cred = udata.get(creds_key, {}).get(str(task_idx), "N/A")

        if task_idx < len(pool):
            task = pool[task_idx]
            if task_type == "rev":
                default_reward = bot_config.get("bulk_review_per_task", 10.0) if len(pending_list) >= 5 else bot_config.get("default_reward", 8.0)
            else:
                default_reward = bot_config.get("bulk_gmail_per_task", 10.0) if len(pending_list) >= 5 else bot_config.get("default_gmail_reward", 8.0)
            reward = task.get("reward", default_reward) if isinstance(task, dict) else default_reward
            task_desc = task.get("description", "No description") if isinstance(task, dict) else task
        else:
            reward = 0
            task_desc = "Unknown"

        caption = (
            f"<b>Task ID {task_idx+1} Submission ({task_type.upper()})</b>\n"
            f"Name: {first_name_safe}{username_display_safe}\n"
            f"User ID: <code>{uid}</code>\n\n"
            f"<b>Task Details:</b>\n{task_desc}\n\n"
            f"<b>User Submitted:</b>\n{html.escape(str(cred))}\n\n"
            f"<b>Reward:</b> ₹{reward}"
        )

        markup = InlineKeyboardMarkup()
        markup.add(
            InlineKeyboardButton("✅ Approve", callback_data=f"bapp_{task_type}_{uid}_{task_idx}"),
            InlineKeyboardButton("❌ Reject", callback_data=f"brej_{task_type}_{uid}_{task_idx}")
        )

        if file_id:
            send_proof(call.message.chat.id, file_id, caption, markup)
        else:
            bot.send_message(call.message.chat.id, caption + "\n\n<i>[⚠️ User's Proof Image Error]</i>", reply_markup=markup, parse_mode="HTML")
        time.sleep(0.1)

    pack_details = (
        f"📦 <b>Bulk { 'Review' if task_type == 'rev' else 'Gmail' } Pack Summary</b>\n\n"
        f"👤 <b>User Details:</b>\n"
        f"Name: {first_name_safe}{username_display_safe}\n"
        f"ID: <code>{uid}</code>\n"
        f"Total Pending in Pack: {len(pending_list)}\n"
    )

    summary_markup = InlineKeyboardMarkup(row_width=2)
    summary_markup.add(
        InlineKeyboardButton("✅ Approve All", callback_data=f"app_pack_{task_type}_{uid}"),
        InlineKeyboardButton("❌ Reject All", callback_data=f"rej_pack_{task_type}_{uid}")
    )
    summary_markup.add(InlineKeyboardButton("🚫 Ban User", callback_data=f"ban_{task_type}_{uid}_0"))

    bot.send_message(call.message.chat.id, pack_details, parse_mode="HTML", reply_markup=summary_markup)

def update_bulk_validation_message(chat_id, message_id, task_type, uid):
    udata = user_data.get(uid, {})
    pending_key = "pending_tasks" if task_type == "rev" else "pending_gmails"
    pending_list = udata.get(pending_key, [])

    if not pending_list:
        first_name = udata.get("first_name", "User")
        first_name_safe = html.escape(first_name)
        username = udata.get("username")
        username_display = f" (@{html.escape(username)})" if username else " (No username)"
        bot.edit_message_text(f"✅ All tasks in this pack for {first_name_safe}{username_display} (<code>{uid}</code>) have been processed.", chat_id=chat_id, message_id=message_id, parse_mode="HTML")
        return

    markup = InlineKeyboardMarkup(row_width=2)
    for task_idx in pending_list[:10]:
        markup.add(
            InlineKeyboardButton(f"✅ App {task_idx+1}", callback_data=f"bapp_{task_type}_{uid}_{task_idx}"),
            InlineKeyboardButton(f"❌ Rej {task_idx+1}", callback_data=f"brej_{task_type}_{uid}_{task_idx}")
        )
    markup.add(InlineKeyboardButton("✅ Approve All", callback_data=f"app_pack_{task_type}_{uid}"),
               InlineKeyboardButton("❌ Reject All", callback_data=f"rej_pack_{task_type}_{uid}"))
    markup.add(InlineKeyboardButton("🚫 Ban User", callback_data=f"ban_{task_type}_{uid}_0"))

    try: bot.edit_message_reply_markup(chat_id=chat_id, message_id=message_id, reply_markup=markup)
    except: pass

@bot.callback_query_handler(func=lambda call: call.data.startswith("bapp_"))
def admin_bulk_approve_single(call):
    if call.from_user.id not in ADMIN_IDS: return
    parts = call.data.split("_")
    task_type = parts[1]
    uid = int(parts[2])
    task_idx = int(parts[3])

    udata = user_data.get(uid, {})
    pending_key = "pending_tasks" if task_type == "rev" else "pending_gmails"
    completed_key = "completed_tasks" if task_type == "rev" else "completed_gmails"
    ts_key = "completed_tasks_ts" if task_type == "rev" else "completed_gmails_ts"
    pool = available_tasks if task_type == "rev" else gmail_tasks

    with data_lock:
        pending_list = udata.get(pending_key, [])
        if task_idx not in pending_list:
            bot.answer_callback_query(call.id, "Already processed.")
            return

        if task_idx < len(pool):
            task = pool[task_idx]
            if isinstance(task, dict): task["status"] = "completed"

            default_r = bot_config.get("default_reward", 8.0) if task_type == "rev" else bot_config.get("default_gmail_reward", 8.0)
            reward = task.get("reward", default_r) if isinstance(task, dict) else default_r

            udata["balance"] += reward
            udata["tasks_completed"] += 1
            earned_key = "earned_from_reviews" if task_type == "rev" else "earned_from_gmails"
            udata[earned_key] = udata.get(earned_key, 0) + reward
            udata.setdefault(completed_key, []).append(task_idx)
            lifetime_key = "lifetime_reviews_completed" if task_type == "rev" else "lifetime_gmails_completed"
            if lifetime_key not in udata:
                udata[lifetime_key] = len(udata.get(completed_key, [])) - 1
            udata[lifetime_key] += 1
            udata.setdefault(ts_key, []).append((time.time(), reward))
            pending_list.remove(task_idx)
            log_admin_action("approve_task_bulk", {"user_id": uid, "task_idx": task_idx, "task_type": task_type, "reward": reward})
            save_data()
            check_and_reward_referrer(uid)

        try: bot.send_message(uid, f"✅ Task {task_idx+1} approved! ₹{reward} added.")
        except: pass

    bot.answer_callback_query(call.id, f"Task {task_idx+1} Approved!")

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("↩️ Undo & Reject", callback_data=f"undo_app_{task_type}_{uid}_{task_idx}"))

    caption_text = call.message.caption or call.message.text or ""
    new_caption = f"✅ Approved\n" + caption_text.replace("✅ Approved\n", "").replace("❌ Rejected\n", "")

    try:
        if call.message.content_type in ['photo', 'document']:
            bot.edit_message_caption(new_caption, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
        else:
            bot.edit_message_text(new_caption, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
    except Exception:
        pass

@bot.callback_query_handler(func=lambda call: call.data.startswith("brej_"))
def admin_bulk_reject_single(call):
    if call.from_user.id not in ADMIN_IDS: return
    parts = call.data.split("_")
    task_type = parts[1]
    uid = int(parts[2])
    task_idx = int(parts[3])

    udata = user_data.get(uid, {})
    pending_key = "pending_tasks" if task_type == "rev" else "pending_gmails"
    with data_lock:
        pending_list = udata.get(pending_key, [])

        if task_idx in pending_list:
            pending_list.remove(task_idx)
            udata["rejection_count"] = udata.get("rejection_count", 0) + 1

            # Track bulk task single rejections
            blocked_key = "bulk_review_blocked" if task_type == "rev" else "bulk_gmail_blocked"
            rejections_key = "bulk_review_rejections" if task_type == "rev" else "bulk_gmail_rejections"
            progress_key = "bulk_review_unlock_progress" if task_type == "rev" else "bulk_gmail_unlock_progress"
            if not udata.get(blocked_key):
                udata[rejections_key] = udata.get(rejections_key, 0) + 1
                if udata[rejections_key] >= 10:
                    udata[blocked_key] = True
                    udata[rejections_key] = 0
                    udata[progress_key] = 0
                    try:
                        task_name_display = "Review" if task_type == "rev" else "Gmail"
                        bot.send_message(uid, f"🚫 **Blocked from Bulk Tasks!** 10 of your bulk tasks were rejected. You cannot do **Bulk {task_name_display} Tasks** now. Do 10 single {task_name_display} tasks to unlock!", parse_mode="Markdown")
                    except:
                        pass

            rejected_key = "rejected_tasks" if task_type == "rev" else "rejected_gmails"
            rejected_list = udata.setdefault(rejected_key, [])
            if task_idx not in rejected_list:
                rejected_list.append(task_idx)

            pool = available_tasks if task_type == "rev" else gmail_tasks
            if task_idx < len(pool):
                task = pool[task_idx]
                if isinstance(task, dict):
                    task["status"] = "available"
                    task["claim_time"] = 0
                    task["claimed_by"] = None

            log_admin_action("reject_task_bulk", {"user_id": uid, "task_idx": task_idx, "task_type": task_type})
            save_data()

        try: bot.send_message(uid, f"❌ Task {task_idx+1} rejected by Admin.")
        except: pass

    bot.answer_callback_query(call.id, f"Task {task_idx+1} Rejected.")

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("↩️ Undo & Approve", callback_data=f"undo_rej_{task_type}_{uid}_{task_idx}"))

    caption_text = call.message.caption or call.message.text or ""
    new_caption = f"❌ Rejected\n" + caption_text.replace("✅ Approved\n", "").replace("❌ Rejected\n", "")

    try:
        if call.message.content_type in ['photo', 'document']:
            bot.edit_message_caption(new_caption, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
        else:
            bot.edit_message_text(new_caption, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
    except Exception:
        pass

@bot.callback_query_handler(func=lambda call: call.data.startswith("app_pack_"))
def admin_approve_pack(call):
    if call.from_user.id not in ADMIN_IDS: return
    parts = call.data.split("_")
    task_type = parts[2]
    uid = int(parts[3])

    udata = user_data.get(uid, {})
    pending_key = "pending_tasks" if task_type == "rev" else "pending_gmails"
    completed_key = "completed_tasks" if task_type == "rev" else "completed_gmails"
    ts_key = "completed_tasks_ts" if task_type == "rev" else "completed_gmails_ts"
    earned_key = "earned_from_reviews" if task_type == "rev" else "earned_from_gmails"
    pool = available_tasks if task_type == "rev" else gmail_tasks

    with data_lock:
        pending_list = udata.get(pending_key, [])
        if not pending_list:
            bot.answer_callback_query(call.id, "Already processed.", show_alert=True)
            return

        total_reward = 0
        tasks_approved = 0
        initial_count = len(pending_list)

        for task_idx in list(pending_list):
            if task_idx < len(pool):
                task = pool[task_idx]
                if isinstance(task, dict): task["status"] = "completed"

                if task_type == "rev":
                    reward = bot_config.get("bulk_review_per_task", 10.0) if initial_count >= 5 else bot_config.get("default_reward", 8.0)
                else:
                    reward = bot_config.get("bulk_gmail_per_task", 10.0) if initial_count >= 5 else bot_config.get("default_gmail_reward", 8.0)

                total_reward += reward
                tasks_approved += 1

                udata.setdefault(completed_key, []).append(task_idx)
                lifetime_key = "lifetime_reviews_completed" if task_type == "rev" else "lifetime_gmails_completed"
                if lifetime_key not in udata:
                    udata[lifetime_key] = len(udata.get(completed_key, [])) - 1
                udata[lifetime_key] += 1
                udata.setdefault(ts_key, []).append((time.time(), reward))
                pending_list.remove(task_idx)

        udata["balance"] += total_reward
        udata["tasks_completed"] += tasks_approved
        udata[earned_key] = udata.get(earned_key, 0) + total_reward
        log_admin_action("approve_pack", {"user_id": uid, "task_type": task_type, "tasks_count": tasks_approved, "total_reward": total_reward})
        save_data()
        check_and_reward_referrer(uid)

    try: bot.send_message(uid, f"✅ **Your Pack of {tasks_approved} {'Review' if task_type=='rev' else 'Gmail'} Tasks has been approved!**\n\n₹{total_reward} has been added to your wallet.", parse_mode="Markdown")
    except: pass
    first_name = udata.get("first_name", "User")
    first_name_safe = html.escape(first_name)
    username = udata.get("username")
    username_display = f" (@{html.escape(username)})" if username else " (No username)"
    bot.edit_message_text(f"✅ Pack Approved for user {first_name_safe}{username_display} (<code>{uid}</code>).\nTotal Reward: ₹{total_reward}", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith("rej_pack_"))
def admin_reject_pack(call):
    if call.from_user.id not in ADMIN_IDS: return
    parts = call.data.split("_")
    task_type = parts[2]
    uid = int(parts[3])

    udata = user_data.get(uid, {})
    pending_key = "pending_tasks" if task_type == "rev" else "pending_gmails"
    pool = available_tasks if task_type == "rev" else gmail_tasks

    with data_lock:
        pending_list = udata.get(pending_key, [])
        if not pending_list:
            bot.answer_callback_query(call.id, "Already processed.", show_alert=True)
            return

        tasks_rejected = len(pending_list)

        rejected_key = "rejected_tasks" if task_type == "rev" else "rejected_gmails"
        rejected_list = udata.setdefault(rejected_key, [])

        for task_idx in list(pending_list):
            if task_idx < len(pool):
                task = pool[task_idx]
                if isinstance(task, dict):
                    task["status"] = "available"
                    task["claim_time"] = 0
                    task["claimed_by"] = None
            if task_idx not in rejected_list:
                rejected_list.append(task_idx)

        udata[pending_key] = []

        # Track bulk task single rejections (entire pack)
        blocked_key = "bulk_review_blocked" if task_type == "rev" else "bulk_gmail_blocked"
        rejections_key = "bulk_review_rejections" if task_type == "rev" else "bulk_gmail_rejections"
        progress_key = "bulk_review_unlock_progress" if task_type == "rev" else "bulk_gmail_unlock_progress"
        if not udata.get(blocked_key):
            udata[rejections_key] = udata.get(rejections_key, 0) + tasks_rejected
            if udata[rejections_key] >= 10:
                udata[blocked_key] = True
                udata[rejections_key] = 0
                udata[progress_key] = 0
                try:
                    task_name_display = "Review" if task_type == "rev" else "Gmail"
                    bot.send_message(uid, f"🚫 **Blocked from Bulk Tasks!** 10 of your bulk tasks were rejected. You cannot do **Bulk {task_name_display} Tasks** now. Do 10 single {task_name_display} tasks to unlock!", parse_mode="Markdown")
                except:
                    pass

        udata["invalid_packs_count"] = udata.get("invalid_packs_count", 0) + 1
        invalid_packs = udata["invalid_packs_count"]

        ban_text = ""
        if invalid_packs >= 5:
            udata["banned"] = True
            log_admin_action("permanent_ban", {"user_id": uid, "reason": "5 invalid packs"})
            ban_text = "\n\n🚫 You have been permanently banned due to 5 invalid packs."
        elif invalid_packs >= 3:
            udata["banned_until"] = time.time() + (2 * 86400) # 2 days temporary ban
            log_admin_action("temporary_ban", {"user_id": uid, "days": 2, "reason": "3 invalid packs"})
            ban_text = "\n\n🚫 You have been temporarily banned for 2 days due to 3 invalid packs."

        log_admin_action("reject_pack", {"user_id": uid, "task_type": task_type, "tasks_count": tasks_rejected})
        save_data()

    try: bot.send_message(uid, f"❌ **Your Pack of {tasks_rejected} tasks was REJECTED.**\n\nAll tasks in this pack were denied.{ban_text}", parse_mode="Markdown")
    except: pass

    first_name = udata.get("first_name", "User")
    first_name_safe = html.escape(first_name)
    username = udata.get("username")
    username_display = f" (@{html.escape(username)})" if username else " (No username)"
    bot.send_message(call.message.chat.id, f"Pack rejected for user {first_name}{username_display} ({uid}).", reply_markup=restore_menu(call.from_user.id))
    bot.edit_message_text(f"❌ Pack Rejected for user {first_name_safe}{username_display} (<code>{uid}</code>).", chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")

@bot.callback_query_handler(func=lambda call: call.data.startswith("undo_app_"))
def admin_undo_approve_start(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    parts = call.data.split("_")
    if len(parts) == 5:
        task_type = parts[2]
        uid = int(parts[3])
        task_idx = int(parts[4])
    else:
        task_type = "rev"
        uid = int(parts[2])
        task_idx = int(parts[3])

    if uid not in user_data:
        return

    completed_list = user_data[uid].get("completed_tasks" if task_type == "rev" else "completed_gmails", [])
    if task_idx not in completed_list:
        bot.answer_callback_query(call.id, "Cannot undo. Task is not in completed list.", show_alert=True)
        return

    msg = bot.send_message(call.message.chat.id, f"Please enter the reason for rejecting previously approved Task ID {task_idx + 1} for user {uid}:", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_undo_approve_reason, uid, task_idx, task_type, call.message)
    bot.answer_callback_query(call.id, "Awaiting rejection reason...")

def process_undo_approve_reason(message, uid, task_idx, task_type, original_message):
    if is_cancel(message): return
    reason = message.text or message.caption or ""
    if not reason:
        if message.content_type == 'sticker':
            reason = "[Sticker]"
        elif message.content_type == 'photo':
            reason = "[Photo]"
        elif message.content_type == 'voice':
            reason = "[Voice Note]"
        else:
            reason = "No reason provided"

    if uid not in user_data:
        return

    completed_list = user_data[uid].get("completed_tasks" if task_type == "rev" else "completed_gmails", [])
    if task_idx not in completed_list:
        bot.send_message(message.chat.id, "Task is no longer approved.", reply_markup=restore_menu(message.from_user.id))
        return

    # Revert approval
    completed_list.remove(task_idx)

    pool = available_tasks if task_type == "rev" else gmail_tasks
    reward = bot_config.get("default_reward", 8.0) if task_type == "rev" else bot_config.get("default_gmail_reward", 8.0)
    if task_idx < len(pool):
        task = pool[task_idx]
        if isinstance(task, dict):
            reward = task.get("reward", reward)
            task["status"] = "available"
            task["claim_time"] = 0
            task["claimed_by"] = None
            task["rejection_count"] = task.get("rejection_count", 0) + 1

    deduct_user_balance(uid, reward)
    user_data[uid]["tasks_completed"] = max(0, user_data[uid].get("tasks_completed", 1) - 1)
    lifetime_key = "lifetime_reviews_completed" if task_type == "rev" else "lifetime_gmails_completed"
    if lifetime_key not in user_data[uid]:
        user_data[uid][lifetime_key] = len(completed_list) + 1
    user_data[uid][lifetime_key] = max(0, user_data[uid].get(lifetime_key, 1) - 1)
    ts_key = "completed_tasks_ts" if task_type == "rev" else "completed_gmails_ts"
    if ts_key in user_data[uid] and user_data[uid][ts_key]:
        user_data[uid][ts_key].pop()

    earned_key = "earned_from_reviews" if task_type == "rev" else "earned_from_gmails"
    user_data[uid][earned_key] = max(0, user_data[uid].get(earned_key, 0) - reward)

    # Apply rejection
    user_data[uid]["rejection_count"] = user_data[uid].get("rejection_count", 0) + 1
    rejection_count = user_data[uid]["rejection_count"]

    rejected_key = "rejected_tasks" if task_type == "rev" else "rejected_gmails"
    rejected_list = user_data[uid].setdefault(rejected_key, [])
    if task_idx not in rejected_list:
        rejected_list.append(task_idx)

    log_admin_action("undo_approve", {"user_id": uid, "task_idx": task_idx, "task_type": task_type, "reason": reason})
    save_data()

    try:
        task_name = "Task" if task_type == "rev" else "Gmail Task"
        bot.send_message(uid, f"❌ A previously approved proof for {task_name} {task_idx + 1} has been reverted and rejected by the admin.\n₹{reward} has been deducted from your wallet.\nReason:")
        send_direct(uid, message)

        if rejection_count >= 3:
            unban_time = time.time() + (2 * 86400)
            user_data[uid]["banned_until"] = unban_time
            user_data[uid]["rejection_count"] = 0
            save_data()
            bot.send_message(uid, "🚫 You have been temporarily banned for 2 days due to reaching 3 rejections.")
    except:
        pass

    # Update the original message
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("↩️ Undo & Approve", callback_data=f"undo_rej_{task_type}_{uid}_{task_idx}"))

    try:
        short_reason = reason[:100] + "..." if len(reason) > 100 else reason
        udata = user_data.get(uid, {})
        first_name = udata.get("first_name", "User")
        first_name_safe = html.escape(first_name)
        username = udata.get("username")
        username_display = f" (@{html.escape(username)})" if username else " (No username)"
        caption_text = (
            f"❌ Reversed & Rejected\n"
            f"Name: {first_name_safe}{username_display}\n"
            f"User ID: <code>{uid}</code>\n"
            f"Task ID: {task_idx + 1}\n"
            f"Reason: {html.escape(short_reason)}\n"
            f"User's Rejections: {rejection_count}"
        )
        try:
            if original_message.content_type in ['photo', 'document']:
                bot.edit_message_caption(caption_text, chat_id=original_message.chat.id, message_id=original_message.message_id, reply_markup=markup, parse_mode="HTML")
            else:
                bot.edit_message_text(caption_text, chat_id=original_message.chat.id, message_id=original_message.message_id, reply_markup=markup, parse_mode="HTML")
        except Exception:
            pass
    except:
        pass

    bot.send_message(message.chat.id, "Approval undone and task rejected.", reply_markup=restore_menu(message.from_user.id))

@bot.callback_query_handler(func=lambda call: call.data.startswith("undo_rej_"))
def admin_undo_reject_to_approve(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    parts = call.data.split("_")
    if len(parts) == 5:
        task_type = parts[2]
        uid = int(parts[3])
        task_idx = int(parts[4])
    else:
        task_type = "rev"
        uid = int(parts[2])
        task_idx = int(parts[3])

    if uid not in user_data:
        return

    completed_list = user_data[uid].setdefault("completed_tasks" if task_type == "rev" else "completed_gmails", [])
    if task_idx in completed_list:
        bot.answer_callback_query(call.id, "Task is already approved.", show_alert=True)
        return

    ban_lifted = False
    # Lift temporary ban if active
    if user_data[uid].get("banned_until", 0) > time.time():
        user_data[uid]["banned_until"] = 0
        user_data[uid]["rejection_count"] = 2
        ban_lifted = True
        try:
            bot.send_message(uid, "✅ Your temporary ban has been lifted because a rejected task was approved.")
        except:
            pass
    else:
        # Revert rejection count normally
        user_data[uid]["rejection_count"] = max(0, user_data[uid].get("rejection_count", 0) - 1)

    rejected_key = "rejected_tasks" if task_type == "rev" else "rejected_gmails"
    rejected_list = user_data[uid].setdefault(rejected_key, [])
    if task_idx in rejected_list:
        rejected_list.remove(task_idx)

    # Apply approval
    pool = available_tasks if task_type == "rev" else gmail_tasks
    reward = bot_config.get("default_reward", 8.0) if task_type == "rev" else bot_config.get("default_gmail_reward", 8.0)
    if task_idx < len(pool):
        task = pool[task_idx]
        if isinstance(task, dict):
            task["status"] = "completed"
            reward = task.get("reward", reward)
            task["rejection_count"] = max(0, task.get("rejection_count", 0) - 1)

    is_first_task = user_data[uid]["tasks_completed"] == 0

    user_data[uid]["balance"] += reward
    user_data[uid]["tasks_completed"] += 1
    ts_key = "completed_tasks_ts" if task_type == "rev" else "completed_gmails_ts"
    user_data[uid].setdefault(ts_key, []).append((time.time(), reward))
    earned_key = "earned_from_reviews" if task_type == "rev" else "earned_from_gmails"
    user_data[uid][earned_key] = user_data[uid].get(earned_key, 0) + reward
    completed_list.append(task_idx)
    lifetime_key = "lifetime_reviews_completed" if task_type == "rev" else "lifetime_gmails_completed"
    if lifetime_key not in user_data[uid]:
        user_data[uid][lifetime_key] = len(completed_list) - 1
    user_data[uid][lifetime_key] += 1

    # Increment unlock progress for bulk tasks if blocked
    blocked_key = "bulk_review_blocked" if task_type == "rev" else "bulk_gmail_blocked"
    progress_key = "bulk_review_unlock_progress" if task_type == "rev" else "bulk_gmail_unlock_progress"
    rejections_key = "bulk_review_rejections" if task_type == "rev" else "bulk_gmail_rejections"
    if user_data[uid].get(blocked_key):
        user_data[uid][progress_key] = user_data[uid].get(progress_key, 0) + 1
        if user_data[uid][progress_key] >= 10:
            user_data[uid][blocked_key] = False
            user_data[uid][progress_key] = 0
            user_data[uid][rejections_key] = 0
            try:
                task_name_display = "Review" if task_type == "rev" else "Gmail"
                bot.send_message(uid, f"🎉 **Unlocked!** You completed 10 single tasks. You can now do **Bulk {task_name_display} Tasks** again!", parse_mode="Markdown")
            except:
                pass

    log_admin_action("undo_reject", {"user_id": uid, "task_idx": task_idx, "task_type": task_type, "reward": reward})
    if ban_lifted:
        log_admin_action("unban_user", {"user_id": uid, "reason": "ban lifted via undo reject"})
    save_data()
    check_and_reward_referrer(uid)

    try:
        strike_msg = "" if ban_lifted else "\n\n✅ 1 Rejection strike has been removed."
        task_name = "Task" if task_type == "rev" else "Gmail Task"
        bot.send_message(uid, f"🎉 Sorry for the confusion, your submission for {task_name} {task_idx + 1} has now been approved! ₹{reward} has been added to your wallet.{strike_msg}")
    except:
        pass

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("↩️ Undo & Reject", callback_data=f"undo_app_{task_type}_{uid}_{task_idx}"))

    try:
        new_rejection_count = user_data[uid].get('rejection_count', 0)
        udata = user_data.get(uid, {})
        first_name = udata.get("first_name", "User")
        first_name_safe = html.escape(first_name)
        username = udata.get("username")
        username_display = f" (@{html.escape(username)})" if username else " (No username)"
        caption_text = (
            f"✅ Reversed & Approved\n"
            f"Name: {first_name_safe}{username_display}\n"
            f"User ID: <code>{uid}</code>\n"
            f"Task ID: {task_idx + 1}\n"
            f"Reward: ₹{reward} added.\n"
            f"User's Rejections: {new_rejection_count}"
        )
        try:
            if call.message.content_type in ['photo', 'document']:
                bot.edit_message_caption(caption_text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
            else:
                bot.edit_message_text(caption_text, chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup, parse_mode="HTML")
        except Exception:
            pass
    except:
        pass

    bot.answer_callback_query(call.id, "Task Approved via Undo.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("ban_"))
def admin_ban_user_menu(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    parts = call.data.split("_")
    if len(parts) >= 4:
        task_type = parts[1]
        uid = int(parts[2])
        task_idx = int(parts[3])
    else:
        task_type = "rev"
        uid = int(parts[1])
        task_idx = int(parts[2])

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("⏳ Temporary Ban", callback_data=f"tempban_{task_type}_{uid}_{task_idx}"))
    markup.add(InlineKeyboardButton("⛔ Permanent Ban", callback_data=f"permban_{task_type}_{uid}_{task_idx}"))
    markup.add(InlineKeyboardButton("🔙 Cancel", callback_data=f"cancelban_{task_type}_{uid}_{task_idx}"))

    bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("cancelban_"))
def admin_cancel_ban(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    parts = call.data.split("_")
    if len(parts) >= 4:
        task_type = parts[1]
        uid = int(parts[2])
        task_idx = int(parts[3])
    else:
        task_type = "rev"
        uid = int(parts[1])
        task_idx = int(parts[2])

    markup = InlineKeyboardMarkup()
    btn1 = InlineKeyboardButton("✅ Approve", callback_data=f"approve_{task_type}_{uid}_{task_idx}")
    btn2 = InlineKeyboardButton("❌ Reject", callback_data=f"reject_{task_type}_{uid}_{task_idx}")
    btn3 = InlineKeyboardButton("🚫 Ban", callback_data=f"ban_{task_type}_{uid}_{task_idx}")
    markup.add(btn1, btn2)
    markup.add(btn3)
    if task_type == "gm":
        markup.add(InlineKeyboardButton("🤖 Bot Mistake", callback_data=f"botmistake_gm_{uid}_{task_idx}"))

    bot.edit_message_reply_markup(chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("permban_"))
def admin_perm_ban(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    parts = call.data.split("_")
    if len(parts) >= 4:
        task_type = parts[1]
        uid = int(parts[2])
        task_idx = int(parts[3])
    else:
        task_type = "rev"
        uid = int(parts[1])
        task_idx = int(parts[2])

    if uid not in user_data:
        user_data[uid] = {"balance": 0, "tasks_completed": 0, "referrals": 0, "completed_tasks": [], "pending_tasks": []}

    user_data[uid]["banned"] = True

    pending_key = "pending_tasks" if task_type == "rev" else "pending_gmails"
    pool = available_tasks if task_type == "rev" else gmail_tasks

    pending_list = user_data[uid].get(pending_key, [])
    if task_idx in pending_list:
        pending_list.remove(task_idx)

    if task_idx < len(pool):
        task = pool[task_idx]
        if isinstance(task, dict) and task.get("claimed_by") is not None and str(task.get("claimed_by")) == str(uid):
            task["status"] = "available"
            task["claim_time"] = 0
            task["claimed_by"] = None

    log_admin_action("permanent_ban", {"user_id": uid})
    save_data()

    try:
        bot.send_message(uid, "🚫 You have been permanently banned from using this bot.")
    except Exception:
        pass

    udata = user_data.get(uid, {})
    first_name = udata.get("first_name", "User")
    first_name_safe = html.escape(first_name)
    username = udata.get("username")
    username_display = f" (@{html.escape(username)})" if username else " (No username)"
    caption_text = (
        f"⛔ Permanently Banned\n"
        f"Name: {first_name_safe}{username_display}\n"
        f"User ID: <code>{uid}</code>\n"
        f"Task ID: {task_idx + 1}"
    )
    try:
        if call.message.content_type in ['photo', 'document']:
            bot.edit_message_caption(caption_text, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")
        else:
            bot.edit_message_text(caption_text, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="HTML")
    except Exception:
        pass
    bot.answer_callback_query(call.id, "User permanently banned.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("tempban_"))
def admin_temp_ban(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    parts = call.data.split("_")
    if len(parts) >= 4:
        task_type = parts[1]
        uid = int(parts[2])
        task_idx = int(parts[3])
    else:
        task_type = "rev"
        uid = int(parts[1])
        task_idx = int(parts[2])

    msg = bot.send_message(call.message.chat.id, f"How many days do you want to ban user {uid} for?", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_temp_ban, uid, task_idx, task_type, call.message)
    bot.answer_callback_query(call.id, "Awaiting days...")

def process_temp_ban(message, uid, task_idx, task_type, original_message):
    if is_cancel(message): return
    try:
        days = int(message.text)
    except ValueError:
        bot.send_message(message.chat.id, "Invalid number of days. Ban cancelled.", reply_markup=restore_menu(message.from_user.id))
        return

    if uid not in user_data:
        user_data[uid] = {"balance": 0, "tasks_completed": 0, "referrals": 0, "completed_tasks": [], "pending_tasks": []}

    unban_time = time.time() + (days * 86400)
    user_data[uid]["banned_until"] = unban_time

    pending_key = "pending_tasks" if task_type == "rev" else "pending_gmails"
    pool = available_tasks if task_type == "rev" else gmail_tasks

    pending_list = user_data[uid].get(pending_key, [])
    if task_idx in pending_list:
        pending_list.remove(task_idx)

    if task_idx < len(pool):
        task = pool[task_idx]
        if isinstance(task, dict) and task.get("claimed_by") is not None and str(task.get("claimed_by")) == str(uid):
            task["status"] = "available"
            task["claim_time"] = 0
            task["claimed_by"] = None

    log_admin_action("temporary_ban", {"user_id": uid, "days": days})
    save_data()

    try:
        bot.send_message(uid, f"🚫 You have been temporarily banned for {days} day(s).")
    except Exception:
        pass

    udata = user_data.get(uid, {})
    first_name = udata.get("first_name", "User")
    first_name_safe = html.escape(first_name)
    username = udata.get("username")
    username_display = f" (@{html.escape(username)})" if username else " (No username)"
    caption_text = (
        f"⏳ Temporarily Banned ({days} days)\n"
        f"Name: {first_name_safe}{username_display}\n"
        f"User ID: <code>{uid}</code>\n"
        f"Task ID: {task_idx + 1}"
    )
    try:
        if original_message.content_type in ['photo', 'document']:
            bot.edit_message_caption(caption_text, chat_id=original_message.chat.id, message_id=original_message.message_id, parse_mode="HTML")
        else:
            bot.edit_message_text(caption_text, chat_id=original_message.chat.id, message_id=original_message.message_id, parse_mode="HTML")
    except Exception:
        pass
    bot.send_message(message.chat.id, f"User {uid} temporarily banned for {days} day(s).", reply_markup=restore_menu(message.from_user.id))

@bot.callback_query_handler(func=lambda call: call.data == "admin_payments")
def handle_payments(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("✅ Paid", callback_data="admin_payments_paid"))
    markup.add(InlineKeyboardButton("⏳ Pending", callback_data="admin_payments_pending"))
    markup.add(InlineKeyboardButton("🔙 Back to Dashboard", callback_data="admin_panel_back"))
    bot.edit_message_text("💳 Select a Payments option:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "admin_payments_paid")
def handle_payments_paid(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    total_paid_amount = sum(u.get("total_withdrawn", 0) for u in list(user_data.values()))
    total_paid_users = sum(1 for u in list(user_data.values()) if u.get("total_withdrawn", 0) > 0)

    msg = f"✅ *Paid Statistics*\n\nTotal Users Paid: {total_paid_users}\nTotal Amount Paid: ₹{int(total_paid_amount)}"

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Back", callback_data="admin_payments"))

    bot.answer_callback_query(call.id)
    bot.edit_message_text(msg, chat_id=call.message.chat.id, message_id=call.message.message_id, parse_mode="Markdown", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "admin_payments_pending")
def handle_payments_pending(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    sent_count = 0
    all_pending = []
    for uid, udata in list(user_data.items()):
        for w in udata.get("pending_withdrawals", []):
            all_pending.append((uid, udata, w))

    all_pending.sort(key=lambda x: 0 if x[2].get("speed") == "urg" else 1)

    for uid, udata, w in all_pending:
            markup = InlineKeyboardMarkup()
            btn1 = InlineKeyboardButton("✅ Pay", callback_data=f"pay_with_{uid}_{w['id']}")
            btn2 = InlineKeyboardButton("❌ Reject", callback_data=f"rej_with_{uid}_{w['id']}")
            markup.add(btn1, btn2)

            first_name = udata.get("first_name", "User")
            first_name_safe = html.escape(first_name)
            username = udata.get("username")

            user_line = f"User: {first_name_safe} (ID: {uid})"
            if username:
                user_line += f" (@{username})"

            speed_text = "⚡ URGENT" if w.get("speed") == "urg" else "🟢 Standard"
            amt_to_pay = w['amount'] - w.get('fee', 0)
            caption = (
                f"<b>Withdrawal Request 💸 [{speed_text}]</b>\n"
                f"{user_line}\n"
                f"Requested: ₹{int(w['amount'])}\n"
                f"Fee: ₹{int(w.get('fee', 0))}\n"
                f"Amount to Pay: ₹{int(amt_to_pay)}\n"
                f"Method: {w['method']}\n"
            )

            if w['method'] == "Crypto":
                caption += "\n⚠️ <i>Please convert this INR amount to USDT before paying!</i>\n"

            if w["details_type"] == "text":
                caption += f"\nDetails:\n<code>{html.escape(w['details_val'])}</code>"
                try:
                    bot.send_message(call.message.chat.id, caption, reply_markup=markup, parse_mode="HTML")
                    sent_count += 1
                    time.sleep(0.1)
                except Exception:
                    pass
            elif w["details_type"] == "photo":
                try:
                    bot.send_photo(call.message.chat.id, w["details_val"], caption=caption, reply_markup=markup, parse_mode="HTML")
                    sent_count += 1
                    time.sleep(0.1)
                except Exception:
                    pass
            elif w.get("details_type") == "upi_combined":
                details_val = w.get("details_val", {})
                photo_id = details_val.get("photo")
                text = details_val.get("text")
                combined_caption = caption + f"\nDetails:\n<code>{html.escape(text)}</code>"
                try:
                    bot.send_photo(call.message.chat.id, photo_id, caption=combined_caption, reply_markup=markup, parse_mode="HTML")
                    sent_count += 1
                    time.sleep(0.1)
                except Exception:
                    pass

    if sent_count == 0:
        bot.answer_callback_query(call.id, "No pending withdrawals at the moment.", show_alert=True)
    else:
        bot.answer_callback_query(call.id, f"Sent {sent_count} pending withdrawal requests.")

@bot.callback_query_handler(func=lambda call: call.data.startswith("pay_with_"))
def admin_pay_withdrawal(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    parts = call.data.split("_")
    uid = int(parts[2])
    w_id = parts[3]

    withdrawals = user_data.get(uid, {}).get("pending_withdrawals", [])
    w = next((x for x in withdrawals if x["id"] == w_id), None)

    if not w:
        bot.answer_callback_query(call.id, "Withdrawal not found or already processed.", show_alert=True)
        return

    amt_to_pay = w['amount'] - w.get('fee', 0)
    amount_str = f"₹{int(amt_to_pay)}"
    if w['method'] == "Crypto":
        amount_str += " (Convert to USDT)"

    msg = bot.send_message(call.message.chat.id, f"Please send the payment screenshot for user {uid} (Amount: {amount_str}):", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_payment_proof, uid, w_id, call.message)
    bot.answer_callback_query(call.id, "Awaiting screenshot...")

def process_payment_proof(message, uid, w_id, original_message):
    if is_cancel(message): return
    is_photo = message.photo is not None and len(message.photo) > 0
    is_image_doc = message.document is not None and message.document.mime_type and message.document.mime_type.startswith("image/")
    if not is_photo and not is_image_doc:
        bot.send_message(message.chat.id, "No photo detected. Payment processing cancelled.", reply_markup=restore_menu(message.from_user.id))
        return

    photo_file_id = message.photo[-1].file_id if is_photo else message.document.file_id
    with data_lock:
        withdrawals = user_data.get(uid, {}).get("pending_withdrawals", [])
        w = next((x for x in withdrawals if x["id"] == w_id), None)

        if not w:
            bot.send_message(message.chat.id, "Withdrawal not found or already processed.", reply_markup=restore_menu(message.from_user.id))
            return

        if user_data[uid].get("balance", 0) >= w["amount"]:
            user_data[uid]["balance"] -= w["amount"]
            user_data[uid]["total_withdrawn"] = user_data[uid].get("total_withdrawn", 0) + w["amount"]
            user_data[uid]["paid_withdrawals_count"] = user_data[uid].get("paid_withdrawals_count", 0) + 1
            user_data[uid].setdefault("paid_withdrawals_ts", []).append((time.time(), w["amount"]))
            user_data[uid].setdefault("payment_history", []).append({"method": "UPI" if w.get("method") == "UPI" else w.get("method", "UPI"), "amount": float(w["amount"]), "status": "Success", "date": get_ist_time().strftime("%d %b %Y, %I:%M %p")})
            user_data[uid]["payment_history"] = user_data[uid]["payment_history"][-100:]
        else:
            bot.send_message(message.chat.id, "User has insufficient balance! Cancelling and removing request.", reply_markup=restore_menu(message.from_user.id))
            log_admin_action("reject_withdrawal", {"user_id": uid, "withdrawal_id": w_id, "amount": w["amount"], "reason": "Insufficient balance at processing"})
            withdrawals.remove(w)
            save_data()
            return

        log_admin_action("pay_withdrawal", {"user_id": uid, "withdrawal_id": w_id, "amount": w["amount"], "fee": w.get("fee", 0)})
        withdrawals.remove(w)
        save_data()

    amt_paid = w['amount'] - w.get('fee', 0)
    fee = w.get('fee', 0)
    try:
        if fee > 0:
            bot.send_photo(uid, photo_file_id, caption=f"✅ Your withdrawal of ₹{int(amt_paid)} (after ₹{int(fee)} fee) is successfully paid!")
        else:
            bot.send_photo(uid, photo_file_id, caption=f"✅ Your withdrawal of ₹{int(w['amount'])} is successfully paid!")
    except Exception:
        pass

    try:
        if original_message.content_type == 'photo':
            bot.edit_message_caption("✅ Paid", chat_id=original_message.chat.id, message_id=original_message.message_id)
        else:
            bot.edit_message_text(original_message.text + "\n\n✅ Paid", chat_id=original_message.chat.id, message_id=original_message.message_id, parse_mode="HTML")
    except Exception:
        pass

    bot.send_message(message.chat.id, "Payment sent and user balance deducted.", reply_markup=restore_menu(message.from_user.id if hasattr(message, "from_user") else message.chat.id))

@bot.callback_query_handler(func=lambda call: call.data.startswith("rej_with_"))
def admin_reject_withdrawal(call):
    if call.from_user.id not in ADMIN_IDS:
        return

    parts = call.data.split("_")
    uid = int(parts[2])
    w_id = parts[3]

    withdrawals = user_data.get(uid, {}).get("pending_withdrawals", [])
    w = next((x for x in withdrawals if x["id"] == w_id), None)

    if not w:
        bot.answer_callback_query(call.id, "Withdrawal not found or already processed.", show_alert=True)
        return

    msg = bot.send_message(call.message.chat.id, f"Please enter the reason for rejecting withdrawal for user {uid} (Amount: ₹{int(w['amount'])}):", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_withdrawal_rejection_reason, uid, w_id, call.message)
    bot.answer_callback_query(call.id, "Awaiting rejection reason...")

def process_withdrawal_rejection_reason(message, uid, w_id, original_message):
    if is_cancel(message): return
    reason = message.text or message.caption or ""
    if not reason:
        if message.content_type == 'sticker':
            reason = "[Sticker]"
        elif message.content_type == 'photo':
            reason = "[Photo]"
        elif message.content_type == 'voice':
            reason = "[Voice Note]"
        else:
            reason = "No reason provided"

    with data_lock:
        withdrawals = user_data.get(uid, {}).get("pending_withdrawals", [])
        w = next((x for x in withdrawals if x["id"] == w_id), None)

        if not w:
            bot.send_message(message.chat.id, "Withdrawal not found or already processed.", reply_markup=restore_menu(message.from_user.id))
            return

        log_admin_action("reject_withdrawal", {"user_id": uid, "withdrawal_id": w_id, "amount": w["amount"], "reason": reason})
        user_data[uid].setdefault("payment_history", []).append({"method": "UPI" if w.get("method") == "UPI" else w.get("method", "UPI"), "amount": float(w["amount"]), "status": "Rejected", "date": get_ist_time().strftime("%d %b %Y, %I:%M %p")})
        user_data[uid]["payment_history"] = user_data[uid]["payment_history"][-100:]
        withdrawals.remove(w)
        save_data()

    try:
        bot.send_message(uid, f"❌ Your withdrawal request for ₹{int(w['amount'])} was rejected by the admin.\nReason:")
        send_direct(uid, message)
    except Exception:
        pass

    try:
        short_reason = reason[:100] + "..." if len(reason) > 100 else reason
        caption = f"❌ Rejected\nReason: {short_reason}"
        if original_message.content_type == 'photo':
            bot.edit_message_caption(caption, chat_id=original_message.chat.id, message_id=original_message.message_id)
        else:
            bot.edit_message_text(original_message.text + f"\n\n{caption}", chat_id=original_message.chat.id, message_id=original_message.message_id, parse_mode="HTML")
    except Exception:
        pass

    bot.send_message(original_message.chat.id, "Withdrawal rejected and user notified.", reply_markup=restore_menu(message.from_user.id))

@bot.message_handler(func=lambda message: message.text in ["Wallet 💰", "💰 Wallet", "💰 ᴡᴀʟʟᴇᴛ"])
def handle_wallet(message):
    if not check_force_sub_and_alert(message): return
    user_id = message.from_user.id
    udata = user_data.get(user_id, {})
    balance = float(udata.get("balance", 0))
    tasks_done = udata.get("tasks_completed", 0)
    level = get_user_level(tasks_done)
    next_tier_req = 10 if tasks_done <= 10 else (50 if tasks_done <= 50 else (150 if tasks_done <= 150 else 500))
    progress = min(100, int((tasks_done / next_tier_req) * 100))
    filled = int(progress / 10)
    bar = "█" * filled + "░" * (10 - filled)
    text = (
        "༶•┈┈⛧┈♛\n💳 <b>ʏ ᴏ ᴜ ʀ  ᴘ ʀ ᴇ ᴍ ɪ ᴜ ᴍ  ᴘ ᴏ ʀ ᴛ ꜰ ᴏ ʟ ɪ ᴏ</b> 💎\n"
        "────── ⋆⋅☆⋅⋆ ──────\n\n"
        f"👤 <b>ᴜꜱᴇʀ ɪᴅ:</b> <code>{user_id}</code>\n"
        f"🏆 <b>ᴄᴜʀʀᴇɴᴛ ʀᴀɴᴋ:</b> {level}\n"
        f"📈 <b>ᴘʀᴏɢʀᴇꜱꜱ:</b> [{bar}] {progress}%\n\n"
        f"💰 <b>ᴀᴠᴀɪʟᴀʙʟᴇ ʙᴀʟᴀɴᴄᴇ:</b> <code>₹{int(balance)}</code> 💸\n"
        f"✅ <b>ᴛᴀꜱᴋꜱ ᴄᴏᴍᴘʟᴇᴛᴇᴅ:</b> {tasks_done}\n\n"
        "꘎♡━━━━━♡꘎━━━━━♡꘎\n"
        "<i>✨ ᴋᴇᴇᴘ ᴄᴏᴍᴘʟᴇᴛɪɴɢ ᴛᴀꜱᴋꜱ ᴛᴏ ᴜɴʟᴏᴄᴋ ʜɪɢʜᴇʀ ʀᴀɴᴋꜱ! 🎊</i>"
    )
    bot.send_message(message.chat.id, text, parse_mode="HTML")

@bot.message_handler(func=lambda message: message.text in ["Invite & Earn 👥", "👥 Invite & Earn", "👥 ɪɴᴠɪᴛᴇ & ᴇᴀʀɴ"])
def handle_invite(message):
    if not check_force_sub_and_alert(message): return
    user_id = message.from_user.id
    referrals = user_data.get(user_id, {}).get("referrals", 0)
    bot_info = bot.get_me()
    invite_link = f"https://t.me/{bot_info.username}?start={user_id}"
    text = (
        "༶•┈┈⛧┈♛\n⚜️ <b>ɪ ɴ ᴠ ɪ ᴛ ᴇ  &  ᴇ ᴀ ʀ ɴ</b> 🎊\n"
        "────── ⋆⋅☆⋅⋆ ──────\n\n"
        "✨ ꜱʜᴀʀᴇ ʏᴏᴜʀ ɪɴᴠɪᴛᴇ ʟɪɴᴋ ᴡɪᴛʜ ꜰʀɪᴇɴᴅꜱ ᴛᴏ ᴇᴀʀɴ <b>₹1</b> ᴘᴇʀ ʀᴇꜰᴇʀʀᴀʟ! 🪙\n\n"
        f"🔗 <b>ʏᴏᴜʀ ɪɴᴠɪᴛᴇ ʟɪɴᴋ:</b>\n<code>{invite_link}</code>\n\n"
        f"📊 <b>ᴛᴏᴛᴀʟ ʀᴇꜰᴇʀʀᴀʟꜱ:</b> {referrals} ⭐\n\n"
        "꘎♡━━━━━♡꘎━━━━━♡꘎\n"
        "<i>✨ ʙᴏɴᴜꜱ ɪꜱ ᴄʀᴇᴅɪᴛᴇᴅ ᴀꜰᴛᴇʀ ʏᴏᴜʀ ɪɴᴠɪᴛᴇᴅ ᴜꜱᴇʀ ᴄᴏᴍᴘʟᴇᴛᴇꜱ ᴛʜᴇɪʀ ꜰɪʀꜱᴛ ᴛᴀꜱᴋ. ✅</i>"
    )
    bot.send_message(message.chat.id, text, parse_mode="HTML")

@bot.message_handler(func=lambda message: message.text in ["Withdraw 💸", "💸 Withdraw", "💸 ᴡɪᴛʜᴅʀᴀᴡ", "💸 ᴡɪᴛʜᴅʀᴀᴡᴀʟ"])
def handle_withdraw(message):
    if not check_force_sub_and_alert(message): return
    user_id = message.from_user.id
    ban_msg = is_user_banned(user_id)
    if ban_msg: return bot.send_message(message.chat.id, ban_msg)

    udata = user_data.setdefault(user_id, {"balance": 0, "tasks_completed": 0, "referrals": 0, "completed_tasks": [], "pending_tasks": []})
    balance = float(udata.get("balance", 0))
    pending_amount = sum(float(w.get("amount", 0)) for w in udata.get("pending_withdrawals", []))
    available_balance = max(0.0, balance - pending_amount)

    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(InlineKeyboardButton("🌐 ᴏᴘᴇɴ ᴛᴏᴊɪ ʙᴀɴᴋ · ᴍɪɴɪ ᴀᴘᴘ", web_app=WebAppInfo(url=_miniapp_url(user_id))))
    markup.add(InlineKeyboardButton("💬 ᴄᴏɴᴛᴀᴄᴛ ꜱᴜᴘᴘᴏʀᴛ", url="https://t.me/REAL_TOJIx"))

    text = (
        "༶•┈┈⛧┈♛\n"
        "💸 <b>ᴡ ɪ ᴛ ʜ ᴅ ʀ ᴀ ᴡ  ꜰ ᴜ ɴ ᴅ ꜱ</b> 💳\n"
        "────── ⋆⋅☆⋅⋆ ──────\n\n"
        f"💰 <b>ᴀᴠᴀɪʟᴀʙʟᴇ ʙᴀʟᴀɴᴄᴇ:</b> ₹{int(available_balance)} ✨\n"
        f"<i>(ᴛᴏᴛᴀʟ: ₹{int(balance)}, ᴘᴇɴᴅɪɴɢ: ₹{int(pending_amount)}) 🪙</i>\n\n"
        "꘎♡━━━━━♡꘎━━━━━♡꘎\n"
        "👇 <b>⚡ ᴏᴘᴇɴ ᴛʜᴇ ᴘʀᴇᴍɪᴜᴍ ᴡɪᴛʜᴅʀᴀᴡᴀʟ ᴘᴏʀᴛᴀʟ ʙᴇʟᴏᴡ:</b>"
    )
    bot.send_message(message.chat.id, text, parse_mode="HTML", reply_markup=markup)

def process_withdraw_amount(message, balance):
    user_id = message.from_user.id
    if user_data.get(user_id, {}).get("withdraw_step") != "amount":
        return

    if is_cancel(message):
        user_data[user_id]["withdraw_step"] = None
        return

    try:
        amount = float(message.text)
    except ValueError:
        bot.send_message(message.chat.id, "⚠️ Wrong amount! Please enter only a number. Withdrawal cancelled.", reply_markup=restore_menu(message.from_user.id))
        user_data[user_id]["withdraw_step"] = None
        return

    if amount < 20:
        bot.send_message(message.chat.id, "⚠️ Minimum withdrawal is ₹20. Withdrawal cancelled.", reply_markup=restore_menu(message.from_user.id))
        user_data[user_id]["withdraw_step"] = None
        return

    if amount > balance:
        bot.send_message(message.chat.id, "⚠️ Not enough balance. Withdrawal cancelled.", reply_markup=restore_menu(message.from_user.id))
        user_data[user_id]["withdraw_step"] = None
        return

    user_data[user_id]["withdraw_step"] = "speed_selection"
    fee = 15.0
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🟢 Standard (24-48 hrs, Free)", callback_data=f"withspeed_std_{amount}"))
    markup.add(InlineKeyboardButton(f"⚡ Urgent (1-2 hrs, ₹{int(fee)} Fee)", callback_data=f"withspeed_urg_{amount}"))
    markup.add(InlineKeyboardButton("🔙 Cancel", callback_data="cancel_withdraw"))
    bot.send_message(message.chat.id, "Select your withdrawal speed:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "cancel_withdraw")
def cancel_withdraw_handler(call):
    if not check_force_sub_and_alert(call): return
    user_id = call.from_user.id
    user_data[user_id]["withdraw_step"] = None
    bot.clear_step_handler_by_chat_id(call.message.chat.id)
    bot.edit_message_text("Withdrawal cancelled.", chat_id=call.message.chat.id, message_id=call.message.message_id)
    bot.send_message(call.message.chat.id, "Returned to main menu.", reply_markup=restore_menu(user_id))

@bot.callback_query_handler(func=lambda call: call.data.startswith("withspeed_"))
def process_withdraw_speed(call):
    if not check_force_sub_and_alert(call): return
    user_id = call.from_user.id
    if user_data.get(user_id, {}).get("withdraw_step") != "speed_selection":
        return

    parts = call.data.split("_")
    speed = parts[1]
    amount = float(parts[2])

    user_data[user_id]["withdraw_step"] = "method_selection"

    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("UPI", callback_data=f"with_{speed}_UPI_{amount}"))
    markup.add(InlineKeyboardButton("Crypto", callback_data=f"with_{speed}_Crypto_{amount}"))
    markup.add(InlineKeyboardButton("🔙 Cancel", callback_data="cancel_withdraw"))
    bot.edit_message_text(f"You selected {'⚡ Urgent' if speed == 'urg' else '🟢 Standard'} withdrawal.\nNow choose payment method:", chat_id=call.message.chat.id, message_id=call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("with_"))
def process_withdraw_method(call):
    if not check_force_sub_and_alert(call): return
    user_id = call.from_user.id
    if user_data.get(user_id, {}).get("withdraw_step") != "method_selection":
        bot.answer_callback_query(call.id, "Method already selected.")
        return

    user_data[user_id]["withdraw_step"] = "details"
    parts = call.data.split("_")
    speed = parts[1]
    method = parts[2]
    amount = float(parts[3])

    try:
        bot.edit_message_text(f"You chose {method}. Now send your details in next message.", chat_id=call.message.chat.id, message_id=call.message.message_id)
    except Exception:
        pass

    bot.clear_step_handler_by_chat_id(call.message.chat.id)

    time_note = "up to 2 hours" if speed == "urg" else "up to 12 hours"
    if method == "UPI":
        msg = bot.send_message(call.message.chat.id, f"Send your UPI ID or QR Code image:\n\n*(Payment can take {time_note} to complete)*", parse_mode="Markdown", reply_markup=get_cancel_menu())
    else:
        msg = bot.send_message(call.message.chat.id, f"INR will convert to USDT.\n\nSend your Crypto address:\n\n*(Payment can take {time_note} to complete)*", parse_mode="Markdown", reply_markup=get_cancel_menu())

    bot.register_next_step_handler(msg, process_withdraw_details, method, amount, speed)
    bot.answer_callback_query(call.id)

def process_withdraw_details(message, method, amount, speed):
    user_id = message.from_user.id
    if user_data.get(user_id, {}).get("withdraw_step") != "details":
        return

    if is_cancel(message):
        user_data[user_id]["withdraw_step"] = None
        return

    user_data[user_id]["withdraw_step"] = None

    details_type = None
    details_val = None

    is_photo = message.photo is not None and len(message.photo) > 0
    is_image_doc = message.document is not None and message.document.mime_type and message.document.mime_type.startswith("image/")

    if (is_photo or is_image_doc) and message.caption:
        details_type = "upi_combined"
        photo_id = message.photo[-1].file_id if is_photo else message.document.file_id
        details_val = {
            "photo": photo_id,
            "text": message.caption
        }
    elif is_photo or is_image_doc:
        details_type = "photo"
        details_val = message.photo[-1].file_id if is_photo else message.document.file_id
    elif message.text:
        details_type = "text"
        details_val = message.text
    else:
        bot.send_message(message.chat.id, "⚠️ Wrong details. Please start withdrawal process again.", reply_markup=restore_menu(message.from_user.id))
        return

    withdraw_id = str(int(time.time() * 1000))
    withdrawal = {
        "id": withdraw_id,
        "amount": amount,
        "method": method,
        "speed": speed,
        "fee": 15.0 if speed == "urg" else 0.0,
        "details_type": details_type,
        "details_val": details_val
    }

    user_data[user_id].setdefault("pending_withdrawals", []).append(withdrawal)
    user_data[user_id]["first_name"] = message.from_user.first_name or "User"
    if message.from_user.username:
        user_data[user_id]["username"] = message.from_user.username
    save_data()

    bot.send_message(message.chat.id, "💸 Your withdrawal request is sent. Wait for admin approval.", reply_markup=restore_menu(message.from_user.id))

    if speed == "urg":
        for admin_id in ADMIN_IDS:
            markup = InlineKeyboardMarkup()
            markup.add(InlineKeyboardButton("✅ Pay", callback_data=f"pay_with_{user_id}_{withdraw_id}"),
                       InlineKeyboardButton("❌ Reject", callback_data=f"rej_with_{user_id}_{withdraw_id}"))

            first_name = message.from_user.first_name or "User"
            first_name_safe = html.escape(first_name)
            username = message.from_user.username
            user_line = f"User: {first_name_safe} (ID: {user_id})"
            if username:
                user_line += f" (@{username})"

            amt_to_pay = amount - 15.0
            caption = (
                f"<b>🚨 URGENT Withdrawal Request 💸</b>\n"
                f"{user_line}\n"
                f"Requested: ₹{int(amount)}\n"
                f"Fee: ₹15\n"
                f"Amount to Pay: ₹{int(amt_to_pay)}\n"
                f"Method: {method}\n"
            )

            if method == "Crypto":
                caption += "\n⚠️ <i>Please convert this INR amount to USDT before paying!</i>\n"

            if details_type == "text":
                caption += f"\nDetails:\n<code>{html.escape(details_val)}</code>"
                try:
                    bot.send_message(admin_id, caption, reply_markup=markup, parse_mode="HTML")
                except:
                    pass
            elif details_type == "photo":
                try:
                    bot.send_photo(admin_id, details_val, caption=caption, reply_markup=markup, parse_mode="HTML")
                except:
                    pass
            elif details_type == "upi_combined":
                photo_id = details_val.get("photo")
                text = details_val.get("text")
                combined_caption = caption + f"\nDetails:\n<code>{html.escape(text)}</code>"
                try:
                    bot.send_photo(admin_id, photo_id, caption=combined_caption, reply_markup=markup, parse_mode="HTML")
                except:
                    pass


# ==========================================
#       TELEGRAM MINI APP DATA BRIDGE
# ==========================================
@bot.message_handler(content_types=["web_app_data"])
def handle_web_app_data(message):
    """Receive Mini App actions and process them using the same persistent DB."""
    try:
        payload = json.loads(message.web_app_data.data or "{}")
    except Exception:
        bot.send_message(message.chat.id, "⚠️ Invalid Mini App request.", reply_markup=restore_menu(message.from_user.id))
        return

    uid = message.from_user.id
    action = payload.get("action")

    if action == "withdraw":
        if payload.get("method") != "UPI":
            bot.send_message(message.chat.id, "❌ Only UPI withdrawals are enabled.", reply_markup=restore_menu(uid)); return
        try:
            amount = float(payload.get("amount", 0))
        except Exception:
            amount = 0
        upi = str(payload.get("details", "")).strip()
        u = user_data.setdefault(uid, {"balance": 0, "tasks_completed": 0, "referrals": 0, "completed_tasks": [], "pending_tasks": []})
        pending = sum(float(w.get("amount", 0)) for w in u.get("pending_withdrawals", []) or [])
        available = float(u.get("balance", 0)) - pending
        if amount < 20:
            bot.send_message(message.chat.id, "⚠️ Minimum withdrawal is ₹20.", reply_markup=restore_menu(uid)); return
        if amount > available:
            bot.send_message(message.chat.id, f"⚠️ Insufficient available balance. Available: ₹{available:.2f}", reply_markup=restore_menu(uid)); return
        if not __import__('re').match(r"^[\w.\-]{2,}@[A-Za-z0-9.\-]{2,}$", upi):
            bot.send_message(message.chat.id, "⚠️ Enter a valid UPI ID.", reply_markup=restore_menu(uid)); return

        wid = str(int(time.time() * 1000))
        w = {"id": wid, "amount": amount, "method": "UPI", "speed": "std", "fee": 0.0, "details_type": "text", "details_val": upi}
        with data_lock:
            u.setdefault("pending_withdrawals", []).append(w)
            u["first_name"] = message.from_user.first_name or "User"
            if message.from_user.username: u["username"] = message.from_user.username
            u.setdefault("payment_history", []).append({"method":"UPI","amount":amount,"status":"Pending","date":get_ist_time().strftime("%d %b %Y, %I:%M %p")})
            u["payment_history"] = u["payment_history"][-100:]
            save_data()
        bot.send_message(message.chat.id, "💸 <b>Withdrawal request submitted.</b>\n\nYour UPI request is now <b>Pending</b> and will be reviewed by an admin.", parse_mode="HTML", reply_markup=restore_menu(uid))
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("✅ Pay", callback_data=f"pay_with_{uid}_{wid}"), InlineKeyboardButton("❌ Reject", callback_data=f"rej_with_{uid}_{wid}"))
        caption = f"<b>💸 UPI Withdrawal Request</b>\n👤 {html.escape(message.from_user.first_name or 'User')}\n🆔 <code>{uid}</code>\n💰 Amount: <b>₹{amount:.2f}</b>\n🏦 UPI: <code>{html.escape(upi)}</code>"
        for admin_id in ADMIN_IDS:
            try: bot.send_message(admin_id, caption, parse_mode="HTML", reply_markup=markup)
            except Exception: pass
        return

    if action in ("admin_approve", "admin_reject"):
        if uid not in ADMIN_IDS:
            bot.send_message(message.chat.id, "❌ Admin only."); return
        wid = str(payload.get("request_id", ""))
        target_uid = None; w = None
        for tuid, tu in user_data.items():
            for candidate in tu.get("pending_withdrawals", []) or []:
                if str(candidate.get("id")) == wid:
                    target_uid, w = tuid, candidate; break
            if w: break
        if not w:
            bot.send_message(message.chat.id, "⚠️ Request not found or already processed."); return
        if action == "admin_reject":
            user_data[target_uid].get("pending_withdrawals", []).remove(w)
            user_data[target_uid].setdefault("payment_history", []).append({"method":"UPI","amount":float(w.get("amount",0)),"status":"Rejected","date":get_ist_time().strftime("%d %b %Y, %I:%M %p")})
            user_data[target_uid]["payment_history"] = user_data[target_uid]["payment_history"][-100:]
            save_data()
            bot.send_message(target_uid, f"🚫 Your UPI withdrawal of ₹{float(w.get('amount',0)):.2f} was rejected by admin.")
            bot.send_message(message.chat.id, "🚫 Withdrawal rejected."); return
        amount = float(w.get("amount",0))
        if float(user_data[target_uid].get("balance",0)) < amount:
            bot.send_message(message.chat.id, "⚠️ User balance is insufficient."); return
        user_data[target_uid]["balance"] = float(user_data[target_uid].get("balance",0)) - amount
        user_data[target_uid]["total_withdrawn"] = float(user_data[target_uid].get("total_withdrawn",0)) + amount
        user_data[target_uid].setdefault("paid_withdrawals_ts", []).append((time.time(), amount))
        user_data[target_uid].setdefault("payment_history", []).append({"method":"UPI","amount":amount,"status":"Success","date":get_ist_time().strftime("%d %b %Y, %I:%M %p")})
        user_data[target_uid]["payment_history"] = user_data[target_uid]["payment_history"][-100:]
        user_data[target_uid].get("pending_withdrawals", []).remove(w)
        save_data()
        bot.send_message(target_uid, f"✅ Your UPI withdrawal of ₹{amount:.2f} has been approved and marked as paid.")
        bot.send_message(message.chat.id, "✅ Withdrawal approved and marked paid."); return

# ==========================================
#              ADMIN COMMANDS
# ==========================================

def get_ordinal(n):
    if 11 <= (n % 100) <= 13:
        return f"{n}th"
    return f"{n}{['th', 'st', 'nd', 'rd', 'th', 'th', 'th', 'th', 'th', 'th'][n % 10]}"

@bot.message_handler(commands=['add'])
def cmd_add_balance(message):
    if message.from_user.id not in ADMIN_IDS: return
    args = message.text.split()
    if len(args) < 3:
        bot.reply_to(message, "⚠️ Usage: `/add <user_id> <amount>`", parse_mode="Markdown")
        return
    try:
        uid = int(args[1])
        amt = float(args[2])
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid User ID or Amount format.")
        return

    with data_lock:
        if uid in user_data:
            user_data[uid]["balance"] = user_data[uid].get("balance", 0) + amt
            save_data()
            bot.reply_to(message, f"✅ Successfully added ₹{amt} to user <code>{uid}</code>.\nNew Balance: ₹{user_data[uid]['balance']}", parse_mode="HTML")
            try:
                bot.send_message(uid, f"💰 <b>Balance Added!</b> Admin credited ₹{amt} to your wallet.", parse_mode="HTML")
            except:
                pass
        else:
            bot.reply_to(message, f"❌ User <code>{uid}</code> not found in database.", parse_mode="HTML")

@bot.message_handler(commands=['cut'])
def cmd_cut_balance(message):
    if message.from_user.id not in ADMIN_IDS: return
    args = message.text.split()
    if len(args) < 3:
        bot.reply_to(message, "⚠️ Usage: `/cut <user_id> <amount>`", parse_mode="Markdown")
        return
    try:
        uid = int(args[1])
        amt = float(args[2])
    except ValueError:
        bot.reply_to(message, "⚠️ Invalid User ID or Amount format.")
        return

    with data_lock:
        if uid in user_data:
            user_data[uid]["balance"] = max(0.0, user_data[uid].get("balance", 0) - amt)
            save_data()
            bot.reply_to(message, f"✅ Successfully deducted ₹{amt} from user <code>{uid}</code>.\nNew Balance: ₹{user_data[uid]['balance']}", parse_mode="HTML")
            try:
                bot.send_message(uid, f"⚠️ <b>Balance Deducted!</b> Admin debited ₹{amt} from your wallet.", parse_mode="HTML")
            except:
                pass
        else:
            bot.reply_to(message, f"❌ User <code>{uid}</code> not found in database.", parse_mode="HTML")

@bot.message_handler(func=lambda message: message.text == "➕ Add Task")
def admin_add_task(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("➕ Add Single Task", callback_data="add_single_task"))
    markup.add(InlineKeyboardButton("➕ Add Bulk Tasks", callback_data="add_bulk_tasks"))
    bot.send_message(message.chat.id, "Select how you want to add tasks:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "add_single_task")
def admin_add_single_task(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    msg = bot.send_message(call.message.chat.id, "Please send the Link for the review task:", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_task_link)
    bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "add_bulk_tasks")
def admin_add_bulk_tasks(call):
    if call.from_user.id not in ADMIN_IDS:
        return
    msg = bot.send_message(call.message.chat.id, "Send the Google Maps review link for which you want to add bulk tasks.", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_bulk_task_link)
    bot.answer_callback_query(call.id)

def process_bulk_task_link(message):
    if is_cancel(message): return
    task_link = message.text
    if not task_link:
        bot.send_message(message.chat.id, "No text detected. Task addition cancelled.", reply_markup=restore_menu(message.from_user.id))
        return

    markup = ReplyKeyboardMarkup(resize_keyboard=True, row_width=1)
    markup.add(KeyboardButton("✅ Submit Tasks"))
    markup.add(KeyboardButton("🔙 Cancel"))

    msg = bot.send_message(message.chat.id, "Link received successfully ✅\nNow send the 1st review comment.", reply_markup=markup)
    comments = []
    bot.register_next_step_handler(msg, process_bulk_task_comment, task_link, comments)

def process_bulk_task_comment(message, task_link, comments):
    if is_cancel(message): return

    if message.text == "✅ Submit Tasks":
        if not comments:
            bot.send_message(message.chat.id, "No comments added. Bulk task addition cancelled.", reply_markup=restore_menu(message.from_user.id))
            return

        reward = bot_config.get("default_reward", 8.0)
        task_link_safe = task_link.replace("<", "&lt;").replace(">", "&gt;")

        for comment in comments:
            task_comment_safe = comment.replace("<", "&lt;").replace(">", "&gt;")
            task_desc = f"Link: {task_link_safe}\n\nComment:\n<code>{task_comment_safe}</code>"
            available_tasks.append({"description": task_desc, "reward": reward, "active": True, "created_at": time.time()})

        save_data()

        bot.send_message(
            message.chat.id,
            f"Bulk task created successfully ✅\n\n📌 Review Link:\n{task_link}\n\n📊 Total Tasks Added: {len(comments)}\n\nAll comments have been saved as separate tasks with the same review link.",
            reply_markup=restore_menu(message.from_user.id)
        )
        return

    comment = message.text
    if not comment:
        msg = bot.send_message(message.chat.id, "No text detected. Please send the comment again or click ✅ Submit Tasks.", reply_markup=ReplyKeyboardMarkup(resize_keyboard=True, row_width=1).add(KeyboardButton("✅ Submit Tasks"), KeyboardButton("🔙 Cancel")))
        bot.register_next_step_handler(msg, process_bulk_task_comment, task_link, comments)
        return

    comments.append(comment)
    count = len(comments) + 1

    msg = bot.send_message(message.chat.id, f"{get_ordinal(count - 1)} comment added successfully ✅\nNow send the {get_ordinal(count)} review comment or click ✅ Submit Tasks to finish.")
    bot.register_next_step_handler(msg, process_bulk_task_comment, task_link, comments)

def process_task_link(message):
    if is_cancel(message): return
    task_link = message.text

    if not task_link:
        bot.send_message(message.chat.id, "No text detected. Task addition cancelled.", reply_markup=restore_menu(message.from_user.id))
        return

    msg = bot.send_message(message.chat.id, "Please send the comment text :", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_task_comment, task_link)

def process_task_comment(message, task_link):
    if is_cancel(message): return
    task_comment = message.text
    if not task_comment:
        bot.send_message(message.chat.id, "No text detected. Task addition cancelled.", reply_markup=restore_menu(message.from_user.id))
        return

    reward = bot_config.get("default_reward", 8.0)
    task_link_safe = task_link.replace("<", "&lt;").replace(">", "&gt;")
    task_comment_safe = task_comment.replace("<", "&lt;").replace(">", "&gt;")

    task_desc = f"Link: {task_link_safe}\n\nComment:\n<code>{task_comment_safe}</code>"

    available_tasks.append({"description": task_desc, "reward": reward, "active": True, "created_at": time.time()})
    save_data()
    active_tasks_count = sum(1 for t in available_tasks if is_task_available(t))
    bot.send_message(message.chat.id, f"✅ Task added successfully with ₹{reward} reward!\n📊 Total active tasks currently available: {active_tasks_count}", reply_markup=restore_menu(message.from_user.id))

def _review_ui_text():
    return (f"༶•┈┈⛧┈♛\n📍 <b>ʀ ᴇ ᴠ ɪ ᴇ ᴡ  ᴛ ᴀ ꜱ ᴋ ꜱ</b> 💎\n────── ⋆⋅☆⋅⋆ ──────\n\n🔹 <b>ꜱɪɴɢʟᴇ:</b> ₹{bot_config.get('default_reward', 8.0)} 🪙\n📦 <b>ʙᴜʟᴋ:</b> ₹{bot_config.get('bulk_review_per_task', 10.0)} ᴘᴇʀ ᴛᴀꜱᴋ\n\n꘎♡━━━━━♡꘎━━━━━♡꘎\n👇 <b>ᴄʜᴏᴏꜱᴇ ʏᴏᴜʀ ᴛᴀꜱᴋ ᴍᴏᴅᴇ:</b>")

def _gmail_ui_text():
    return (f"༶•┈┈⛧┈♛\n📧 <b>ɢ ᴍ ᴀ ɪ ʟ  ᴛ ᴀ ꜱ ᴋ ꜱ</b> 💎\n────── ⋆⋅☆⋅⋆ ──────\n\n🔹 <b>ꜱɪɴɢʟᴇ:</b> ₹{bot_config.get('default_gmail_reward', 8.0)} 🪙\n📦 <b>ʙᴜʟᴋ:</b> ₹{bot_config.get('bulk_gmail_per_task', 10.0)} ᴘᴇʀ ᴛᴀꜱᴋ\n\n꘎♡━━━━━♡꘎━━━━━♡꘎\n👇 <b>ᴄʜᴏᴏꜱᴇ ʏᴏᴜʀ ᴛᴀꜱᴋ ᴍᴏᴅᴇ:</b>")

def _review_markup():
    m=InlineKeyboardMarkup(row_width=1)
    m.add(premium_button("ꜱɪɴɢʟᴇ ᴛᴀꜱᴋ  ·  ᴘʀᴇᴍɪᴜᴍ", callback_data="start_single_review"), premium_button("ʙᴜʟᴋ ᴘᴀᴄᴋ  ·  ᴘʀᴇᴍɪᴜᴍ", callback_data="start_bulk_review_warn"), premium_button("ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ", callback_data="ui_home"))
    return m

def _gmail_markup():
    m=InlineKeyboardMarkup(row_width=1)
    m.add(premium_button("ꜱɪɴɢʟᴇ ɢᴍᴀɪʟ ᴛᴀꜱᴋ  ·  ᴘʀᴇᴍɪᴜᴍ", callback_data="start_single_gmail"), premium_button("ʙᴜʟᴋ ɢᴍᴀɪʟ  ·  ᴘʀᴇᴍɪᴜᴍ", callback_data="start_bulk_gmail_warn"), premium_button("ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ", callback_data="ui_home"))
    return m

@bot.message_handler(func=lambda message: message.text in ["ʀᴇᴠɪᴇᴡ ᴛᴀꜱᴋ", "📍 ʀᴇᴠɪᴇᴡ ᴛᴀꜱᴋ"])
def reply_menu_review(message):
    if not check_force_sub_and_alert(message): return
    _send_replaced_message(message.chat.id, _review_ui_text(), _review_markup())

@bot.message_handler(func=lambda message: message.text in ["ɢᴍᴀɪʟ ᴛᴀꜱᴋ", "📧 ɢᴍᴀɪʟ ᴛᴀꜱᴋ"])
def reply_menu_gmail(message):
    if not check_force_sub_and_alert(message): return
    _send_replaced_message(message.chat.id, _gmail_ui_text(), _gmail_markup())

@bot.message_handler(func=lambda message: message.text in ["ᴛᴏᴊɪ ʙᴀɴᴋ", "💸 ᴡɪᴛʜᴅʀᴀᴡ"])
def reply_menu_toji_bank(message):
    if not check_force_sub_and_alert(message): return
    user_id = message.from_user.id
    if is_user_banned(user_id):
        _send_replaced_message(message.chat.id, "🚫 <b>ʏᴏᴜʀ ᴀᴄᴄᴏᴜɴᴛ ɪꜱ ʙᴀɴɴᴇᴅ.</b>", restore_menu(user_id))
        return
    u=user_data.get(user_id,{})
    balance=float(u.get("balance",0))
    pending=sum(float(w.get("amount",0)) for w in u.get("pending_withdrawals",[]))
    available=max(0,balance-pending)
    text = (f"༶•┈┈⛧┈♛\n🏦 <b>ᴛ ᴏ ᴊ ɪ  ʙ ᴀ ɴ ᴋ</b> 💎\n────── ⋆⋅☆⋅⋆ ──────\n\n💰 <b>ᴀᴠᴀɪʟᴀʙʟᴇ ʙᴀʟᴀɴᴄᴇ:</b> ₹{available:.2f}\n⏳ <b>ᴘᴇɴᴅɪɴɢ:</b> ₹{pending:.2f}\n\n꘎♡━━━━━♡꘎━━━━━♡꘎\n✨ <b>ᴛᴏᴊɪ ʙᴀɴᴋ ᴘʀᴇᴍɪᴜᴍ ᴘᴏʀᴛᴀʟ</b>\n🌐 ᴍᴀɴᴀɢᴇ ʙᴀʟᴀɴᴄᴇ, ᴡɪᴛʜᴅʀᴀᴡᴀʟ & ᴛʀᴀɴꜱᴀᴄᴛɪᴏɴꜱ ɪɴꜱɪᴅᴇ ᴛᴇʟᴇɢʀᴀᴍ.\n\n👇 <b>ᴏᴘᴇɴ ᴛʜᴇ ᴛᴏᴊɪ ʙᴀɴᴋ ᴍɪɴɪ ᴀᴘᴘ:</b>")
    m=InlineKeyboardMarkup(row_width=1)
    m.add(premium_button("ᴏᴘᴇɴ ᴛᴏᴊɪ ʙᴀɴᴋ  ·  ᴍɪɴɪ ᴀᴘᴘ", web_app=WebAppInfo(url=_miniapp_url(user_id))), premium_button("ᴄᴏɴᴛᴀᴄᴛ ꜱᴜᴘᴘᴏʀᴛ", url="https://t.me/REAL_TOJIx"), premium_button("ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ", callback_data="ui_home"))
    _send_replaced_message(message.chat.id, text, m)

@bot.callback_query_handler(func=lambda call: call.data == "ui_home")
def ui_home(call):
    if not check_force_sub_and_alert(call): return
    user_id = call.from_user.id
    name = call.from_user.first_name or "User"
    caption = _welcome_caption(name, force_join=False)
    try: bot.delete_message(call.message.chat.id, call.message.message_id)
    except: pass
    if os.path.exists(WELCOME_PHOTO_PATH):
        with open(WELCOME_PHOTO_PATH, "rb") as photo: msg = bot.send_photo(call.message.chat.id, photo, caption=caption, parse_mode="HTML", reply_markup=restore_menu(user_id))
    else: msg = bot.send_message(call.message.chat.id, caption, parse_mode="HTML", reply_markup=restore_menu(user_id))
    _remember_ui_message(msg)
    bot.answer_callback_query(call.id)

@bot.message_handler(func=lambda message: message.text == "📢 Broadcast")
def admin_broadcast(message):
    if message.from_user.id not in ADMIN_IDS:
        return
    msg = bot.send_message(message.chat.id, "Please type or send the message (photo/video/text) you want to broadcast to all users:", reply_markup=get_cancel_menu())
    bot.register_next_step_handler(msg, process_broadcast)

def process_broadcast(message):
    if is_cancel(message): return
    bot.send_message(message.chat.id, "📢 Broadcast started. This may take some time depending on the number of users...", reply_markup=restore_menu(message.from_user.id))

    def run_broadcast():
        success_count = 0
        failed_count = 0
        blocked_count = 0
        deleted_count = 0
        for user_id in list(user_data.keys()):
            try:
                send_direct(user_id, message)
                success_count += 1
            except telebot.apihelper.ApiTelegramException as e:
                if e.error_code == 429:
                    retry_after = e.result_json.get('parameters', {}).get('retry_after', 3) if hasattr(e, 'result_json') else 3
                    time.sleep(retry_after)
                    try:
                        send_direct(user_id, message)
                        success_count += 1
                    except telebot.apihelper.ApiTelegramException as inner_e:
                        desc = inner_e.description.lower()
                        if "blocked" in desc:
                            blocked_count += 1
                        elif "deactivated" in desc or "not found" in desc:
                            deleted_count += 1
                        else:
                            failed_count += 1
                    except Exception:
                        failed_count += 1
                else:
                    desc = e.description.lower()
                    if "blocked" in desc:
                        blocked_count += 1
                    elif "deactivated" in desc or "not found" in desc:
                        deleted_count += 1
                    else:
                        failed_count += 1
            except Exception:
                failed_count += 1
            time.sleep(0.05) # Always sleep to prevent rate limiting

        group_success = 0
        for chat_id in list(known_groups):
            try:
                send_direct(chat_id, message)
                group_success += 1
            except telebot.apihelper.ApiTelegramException as e:
                if e.error_code == 429:
                    retry_after = e.result_json.get('parameters', {}).get('retry_after', 3) if hasattr(e, 'result_json') else 3
                    time.sleep(retry_after)
                    try:
                        send_direct(chat_id, message)
                        group_success += 1
                    except Exception:
                        pass
            except Exception:
                pass
            time.sleep(0.05)

        try:
            summary = (
                f"✅ *Broadcast finished!*\n\n"
                f"👤 Sent Successfully: {success_count}\n"
                f"🚫 Blocked Bot: {blocked_count}\n"
                f"🗑️ Deleted Accounts: {deleted_count}\n"
                f"⚠️ Other Errors: {failed_count}\n"
                f"👥 Groups/Channels: {group_success}"
            )
            bot.send_message(message.chat.id, summary, parse_mode="Markdown")
        except Exception:
            pass

    threading.Thread(target=run_broadcast).start()

@bot.my_chat_member_handler()
def track_chats(message):
    chat_id = message.chat.id
    new_status = message.new_chat_member.status
    if new_status in ["member", "administrator"]:
        if chat_id not in known_groups and message.chat.type in ["group", "supergroup", "channel"]:
            known_groups.append(chat_id)
            save_data()
    elif new_status in ["left", "kicked"]:
        if chat_id in known_groups:
            known_groups.remove(chat_id)
            save_data()


# ==========================================
#        PREMIUM REPLY MENU HANDLERS
# ==========================================

def _reply_menu_allowed(message):
    return message.chat.type == "private"

@bot.message_handler(func=lambda message: message.text == "ɪɴᴠɪᴛᴇ & ᴇᴀʀɴ")
def reply_menu_invite(message):
    if not _reply_menu_allowed(message): return
    if not check_force_sub_and_alert(message): return
    _send_replaced_message(message.chat.id, _invite_text(message.from_user.id), _back_markup())

@bot.message_handler(func=lambda message: message.text == "ʟᴇᴀᴅᴇʀʙᴏᴀʀᴅ")
def reply_menu_leaderboard(message):
    if not _reply_menu_allowed(message): return
    if not check_force_sub_and_alert(message): return
    _send_replaced_message(message.chat.id, _leaderboard_text(message.from_user.id), _back_markup())

@bot.message_handler(func=lambda message: message.text == "ʜᴇʟᴘ & ꜱᴜᴘᴘᴏʀᴛ")
def reply_menu_help(message):
    if not _reply_menu_allowed(message): return
    if not check_force_sub_and_alert(message): return
    _send_replaced_message(message.chat.id, _help_text(), _help_markup())

@bot.message_handler(func=lambda message: message.text == "ᴛᴏᴊɪ ʙᴀɴᴋ")
def reply_menu_toji_bank(message):
    if not _reply_menu_allowed(message): return
    if not check_force_sub_and_alert(message): return
    if is_user_banned(message.from_user.id):
        _send_replaced_message(message.chat.id, "🚫 <b>ʏᴏᴜʀ ᴀᴄᴄᴏᴜɴᴛ ɪꜱ ʙᴀɴɴᴇᴅ.</b>", restore_menu(message.from_user.id))
        return
    _send_replaced_message(message.chat.id, _withdraw_text(message.from_user.id), _withdraw_markup(message.from_user.id))

@bot.message_handler(func=lambda message: message.text == "👑 ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ")
def reply_menu_admin(message):
    if not _reply_menu_allowed(message): return
    if message.from_user.id not in ADMIN_IDS:
        return
    if not check_force_sub_and_alert(message): return
    _send_replaced_message(message.chat.id, get_dashboard_text(), _admin_markup())

# ==========================================
#           CATCH-ALL HANDLER
# ==========================================

@bot.message_handler(func=lambda message: message.text in ["🔙 Cancel", "/cancel", "Cancel", "cancel"])
def global_cancel(message):
    bot.clear_step_handler_by_chat_id(message.chat.id)
    bot.send_message(message.chat.id, "Action cancelled. Returning to main menu.", reply_markup=restore_menu(message.from_user.id))

@bot.message_handler(func=lambda message: True)
def handle_unknown(message):
    """Handles any text that is not recognized as a command button."""
    if message.chat.type == "private":
        _send_replaced_message(message.chat.id, "ɪ ᴅᴏɴ'ᴛ ᴜɴᴅᴇʀꜱᴛᴀɴᴅ ᴛʜᴀᴛ ᴄᴏᴍᴍᴀɴᴅ. ᴘʟᴇᴀꜱᴇ ᴜꜱᴇ ᴛʜᴇ ᴍᴇɴᴜ ʙᴜᴛᴛᴏɴꜱ ᴘʀᴏᴠɪᴅᴇᴅ.", restore_menu(message.from_user.id))
    else:
        if message.chat.id not in known_groups:
            known_groups.append(message.chat.id)
            save_data()

def periodic_background_checks():
    while True:
        try:
            check_expired_tasks()
        except Exception as e:
            print("Error checking expired tasks:", e)
        try:
            run_auto_broadcast_check()
        except Exception as e:
            print("Error checking auto broadcasts:", e)
        try:
            check_expired_bans()
        except Exception as e:
            print("Error checking expired bans:", e)
        time.sleep(60)


# ==========================================
#      CASHGLIDE PREMIUM SINGLE-MESSAGE UI
# ==========================================

def _back_markup():
    return InlineKeyboardMarkup().add(premium_button("ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ", callback_data="ui_home"))

def _render_text_or_caption(call, text, markup):
    """Keep navigation in one bot message whenever Telegram permits editing."""
    chat_id = call.message.chat.id
    message_id = call.message.message_id
    try:
        if getattr(call.message, "content_type", "") == "photo":
            msg = bot.edit_message_caption(text, chat_id=chat_id, message_id=message_id,
                                            parse_mode="HTML", reply_markup=markup)
        else:
            msg = bot.edit_message_text(text, chat_id=chat_id, message_id=message_id,
                                         parse_mode="HTML", reply_markup=markup)
        last_ui_messages[chat_id] = message_id
        return msg or True
    except Exception:
        try:
            bot.delete_message(chat_id, message_id)
        except Exception:
            pass
        msg = bot.send_message(chat_id, text, parse_mode="HTML", reply_markup=markup)
        return _remember_ui_message(msg)

def _home_from_call(call):
    user_id = call.from_user.id
    name = call.from_user.first_name or "User"
    caption = _welcome_caption(name, force_join=False)
    # Preserve the welcome image whenever possible.
    if getattr(call.message, "content_type", "") == "photo":
        _render_text_or_caption(call, caption, restore_menu(user_id))
    else:
        # Home can be upgraded from a text page to the branded image once.
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception:
            pass
        _send_welcome_photo(call.message.chat.id, caption, restore_menu(user_id))

def _review_ui_text():
    return (
        "༶•┈┈⛧┈♛\n"
        "📍 <b>ʀ ᴇ ᴠ ɪ ᴇ ᴡ  ᴛ ᴀ ꜱ ᴋ ꜱ</b> 💎\n"
        "────── ⋆⋅☆⋅⋆ ──────\n\n"
        f"🔹 <b>ꜱɪɴɢʟᴇ:</b> ₹{bot_config.get('default_reward', 8.0)} 🪙\n"
        f"📦 <b>ʙᴜʟᴋ:</b> ₹{bot_config.get('bulk_review_per_task', 10.0)} ᴘᴇʀ ᴛᴀꜱᴋ\n\n"
        "꘎♡━━━━━♡꘎━━━━━♡꘎\n"
        "👇 <b>ᴄʜᴏᴏꜱᴇ ʏᴏᴜʀ ᴛᴀꜱᴋ ᴍᴏᴅᴇ:</b>"
    )

def _gmail_ui_text():
    return (
        "༶•┈┈⛧┈♛\n"
        "📧 <b>ɢ ᴍ ᴀ ɪ ʟ  ᴛ ᴀ ꜱ ᴋ ꜱ</b> 💎\n"
        "────── ⋆⋅☆⋅⋆ ──────\n\n"
        f"🔹 <b>ꜱɪɴɢʟᴇ:</b> ₹{bot_config.get('default_gmail_reward', 8.0)} 🪙\n"
        f"📦 <b>ʙᴜʟᴋ:</b> ₹{bot_config.get('bulk_gmail_per_task', 10.0)} ᴘᴇʀ ᴛᴀꜱᴋ\n\n"
        "꘎♡━━━━━♡꘎━━━━━♡꘎\n"
        "👇 <b>ᴄʜᴏᴏꜱᴇ ʏᴏᴜʀ ᴛᴀꜱᴋ ᴍᴏᴅᴇ:</b>"
    )

def _review_markup():
    m=InlineKeyboardMarkup(row_width=1)
    m.add(premium_button("ꜱɪɴɢʟᴇ ᴛᴀꜱᴋ  ·  ᴘʀᴇᴍɪᴜᴍ", callback_data="start_single_review"),
          premium_button("ʙᴜʟᴋ ᴘᴀᴄᴋ  ·  ᴘʀᴇᴍɪᴜᴍ", callback_data="start_bulk_review_warn"),
          premium_button("ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ", callback_data="ui_home"))
    return m

def _gmail_markup():
    m=InlineKeyboardMarkup(row_width=1)
    m.add(premium_button("ꜱɪɴɢʟᴇ ɢᴍᴀɪʟ ᴛᴀꜱᴋ  ·  ᴘʀᴇᴍɪᴜᴍ", callback_data="start_single_gmail"),
          premium_button("ʙᴜʟᴋ ɢᴍᴀɪʟ  ·  ᴘʀᴇᴍɪᴜᴍ", callback_data="start_bulk_gmail_warn"),
          premium_button("ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ", callback_data="ui_home"))
    return m

def _wallet_text(user_id):
    u=user_data.get(user_id,{})
    balance=float(u.get("balance",0))
    pending=sum(float(w.get("amount",0)) for w in u.get("pending_withdrawals",[]))
    completed=int(u.get("tasks_completed",0))
    total_earned=float(u.get("total_earned",0))
    withdrawn=float(u.get("total_withdrawn",0))
    level=get_user_level(completed)
    return (
        "༶•┈┈⛧┈♛\n"
        "💰 <b>ᴡ ᴀ ʟ ʟ ᴇ ᴛ</b> 💎\n"
        "────── ⋆⋅☆⋅⋆ ──────\n\n"
        f"💵 <b>ᴀᴠᴀɪʟᴀʙʟᴇ:</b> ₹{balance-pending:.2f}\n"
        f"⏳ <b>ᴘᴇɴᴅɪɴɢ:</b> ₹{pending:.2f}\n"
        f"📈 <b>ᴛᴏᴛᴀʟ ᴇᴀʀɴᴇᴅ:</b> ₹{total_earned:.2f}\n"
        f"💸 <b>ᴛᴏᴛᴀʟ ᴡɪᴛʜᴅʀᴀᴡɴ:</b> ₹{withdrawn:.2f}\n"
        f"✅ <b>ᴛᴀꜱᴋꜱ:</b> {completed}\n"
        f"🏆 <b>ʟᴇᴠᴇʟ:</b> {level}\n\n"
        "꘎♡━━━━━♡꘎━━━━━♡꘎\n"
        "✨ <i>ᴋᴇᴇᴘ ᴄᴏᴍᴘʟᴇᴛɪɴɢ ᴛᴀꜱᴋꜱ ᴛᴏ ʀɪꜱᴇ ʏᴏᴜʀ ʀᴀɴᴋ.</i>"
    )

def _wallet_markup():
    m=InlineKeyboardMarkup(row_width=1)
    m.add(premium_button("ᴛᴏᴊɪ ʙᴀɴᴋ", callback_data="ui_toji_bank"),
          premium_button("ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ", callback_data="ui_home"))
    return m

def _invite_text(user_id):
    referrals=user_data.get(user_id,{}).get("referrals",0)
    try: bot_username=bot.get_me().username
    except Exception: bot_username="CashGlideBot"
    link=f"https://t.me/{bot_username}?start={user_id}"
    return (
        "༶•┈┈⛧┈♛\n"
        "👥 <b>ɪ ɴ ᴠ ɪ ᴛ ᴇ  &  ᴇ ᴀ ʀ ɴ</b> 🎊\n"
        "────── ⋆⋅☆⋅⋆ ──────\n\n"
        "🪙 <b>₹1 ʀᴇꜰᴇʀʀᴀʟ ʙᴏɴᴜꜱ</b> ᴀꜰᴛᴇʀ ᴛʜᴇ ɪɴᴠɪᴛᴇᴅ ᴜꜱᴇʀ ᴄᴏᴍᴘʟᴇᴛᴇꜱ ᴛʜᴇɪʀ ꜰɪʀꜱᴛ ᴛᴀꜱᴋ.\n\n"
        f"🔗 <b>ʏᴏᴜʀ ʟɪɴᴋ:</b>\n<code>{html.escape(link)}</code>\n\n"
        f"📊 <b>ᴛᴏᴛᴀʟ ʀᴇꜰᴇʀʀᴀʟꜱ:</b> {referrals}\n\n"
        "꘎♡━━━━━♡꘎━━━━━♡꘎\n"
        "✨ <i>ꜱʜᴀʀᴇ ʏᴏᴜʀ ʟɪɴᴋ ᴡɪᴛʜ ꜰʀɪᴇɴᴅꜱ.</i>"
    )

def _help_markup():
    m=InlineKeyboardMarkup(row_width=1)
    m.add(premium_button("ʀᴇᴠɪᴇᴡ ᴛᴜᴛᴏʀɪᴀʟ", url="https://t.me/MapreviewsEra"),
          premium_button("ɢᴍᴀɪʟ ᴛᴜᴛᴏʀɪᴀʟ", url="https://t.me/GmailworkEra"),
          premium_button("ᴄᴀꜱʜɢʟɪᴅᴇ ᴛᴜᴛᴏʀɪᴀʟ", url="https://t.me/CashGlideTutorial"),
          premium_button("ᴄᴏɴᴛᴀᴄᴛ ꜱᴜᴘᴘᴏʀᴛ", url="https://t.me/REAL_TOJIx"),
          premium_button("ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ", callback_data="ui_home"))
    return m

def _help_text():
    return (
        "༶•┈┈⛧┈♛\n"
        "🆘 <b>ʜ ᴇ ʟ ᴘ  &  ꜱ ᴜ ᴘ ᴘ ᴏ ʀ ᴛ</b> ⚜️\n"
        "────── ⋆⋅☆⋅⋆ ──────\n\n"
        "✨ ᴄʜᴏᴏꜱᴇ ᴀ ᴛᴜᴛᴏʀɪᴀʟ ʙᴇʟᴏᴡ ᴏʀ ᴄᴏɴᴛᴀᴄᴛ ꜱᴜᴘᴘᴏʀᴛ.\n\n"
        "꘎♡━━━━━♡꘎━━━━━♡꘎"
    )

def _leaderboard_text(user_id):
    sr=sorted(user_data.items(), key=lambda x:x[1].get("lifetime_reviews_completed",len(x[1].get("completed_tasks",[]))), reverse=True)
    sg=sorted(user_data.items(), key=lambda x:x[1].get("lifetime_gmails_completed",len(x[1].get("completed_gmails",[]))), reverse=True)
    lines=["༶•┈┈⛧┈♛","🏆 <b>ᴠ ɪ ᴘ  ʟ ᴇ ᴀ ᴅ ᴇ ʀ ʙ ᴏ ᴀ ʀ ᴅ</b> ✨","────── ⋆⋅☆⋅⋆ ──────","","📍 <b>ᴛᴏᴘ ʀᴇᴠɪᴇᴡ ᴡᴏʀᴋᴇʀꜱ</b>",""]
    medals=['🥇','🥈','🥉']
    for i,(uid,u) in enumerate([x for x in sr if x[1].get("lifetime_reviews_completed",len(x[1].get("completed_tasks",[])))>0][:5]):
        n=html.escape(u.get('first_name','User')); c=u.get('lifetime_reviews_completed',len(u.get('completed_tasks',[])))
        lines.append(f"{medals[i] if i<3 else '🏅'} <b>{n}</b> — {c} ʀᴇᴠɪᴇᴡꜱ")
    if len(lines)==6: lines.append("<i>No review tasks completed yet.</i>")
    lines += ["","꘎♡━━━━━♡꘎━━━━━♡꘎","","📧 <b>ᴛᴏᴘ ɢᴍᴀɪʟ ᴡᴏʀᴋᴇʀꜱ</b>",""]
    start=len(lines)
    for i,(uid,u) in enumerate([x for x in sg if x[1].get("lifetime_gmails_completed",len(x[1].get('completed_gmails',[])))>0][:5]):
        n=html.escape(u.get('first_name','User')); c=u.get('lifetime_gmails_completed',len(u.get('completed_gmails',[])))
        lines.append(f"{medals[i] if i<3 else '🏅'} <b>{n}</b> — {c} ɢᴍᴀɪʟꜱ")
    if len(lines)==start: lines.append("<i>No Gmail tasks completed yet.</i>")
    u=user_data.get(user_id,{})
    lines += ["","────── ⋆⋅☆⋅⋆ ──────",f"👤 <b>ʏᴏᴜʀ ꜱᴛᴀᴛꜱ</b>",f"📍 ʀᴇᴠɪᴇᴡꜱ: {u.get('lifetime_reviews_completed',len(u.get('completed_tasks',[])))}",f"📧 ɢᴍᴀɪʟꜱ: {u.get('lifetime_gmails_completed',len(u.get('completed_gmails',[])))}","","꘎♡━━━━━♡꘎"]
    return "\n".join(lines)

def _withdraw_text(user_id):
    u=user_data.get(user_id,{})
    balance=float(u.get("balance",0))
    pending=sum(float(w.get("amount",0)) for w in u.get("pending_withdrawals",[]))
    available=max(0,balance-pending)
    return (
        "༶•┈┈⛧┈♛\n"
        "🏦 <b>ᴛ ᴏ ᴊ ɪ  ʙ ᴀ ɴ ᴋ</b> 💎\n"
        "────── ⋆⋅☆⋅⋆ ──────\n\n"
        f"💰 <b>ᴀᴠᴀɪʟᴀʙʟᴇ ʙᴀʟᴀɴᴄᴇ:</b> ₹{available:.2f}\n"
        f"⏳ <b>ᴘᴇɴᴅɪɴɢ:</b> ₹{pending:.2f}\n\n"
        "꘎♡━━━━━♡꘎━━━━━♡꘎\n"
        "✨ <b>ᴛᴏᴊɪ ʙᴀɴᴋ ᴘʀᴇᴍɪᴜᴍ ᴘᴏʀᴛᴀʟ</b>\n"
        "🌐 ᴍᴀɴᴀɢᴇ ʙᴀʟᴀɴᴄᴇ, ᴡɪᴛʜᴅʀᴀᴡᴀʟ & ᴛʀᴀɴꜱᴀᴄᴛɪᴏɴꜱ ɪɴꜱɪᴅᴇ ᴛᴇʟᴇɢʀᴀᴍ.\n\n"
        "👇 <b>ᴏᴘᴇɴ ᴛʜᴇ ᴛᴏᴊɪ ʙᴀɴᴋ ᴍɪɴɪ ᴀᴘᴘ:</b>"
    )


def _miniapp_url(user_id):
    """Build a user-specific Mini App URL from the existing bot database.
    The same TOJI_db.json remains the source of truth, so old balances/data are preserved.
    """
    u = user_data.get(user_id, {})
    balance = float(u.get("balance", 0.0))
    withdrawn = float(u.get("total_withdrawn", 0.0))
    pending = sum(float(w.get("amount", 0.0)) for w in u.get("pending_withdrawals", []) or [])
    earned = float(u.get("total_earned", balance + withdrawn))
    history = list(u.get("payment_history", []) or [])[-30:]
    requests = []
    for uid, ud in user_data.items():
        for w in ud.get("pending_withdrawals", []) or []:
            if w.get("method") != "UPI":
                continue
            details = w.get("details_val", "")
            if isinstance(details, dict):
                details = details.get("text", "")
            requests.append({
                "id": str(w.get("id", "")), "user_id": int(uid),
                "name": ud.get("first_name", "User"),
                "amount": float(w.get("amount", 0)),
                "upi": str(details), "status": "Pending"
            })
    params = {
        "bal": f"{max(0.0, balance-pending):.2f}",
        "earned": f"{earned:.2f}",
        "withdrawn": f"{withdrawn:.2f}",
        "pending": f"{pending:.2f}",
        "history": json.dumps(history, ensure_ascii=False, separators=(",", ":")),
        "admin_ids": ",".join(str(x) for x in ADMIN_IDS),
        "requests": json.dumps(requests, ensure_ascii=False, separators=(",", ":")) if user_id in ADMIN_IDS else "[]",
        "proof": WEB_SETTINGS.get("proof", "@TOJIXWORKS"),
        "contact": WEB_SETTINGS.get("contact", "@TOJI_HERE_PERSONALBOT"),
        "api": WEB_API_BASE,
    }
    return WITHDRAW_WEB_URL.split("#", 1)[0] + ("&" if "?" in WITHDRAW_WEB_URL else "?") + urlencode(params)

def _withdraw_markup(user_id=None):
    m=InlineKeyboardMarkup(row_width=1)
    app_url = _miniapp_url(user_id) if user_id is not None else WITHDRAW_WEB_URL
    m.add(premium_button("ᴏᴘᴇɴ ᴛᴏᴊɪ ʙᴀɴᴋ  ·  ᴍɪɴɪ ᴀᴘᴘ", web_app=WebAppInfo(url=app_url)),
          premium_button("ᴄᴏɴᴛᴀᴄᴛ ꜱᴜᴘᴘᴏʀᴛ", url="https://t.me/REAL_TOJIx"),
          premium_button("ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ", callback_data="ui_home"))
    return m

def _admin_markup():
    m=InlineKeyboardMarkup(row_width=2)
    m.add(premium_button("ʀᴇᴠɪᴇᴡ ᴘᴀɴᴇʟ", callback_data="admin_review_panel"),
          premium_button("ɢᴍᴀɪʟ ᴘᴀɴᴇʟ", callback_data="admin_gmail_panel"),
          premium_button("ʙᴏᴛ ꜱᴇᴛᴛɪɴɢꜱ", callback_data="admin_bot_settings"),
          premium_button("ʙᴀᴄᴋ ᴛᴏ ʜᴏᴍᴇ", callback_data="ui_home"))
    return m

@bot.callback_query_handler(func=lambda call: call.data == "ui_home")
def ui_home(call):
    if not check_force_sub_and_alert(call): return
    _home_from_call(call); bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "ui_review")
def ui_review(call):
    if not check_force_sub_and_alert(call): return
    _render_text_or_caption(call,_review_ui_text(),_review_markup()); bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "ui_gmail")
def ui_gmail(call):
    if not check_force_sub_and_alert(call): return
    _render_text_or_caption(call,_gmail_ui_text(),_gmail_markup()); bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "ui_wallet")
def ui_wallet(call):
    if not check_force_sub_and_alert(call): return
    _render_text_or_caption(call,_wallet_text(call.from_user.id),_wallet_markup()); bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "ui_invite")
def ui_invite(call):
    if not check_force_sub_and_alert(call): return
    _render_text_or_caption(call,_invite_text(call.from_user.id),_back_markup()); bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "ui_help")
def ui_help(call):
    if not check_force_sub_and_alert(call): return
    _render_text_or_caption(call,_help_text(),_help_markup()); bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "ui_leaderboard")
def ui_leaderboard(call):
    if not check_force_sub_and_alert(call): return
    _render_text_or_caption(call,_leaderboard_text(call.from_user.id),_back_markup()); bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data in ["ui_toji_bank", "ui_withdraw"])
def ui_toji_bank(call):
    if not check_force_sub_and_alert(call): return
    if is_user_banned(call.from_user.id):
        bot.answer_callback_query(call.id,"🚫 ʏᴏᴜʀ ᴀᴄᴄᴏᴜɴᴛ ɪꜱ ʙᴀɴɴᴇᴅ.",show_alert=True); return
    _render_text_or_caption(call,_withdraw_text(call.from_user.id),_withdraw_markup(call.from_user.id)); bot.answer_callback_query(call.id)

@bot.callback_query_handler(func=lambda call: call.data == "ui_admin")
def ui_admin(call):
    if call.from_user.id not in ADMIN_IDS:
        bot.answer_callback_query(call.id,"❌ ᴀᴅᴍɪɴ ᴏɴʟʏ.",show_alert=True); return
    _render_text_or_caption(call,get_dashboard_text(),_admin_markup()); bot.answer_callback_query(call.id)


# ==========================================
#          TOJI BANK MINI APP API
# ==========================================
try:
    from flask import Flask, request as flask_request, jsonify
    web_app = Flask(__name__)

    @web_app.after_request
    def _cors(resp):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, X-Telegram-Init-Data"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
        return resp

    def _telegram_webapp_user(init_data):
        if not init_data:
            return None
        try:
            pairs = dict(parse_qsl(init_data, keep_blank_values=True))
            supplied_hash = pairs.pop("hash", "")
            if not supplied_hash:
                return None
            check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
            secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
            calc = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(calc, supplied_hash):
                return None
            auth_date = int(pairs.get("auth_date", "0") or 0)
            if auth_date and time.time() - auth_date > 86400:
                return None
            raw_user = pairs.get("user", "{}")
            return json.loads(unquote(raw_user))
        except Exception:
            return None

    def _api_user():
        u = _telegram_webapp_user(flask_request.headers.get("X-Telegram-Init-Data", ""))
        if not u or not u.get("id"):
            return None
        return u

    @web_app.route("/api/withdraw", methods=["POST", "OPTIONS"])
    def api_withdraw():
        if flask_request.method == "OPTIONS":
            return ("", 204)
        tg_user = _api_user()
        if not tg_user:
            return jsonify(error="Telegram session verification failed."), 401
        try:
            body = flask_request.get_json(force=True) or {}
            amount = float(body.get("amount", 0))
            upi = str(body.get("upi", "")).strip()
        except Exception:
            return jsonify(error="Invalid withdrawal data."), 400
        if amount < 20:
            return jsonify(error="Minimum withdrawal is ₹20."), 400
        if not __import__('re').match(r"^[\w.\-]{2,}@[A-Za-z0-9.\-]{2,}$", upi):
            return jsonify(error="Enter a valid UPI ID."), 400
        uid = int(tg_user["id"])
        with data_lock:
            u = user_data.setdefault(uid, {"balance": 0.0, "tasks_completed": 0, "referrals": 0, "completed_tasks": [], "pending_tasks": []})
            pending = sum(float(w.get("amount", 0)) for w in u.get("pending_withdrawals", []) or [])
            available = float(u.get("balance", 0)) - pending
            if amount > available:
                return jsonify(error=f"Insufficient available balance. Available: ₹{available:.2f}"), 400
            wid = str(int(time.time() * 1000))
            w = {"id": wid, "amount": amount, "method": "UPI", "speed": "std", "fee": 0.0, "details_type": "text", "details_val": upi}
            u.setdefault("pending_withdrawals", []).append(w)
            u["first_name"] = tg_user.get("first_name") or "User"
            if tg_user.get("username"): u["username"] = tg_user["username"]
            u.setdefault("payment_history", []).append({"method":"UPI", "amount":amount, "status":"Pending", "date":get_ist_time().strftime("%d %b %Y, %I:%M %p"), "id":wid, "user_id":uid, "name":u["first_name"], "upi":upi})
            u["payment_history"] = u["payment_history"][-100:]
            save_data()
        request_obj = {"id":wid,"amount":amount,"method":"UPI","status":"Pending","upi":upi,"details":upi,"name":u.get("first_name","User"),"user_id":uid}
        markup = InlineKeyboardMarkup(row_width=2)
        markup.add(InlineKeyboardButton("✅ Pay", callback_data=f"pay_with_{uid}_{wid}"), InlineKeyboardButton("❌ Reject", callback_data=f"rej_with_{uid}_{wid}"))
        caption = f"<b>💸 UPI Withdrawal Request</b>\n👤 {html.escape(u.get('first_name','User'))}" + (f" (@{html.escape(u.get('username'))})" if u.get('username') else "") + f"\n🆔 <code>{uid}</code>\n💰 Amount: <b>₹{amount:.2f}</b>\n🏦 UPI: <code>{html.escape(upi)}</code>"
        for admin_id in ADMIN_IDS:
            try: bot.send_message(admin_id, caption, parse_mode="HTML", reply_markup=markup)
            except Exception: pass
        return jsonify(ok=True, request=request_obj)

    @web_app.route("/api/user/withdrawals", methods=["GET", "OPTIONS"])
    def api_user_withdrawals():
        if flask_request.method == "OPTIONS": return ("", 204)
        tg_user = _api_user()
        if not tg_user: return jsonify(error="Telegram session verification failed."), 401
        uid=int(tg_user["id"])
        u=user_data.get(uid,{})
        rows=[]
        for x in u.get("payment_history",[]) or []:
            if str(x.get("method","UPI")).upper()=="UPI": rows.append(x)
        return jsonify(requests=rows[-50:], proof=WEB_SETTINGS.get("proof","@TOJIXWORKS"), contact=WEB_SETTINGS.get("contact","@TOJI_HERE_PERSONALBOT"))

    @web_app.route("/api/admin/withdrawals", methods=["GET", "OPTIONS"])
    def api_admin_withdrawals():
        if flask_request.method == "OPTIONS": return ("", 204)
        tg_user=_api_user()
        if not tg_user or int(tg_user["id"]) not in ADMIN_IDS: return jsonify(error="Admin only."),403
        rows=[]
        for uid,u in user_data.items():
            for w in u.get("pending_withdrawals",[]) or []:
                if w.get("method")!="UPI": continue
                details=w.get("details_val","")
                rows.append({"id":str(w.get("id","")),"user_id":int(uid),"name":u.get("first_name","User"),"username":u.get("username",""),"amount":float(w.get("amount",0)),"upi":str(details),"status":"Pending"})
            for h in u.get("payment_history",[]) or []:
                if str(h.get("method","UPI")).upper()!="UPI": continue
                st=str(h.get("status","")).lower()
                if not any(k in st for k in ("success","paid","approved","rejected","reject")): continue
                rows.append({"id":str(h.get("id", "")),"user_id":int(uid),"name":u.get("first_name","User"),"username":u.get("username",""),"amount":float(h.get("amount",0)),"upi":str(h.get("upi") or h.get("details") or ""),"status":"Paid" if any(k in st for k in ("success","paid","approved")) else "Rejected"})
        return jsonify(requests=rows[-500:], proof=WEB_SETTINGS.get("proof","@TOJIXWORKS"), contact=WEB_SETTINGS.get("contact","@TOJI_HERE_PERSONALBOT"))

    @web_app.route("/api/admin/withdrawal/<wid>", methods=["POST", "OPTIONS"])
    def api_admin_withdrawal(wid):
        if flask_request.method == "OPTIONS": return ("", 204)
        tg_user=_api_user()
        if not tg_user or int(tg_user["id"]) not in ADMIN_IDS: return jsonify(error="Admin only."),403
        body=flask_request.get_json(force=True) or {}; action=body.get("action")
        if action not in ("approve","reject"): return jsonify(error="Invalid action."),400
        target_uid=None; w=None
        with data_lock:
            for tuid,tu in user_data.items():
                for candidate in tu.get("pending_withdrawals",[]) or []:
                    if str(candidate.get("id"))==str(wid): target_uid,w=tuid,candidate; break
                if w: break
            if not w: return jsonify(error="Request not found or already processed."),404
            amount=float(w.get("amount",0))
            tu=user_data[target_uid]
            if action=="reject":
                tu.get("pending_withdrawals",[]).remove(w)
                tu.setdefault("payment_history",[]).append({"method":"UPI","amount":amount,"status":"Rejected","date":get_ist_time().strftime("%d %b %Y, %I:%M %p"),"id":str(wid),"user_id":target_uid,"name":tu.get("first_name","User"),"upi":w.get("details_val","")})
                status="Rejected"
            else:
                if float(tu.get("balance",0)) < amount: return jsonify(error="User balance is insufficient."),400
                tu["balance"]=float(tu.get("balance",0))-amount
                tu["total_withdrawn"]=float(tu.get("total_withdrawn",0))+amount
                tu.setdefault("paid_withdrawals_ts",[]).append((time.time(),amount))
                tu.setdefault("payment_history",[]).append({"method":"UPI","amount":amount,"status":"Success","date":get_ist_time().strftime("%d %b %Y, %I:%M %p"),"id":str(wid),"user_id":target_uid,"name":tu.get("first_name","User"),"upi":w.get("details_val","")})
                tu.get("pending_withdrawals",[]).remove(w)
                status="Paid"
            tu["payment_history"]=tu["payment_history"][-100:]
            save_data()
        try:
            if action=="reject": bot.send_message(target_uid, f"🚫 Your UPI withdrawal of ₹{amount:.2f} was rejected by admin.\n\nCONTACT~ {WEB_SETTINGS.get('contact','@TOJI_HERE_PERSONALBOT')}")
            else: bot.send_message(target_uid, f"✅ Your UPI withdrawal of ₹{amount:.2f} has been approved and marked as paid.\n\nPROOF~ {WEB_SETTINGS.get('proof','@TOJIXWORKS')}")
        except Exception: pass
        return jsonify(ok=True,status=status)

    @web_app.route("/api/admin/settings", methods=["POST", "OPTIONS"])
    def api_admin_settings():
        if flask_request.method == "OPTIONS": return ("",204)
        tg_user=_api_user()
        if not tg_user or int(tg_user["id"]) not in ADMIN_IDS: return jsonify(error="Admin only."),403
        body=flask_request.get_json(force=True) or {}; typ=body.get("type"); value=str(body.get("value","")).strip()
        if typ not in ("proof","contact") or not value: return jsonify(error="Invalid setting."),400
        if not value.startswith("@"): value="@"+value
        WEB_SETTINGS[typ]=value
        save_data()
        return jsonify(ok=True,value=value)

    def start_web_api():
        port=int(os.getenv("PORT","8080"))
        web_app.run(host="0.0.0.0",port=port,debug=False,use_reloader=False)
except Exception as _flask_error:
    web_app=None
    def start_web_api():
        print("Flask API unavailable:", _flask_error)

if __name__ == "__main__":
    print("Bot is starting up... Press Ctrl+C to stop.")
    if web_app is not None:
        api_thread = threading.Thread(target=start_web_api, daemon=True)
        api_thread.start()
    background_thread = threading.Thread(target=periodic_background_checks)
    background_thread.daemon = True
    background_thread.start()
    bot.infinity_polling(none_stop=True)
