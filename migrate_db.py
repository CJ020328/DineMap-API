import re
import json
import psycopg2
from psycopg2.extras import DictCursor

# 数据库连接参数
DB_NAME = "subway_db"
DB_USER = "postgres"
DB_PASSWORD = "Chai2002328"
DB_HOST = "localhost"
DB_PORT = "5432"

def connect_db():
    """连接到PostgreSQL数据库"""
    return psycopg2.connect(
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        host=DB_HOST,
        port=DB_PORT
    )

def add_new_columns():
    """添加新的结构化字段到数据库"""
    conn = connect_db()
    try:
        with conn.cursor() as cur:
            # 检查列是否已存在
            cur.execute("""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'subway_outlets'
            """)
            existing_columns = [col[0] for col in cur.fetchall()]
            
            # 准备要添加的新列
            new_columns = [
                {"name": "city", "type": "VARCHAR(100)"},
                {"name": "district", "type": "VARCHAR(100)"},
                {"name": "postcode", "type": "VARCHAR(10)"},
                {"name": "street_address", "type": "VARCHAR(255)"},
                {"name": "opening_hours", "type": "JSONB"},
                {"name": "is_24hours", "type": "BOOLEAN DEFAULT false"}
            ]
            
            # 添加不存在的列
            for col in new_columns:
                if col["name"] not in existing_columns:
                    print(f"添加列: {col['name']}")
                    cur.execute(f"ALTER TABLE subway_outlets ADD COLUMN {col['name']} {col['type']}")
            
            conn.commit()
            print("✅ 数据库结构已更新")
    except Exception as e:
        print(f"❌ 更新数据库结构时出错: {e}")
    finally:
        conn.close()

def extract_address_components():
    """增强版地址解析"""
    conn = connect_db()
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            # 获取所有门店数据
            cur.execute("SELECT id, address FROM subway_outlets")
            rows = cur.fetchall()
            
            for row in rows:
                outlet_id = row['id']
                address = row['address']
                
                if not address or address == 'N/A':
                    continue
                
                # 提取邮编
                postcode = None
                postcode_match = re.search(r'(\d{5})', address)
                if postcode_match:
                    postcode = postcode_match.group(1)
                
                # 提取城市 (尝试多种模式)
                city = None
                city_patterns = [
                    r'(?:,\s*)([A-Za-z\s]+)(?:,\s*)\d{5}',  # 模式1: , Petaling Jaya, 47500
                    r'(\w+)(?:\s+\d{5})',                   # 模式2: Selangor 40150
                    r'([A-Za-z\s]+)(?=\s*\d{5})'            # 模式3: Kuala Lumpur 50450
                ]
                
                for pattern in city_patterns:
                    city_match = re.search(pattern, address)
                    if city_match:
                        city = city_match.group(1).strip()
                        break
                
                # 提取区域 (更多种模式)
                district = None
                district_patterns = [
                    r'(?:Section|Seksyen)\s+([A-Za-z0-9/]+)',  # 模式1: Section U5
                    r'(Taman\s+[A-Za-z\s]+)',                 # 模式2: Taman Maluri
                    r'(Bandar\s+[A-Za-z\s]+)'                 # 模式3: Bandar Sunway
                ]
                
                for pattern in district_patterns:
                    district_match = re.search(pattern, address, re.IGNORECASE)
                    if district_match:
                        district = district_match.group(0).strip()
                        break
                
                # 更新数据库
                cur.execute("""
                    UPDATE subway_outlets 
                    SET postcode = %s, city = %s, district = %s
                    WHERE id = %s
                """, (postcode, city, district, outlet_id))
            
            conn.commit()
    finally:
        conn.close()

def parse_operating_hours(hours_text):
    """增强版营业时间解析，处理多种格式"""
    if not hours_text or hours_text == "N/A":
        return {
            "text": hours_text,
            "hours_json": {"monday": None, "tuesday": None, "wednesday": None, 
                          "thursday": None, "friday": None, "saturday": None, "sunday": None},
            "is_24hours": False
        }
    
    # 检测24小时营业
    is_24hours = "24" in hours_text.lower() or "24hrs" in hours_text.lower() or "24 hours" in hours_text.lower()
    
    # 初始化结果JSON
    hours_data = {
        "monday": None, "tuesday": None, "wednesday": None,
        "thursday": None, "friday": None, "saturday": None, "sunday": None
    }
    
    # 分割不同的时段(通常用|分隔)
    segments = re.split(r'\s*\|\s*', hours_text)
    
    for segment in segments:
        # 清理segment
        segment = segment.strip()
        
        # 尝试多种模式匹配:
        
        # 1. 模式: "0800 - 2200 (Sun - Thur)"
        pattern1 = re.search(r'(\d{3,4})\s*[-–]\s*(\d{3,4})\s*\(\s*([^)]+)\s*\)', segment)
        if pattern1:
            time_open = pattern1.group(1)
            time_close = pattern1.group(2)
            days_text = pattern1.group(3).lower()
            days = extract_days_from_text(days_text)
            for day in days:
                hours_data[day] = {"open": time_open, "close": time_close}
            continue
            
        # 2. 模式: "Monday - Saturday, 8:00 AM - 9:00PM"
        pattern2 = re.search(r'([a-zA-Z]+(?:\s*[-–]\s*[a-zA-Z]+)?),\s*(\d{1,2}:\d{2})\s*([AP]M)\s*[-–]\s*(\d{1,2}:\d{2})\s*([AP]M)', segment, re.IGNORECASE)
        if pattern2:
            days_text = pattern2.group(1).lower()
            time_open = pattern2.group(2) + ' ' + pattern2.group(3)
            time_close = pattern2.group(4) + ' ' + pattern2.group(5)
            days = extract_days_from_text(days_text)
            
            # 转换12小时制到24小时制
            time_open_24 = convert_12h_to_24h(time_open)
            time_close_24 = convert_12h_to_24h(time_close)
            
            for day in days:
                hours_data[day] = {"open": time_open_24, "close": time_close_24}
            continue
            
        # 3. 模式: "Monday, 8:00 AM - 10:00PM" (单天)
        pattern3 = re.search(r'([a-zA-Z]+),\s*(\d{1,2}:\d{2})\s*([AP]M)\s*[-–]\s*(\d{1,2}:\d{2})\s*([AP]M)', segment, re.IGNORECASE)
        if pattern3:
            day_text = pattern3.group(1).lower()
            time_open = pattern3.group(2) + ' ' + pattern3.group(3)
            time_close = pattern3.group(4) + ' ' + pattern3.group(5)
            
            # 转换12小时制到24小时制
            time_open_24 = convert_12h_to_24h(time_open)
            time_close_24 = convert_12h_to_24h(time_close)
            
            day = get_day_key(day_text)
            if day:
                hours_data[day] = {"open": time_open_24, "close": time_close_24}
            continue
        
        # 4. 简单格式: "0800-2200"
        pattern4 = re.search(r'(\d{3,4})\s*[-–]\s*(\d{3,4})', segment)
        if pattern4:
            time_open = pattern4.group(1)
            time_close = pattern4.group(2)
            
            # 如果前面模式没有匹配任何日期，默认应用到所有日期
            if all(hours_data[day] is None for day in hours_data):
                for day in hours_data:
                    hours_data[day] = {"open": time_open, "close": time_close}
            continue
        
        # 5. 添加模式5: "Monday - Sunday (8:00AM - 10:00PM)"
        pattern5 = re.search(r'([a-zA-Z]+(?:\s*[-–]\s*[a-zA-Z]+)?)\s*\(\s*(\d{1,2}:\d{2})\s*([AP]M)\s*[-–]\s*(\d{1,2}:\d{2})\s*([AP]M)\s*\)', segment, re.IGNORECASE)
        if pattern5:
            days_text = pattern5.group(1).lower()
            time_open = pattern5.group(2) + ' ' + pattern5.group(3)
            time_close = pattern5.group(4) + ' ' + pattern5.group(5)
            days = extract_days_from_text(days_text)
            
            # 转换12小时制到24小时制
            time_open_24 = convert_12h_to_24h(time_open)
            time_close_24 = convert_12h_to_24h(time_close)
            
            for day in days:
                hours_data[day] = {"open": time_open_24, "close": time_close_24}
            continue
        
        # 6. 添加模式6: "Monday to Sunday (10:00AM - 10:00PM)"
        pattern6 = re.search(r'([a-zA-Z]+)\s+to\s+([a-zA-Z]+)\s*\(\s*(\d{1,2}:\d{2})\s*([AP]M)\s*[-–]\s*(\d{1,2}:\d{2})\s*([AP]M)\s*\)', segment, re.IGNORECASE)
        if pattern6:
            start_day = pattern6.group(1).lower()
            end_day = pattern6.group(2).lower()
            days_text = f"{start_day}-{end_day}"
            time_open = pattern6.group(3) + ' ' + pattern6.group(4)
            time_close = pattern6.group(5) + ' ' + pattern6.group(6)
            
            days = extract_days_from_text(days_text)
            
            # 转换12小时制到24小时制
            time_open_24 = convert_12h_to_24h(time_open)
            time_close_24 = convert_12h_to_24h(time_close)
            
            for day in days:
                hours_data[day] = {"open": time_open_24, "close": time_close_24}
            continue
    
    return {
        "text": hours_text,
        "hours_json": hours_data,
        "is_24hours": is_24hours
    }

def extract_days_from_text(days_text):
    """从文本中提取星期几"""
    days_text = days_text.lower()
    result = []
    
    # 映射
    day_mapping = {
        'mon': 'monday', 'monday': 'monday',
        'tue': 'tuesday', 'tues': 'tuesday', 'tuesday': 'tuesday',
        'wed': 'wednesday', 'weds': 'wednesday', 'wednesday': 'wednesday',
        'thu': 'thursday', 'thur': 'thursday', 'thurs': 'thursday', 'thursday': 'thursday',
        'fri': 'friday', 'friday': 'friday',
        'sat': 'saturday', 'saturday': 'saturday',
        'sun': 'sunday', 'sunday': 'sunday'
    }
    
    # 检查范围 (例如 "Mon-Fri")
    range_match = re.search(r'([a-z]+)\s*[-–]\s*([a-z]+)', days_text)
    if range_match:
        start_day = range_match.group(1).lower()
        end_day = range_match.group(2).lower()
        
        # 映射到标准名称
        start_day = day_mapping.get(start_day)
        end_day = day_mapping.get(end_day)
        
        if start_day and end_day:
            # 定义星期顺序
            all_days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']
            start_index = all_days.index(start_day)
            end_index = all_days.index(end_day)
            
            # 处理循环情况 (例如"Sun-Sat"应该是整周)
            if start_index <= end_index:
                result = all_days[start_index:end_index+1]
            else:
                result = all_days[start_index:] + all_days[:end_index+1]
        return result
    
    # 单个日期或逗号分隔的日期
    for day_abbr in re.findall(r'([a-z]+)', days_text):
        if day_abbr in day_mapping:
            result.append(day_mapping[day_abbr])
    
    return result

def get_day_key(day_text):
    """获取标准化的星期几键名"""
    day_text = day_text.lower()
    day_mapping = {
        'mon': 'monday', 'monday': 'monday',
        'tue': 'tuesday', 'tues': 'tuesday', 'tuesday': 'tuesday',
        'wed': 'wednesday', 'weds': 'wednesday', 'wednesday': 'wednesday',
        'thu': 'thursday', 'thur': 'thursday', 'thurs': 'thursday', 'thursday': 'thursday',
        'fri': 'friday', 'friday': 'friday',
        'sat': 'saturday', 'saturday': 'saturday',
        'sun': 'sunday', 'sunday': 'sunday'
    }
    return day_mapping.get(day_text)

def convert_12h_to_24h(time_str):
    """将12小时制转换为24小时制数字格式"""
    # 清理特殊字符
    time_str = re.sub(r'[^0-9:APM ]', '', time_str)
    
    # 解析时间
    try:
        # 处理格式 "8:00 AM"
        match = re.search(r'(\d{1,2}):(\d{2})\s*([AP]M)', time_str, re.IGNORECASE)
        if match:
            hour = int(match.group(1))
            minute = int(match.group(2))
            ampm = match.group(3).upper()
            
            # 转换到24小时制
            if ampm == 'PM' and hour < 12:
                hour += 12
            elif ampm == 'AM' and hour == 12:
                hour = 0
                
            # 格式化为4位数字格式
            return f"{hour:02d}{minute:02d}"
            
        # 其他格式...
        return "0000"  # 默认值
    except:
        return "0000"  # 解析失败

def extract_opening_hours():
    """从operating_hours文本字段中提取结构化营业时间信息"""
    conn = connect_db()
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            # 获取所有门店数据
            cur.execute("SELECT id, operating_hours FROM subway_outlets")
            rows = cur.fetchall()
            
            # 处理每一条记录
            print(f"开始处理 {len(rows)} 条营业时间记录...")
            
            for row in rows:
                outlet_id = row['id']
                hours_text = row['operating_hours']
                
                if not hours_text or hours_text == 'N/A':
                    continue
                
                # 解析营业时间
                hours_data = parse_operating_hours(hours_text)
                
                # 更新数据库
                cur.execute("""
                    UPDATE subway_outlets 
                    SET opening_hours = %s, is_24hours = %s
                    WHERE id = %s
                """, (json.dumps(hours_data['hours_json']), hours_data['is_24hours'], outlet_id))
                
                if outlet_id % 10 == 0:  # 每处理10条打印一次进度
                    print(f"已处理 {outlet_id} 条记录")
            
            conn.commit()
            print("✅ 营业时间信息已提取并更新")
    except Exception as e:
        print(f"❌ 提取营业时间信息时出错: {e}")
    finally:
        conn.close()

def build_postcode_area_mapping():
    """从已有数据构建邮编到地区的映射"""
    conn = connect_db()
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            # 获取所有邮编和对应的城市/地区
            cur.execute("""
                SELECT DISTINCT postcode, city 
                FROM subway_outlets 
                WHERE postcode IS NOT NULL AND city IS NOT NULL
            """)
            rows = cur.fetchall()
            
            # 构建映射
            mapping = {}
            for row in rows:
                postcode = row['postcode']
                city = row['city']
                if postcode not in mapping:
                    mapping[postcode] = city
            
            # 可以保存到数据库或文件中
            return mapping
    finally:
        conn.close()

def main():
    """主函数：执行所有迁移步骤"""
    print("开始数据库迁移...")
    
    # 步骤1: 添加新列
    add_new_columns()
    
    # 步骤2: 提取地址信息
    extract_address_components()
    
    # 步骤3: 提取营业时间信息
    extract_opening_hours()
    
    print("✅ 数据库迁移完成!")

if __name__ == "__main__":
    main()
    