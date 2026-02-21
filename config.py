import os
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = "sqlite:///./noc.db"
SECRET_TOKEN = os.getenv("SECRET_TOKEN", "supersecreto")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")