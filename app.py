import streamlit as st
import os, io, tempfile, logging, asyncio
from docx import Document
import PyPDF2
import speech_recognition as sr
from pydub import AudioSegment
import httpx
import edge_tts
from httpx_oauth.clients.google import GoogleOAuth2
from sambanova_agent import translate_with_sambanova
from dotenv import load_dotenv
import pytesseract
from PIL import Image, ImageEnhance
import sqlite3
import hashlib
import secrets
from datetime import datetime
import io
from google.cloud import vision
from google.oauth2 import service_account
import requests
import json
import base64
import re

load_dotenv()
CLIENT_ID = os.getenv("CLIENT_ID")
CLIENT_SECRET = os.getenv("CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI")
DATABASE_NAME = "users.db"
pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

os.environ["PATH"] += os.pathsep + r"C:\\Users\\Hannah\\OneDrive\\Desktop\\ffmpeg\\ffmpeg-2025-07-01-git-11d1b71c31-essentials_build\\bin"
AudioSegment.converter = r"C:\\Users\\Hannah\\OneDrive\\Desktop\\ffmpeg\\ffmpeg-2025-07-01-git-11d1b71c31-essentials_build\\bin\\ffmpeg.exe"

st.set_page_config(page_title=" Bytebrains Translator", layout="wide", page_icon="")
logging.basicConfig(level=logging.INFO)

client = GoogleOAuth2(CLIENT_ID, CLIENT_SECRET)

st.session_state.setdefault("page", "playground")
st.session_state.setdefault("dark_mode", False)

def get_theme_colors():
    if st.session_state.dark_mode:
        return {
            "bg_primary": "#0f172a",
            "bg_secondary": "#1e293b",
            "bg_card": "#334155",
            "text_primary": "#f8fafc",
            "text_secondary": "#cbd5e1", 
            "accent": "#3b82f6",
            "accent_hover": "#2563eb",
            "border": "#475569",
            "success": "#10b981",
            "warning": "#f59e0b",
            "error": "#ef4444",
            "sidebar_bg": "#1e293b",
            "button_bg": "#f8fafc",
            "button_hover": "#e2e8f0"
        }
    else:
        return {
            "bg_primary": "#ffffff",
            "bg_secondary": "#f8fafc",
            "bg_card": "#ffffff",
            "text_primary": "#1e293b",
            "text_secondary": "#64748b",
            "accent": "#3b82f6",
            "accent_hover": "#2563eb", 
            "border": "#e2e8f0",
            "success": "#10b981",
            "warning": "#f59e0b",
            "error": "#ef4444",
            "sidebar_bg": "#f8fafc",
            "button_bg": "#f8fafc",
            "button_hover": "#e2e8f0"
        }

colors = get_theme_colors()
def clean_translation(raw_output):
    import re

    lines = raw_output.strip().splitlines()
    cleaned_lines = []
    skip_mode = False

    junk_starters = [
        "note:",
        "explanation:",
        "here is the corrected response",
        "however, to follow",
        "let me know",
        "additional notes:",
        "thank you",
        "best regards",
        "disclaimer:",
        "context:",
        "assistant:"
    ]

    for line in lines:
        line_clean = line.rstrip()
        if not line_clean.strip():
            continue
        if skip_mode:
            continue
        if any(line_clean.lower().startswith(junk) for junk in junk_starters):
            skip_mode = True
            continue
        cleaned_lines.append(line_clean)

    return "\n".join(cleaned_lines).strip()


def init_db():
    conn = sqlite3.connect(DATABASE_NAME)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT,
            name TEXT,
            google_id TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()
init_db()

def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    hashed = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 100000)
    return f"{salt}${hashed.hex()}"

def verify_password(stored_password, provided_password):
    if not stored_password:
        return False
    salt, hashed = stored_password.split('$')
    new_hash = hashlib.pbkdf2_hmac('sha256', provided_password.encode(), salt.encode(), 100000).hex()
    return new_hash == hashed

def create_user(email, password=None, google_id=None, name=None):
    conn = sqlite3.connect(DATABASE_NAME)
    c = conn.cursor()
    try:
        password_hash = hash_password(password) if password else None
        c.execute(
            "INSERT INTO users (email, password_hash, google_id, name) VALUES (?, ?, ?, ?)",
            (email, password_hash, google_id, name)
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False
    finally:
        conn.close()

def get_user_by_email(email):
    conn = sqlite3.connect(DATABASE_NAME)
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE email = ?", (email,))
    user = c.fetchone()
    conn.close()
    return user

def authenticate_user(email, password):
    user = get_user_by_email(email)
    if user and verify_password(user[2], password): 
        st.session_state.user_name = user[3] if user[3] else email.split('@')[0]
        return True
    return False

async def get_auth_url():
    return await client.get_authorization_url(
        REDIRECT_URI,
        scope=["openid", "profile", "email"],
        extras_params={"access_type": "offline"}
    )

async def get_access_token(code):
    return await client.get_access_token(code, REDIRECT_URI)

async def get_user_info(token):
    async with httpx.AsyncClient() as http_client:
        response = await http_client.get(
            "https://www.googleapis.com/oauth2/v3/userinfo",
            headers={"Authorization": f"Bearer {token['access_token']}"}
        )
    profile = response.json()
    return {
        "email": profile.get("email"),
        "name": profile.get("name", profile.get("email", "").split("@")[0]),
        "google_id": profile.get("sub")
    }

def handle_google_auth():
    code = st.query_params.get("code")
    if code and "user" not in st.session_state:
        try:
            token = asyncio.run(get_access_token(code))
            user_info = asyncio.run(get_user_info(token))

            if user_info["email"]:
                user = get_user_by_email(user_info["email"])
                if not user:
                    create_user(user_info["email"], None, user_info["google_id"], user_info["name"])
                st.session_state.user = user_info["email"]
                st.session_state.user_name = user_info["name"]
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Failed to retrieve user info from Google.")
        except Exception as e:
            st.error(f"Google Auth Error: {e}")

def show_google_button():
    if "user" in st.session_state:
        return
    auth_url = asyncio.run(get_auth_url())
    st.markdown(f"""
    <div style="text-align: center; margin: 10px 0;">
        <a href="{auth_url}">
            <img src="https://developers.google.com/identity/images/btn_google_signin_dark_normal_web.png" alt="Sign in with Google">
        </a>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div style="display: flex; align-items: center; justify-content: center; margin: 10px 0;">
        <div style="flex-grow: 1; height: 1px; background-color: #ccc;"></div>
        <div style="margin: 0 10px; color: #777;">OR</div>
        <div style="flex-grow: 1; height: 1px; background-color: #ccc;"></div>
    </div>
    """, unsafe_allow_html=True)

def show_login():
    st.markdown("<h2 style='text-align: center;'>Login to Your Account</h2>", unsafe_allow_html=True)
    left, center, right = st.columns([1, 2, 1])

    with center:
        handle_google_auth()
        show_google_button()
        st.markdown("<hr style='margin: 20px 0;'>", unsafe_allow_html=True)
        with st.form("login_form"):
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            if st.form_submit_button("Login"):
                if authenticate_user(email, password):
                    st.session_state.user = email
                    st.session_state.authenticated = True
                    st.success("✅ Logged in!")
                    st.rerun()
                else:
                    st.error("Invalid email or password")
def show_signup():
    st.markdown("<h2 style='text-align: center;'>Create an Account</h2>", unsafe_allow_html=True)
    left, center, right = st.columns([1, 2, 1])
    with center:
        handle_google_auth()
        show_google_button()

        st.markdown("<hr style='margin: 20px 0;'>", unsafe_allow_html=True)

        with st.form("signup_form"):
            name = st.text_input("Full Name") 
            email = st.text_input("Email")
            password = st.text_input("Password", type="password")
            confirm_password = st.text_input("Confirm Password", type="password")
            if st.form_submit_button("Sign Up"):
                if password != confirm_password:
                    st.error("Passwords do not match.")
                elif len(password) < 8:
                    st.error("Password must be at least 8 characters.")
                elif create_user(email,password,name=name):
                    st.success("Account created! Please log in.")
                    import time
                    time.sleep(3)
                else:
                    st.error("Email or username already registered.")

def main():
    if st.session_state.get("authenticated"):
        show_main_app()
        return
    page = st.query_params.get("page")
    if page == "signup":
        st.session_state.show_login = False
    elif page == "login":
        st.session_state.show_login = True
    elif "show_login" not in st.session_state:
        st.session_state.show_login = True

    if st.session_state.show_login:
        show_login()
        st.markdown("""
        <p style='text-align: center; margin-top: 20px;'>
            Don't have an account?
            <a href='?page=signup'>Sign Up</a>
        </p>
        """, unsafe_allow_html=True)
    else:
        show_signup()
        st.markdown("""
        <p style='text-align: center; margin-top: 20px;'>
            Already have an account?
            <a href='?page=login'>Login</a>
        </p>
        """, unsafe_allow_html=True)

def show_main_app():
    user_name = st.session_state.get("user_name", st.session_state.get("user", "").split("@")[0])
    userinfo = {
        "name": user_name,  # Use the stored name
        "email": st.session_state.get("user"),
        "picture": "https://www.gravatar.com/avatar/placeholder?d=mp" 
    }
    st.markdown(f"""
        <style>
        /* Main theme colors */
        .stApp {{
            background-color: {colors["bg_primary"]};
            color: {colors["text_primary"]};
            transition: all 0.3s ease;
        }}
        
        .block-container {{ 
            padding-top: 0rem !important; 
            background-color: {colors["bg_primary"]};
        }}
        
        .top-bar {{
            display: flex;
            align-items: center;
            background: linear-gradient(135deg, {colors["accent"]} 0%, {colors["accent_hover"]} 100%);
            padding: 1.5rem 2rem;
            margin-bottom: 2rem;
            box-shadow: 0 4px 20px rgba(59, 130, 246, 0.15);
            border-radius: 0 0 16px 16px;
            border-bottom: 3px solid {colors["accent_hover"]};
        }}

        .top-bar-logo {{
            height: 40px;
            width: auto;
            margin-right: 15px;
        }}

        .top-bar h2 {{
            color: white !important;
            margin: 0 !important;
            font-weight: 700;
            font-size: 2rem;
            text-shadow: 0 2px 4px rgba(0,0,0,0.1);
            font-family: 'Exo 2';
        }}
        
        /* Sidebar styling */
        .css-1d391kg {{
            background-color: {colors["sidebar_bg"]} !important;
            border-right: 2px solid {colors["border"]};
        }}
        
        /* Main buttons */
        .stButton > button {{
            background: linear-gradient(135deg, {colors["button_bg"]} 0%, {colors["button_hover"]} 100%);
            color: white !important;
            border-radius: 12px;
            padding: 0.75rem 2rem;
            font-size: 16px;
            font-weight: 600;
            border: none;
            box-shadow: 0 4px 12px rgba(59, 130, 246, 0.3);
            transition: all 0.3s ease;
            transform: translateY(0);
        }}
        
        .stButton > button:hover {{
            background: linear-gradient(135deg, {colors["button_hover"]} 0%, #1d4ed8 100%);
            transform: translateY(-2px);
            box-shadow: 0 6px 20px rgba(59, 130, 246, 0.4);
        }}
        
        /* Sidebar buttons */
        .sidebar .stButton > button {{
            width: 100%;
            text-align: left;
            padding: 0.75rem 1.25rem;
            margin-bottom: 0.5rem;
            font-size: 16px;
            font-weight: 500;
            background-color: transparent !important;
            color: {colors["text_primary"]} !important;
            border: 2px solid transparent;
            border-radius: 10px;
            box-shadow: none;
            transform: none;
        }}
        
        .sidebar .stButton > button:hover {{
            background-color: {colors["accent"]} !important;
            color: white !important;
            border-color: {colors["accent"]};
            transform: translateX(4px);
        }}
        
        /* Dark mode toggle */
        .dark-mode-toggle {{
            position: fixed;
            top: 20px;
            right: 20px;
            z-index: 999;
            background: {colors["bg_card"]};
            border: 2px solid {colors["border"]};
            border-radius: 50px;
            padding: 8px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.1);
            transition: all 0.3s ease;
        }}
        
        .toggle-switch {{
            width: 60px;
            height: 30px;
            background-color: {"#3b82f6" if st.session_state.dark_mode else "#cbd5e1"};
            border-radius: 15px;
            position: relative;
            cursor: pointer;
            transition: all 0.3s ease;
            display: flex;
            align-items: center;
            justify-content: {"flex-end" if st.session_state.dark_mode else "flex-start"};
            padding: 3px;
        }}
        
        .toggle-circle {{
            width: 24px;
            height: 24px;
            background-color: white;
            border-radius: 50%;
            transition: all 0.3s ease;
            box-shadow: 0 2px 6px rgba(0,0,0,0.2);
        }}
        
        /* Cards and containers */
        .stSelectbox > div > div {{
            background-color: {colors["bg_card"]} !important;
            border: 2px solid {colors["border"]} !important;
            border-radius: 12px !important;
            color: {colors["text_primary"]} !important;
        }}
        
        .stTextArea > div > div > textarea {{
            background-color: {colors["bg_card"]} !important;
            border: 2px solid {colors["border"]} !important;
            border-radius: 12px !important;
            color: {colors["text_primary"]} !important;
        }}
        
        .stTextInput > div > div > input {{
            background-color: {colors["bg_card"]} !important;
            border: 2px solid {colors["border"]} !important;
            border-radius: 12px !important;
            color: {colors["text_primary"]} !important;
        }}
        
        /* Alert styling */
        .stAlert {{
            border-radius: 12px !important;
            border: none !important;
            box-shadow: 0 4px 12px rgba(0,0,0,0.1) !important;
        }}
        
        /* Success message */
        .stSuccess {{
            background-color: rgba(16, 185, 129, 0.1) !important;
            border-left: 4px solid {colors["success"]} !important;
            color: {colors["success"]} !important;
        }}
        
        /* Warning message */
        .stWarning {{
            background-color: rgba(245, 158, 11, 0.1) !important;
            border-left: 4px solid {colors["warning"]} !important;
            color: {colors["warning"]} !important;
        }}
        
        /* Error message */
        .stError {{
            background-color: rgba(239, 68, 68, 0.1) !important;
            border-left: 4px solid {colors["error"]} !important;
            color: {colors["error"]} !important;
        }}
        
        /* Code blocks */
        .stCodeBlock {{
            background-color: {colors["bg_secondary"]} !important;
            border: 2px solid {colors["border"]} !important;
            border-radius: 12px !important;
        }}
        
        /* Tabs */
        .stTabs [data-baseweb="tab-list"] {{
            gap: 8px;
        }}
        
        .stTabs [data-baseweb="tab"] {{
            background-color: {colors["bg_card"]};
            border: 2px solid {colors["border"]};
            border-radius: 10px;
            padding: 0.5rem 1rem;
            font-weight: 500;
        }}
        
        .stTabs [aria-selected="true"] {{
            background-color: {colors["accent"]} !important;
            color: white !important;
            border-color: {colors["accent"]} !important;
        }}
        
        </style>
    """, unsafe_allow_html=True)

    st.markdown(f"""
        <div class="dark-mode-toggle">
            <div class="toggle-switch" onclick="toggleDarkMode()">
                <div class="toggle-circle"></div>
            </div>
        </div>
        <script>
            function toggleDarkMode() {{
                // This will be handled by Streamlit button
            }}
        </script>
    """, unsafe_allow_html=True)


    ###
    st.markdown("""
        <style>
        section[data-testid="stSidebar"] * {
            color: black !important;
        }

        section[data-testid="stSidebar"] a {
            color: black !important;
        }

        section[data-testid="stSidebar"] .stButton > button *,
        section[data-testid="stSidebar"] .stButton > button {
            color: black !important;
        }
        section[data-testid="stSidebar"] .stButton {
            width: 100% !important;
        }

        section[data-testid="stSidebar"] .stButton > button {
            width: 100% !important;                 /* 100% of sidebar width */
            height: 48px !important;                /* Fixed height */
            padding: 0 !important;
            font-size: 16px !important;
            font-weight: 600 !important;
            border-radius: 12px !important;
            background-color: inherit !important;
            border: none !important;
            box-shadow: none !important;
            display: block !important;              
            text-align: center !important;
            white-space: nowrap !important;
            overflow: hidden !important;
            text-overflow: ellipsis !important;
            margin-bottom: 0.5rem !important;
        }
        section[data-testid="stSidebar"] .stButton > button * {
            flex-shrink: 0 !important;
            color: black !important;
        }
        </style>
    """, unsafe_allow_html=True)

    st.markdown("""
        <style>
        .stButton > button {
            color: black !important;
            fill: black !important;
            stroke: black !important;
        }

        .stButton > button * {
            color: black !important;
            fill: black !important;
            stroke: black !important;
        }
        .stButton > button {
            text-shadow: none !important;
        }
        </style>
    """, unsafe_allow_html=True)


    with st.sidebar:
        if st.button("Dark Mode" if not st.session_state.dark_mode else "Light Mode", key="dark_mode_toggle"):
            st.session_state.dark_mode = not st.session_state.dark_mode
            st.rerun()
        col1, col2, col3 = st.columns([1,3,1])
        with col2:
            st.markdown("## Broadrange AI")
            st.caption("Multi-Model AI Translator")
            st.markdown("---")

        def get_color_from_email(email):
            hash_digest = hashlib.md5(email.encode()).hexdigest()
            r = int(hash_digest[0:2], 16)
            g = int(hash_digest[2:4], 16)
            b = int(hash_digest[4:6], 16)
            return f"rgb({r},{g},{b})"

        user_color = get_color_from_email(userinfo["email"])
        user_name = userinfo["name"]

        st.markdown(f"""
            <div style="display: flex; flex-direction: column; align-items: center; margin-bottom: 1rem;">
                <div style="width: 100px; height: 100px; border-radius: 50%;
                            overflow: hidden; border: 4px solid {user_color}; margin-bottom: 8px;">
                    <img src="{userinfo['picture']}" style="width: 100%; height: 100%; object-fit: cover;" />
                </div>
                <div style="font-weight: 600;">{user_name}</div>
                <div style="font-size: 12px; color: gray;">{userinfo["email"]}</div>
            </div>
        """, unsafe_allow_html=True)

        
        if st.button("Playground", key="playground_button"):
            st.session_state.page = "playground"
        if st.button("Dashboard", key="dashboard_button"):
            st.session_state.page = "dashboard"
        if st.button("Documentation", key="docs_button"):
            st.session_state.page = "docs"
        if st.button("Contact Us", key="contact_button"):
            st.session_state.page = "contact"

        st.markdown("---")
        if st.button("🚪 Logout"):
            st.session_state.clear()
            st.rerun()

        
        st.caption("© 2025 ByteBrains AI")


    from pathlib import Path

    def get_image_base64(image_path):
        try:
            return base64.b64encode(Path(image_path).read_bytes()).decode()
        except:
            return ""
    logo_base64 = get_image_base64("static/logo.png")
    st.markdown(f"""
    <div class="top-bar">
        <img class="top-bar-logo" src="data:image/png;base64,{logo_base64}" alt="ByteBrains Logo">
        <h2>ByteBrains Translator</h2>
    </div>
""", unsafe_allow_html=True)
    language_options = [
        ("Afrikaans", "af"), ("Albanian", "sq"), ("Amharic", "am"), ("Arabic", "ar"), ("Armenian", "hy"),
        ("Assamese", "as"), ("Azerbaijani", "az"), ("Bengali", "bn"), ("Bosnian", "bs"), ("Bulgarian", "bg"),
        ("Burmese", "my"), ("Catalan", "ca"), ("Chinese (Simplified)", "zh-CN"), ("Chinese (Traditional)", "zh-TW"),
        ("Croatian", "hr"), ("Czech", "cs"), ("Danish", "da"), ("Dutch", "nl"), ("English", "en"), ("Estonian", "et"),
        ("Filipino", "tl"), ("Finnish", "fi"), ("French", "fr"), ("Georgian", "ka"), ("German", "de"), ("Greek", "el"),
        ("Gujarati", "gu"), ("Hausa", "ha"), ("Hebrew", "he"), ("Hindi", "hi"), ("Hungarian", "hu"), ("Icelandic", "is"),
        ("Igbo", "ig"), ("Indonesian", "id"), ("Irish", "ga"), ("Italian", "it"), ("Japanese", "ja"), ("Javanese", "jw"),
        ("Kannada", "kn"), ("Kazakh", "kk"), ("Khmer", "km"), ("Korean", "ko"), ("Lao", "lo"), ("Latvian", "lv"),
        ("Lithuanian", "lt"), ("Macedonian", "mk"), ("Malagasy", "mg"), ("Malay", "ms"), ("Malayalam", "ml"),
        ("Maltese", "mt"), ("Marathi", "mr"), ("Nepali", "ne"), ("Norwegian", "no"), ("Odia", "or"), ("Persian", "fa"),
        ("Polish", "pl"), ("Portuguese", "pt"), ("Punjabi", "pa"), ("Romanian", "ro"), ("Russian", "ru"), ("Serbian", "sr"),
        ("Sinhala", "si"), ("Slovak", "sk"), ("Slovenian", "sl"), ("Somali", "so"), ("Spanish", "es"), ("Sundanese", "su"),
        ("Swahili", "sw"), ("Swedish", "sv"), ("Tamil", "ta"), ("Telugu", "te"), ("Thai", "th"), ("Turkish", "tr"),
        ("Ukrainian", "uk"), ("Urdu", "ur"), ("Uzbek", "uz"), ("Vietnamese", "vi"), ("Welsh", "cy"), ("Yoruba", "yo"),
        ("Zulu", "zu")
    ]
    language_code_to_name = {code: name for name, code in language_options} 
    language_name_to_code = dict(language_options) 

    page = st.session_state.page
    if page == "playground":
        voice_map = {
            'af': 'af-ZA-AdriNeural', 'sq': 'sq-AL-AnilaNeural', 'am': 'am-ET-AmehaNeural', 'ar': 'ar-EG-SalmaNeural',
            'hy': 'hy-AM-AnahitNeural', 'as': 'as-IN-JyotiNeural', 'az': 'az-AZ-BabekNeural', 'bn': 'bn-IN-TanishaaNeural',
            'bs': 'bs-BA-GoranNeural', 'bg': 'bg-BG-KalinaNeural', 'my': 'my-MM-NilarNeural', 'ca': 'ca-ES-JoanaNeural',
            'zh-CN': 'zh-CN-XiaoxiaoNeural', 'zh-TW': 'zh-TW-HsiaoChenNeural', 'hr': 'hr-HR-GabrijelaNeural',
            'cs': 'cs-CZ-VlastaNeural', 'da': 'da-DK-ChristelNeural', 'nl': 'nl-NL-ColetteNeural', 'en': 'en-US-JennyNeural',
            'et': 'et-EE-AnuNeural', 'tl': 'tl-PH-JaimeNeural', 'fi': 'fi-FI-NooraNeural', 'fr': 'fr-FR-DeniseNeural',
            'ka': 'ka-GE-EkaNeural', 'de': 'de-DE-KatjaNeural', 'el': 'el-GR-AthinaNeural', 'gu': 'gu-IN-DhwaniNeural',
            'ha': 'ha-NG-AminaNeural', 'he': 'he-IL-AvriNeural', 'hi': 'hi-IN-MadhurNeural', 'hu': 'hu-HU-NoemiNeural',
            'is': 'is-IS-GudrunNeural', 'ig': 'ig-NG-EzinneNeural', 'id': 'id-ID-GadisNeural', 'ga': 'ga-IE-ColmNeural',
            'it': 'it-IT-ElsaNeural', 'ja': 'ja-JP-NanamiNeural', 'jw': 'jw-ID-SitiNeural', 'kn': 'kn-IN-GaganNeural',
            'kk': 'kk-KZ-AigulNeural', 'km': 'km-KH-PisethNeural', 'ko': 'ko-KR-SoonBokNeural', 'lo': 'lo-LA-KeomanyNeural',
            'lv': 'lv-LV-EveritaNeural', 'lt': 'lt-LT-LeonasNeural', 'mk': 'mk-MK-MarijaNeural', 'mg': 'mg-MG-TinaNeural',
            'ms': 'ms-MY-OsmanNeural', 'ml': 'ml-IN-MidhunNeural', 'mt': 'mt-MT-GraceNeural', 'mr': 'mr-IN-AarohiNeural',
            'ne': 'ne-NP-HemkalaNeural', 'no': 'nb-NO-IselinNeural', 'or': 'or-IN-MadhurNeural', 'fa': 'fa-IR-DilaraNeural',
            'pl': 'pl-PL-ZofiaNeural', 'pt': 'pt-PT-RaquelNeural', 'pa': 'pa-IN-SandeepNeural', 'ro': 'ro-RO-AlinaNeural',
            'ru': 'ru-RU-SvetlanaNeural', 'sr': 'sr-RS-NicholasNeural', 'si': 'si-LK-SameeraNeural', 'sk': 'sk-SK-ViktoriaNeural',
            'sl': 'sl-SI-PetraNeural', 'so': 'so-SO-MuuseNeural', 'es': 'es-ES-ElviraNeural', 'su': 'su-ID-JajangNeural',
            'sw': 'sw-KE-RafikiNeural', 'sv': 'sv-SE-SofieNeural', 'ta': 'ta-IN-PallaviNeural', 'te': 'te-IN-MohanNeural',
            'th': 'th-TH-PremwadeeNeural', 'tr': 'tr-TR-EmelNeural', 'uk': 'uk-UA-PoltavNeural', 'ur': 'ur-PK-AsadNeural',
            'uz': 'uz-UZ-MadinaNeural', 'vi': 'vi-VN-HoaiMyNeural', 'cy': 'cy-GB-AledNeural', 'yo': 'yo-NG-AdeolaNeural',
            'zu': 'zu-ZA-ThandoNeural'
        }
        tesseract_lang_map = {
            "en": "eng", "hi": "hin", "ta": "tam", "te": "tel", 
            "ml": "mal", "bn": "ben", "kn": "kan", "gu": "guj",
            "pa": "pan", "mr": "mar", "ar": "ara", "zh-CN": "chi_sim",
            "zh-TW": "chi_tra", "ja": "jpn", "ko": "kor", "es": "spa",
            "fr": "fra", "de": "deu", "it": "ita", "pt": "por",
            "ru": "rus", "ur": "urd", "fa": "fas", "th": "tha"
        }

        async def generate_tts(text, lang_code):
            if not text.strip():
                raise ValueError("⚠ Empty text provided for TTS.")
            voice = voice_map.get(lang_code, 'en-US-JennyNeural')
            communicate = edge_tts.Communicate(text, voice)
            with tempfile.NamedTemporaryFile(delete=False, suffix='.mp3') as tmp_audio_file:
                await communicate.save(tmp_audio_file.name)
                tmp_audio_path = tmp_audio_file.name
            with open(tmp_audio_path, 'rb') as f:
                audio_bytes = f.read()
            os.remove(tmp_audio_path)
            return audio_bytes

        def preprocess_image_for_ocr(image):
            """Preprocess image to improve OCR accuracy"""
            try:
                if image.mode != 'L':
                    image = image.convert('L')
            
                enhancer = ImageEnhance.Contrast(image)
                image = enhancer.enhance(2.0)  
                enhancer = ImageEnhance.Sharpness(image)
                image = enhancer.enhance(2.0)
                
                return image
            except:
                return image  
        def extract_text_with_tesseract_enhanced(image, lang_hint='eng'):
            """Enhanced text extraction but without language detection — returns text only"""
            try:
                t_lang = 'eng'
                if lang_hint in tesseract_lang_map:
                    t_lang = tesseract_lang_map[lang_hint]
                else:
                    t_lang = 'eng'
                try:
                    text = pytesseract.image_to_string(image, lang=t_lang, config='--psm 6')
                    if text and text.strip():
                        return text
                except Exception:
                    pass
                text = pytesseract.image_to_string(image, lang='eng', config='--psm 6')
                if text and text.strip():
                    return text
                else:
                    return ""
            except Exception as e:
                return f"OCR Error: {e}"

        st.markdown("<h2 style='color: #4CAF50;'>Playground</h2>", unsafe_allow_html=True)

        tab1, tab2, tab3, tab4 = st.tabs(["📝 Text", "📄 Document", "🎙 Audio", "🖼 Image"])

        with tab1:
            st.subheader("Text Translation")
            text_input = st.text_area("Enter Text", height=150)

            col1, col2 = st.columns(2)
            with col1:
                src_label = st.selectbox("Source Language", [name for name, code in language_options], index=[name for name, code in language_options].index("English"))
            with col2:
                tgt_label = st.selectbox("Target Language", [name for name, code in language_options], index=1)

            model = st.selectbox("Model", ["DeepSeek-V3-0324", "Llama-3.3-Swallow-70B-Instruct-v0.4","Meta-Llama-3.3-70B-Instruct", "Qwen3-32B", "DeepSeek-R1-0528"])

            if st.button("Translate Text",key="translate_text_btn"):
                if not text_input.strip():
                    st.warning("Please enter text.")
                else:
                    src_code = language_name_to_code[src_label]
                    tgt_code = language_name_to_code[tgt_label]
                    if src_code == tgt_code:
                        st.warning("Source and target languages are the same. Please select different languages.")
                    else:
                        with st.spinner("Translating..."):
                            raw = translate_with_sambanova(text_input, src_code, tgt_code, model)
                            translation = clean_translation(raw)
                        st.markdown("Translation")
                        st.markdown(f"<div style='background-color: #f0f0f0; border: 1px solid #ccc; border-radius: 5px; padding: 10px; color: black; font-weight: bold; height: 150px; overflow-y: auto;'>{translation}</div>",
                        unsafe_allow_html=True)

        with tab2:
            st.subheader("Document Translation")
            doc = st.file_uploader("Upload Document", type=["txt", "pdf", "docx"])

            col1, col2 = st.columns(2)
            with col1:
                src_label_doc = st.selectbox("Source Language", [name for name, code in language_options], index=[name for name, code in language_options].index("English"), key="doc_src_lang")
            with col2:
                doc_tgt = st.selectbox("Target Language", [name for name, code in language_options], index=1, key="doc_tgt")

            doc_model = st.selectbox("Model", ["DeepSeek-V3-0324", "Llama-3.3-Swallow-70B-Instruct-v0.4","Meta-Llama-3.3-70B-Instruct", "Qwen3-32B", "DeepSeek-R1-0528"], key="doc_model")

            if st.button("Translate Document", key="translate_doc_btn"):
                if not doc:
                    st.warning("Please upload a document.")
                else:
                    try:
                        if doc.type == "text/plain":
                            doc_text = doc.read().decode("utf-8")
                        elif doc.type == "application/pdf":
                            doc_text = "\n".join(p.extract_text() or "" for p in PyPDF2.PdfReader(doc).pages)
                        elif doc.type.startswith("application/vnd.openxmlformats"):
                            doc_obj = Document(io.BytesIO(doc.read()))
                            doc_text = "\n".join(p.text for p in doc_obj.paragraphs)
                        else:
                            st.error("Unsupported file type.")
                            st.stop()
                        
                        src_code = language_name_to_code[src_label_doc]
                        tgt_code = language_name_to_code[doc_tgt]
                        
                        if src_code == tgt_code:
                            st.warning("Source and target languages are the same. Please select different languages.")
                            st.stop()

                        chunks = []
                        buffer = ""
                        for line in doc_text.splitlines():
                            if len(buffer + line) < 1500:
                                buffer += line + "\n"
                            else:
                                chunks.append(buffer.strip())
                                buffer = line + "\n"
                        if buffer:
                            chunks.append(buffer.strip())
                        
                        translated_chunks = []
                        with st.spinner("Translating..."):
                            for i, chunk in enumerate(chunks):
                                if chunk.strip():
                                    raw = translate_with_sambanova(chunk.strip(), src_code, tgt_code, doc_model)
                                    cleaned = clean_translation(raw)
                                    translated_chunks.append(cleaned)
                        
                        result = "\n\n".join(translated_chunks)
                        st.markdown("Preview")
                        st.markdown(
                            f"<div style='background-color: #f0f0f0; border: 1px solid #ccc; border-radius: 5px; padding: 10px; color: black; font-weight: bold; height: 150px; overflow-y: auto;'>{result[:1000]}.....</div>",
                            unsafe_allow_html=True
                        )
                        st.markdown("""
                        <style>
                            .stDownloadButton button {
                                color: black !important;
                            }
                            .stDownloadButton button div p {
                                color: black !important;
                            }
                        </style>
                        """, unsafe_allow_html=True)
                        st.download_button("⬇ Download", result, file_name="translated.txt", mime="text/plain")
                        
                    except Exception as e:
                        st.error(f"Translation failed: {e}")

        with tab3:
            st.subheader("Audio Translation")

            mode = st.radio("Audio Input Method", ["Upload Audio File", "Record Audio"], key="audio_mode")
            audio_key = "audio_data"

            if mode == "Upload Audio File":
                uploaded_file = st.file_uploader("Upload Audio (WAV only)", type=["wav"], key="audio_upload")
                if uploaded_file:
                    st.session_state[audio_key] = uploaded_file.read()
                    st.audio(st.session_state[audio_key], format="audio/wav")
            else:
                try:
                    from streamlit_mic_recorder import mic_recorder
                    rec = mic_recorder("🎤 Start Recording", "⏹ Stop", just_once=True, format="wav", key="mic_recording")
                    if rec and "bytes" in rec:
                        st.session_state[audio_key] = rec["bytes"]
                        st.audio(st.session_state[audio_key], format="audio/wav")
                except ImportError:
                    st.error("Install with: pip install streamlit-mic-recorder")

            col1, col2 = st.columns(2)
            with col1:
                src_label_audio = st.selectbox("Source Language", [name for name, code in language_options], index=[name for name, code in language_options].index("English"), key="audio_src_lang")
            with col2:
                tgt_label = st.selectbox("Target Language", [name for name, code in language_options], index=1, key="audio_tgt")

            audio_model = st.selectbox("Model", ["DeepSeek-V3-0324", "Llama-3.3-Swallow-70B-Instruct-v0.4","Meta-Llama-3.3-70B-Instruct", "Qwen3-32B", "DeepSeek-R1-0528"], key="audio_model")

            if st.button("Translate Voice", key="translate_audio_btn"):
                if audio_key not in st.session_state or not st.session_state[audio_key]:
                    st.warning("⚠ Please upload or record audio.")
                    st.stop()
                
                src_code = language_name_to_code[src_label_audio]
                tgt_code = language_name_to_code[tgt_label]

                if src_code == tgt_code:
                    st.warning("Source and target languages are the same. Please select different languages.")
                    st.stop()

                audio_bytes = st.session_state[audio_key]
                with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as tmp_file:
                    tmp_file.write(audio_bytes)
                    tmp_path = tmp_file.name

                recognizer = sr.Recognizer()
                with sr.AudioFile(tmp_path) as source:
                    audio_data = recognizer.record(source)

                try:
                    with st.spinner("Transcribing..."):
                        transcript = recognizer.recognize_google(audio_data, language=src_code)
                except sr.UnknownValueError:
                    st.error("Transcription failed - audio not recognized.")
                    st.stop()
                except Exception as e:
                    st.error(f"Transcription failed: {e}")
                    st.stop()
                st.markdown("Transcript")
                st.markdown(f"<div style='background-color: #f0f0f0; border: 1px solid #ccc; border-radius: 5px; padding: 10px; color: black; font-weight: bold; height: 150px; overflow-y: auto;'>{transcript}</div>",
                unsafe_allow_html=True)

                with st.spinner("Translating..."):
                    raw = translate_with_sambanova(transcript, src_code, tgt_code, audio_model)
                    translation = clean_translation(raw)
                st.markdown("Translation")
                st.markdown(f"<div style='background-color: #f0f0f0; border: 1px solid #ccc; border-radius: 5px; padding: 10px; color: black; font-weight: bold; height: 150px; overflow-y: auto;'>{translation}</div>",
                unsafe_allow_html=True)

                try:
                    with st.spinner("🔊 Generating Audio..."):
                        output_audio = asyncio.run(generate_tts(translation, language_name_to_code[tgt_label]))
                    st.audio(output_audio, format="audio/mp3")
                    st.markdown("""
                        <style>
                            .stDownloadButton button {
                                color: black !important;
                            }
                            .stDownloadButton button div p {
                                color: black !important;
                            }
                        </style>
                        """, unsafe_allow_html=True)
                    st.download_button("⬇ Download Translated Audio", output_audio, file_name="translation.mp3", mime="audio/mp3")
                except Exception as e:
                    st.error(f"Audio generation failed: {e}")

        with tab4:
            st.subheader("Image Translation")

            uploaded_image = st.file_uploader("Upload Image (PNG, JPG, JPEG)", type=["png", "jpg", "jpeg"], key="image_upload")

            extracted_text = ""

            col1, col2 = st.columns(2)
            with col1:
                src_label_img = st.selectbox("Source Language", [name for name, code in language_options], index=[name for name, code in language_options].index("English"), key="img_src_lang")
            with col2:
                tgt_label_img = st.selectbox("Translate to", [name for name, code in language_options], index=1, key="img_tgt_lang")

    # MODEL SELECTION - MOVED HERE TO SHOW IMMEDIATELY
            image_model = st.selectbox("Model", ["DeepSeek-V3-0324", "Llama-3.3-Swallow-70B-Instruct-v0.4","Meta-Llama-3.3-70B-Instruct", "Qwen3-32B", "DeepSeek-R1-0528"], key="image_model")

            if uploaded_image:
                try:
                    image = Image.open(uploaded_image)
                    image_proc = preprocess_image_for_ocr(image)
                    with st.spinner("Extracting text from image..."):
                        extracted_text = extract_text_with_tesseract_enhanced(image_proc, lang_hint=language_name_to_code[src_label_img])
                        if extracted_text and not extracted_text.startswith("OCR Error:"):
                            st.markdown("Extracted Text")
                            st.text_area("", extracted_text, height=150, key="extracted_text_area", label_visibility="collapsed")
                        else:
                            st.error(f"Failed to extract text: {extracted_text}")
                except Exception as e:
                    st.error(f"Error processing image: {e}")

            if (uploaded_image and extracted_text) or (st.session_state.get("extracted_text_area")):
                user_text = st.text_area("Edit extracted text before translation (if needed):", value=extracted_text or "", height=150, key="final_extracted_text")
                
                if st.button("Translate Image Text", key="translate_img_btn"):
                    src_code = language_name_to_code[src_label_img]
                    tgt_code = language_name_to_code[tgt_label_img]
                    if src_code == tgt_code:
                        st.warning("Source and target languages are the same. Please select different languages.")
                    else:
                        with st.spinner("Translating..."):
                            raw_translation = translate_with_sambanova(user_text, src_code, tgt_code, image_model)
                            translation = clean_translation(raw_translation)
                        st.markdown("Translation")
                        st.text_area("", translation, height=150, key="translation_area", label_visibility="collapsed")   
    elif page == "dashboard":
            st.markdown("<h2 style='color: #4CAF50;'>Dashboard</h2>", unsafe_allow_html=True)
            st.markdown("<h3>Available Models</h3>", unsafe_allow_html=True)
        
            models_info = [
            "DeepSeek-V3-0324",
            "Llama-3.3-Swallow-70B-Instruct-v0.4",
            "Meta-Llama-3.3-70B-Instruct",
            "Qwen3-32B",
            "DeepSeek-R1-0528"
        ]
        
            for model in models_info:
               st.markdown(f"<p style='font-size: 18px;'>- <strong>{model}</strong></p>", unsafe_allow_html=True)
        
            st.markdown("<hr>", unsafe_allow_html=True)
        
            st.markdown("<h3>Developer Quickstart</h3>", unsafe_allow_html=True)
            st.markdown("<p>Start building AI-powered applications in minutes with our OpenAI compatible API.</p>", unsafe_allow_html=True)
        

            model = st.selectbox("Select a model", models_info)

            tabs = st.tabs(["Curl", "Python", "Gradio"])

            with tabs[0]:
                curl_code = f'''curl -H "Authorization: Bearer $API_KEY" \\
                -H "Content-Type: application/json" \\
                -d {{
                "model": "{model}",
                "inputs": "Translate this sentence to French.",
                "source_language": "en",
                "target_language": "fr"
                }} https://api.example.com/v1/translate'''
                st.code(curl_code, language="bash")

            with tabs[1]:
                python_code = f'''import requests

    url = "https://api.example.com/v1/translate"
    headers = {{
        "Authorization": "Bearer YOUR_API_KEY",
        "Content-Type": "application/json"
    }}
    data = {{
        "model": "{model}",
        "inputs": "Translate this sentence to French.",
        "source_language": "en",
        "target_language": "fr"
    }}

    response = requests.post(url, headers=headers, json=data)
    print(response.json())'''
                st.code(python_code, language="python")

            with tabs[2]:
                gradio_code = f'''import gradio as gr
    import requests

    def translate(text):
        response = requests.post("https://api.example.com/v1/translate", json={{
            "model": "{model}",
            "inputs": text,
            "source_language": "en",
            "target_language": "fr"
        }}, headers={{
            "Authorization": "Bearer YOUR_API_KEY"
        }})
        return response.json().get("translation", "")

    gr.Interface(fn=translate, inputs="text", outputs="text", title="{model} Translator").launch()'''
                st.code(gradio_code, language="python")

            st.markdown("<hr>", unsafe_allow_html=True)
        
            col1, col2 = st.columns(2)

            with col1:
                if st.button("View Documentation",key="doc_button"):
                   st.session_state.page = "docs"
            with col2:
                if st.button("Try in Playground",key = "playgrnd_button"):
                  st.session_state.page = "playground"


    elif page == "docs":
        st.markdown("<h2 style='color: #4CAF50;'>Documentation</h2>", unsafe_allow_html=True)
        st.markdown("""
        Welcome to ByteBrains Translator!  
        This application leverages advanced AI translation models to provide high-quality translations across multiple languages. Below is a guide on how to use the app effectively.

        ### Features:
        - Select from multiple AI translation models: Choose the model that best fits your translation needs.
        - Input text or upload documents: You can either type in text or upload documents for translation.
        - Audio Translation: Record audio or upload audio files for instant transcription and translation.
        - Image Text Extraction and Translation: Upload images containing text, and the app will extract and translate the text for you.
        - Choose source and target languages: Select from a wide variety of languages for both source and target.
        - Get instant professional translations: Receive translations quickly and efficiently.
        - Download Translated Documents: After translation, download the translated documents in the original format.
        - Dark/Light mode support: Switch between dark and light themes for a comfortable user experience.

        ### Available Models:
        - DeepSeek-V3-0324: A state-of-the-art model designed for high-quality translations across multiple languages, optimized for speed and accuracy.
        - Llama-3.3-Swallow-70B-Instruct-v0.4: An advanced instruction-tuned model that excels in understanding and generating human-like responses.
        - Meta-Llama-3.3-70B-Instruct: Meta's latest model, fine-tuned for a variety of tasks, including translation, summarization, and question answering.
        - Qwen3-32B: A lightweight model that provides efficient translations while maintaining a balance between performance and resource usage.
        - DeepSeek-R1-0528: A robust model focused on delivering reliable translations, particularly in technical and formal contexts.

        ### Supported File Types:
        - .txt - Plain text files
        - .docx - Microsoft Word documents  
        - .pdf - Portable Document Format
        - .wav - Audio files for audio translation
        - .jpg, .png - Image files for text extraction and translation

        ### Best Practices:
        - Use clear, well-structured sentences for optimal translation quality.
        - Upload plain-text documents for the highest accuracy.
        - Ensure source and target languages are different to avoid confusion.
        - Review translations for context-specific nuances to ensure the translated text conveys the intended meaning.

        ### Getting Started:
        1. Select a Model: Choose one of the available models from the dropdown menu.
        2. Input Text or Upload a Document: Enter your text directly or upload a supported document file.
        3. Choose Languages: Select the source and target languages from the dropdown menus.
        4. Translate: Click the "Translate" button to get your translation.
        5. Review and Download: Review the translation and download the audio or translated document if needed.

        For any further questions or support, please contact our support team.
        """)
        if st.button("Contact Us",key = "contact_us_button"):
            st.session_state.page = "contact" 
    
    elif page == "contact":
        st.markdown("<h2 style='color: #4CAF50;'>Contact Us</h2>", unsafe_allow_html=True)
        st.markdown("Get in touch with our team for support, partnerships, or feedback!")
        st.markdown("""
<style>
    /* Fix download button text visibility */
    .stForm button {
        color: black !important;
    }
    .stForm button div p {
        color: black !important;
    }
</style>
""", unsafe_allow_html=True)

        with st.form("contact_form", clear_on_submit=True):
            name = st.text_input("Your Name")
            email = st.text_input("Your Email")
            message = st.text_area("Your Message")
            submitted = st.form_submit_button("Send")

            if submitted:
                if name and email and message:
                    with open("contact_messages.txt", "a") as f:
                        f.write(f"---\nTime: {datetime.now()}\nName: {name}\nEmail: {email}\nMessage: {message}\n")
                    st.success("Message sent successfully!We'll get back to you within 24 hours.")
                else:
                    st.error("Please fill in all fields.")
    st.markdown("---")
    st.markdown(f"""
        <div style='text-align: center; padding: 2rem; background-color: {colors["bg_secondary"]}; border-radius: 12px; margin-top: 2rem;'>
            <p style='color: {colors["text_secondary"]}; margin: 0;'>
                Built by ByteBrains Research Team | Powered by Streamlit
            </p>
        </div>
    """, unsafe_allow_html=True)


if __name__ == "__main__":
    main()