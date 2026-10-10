import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
ROOT_DIR = BASE_DIR.parent
DATA_DIR = Path(os.getenv("BC_DATA_DIR", ROOT_DIR / "data"))
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "bidgetcheck.db"
PROMPTS_DIR = ROOT_DIR / "prompts"
STATIC_DIR = BASE_DIR / "static"

LLM_PROVIDER = os.getenv("BC_LLM_PROVIDER", "gemini")
OLLAMA_HOST = os.getenv("BC_OLLAMA_HOST", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("BC_OLLAMA_MODEL", "qwen2.5:7b-instruct")
OLLAMA_NUM_CTX = int(os.getenv("BC_OLLAMA_NUM_CTX", "8192"))
OPENAI_BASE_URL = os.getenv("BC_OPENAI_BASE_URL", "http://localhost:8000/v1")
OPENAI_MODEL = os.getenv("BC_OPENAI_MODEL", "local-model")
OPENAI_API_KEY = os.getenv("BC_OPENAI_API_KEY", "not-needed")

GEMINI_API_KEY = os.getenv("BC_GEMINI_API_KEY") or os.getenv("GEMINI_API_KEY") or ""
GEMINI_BASE = os.getenv("BC_GEMINI_BASE", "https://generativelanguage.googleapis.com/v1beta")
GEMINI_MODEL = os.getenv("BC_GEMINI_MODEL", "gemini-3.7-flash")
LLM_TIMEOUT = int(os.getenv("BC_LLM_TIMEOUT", "600"))
LLM_DELAY_MS = int(os.getenv("BC_LLM_DELAY_MS", "0"))

APP_ENV = os.getenv("BC_ENV", "development")
SESSION_COOKIE = os.getenv("BC_SESSION_COOKIE", "bgc_session")

CHUNK_CHARS = int(os.getenv("BC_CHUNK_CHARS", "7000"))
CHUNK_OVERLAP = int(os.getenv("BC_CHUNK_OVERLAP", "400"))
MAX_PAGES = int(os.getenv("BC_MAX_PAGES", "600"))
MIN_VERIFIED_RATIO = float(os.getenv("BC_MIN_VERIFIED_RATIO", "0.6"))

DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)