import json
import re
import hashlib

# ================= 配置文件名 =================
MD_FILE_PATH = r'data\gemini-voyager-export\gemini-chat-20260127-160519.md'       # Voyager 导出的 MD
TAKEOUT_FILE_PATH = r'data\google-takeout\我的活动记录.json'   # Google Takeout JSON
OUTPUT_FILE_PATH = r'data\output\master_archive.json' # 结果文件
# ============================================

def clean_takeout_prompt(title):
    """从 Takeout 标题中提取用户 Prompt (去掉 'Prompted ' 前缀)"""
    if title.startswith("Prompted "):
        return title[9:].strip()
    return title.strip()
    # TODO: check if the string is start with "Prompted "

def parse_voyager_md(md_content):
    """
    解析 Voyager MD 文件结构
    返回: { 'meta': dict, 'turns': list }
    """
    result = {'meta': {}, 'turns': []}
    
    # 1. 提取 Meta 信息 (Title, Source URL/ID)
    lines = md_content.split('\n')
    
    # 提取标题 (第一行 # 后面的内容)
    for line in lines:
        if line.startswith('# '):
            # TODO: 如果当前行数太大了，则很有可能不是标题
            result['meta']['title'] = line[2:].strip()
            break
            
    # 提取 Source URL 和 ID
    # 匹配模式: **Source**: [Gemini Chat](https://gemini.google.com/app/xxxx)
    source_pattern = r'\*\*Source\*\*: \[.*?\]\((https://gemini\.google\.com/app/([a-zA-Z0-9]+))\)'
    source_match = re.search(source_pattern, md_content)
    if source_match:
        result['meta']['source_url'] = source_match.group(1)
        result['meta']['id'] = source_match.group(2) # 优先用 URL 尾部 ID
    else:
        # TODO: 如果找不到，这里得弹出一个警告
        # 如果找不到 URL，用 Title 做个 Hash 当 ID (保底)
        result['meta']['source_url'] = ""
        result['meta']['id'] = hashlib.md5(result['meta'].get('title', '').encode()).hexdigest()[:16]

    # TODO: 提取总轮数"**Turns**: n"并于之后看到的最大轮数比较验证
    # TODO: 提取voyager导出时间并转为ISO8601，格式: "**Date**: January 27, 2026 at 01:44 PM"(默认认为基于当前时区)，所以需要转换为UTC或使用当前时区

    # 2. 分割对话轮次 (Turns)
    # 使用正则表达式按 "## Turn n" 分割
    # split 后第一个元素通常是 header 信息，后面是各个 turn
    # TODO: 去除md文本后缀"\n---\n\n*Exported from [Gemini Voyager]"及之后内容
    parts = re.split(r'## Turn \d+', md_content)
    
    for part in parts[1:]: # 跳过头部 meta 信息
        turn_data = {}
        
        # 提取 User 内容 (在 ### 👤 User 和 ### 🤖 Assistant 之间)
        user_match = re.search(r'### 👤 User\s+(.*?)\s+### 🤖 Assistant', part, re.DOTALL)
        # 提取 Assistant 内容 (在 ### 🤖 Assistant 之后)
        assistant_match = re.search(r'### 🤖 Assistant\s+(.*)', part, re.DOTALL)
        
        if user_match:
            turn_data['user_text'] = user_match.group(1).strip()
        if assistant_match:
            turn_data['assistant_text'] = assistant_match.group(1).strip()
            
        if turn_data:
            result['turns'].append(turn_data)
            
    return result

def load_takeout_index(json_path):

    # TODO: 使用某种方式处理每条JSON的title，使其能高效查找定位O(1)
    """
    加载 Takeout JSON 并建立索引
    Key: 用户 Prompt (文本) -> Value: 完整 Entry (包含时间, HTML)
    """
    try:
        with open(json_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except FileNotFoundError:
        print(f"错误: 找不到文件 {json_path}")
        return {}

    index = {}
    for entry in data:
        if 'title' in entry:
            clean_prompt = clean_takeout_prompt(entry['title'])
            # 注意：如果同一句话问了多次，这里只会存最后一次的索引（简单起见）
            # 改进版可以用 list 存储多个同名 prompt
            if clean_prompt not in index:
                index[clean_prompt] = []
            index[clean_prompt].append(entry)
            # TODO: check if all the 'title' is unique

    return index

def main():
    # 1. 读取并解析 MD
    try:
        with open(MD_FILE_PATH, 'r', encoding='utf-8') as f:
            md_content = f.read()
    except FileNotFoundError:
        print(f"错误: 找不到文件 {MD_FILE_PATH}")
        return

    voyager_data = parse_voyager_md(md_content)
    print(f"解析 MD 成功: 标题 '{voyager_data['meta'].get('title')}', 共 {len(voyager_data['turns'])} 轮对话")

    # 2. 加载 Takeout 索引
    takeout_index = load_takeout_index(TAKEOUT_FILE_PATH)
    print(f"加载 Takeout 索引成功，共 {len(takeout_index)} 条记录")

    # 3. 构建 Master JSON
    master_json = {
        "meta": {
            "id": voyager_data['meta'].get('id'),
            "title": voyager_data['meta'].get('title'),
            "source_url": voyager_data['meta'].get('source_url'),
            "created_at": None, # 稍后填充
            "tags": ["Gemini_Archive", "Imported"]
        },
        "messages": []
    }

    # 4. 遍历合并
    for i, turn in enumerate(voyager_data['turns']):
        user_txt = turn.get('user_text', '')
        assistant_txt = turn.get('assistant_text', '')
        
        # --- 查找 Takeout 匹配 ---
        matched_entry = None
        # TODO: 重写匹配逻辑，以函数方式
        # 取user_txt中不包含换行符空格制表符等的最长连续字符，在takeout_index中查找
        # 若结果唯一则返回，若不唯一则更换另一组连续字符，重复此过程，直到找到唯一结果或遍历完所有可能
        if user_txt in takeout_index and takeout_index[user_txt]:
            # 取最早的一条（假设按时间倒序，pop() 取最后一条即最早的？需确认 takeout 顺序）
            # Takeout 通常是时间倒序 (最新的在上面)。
            # Voyager 也是时间倒序还是正序？通常 Chat 记录是正序 (Turn 1 是最早)。
            # 这里简单处理：匹配到就用，用完弹出一个，避免重复匹配
            matched_entry = takeout_index[user_txt].pop(-1) # 尝试弹出一项
        
        # 获取关键数据
        timestamp = matched_entry['time'] if matched_entry else None
        raw_html = None
        if matched_entry and 'safeHtmlItem' in matched_entry:
             # Takeout 的 HTML 藏在 safeHtmlItem 列表里
             if len(matched_entry['safeHtmlItem']) > 0:
                 raw_html = matched_entry['safeHtmlItem'][0].get('html')

        # 如果是第一轮对话，设置整个对话的创建时间
        if i == 0 and timestamp:
            master_json['meta']['created_at'] = timestamp

        # --- 构建 User 消息 ---
        msg_user = {
            "id": f"{master_json['meta']['id']}_turn_{i+1}_user",
            "role": "user",
            "created_at": timestamp, # 只有匹配到了才有时间
            "content": {
                # 按照你的要求：如果是 User，取 Takeout 的 Prompt (去掉前缀)
                "text": clean_takeout_prompt(matched_entry['title']) if matched_entry else user_txt
            }
        }
        master_json['messages'].append(msg_user)

        # --- 构建 Assistant 消息 ---
        msg_assistant = {
            "id": f"{master_json['meta']['id']}_turn_{i+1}_assistant",
            "role": "assistant",
            "parent_id": f"{master_json['meta']['id']}_turn_{i+1}_user", # 关联父消息
            # 身份为 Assistant，时无时间信息
            "content": {
                "text": assistant_txt, # Voyager 的 MD
                "original_html": raw_html # Takeout 的 HTML
            }
        }
        master_json['messages'].append(msg_assistant)

        if not matched_entry:
            print(f"⚠️ Warning: Turn {i+1} 的用户提问 '{user_txt[:15]}...' 未在 Takeout 中找到匹配，时间戳缺失。")

    # 5. 输出
    with open(OUTPUT_FILE_PATH, 'w', encoding='utf-8') as f:
        json.dump(master_json, f, indent=2, ensure_ascii=False)
    
    print(f"✅ 成功生成 Master JSON: {OUTPUT_FILE_PATH}")

if __name__ == "__main__":
    main()