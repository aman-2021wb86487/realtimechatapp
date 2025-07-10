import os

class Config:
    SECRET_KEY = "2021WB86487"
    MYSQL_HOST = 'localhost'
    MYSQL_USER = 'root'
    MYSQL_PASSWORD = 'As@#151198'
    MYSQL_DB = 'chatdb'
    MYSQL_CURSORCLASS = 'DictCursor'
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'