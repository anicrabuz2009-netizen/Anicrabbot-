import logging
import os
import sqlite3
import datetime
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from telegram.error import RetryAfter, TimedOut

# Bot tokeni
BOT_TOKEN = "8461887536:AAGpYdaJLskR2mcBDzEhG6BB9BZBnhpV4lY"
ADMIN_IDS = [7991544389]  # Sizning ID'ingiz

# Papkalar
VIDEO_FOLDER = "anime_videos"
POSTER_FOLDER = "posters"
DB_FILE = "anicrab.db"

# Papkalarni yaratish
os.makedirs(VIDEO_FOLDER, exist_ok=True)
os.makedirs(POSTER_FOLDER, exist_ok=True)

# Logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
SEARCH_BY_CODE = 1
ADD_CHANNEL = 2

# ==================== MA'LUMOTLAR BAZASI ====================
class Database:
    def __init__(self):
        self.init_db()
    
    def init_db(self):
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            
            # Foydalanuvchilar
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    join_date TEXT,
                    last_active TEXT
                )
            ''')
            
            # KANALLAR
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS channels (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT,
                    channel_name TEXT,
                    channel_link TEXT,
                    is_active INTEGER DEFAULT 1,
                    added_by INTEGER,
                    added_date TEXT,
                    order_num INTEGER DEFAULT 0
                )
            ''')
            
            # Anime ma'lumotlari
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS anime (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT UNIQUE,
                    title TEXT,
                    title_ru TEXT,
                    title_en TEXT,
                    genre TEXT,
                    studio TEXT,
                    year INTEGER,
                    episodes INTEGER,
                    rating TEXT,
                    description TEXT,
                    language TEXT DEFAULT 'O\'zbekcha',
                    voice_actor TEXT,
                    poster TEXT,
                    channel_post_id INTEGER,
                    created_date TEXT
                )
            ''')
            
            # Anime qismlari
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS episodes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    anime_id INTEGER,
                    episode_number INTEGER,
                    title TEXT,
                    video_path TEXT,
                    duration TEXT,
                    file_size TEXT,
                    views INTEGER DEFAULT 0,
                    added_date TEXT,
                    FOREIGN KEY (anime_id) REFERENCES anime (id) ON DELETE CASCADE,
                    UNIQUE(anime_id, episode_number)
                )
            ''')
            
            # Sozlamalar
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            
            # Default sozlamalar
            default_settings = [
                ('bot_name', 'Anicrab.uz'),
                ('bot_username', 'anicrab_bot'),
                ('watch_button_text', '🎬 Tomosha qilish'),
                ('download_button_text', '📥 Yuklab olish'),
            ]
            
            for key, value in default_settings:
                cursor.execute('INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)', (key, value))
            
            conn.commit()
    
    def execute(self, query: str, params: tuple = ()):
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            conn.commit()
            return cursor
    
    def fetch_one(self, query: str, params: tuple = ()):
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchone()
    
    def fetch_all(self, query: str, params: tuple = ()):
        with sqlite3.connect(DB_FILE) as conn:
            cursor = conn.cursor()
            cursor.execute(query, params)
            return cursor.fetchall()
    
    def get_active_channels(self):
        """Faol kanallarni olish"""
        return self.fetch_all('''
            SELECT channel_id, channel_name, channel_link FROM channels 
            WHERE is_active = 1
            ORDER BY order_num
        ''')
    
    def get_all_channels(self):
        """Barcha kanallarni olish"""
        return self.fetch_all('''
            SELECT id, channel_id, channel_name, channel_link, is_active 
            FROM channels
            ORDER BY is_active DESC, order_num
        ''')
    
    def toggle_channel(self, channel_id):
        """Kanal holatini o'zgartirish"""
        current = self.fetch_one("SELECT is_active FROM channels WHERE id = ?", (channel_id,))
        if current:
            new_status = 0 if current[0] == 1 else 1
            self.execute("UPDATE channels SET is_active = ? WHERE id = ?", (new_status, channel_id))
            return new_status
        return None
    
    def add_channel(self, channel_id, channel_name, channel_link, added_by):
        """Yangi kanal qo'shish"""
        self.execute('''
            INSERT INTO channels (channel_id, channel_name, channel_link, is_active, added_by, added_date)
            VALUES (?, ?, ?, 1, ?, ?)
        ''', (channel_id, channel_name, channel_link, added_by, datetime.datetime.now().isoformat()))
    
    def delete_channel(self, channel_id):
        """Kanalni o'chirish"""
        self.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
    
    def get_anime_by_code(self, code: str):
        """Kod bo'yicha anime topish"""
        code = code.strip().upper()
        return self.fetch_one('''
            SELECT id, code, title, title_ru, title_en, genre, studio, 
                   year, episodes, rating, description, language, voice_actor, poster
            FROM anime WHERE code = ? OR code LIKE ? OR LOWER(code) = ?
        ''', (code, f'%{code}%', code.lower()))
    
    def get_anime_episodes(self, anime_id: int):
        """Anime qismlarini olish"""
        return self.fetch_all('''
            SELECT id, episode_number, title, video_path, duration, file_size, views
            FROM episodes
            WHERE anime_id = ?
            ORDER BY episode_number
        ''', (anime_id,))

db = Database()

# ==================== ADMIN TEKSHIRISH ====================
async def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

# ==================== START KOMANDASI ====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    
    # Foydalanuvchini bazaga qo'shish
    db.execute('''
        INSERT OR IGNORE INTO users (user_id, username, first_name, join_date, last_active)
        VALUES (?, ?, ?, ?, ?)
    ''', (user.id, user.username, user.first_name, datetime.datetime.now().isoformat(), datetime.datetime.now().isoformat()))
    
    # /start code_10 formatidan kodni olish
    if context.args and context.args[0].startswith('code_'):
        code = context.args[0].replace('code_', '')
        
        anime = db.get_anime_by_code(code)
        
        if anime:
            await show_anime_episodes(update, context, anime)
            return
    
    # Oddiy start
    text = (
        "👋 *Salom botimizga xush kelipsiz!*\n\n"
        "🤖 *Anicrab.uz* - Eng sara animelar\n\n"
        "🔢 *Anime kodini yuboring* (masalan: `10`, `186`, `001`)\n\n"
        "👇 Quyidagi tugmalardan birini tanlang:"
    )
    
    # Faol kanallarni olish
    active_channels = db.get_active_channels()
    
    keyboard = [
        [InlineKeyboardButton("🔢 Kod bilan qidirish", callback_data='search_by_code')]
    ]
    
    # Faol kanallarga tugma qo'shish
    for channel in active_channels:
        channel_id, channel_name, channel_link = channel
        keyboard.append([InlineKeyboardButton(f"📢 {channel_name}", url=channel_link)])
    
    if await is_admin(user.id):
        keyboard.append([InlineKeyboardButton("👑 Admin panel", callback_data='admin_panel')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await update.message.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup, disable_web_page_preview=True)

# ==================== KOD BILAN QIDIRISH ====================
async def search_by_code_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = (
        "🔢 *Anime kodi bilan qidirish*\n\n"
        "Qidirmoqchi bo'lgan anime kodini yozing.\n\n"
        "Masalan: `10`, `186`, `001`, `025`\n\n"
        "❌ Bekor qilish uchun /cancel"
    )
    
    keyboard = [[InlineKeyboardButton("◀️ Orqaga", callback_data='main_menu')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup)
    
    return SEARCH_BY_CODE

async def search_by_code_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    code = update.message.text.strip()
    
    # Foydalanuvchi aktivligini yangilash
    db.execute("UPDATE users SET last_active = ? WHERE user_id = ?", (datetime.datetime.now().isoformat(), user_id))
    
    code = code.replace('#', '').replace(' ', '').upper()
    
    anime = db.get_anime_by_code(code)
    
    if not anime:
        # Faol kanallarni olish
        active_channels = db.get_active_channels()
        
        keyboard = [[InlineKeyboardButton("🔢 Qayta qidirish", callback_data='search_by_code')]]
        
        for channel in active_channels:
            channel_id, channel_name, channel_link = channel
            keyboard.append([InlineKeyboardButton(f"📢 {channel_name}", url=channel_link)])
        
        await update.message.reply_text(
            f"❌ *Hech narsa topilmadi!*\n\n"
            f"'{code}' kodli anime topilmadi.\n\n"
            f"🔍 Iltimos, to'g'ri kod kiriting yoki kanallardan toping.",
            parse_mode='Markdown',
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return ConversationHandler.END
    
    await show_anime_episodes(update, context, anime)

async def show_anime_episodes(update: Update, context: ContextTypes.DEFAULT_TYPE, anime):
    """Anime qismlarini ko'rsatish"""
    
    anime_id, code, title, title_ru, title_en, genre, studio, year, episodes, rating, description, language, voice_actor, poster = anime
    
    episode_list = db.get_anime_episodes(anime_id)
    
    text = (
        f"🎬 *{title}*\n"
        f"🔢 *Kod:* `{code}`\n\n"
        f"📌 *MA'LUMOTLAR*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🎭 Janr: {genre}\n"
        f"🏢 Studiya: {studio}\n"
        f"📅 Yil: {year}\n"
        f"📦 Qismlar: {episodes} ta\n"
        f"⭐ Reyting: {rating}\n"
        f"🎙 Til: {language}\n"
        f"🎤 Ovoz berdi: {voice_actor}\n\n"
        f"📝 *Tavsif*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{description}\n\n"
        f"📥 *QUYIDAGI TUGMALAR ORQALI YUKLAB OLING:*\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    
    # Yuklab olish tugmalari (barcha qismlar)
    keyboard = []
    
    # Qismlar tugmalari (5 tadan)
    episode_row = []
    for i, ep in enumerate(episode_list, 1):
        ep_id, ep_num, ep_title, ep_path, ep_duration, ep_size, ep_views = ep
        episode_row.append(InlineKeyboardButton(f"{ep_num}-qism", callback_data=f'download_{anime_id}_{ep_num}'))
        if len(episode_row) == 5:
            keyboard.append(episode_row)
            episode_row = []
    
    if episode_row:
        keyboard.append(episode_row)
    
    keyboard.append([InlineKeyboardButton("🔍 Yangi kod qidirish", callback_data='search_by_code')])
    keyboard.append([InlineKeyboardButton("◀️ Orqaga", callback_data='main_menu')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.message:
        await update.message.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)
    
    return ConversationHandler.END

# ==================== YUKLAB OLISH ====================
async def download_episode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Qismni yuklab olish (video yuborish)"""
    
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    # Foydalanuvchi aktivligini yangilash
    db.execute("UPDATE users SET last_active = ? WHERE user_id = ?", (datetime.datetime.now().isoformat(), user_id))
    
    data = query.data.replace('download_', '')
    anime_id, episode_num = data.split('_')
    anime_id = int(anime_id)
    episode_num = int(episode_num)
    
    anime = db.fetch_one("SELECT code, title, voice_actor FROM anime WHERE id = ?", (anime_id,))
    
    if not anime:
        await query.edit_message_text("❌ Anime topilmadi!")
        return
    
    code, title, voice_actor = anime
    
    episode = db.fetch_one('''
        SELECT id, title, video_path, duration, file_size FROM episodes 
        WHERE anime_id = ? AND episode_number = ?
    ''', (anime_id, episode_num))
    
    if not episode:
        await query.edit_message_text(f"❌ {episode_num}-qism topilmadi!")
        return
    
    ep_id, ep_title, video_path, duration, file_size = episode
    
    # Yuklashlar sonini oshirish
    db.execute("UPDATE episodes SET views = views + 1 WHERE id = ?", (ep_id,))
    
    caption = (
        f"🎬 *{title}* - {episode_num}-qism\n\n"
        f"⏱ Davomiyligi: {duration}\n"
        f"📦 Hajmi: {file_size}\n"
        f"🎤 Ovoz berdi: {voice_actor}\n"
        f"🔢 Kod: `{code}`\n\n"
        f"📥 Yuklab olish tugallandi!\n\n"
        f"🤖 Anicrab.uz"
    )
    
    # Tugmalar
    keyboard = []
    nav_row = []
    
    # Oldingi qism
    if episode_num > 1:
        nav_row.append(InlineKeyboardButton("⬅️ Oldingi", callback_data=f'download_{anime_id}_{episode_num-1}'))
    
    # Keyingi qism
    next_ep = db.fetch_one("SELECT id FROM episodes WHERE anime_id = ? AND episode_number = ?", (anime_id, episode_num + 1))
    if next_ep:
        nav_row.append(InlineKeyboardButton("➡️ Keyingi", callback_data=f'download_{anime_id}_{episode_num+1}'))
    
    if nav_row:
        keyboard.append(nav_row)
    
    keyboard.append([InlineKeyboardButton("🔙 Barcha qismlar", callback_data=f'back_to_anime_{anime_id}')])
    keyboard.append([InlineKeyboardButton("🔍 Yangi kod qidirish", callback_data='search_by_code')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    full_path = os.path.join(VIDEO_FOLDER, video_path)
    
    # Yuklab olish xabarini o'chirish
    try:
        await query.message.delete()
    except:
        pass
    
    if os.path.exists(full_path):
        with open(full_path, 'rb') as video:
            await query.message.reply_video(
                video=video,
                caption=caption,
                parse_mode='Markdown',
                reply_markup=reply_markup,
                read_timeout=300,
                write_timeout=300
            )
    else:
        await query.message.reply_text(
            f"❌ Video topilmadi!\n\n{title} - {episode_num}-qism\n\nAdmin bilan bog'laning: @anicrab_admin",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 Barcha qismlar", callback_data=f'back_to_anime_{anime_id}')
            ]])
        )

async def back_to_anime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Anime qismlariga qaytish"""
    
    query = update.callback_query
    await query.answer()
    
    anime_id = int(query.data.replace('back_to_anime_', ''))
    
    anime = db.fetch_one('''
        SELECT id, code, title, title_ru, title_en, genre, studio, 
               year, episodes, rating, description, language, voice_actor, poster
        FROM anime WHERE id = ?
    ''', (anime_id,))
    
    if anime:
        # Eski xabarni o'chirish
        try:
            await query.message.delete()
        except:
            pass
        
        # Yangi xabar yuborish
        await show_anime_episodes_callback(query.message, context, anime)

async def show_anime_episodes_callback(message, context, anime):
    """Callback uchun anime qismlarini ko'rsatish"""
    
    anime_id, code, title, title_ru, title_en, genre, studio, year, episodes, rating, description, language, voice_actor, poster = anime
    
    episode_list = db.get_anime_episodes(anime_id)
    
    text = (
        f"🎬 *{title}*\n"
        f"🔢 *Kod:* `{code}`\n\n"
        f"📌 *MA'LUMOTLAR*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🎭 Janr: {genre}\n"
        f"🏢 Studiya: {studio}\n"
        f"📅 Yil: {year}\n"
        f"📦 Qismlar: {episodes} ta\n"
        f"⭐ Reyting: {rating}\n"
        f"🎙 Til: {language}\n"
        f"🎤 Ovoz berdi: {voice_actor}\n\n"
        f"📝 *Tavsif*\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"{description}\n\n"
        f"📥 *QUYIDAGI TUGMALAR ORQALI YUKLAB OLING:*\n"
        f"━━━━━━━━━━━━━━━━━━"
    )
    
    keyboard = []
    
    # Qismlar tugmalari (5 tadan)
    episode_row = []
    for i, ep in enumerate(episode_list, 1):
        ep_id, ep_num, ep_title, ep_path, ep_duration, ep_size, ep_views = ep
        episode_row.append(InlineKeyboardButton(f"{ep_num}-qism", callback_data=f'download_{anime_id}_{ep_num}'))
        if len(episode_row) == 5:
            keyboard.append(episode_row)
            episode_row = []
    
    if episode_row:
        keyboard.append(episode_row)
    
    keyboard.append([InlineKeyboardButton("🔍 Yangi kod qidirish", callback_data='search_by_code')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await message.reply_text(text, parse_mode='Markdown', reply_markup=reply_markup)

# ==================== ASOSIY MENYU ====================
async def main_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Asosiy menyu"""
    
    query = update.callback_query
    await query.answer()
    
    user_id = query.from_user.id
    
    text = (
        "🏠 *Bosh menyu*\n\n"
        "🔢 *Anime kodini yuboring* (masalan: `10`, `186`, `001`)\n\n"
        "👇 Quyidagi tugmalardan birini tanlang:"
    )
    
    # Faol kanallarni olish
    active_channels = db.get_active_channels()
    
    keyboard = [
        [InlineKeyboardButton("🔢 Kod bilan qidirish", callback_data='search_by_code')]
    ]
    
    # Faol kanallarga tugma qo'shish
    for channel in active_channels:
        channel_id, channel_name, channel_link = channel
        keyboard.append([InlineKeyboardButton(f"📢 {channel_name}", url=channel_link)])
    
    if await is_admin(user_id):
        keyboard.append([InlineKeyboardButton("👑 Admin panel", callback_data='admin_panel')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=reply_markup, disable_web_page_preview=True)

# ==================== ADMIN PANEL ====================
async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin panel - Kanallarni boshqarish"""
    
    query = update.callback_query
    await query.answer()
  
