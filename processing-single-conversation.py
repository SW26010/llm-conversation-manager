import json
import re
import hashlib
from datetime import datetime, timedelta

# ================= 配置文件名 =================
MD_FILE_PATH = r'data\gemini-voyager-export\chat.md'       # Voyager 导出的 MD
TAKEOUT_FILE_PATH = r'data\google-takeout\我的活动记录.json'   # Google Takeout JSON
OUTPUT_FILE_PATH = r'data\output\master_archive.json' # 结果文件
# ============================================

def clean_takeout_prompt(title):
    """从 Takeout 标题中提取用户 Prompt (去掉 'Prompted ' 前缀)"""
    if title.startswith("Prompted "):
        return title[9:].strip()

    # 处理其他可能的 Gemini 活动类型，防止脚本因未知类型崩溃
    known_prefixes = [
        "Created Gemini Canvas titled ", # 深度研究或互动测试
        "Gave feedback: ",
        "Used an Assistant feature",
        "Selected preferred draft"
    ]

    if any(title.startswith(prefix) for prefix in known_prefixes):
        print(f"ℹ️ Info: 跳过非对话类活动: '{title[:30]}...'")
    else:
        print(f"⚠️ Warning: 暂不受支持的prompt类型: '{title[:30]}'")
    # 原样保留预期外的类型，相信后人的智慧
    return title.strip()
    


def parse_voyager_md(md_content):
    """
    解析 Voyager MD 文件结构 (增强版)
    包含元数据验证、时间解析、页脚清洗及完整性检查
    """
    result = {'meta': {}, 'turns': []}
    
    # ================= 0. 预处理：清洗页脚 =================
    # 去除md文本后缀"\n---\n\n*Exported from [Gemini Voyager]"及之后内容
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
    # 如果当前行数太大了，则很有可能不是标题
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
        # 如果找不到，这里得弹出一个警告
        print(f"⚠️ Warning: 未找到 Gemini Source URL (对话ID)。将使用标题哈希作为临时 ID，这可能导致未来无法精确去重。")
        result['meta']['source_url'] = ""
        # 降级方案：使用 Title + 内容前20个字符做 Hash，尽量保证唯一
        hash_source = (result['meta']['title'] + md_content[:50]).encode('utf-8')
        result['meta']['id'] = hashlib.md5(hash_source).hexdigest()[:16]

    # --- 1.3 提取并校验轮数 (Turns) ---
    # 提取总轮数"**Turns**: n"并于之后看到的最大轮数比较验证
    turns_match = re.search(r'\*\*Turns\*\*: (\d+)', md_content)
    expected_turn_count = int(turns_match.group(1)) if turns_match else 0

    # --- 1.4 提取导出时间 (Date) ---
    # 提取voyager导出时间并转为ISO8601
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
            # TODO: do not clean the prompt this time, just use the original title
            # 注意：如果同一句话问了多次，这里只会存最后一次的索引（简单起见）
            # 改进版可以用 list 存储多个同名 prompt
            if clean_prompt not in index:
                index[clean_prompt] = []
            index[clean_prompt].append(entry)
            # TODO: check if all the 'title' is unique

    return index


# ================== 模糊查找相关逻辑 ==================

def get_clean_segments(text):
    """
    将文本按空白字符切割，并按长度降序排列。
    例如: "Hello World\nCheck this" -> ["Hello", "World", "Check", "this"] (排序后)
    """
    if not text:
        return []
    # split() 默认会去除 \n, \t, 空格等所有空白符
    segments = text.split()
    # 按长度降序排列，优先匹配长词/长句，准确率更高
    return sorted(segments, key=len, reverse=True)

def fuzzy_find_key(user_txt, all_keys):
    """
    TODO 1 实现: 键名查找失败的回退方法
    策略: 拿 user_txt 的长片段去 all_keys 里通过包含关系(in) 筛选
    """
    # 1. 获取用户输入的所有“连续字符片段”
    segments = get_clean_segments(user_txt)
    
    # 初始候选集是所有键
    candidates = list(all_keys)
    
    # 2. 迭代筛选
    for seg in segments:
        # 找出包含当前片段的候选键
        # 忽略大小写可能更稳健，但这里先严格按照你的要求做
        new_candidates = [k for k in candidates if seg in k]
        
        if len(new_candidates) == 0:
            # 当前片段可能包含 Markdown 符号或错别字，导致匹配不到，跳过它，尝试下一个片段
            continue
        elif len(new_candidates) == 1:
            # 找到唯一结果，直接返回
            return new_candidates[0]
        else:
            # 结果不唯一 (例如 seg="测试" 匹配到了 "测试A", "测试B")
            # 将候选集缩小，进入下一轮循环，用下一个片段继续过滤
            candidates = new_candidates
            
    # 3. 循环结束后的处理
    # 如果最后剩下一个或多个，返回最长的那一个（概率上最接近）
    if candidates:
        return max(candidates, key=len)
    
    return None

def disambiguate_entries(entries, assistant_txt):
    """
    实现: 若存在不止一项，根据 assistant 的信息判断
    策略: 拿 assistant_txt 的长片段去 entry['html'] 里筛选
    """
    if not entries:
        return None
    if len(entries) == 1:
        return entries[0]
        
    # 获取 Voyager 记录中 Assistant 回复的片段
    segments = get_clean_segments(assistant_txt)
    
    candidates = entries[:]
    
    for seg in segments:
        new_candidates = []
        for entry in candidates:
            # 获取 Takeout 中的 HTML (作为真值依据)
            raw_html = ""
            if 'safeHtmlItem' in entry and entry['safeHtmlItem']:
                raw_html = entry['safeHtmlItem'][0].get('html', "")
            
            # 判断片段是否在 HTML 中
            # 注意: HTML 包含标签，简单的 'in' 可能会因为标签截断文本而失败
            # 但长片段匹配法通常能命中无标签的纯文本部分
            if seg in raw_html:
                new_candidates.append(entry)
        
        if len(new_candidates) == 1:
            return new_candidates[0]
        elif len(new_candidates) > 1:
            # 缩小范围，继续下一轮
            candidates = new_candidates
        # 如果 == 0，说明这个片段在 HTML 里没找到 (可能是 Markdown 格式差异)，忽略该片段
    
    # 如果筛选到底还是有多个 (或者全都没命中)，默认返回时间最早的一个 (通常是 pop(-1))
    # 但为了逻辑严谨，这里返回列表中的最后一个 (对应 Takeout 列表通常是倒序的逻辑)
    return candidates[-1]

# =================== UUID v7 ===================

import uuid
import secrets
from datetime import datetime, timezone

def generate_uuidv7(dt: datetime) -> str:
    """
    基于给定时间生成 RFC 9562 UUIDv7。
    逻辑：分段构建 (Timestamp | Ver | Rand A | Var | Rand B)
    """
    # 1. 提取毫秒级时间戳 (48 bits)
    # 确保使用 UTC 时间戳，若为 naive time 则视为本地时间并转为 UTC
    ts_ms = int(dt.timestamp() * 1000)
    
    # 2. 生成随机部分
    # rand_a: 12 bits (填充在 Version 之后)
    # rand_b: 62 bits (填充在 Variant 之后)
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    
    # 3. 按位组装 128 位整数
    uuid_int = (
        (ts_ms << 80)   |  # 48 bits: Unix Timestamp
        (0x7   << 76)   |  #  4 bits: Version 7
        (rand_a << 64)  |  # 12 bits: Random A
        (0x2   << 62)   |  #  2 bits: Variant 2
        rand_b             # 62 bits: Random B
    )
    
    return str(uuid.UUID(int=uuid_int))

# =================== 主逻辑 ===================

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
            "last_collected_at": voyager_data['meta'].get('exported_at')
            # 暂时不需要tags
        },
        "messages": []
    }

    # 获取所有 Takeout 的键，用于模糊搜索
    takeout_keys_cache = list(takeout_index.keys())

    # 用于时间戳单调性检查
    last_valid_dt = None

    # 4. 遍历合并
    for i, turn in enumerate(voyager_data['turns']):
        user_txt = turn.get('user_text', '')
        assistant_txt = turn.get('assistant_text', '')
        
        # --- 查找 Takeout 匹配 ---
        matched_entry = None
        target_key = None

        # --- 1. 尝试精确查找 ---
        if user_txt in takeout_index:
            target_key = user_txt
        else:
            # --- 2. 尝试模糊查找 ---
            print(f"🔄 Turn {i+1}: 精确匹配失败，尝试模糊查找: '{user_txt[:10]}...'")
            fuzzy_key = fuzzy_find_key(user_txt, takeout_keys_cache)
            if fuzzy_key:
                print(f"   ✅ 模糊匹配成功: '{fuzzy_key[:20]}...'")
                target_key = fuzzy_key
            else:
                print(f"   ❌ 模糊匹配失败，无法找到对应的时间戳。")

        # --- 3. 获取并消歧义 ---
        if target_key:
            entries_list = takeout_index[target_key]
            
            # 使用 Assistant 内容进行消歧义
            matched_entry = disambiguate_entries(entries_list, assistant_txt)
            
            # 重要: 匹配完后，从索引列表中移除该条目，防止下一轮重复匹配
            # (因为 disambiguate 返回的是引用，我们需要在 list 中找到它并移除)
            if matched_entry in entries_list:
                entries_list.remove(matched_entry)
                # 如果该 key 下空了，甚至可以 del takeout_index[target_key]

        # --- 构建 JSON 逻辑 ---

        # 获取关键数据
        timestamp = datetime.fromisoformat(matched_entry['time']) if matched_entry else None
        # fromisoformat会帮我们检查matched_entry['time']拿到的是不是合法的时间格式
        raw_html = None
        if matched_entry and 'safeHtmlItem' in matched_entry:
             # Takeout 的 HTML 藏在 safeHtmlItem 列表里
             if len(matched_entry['safeHtmlItem']) > 0:
                 raw_html = matched_entry['safeHtmlItem'][0].get('html')

        # 检查根据对话顺序，时间戳时间是否是单调递增的，如不是则报错
        if timestamp and last_valid_dt:
            if timestamp < last_valid_dt:
                print(f"❌ Error: Turn {i+1}: 时间戳不是单调递增的，前一个时间戳是 {last_valid_dt}，当前时间戳是 {timestamp}")
                raise ValueError("时间戳非单调递增，归档终止。")
            last_valid_dt = timestamp

        elif timestamp and not last_valid_dt: # 此时应当是第一轮，i=0
            last_valid_dt = timestamp
            master_json['meta']['created_at'] = timestamp.isoformat(timespec='milliseconds')
            if i != 0: # 相当罕见情况，即前几轮都没时间戳
                print(f"⚠️ 警告: Turn {i+1}: 发现时间戳，但前几轮没有时间戳，将使用当前时间戳作为对话创建时间")

        else: # takeout阶段未匹配到相关条目，则不设置时间戳
            pass

        # --- 构建 User 消息 ---
        msg_user_id = generate_uuidv7(timestamp if timestamp else datetime.now(timezone.utc))
        # TODO: 如果timestamp缺失时，应如何处理uuid
        msg_user = {
            "id": msg_user_id,
            "role": "user",
            "created_at": timestamp.isoformat(timespec='milliseconds') if timestamp else None, # 只有匹配到了才有时间
            "content": {
                # 如果是 User，取 Takeout 的 Prompt (去掉前缀)
                "text": clean_takeout_prompt(matched_entry['title']) if matched_entry else user_txt
            }
        }
        master_json['messages'].append(msg_user)

        # --- 构建 Assistant 消息 ---
        msg_assistant_id = generate_uuidv7(timestamp + timedelta(seconds=20) if timestamp else datetime.now(timezone.utc))
        msg_assistant = {
            "id": msg_assistant_id,
            "role": "assistant",
            "parent_id": msg_user_id, # 关联父消息
            "created_at": None, # 身份为 Assistant，时无时间信息
            "content": {
                "text": assistant_txt, # Voyager 的 MD
                "original_html": raw_html # Takeout 的 HTML
            }
        }
        master_json['messages'].append(msg_assistant)

    # 5. 输出
    with open(OUTPUT_FILE_PATH, 'w', encoding='utf-8') as f:
        json.dump(master_json, f, indent=2, ensure_ascii=False)
    
    print(f"✅ 成功生成 Master JSON: {OUTPUT_FILE_PATH}")

if __name__ == "__main__":
    main()