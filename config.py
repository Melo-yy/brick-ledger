"""Application configuration."""
import os

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Data directory — override via env var for cloud deployment (e.g. Railway Volume)
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)

UPLOAD_FOLDER = os.path.join(DATA_DIR, "uploads")
DATABASE_PATH = os.path.join(DATA_DIR, "orders.db")
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "webp", "bmp"}
MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16 MB

# Flask host/port
HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", 5000))
DEBUG = os.environ.get("DEBUG", "").lower() in ("1", "true", "yes")

# JWT
JWT_SECRET = os.environ.get("JWT_SECRET", "dev-secret-change-in-production")
