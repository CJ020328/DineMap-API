import psycopg2
from psycopg2.extras import DictCursor
import os

# 环境变量
DB_NAME = "subway_db" 
DB_USER = "neondb_owner" 
DB_PASSWORD = "npg_c7TZpqPHBg9L" 
DB_HOST = "ep-blue-leaf-a5q7udps-pooler.us-east-2.aws.neon.tech"  
DB_PORT = "5432"  

def connect_db():
    """ 连接 Neon PostgreSQL 数据库 """
    try:
        conn = psycopg2.connect(
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASSWORD,
            host=DB_HOST,
            port=DB_PORT,
            sslmode="require"  # Neon 需要 SSL 连接
        )
        return conn
    except Exception as e:
        print(f"⚠️ Database connection error: {e}")
        return None

def execute_query(query, values=None, fetch=False):
    """ 执行 SQL 查询 """
    conn = connect_db()
    if not conn:
        return None
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute(query, values)
            if fetch:
                result = cur.fetchall()
            else:
                result = None
            conn.commit()
        return result
    except Exception as e:
        print(f"⚠️ Query execution error: {e}")
        return None
    finally:
        conn.close()
