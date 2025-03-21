import requests
from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from backend.db import execute_query
import time
import re
import json

# 解析地址
def parse_address(address_text):
    """增强版地址解析，提取结构化信息"""
    if not address_text or address_text == "N/A":
        return {
            "full_address": address_text,
            "street_address": None,
            "district": None,
            "city": None,
            "postcode": None
        }
    
    # 提取邮编
    postcode = None
    postcode_match = re.search(r'(\d{5})', address_text)
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
        city_match = re.search(pattern, address_text)
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
        district_match = re.search(pattern, address_text, re.IGNORECASE)
        if district_match:
            district = district_match.group(0).strip()
            break
    
    # 提取街道地址
    street = None
    street_match = re.search(r'(?:No\.?\s*\d+[,\s]*)?([^,]+?)(?:,|$)', address_text)
    if street_match:
        street = street_match.group(1).strip()
    
    return {
        "full_address": address_text,
        "street_address": street,
        "district": district,
        "city": city,
        "postcode": postcode
    }

# 辅助函数：解析营业时间
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

# 解析营业时间
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

service = Service(ChromeDriverManager().install())
driver = webdriver.Chrome(service=service)

url = "https://subway.com.my/find-a-subway"
driver.get(url)

search_box = driver.find_element(By.ID, "fp_searchAddress")
search_box.clear()
search_box.send_keys("Kuala Lumpur")

search_btn = driver.find_element(By.ID, "fp_searchAddressBtn")  
search_btn.click()  
time.sleep(3)  # 等待页面加载

# 抓取所有门店列表
all_outlets = driver.find_elements(By.CLASS_NAME, "fp_listitem")
outlet_data = []

for outlet in all_outlets:
    # ------------------------------
    # 1) 只处理在前台可见的门店
    # ------------------------------
    if not outlet.is_displayed():
        # 如果这个节点被设置为 display:none 或其父级被隐藏，就跳过
        continue

    try:
        # 获取门店名称
        name_element = outlet.find_elements(By.TAG_NAME, "h4")
        name = name_element[0].text.strip() if name_element else "N/A"

        latitude = outlet.get_attribute("data-latitude")
        longitude = outlet.get_attribute("data-longitude")

        # 等待 infoboxcontent
        try:
            info_box = WebDriverWait(outlet, 5).until(
                EC.presence_of_element_located((By.CLASS_NAME, "infoboxcontent"))
            )
            # 强制显示 (可选)
            if info_box and not info_box.is_displayed():
                driver.execute_script("arguments[0].style.display = 'block';", info_box)
        except:
            info_box = None

        # 提取地址和营业时间
        if info_box:
            p_tags = info_box.find_elements(By.TAG_NAME, "p")
            address = p_tags[0].text.strip() if len(p_tags) > 0 else "N/A"

            hours_list = [p.text.strip() for p in p_tags[1:] if p.text.strip()]
            hours = " | ".join(hours_list) if hours_list else "N/A"
        else:
            address = "N/A"
            hours = "N/A"

        # 获取 Waze 和 Google Maps 链接
        waze_link = "N/A"
        google_maps_link = "N/A"
        try:
            links = outlet.find_elements(By.XPATH, ".//a")
            for link in links:
                href = link.get_attribute("href")
                if "waze.com" in href:
                    waze_link = href
                elif "goo.gl/maps" in href or "google.com/maps" in href:
                    google_maps_link = href
        except:
            pass

        # 解析地址和营业时间
        address_data = parse_address(address)
        hours_data = parse_operating_hours(hours)

        # 存储数据到 PostgreSQL
        query = """
        INSERT INTO subway_outlets (
            name, address, operating_hours, latitude, longitude, 
            waze_link, google_maps_link, street_address, district, 
            city, postcode, opening_hours, is_24hours
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (name, address) 
        DO UPDATE SET 
            operating_hours = EXCLUDED.operating_hours,
            latitude = EXCLUDED.latitude,
            longitude = EXCLUDED.longitude,
            waze_link = EXCLUDED.waze_link,
            google_maps_link = EXCLUDED.google_maps_link,
            street_address = EXCLUDED.street_address,
            district = EXCLUDED.district,
            city = EXCLUDED.city,
            postcode = EXCLUDED.postcode,
            opening_hours = EXCLUDED.opening_hours,
            is_24hours = EXCLUDED.is_24hours,
            updated_at = CURRENT_TIMESTAMP;
        """
        values = (
            name, address, hours_data["text"], latitude, longitude, 
            waze_link, google_maps_link, address_data["street_address"], 
            address_data["district"], address_data["city"], address_data["postcode"], 
            json.dumps(hours_data["hours_json"]), hours_data["is_24hours"]
        )
        execute_query(query, values)

        # 存储到结果列表
        outlet_data.append({
            "name": name,
            "address": address,
            "hours": hours,
            "latitude": latitude,
            "longitude": longitude,
            "waze_link": waze_link,
            "google_maps_link": google_maps_link
        })

    except Exception as e:
        print(f"Error processing outlet: {e}")


driver.quit()
