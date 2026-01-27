import json
import re
import hashlib
from datetime import datetime

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
    解析 Voyager MD 文件结构 (增强版)
    包含元数据验证、时间解析、页脚清洗及完整性检查
    """
    result = {'meta': {}, 'turns': []}
    
    # ================= 0. 预处理：清洗页脚 =================
    # TODO: 去除md文本后缀"\n---\n\n*Exported from [Gemini Voyager]"及之后内容
    # 使用正则非贪婪匹配找到最后的分割线和 Export 签名
    footer_pattern = r'\n---\n\n\*Exported from \[Gemini Voyager\].*$'
    if re.search(footer_pattern, md_content, re.DOTALL):
        md_content = re.sub(footer_pattern, '', md_content, flags=re.DOTALL).strip()
    else:
        # 也许是手动复制没有带 footer，或者格式变了，打印一个轻微提示
        # print("ℹ️ Note: 未检测到标准 Voyager 页脚签名，可能是手动复制的内容。")
        pass

    lines = md_content.split('\n')

    # ================= 1. 提取 Meta 信息 =================
    
    # --- 1.1 提取标题 (带行数限制的安全检查) ---
    # TODO: 如果当前行数太大了，则很有可能不是标题
    title_found = False
    for idx, line in enumerate(lines):
        if idx > 10: # 如果前10行都没找到标题，说明文件头可能坏了
            break
        if line.startswith('# '):
            result['meta']['title'] = line[2:].strip()
            title_found = True
            break
    
    if not title_found:
        print("⚠️ Warning: 未在前10行检测到标准一级标题 (# Title)，将使用默认标题。")
        result['meta']['title'] = "Untitled Chat"

    # --- 1.2 提取 Source URL 和 ID ---
    source_pattern = r'\*\*Source\*\*: \[.*?\]\((https://gemini\.google\.com/app/([a-zA-Z0-9]+))\)'
    source_match = re.search(source_pattern, md_content)
    
    if source_match:
        result['meta']['source_url'] = source_match.group(1)
        result['meta']['id'] = source_match.group(2)
    else:
        # TODO: 如果找不到，这里得弹出一个警告
        print(f"⚠️ Warning: 未找到 Gemini Source URL (对话ID)。将使用标题哈希作为临时 ID，这可能导致未来无法精确去重。")
        result['meta']['source_url'] = ""
        # 降级方案：使用 Title + 内容前20个字符做 Hash，尽量保证唯一
        hash_source = (result['meta']['title'] + md_content[:50]).encode('utf-8')
        result['meta']['id'] = hashlib.md5(hash_source).hexdigest()[:16]

    # --- 1.3 提取并校验轮数 (Turns) ---
    # TODO: 提取总轮数"**Turns**: n"并于之后看到的最大轮数比较验证
    turns_match = re.search(r'\*\*Turns\*\*: (\d+)', md_content)
    expected_turn_count = int(turns_match.group(1)) if turns_match else 0

    # --- 1.4 提取导出时间 (Date) ---
    # TODO: 提取voyager导出时间并转为ISO8601
    # 格式示例: "**Date**: January 27, 2026 at 01:44 PM"
    date_match = re.search(r'\*\*Date\*\*: (.*)', md_content)
    if date_match:
        date_str = date_match.group(1).strip()
        try:
            # 解析英文格式时间 (注意：这依赖系统locale支持英文月份，如果是在中文Win系统可能需要手动映射月份)
            # 格式解析: %B=FullMonthName, %d=Day, %Y=Year, %I=12H, %M=Minute, %p=AM/PM
            # 这里的时区处理比较复杂。Voyager 导出的是"生成文件时的时间"（通常是浏览器本地时间）。
            # 为了简单且标准，我们暂且视为本地时间，并转为 ISO 格式字符串。
            dt_obj = datetime.strptime(date_str, "%B %d, %Y at %I:%M %p")
            result['meta']['exported_at'] = dt_obj.isoformat()
        except ValueError as e:
            print(f"⚠️ Warning: 时间格式解析失败 '{date_str}': {e}")
            result['meta']['exported_at'] = None
    else:
        result['meta']['exported_at'] = None

    # ================= 2. 分割与解析对话轮次 =================

    # 使用正则分割，保留分割符以便后续调试（这里直接split丢弃分割符即可）
    # 注意：前面已经清洗了 footer，所以最后一个 Turn 应该是干净的
    parts = re.split(r'## Turn \d+', md_content)
    
    # parts[0] 是 header，跳过
    for part in parts[1:]: 
        if not part.strip(): continue # 跳过空块
        
        turn_data = {}
        
        # 优化正则：增加非贪婪匹配和边界容错
        user_match = re.search(r'### 👤 User\s+(.*?)\s+### 🤖 Assistant', part, re.DOTALL)
        assistant_match = re.search(r'### 🤖 Assistant\s+(.*)', part, re.DOTALL)
        
        if user_match:
            turn_data['user_text'] = user_match.group(1).strip()
        
        if assistant_match:
            turn_data['assistant_text'] = assistant_match.group(1).strip()
            
        # 只有当至少有一方有内容时才添加
        if turn_data:
            result['turns'].append(turn_data)

    # ================= 3. 完整性校验 (Sanity Check) =================
    
    actual_turn_count = len(result['turns'])
    
    # 如果 Voyager 声明了轮数，但我们解析出来的数量不对
    if expected_turn_count > 0 and expected_turn_count != actual_turn_count:
        print(f"🚨 CRITICAL WARNING: 数据完整性受损！")
        print(f"   元数据声明轮数: {expected_turn_count}")
        print(f"   实际解析轮数: {actual_turn_count}")
        print(f"   可能原因: 正则匹配失败或 Markdown 结构被破坏。建议人工检查文件: {result['meta']['title']}")
        # 这里你可以选择 raise Exception 阻断流程，或者标记 meta 数据
        result['meta']['integrity_check'] = False
    else:
        result['meta']['integrity_check'] = True

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