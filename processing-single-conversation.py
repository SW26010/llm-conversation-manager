import json
import re
import hashlib
from rapidfuzz import fuzz
from datetime import datetime, timedelta
from typing import Tuple, Dict, Any
from collections import defaultdict

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
        # print(f"ℹ️ Info: 跳过非对话类活动: '{title[:30]}...'")
        pass
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

# 识别 voyager 中某条信息的附件数量
def count_attachments(user_text):
    """
    解析 user_text 中的附件标记并返回数量
    """
    # 提取所有非空行
    lines = [line.strip() for line in user_text.splitlines() if line.strip()]
    
    # 校验首行内容是否符合协议标识
    if not lines or lines[0] != "*[This turn includes uploaded images]*":
        return 0
    
    # 统计符合 ![description](url) 模式的行数
    # ^ 匹配行首，$ 匹配行尾，确保整行符合 Markdown 图片语法
    attachments = re.findall(r'^!\[.*?\]\(.*?\)$', user_text, re.MULTILINE)
    
    return len(attachments)


# 识别 takeout 中的附件
def extract_attachments(matched_entry: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    从 matched_entry 中提取附件列表并校验数量。
    """
    # 1. 获取 subtitles 字段，若无则直接返回空列表
    subtitles = matched_entry.get("subtitles")
    if not subtitles:
        return []

    # 2. 解析索引 0 的描述信息获取预期数量
    # 格式示例: "Attached 4 files." 或 "Attached 1 file."
    header_text = subtitles[0].get("name", "")
    match = re.search(r"Attached (\d+) file", header_text)
    
    # 若无法匹配数字，默认预期为 0 (或根据需求抛出异常，此处保持简洁设为 0)
    expected_count = int(match.group(1)) if match else 0

    # 3. 提取实际附件 (从索引 1 开始)
    attachments = subtitles[1:]

    # 4. 校验数量是否一致 (若不一致将中断程序，提示不匹配)
    if len(attachments) != expected_count:
        raise ValueError(f"附件数量校验失败: 标称 {expected_count}, 实际 {len(attachments)}")

    # 对附件json对象和文件名的检查，保留真正能用的文件名，其它要不要都无所谓
    for attachment in attachments:
        if attachment["name"].startswith("-  "):
            attachment["name"] = attachment["name"][3:]
        else:
            raise ValueError(f"附件名称校验失败: {attachment['name']}")
    
    return attachments


def load_takeout_index(json_path: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:

    """
    加载 Takeout JSON 并建立两套索引
    Key: 用户 Prompt (文本) (用于O1复杂度哈希查找)
    Key2: 助理回复 (文本) (用于ON复杂度模糊查找)
    -> Value: 完整 Entry (包含时间, HTML)
    """
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    user_index: Dict[str, Any] = {}
    assistant_index: Dict[str, Any] = {}

    # 计数器：记录每个 clean_title 出现的次数
    prompt_counts: Dict[str, int] = defaultdict(int)

    for entry in data:
        
        # 建立用户索引
        clean_title = clean_takeout_prompt(entry['title'])
        
        # 计数器：记录每个 clean_title 出现的次数
        prompt_counts[clean_title] += 1
        current_count = prompt_counts[clean_title]
        
        if current_count == 1:  # 首次出现：正常占位
            user_index[clean_title] = entry
        elif current_count == 2:  # 第二次出现：触发“迁移逻辑”
            # 1. 取出之前占位的第一条数据
            first_entry = user_index.pop(clean_title)
            # 2. 为第一条数据添加后缀 _0
            user_index[f"{clean_title}_0"] = first_entry
            # 3. 为当前（第二条）数据添加后缀 _1
            user_index[f"{clean_title}_1"] = entry
            # 注意：此时 user_index[clean_title] 已不存在

        else:  # 第三次及以后：直接添加后缀
            # current_count 为 3 时，对应后缀 _2
            user_index[f"{clean_title}_{current_count - 1}"] = entry

        try:  # 建立助理索引
            assistant_text = entry['safeHtmlItem'][0]['html']
            assistant_index[assistant_text] = entry

        except Exception as e:
            # 抓住不存在条目错误
            # print(f"错误: 不存在的条目 {entry}: {e}")
            continue

    print(f"索引构建完成 (总记录: {len(data)})：\n"
          f" - 用户索引 (User Prompts): {len(user_index)} 条\n"
          f" - 助理索引 (Assistant Replies): {len(assistant_index)} 条")

    return user_index, assistant_index


# ================== 模糊查找相关逻辑 ==================

def get_clean_segments(text):
    """
    根据定义的规则列表切割文本，并按长度降序排列。
    """
    if not text:
        return []

    # 1. 定义切割规则列表（每一项都是一个正则片段）
    # 相比单行长字符串，列表形式更易读、易维护
    split_rules = [
        r'\n',       # 换行符
        r'\*\*',       # 加粗**号
        r'#',        # 标题#号
        r'- ',        # 短横线，无序列表
        r'---',      # 短横线，分隔线
        r'`',        # 数据变量或代码块
        r'\|',        # 表格
    ]

    # 2. 将列表通过 "|" (逻辑或) 拼接成完整正则: pattern1|pattern2|pattern3...
    full_pattern = "|".join(split_rules)

    # 3. 执行切割
    # re.split 会同时根据上述所有规则切分
    segments = re.split(full_pattern, text)

    # 4. 处理清洗逻辑
    # 逻辑：先 strip() 去掉首尾空格，如果剩下内容不为空，则保留
    cleaned_segments = [s.strip() for s in segments if s.strip()]

    # 5. 按长度降序排序
    return sorted(cleaned_segments, key=len, reverse=True)

def fuzzy_match(query, candidates):
    """
    使用用户文本片段对候选键进行漏斗式筛选，旨在寻找唯一匹配项。
    
    Args:
        query (str): 用户输入的目标文本。
        candidates (iterable): 所有的候选键字符串列表。
        
    Returns:
        str or None: 若筛选出唯一匹配键则返回该键，否则返回 None。
    """
    # 获取排序后的有效片段 (假设外部已定义该函数)
    segments = get_clean_segments(query)
    
    for seg in segments:
        # 在当前候选集中筛选包含 seg 的项
        current_matches = [k for k in candidates if seg in k]
        
        # 情况 A: 筛选后为空 -> 说明 seg 可能是噪音或错别字
        # 策略: 忽略当前 seg，保持原有 candidates，继续尝试下一个 seg
        if not current_matches:
            continue
            
        # updates: 存在匹配项，更新候选集（收敛范围）
        candidates = current_matches
        
        # 情况 B: 命中唯一结果 -> 成功，提前返回
        if len(candidates) == 1:
            score = fuzz.ratio(query, candidates[0])
            print(f"模糊匹配成功: {len(seg)}/{len(candidates[0])} , Similarity Score: {score}")
            return candidates[0]
            
    # 循环结束：若仍剩余多个候选或 0 个，均视为匹配失败
    return None

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
    # 1. 加载 Takeout 索引
    user_index, assistant_index = load_takeout_index(TAKEOUT_FILE_PATH)

    # 2. 读取并解析 MD
    try:
        with open(MD_FILE_PATH, 'r', encoding='utf-8') as f:
            md_content = f.read()
    except FileNotFoundError:
        print(f"错误: 找不到文件 {MD_FILE_PATH}")
        return

    voyager_data = parse_voyager_md(md_content)
    print(f"解析 MD 成功: 标题 '{voyager_data['meta'].get('title')}', 共 {len(voyager_data['turns'])} 轮对话")

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

    # 获取所有 Takeout 的assistant键，用于模糊搜索
    assistant_keys_cache = list(assistant_index.keys())

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
        # 优先user_txt精确查找，因为它的格式几乎不变，且字典查找复杂度O(N)
        if user_txt in user_index:
            matched_entry = user_index[user_txt]
        else:
            # 当精确查找无结果/多结果时，直接扔给assistant_txt的模糊查找
            # assistant_txt长度长，结构多，格式不一致，适合模糊查找
            # --- 2. 尝试模糊查找 ---
            print(f"🔄 Turn {i+1}: 用户提示词 '{user_txt[:10]}...'精确匹配失败，尝试助手回复 '{assistant_txt[:10]}...'模糊查找")
            fuzzy_key = fuzzy_match(assistant_txt, assistant_keys_cache)
            if fuzzy_key:
                print(f"   ✅ 模糊匹配成功: '{fuzzy_key[:20]}...'")
                matched_entry = assistant_index[fuzzy_key]
            else:
                print(f"   ❌ 模糊匹配失败，无法找到对应的时间戳。")

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
                #raise ValueError("时间戳非单调递增，归档终止。")
            last_valid_dt = timestamp

        elif timestamp and not last_valid_dt: # 此时应当是第一轮，i=0
            last_valid_dt = timestamp
            master_json['meta']['created_at'] = timestamp.isoformat(timespec='milliseconds')
            if i != 0: # 相当罕见情况，即前几轮都没时间戳
                print(f"⚠️ 警告: Turn {i+1}: 发现时间戳，但前几轮没有时间戳，将使用当前时间戳作为对话创建时间")

        else: # takeout阶段未匹配到相关条目，则不设置时间戳
            pass

        # ---------------- 对用户信息的附件检查 -----------------
        voyager_attachment_count = count_attachments(user_txt)
        takeout_attachments = extract_attachments(matched_entry)

        if voyager_attachment_count > 0 or takeout_attachments:
            print(f"存在附件，voyager数量为{voyager_attachment_count}，takeout数量为{len(takeout_attachments)}")
            if voyager_attachment_count != len(takeout_attachments):
                raise ValueError("附件数量不匹配")

        # --- 构建 User 消息 ---
        msg_user_id = generate_uuidv7(timestamp if timestamp else datetime.now(timezone.utc))
        msg_user = {
            "id": msg_user_id,
            "role": "user",
            "created_at": timestamp.isoformat(timespec='milliseconds') if timestamp else None, # 只有匹配到了才有时间
            "content": {
                # 如果是 User，取 Takeout 的 Prompt (去掉前缀)
                "text": clean_takeout_prompt(matched_entry['title']) if matched_entry else user_txt
            }
        }
        if takeout_attachments: msg_user["content"]["attachments"] = takeout_attachments
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

'''
这是目前我优化后的代码。现在我的重点工作是对附件格式的支持。
并且在主逻辑的构建信息环节，无论是否有无附件，对user_text的处理应当是一致的，也可以复用
还有新的对是否存在附件检测的逻辑。。。。
我发现我应当重构代码为函数，尤其是主实现逻辑main，否则后期的维护将越来越困难。
所以请告诉我，我应当如何设置各个函数（函数名，输入输出，功能等，不需要具体的代码），以及现有哪些函数可以保留，或需要调整。
'''