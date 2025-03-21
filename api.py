from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from typing import List, Optional
from fastapi.middleware.cors import CORSMiddleware
import re
import psycopg2
from psycopg2.extras import DictCursor
from openai import OpenAI
import os
import json
import math
from datetime import datetime
import traceback

app = FastAPI()

# 配置 - 使用环境变量
openai_api_key = os.getenv("OPENAI_API_KEY", "你的API密钥")

# 打印环境变量状态（调试用）
print(
    f"OPENAI_API_KEY 是否存在: {openai_api_key != '你的API密钥' and len(openai_api_key) > 10}")

# OpenAI客户端
client = None
try:
    client = OpenAI(api_key=openai_api_key)
    print("✅ OpenAI客户端初始化成功")
except Exception as e:
    print(f"⚠️ 警告: 无法初始化OpenAI客户端: {e}")

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 数据库连接函数
DB_NAME = "subway_db" 
DB_USER = "neondb_owner" 
DB_PASSWORD = "npg_c7TZpqPHBg9L" 
DB_HOST = "ep-blue-leaf-a5q7udps-pooler.us-east-2.aws.neon.tech"  
DB_PORT = "5432"  

def connect_db():
    return psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT
    )


def get_db_connection():
    try:
        conn = connect_db()
        return conn
    except Exception as e:
        raise HTTPException(
    status_code=500,
     detail=f"DB Connection Failed: {e}")

# 格式化工具函数


def decimal_hour_to_str(h: float) -> str:
    """将小数点形式的小时转换为时:分格式"""
    hh = int(h)
    if hh >= 24:  # 处理跨午夜情况
        hh -= 24
    mm = int(round((h - int(h)) * 60))
    return f"{hh:02d}:{mm:02d}"


def convert_12h_to_24h(time_str):
    """将12小时制转换为24小时制数字格式"""
    print(f"转换12小时制: {time_str}")
    # 清理特殊字符
    time_str = re.sub(r'[^0-9:APMapm ]', '', time_str)
    
    # 调试输出
    print(f"清理后: {time_str}")
    
    # 解析时间
    try:
        # 处理格式 "8:00 AM"
        match = re.search(
    r'(\d{1,2}):?(\d{2})?\s*([APap][Mm])',
    time_str)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2)) if match.group(2) else 0
            ampm = match.group(3).upper()
            
            print(f"解析结果: 小时={hour}, 分钟={minute}, 上下午={ampm}")
            
            # 转换到24小时制
            if ampm.upper() == 'PM' and hour < 12:
                hour += 12
            elif ampm.upper() == 'AM' and hour == 12:
                hour = 0
                
            # 格式化为4位数字格式
            result = f"{hour:02d}{minute:02d}"
            print(f"转换结果: {result}")
            return result
        
        # 处理没有冒号的格式，如 "9am"
        match = re.search(r'(\d{1,2})\s*([APap][Mm])', time_str)
        if match:
            hour = int(match.group(1))
            ampm = match.group(2).upper()
            
            print(f"解析结果: 小时={hour}, 上下午={ampm}")
            
            # 转换到24小时制
            if ampm.upper() == 'PM' and hour < 12:
                hour += 12
            elif ampm.upper() == 'AM' and hour == 12:
                hour = 0
                
            # 格式化为4位数字格式
            result = f"{hour:02d}00"
            print(f"转换结果: {result}")
            return result
            
        print(f"无法匹配时间格式: {time_str}")
        return "0000"  # 默认值
    except Exception as e:
        print(f"时间转换出错: {str(e)}")
        return "0000"  # 解析失败

# 距离计算函数


def calculate_distance(lat1, lon1, lat2, lon2):
    """计算两点之间的距离（公里）"""
    if not all([lat1, lon1, lat2, lon2]):
        return float('inf')  # 如果有缺失值，返回无穷大
    
    try:
        lat1, lon1 = float(lat1), float(lon1)
        lat2, lon2 = float(lat2), float(lon2)
        
        R = 6371  # 地球半径（公里）
        dLat = math.radians(lat2 - lat1)
        dLon = math.radians(lon2 - lon1)
        a = (math.sin(dLat / 2) * math.sin(dLat / 2) +
             math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) *
             math.sin(dLon / 2) * math.sin(dLon / 2))
        c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
        distance = R * c
        return distance
    except Exception as e:
        print(f"Error calculating distance: {e}")
        return float('inf')

# 查询请求体


class ChatRequest(BaseModel):
    query: str
    lat: Optional[float] = None  # 可选：用户当前位置纬度
    lon: Optional[float] = None  # 可选：用户当前位置经度

# === 核心查询功能 ===

# 1. 位置相关查询


def find_outlets_by_location(location_query):
    """查找特定位置的门店"""
    conn = connect_db()
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            # 尝试直接邮编匹配
            if location_query.isdigit() and len(location_query) == 5:
                print(f"尝试邮编精确匹配: {location_query}")
                cur.execute(
    "SELECT * FROM subway_outlets WHERE address ILIKE %s OR postcode = %s", [
        f'%{location_query}%', location_query])
                postcode_results = [dict(r) for r in cur.fetchall()]
                if postcode_results:
                    print(f"邮编匹配成功，找到 {len(postcode_results)} 个结果")
                    return postcode_results
            
            # 邮编范围匹配 - 吉隆坡和雪兰莪地区邮编范围
            postcode_ranges = {
                # 吉隆坡地区
                "City Centre": ["50000", "50999"],
                "Taman Tun Dr Ismail": ["60000"],
                "Ampang": ["68000"],
                "Batu Caves": ["68100"],
                "Bukit Bintang": ["55100"],
                "Cheras": ["56000"],
                "Kepong": ["52100"],
                "Setapak": ["53000"],
                "Wangsa Maju": ["53300"],
                "Bangsar": ["59000"],
                "Brickfields": ["50470"],
                "Mont Kiara": ["50480"],
                "Sri Hartamas": ["50480"],
                "Seputeh": ["58000"],
                "Bukit Jalil": ["57000"],
                "Sri Petaling": ["57000"],
                "Desa Petaling": ["57100"],
                "Taman Desa": ["58100"],
                "Kuchai Lama": ["58200"],
                "Salak South": ["57100"],
                "Sungai Besi": ["57000"],
                # 雪兰莪地区
                "Petaling Jaya": ["46000", "46999"],
                "Subang Jaya": ["47500", "47630"],
                "UEP Subang Jaya (USJ)": ["47600"],
                "Bandar Sunway": ["47500"],
                "Puchong": ["47100"],
                "Seri Kembangan": ["43300"],
                "Putra Heights": ["47650"],
                "Kota Damansara": ["47810"],
                "Ara Damansara": ["47301"],
                "Damansara Jaya": ["47400"],
                "Damansara Utama": ["47400"],
                "Mutiara Damansara": ["47800"],
                "Bandar Utama": ["47800"],
                "Kelana Jaya": ["47301"],
                "Gombak": ["68100"],
                "Selayang": ["68100"],
                "Rawang": ["48000"],
                "Kundang": ["48050"],
                "Kuang": ["48050"],
                "Taman Melawati": ["53100"],
                "Taman Sri Gombak": ["68100"],
                "Setiawangsa": ["54200"],
                "Klang": ["41000", "41999"],
                "Port Klang": ["42000"],
                "Kapar": ["42200"],
                "Meru": ["41050"],
                "Pandamaran": ["42000"],
                "Bukit Tinggi": ["41200"],
                "Bandar Botanik": ["41200"],
                "Telok Panglima Garang": ["42500"],
                "Banting": ["42700"],
                "Kajang": ["43000"],
                "Semenyih": ["43500"],
                "Bangi": ["43650"],
                "Cheras": ["43200"],
                "Balakong": ["43300"],
                "Hulu Langat": ["43100"],
                "Sungai Long": ["43000"],
                "Bandar Mahkota Cheras": ["43200"],
                "Kuala Kubu Bharu": ["44000"],
                "Batang Kali": ["44300"],
                "Serendah": ["44300"],
                "Rasa": ["44200"],
                "Ulu Yam": ["44300"],
                "Bukit Beruntung": ["48300"],
                "Sepang": ["43900"],
                "Dengkil": ["43800"],
                "Salak Tinggi": ["43900"],
                "Cyberjaya": ["63000"],
                "KLIA": ["64000"]
            }
            
            # 检查输入是否匹配邮编范围名称或名称的一部分
            matched_areas = []
            normalized_query = location_query.lower()
            for area, postcodes in postcode_ranges.items():
                if normalized_query in area.lower() or area.lower() in normalized_query:
                    matched_areas.append((area, postcodes))
            
            # 如果匹配到地区名称，查询该地区所有邮编范围内的店铺
            if matched_areas:
                area_results = []
                for area, postcodes in matched_areas:
                    print(f"匹配到地区: {area}, 邮编范围: {postcodes}")
                    # 构建邮编范围查询条件
                    postcode_conditions = []
                    params = []
                    
                    for postcode in postcodes:
                        if "-" in postcode:  # 处理带范围的邮编
                            start, end = postcode.split("-")
                            # 这里假设邮编是数字
                            postcode_conditions.append(
                                "(CAST(postcode AS INTEGER) BETWEEN %s AND %s)")
                            params.extend([start, end])
                        else:  # 精确匹配单个邮编
                            postcode_conditions.append("postcode = %s")
                            params.append(postcode)
                    
                    # 添加地区名称匹配
                    postcode_conditions.append(
                        "(city ILIKE %s OR district ILIKE %s OR address ILIKE %s)")
                    params.extend([f"%{area}%", f"%{area}%", f"%{area}%"])
                    
                    # 添加店铺名称匹配
                    postcode_conditions.append("name ILIKE %s")
                    params.append(f"%{area}%")
                    
                    query = "SELECT * FROM subway_outlets WHERE " + \
                        " OR ".join(postcode_conditions)
                    cur.execute(query, params)
                    results = cur.fetchall()
                    if results:
                        area_results.extend([dict(r) for r in results])
                
                if area_results:
                    print(f"通过邮编范围匹配成功，找到 {len(area_results)} 个结果")
                    return area_results
            
            # 多种匹配条件
            query = """
                SELECT * FROM subway_outlets
                WHERE 
                    city ILIKE %s 
                    OR district ILIKE %s 
                    OR address ILIKE %s
                    OR street_address ILIKE %s
                    OR name ILIKE %s
            """
            params = [
                f"%{location_query}%",  # 城市
                f"%{location_query}%",  # 区域
                f"%{location_query}%",  # 完整地址
                f"%{location_query}%",  # 街道
                f"%{location_query}%"   # 店名
            ]
            
            cur.execute(query, params)
            results = [dict(r) for r in cur.fetchall()]
            
            # 如果没找到，尝试更模糊的搜索
            if not results:
                # 1. 尝试部分匹配
                words = location_query.split()
                if len(words) > 1:
                    for word in words:
                        if len(word) > 3:  # 只使用较长的词以避免匹配太多无关项
                            cur.execute("SELECT * FROM subway_outlets WHERE address ILIKE %s OR name ILIKE %s OR city ILIKE %s OR district ILIKE %s", 
                                      [f'%{word}%', f'%{word}%', f'%{word}%', f'%{word}%'])
                            partial_results = [dict(r) for r in cur.fetchall()]
                            if partial_results:
                                print(f"找到部分匹配: {word}")
                                results.extend(partial_results)
                
                # 2. 尝试处理邮编格式
                # 添加对邮编格式的特殊处理（处理不同长度的邮编）
                if location_query.isdigit() and 4 <= len(location_query) <= 6:
                    padded_postcode = location_query.zfill(5)  # 将邮编填充到5位数
                    cur.execute(
    "SELECT * FROM subway_outlets WHERE address ILIKE %s OR postcode = %s", [
        f'%{padded_postcode}%', padded_postcode])
                    postcode_fuzzy_results = [dict(r) for r in cur.fetchall()]
                    if postcode_fuzzy_results:
                        print(f"邮编模糊匹配成功: {padded_postcode}")
                        results.extend(postcode_fuzzy_results)
                
                # 3. 尝试同音字或拼写相似的单词
                if not results and len(location_query) > 3:
                    # 例如：Bangsar, Bandar, Bangi - 开头相似
                    prefix = location_query[:3]
                    cur.execute("""
                        SELECT * FROM subway_outlets 
                        WHERE address ILIKE %s 
                        OR city ILIKE %s 
                        OR district ILIKE %s
                        OR name ILIKE %s
                    """, [f'%{prefix}%', f'%{prefix}%', f'%{prefix}%', f'%{prefix}%'])
                    similar_results = [dict(r) for r in cur.fetchall()]
                    if similar_results:
                        print(f"找到相似匹配: {prefix}")
                        results.extend(similar_results)
                
                # 4. 尝试使用特定键值对查询
                if not results:
                    location_lower = location_query.lower()
                    specific_locations = {
                        'bangsar': ['bangsar', 'telawi', 'jalan bangsar', 'lorong maarof', 'kerinchi', 'pantai', 'abdullah', 'menara ub', 'pusat', 'village'],
                        'bandar': ['bandar', 'sri', 'maju', 'puteri', 'utama', 'kinrara', 'sunway', 'sri', 'pudu', 'baru'],
                        'ampang': ['ampang', 'ukay', 'hulu', 'jaya', 'point', 'ampang park', 'ampang putra'],
                        'klcc': ['klcc', 'kuala lumpur city centre', 'pavilion', 'bukit bintang', 'suria', 'twin towers', 'avenue k'],
                        'sunway': ['sunway', 'subang', 'usj', 'sunway pyramid', 'subang jaya', 'sunway velocity'],
                        'damansara': ['damansara', 'mutiara', 'uptown', 'utama', 'perdana', 'heights', 'damai', 'jaya'],
                        'pj': ['petaling jaya', 'pj', 'ss2', 'ss15', 'kelana jaya', 'damansara'],
                        'kl': ['kuala lumpur', 'kl', 'sentral', 'berjaya times square', 'pavilion', 'kl gateway'],
                        'shah alam': ['shah alam', 'seksyen', 'setia alam', 'bukit jelutong']
                    }
                    
                    # 查找匹配的地区
                    matched_areas = []
                    for area, keywords in specific_locations.items():
                        if area in location_lower:
                            matched_areas.append(area)
                        else:
                            # 检查关键词是否匹配
                            for keyword in keywords:
                                if keyword in location_lower:
                                    matched_areas.append(area)
                                    break
                    
                    # 查询匹配地区的店铺
                    for area in matched_areas:
                        keywords = specific_locations[area]
                        for keyword in keywords:
                            cur.execute("""
                                SELECT * FROM subway_outlets 
                                WHERE address ILIKE %s 
                                OR name ILIKE %s
                                OR city ILIKE %s
                                OR district ILIKE %s
                            """, [f'%{keyword}%', f'%{keyword}%', f'%{keyword}%', f'%{keyword}%'])
                            keyword_results = [dict(r) for r in cur.fetchall()]
                            if keyword_results:
                                print(f"找到特定关键词匹配: {keyword}")
                                results.extend(keyword_results)
            
            # 4. 限制最大结果数，避免返回太多
            if len(results) > 20:
                print(f"结果过多 ({len(results)}), 限制为前20个")
                results = results[:20]
            
            # 去重
            seen_ids = set()
            unique_results = []
            for o in results:
                if o['id'] not in seen_ids:
                    seen_ids.add(o['id'])
                    unique_results.append(o)
            
            return unique_results
    except Exception as e:
        print(f"Error finding outlets by location: {e}")
        return []
    finally:
        conn.close()

# 2. 时间相关查询


def find_outlets_by_time(time_query, day=None, is_weekend=None):
    """查找特定时间营业的门店

    参数:
        time_query: 时间查询字符串，如"now"、"9pm"、"after 9pm"、"before 8am"等
        day: 指定星期几，如"monday"、"tuesday"等，默认为当前日期
        is_weekend: 是否为周末查询，如果为None则根据day参数或当前日期判断
    """
    # 默认使用当前时间和星期
    now = datetime.now()
    if not day:
        day_index = now.weekday()
        day = [
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
     "sunday"][day_index]

    # 判断周末
    if is_weekend is None:
        if day in ["saturday", "sunday"]:
            is_weekend = True
        else:
            is_weekend = False

    # 确定要查询的天
    if is_weekend:
        days = ["saturday", "sunday"]
    else:
        days = ["monday", "tuesday", "wednesday", "thursday", "friday"]

    # 如果指定了具体日期，则只查询该天
    if day and day not in days:
        days = [day]
    
    # 将时间转换为24小时制数字
    target_time = None
    print(f"解析时间: {time_query}, 星期: {day}, 是否周末: {is_weekend}")
    
    # 处理特殊时间查询
    if time_query.lower() in ["now", "current", "currently", "open now"]:
        now = datetime.now()
        target_time = f"{now.hour:02d}{now.minute:02d}"
        print(f"当前时间: {target_time}")
    elif ":" in time_query:  # 格式如 "22:30"
        try:
            hour, minute = map(int, time_query.split(":"))
            target_time = f"{hour:02d}{minute:02d}"
        except:
            print(f"时间格式错误: {time_query}")
    elif "am" in time_query.lower() or "pm" in time_query.lower():  # 格式如 "10:30 PM"
        target_time = convert_12h_to_24h(time_query)
    else:  # 尝试解析数字, 如 "22"
        try:
            # 检查是否为"after X"或"before X"格式
            after_pattern = r'after|past|later than|afternoon|evening|night'
            before_pattern = r'before|earlier than|prior to|morning|dawn'
            at_pattern = r'at|exactly at|precisely at|sharp'
            
            if re.search(after_pattern, time_query.lower()):
                hour_match = re.search(r'(\d+(?:\:\d+)?)', time_query)
                if hour_match:
                    time_str = hour_match.group(1)
                    if ":" in time_str:
                        hour, minute = map(int, time_str.split(":"))
                    else:
                        hour = int(time_str)
                        minute = 0
                    
                    # 处理12小时制
                    if "pm" in time_query.lower() and hour < 12:
                        hour += 12
                    elif "am" in time_query.lower() and hour == 12:
                        hour = 0
                    # 处理下午/晚上的情况
                    elif hour <= 12 and ("afternoon" in time_query.lower() or
                                      "evening" in time_query.lower() or
                                      "night" in time_query.lower()):
                        hour += 12
                        
                    target_time = f"{hour:02d}{minute:02d}"
                    print(f"'after {hour}:{minute}' => {target_time}")
                    # 标记为"之后"查询
                    is_after_query = True
                    is_before_query = False
                    is_at_query = False
            elif re.search(before_pattern, time_query.lower()):
                hour_match = re.search(r'(\d+(?:\:\d+)?)', time_query)
                if hour_match:
                    time_str = hour_match.group(1)
                    if ":" in time_str:
                        hour, minute = map(int, time_str.split(":"))
                    else:
                        hour = int(time_str)
                        minute = 0
                    
                    # 处理12小时制
                    if "pm" in time_query.lower() and hour < 12:
                        hour += 12
                    elif "am" in time_query.lower() and hour == 12:
                        hour = 0
                    # 处理早上的情况
                    elif hour <= 8 and "morning" in time_query.lower():
                        # 早上通常是AM
                        pass
                        
                    target_time = f"{hour:02d}{minute:02d}"
                    print(f"'before {hour}:{minute}' => {target_time}")
                    # 标记为"之前"查询
                    is_after_query = False
                    is_before_query = True
                    is_at_query = False
            elif re.search(at_pattern, time_query.lower()):
                hour_match = re.search(r'(\d+(?:\:\d+)?)', time_query)
                if hour_match:
                    time_str = hour_match.group(1)
                    if ":" in time_str:
                        hour, minute = map(int, time_str.split(":"))
                    else:
                        hour = int(time_str)
                        minute = 0

                    # 处理12小时制
                    if "pm" in time_query.lower() and hour < 12:
                        hour += 12
                    elif "am" in time_query.lower() and hour == 12:
                        hour = 0

                    target_time = f"{hour:02d}{minute:02d}"
                    print(f"'at {hour}:{minute}' => {target_time}")
                    # 标记为"特定时间"查询
                    is_after_query = False
                    is_before_query = False
                    is_at_query = True
            else:
                # 尝试直接解析为小时
                hour_match = re.search(r'(\d+(?:\:\d+)?)', time_query)
                if hour_match:
                    time_str = hour_match.group(1)
                    if ":" in time_str:
                        hour, minute = map(int, time_str.split(":"))
                    else:
                        hour = int(time_str)
                        minute = 0
                    
                    # 处理12小时制 (假设晚上的时间大部分是PM)
                    if hour <= 12 and ("evening" in time_query.lower() or 
                                       "night" in time_query.lower() or 
                                       "pm" in time_query.lower()):
                        hour += 12
                    target_time = f"{hour:02d}{minute:02d}"

                    # 默认为"在此时刻开门"查询
                    is_after_query = False
                    is_before_query = False
                    is_at_query = True
        except Exception as e:
            print(f"解析时间错误: {time_query}, {e}")
            return []
    
    if not target_time:
        print(f"无法解析时间: {time_query}")
        return []
    
    print(f"目标时间: {target_time}")
    target_hour = int(target_time[:2])
    target_minute = int(target_time[2:]) if len(target_time) >= 4 else 0

    # 如果没有显式指定查询类型，尝试从原始查询中推断
    if 'is_after_query' not in locals():
        is_after_query = re.search(
    r'after|past|later than|晚上|下午|evening|night|pm',
     time_query.lower()) is not None
    if 'is_before_query' not in locals():
        is_before_query = re.search(
    r'before|earlier than|prior to|morning|早上|am',
     time_query.lower()) is not None
    if 'is_at_query' not in locals():
        is_at_query = not is_after_query and not is_before_query
    
    conn = connect_db()
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            # 查询所有有营业时间的店铺
            cur.execute("""
                SELECT * FROM subway_outlets
                WHERE opening_hours IS NOT NULL
                OR is_24hours = true
                    OR operating_hours ILIKE '%24%'
                    OR operating_hours ILIKE '%24 hours%'
            """)
            
            rows = cur.fetchall()
            results = []
            
            # 先添加24小时营业的店铺
            for row in rows:
                row_dict = dict(row)
                if row_dict.get('is_24hours') or '24' in (
                    row_dict.get('operating_hours') or ''):
                    results.append(row_dict)

            # 处理其他有营业时间的店铺
            for row in rows:
                row_dict = dict(row)
                # 跳过已添加的24小时店铺
                if row_dict.get('is_24hours') or '24' in (
                    row_dict.get('operating_hours') or ''):
                    continue
                
                hours = row_dict.get('opening_hours', {})
                if not hours:
                            continue

                # 检查指定天的营业时间
                for check_day in days:
                    if check_day not in hours or not hours[check_day]:
                            continue

                    day_hours = hours[check_day]
                    if 'open' not in day_hours or 'close' not in day_hours:
                        continue

                    open_time = day_hours['open']
                    close_time = day_hours['close']

                    if not open_time or not close_time:
                        continue

                    # 解析开关门时间
                    open_hour = int(open_time[:2])
                    open_minute = int(open_time[2:]) if len(
                        open_time) >= 4 else 0
                    close_hour = int(close_time[:2])
                    close_minute = int(close_time[2:]) if len(
                        close_time) >= 4 else 0

                    # 处理跨午夜情况
                    is_overnight = False
                    if close_hour < open_hour or (
    close_hour == open_hour and close_minute < open_minute):
                        is_overnight = True

                    # 根据查询类型进行匹配
                    if is_after_query:
                        # "after X" 查询 - 在目标时间后仍然营业
                        if is_overnight:
                            # 跨午夜情况
                            if (open_hour <= target_hour and target_hour <= 23) or \
                               (0 <= target_hour < close_hour) or \
                               (target_hour == close_hour and close_minute > 0):
                                # 只要目标时间在营业时间内，或者关门时间比目标时间晚
                                results.append(row_dict)
                                break
                        else:
                            # 当天情况 - 关门时间必须晚于目标时间
                            if close_hour > target_hour or \
                               (close_hour == target_hour and close_minute > target_minute):
                                results.append(row_dict)
                                break
                    elif is_before_query:
                        # "before X" 查询 - 开门时间早于目标时间
                        if open_hour < target_hour or \
                           (open_hour == target_hour and open_minute <= target_minute):
                            results.append(row_dict)
                            break
                    else:  # is_at_query
                        # "at X" 查询 - 查询时间在营业时间内
                        if is_overnight:
                            # 跨午夜情况
                            if (open_hour <= target_hour and target_hour <= 23) or \
                               (0 <= target_hour < close_hour) or \
                               (target_hour == close_hour and target_minute <= close_minute):
                                results.append(row_dict)
                                break
                        else:
                            # 当天情况
                                        if (open_hour < target_hour or (open_hour == target_hour and open_minute <= target_minute)) and \
                                           (target_hour < close_hour or (target_hour == close_hour and target_minute <= close_minute)):
                                            results.append(row_dict)
                                            break

            return results
    except Exception as e:
        print(f"查找符合时间条件的店铺时出错: {e}")
        return []
    finally:
        conn.close()

# 查询特定时间开店的门店


def find_outlets_by_opening_time(time_query, is_weekend=False):
    """查找在特定时间开店的门店

    参数:
        time_query: 时间查询字符串，如"9am"、"10:30"等
        is_weekend: 是否为周末查询

    返回值:
        符合条件的门店列表
    """
    conn = connect_db()
    try:
        # 解析目标时间
        target_time = None

        # 检查是否为"之前"或"之后"查询
        is_before = "before" in time_query.lower() or "earlier" in time_query.lower()
        is_after = "after" in time_query.lower() or "later" in time_query.lower()
        
        # 清理查询字符串，移除"before"/"after"等词汇以便解析实际时间
        clean_time = time_query.lower()
        clean_time = re.sub(r'before|after|earlier|later|than|open|opens|at|still', '', clean_time).strip()
        
        print(f"清理后的时间查询: '{clean_time}'")

        # 处理时间格式
        if ":" in clean_time:  # 格式如 "9:30"
            try:
                hour, minute = map(int, clean_time.split(":"))
                target_time = f"{hour:02d}{minute:02d}"
            except:
                print(f"时间格式错误: {clean_time}")
                return []
        elif "am" in clean_time.lower() or "pm" in clean_time.lower():  # 格式如 "9am"
            target_time = convert_12h_to_24h(clean_time)
            print(f"12小时制转换结果: {clean_time} -> {target_time}")
        else:  # 尝试解析数字, 如 "9"
            try:
                hour = int(re.search(r'(\d+)', clean_time).group(1))
                print(f"提取到小时: {hour}")
                # 处理下午/晚上的情况
                if hour <= 12 and ("evening" in clean_time.lower() or
                                 "night" in clean_time.lower() or
                                 "pm" in clean_time.lower()):
                    hour += 12
                    print(f"调整为PM: {hour}")
                target_time = f"{hour:02d}00"
            except Exception as e:
                print(f"无法解析时间: {clean_time}, 错误: {e}")
                return []

        if not target_time:
            print(f"无法解析时间: {clean_time}")
            return []

        print(f"目标时间: {target_time}")
        if is_before:
            print(f"查找在 {target_time} 之前开店的门店")
        elif is_after:
            print(f"查找在 {target_time} 之后开店的门店")
        else:
            print(f"查找在 {target_time} 开店的门店")

        # 确定要查询的天
        days = [
    "saturday",
    "sunday"] if is_weekend else [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
         "friday"]

        print(f"查询的日期: {days}")

        with conn.cursor(cursor_factory=DictCursor) as cur:
            # 查询所有有营业时间的店铺
            cur.execute("""
                SELECT * FROM subway_outlets
                WHERE opening_hours IS NOT NULL
            """)

            rows = cur.fetchall()
            print(f"查询到 {len(rows)} 家有营业时间的店铺")
            results = []

            matched_count = 0
            
            for row in rows:
                row_dict = dict(row)
                hours = row_dict.get('opening_hours', {})

                if not hours:
                    continue
                    
                # 检查每个指定天的开门时间
                for day in days:
                    if day not in hours:
                        # print(f"店铺 {row_dict['name']} 没有 {day} 的数据")
                        continue
                        
                    if not hours[day]:
                        # print(f"店铺 {row_dict['name']} 在 {day} 没有营业时间")
                        continue
                        
                    if 'open' not in hours[day]:
                        # print(f"店铺 {row_dict['name']} 在 {day} 没有开门时间")
                        continue
                    
                    open_time = hours[day]['open']
                    if not open_time:
                        continue
                    
                    # 输出调试信息
                    print(f"店铺 {row_dict['name']} 在 {day} 的开门时间: {open_time}")
                    
                    # 根据查询类型匹配
                    if is_before:
                        # 查找在指定时间之前开门的店铺
                        if open_time < target_time:
                            print(f"匹配! {row_dict['name']} 在 {target_time} 之前开门 ({open_time})")
                            if row_dict not in results:  # 避免重复添加
                                results.append(row_dict)
                                matched_count += 1
                            break
                    elif is_after:
                        # 查找在指定时间之后开门的店铺
                        if open_time > target_time:
                            print(f"匹配! {row_dict['name']} 在 {target_time} 之后开门 ({open_time})")
                            if row_dict not in results:  # 避免重复添加
                                results.append(row_dict)
                                matched_count += 1
                            break
                    else:
                        # 查找在指定时间开门的店铺 (允许30分钟的误差)
                        open_hour = int(open_time[:2])
                        open_minute = int(open_time[2:]) if len(
                            open_time) >= 4 else 0
                        target_hour = int(target_time[:2])
                        target_minute = int(target_time[2:]) if len(
                            target_time) >= 4 else 0

                        # 计算时间差（分钟）
                        open_total_mins = open_hour * 60 + open_minute
                        target_total_mins = target_hour * 60 + target_minute
                        diff_mins = abs(open_total_mins - target_total_mins)

                        # 如果时间差在30分钟以内，视为匹配
                        if diff_mins <= 30:
                            print(f"匹配! {row_dict['name']} 开门时间接近 {target_time} (差异 {diff_mins} 分钟)")
                            if row_dict not in results:  # 避免重复添加
                                results.append(row_dict)
                                matched_count += 1
                            break
            
            print(f"共找到 {matched_count} 家符合条件的店铺")
            # 输出前5家店铺名称作为示例
            if results:
                sample_names = [r['name'] for r in results[:5]]
                print(f"示例店铺: {', '.join(sample_names)}")
            return results
    except Exception as e:
        print(f"查询特定时间开店的门店时出错: {e}")
        traceback.print_exc()
        return []
    finally:
        conn.close()

# 查询特定时间关店的门店


def find_outlets_by_closing_time(time_query, is_weekend=False):
    """查找在特定时间关店的门店

    参数:
        time_query: 时间查询字符串，如"9pm"、"22:30"等
        is_weekend: 是否为周末查询

    返回值:
        符合条件的门店列表
    """
    conn = connect_db()
    try:
        # 解析目标时间
        target_time = None
        
        # 检查是否为"之前"或"之后"查询
        is_before = "before" in time_query.lower() or "earlier" in time_query.lower()
        is_after = "after" in time_query.lower() or "later" in time_query.lower()
        
        # 清理查询字符串，移除"before"/"after"等词汇以便解析实际时间
        clean_time = time_query.lower()
        clean_time = re.sub(r'before|after|earlier|later|than|close|closes|at|still', '', clean_time).strip()
        
        print(f"清理后的时间查询: '{clean_time}'")

        # 处理时间格式
        if ":" in clean_time:  # 格式如 "22:30"
            try:
                hour, minute = map(int, clean_time.split(":"))
                target_time = f"{hour:02d}{minute:02d}"
            except:
                print(f"时间格式错误: {clean_time}")
                return []
        elif "am" in clean_time.lower() or "pm" in clean_time.lower():  # 格式如 "10pm"
            target_time = convert_12h_to_24h(clean_time)
            print(f"12小时制转换结果: {clean_time} -> {target_time}")
        else:  # 尝试解析数字, 如 "22"
            try:
                hour = int(re.search(r'(\d+)', clean_time).group(1))
                print(f"提取到小时: {hour}")
                # 处理下午/晚上的情况
                if hour <= 12 and ("evening" in clean_time.lower() or
                                 "night" in clean_time.lower() or
                                 "pm" in clean_time.lower() or
                                 hour >= 7):  # 晚上7点以后默认为PM
                    hour += 12
                    print(f"调整为PM: {hour}")
                target_time = f"{hour:02d}00"
            except Exception as e:
                print(f"无法解析时间: {clean_time}, 错误: {e}")
                return []

        if not target_time:
            print(f"无法解析时间: {clean_time}")
            return []

        print(f"目标时间: {target_time}")
        if is_before:
            print(f"查找在 {target_time} 之前关店的门店")
        elif is_after:
            print(f"查找在 {target_time} 之后关店的门店")
        else:
            print(f"查找在 {target_time} 关店的门店")

        # 确定要查询的天
        days = [
    "saturday",
    "sunday"] if is_weekend else [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
         "friday"]

        print(f"查询的日期: {days}")

        with conn.cursor(cursor_factory=DictCursor) as cur:
            # 查询所有有营业时间的店铺
            cur.execute("""
                SELECT * FROM subway_outlets
                WHERE opening_hours IS NOT NULL
            """)

            rows = cur.fetchall()
            print(f"查询到 {len(rows)} 家有营业时间的店铺")
            results = []
            
            matched_count = 0

            for row in rows:
                row_dict = dict(row)
                hours = row_dict.get('opening_hours', {})

                if not hours:
                    continue

                # 检查每个指定天的关门时间
                for day in days:
                    if day not in hours:
                        # print(f"店铺 {row_dict['name']} 没有 {day} 的数据")
                        continue
                        
                    if not hours[day]:
                        # print(f"店铺 {row_dict['name']} 在 {day} 没有营业时间")
                        continue
                        
                    if 'close' not in hours[day]:
                        # print(f"店铺 {row_dict['name']} 在 {day} 没有关门时间")
                        continue

                    close_time = hours[day]['close']
                    if not close_time:
                        continue
                    
                    # 输出调试信息
                    print(f"店铺 {row_dict['name']} 在 {day} 的关门时间: {close_time}")
                    
                    # 处理跨午夜情况
                    day_hours = hours[day]
                    adjusted_close_time = close_time
                    if 'open' in day_hours and day_hours['open'] > close_time:
                        # 跨午夜情况，关门时间应该加上24小时
                        hour = int(close_time[:2]) + 24
                        minute = int(close_time[2:]) if len(
                            close_time) >= 4 else 0
                        adjusted_close_time = f"{hour:02d}{minute:02d}"
                        print(f"  跨午夜情况，调整后关门时间: {adjusted_close_time}")

                    # 根据查询类型匹配
                    if is_before:
                        # 查找在指定时间之前关门的店铺
                        if adjusted_close_time < target_time:
                            print(f"匹配! {row_dict['name']} 在 {target_time} 之前关门 ({close_time})")
                            if row_dict not in results:  # 避免重复添加
                                results.append(row_dict)
                                matched_count += 1
                            break
                    elif is_after:
                        # 查找在指定时间之后关门的店铺
                        if adjusted_close_time > target_time:
                            print(f"匹配! {row_dict['name']} 在 {target_time} 之后关门 ({close_time})")
                            if row_dict not in results:  # 避免重复添加
                                results.append(row_dict)
                                matched_count += 1
                            break
                    else:
                        # 查找在指定时间关门的店铺 (允许30分钟的误差)
                        close_hour = int(adjusted_close_time[:2])
                        close_minute = int(adjusted_close_time[2:]) if len(
                            adjusted_close_time) >= 4 else 0
                        target_hour = int(target_time[:2])
                        target_minute = int(target_time[2:]) if len(
                            target_time) >= 4 else 0

                        # 处理跨午夜情况进行比较
                        if close_hour >= 24:
                            close_hour -= 24

                        # 计算时间差（分钟）
                        close_total_mins = close_hour * 60 + close_minute
                        target_total_mins = target_hour * 60 + target_minute
                        diff_mins = abs(close_total_mins - target_total_mins)

                        # 如果时间差在30分钟以内，视为匹配
                        if diff_mins <= 30:
                            print(f"匹配! {row_dict['name']} 关门时间接近 {target_time} (差异 {diff_mins} 分钟)")
                            if row_dict not in results:  # 避免重复添加
                                results.append(row_dict)
                                matched_count += 1
                            break
            
            print(f"共找到 {matched_count} 家符合条件的店铺")
            # 输出前5家店铺名称作为示例
            if results:
                sample_names = [r['name'] for r in results[:5]]
                print(f"示例店铺: {', '.join(sample_names)}")
            return results
    except Exception as e:
        print(f"查询特定时间关店的门店时出错: {e}")
        traceback.print_exc()
        return []
    finally:
        conn.close()

# 3. 获取最近的店铺


def get_nearest_outlets(lat, lon, limit=5):
    """获取距离给定坐标最近的n个门店"""
    conn = connect_db()
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute(
                "SELECT * FROM subway_outlets WHERE latitude IS NOT NULL AND longitude IS NOT NULL;")
            outlets = [dict(r) for r in cur.fetchall()]
            
            # 计算每个门店的距离
            for outlet in outlets:
                outlet['distance'] = calculate_distance(
                    lat, lon,
                    outlet['latitude'], outlet['longitude']
                )
            
            # 按距离排序并返回前n个
            nearest = sorted(outlets, key=lambda x: x['distance'])[:limit]
            return nearest
    except Exception as e:
        print(f"Error finding nearest outlets: {e}")
        return []
    finally:
        conn.close()

# 4. 复合查询


def find_outlets_compound(
    location_query,
    time_query=None,
    day=None,
    is_weekend=None):
    """复合查询：位置+时间

    参数:
        location_query: 位置查询字符串
        time_query: 时间查询字符串，如"now"、"9pm"等
        day: 指定星期几，如"monday"、"tuesday"等
        is_weekend: 是否为周末查询

    返回值:
        符合条件的门店列表
    """
    print(f"执行复合查询 - 位置: '{location_query}', 时间: '{time_query}', 日期: '{day}', 周末: {is_weekend}")
    
    # 判断当前是否为周末
    if is_weekend is None:
        if day:
            is_weekend = day.lower() in ["saturday", "sunday"]
        else:
            today = datetime.now().weekday()
            is_weekend = today >= 5  # 5和6分别是周六和周日

    # 先按位置筛选
    location_results = find_outlets_by_location(location_query)
    print(f"位置查询 '{location_query}' 找到 {len(location_results)} 家店铺")
    
    # 如果没有时间条件，直接返回
    if not time_query:
        return location_results
    
    # 检查是否为特殊的开关门时间查询
    time_query_lower = time_query.lower()
    
    # 清理时间查询字符串，确保它不包含位置信息
    # 这可能是查询解析的问题，导致时间条件中包含了位置信息
    location_lower = location_query.lower()
    if location_lower in time_query_lower:
        # 从时间查询中移除位置信息
        time_query_lower = time_query_lower.replace(location_lower, "").strip()
        # 如果有多余的"at"、"in"等词，也去掉
        time_query_lower = re.sub(r'\s+(?:at|in)\s+$', '', time_query_lower).strip()
        print(f"清理后的时间查询: '{time_query_lower}'")
    
    # 判断查询类型（开门/关门）和查询方式（before/after）
    is_still_open_query = "still open" in time_query_lower
    is_open_after_query = ("open after" in time_query_lower or "opens after" in time_query_lower)
    
    # 合并"still open"和"open after"为同一类查询
    is_after_open_query = is_still_open_query or is_open_after_query or ("open" in time_query_lower and "after" in time_query_lower)
    
    # 只有"open before"类查询才归类为普通开门查询
    is_opening_query = ("open" in time_query_lower and "before" in time_query_lower) and not any(kw in time_query_lower for kw in ["now", "currently"])
    
    is_closing_query = "close" in time_query_lower or "closing" in time_query_lower
    is_before_query = "before" in time_query_lower
    is_after_query = "after" in time_query_lower
    is_earliest_latest_query = any(kw in time_query_lower for kw in ["earliest", "latest"])
    
    print(f"查询分析 - 开门查询: {is_opening_query}, 关门查询: {is_closing_query}, after时间营业查询: {is_after_open_query}")
    print(f"查询分析 - before查询: {is_before_query}, after查询: {is_after_query}")
    print(f"查询分析 - earliest/latest查询: {is_earliest_latest_query}")
    
    # 特殊处理: 如果只有before/after时间查询，没有明确指定open/close，则推断查询意图
    if is_before_query and not (is_opening_query or is_closing_query or is_after_open_query):
        # 假设带有before的查询是关闭时间查询
        print(f"推断查询意图: 'before'查询被解释为关门时间查询")
        is_closing_query = True
    
    # 处理"仍然营业"查询和"open after"查询（两者使用同样的逻辑 - find_outlets_by_time函数）
    if is_after_open_query:
        print(f"处理'在特定时间后营业'查询: {time_query_lower}")
        # 使用find_outlets_by_time处理
        time_results = find_outlets_by_time(time_query_lower, day, is_weekend)
        if time_results:
            print(f"找到 {len(time_results)} 家店铺，筛选位于 '{location_query}' 的店铺")
            location_ids = [r['id'] for r in location_results]
            filtered = [o for o in time_results if o['id'] in location_ids]
            print(f"筛选后剩余 {len(filtered)} 家店铺")
            return filtered
    # 处理开门时间查询
    elif is_opening_query:
        # 处理最早/最晚开门查询
        if is_earliest_latest_query:
            if "earliest" in time_query_lower:
                print("查询最早开门的店铺")
                outlets, open_time = find_earliest_opening_outlets(is_weekend)
            elif "latest" in time_query_lower:
                print("查询最晚开门的店铺")
                outlets, open_time = find_latest_opening_outlets(is_weekend)
            else:
                outlets = []
                
            if outlets:
                print(f"找到 {len(outlets)} 家店铺，筛选位于 '{location_query}' 的店铺")
                location_ids = [r['id'] for r in location_results]
                filtered = [o for o in outlets if o['id'] in location_ids]
                print(f"筛选后剩余 {len(filtered)} 家店铺")
                return filtered
        else:
            # 按具体开门时间查询（before/after）
            print(f"查询在{time_query_lower}开门的店铺")
            time_results = find_outlets_by_opening_time(time_query_lower, is_weekend)
            if time_results:
                print(f"找到 {len(time_results)} 家店铺，筛选位于 '{location_query}' 的店铺")
                location_ids = [r['id'] for r in location_results]
                filtered = [o for o in time_results if o['id'] in location_ids]
                print(f"筛选后剩余 {len(filtered)} 家店铺")
                return filtered
    
    # 处理关门时间查询
    elif is_closing_query or (is_before_query and not is_opening_query):
        # 处理最早/最晚关门查询
        if is_earliest_latest_query:
            if "earliest" in time_query_lower:
                print("查询最早关门的店铺")
                outlets, close_time = find_earliest_closing_outlets(is_weekend)
            elif "latest" in time_query_lower:
                print("查询最晚关门的店铺")
                outlets, close_time = find_latest_closing_outlets(is_weekend)
            else:
                outlets = []
                
            if outlets:
                print(f"找到 {len(outlets)} 家店铺，筛选位于 '{location_query}' 的店铺")
                location_ids = [r['id'] for r in location_results]
                filtered = [o for o in outlets if o['id'] in location_ids]
                print(f"筛选后剩余 {len(filtered)} 家店铺")
                return filtered
        else:
            # 按具体关门时间查询（before/after）
            # 如果只有"before X"这样的查询，并且没有明确指定关门，也当作关门时间查询处理
            if is_before_query and not "close" in time_query_lower:
                print(f"将'{time_query_lower}'解释为关门时间查询")
                # 确保时间查询格式正确
                if not time_query_lower.startswith("close"):
                    time_query_lower = f"close {time_query_lower}"
            
            print(f"查询在{time_query_lower}关门的店铺")
            time_results = find_outlets_by_closing_time(time_query_lower, is_weekend)
            if time_results:
                print(f"找到 {len(time_results)} 家店铺，筛选位于 '{location_query}' 的店铺")
                location_ids = [r['id'] for r in location_results]
                filtered = [o for o in time_results if o['id'] in location_ids]
                print(f"筛选后剩余 {len(filtered)} 家店铺")
                return filtered

    # 一般时间查询 - 查询在特定时间点营业的店铺
    print(f"执行一般时间查询，查询在 '{time_query_lower}' 营业的店铺")
    time_results = find_outlets_by_time(time_query_lower, day, is_weekend)
    print(f"时间查询找到 {len(time_results)} 家店铺")
    location_ids = [r['id'] for r in location_results]

    # 取交集
    filtered = [r for r in time_results if r['id'] in location_ids]
    print(f"与位置查询结果取交集后，剩余 {len(filtered)} 家店铺")
    return filtered

# 5. 查找最早开门的店铺


def find_earliest_opening_outlets(is_weekend=False):
    """查找最早开门的店铺，区分周末和工作日"""
    conn = connect_db()
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            # 获取所有有营业时间的店铺
            cur.execute("""
                SELECT id, name, address, opening_hours, operating_hours, latitude, longitude 
                FROM subway_outlets 
                WHERE opening_hours IS NOT NULL 
            """)

            # 根据是否为周末选择要查询的天
            days = [
    "saturday",
    "sunday"] if is_weekend else [
        "monday",
        "tuesday",
        "wednesday",
        "thursday",
         "friday"]
            
            outlets = []
            earliest_time = "2400"  # 初始值设为最晚
            outlet_ids = set()  # 用于跟踪已添加的店铺ID
            
            for row in cur.fetchall():
                row_dict = dict(row)
                hours = row_dict.get('opening_hours', {})
                
                # 跳过没有营业时间的店铺
                if not hours:
                            continue
                        
                # 检查指定天的开门时间
                for day in days:
                    if day not in hours or not hours[day] or 'open' not in hours[day]:
                        continue

                    open_time = hours[day]['open']
                    if not open_time:
                        continue

                    # 更新最早时间
                    if open_time < earliest_time:
                        earliest_time = open_time
                        outlets = [row_dict]  # 重置列表
                        outlet_ids = {row_dict['id']}  # 重置ID集合
                    elif open_time == earliest_time and row_dict['id'] not in outlet_ids:
                        outlets.append(row_dict)  # 添加到列表
                        outlet_ids.add(row_dict['id'])  # 记录ID已添加
            
            # 为每个店铺添加开门时间
            for outlet in outlets:
                outlet['opening_time'] = earliest_time
                # 添加相关天信息
                outlet['days'] = "weekend" if is_weekend else "weekday"
            
            return outlets, earliest_time
    except Exception as e:
        print(f"Error finding earliest opening outlets: {e}")
        return [], "2400"
    finally:
        conn.close()

# 新增：查找最晚开门的店铺
def find_latest_opening_outlets(is_weekend=False):
    """查找最晚开门的店铺，区分周末和工作日"""
    conn = connect_db()
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            # 获取所有有营业时间的店铺
            cur.execute("""
                SELECT id, name, address, opening_hours, operating_hours, latitude, longitude 
                FROM subway_outlets 
                WHERE opening_hours IS NOT NULL
            """)
            
            # 根据是否为周末选择要查询的天
            days = ["saturday", "sunday"] if is_weekend else ["monday", "tuesday", "wednesday", "thursday", "friday"]
            
            outlets = []
            latest_time = "0000"  # 初始值设为最早
            outlet_ids = set()  # 用于跟踪已添加的店铺ID
            
            for row in cur.fetchall():
                row_dict = dict(row)
                hours = row_dict.get('opening_hours', {})
                
                # 跳过没有营业时间的店铺
                if not hours:
                                    continue
                            
                # 检查指定天的开门时间
                for day in days:
                    if day not in hours or not hours[day] or 'open' not in hours[day]:
                        continue
                    
                    open_time = hours[day]['open']
                    if not open_time:
                        continue
                    
                    # 更新最晚时间
                    if open_time > latest_time:
                        latest_time = open_time
                        outlets = [row_dict]  # 重置列表
                        outlet_ids = {row_dict['id']}  # 重置ID集合
                    elif open_time == latest_time and row_dict['id'] not in outlet_ids:
                        outlets.append(row_dict)  # 添加到列表
                        outlet_ids.add(row_dict['id'])  # 记录ID已添加
            
            # 为每个店铺添加开门时间
            for outlet in outlets:
                outlet['opening_time'] = latest_time
                # 添加相关天信息
                outlet['days'] = "weekend" if is_weekend else "weekday"
            
            return outlets, latest_time
    except Exception as e:
        print(f"Error finding latest opening outlets: {e}")
        return [], "0000"
    finally:
        conn.close()

# 新增：查找最早关门的店铺
def find_earliest_closing_outlets(is_weekend=False):
    """查找最早关门的店铺，区分周末和工作日"""
    conn = connect_db()
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("""
                SELECT id, name, address, opening_hours, operating_hours, latitude, longitude 
                FROM subway_outlets 
                WHERE opening_hours IS NOT NULL 
            """)
            
            # 根据是否为周末选择要查询的天
            days = ["saturday", "sunday"] if is_weekend else ["monday", "tuesday", "wednesday", "thursday", "friday"]
            
            outlets = []
            earliest_time = "2500"  # 初始值设为超晚（比24小时还晚）
            outlet_ids = set()  # 用于跟踪已添加的店铺ID
            
            for row in cur.fetchall():
                row_dict = dict(row)
                hours = row_dict.get('opening_hours', {})
                
                # 跳过没有营业时间的店铺
                if not hours:
                            continue
                        
                # 检查指定天的关门时间
                for day in days:
                    if day not in hours or not hours[day] or 'close' not in hours[day]:
                        continue
                    
                    close_time = hours[day]['close']
                    if not close_time:
                        continue
                    
                    # 处理跨午夜情况 (如果关门时间早于开门时间，可能是第二天凌晨)
                    day_hours = hours[day]
                    # 跳过跨午夜的情况，因为我们要找最早关门的（非凌晨关门的）
                    if 'open' in day_hours and day_hours['open'] > close_time:
                                    continue
                            
                    # 更新最早时间
                    if close_time < earliest_time:
                        earliest_time = close_time
                        outlets = [row_dict]  # 重置列表
                        outlet_ids = {row_dict['id']}  # 重置ID集合
                    elif close_time == earliest_time and row_dict['id'] not in outlet_ids:
                        outlets.append(row_dict)  # 添加到列表
                        outlet_ids.add(row_dict['id'])  # 记录ID已添加
            
            # 为每个店铺添加关门时间
            for outlet in outlets:
                outlet['closing_time'] = earliest_time
                # 添加相关天信息
                outlet['days'] = "weekend" if is_weekend else "weekday"
            
            return outlets, earliest_time
    except Exception as e:
        print(f"Error finding earliest closing outlets: {e}")
        return [], "2400"
    finally:
        conn.close()

# 6. 查找最晚关门的店铺
def find_latest_closing_outlets(is_weekend=False):
    """查找最晚关门的店铺，区分周末和工作日"""
    conn = connect_db()
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("""
                SELECT id, name, address, opening_hours, operating_hours, latitude, longitude 
                FROM subway_outlets 
                WHERE opening_hours IS NOT NULL
            """)
            
            # 根据是否为周末选择要查询的天
            days = ["saturday", "sunday"] if is_weekend else ["monday", "tuesday", "wednesday", "thursday", "friday"]
            
            outlets = []
            latest_time = "0000"  # 初始值设为最早
            outlet_ids = set()  # 用于跟踪已添加的店铺ID
            
            for row in cur.fetchall():
                row_dict = dict(row)
                hours = row_dict.get('opening_hours', {})
                
                # 跳过没有营业时间的店铺
                if not hours:
                    continue
                
                # 检查指定天的关门时间
                for day in days:
                    if day not in hours or not hours[day] or 'close' not in hours[day]:
                        continue
                    
                    close_time = hours[day]['close']
                    if not close_time:
                        continue
                    
                    # 处理跨午夜情况 (如果关门时间早于开门时间，可能是第二天凌晨)
                    day_hours = hours[day]
                    if 'open' in day_hours and day_hours['open'] > close_time:
                        # 这里是跨午夜情况，关门时间应该加上24小时
                        hour = int(close_time[:2]) + 24
                        minute = int(close_time[2:]) if len(close_time) >= 4 else 0
                        close_time = f"{hour:02d}{minute:02d}"
                    
                    # 更新最晚时间
                    if close_time > latest_time:
                        latest_time = close_time
                        outlets = [row_dict]  # 重置列表
                        outlet_ids = {row_dict['id']}  # 重置ID集合
                    elif close_time == latest_time and row_dict['id'] not in outlet_ids:
                        outlets.append(row_dict)  # 添加到列表
                        outlet_ids.add(row_dict['id'])  # 记录ID已添加
            
            # 为每个店铺添加关门时间
            displayed_time = latest_time
            if int(latest_time[:2]) >= 24:
                # 如果是跨午夜的时间，转换回正常显示格式
                hour = int(latest_time[:2]) - 24
                minute = latest_time[2:] if len(latest_time) >= 4 else "00"
                displayed_time = f"{hour:02d}{minute}"
                
            for outlet in outlets:
                outlet['closing_time'] = displayed_time
                # 添加相关天信息
                outlet['days'] = "weekend" if is_weekend else "weekday"
            
            return outlets, displayed_time
    except Exception as e:
        print(f"Error finding latest closing outlets: {e}")
        return [], "0000"
    finally:
        conn.close()

# 7. 获取24小时营业店铺
def find_24hour_outlets():
    """查找所有24小时营业的店铺"""
    conn = connect_db()
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("""
                SELECT * FROM subway_outlets
                WHERE is_24hours = true 
                OR operating_hours ILIKE '%24 hours%' 
                OR operating_hours ILIKE '%24hrs%'
                OR operating_hours ILIKE '%24hr%'
            """)
            return [dict(r) for r in cur.fetchall()]
    except Exception as e:
        print(f"Error finding 24-hour outlets: {e}")
        return []
    finally:
        conn.close()

# 获取特定位置的坐标
def get_location_coordinates(location):
    """尝试找出地点的大致坐标（从数据库中的地址匹配）"""
    outlets = find_outlets_by_location(location)
    
    if not outlets:
        return None, None
    
    # 取平均值作为该区域的中心点
    avg_lat = sum(float(o['latitude']) for o in outlets if o.get('latitude'))/len(outlets)
    avg_lng = sum(float(o['longitude']) for o in outlets if o.get('longitude'))/len(outlets)
    
    return avg_lat, avg_lng

# 通过OpenAI处理查询
def process_with_ai(query):
    # 获取所有可能的地区和店铺示例
    conn = connect_db()
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            # 获取地区示例
            cur.execute("""
                SELECT DISTINCT city 
                FROM subway_outlets 
                WHERE city IS NOT NULL AND city != ''
                LIMIT 20
            """)
            areas = [row[0] for row in cur.fetchall() if row[0]]
            
            # 获取店铺示例
            cur.execute("SELECT id, name, address, opening_hours, latitude, longitude FROM subway_outlets LIMIT 10;")
            outlets = [dict(r) for r in cur.fetchall()]
            
            # 构建系统提示
            system_prompt = f"""You are an AI assistant for a Subway restaurant map application. 
Your task is to interpret user queries about Subway stores and extract relevant information.
You have access to information about Subway outlets in various locations.
Available areas include: {', '.join(areas)}

You should be able to handle queries in different languages, including English, Chinese, and others.
If the query is in a language other than English, understand it and process it accordingly.

When a user asks about stores in a specific location, answer ONLY in this format:
{{
  "answer": "Your natural language answer here",
  "action": "search_location",
  "location": "the_location_name"
}}

When a user asks about store attributes related to opening/closing times, answer ONLY in this format:
{{
  "answer": "Your natural language answer here",
  "action": "get_attribute",
  "attribute": "earliest_opening" | "latest_closing" | "earliest_closing" | "24hours"
}}

IMPORTANT: For "earliest_closing" attribute, this means the outlet that closes the earliest in the day (the first to close),
NOT the outlet that is closest to closing right now.

IMPORTANT CLARIFICATION: 
- If the user asks "Which outlets close before 10pm?", this is a time-based query asking for all outlets that close before a specific time.
  This should be classified as "action": "time_query", with "time": "before 10pm", NOT as a "earliest_closing" attribute query.
- Only use "earliest_closing" attribute when the user specifically asks for the earliest closing outlet(s).

When a user wants to know about stores near a specific location, answer ONLY in this format:
{{
  "answer": "Your natural language answer here",
  "action": "get_nearest",
  "location": "location_name_or_coords"
}}

When a user asks about stores open at a specific time (possibly in a specific location), answer ONLY in this format:
{{
  "answer": "Your natural language answer here",
  "action": "time_query",
  "location": "location_name",
  "time": "specified_time"
}}

When a user asks about stores in a specific location with time conditions (e.g., "stores in Bangsar open after 8pm"), answer in this format:
{{
  "answer": "Your natural language answer here",
  "action": "compound_query",
  "location": "location_name",
  "time": "time_condition"
}}

For "open now" or "currently open" queries, these should be handled as time_query with the current time.

You MUST follow these formats EXACTLY. ONLY reply with the JSON object. Do not add any other text.
"""

            # 没有OpenAI客户端时的备用处理
            if not client:
                print("⚠️ 未配置OpenAI API密钥，使用默认解析")
                
                # 提取位置和时间的简单规则
                location_match = None
                time_match = None
                
                # Check for location keywords in English
                for area in areas:
                    if area.lower() in query.lower():
                        location_match = area
                        break
                
                # Pattern matching for locations in different formats
                location_patterns = [
                    r'in\s+([a-zA-Z\s]+)(?:\s+that|\s+which|\s+area|\s+district)?',  # "in Bangsar area"
                    r'([a-zA-Z\s]+)\s+(?:area|district|region)',  # "Bangsar area"
                    r'near\s+([a-zA-Z\s]+)',  # "near KLCC"
                    r'around\s+([a-zA-Z\s]+)',  # "around Sunway"
                ]
                
                for pattern in location_patterns:
                    match = re.search(pattern, query)
                    if match:
                        potential_location = match.group(1).strip().lower()
                        # Check if extracted location is in our known areas
                        for area in areas:
                            if area.lower() in potential_location:
                                location_match = area
                                break
                        if location_match:
                            break
                
                # Check for time-related patterns
                time_patterns = [
                    r'(?:close|closes|closing)?\s*(?:before|after)\s+(\d+(?::\d+)?(?:\s*[ap]m)?)',  # "close before 9:30am"
                    r'before\s+(\d+(?::\d+)?(?:\s*[ap]m)?)',  # "before 8pm"
                    r'after\s+(\d+(?::\d+)?(?:\s*[ap]m)?)',  # "after 10pm"
                    r'at\s+(\d+(?::\d+)?(?:\s*[ap]m)?)',  # "at 7pm"
                    r'(\d+(?::\d+)?(?:\s*[ap]m)?)',  # "8pm", "9:30am"
                    r'(now|currently|at this time)'  # current time
                ]
                
                for pattern in time_patterns:
                    match = re.search(pattern, query.lower())
                    if match:
                        time_match = match.group(0)
                        break
                
                # Check for opening/closing keywords
                is_opening_query = any(kw in query.lower() for kw in ["open", "opens", "opening"])
                is_closing_query = any(kw in query.lower() for kw in ["close", "closes", "closing"])
                is_still_open_query = any(kw in query.lower() for kw in ["still open", "still operating"])
                is_before_query = "before" in query.lower()
                
                # Handle compound query - location + time
                if location_match and time_match:
                    # Build time condition
                    if is_opening_query:
                        time_condition = f"open {time_match}"
                    elif is_closing_query:
                        time_condition = f"close {time_match}"
                    elif is_still_open_query or "after" in time_match:
                        time_condition = f"open {time_match}"
                    elif is_before_query and not (is_opening_query or is_closing_query):
                        # 如果只有"before X"这样的查询，并且没有明确指定开门或关门，默认为关门查询
                        time_condition = f"close {time_match}"
                    else:
                        time_condition = time_match
                    
                    # 确保时间条件不包含位置信息
                    location_lower = location_match.lower()
                    if location_lower in time_condition.lower():
                        # 从时间查询中移除位置信息
                        time_condition = re.sub(f'(?i)(?:at|in)?\s+{re.escape(location_lower)}', '', time_condition).strip()
                    
                    return {
                        "answer": f"Looking for outlets in {location_match} that match the time condition: {time_condition}.",
                        "action": "compound_query",
                        "location": location_match,
                        "time": time_condition
                    }
                    
                # 以下是原有代码的处理逻辑...
                
                # 检查是否是特定时间开店查询
                if ("open before" in query.lower() or 
                    "opens before" in query.lower()):
                    time_pattern = r'(before|after)\s+(\d+(?::\d+)?(?:\s*[ap]m)?)'
                    match = re.search(time_pattern, query.lower())
                    if match:
                        time_query = match.group(0)
                        return {
                            "answer": f"Let me find Subway outlets that open {time_query}.",
                            "action": "opening_time_query",
                            "time": time_query,
                            "location": location_match
                        }
                
                # 检查是否为特殊时间+位置查询（如最早开门、最晚关门等）
                special_time_patterns = [
                    (r'earliest\s+(?:to\s+)?open', 'earliest_opening'),
                    (r'open\s+(?:the\s+)?earliest', 'earliest_opening'),
                    (r'latest\s+(?:to\s+)?open', 'latest_opening'),
                    (r'open\s+(?:the\s+)?latest', 'latest_opening'),
                    (r'earliest\s+(?:to\s+)?close', 'earliest_closing'),
                    (r'close\s+(?:the\s+)?earliest', 'earliest_closing'),
                    (r'latest\s+(?:to\s+)?close', 'latest_closing'),
                    (r'close\s+(?:the\s+)?latest', 'latest_closing')
                ]
                
                for pattern, attribute in special_time_patterns:
                    if re.search(pattern, query.lower()) and location_match:
                        print(f"检测到特殊时间+位置查询 - 位置: {location_match}, 属性: {attribute}")
                        return {
                            "answer": f"Looking for outlets in {location_match} with attribute: {attribute}",
                            "action": "special_time_location",
                            "location": location_match,
                            "attribute": attribute
                        }
                
                # 检查是否是"still open after"或"open after"查询（同样的处理方式）
                if ("still open after" in query.lower() or 
                    "still open before" in query.lower() or
                    "open after" in query.lower() or
                    "opens after" in query.lower()):
                    time_pattern = r'(before|after)\s+(\d+(?::\d+)?(?:\s*[ap]m)?)'
                    match = re.search(time_pattern, query.lower())
                    if match:
                        time_query = match.group(0)
                        action_type = "still_open_after" if "after" in time_query.lower() else "still_open_before"
                        return {
                            "answer": f"Let me find Subway outlets that are still open {time_query}.",
                            "action": action_type,
                            "time": time_query,
                            "location": location_match
                        }
                
                # 检查是否是特定时间关店查询
                if ("close before" in query.lower() or "close after" in query.lower() or 
                    "closes before" in query.lower() or "closes after" in query.lower()):
                    time_pattern = r'(before|after)\s+(\d+(?::\d+)?(?:\s*[ap]m)?)'
                    match = re.search(time_pattern, query.lower())
                    if match:
                        time_query = match.group(0)
                        return {
                            "answer": f"Let me find Subway outlets that close {time_query}.",
                            "action": "closing_time_query",
                            "time": time_query,
                            "location": location_match
                        }
                
                # 检查是否包含时间关键词
                time_keywords = ["open", "close", "opening", "closing", "hour", "time", "now", "late", "early", "24"]
                has_time_keywords = any(kw in query.lower() for kw in time_keywords)
                
                if location_match and has_time_keywords:
                    return {
                        "answer": f"Let me check Subway outlets in {location_match} for the specified time.",
                        "action": "time_query",
                        "location": location_match,
                        "time": "now" if "now" in query.lower() else "business_hours"
                    }
                elif location_match:
                    return {
                        "answer": f"Let me find Subway outlets in {location_match}.",
                        "action": "search_location",
                        "location": location_match
                    }
                elif "nearest" in query.lower() or "near" in query.lower() or "nearby" in query.lower() or "close to" in query.lower():
                    return {
                        "answer": "Let me find the nearest Subway outlets to your location.",
                        "action": "get_nearest",
                        "location": "user_location"
                    }
                elif "open now" in query.lower() or "currently open" in query.lower():
                    return {
                        "answer": "Let me find Subway outlets that are currently open.",
                        "action": "time_query",
                        "time": "now"
                    }
                elif "earliest opening" in query.lower() or "open earliest" in query.lower() or "open the earliest" in query.lower():
                    return {
                        "answer": "Let me find which Subway outlet opens the earliest.",
                        "action": "get_attribute",
                        "attribute": "earliest_opening"
                    }
                elif "latest closing" in query.lower() or "close latest" in query.lower() or "close the latest" in query.lower():
                    return {
                        "answer": "Let me find which Subway outlet closes the latest.",
                        "action": "get_attribute",
                        "attribute": "latest_closing"
                    }
                elif "earliest closing" in query.lower() or "close earliest" in query.lower() or "close the earliest" in query.lower():
                    return {
                        "answer": "Let me find which Subway outlet closes the earliest.",
                        "action": "get_attribute",
                        "attribute": "earliest_closing"
                    }
                elif "24 hour" in query.lower() or "24hour" in query.lower() or "all day" in query.lower() or "all night" in query.lower():
                    return {
                        "answer": "Let me find Subway outlets that are open 24 hours.",
                        "action": "get_attribute",
                        "attribute": "24hours"
                    }
                elif "latest opening" in query.lower() or "open latest" in query.lower() or "open the latest" in query.lower():
                    return {
                        "answer": "Let me find which Subway outlet opens the latest.",
                        "action": "get_attribute",
                        "attribute": "latest_opening"
                    }
                else:
                    return {
                        "answer": "I'm not sure how to answer that. You can ask about Subway outlets in specific locations, opening times, or find the nearest outlet to your location.",
                        "action": ""
                    }
            
            # 使用OpenAI API
            try:
                # 可视化消息流
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": query}
                ]
                
                response = client.chat.completions.create(
                    model="gpt-3.5-turbo",
                    messages=messages,
                    temperature=0.3,
                    max_tokens=150
                )
                
                result_text = response.choices[0].message.content
                
                # 尝试解析JSON
                try:
                    result = json.loads(result_text)
                    return result
                except json.JSONDecodeError as e:
                    print(f"解析AI回复为JSON时出错: {e}")
                    print(f"原始回复: {result_text}")
                    
                    # 尝试提取JSON部分
                    json_extract = re.search(r'(\{.*?\})', result_text, re.DOTALL)
                    if json_extract:
                        try:
                            result = json.loads(json_extract.group(1))
                            return result
                        except:
                            pass
                    
                    # 备用策略：使用规则
                    return {
                        "answer": result_text,
                        "action": ""
                    }
                    
            except Exception as e:
                print(f"调用OpenAI API时出错: {e}")
                return {
                    "answer": "I'm sorry, I'm having trouble processing your request at the moment. Please try again or ask a different question.",
                    "action": ""
                }
                
    except Exception as e:
        print(f"Error in process_with_ai: {e}")
        return {
            "answer": "I'm sorry, I'm having trouble understanding your request. Could you rephrase it?",
            "action": ""
        }
    finally:
        conn.close()

# === API路由 ===

@app.get("/")
def root():
    return {"message": "Welcome to the Subway API! Use /docs for documentation."}

@app.get("/outlets")
def get_all_outlets():
    """获取所有门店信息"""
    conn = get_db_connection()
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("SELECT * FROM subway_outlets;")
            rows = cur.fetchall()
            outlets = [dict(r) for r in rows]
        return {"outlets": outlets}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()

@app.post("/chatbot/query")
async def query_chatbot(request: ChatRequest):
    user_query = request.query
    user_lat = request.lat
    user_lon = request.lon
    
    # 打印查询详情
    print(f"收到查询: {user_query}")
    print(f"用户位置: 纬度 {user_lat}, 经度 {user_lon}" if user_lat and user_lon else "用户未提供位置")
    
    # 使用process_with_ai处理查询，获取用户意图
    ai_result = process_with_ai(user_query)
    print(f"AI处理结果: {ai_result}")
    
    # === 特殊查询路由 ===
    
    # 处理当前时间的查询
    if "open now" in user_query.lower() or "currently open" in user_query.lower() or "now open" in user_query.lower():
        print("转发到当前时间查询处理器")
        return await handle_current_time_query(request)
    
    # 检测是否包含位置信息的特殊时间查询
    query_lower = user_query.lower()
    
    # 检查是否是get_attribute操作但实际包含位置信息的情况
    if ai_result.get("action") == "get_attribute" and ai_result.get("attribute") in ["earliest_opening", "latest_opening", "earliest_closing", "latest_closing"]:
        # 验证查询中是否真的包含earliest/latest关键词
        attribute_type = ai_result.get("attribute")
        attribute_keywords = {
            "earliest_opening": ["earliest", "first", "soonest"],
            "latest_opening": ["latest", "last"],  
            "earliest_closing": ["earliest", "first", "soonest"],
            "latest_closing": ["latest", "last"]
        }
        
        keywords_present = any(kw in query_lower for kw in attribute_keywords.get(attribute_type, []))
        time_pattern_present = re.search(r'\b(before|after|at)\s+\d+(?::\d+)?\s*(?:am|pm)?\b', query_lower)
        
        if not keywords_present and time_pattern_present:
            print(f"⚠️ 查询'{query_lower}'包含时间匹配模式但缺少'{attribute_type}'关键词，重新路由为时间查询")
            # 提取时间部分
            time_match = re.search(r'\b(before|after|at)\s+\d+(?::\d+)?\s*(?:am|pm)?\b', query_lower)
            if time_match:
                time_query = time_match.group(0)
                # 重定向到时间查询处理
                if "before" in time_query and "close" in query_lower:
                    print(f"重定向到'关门前'时间查询: {time_query}")
                    return find_outlets_by_closing_time(time_query, is_weekend=False)
                elif "after" in time_query and "open" in query_lower:
                    print(f"重定向到'开门后'时间查询: {time_query}")
                    return find_outlets_by_opening_time(time_query, is_weekend=False)
        
        # 继续正常处理位置信息
        conn = connect_db()
        try:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                # 获取已知的所有地区
                cur.execute("""
                    SELECT DISTINCT city 
                    FROM subway_outlets 
                    WHERE city IS NOT NULL AND city != ''
                """)
                all_areas = [row[0].lower() for row in cur.fetchall() if row[0]]
                
                # 检查查询中是否包含已知的地区
                location_in_query = None
                for area in all_areas:
                    # 添加完整单词匹配检查，避免数字或部分字符被错误匹配
                    # 使用正则表达式查找完整的单词匹配
                    if re.search(r'\b' + re.escape(area.lower()) + r'\b', query_lower):
                        location_in_query = area
                        break
                
                # 如果原始AI结果没有位置但查询中包含位置，重新路由
                if location_in_query:
                    print(f"检测到查询中包含位置信息：{location_in_query}，但AI未正确识别。重新路由为特殊时间+位置查询。")
                    # 修改请求属性
                    modified_request = ChatRequest(
                        query=f"{ai_result.get('attribute')} in {location_in_query}",
                        lat=request.lat,
                        lon=request.lon
                    )
                    return await handle_special_time_in_location(modified_request)
        finally:
            conn.close()
    
    # 检查AI是否识别为特殊时间+位置查询
    if ai_result.get("action") == "special_time_location":
        print(f"AI识别为特殊时间+位置查询: {user_query}")
        print(f"位置: {ai_result.get('location')}, 属性: {ai_result.get('attribute')}")
        return await handle_special_time_in_location(request)
    
    # 关键词手动检测最早/最晚开门/关门+位置的查询
    has_special_time_keyword = any(kw in query_lower for kw in [
        "earliest", "latest", "first", "last"
    ])
    has_time_action = any(kw in query_lower for kw in [
        "open", "close", "opening", "closing"
    ])
    location = ai_result.get("location")
    
    # 直接匹配特定格式的查询
    is_special_format = False
    if location:
        special_patterns = [
            r'latest\s+opening\s+(?:outlet|store|subway|restaurant)?\s+in\s+\w+',
            r'earliest\s+opening\s+(?:outlet|store|subway|restaurant)?\s+in\s+\w+',
            r'latest\s+closing\s+(?:outlet|store|subway|restaurant)?\s+in\s+\w+',
            r'earliest\s+closing\s+(?:outlet|store|subway|restaurant)?\s+in\s+\w+',
            r'(?:outlet|store|subway|restaurant)?\s+(?:that|which)\s+opens?\s+(?:the\s+)?latest\s+in\s+\w+',
            r'(?:outlet|store|subway|restaurant)?\s+(?:that|which)\s+opens?\s+(?:the\s+)?earliest\s+in\s+\w+',
            r'(?:outlet|store|subway|restaurant)?\s+(?:that|which)\s+closes?\s+(?:the\s+)?latest\s+in\s+\w+',
            r'(?:outlet|store|subway|restaurant)?\s+(?:that|which)\s+closes?\s+(?:the\s+)?earliest\s+in\s+\w+',
        ]
        for pattern in special_patterns:
            if re.search(pattern, query_lower):
                is_special_format = True
                break
    
    # 如果匹配特殊格式或同时包含时间关键词和位置，使用特殊时间处理器
    if (is_special_format or (has_special_time_keyword and has_time_action and location)):
        print(f"检测到特殊时间+位置查询: '{user_query}'")
        print(f"位置: {location}")
        return await handle_special_time_in_location(request)
    
    # 检查是否为复合查询（位置+时间）
    if ai_result.get("action") == "compound_query":
        print("检测到复合查询（位置+时间），转发到复合查询处理器")
        return await handle_compound_query(request)
    
    # 处理其他类型的查询...
    # 提取时间模式 - 用于后续处理
    time_pattern = r'(before|after)\s+(\d+(?::\d+)?(?:\s*[ap]m)?)'
    time_match = re.search(time_pattern, user_query.lower())
    
    # 提取可能的位置信息
    location = None
    for loc in ['bangsar', 'klcc', 'pj', 'petaling', 'subang', 'sunway', 'damansara', 'kl', 'shah alam']:
        if loc in user_query.lower():
            location = loc
            break
    
    # 检查是否是关于开门时间的查询
    is_opening_query = (
        "open before" in user_query.lower() or 
        "opens before" in user_query.lower() or
        "which subway outlets open before" in user_query.lower() or 
        # 检查位置相关开门查询，例如 "which sunway outlets open before 10 pm"
        (location and "open" in user_query.lower() and "before" in user_query.lower() and time_match)
    )
    
    # 检查是否是"open after"或"still open after"查询 - 这类查询应该使用find_outlets_by_time
    is_still_open_query = (
        "still open after" in user_query.lower() or 
        "still open before" in user_query.lower() or
        "are still open after" in user_query.lower() or
        "are still open before" in user_query.lower() or
        "still open" in user_query.lower() and "after" in user_query.lower() or
        "still open" in user_query.lower() and "before" in user_query.lower() or
        # 将 "open after" 也视为 "still open after"
        "open after" in user_query.lower() or
        "opens after" in user_query.lower() or
        "which subway outlets open after" in user_query.lower() or
        # 检查位置相关后时间开门查询
        (location and "open" in user_query.lower() and "after" in user_query.lower() and time_match)
    )
    
    # 检查是否是关于关门时间的查询
    is_closing_query = (
        "close before" in user_query.lower() or 
        "close after" in user_query.lower() or 
        "closes before" in user_query.lower() or 
        "closes after" in user_query.lower() or
        "which subway outlets close before" in user_query.lower() or 
        "which subway outlets close after" in user_query.lower() or
        # 检查位置相关关门查询
        (location and "close" in user_query.lower() and time_match)
    )
    
    # 处理"仍然营业"查询（使用find_outlets_by_time函数）
    if is_still_open_query and time_match:
        print("处理'仍然营业'查询")
        full_time_query = time_match.group(0)  # 包含 "after 10pm" 这样的完整短语
        time_direction = time_match.group(1)  # "before" 或 "after"
        time_value = time_match.group(2)  # "10pm" 或 "9:30pm" 等
        
        print(f"解析时间查询: 方向={time_direction}, 时间值={time_value}, 完整查询={full_time_query}")
        
        # 检查是否指定了周末或工作日
        is_weekend = "weekend" in user_query.lower() or "周末" in user_query.lower()
        is_weekday = "weekday" in user_query.lower() or "工作日" in user_query.lower() or "weekdays" in user_query.lower()
        
        if not is_weekend and not is_weekday:
            today = datetime.now().weekday()
            is_weekend = today >= 5
            
        print(f"查询的日期类型: {'周末' if is_weekend else '工作日'}")
        
        if location:
            print(f"查询在{location}地区{full_time_query}营业的门店")
            # 特定地区在特定时间仍然营业的门店
            outlets = find_outlets_compound(location, full_time_query)
        else:
            print(f"查询{full_time_query}仍然营业的门店")
            # 所有在特定时间仍然营业的门店
            outlets = find_outlets_by_time(full_time_query, is_weekend=is_weekend)
            
        print(f"查询结果: 找到 {len(outlets) if outlets else 0} 家符合条件的店铺")
            
        day_type = "weekend" if is_weekend else "weekday"
        if not outlets:
            location_str = f" in {location}" if location else ""
            return {"answer": f"I couldn't find any Subway outlets that are still open {full_time_query}{location_str} on {day_type}.", "related_ids": [], "center": []}
        
        count = len(outlets)
        ids = [o['id'] for o in outlets]
        
        # 计算地图中心点
        try:
            valid_outlets = [o for o in outlets if o.get('latitude') and o.get('longitude')]
            if valid_outlets:
                avg_lat = sum(float(o['latitude']) for o in valid_outlets) / len(valid_outlets)
                avg_lng = sum(float(o['longitude']) for o in valid_outlets) / len(valid_outlets)
                center = [avg_lat, avg_lng]
            else:
                center = []
        except Exception as e:
            print(f"计算地图中心点时出错: {e}")
            center = []
            
        location_str = f" in {location}" if location else ""
        
        # 输出前几家店铺的名称，作为调试信息
        if outlets and len(outlets) > 0:
            sample_names = [o['name'] for o in outlets[:min(5, len(outlets))]]
            print(f"示例店铺: {', '.join(sample_names)}")
        
        return {
            "answer": f"I found {count} Subway outlets that are still open {full_time_query}{location_str} on {day_type}.",
            "related_ids": ids,
            "center": center
        }
    # 处理开门时间查询
    elif is_opening_query:
        print("处理特定时间开店查询")
        full_time_query = time_match.group(0)  # 包含 "before 9am" 这样的完整短语
        time_direction = time_match.group(1)  # "before" 或 "after"
        time_value = time_match.group(2)  # "9am" 或 "9:30am" 等
        
        print(f"解析时间查询: 方向={time_direction}, 时间值={time_value}, 完整查询={full_time_query}")
        
        # 检查是否指定了周末或工作日
        is_weekend = "weekend" in user_query.lower() or "周末" in user_query.lower()
        is_weekday = "weekday" in user_query.lower() or "工作日" in user_query.lower() or "weekdays" in user_query.lower()
        
        if not is_weekend and not is_weekday:
            today = datetime.now().weekday()
            is_weekend = today >= 5
            
        print(f"查询的日期类型: {'周末' if is_weekend else '工作日'}")
        
        if location:
            print(f"查询在{location}地区{full_time_query}开店的门店")
            # 特定地区特定时间开店的门店
            outlets = find_outlets_compound(location, full_time_query)
        else:
            print(f"查询{full_time_query}开店的门店")
            # 所有特定时间开店的门店
            outlets = find_outlets_by_opening_time(full_time_query, is_weekend)
            
        print(f"查询结果: 找到 {len(outlets) if outlets else 0} 家符合条件的店铺")
            
        day_type = "weekend" if is_weekend else "weekday"
        if not outlets:
            location_str = f" in {location}" if location else ""
            return {"answer": f"I couldn't find any Subway outlets that open {full_time_query}{location_str} on {day_type}.", "related_ids": [], "center": []}
        
        count = len(outlets)
        ids = [o['id'] for o in outlets]
        
        # 计算地图中心点
        try:
            valid_outlets = [o for o in outlets if o.get('latitude') and o.get('longitude')]
            if valid_outlets:
                avg_lat = sum(float(o['latitude']) for o in valid_outlets) / len(valid_outlets)
                avg_lng = sum(float(o['longitude']) for o in valid_outlets) / len(valid_outlets)
                center = [avg_lat, avg_lng]
            else:
                center = []
        except Exception as e:
            print(f"计算地图中心点时出错: {e}")
            center = []
            
        location_str = f" in {location}" if location else ""
        
        # 输出前几家店铺的名称，作为调试信息
        if outlets and len(outlets) > 0:
            sample_names = [o['name'] for o in outlets[:min(5, len(outlets))]]
            print(f"示例店铺: {', '.join(sample_names)}")
        
        return {
            "answer": f"I found {count} Subway outlets that open {full_time_query}{location_str} on {day_type}.",
            "related_ids": ids,
            "center": center
        }
    
    # 处理关门时间查询
    elif is_closing_query and time_match:
        print("处理特定时间关店查询")
        full_time_query = time_match.group(0)  # 包含 "after 9pm" 这样的完整短语
        time_direction = time_match.group(1)  # "before" 或 "after"
        time_value = time_match.group(2)  # "9pm" 或 "9:30am" 等
        
        print(f"解析时间查询: 方向={time_direction}, 时间值={time_value}, 完整查询={full_time_query}")
        
        # 检查是否指定了周末或工作日
        is_weekend = "weekend" in user_query.lower() or "周末" in user_query.lower()
        is_weekday = "weekday" in user_query.lower() or "工作日" in user_query.lower() or "weekdays" in user_query.lower()
        
        if not is_weekend and not is_weekday:
            today = datetime.now().weekday()
            is_weekend = today >= 5
            
        print(f"查询的日期类型: {'周末' if is_weekend else '工作日'}")
        
        if location:
            print(f"查询在{location}地区{full_time_query}关店的门店")
            # 特定地区特定时间关店的门店
            outlets = find_outlets_compound(location, full_time_query)
        else:
            print(f"查询{full_time_query}关店的门店")
            # 所有特定时间关店的门店
            outlets = find_outlets_by_closing_time(full_time_query, is_weekend)
            
        print(f"查询结果: 找到 {len(outlets) if outlets else 0} 家符合条件的店铺")
            
        day_type = "weekend" if is_weekend else "weekday"
        if not outlets:
            location_str = f" in {location}" if location else ""
            return {"answer": f"I couldn't find any Subway outlets that close {full_time_query}{location_str} on {day_type}.", "related_ids": [], "center": []}
        
        count = len(outlets)
        ids = [o['id'] for o in outlets]
        
        # 计算地图中心点
        try:
            valid_outlets = [o for o in outlets if o.get('latitude') and o.get('longitude')]
            if valid_outlets:
                avg_lat = sum(float(o['latitude']) for o in valid_outlets) / len(valid_outlets)
                avg_lng = sum(float(o['longitude']) for o in valid_outlets) / len(valid_outlets)
                center = [avg_lat, avg_lng]
            else:
                center = []
        except Exception as e:
            print(f"计算地图中心点时出错: {e}")
            center = []
            
        location_str = f" in {location}" if location else ""
        
        # 输出前几家店铺的名称，作为调试信息
        if outlets and len(outlets) > 0:
            sample_names = [o['name'] for o in outlets[:min(5, len(outlets))]]
            print(f"示例店铺: {', '.join(sample_names)}")
        
        return {
            "answer": f"I found {count} Subway outlets that close {full_time_query}{location_str} on {day_type}.",
            "related_ids": ids,
            "center": center
        }
    
    # 处理特殊查询 - "closes earliest"或"earliest closing"
    if "close earliest" in user_query.lower() or "earliest closing" in user_query.lower() or "close the earliest" in user_query.lower():
        print("处理'最早关门'查询")
        try:
            # 检查是否指定了周末
            is_weekend = "weekend" in user_query.lower() or "周末" in user_query.lower()
            # 检查是否指定了工作日
            is_weekday = "weekday" in user_query.lower() or "工作日" in user_query.lower() or "weekdays" in user_query.lower()
            
            # 如果没有明确指定，根据当前日期判断
            if not is_weekend and not is_weekday:
                today = datetime.now().weekday()
                is_weekend = today >= 5  # 5和6分别是周六和周日
            
            outlets, earliest_close_time = find_earliest_closing_outlets(is_weekend)
            if outlets and outlets[0]:
                earliest_outlet = outlets[0]
                hour = int(earliest_close_time[:2])
                minute = int(earliest_close_time[2:]) if len(earliest_close_time) >= 4 else 0
                time_str = f"{hour:02d}:{minute:02d}"
                day_type = "weekend" if is_weekend else "weekday"
                
                outlet_count = len(outlets)
                if outlet_count > 1:
                    # 对名称进行去重
                    unique_names = []
                    seen_names = set()
                    for o in outlets:
                        if o['name'] not in seen_names:
                            unique_names.append(o['name'])
                            seen_names.add(o['name'])
                    
                    # 显示前三个不重复的名称
                    outlet_names = ", ".join(unique_names[:3])
                    if len(unique_names) > 3:
                        outlet_names += f" and {len(unique_names)-3} other outlets"
                
                    return {
                        "answer": f"{outlet_count} outlets close earliest on {day_type} at {time_str}. Including: {outlet_names}",
                        "related_ids": [o['id'] for o in outlets],
                        "center": [float(outlets[0]['latitude']), float(outlets[0]['longitude'])]
                    }
                else:
                    return {
                        "answer": f"{earliest_outlet['name']} closes earliest on {day_type} at {time_str}.",
                        "related_ids": [earliest_outlet['id']],
                        "center": [float(earliest_outlet['latitude']), float(earliest_outlet['longitude'])]
                    }
            else:
                return {
                    "answer": "I couldn't find information about the earliest closing outlets.",
                    "related_ids": [],
                    "center": []
                }

            # 下方這段原始程式中的第二個 else，會導致語法錯誤，故以註釋方式保留：
            # else:
            #     return {"answer": "I couldn't find information about the earliest closing outlets.", "related_ids": [], "center": []}

        except Exception as e:
            print(f"查找最早关门店铺时出错: {e}")
            return {"answer": "Sorry, there was a problem finding the earliest closing outlets.", "related_ids": [], "center": []}
    
    # 处理特殊查询 - "opens earliest"或"earliest opening"
    elif "open earliest" in user_query.lower() or "earliest opening" in user_query.lower() or "open the earliest" in user_query.lower():
        print("处理'最早开门'查询")
        # 检查是否指定了周末
        is_weekend = "weekend" in user_query.lower() or "周末" in user_query.lower()
        # 检查是否指定了工作日
        is_weekday = "weekday" in user_query.lower() or "工作日" in user_query.lower() or "weekdays" in user_query.lower()
        
        # 如果没有明确指定，根据当前日期判断
        if not is_weekend and not is_weekday:
            today = datetime.now().weekday()
            is_weekend = today >= 5  # 5和6分别是周六和周日
        
        outlets, time = find_earliest_opening_outlets(is_weekend)
        if not outlets:
            return {"answer": "I couldn't find information about the earliest opening outlets.", "related_ids": [], "center": []}
        
        hour = int(time[:2])
        minute = int(time[2:]) if len(time) >= 4 else 0
        time_str = f"{hour:02d}:{minute:02d}"
        day_type = "weekend" if is_weekend else "weekday"
        
        outlet_count = len(outlets)
        if outlet_count > 1:
            # 对名称进行去重
            unique_names = []
            seen_names = set()
            for o in outlets:
                if o['name'] not in seen_names:
                    unique_names.append(o['name'])
                    seen_names.add(o['name'])
            
            # 显示前三个不重复的名称
            outlet_names = ", ".join(unique_names[:3])
            if len(unique_names) > 3:
                outlet_names += f" and {len(unique_names)-3} other outlets"
            
            return {
                "answer": f"{outlet_count} outlets open earliest on {day_type} at {time_str}. Including: {outlet_names}",
                "related_ids": [o['id'] for o in outlets],
                "center": [float(outlets[0]['latitude']), float(outlets[0]['longitude'])]
            }
        else:
            outlet = outlets[0]
            return {
                "answer": f"{outlet['name']} opens earliest on {day_type} at {time_str}.",
                "related_ids": [outlet['id']],
                "center": [float(outlet['latitude']), float(outlet['longitude'])]
            }
    
    # 处理特殊查询 - "closes latest"或"latest closing"
    elif "close latest" in user_query.lower() or "latest closing" in user_query.lower() or "close the latest" in user_query.lower():
        print("处理'最晚关门'查询")
        # 检查是否指定了周末
        is_weekend = "weekend" in user_query.lower() or "周末" in user_query.lower()
        # 检查是否指定了工作日
        is_weekday = "weekday" in user_query.lower() or "工作日" in user_query.lower() or "weekdays" in user_query.lower()
        
        # 如果没有明确指定，根据当前日期判断
        if not is_weekend and not is_weekday:
            today = datetime.now().weekday()
            is_weekend = today >= 5  # 5和6分别是周六和周日
        
        outlets, time = find_latest_closing_outlets(is_weekend)
        if not outlets:
            return {"answer": "I couldn't find information about the latest closing outlets.", "related_ids": [], "center": []}
        
        hour = int(time[:2])
        if hour >= 24:
            hour -= 24
        minute = int(time[2:]) if len(time) >= 4 else 0
        time_str = f"{hour:02d}:{minute:02d}"
        day_type = "weekend" if is_weekend else "weekday"
        
        outlet_count = len(outlets)
        if outlet_count > 1:
            # 对名称进行去重
            unique_names = []
            seen_names = set()
            for o in outlets:
                if o['name'] not in seen_names:
                    unique_names.append(o['name'])
                    seen_names.add(o['name'])
            
            # 显示前三个不重复的名称
            outlet_names = ", ".join(unique_names[:3])
            if len(unique_names) > 3:
                outlet_names += f" and {len(unique_names)-3} other outlets"
            
            return {
                "answer": f"{outlet_count} outlets close latest on {day_type} at {time_str}. Including: {outlet_names}",
                "related_ids": [o['id'] for o in outlets],
                "center": [float(outlets[0]['latitude']), float(outlets[0]['longitude'])]
            }
        else:
            outlet = outlets[0]
            return {
                "answer": f"{outlet['name']} closes latest on {day_type} at {time_str}.",
                "related_ids": [outlet['id']],
                "center": [float(outlet['latitude']), float(outlet['longitude'])]
            }
    
    # 添加新的查询类型 - "opens latest"或"latest opening"
    elif "open latest" in user_query.lower() or "latest opening" in user_query.lower() or "open the latest" in user_query.lower():
        print("处理'最晚开门'查询")
        # 检查是否指定了周末
        is_weekend = "weekend" in user_query.lower() or "周末" in user_query.lower()
        # 检查是否指定了工作日
        is_weekday = "weekday" in user_query.lower() or "工作日" in user_query.lower() or "weekdays" in user_query.lower()
        
        # 如果没有明确指定，根据当前日期判断
        if not is_weekend and not is_weekday:
            today = datetime.now().weekday()
            is_weekend = today >= 5  # 5和6分别是周六和周日
        
        outlets, time = find_latest_opening_outlets(is_weekend)
        if not outlets:
            return {"answer": "I couldn't find information about the latest opening outlets.", "related_ids": [], "center": []}
        
        hour = int(time[:2])
        minute = int(time[2:]) if len(time) >= 4 else 0
        time_str = f"{hour:02d}:{minute:02d}"
        day_type = "weekend" if is_weekend else "weekday"
        
        outlet_count = len(outlets)
        if outlet_count > 1:
            # 对名称进行去重
            unique_names = []
            seen_names = set()
            for o in outlets:
                if o['name'] not in seen_names:
                    unique_names.append(o['name'])
                    seen_names.add(o['name'])
            
            # 显示前三个不重复的名称
            outlet_names = ", ".join(unique_names[:3])
            if len(unique_names) > 3:
                outlet_names += f" and {len(unique_names)-3} other outlets"
            
            return {
                "answer": f"{outlet_count} outlets open latest on {day_type} at {time_str}. Including: {outlet_names}",
                "related_ids": [o['id'] for o in outlets],
                "center": [float(outlets[0]['latitude']), float(outlets[0]['longitude'])]
            }
        else:
            outlet = outlets[0]
            return {
                "answer": f"{outlet['name']} opens latest on {day_type} at {time_str}.",
                "related_ids": [outlet['id']],
                "center": [float(outlet['latitude']), float(outlet['longitude'])]
            }
    
    # 使用AI处理查询
    ai_result = process_with_ai(user_query)
    print(f"AI结果: {ai_result}")
    
    # 根据AI返回的action类型执行不同操作
    action = ai_result.get("action", "")
    
    if action == "search_location":
        location = ai_result.get("location", "")
        if not location:
            return {"answer": "I need a location to search for outlets.", "related_ids": [], "center": []}
        
        outlets = find_outlets_by_location(location)
        if not outlets:
            return {"answer": f"I couldn't find any Subway outlets in {location}.", "related_ids": [], "center": []}
        
        count = len(outlets)
        ids = [o['id'] for o in outlets]
        
        # 计算地图中心点
        try:
            avg_lat = sum(float(o['latitude']) for o in outlets if o.get('latitude'))/count
            avg_lng = sum(float(o['longitude']) for o in outlets if o.get('longitude'))/count
            center = [avg_lat, avg_lng]
        except:
            center = []
        
        return {
            "answer": f"I found {count} Subway outlets in or near {location.title()}.",
            "related_ids": ids,
            "center": center
        }
    
    elif action == "get_nearest":
        location = ai_result.get("location", "")
        
        # 如果用户提供了位置，优先使用用户位置
        if user_lat and user_lon:
            nearest_outlets = get_nearest_outlets(user_lat, user_lon)
            if not nearest_outlets:
                return {"answer": "I couldn't find any nearby Subway outlets.", "related_ids": [], "center": []}
            
            ids = [o['id'] for o in nearest_outlets]
            distances = [f"{o['name']}: {o['distance']:.1f}km" for o in nearest_outlets]
            
            answer = f"Here are the nearest Subway outlets to your location:\n" + "\n".join(distances[:3])
            return {"answer": answer, "related_ids": ids, "center": [user_lat, user_lon]}
        
        # 否则使用地点名查询
        elif location:
            loc_lat, loc_lon = get_location_coordinates(location)
            if not loc_lat or not loc_lon:
                return {"answer": f"I couldn't determine the location of {location}.", "related_ids": [], "center": []}
            
            nearest_outlets = get_nearest_outlets(loc_lat, loc_lon)
            if not nearest_outlets:
                return {"answer": f"I couldn't find any Subway outlets near {location}.", "related_ids": [], "center": []}
            
            ids = [o['id'] for o in nearest_outlets]
            return {"answer": ai_result.get("answer"), "related_ids": ids, "center": [loc_lat, loc_lon]}
        
        return {"answer": "I need a location to find nearby outlets.", "related_ids": [], "center": []}
    
    elif action == "opening_time_query":
        # 处理特定开店时间查询
        time_query = ai_result.get("time", "")
        location = ai_result.get("location", "")
        
        if not time_query:
            return {"answer": "I need a time to search for outlets.", "related_ids": [], "center": []}
            
        # 检查是否指定了周末或工作日
        is_weekend = "weekend" in user_query.lower() or "周末" in user_query.lower()
        is_weekday = "weekday" in user_query.lower() or "工作日" in user_query.lower() or "weekdays" in user_query.lower()
        
        if not is_weekend and not is_weekday:
            today = datetime.now().weekday()
            is_weekend = today >= 5
            
        if location:
            # 特定地区特定时间开店的门店
            outlets = find_outlets_compound(location, time_query)
        else:
            # 所有特定时间开店的门店
            outlets = find_outlets_by_opening_time(time_query, is_weekend)
            
        day_type = "weekend" if is_weekend else "weekday"
        if not outlets:
            location_str = f" in {location}" if location else ""
            return {"answer": f"I couldn't find any Subway outlets that open {time_query}{location_str} on {day_type}.", "related_ids": [], "center": []}
        
        count = len(outlets)
        ids = [o['id'] for o in outlets]
        
        # 计算地图中心点
        try:
            avg_lat = sum(float(o['latitude']) for o in outlets if o.get('latitude'))/count
            avg_lng = sum(float(o['longitude']) for o in outlets if o.get('longitude'))/count
            center = [avg_lat, avg_lng]
        except:
            center = []
            
        location_str = f" in {location}" if location else ""
        return {
            "answer": f"I found {count} Subway outlets that open {time_query}{location_str} on {day_type}.",
            "related_ids": ids,
            "center": center
        }
        
    elif action == "closing_time_query":
        # 处理特定关店时间查询
        time_query = ai_result.get("time", "")
        location = ai_result.get("location", "")
        
        if not time_query:
            return {"answer": "I need a time to search for outlets.", "related_ids": [], "center": []}
            
        # 检查是否指定了周末或工作日
        is_weekend = "weekend" in user_query.lower() or "周末" in user_query.lower()
        is_weekday = "weekday" in user_query.lower() or "工作日" in user_query.lower() or "weekdays" in user_query.lower()
        
        if not is_weekend and not is_weekday:
            today = datetime.now().weekday()
            is_weekend = today >= 5
            
        if location:
            # 特定地区特定时间关店的门店
            outlets = find_outlets_compound(location, time_query)
        else:
            # 所有特定时间关店的门店
            outlets = find_outlets_by_closing_time(time_query, is_weekend)
            
        day_type = "weekend" if is_weekend else "weekday"
        if not outlets:
            location_str = f" in {location}" if location else ""
            return {"answer": f"I couldn't find any Subway outlets that close {time_query}{location_str} on {day_type}.", "related_ids": [], "center": []}
        
        count = len(outlets)
        ids = [o['id'] for o in outlets]
        
        # 计算地图中心点
        try:
            avg_lat = sum(float(o['latitude']) for o in outlets if o.get('latitude'))/count
            avg_lng = sum(float(o['longitude']) for o in outlets if o.get('longitude'))/count
            center = [avg_lat, avg_lng]
        except:
            center = []
            
        location_str = f" in {location}" if location else ""
        return {
            "answer": f"I found {count} Subway outlets that close {time_query}{location_str} on {day_type}.",
            "related_ids": ids,
            "center": center
        }
    
    elif action == "time_query":
        location = ai_result.get("location", "")
        time = ai_result.get("time", "")
        
        if not location or not time:
            return {"answer": "I need both a location and time to search.", "related_ids": [], "center": []}
        
        outlets = find_outlets_compound(location, time)
        if not outlets:
            return {"answer": f"I couldn't find any Subway outlets in {location} open at {time}.", "related_ids": [], "center": []}
        
        count = len(outlets)
        ids = [o['id'] for o in outlets]
        
        # 计算地图中心点
        try:
            avg_lat = sum(float(o['latitude']) for o in outlets if o.get('latitude'))/count
            avg_lng = sum(float(o['longitude']) for o in outlets if o.get('longitude'))/count
            center = [avg_lat, avg_lng]
        except:
            center = []
            
        return {
            "answer": f"I found {count} Subway outlets in {location} open at {time}.",
            "related_ids": ids,
            "center": center
        }
    
    elif action == "get_attribute":
        attribute = ai_result.get("attribute", "")
        
        if attribute == "latest_closing":
            # 检查是否指定了周末或工作日
            is_weekend = "weekend" in user_query.lower() or "周末" in user_query.lower()
            is_weekday = "weekday" in user_query.lower() or "工作日" in user_query.lower() or "weekdays" in user_query.lower()
            
            # 如果没有明确指定，根据当前日期判断
            if not is_weekend and not is_weekday:
                today = datetime.now().weekday()
                is_weekend = today >= 5  # 5和6分别是周六和周日
                
            outlets, time = find_latest_closing_outlets(is_weekend)
            if not outlets:
                return {"answer": "I couldn't determine which outlet closes the latest.", "related_ids": [], "center": []}
            
            hour = int(time[:2])
            if hour >= 24:
                hour -= 24
            minute = int(time[2:]) if len(time) >= 4 else 0
            time_str = f"{hour:02d}:{minute:02d}"
            
            day_type = "weekend" if is_weekend else "weekday"
            
            outlet_count = len(outlets)
            if outlet_count > 1:
                # 对名称进行去重
                unique_names = []
                seen_names = set()
                for o in outlets:
                    if o['name'] not in seen_names:
                        unique_names.append(o['name'])
                        seen_names.add(o['name'])
                
                # 显示前三个不重复的名称
                outlet_names = ", ".join(unique_names[:3])
                if len(unique_names) > 3:
                    outlet_names += f" and {len(unique_names)-3} other outlets"
                
                return {
                    "answer": f"{outlet_count} outlets close latest on {day_type} at {time_str}. Including: {outlet_names}",
                    "related_ids": [o['id'] for o in outlets],
                    "center": [float(outlets[0]['latitude']), float(outlets[0]['longitude'])]
                }
            else:
                outlet = outlets[0]
                return {
                    "answer": f"{outlet['name']} closes latest on {day_type} at {time_str}.",
                    "related_ids": [outlet['id']],
                    "center": [float(outlet['latitude']), float(outlet['longitude'])]
                }
        
        elif attribute == "earliest_closing":
            # 检查是否指定了周末或工作日
            is_weekend = "weekend" in user_query.lower() or "周末" in user_query.lower()
            is_weekday = "weekday" in user_query.lower() or "工作日" in user_query.lower() or "weekdays" in user_query.lower()
            
            # 如果没有明确指定，根据当前日期判断
            if not is_weekend and not is_weekday:
                today = datetime.now().weekday()
                is_weekend = today >= 5  # 5和6分别是周六和周日
                
            outlets, time = find_earliest_closing_outlets(is_weekend)
            if not outlets or not outlets[0]:
                return {"answer": "I couldn't determine which outlet closes the earliest.", "related_ids": [], "center": []}
            
            hour = int(time[:2])
            minute = int(time[2:]) if len(time) >= 4 else 0
            time_str = f"{hour:02d}:{minute:02d}"
            
            day_type = "weekend" if is_weekend else "weekday"
            
            outlet_count = len(outlets)
            if outlet_count > 1:
                # 对名称进行去重
                unique_names = []
                seen_names = set()
                for o in outlets:
                    if o['name'] not in seen_names:
                        unique_names.append(o['name'])
                        seen_names.add(o['name'])
                
                # 显示前三个不重复的名称
                outlet_names = ", ".join(unique_names[:3])
                if len(unique_names) > 3:
                    outlet_names += f" and {len(unique_names)-3} other outlets"
                
                return {
                    "answer": f"{outlet_count} outlets close earliest on {day_type} at {time_str}. Including: {outlet_names}",
                    "related_ids": [o['id'] for o in outlets],
                    "center": [float(outlets[0]['latitude']), float(outlets[0]['longitude'])]
                }
            else:
                outlet = outlets[0]
                return {
                    "answer": f"{outlet['name']} closes earliest on {day_type} at {time_str}.",
                    "related_ids": [outlet['id']],
                    "center": [float(outlet['latitude']), float(outlet['longitude'])]
                }

        elif attribute == "latest_opening":
            # 检查是否指定了周末或工作日
            is_weekend = "weekend" in user_query.lower() or "周末" in user_query.lower()
            is_weekday = "weekday" in user_query.lower() or "工作日" in user_query.lower() or "weekdays" in user_query.lower()
            
            # 如果没有明确指定，根据当前日期判断
            if not is_weekend and not is_weekday:
                today = datetime.now().weekday()
                is_weekend = today >= 5  # 5和6分别是周六和周日
                
            outlets, time = find_latest_opening_outlets(is_weekend)
            if not outlets:
                return {"answer": "I couldn't determine which outlet opens the latest.", "related_ids": [], "center": []}
            
            hour = int(time[:2])
            minute = int(time[2:]) if len(time) >= 4 else 0
            time_str = f"{hour:02d}:{minute:02d}"
            
            day_type = "weekend" if is_weekend else "weekday"
            
            outlet_count = len(outlets)
            if outlet_count > 1:
                # 对名称进行去重
                unique_names = []
                seen_names = set()
                for o in outlets:
                    if o['name'] not in seen_names:
                        unique_names.append(o['name'])
                        seen_names.add(o['name'])
                
                # 显示前三个不重复的名称
                outlet_names = ", ".join(unique_names[:3])
                if len(unique_names) > 3:
                    outlet_names += f" and {len(unique_names)-3} other outlets"
                
                return {
                    "answer": f"{outlet_count} outlets open latest on {day_type} at {time_str}. Including: {outlet_names}",
                    "related_ids": [o['id'] for o in outlets],
                    "center": [float(outlets[0]['latitude']), float(outlets[0]['longitude'])]
                }
            else:
                outlet = outlets[0]
                return {
                    "answer": f"{outlet['name']} opens latest on {day_type} at {time_str}.",
                    "related_ids": [outlet['id']],
                    "center": [float(outlet['latitude']), float(outlet['longitude'])]
                }
            
        elif attribute == "earliest_opening":
            # 检查是否指定了周末或工作日
            is_weekend = "weekend" in user_query.lower() or "周末" in user_query.lower()
            is_weekday = "weekday" in user_query.lower() or "工作日" in user_query.lower() or "weekdays" in user_query.lower()
            
            # 如果没有明确指定，根据当前日期判断
            if not is_weekend and not is_weekday:
                today = datetime.now().weekday()
                is_weekend = today >= 5  # 5和6分别是周六和周日
                
            outlets, time = find_earliest_opening_outlets(is_weekend)
            if not outlets:
                return {"answer": "I couldn't determine which outlet opens the earliest.", "related_ids": [], "center": []}
            
            hour = int(time[:2])
            minute = int(time[2:]) if len(time) >= 4 else 0
            time_str = f"{hour:02d}:{minute:02d}"
            
            day_type = "weekend" if is_weekend else "weekday"
            
            outlet_count = len(outlets)
            if outlet_count > 1:
                # 对名称进行去重
                unique_names = []
                seen_names = set()
                for o in outlets:
                    if o['name'] not in seen_names:
                        unique_names.append(o['name'])
                        seen_names.add(o['name'])
                
                # 显示前三个不重复的名称
                outlet_names = ", ".join(unique_names[:3])
                if len(unique_names) > 3:
                    outlet_names += f" and {len(unique_names)-3} other outlets"
                
                return {
                    "answer": f"{outlet_count} outlets open earliest on {day_type} at {time_str}. Including: {outlet_names}",
                    "related_ids": [o['id'] for o in outlets],
                    "center": [float(outlets[0]['latitude']), float(outlets[0]['longitude'])]
                }
            else:
                outlet = outlets[0]
                return {
                    "answer": f"{outlet['name']} opens earliest on {day_type} at {time_str}.",
                    "related_ids": [outlet['id']],
                    "center": [float(outlet['latitude']), float(outlet['longitude'])]
                }
            
        elif attribute == "24hours":
            outlets = find_24hour_outlets()
            if not outlets:
                return {"answer": "I couldn't find any 24-hour Subway outlets.", "related_ids": [], "center": []}
            
            count = len(outlets)
            ids = [o['id'] for o in outlets]
            
            # 计算地图中心点
            try:
                avg_lat = sum(float(o['latitude']) for o in outlets if o.get('latitude'))/count
                avg_lng = sum(float(o['longitude']) for o in outlets if o.get('longitude'))/count
                center = [avg_lat, avg_lng]
            except:
                center = []
                
            return {
                "answer": f"I found {count} Subway outlets that are open 24 hours.",
                "related_ids": ids,
                "center": center
            }
    
    # 默认回复
    return {
        "answer": ai_result.get("answer", "I'm not sure how to help with that query."),
        "related_ids": [],
        "center": []
    }

# === 其他API端点 ===

@app.get("/api/outlets/search")
def search_outlets(query: str, time: Optional[str] = None):
    """综合搜索API - 支持位置、时间或两者的组合"""
    outlets = []
    
    if time:
        # 复合查询
        outlets = find_outlets_compound(query, time)
    else:
        # 仅位置查询
        outlets = find_outlets_by_location(query)
    
    if not outlets:
        return {"message": f"No outlets found for query: {query}", "outlets": []}
    
    return {"message": f"Found {len(outlets)} outlets", "outlets": outlets}

@app.get("/api/outlets/open-now")
def currently_open_outlets(location: Optional[str] = None):
    """获取当前营业的店铺"""
    now = datetime.now()
    time_str = f"{now.hour:02d}:{now.minute:02d}"
    
    # 打印当前时间，帮助调试
    print(f"当前时间: {time_str}")
    
    if location:
        # 特定地区当前营业的店铺
        outlets = find_outlets_compound(location, "now")
    else:
        # 所有当前营业的店铺
        outlets = find_outlets_by_time("now")
    
    if not outlets:
        # 如果没有结果，尝试查询营业至晚上的店铺
        if now.hour < 20:  # 现在是晚上8点前
            outlets = find_outlets_by_time("20:00")  # 假设至少营业到晚上8点
            
        if not outlets and now.hour < 22:  # 现在是晚上10点前
            outlets = find_outlets_by_time("after 18")  # 假设晚上6点后开门
    
    if not outlets:
        return {"message": "No outlets currently open", "outlets": []}
    
    return {"message": f"Found {len(outlets)} open outlets", "outlets": outlets}

@app.get("/api/outlets/earliest")
def earliest_opening_outlets():
    """获取最早开门的店铺"""
    outlets, time = find_earliest_opening_outlets()
    
    if not outlets:
        return {"message": "No earliest opening information available", "outlets": []}
    
    hour = int(time[:2])
    minute = int(time[2:]) if len(time) >= 4 else 0
    time_str = f"{hour:02d}:{minute:02d}"
    
    return {
        "message": f"Outlets opening earliest at {time_str}",
        "time": time_str,
        "outlets": outlets
    }

@app.get("/api/outlets/latest")
def latest_closing_outlets():
    """获取最晚关门的店铺"""
    outlets, time = find_latest_closing_outlets()
    
    if not outlets:
        return {"message": "No latest closing information available", "outlets": []}
    
    # 处理跨午夜情况
    hour = int(time[:2])
    if hour >= 24:
        hour -= 24
    minute = int(time[2:]) if len(time) >= 4 else 0
    time_str = f"{hour:02d}:{minute:02d}"
    
    return {
        "message": f"Outlets closing latest at {time_str}",
        "time": time_str,
        "outlets": outlets
    }

@app.get("/api/outlets/24hours")
def hours24_outlets():
    """获取24小时营业的店铺"""
    outlets = find_24hour_outlets()
    
    if not outlets:
        return {"message": "No 24-hour outlets found", "outlets": []}
    
    return {
        "message": f"Found {len(outlets)} 24-hour outlets",
        "outlets": outlets
    } 

# 添加这些API端点在文件末尾

@app.get("/api/outlets/earliest-opening")
def earliest_opening_outlets_api(is_weekend: Optional[bool] = None):
    """获取最早开门的店铺
    
    参数:
        is_weekend: 是否查询周末，默认根据当前日期判断
    """
    if is_weekend is None:
        today = datetime.now().weekday()
        is_weekend = today >= 5  # 5和6分别是周六和周日
    
    outlets, time = find_earliest_opening_outlets(is_weekend)
    
    if not outlets:
        return {"message": "No earliest opening information available", "outlets": []}
    
    hour = int(time[:2])
    minute = int(time[2:]) if len(time) >= 4 else 0
    time_str = f"{hour:02d}:{minute:02d}"
    
    day_type = "weekend" if is_weekend else "weekday"
    
    return {
        "message": f"Outlets that open earliest on {day_type} at {time_str}",
        "time": time_str,
        "day_type": day_type,
        "outlets": outlets
    }

@app.get("/api/outlets/latest-opening")
def latest_opening_outlets_api(is_weekend: Optional[bool] = None):
    """获取最晚开门的店铺
    
    参数:
        is_weekend: 是否查询周末，默认根据当前日期判断
    """
    if is_weekend is None:
        today = datetime.now().weekday()
        is_weekend = today >= 5
    
    outlets, time = find_latest_opening_outlets(is_weekend)
    
    if not outlets:
        return {"message": "No latest opening information available", "outlets": []}
    
    hour = int(time[:2])
    minute = int(time[2:]) if len(time) >= 4 else 0
    time_str = f"{hour:02d}:{minute:02d}"
    
    day_type = "weekend" if is_weekend else "weekday"
    
    return {
        "message": f"Outlets that open latest on {day_type} at {time_str}",
        "time": time_str,
        "day_type": day_type,
        "outlets": outlets
    }

@app.get("/api/outlets/earliest-closing")
def earliest_closing_outlets_api(is_weekend: Optional[bool] = None):
    """获取最早关门的店铺
    
    参数:
        is_weekend: 是否查询周末，默认根据当前日期判断
    """
    if is_weekend is None:
        today = datetime.now().weekday()
        is_weekend = today >= 5
    
    outlets, time = find_earliest_closing_outlets(is_weekend)
    
    if not outlets:
        return {"message": "No earliest closing information available", "outlets": []}
    
    hour = int(time[:2])
    minute = int(time[2:]) if len(time) >= 4 else 0
    time_str = f"{hour:02d}:{minute:02d}"
    
    day_type = "weekend" if is_weekend else "weekday"
    
    return {
        "message": f"Outlets that close earliest on {day_type} at {time_str}",
        "time": time_str,
        "day_type": day_type,
        "outlets": outlets
    }

@app.get("/api/outlets/latest-closing")
def latest_closing_outlets_api(is_weekend: Optional[bool] = None):
    """获取最晚关门的店铺
    
    参数:
        is_weekend: 是否查询周末，默认根据当前日期判断
    """
    if is_weekend is None:
        today = datetime.now().weekday()
        is_weekend = today >= 5
    
    outlets, time = find_latest_closing_outlets(is_weekend)
    
    if not outlets:
        return {"message": "No latest closing information available", "outlets": []}
    
    # 处理跨午夜情况
    hour = int(time[:2])
    if hour >= 24:
        hour -= 24
    minute = int(time[2:]) if len(time) >= 4 else 0
    time_str = f"{hour:02d}:{minute:02d}"
    
    day_type = "weekend" if is_weekend else "weekday"
    
    return {
        "message": f"Outlets that close latest on {day_type} at {time_str}",
        "time": time_str,
        "day_type": day_type,
        "outlets": outlets
    }

@app.get("/api/outlets/by-opening-time")
def outlets_by_opening_time(time: str, is_weekend: Optional[bool] = None):
    """获取特定时间开门的店铺
    
    参数:
        time: 时间查询字符串，如"9am"、"before 10am"、"after 11am"等
        is_weekend: 是否查询周末，默认根据当前日期判断
    """
    if is_weekend is None:
        today = datetime.now().weekday()
        is_weekend = today >= 5
    
    outlets = find_outlets_by_opening_time(time, is_weekend)
    
    day_type = "weekend" if is_weekend else "weekday"
    
    if not outlets:
        return {"message": f"No outlets found that open {time} on {day_type}", "outlets": []}
    
    return {
        "message": f"Found {len(outlets)} outlets that open {time} on {day_type}",
        "day_type": day_type,
        "time_query": time,
        "outlets": outlets
    }

@app.get("/api/outlets/by-closing-time")
def outlets_by_closing_time(time: str, is_weekend: Optional[bool] = None):
    """获取特定时间关门的店铺
    
    参数:
        time: 时间查询字符串，如"10pm"、"before 11pm"、"after 9pm"等
        is_weekend: 是否查询周末，默认根据当前日期判断
    """
    if is_weekend is None:
        today = datetime.now().weekday()
        is_weekend = today >= 5
    
    outlets = find_outlets_by_closing_time(time, is_weekend)
    
    day_type = "weekend" if is_weekend else "weekday"
    
    if not outlets:
        return {"message": f"No outlets found that close {time} on {day_type}", "outlets": []}
    
    return {
        "message": f"Found {len(outlets)} outlets that close {time} on {day_type}",
        "day_type": day_type,
        "time_query": time,
        "outlets": outlets
    }

@app.get("/api/outlets/open-at-time")
def outlets_open_at_time(time: str, location: Optional[str] = None, is_weekend: Optional[bool] = None):
    """获取特定时间点营业的店铺
    
    参数:
        time: 时间查询字符串，如"10pm"、"9am"等
        location: 可选的位置过滤
        is_weekend: 是否查询周末，默认根据当前日期判断
    """
    if is_weekend is None:
        today = datetime.now().weekday()
        is_weekend = today >= 5
    
    if location:
        outlets = find_outlets_compound(location, time, is_weekend=is_weekend)
    else:
        outlets = find_outlets_by_time(time, is_weekend=is_weekend)
    
    day_type = "weekend" if is_weekend else "weekday"
    location_str = f" in {location}" if location else ""
    
    if not outlets:
        return {"message": f"No outlets found open at {time} on {day_type}{location_str}", "outlets": []}
    
    return {
        "message": f"Found {len(outlets)} outlets open at {time} on {day_type}{location_str}",
        "day_type": day_type,
        "time_query": time,
        "location": location,
        "outlets": outlets
    }

@app.post("/chatbot/handle_current_time")
async def handle_current_time_query(request: ChatRequest):
    """处理当前时间的查询"""
    user_query = request.query
    
    print("处理当前时间查询")
    now = datetime.now()
    
    # 检查是否指定了位置
    location = None
    for loc in ['bangsar', 'klcc', 'pj', 'petaling', 'subang', 'sunway', 'damansara', 'kl', 'shah alam']:
        if loc in user_query.lower():
            location = loc
            break
    
    # 检查是否指定了周末或工作日
    is_weekend = "weekend" in user_query.lower() or "周末" in user_query.lower()
    is_weekday = "weekday" in user_query.lower() or "工作日" in user_query.lower() or "weekdays" in user_query.lower()
    
    # 如果没有明确指定，根据当前日期判断
    if not is_weekend and not is_weekday:
        today = now.weekday()
        is_weekend = today >= 5  # 5和6分别是周六和周日
    
    if location:
        outlets = find_outlets_compound(location, "now", is_weekend=is_weekend)
    else:
        outlets = find_outlets_by_time("now", is_weekend=is_weekend)
    
    if not outlets:
        # 如果没有结果，尝试查询当天营业的店铺
        day = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"][now.weekday()]
        # 如果指定了周末或工作日，使用相应的日期
        if is_weekend:
            day = "saturday" if "saturday" in user_query.lower() else "sunday"
        elif is_weekday:
            # 默认使用周一，除非指定了特定工作日
            for weekday in ["monday", "tuesday", "wednesday", "thursday", "friday"]:
                if weekday in user_query.lower():
                    day = weekday
                    break
        
        conn = connect_db()
        try:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                cur.execute(f"""
                    SELECT * FROM subway_outlets 
                    WHERE is_24hours = true 
                    OR operating_hours ILIKE '%24 hours%'
                    OR opening_hours->'{day}' IS NOT NULL
                """)
                outlets = [dict(r) for r in cur.fetchall()]
        finally:
            conn.close()

    if outlets:
        count = len(outlets)
        ids = [o['id'] for o in outlets]
        
        # 计算地图中心点
        try:
            avg_lat = sum(float(o['latitude']) for o in outlets if o.get('latitude'))/count
            avg_lng = sum(float(o['longitude']) for o in outlets if o.get('longitude'))/count
            center = [avg_lat, avg_lng]
        except:
            center = []
            
        location_str = f" in {location}" if location else ""
        day_type = "weekend" if is_weekend else "weekday"
        return {
            "answer": f"I found {count} Subway outlets currently open on {day_type}{location_str}.",
            "related_ids": ids,
            "center": center
        }
    else:
        day_type = "weekend" if is_weekend else "weekday"
        location_str = f" in {location}" if location else ""
        return {"answer": f"I couldn't find any Subway outlets currently open on {day_type}{location_str}.", "related_ids": [], "center": []}
    
@app.post("/api/outlets/compound_search")
def compound_search_outlets(location_query: str, time_query: str, is_weekend: Optional[bool] = None):
    """Compound search - Process both location and time conditions
    
    Parameters:
        location_query: Location query string, e.g., "KL", "Petaling Jaya", etc.
        time_query: Time query string, e.g., "now", "9pm", "after 8pm", "before 9am", etc.
        is_weekend: Whether to query for weekend, defaults to determining based on current date
    """
    # Determine if it's a weekend
    if is_weekend is None:
        today = datetime.now().weekday()
        is_weekend = today >= 5  # 5 and 6 are Saturday and Sunday
    
    day_type = "weekend" if is_weekend else "weekday"
    
    # Log the query details
    print(f"Compound search - Location: '{location_query}', Time: '{time_query}', Day type: {day_type}")
    
    # Use find_outlets_compound for compound query
    outlets = find_outlets_compound(location_query, time_query, is_weekend=is_weekend)
    
    if not outlets:
        return {
            "message": f"No outlets found in {location_query} matching time condition '{time_query}' on {day_type}",
            "count": 0,
            "day_type": day_type,
            "location_query": location_query, 
            "time_query": time_query,
            "outlets": []
        }
    
    # Calculate map center for frontend
    try:
        count = len(outlets)
        avg_lat = sum(float(o['latitude']) for o in outlets if o.get('latitude'))/count
        avg_lng = sum(float(o['longitude']) for o in outlets if o.get('longitude'))/count
        center = [avg_lat, avg_lng]
    except:
        center = []
    
    return {
        "message": f"Found {len(outlets)} outlets in {location_query} matching time condition '{time_query}' on {day_type}",
        "count": len(outlets),
        "day_type": day_type,
        "location_query": location_query, 
        "time_query": time_query,
        "center": center,
        "outlets": outlets
    }

@app.post("/chatbot/compound_query")
async def handle_compound_query(request: ChatRequest):
    """Handle location+time compound queries
    
    Example queries:
    - "Stores in Sunway open after 8pm"
    - "Outlets in Bangsar that close before 9pm"
    """
    user_query = request.query
    print(f"处理复合查询: '{user_query}'")
    
    # Use process_with_ai to extract location and time information
    ai_result = process_with_ai(user_query)
    location = ai_result.get("location")
    time_condition = ai_result.get("time")
    
    print(f"AI解析结果 - 位置: '{location}', 时间条件: '{time_condition}'")
    
    # Check if both location and time were extracted
    if not location or not time_condition:
        return {
            "answer": "Sorry, I couldn't understand your query. Please provide both location and time information, like 'Stores in Bangsar open after 8pm'.",
            "related_ids": [],
            "center": []
        }
    
    # 清理时间条件中可能存在的位置信息
    location_lower = location.lower()
    if location_lower in time_condition.lower():
        # 从时间查询中移除位置信息
        time_condition = re.sub(f'(?i)(?:at|in)\s+{re.escape(location_lower)}', '', time_condition).strip()
        # 如果有多余的"at"、"in"等词，也去掉
        time_condition = re.sub(r'\s+(?:at|in)\s+$', '', time_condition).strip()
        print(f"清理后的时间条件: '{time_condition}'")
    
    # 如果只有"before X"这样的查询，并且没有明确指定关门，添加close前缀
    if "before" in time_condition.lower() and not "close" in time_condition.lower() and not "open" in time_condition.lower():
        time_condition = f"close {time_condition}"
        print(f"修正后的时间条件: '{time_condition}'")
    
    # Determine if it's a weekend query
    is_weekend = None
    if "weekend" in user_query.lower():
        is_weekend = True
    elif "weekday" in user_query.lower():
        is_weekend = False
    else:
        today = datetime.now().weekday()
        is_weekend = today >= 5  # 5 and 6 are Saturday and Sunday
    
    print(f"执行复合查询 - 位置: '{location}', 时间条件: '{time_condition}', 周末: {is_weekend}")
    
    # Find outlets matching both location and time conditions
    outlets = find_outlets_compound(location, time_condition, is_weekend=is_weekend)
    
    if not outlets:
        return {
            "answer": f"Sorry, I couldn't find any outlets in {location} that meet the time condition '{time_condition}'.",
            "related_ids": [],
            "center": []
        }
    
    # Prepare response data
    ids = [o['id'] for o in outlets]
    count = len(outlets)
    
    # Calculate map center
    try:
        avg_lat = sum(float(o['latitude']) for o in outlets if o.get('latitude'))/count
        avg_lng = sum(float(o['longitude']) for o in outlets if o.get('longitude'))/count
        center = [avg_lat, avg_lng]
    except:
        center = []
    
    # Format outlet names
    day_type = "weekend" if is_weekend else "weekday"
    
    if count > 1:
        # De-duplicate names
        unique_names = []
        seen_names = set()
        for o in outlets:
            if o['name'] not in seen_names:
                unique_names.append(o['name'])
                seen_names.add(o['name'])
        
        # Show first three unique names
        outlet_names = ", ".join(unique_names[:3])
        if len(unique_names) > 3:
            outlet_names += f" and {len(unique_names)-3} more outlets"
        
        return {
            "answer": f"I found {count} outlets in {location} that meet the time condition '{time_condition}' on {day_type}, including: {outlet_names}.",
            "related_ids": ids,
            "center": center
        }
    else:
        outlet = outlets[0]
        return {
            "answer": f"I found 1 outlet in {location} that meets the time condition '{time_condition}' on {day_type}: {outlet['name']}.",
            "related_ids": ids,
            "center": center
        }
    
@app.get("/api/outlets/special-time-in-location")
def special_time_outlets_in_location(
    location: str, 
    attribute: str, 
    is_weekend: Optional[bool] = None
):
    """查询特定地区的最早/最晚开门/关门店铺
    
    Parameters:
        location: Location query string
        attribute: One of "earliest_opening", "latest_opening", "earliest_closing", "latest_closing"
        is_weekend: Whether to query for weekend, defaults to determining based on current date
    """
    # Determine if it's a weekend
    if is_weekend is None:
        today = datetime.now().weekday()
        is_weekend = today >= 5  # 5 and 6 are Saturday and Sunday
    
    day_type = "weekend" if is_weekend else "weekday"
    
    # 先按位置筛选
    location_results = find_outlets_by_location(location)
    if not location_results:
        return {
            "message": f"No outlets found in {location}",
            "count": 0,
            "day_type": day_type,
            "location": location,
            "attribute": attribute,
            "outlets": []
        }
    
    # 获取店铺ID列表
    location_ids = [r['id'] for r in location_results]
    
    # 实现位置内的最早/最晚开关门店铺查询
    conn = connect_db()
    try:
        # 根据是否周末确定要检查的营业日
        if is_weekend:
            days = ["saturday", "sunday"]
        else:
            days = ["monday", "tuesday", "wednesday", "thursday", "friday"]
        
        # 根据属性确定时间字段和比较方式
        if attribute == "earliest_opening":
            time_field = "open"
            best_time = "2400"  # 初始值设为最大，寻找最小值
            compare_func = lambda x, y: x < y  # 比较函数：找最小值
        elif attribute == "latest_opening":
            time_field = "open"
            best_time = "0000"  # 初始值设为最小，寻找最大值
            compare_func = lambda x, y: x > y  # 比较函数：找最大值
        elif attribute == "earliest_closing":
            time_field = "close"
            best_time = "2400"  # 初始值设为最大，寻找最小值
            compare_func = lambda x, y: x < y  # 比较函数：找最小值
        elif attribute == "latest_closing":
            time_field = "close"
            best_time = "0000"  # 初始值设为最小，寻找最大值
            compare_func = lambda x, y: x > y  # 比较函数：找最大值
        else:
            return {
                "message": f"Invalid attribute: {attribute}. Must be one of 'earliest_opening', 'latest_opening', 'earliest_closing', 'latest_closing'",
                "count": 0,
                "outlets": []
            }
        
        # 查询位置内的所有店铺
        with conn.cursor(cursor_factory=DictCursor) as cur:
            location_ids_str = ", ".join([f"'{id}'" for id in location_ids])
            cur.execute(f"""
                SELECT * FROM subway_outlets
                WHERE id IN ({location_ids_str})
                AND opening_hours IS NOT NULL
            """)
            
            rows = cur.fetchall()
            if not rows:
                return {
                    "message": f"No outlets with opening hours found in {location}",
                    "count": 0,
                    "day_type": day_type,
                    "location": location,
                    "attribute": attribute,
                    "outlets": []
                }
            
            print(f"从 {location} 区域找到 {len(rows)} 家有营业时间的店铺")
            
            # 用于跟踪最佳结果
            best_outlets = []
            outlet_ids = set()
            
            # 遍历店铺找出符合条件的
            for row in rows:
                row_dict = dict(row)
                hours = row_dict.get('opening_hours', {})
                
                # 跳过没有营业时间的店铺
                if not hours:
                    continue
                
                # 遍历指定的营业日
                for day in days:
                    if day not in hours or not hours[day] or time_field not in hours[day]:
                        continue
                    
                    time_value = hours[day][time_field]
                    if not time_value:
                        continue
                    
                    # 特殊处理"latest_closing"，考虑跨午夜情况
                    if attribute == "latest_closing" and 'open' in hours[day] and hours[day]['open'] > time_value:
                        # 跨午夜情况：关门时间在次日，需要加24小时
                        hour = int(time_value[:2]) + 24
                        minute = int(time_value[2:]) if len(time_value) >= 4 else 0
                        time_value = f"{hour:02d}{minute:02d}"
                    
                    # 跳过earliest_closing的跨午夜情况
                    if attribute == "earliest_closing" and 'open' in hours[day] and hours[day]['open'] > time_value:
                        continue
                    
                    print(f"店铺 {row_dict['name']} 在 {day} 的 {time_field} 时间: {time_value}")
                    
                    # 检查是否是最佳结果
                    if compare_func(time_value, best_time):
                        best_time = time_value
                        best_outlets = [row_dict]
                        outlet_ids = {row_dict['id']}
                    elif time_value == best_time and row_dict['id'] not in outlet_ids:
                        best_outlets.append(row_dict)
                        outlet_ids.add(row_dict['id'])
            
            if not best_outlets:
                attribute_display = {
                    "earliest_opening": "open earliest",
                    "latest_opening": "open latest",
                    "earliest_closing": "close earliest",
                    "latest_closing": "close latest"
                }.get(attribute, attribute)
                
                return {
                    "message": f"No outlets found in {location} that {attribute_display} on {day_type}",
                    "count": 0,
                    "day_type": day_type,
                    "location": location,
                    "attribute": attribute,
                    "outlets": []
                }
            
            # 格式化时间显示
            if best_time:
                hour = int(best_time[:2])
                # 处理跨午夜情况
                if attribute == "latest_closing" and hour >= 24:
                    hour -= 24
                minute = int(best_time[2:]) if len(best_time) >= 4 else 0
                formatted_time = f"{hour:02d}:{minute:02d}"
            else:
                formatted_time = "N/A"
            
            # 准备返回数据
            outlets = best_outlets
            
            # 计算地图中心
            try:
                count = len(outlets)
                avg_lat = sum(float(o['latitude']) for o in outlets if o.get('latitude'))/count
                avg_lng = sum(float(o['longitude']) for o in outlets if o.get('longitude'))/count
                center = [avg_lat, avg_lng]
            except:
                center = []
            
            attribute_display = {
                "earliest_opening": "open earliest",
                "latest_opening": "open latest",
                "earliest_closing": "close earliest",
                "latest_closing": "close latest"
            }.get(attribute, attribute)
            
            return {
                "message": f"Found {len(outlets)} outlets in {location} that {attribute_display} at {formatted_time} on {day_type}",
                "count": len(outlets),
                "day_type": day_type,
                "location": location,
                "attribute": attribute,
                "time": formatted_time,
                "center": center,
                "outlets": outlets
            }
    except Exception as e:
        print(f"Error in special_time_outlets_in_location: {e}")
        return {
            "message": f"Error processing request: {str(e)}",
            "count": 0,
            "day_type": day_type,
            "location": location,
            "attribute": attribute,
            "outlets": []
        }
    finally:
        conn.close()
    
@app.post("/chatbot/special_time_in_location")
async def handle_special_time_in_location(request: ChatRequest):
    """Handle queries for earliest/latest opening/closing outlets in a specific location
    
    Example queries:
    - "Earliest closing store in Subang"
    - "Latest opening outlet in Bangsar"
    - "Which store opens earliest in KLCC"
    """
    user_query = request.query
    print(f"处理特殊时间+位置查询: '{user_query}'")
    
    # Use process_with_ai to extract location and attribute information
    ai_result = process_with_ai(user_query)
    location = ai_result.get("location")
    
    print(f"AI解析结果 - 位置: '{location}'")
    
    # 如果AI未提取到位置，尝试从查询中直接提取
    if not location:
        print("⚠️ AI未能识别位置，尝试直接从查询中提取位置")
        query_lower = user_query.lower()
        conn = connect_db()
        try:
            with conn.cursor(cursor_factory=DictCursor) as cur:
                # 获取所有已知地区
                cur.execute("""
                    SELECT DISTINCT city 
                    FROM subway_outlets 
                    WHERE city IS NOT NULL AND city != ''
                """)
                all_areas = [row[0] for row in cur.fetchall() if row[0]]
                
                # 检查查询中是否包含这些地区
                for area in all_areas:
                    # 使用完整单词匹配，避免数字或部分字符被错误匹配为位置
                    if re.search(r'\b' + re.escape(area.lower()) + r'\b', query_lower):
                        location = area
                        print(f"从查询中提取到位置: {location}")
                        break
        finally:
            conn.close()
    
    # 检查是否能提取到位置信息
    if not location:
        print("⚠️ 无法确定查询位置")
        return {
            "answer": "Sorry, I couldn't determine the location you're asking about. Please specify a location like 'Subang' or 'KLCC'.",
            "related_ids": [],
            "center": []
        }
    
    # 根据查询确定时间属性
    query_lower = user_query.lower()
    
    # 判断查询类型
    is_opening_query = any(kw in query_lower for kw in ["open", "opens", "opening"])
    is_closing_query = any(kw in query_lower for kw in ["close", "closes", "closing"])
    is_earliest_query = any(kw in query_lower for kw in ["earliest", "first", "soonest"])
    is_latest_query = any(kw in query_lower for kw in ["latest", "last"])
    
    print(f"查询分析 - 开门查询: {is_opening_query}, 关门查询: {is_closing_query}")
    print(f"查询分析 - 最早查询: {is_earliest_query}, 最晚查询: {is_latest_query}")
    
    # 确定具体的属性
    attribute = None
    
    # 如果AI已经提供了属性，优先使用AI提供的
    if ai_result.get("attribute") in ["earliest_opening", "latest_opening", "earliest_closing", "latest_closing"]:
        attribute = ai_result.get("attribute")
        print(f"使用AI提供的属性: {attribute}")
    else:
        # 根据关键词确定属性
        if is_earliest_query and is_opening_query:
            attribute = "earliest_opening"
        elif is_latest_query and is_opening_query:
            attribute = "latest_opening"
        elif is_earliest_query and is_closing_query:
            attribute = "earliest_closing"
        elif is_latest_query and is_closing_query:
            attribute = "latest_closing"
        # 默认处理
        elif is_earliest_query:
            attribute = "earliest_opening"  # 默认为最早开门
        elif is_latest_query:
            attribute = "latest_closing"  # 默认为最晚关门
        elif is_opening_query:
            attribute = "latest_opening"  # 如果只提到开门，默认为最晚开门
        elif is_closing_query:
            attribute = "latest_closing"  # 如果只提到关门，默认为最晚关门
    
    print(f"确定查询属性: '{attribute}'")
    
    if not attribute:
        print("⚠️ 无法确定查询属性")
        return {
            "answer": f"Sorry, I couldn't determine what time attribute you're asking about (earliest/latest opening/closing). Please try a more specific query.",
            "related_ids": [],
            "center": []
        }
    
    # Determine if it's a weekend query
    is_weekend = None
    if "weekend" in query_lower:
        is_weekend = True
    elif "weekday" in query_lower:
        is_weekend = False
    else:
        today = datetime.now().weekday()
        is_weekend = today >= 5  # 5 and 6 are Saturday and Sunday
    
    day_type = "weekend" if is_weekend else "weekday"
    print(f"查询日期类型: {day_type}")
    
    # 调用更新后的API端点
    print(f"调用special_time_outlets_in_location API - 位置: '{location}', 属性: '{attribute}', 周末: {is_weekend}")
    result = special_time_outlets_in_location(
        location=location,
        attribute=attribute,
        is_weekend=is_weekend
    )
    
    print(f"API返回结果 - 数量: {result.get('count', 0)}, 消息: '{result.get('message', '')}'")
    
    # 如果没有找到结果
    if result.get("count", 0) == 0:
        print("⚠️ 未找到符合条件的店铺")
        return {
            "answer": result.get("message", f"Sorry, I couldn't find any outlets in {location} that match your criteria."),
            "related_ids": [],
            "center": result.get("center", [])
        }
    
    # 格式化回复
    outlets = result.get("outlets", [])
    formatted_time = result.get("time", "N/A")
    day_type = result.get("day_type", "weekday")
    attribute_display = {
        "earliest_opening": "open earliest",
        "latest_opening": "open latest",
        "earliest_closing": "close earliest",
        "latest_closing": "close latest"
    }.get(attribute, attribute)
    
    # 准备返回数据
    ids = [o['id'] for o in outlets]
    count = len(outlets)
    center = result.get("center", [])
    
    print(f"找到 {count} 家店铺，时间: {formatted_time}")
    
    if count > 1:
        # 去重店铺名称
        unique_names = []
        seen_names = set()
        for o in outlets:
            if o['name'] not in seen_names:
                unique_names.append(o['name'])
                seen_names.add(o['name'])
        
        # 显示前三个不重复的名称
        outlet_names = ", ".join(unique_names[:3])
        if len(unique_names) > 3:
            outlet_names += f" and {len(unique_names)-3} more outlets"
        
        return {
            "answer": f"I found {count} outlets in {location} that {attribute_display} at {formatted_time} on {day_type}, including: {outlet_names}.",
            "related_ids": ids,
            "center": center
        }
    else:
        outlet = outlets[0]
        return {
            "answer": f"I found that {outlet['name']} is the outlet in {location} that {attribute_display} at {formatted_time} on {day_type}.",
            "related_ids": ids,
            "center": center
        }
    