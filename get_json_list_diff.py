import json

def get_json_list_diff(list_a, list_b, key="time"):
    """
    计算两个 JSON 对象列表的差集 (list_a - list_b)
    
    参数:
        list_a (list): 被减数列表 (保留这里面独有的元素)
        list_b (list): 减数列表 (用于排除的元素)
        key (str): 唯一键的字段名，默认为 "time"
        
    返回:
        list: 存在于 list_a 但不存在于 list_b 中的对象列表
    """
    # 1. 将 list_b 的所有的 time 提取到一个 Set 中
    # Set 的查找时间复杂度是 O(1)，这对于几千条数据非常关键
    b_keys = {item[key] for item in list_b if key in item}
    
    # 2. 遍历 list_a，保留 time 不在 b_keys 中的对象
    # 列表推导式不仅简洁，且执行速度比原生 for 循环 append 更快
    diff_list = [item for item in list_a if item.get(key) not in b_keys]
    
    return diff_list



import os
import time

# ================= 配置区 =================
# 输入：被减数文件（包含较多数据的文件）
INPUT_FILE_A = r'data\takeout_diff\takeout_0129.json' 
# 输入：减数文件（需要从 A 中剔除的数据）
INPUT_FILE_B = r'data\takeout_diff\takeout_0210.json'
# 输出：结果文件
OUTPUT_FILE = r'data\takeout_diff\diff_result.json'
# 唯一键字段名
UNIQUE_KEY = 'time'
# ==========================================


def main():
    start_time = time.perf_counter()

    # 1. 检查文件是否存在
    if not os.path.exists(INPUT_FILE_A) or not os.path.exists(INPUT_FILE_B):
        print(f"❌ 错误：找不到输入文件。请检查 {INPUT_FILE_A} 和 {INPUT_FILE_B}")
        return

    try:
        # 2. 加载数据
        print(f"📂 正在加载文件...")
        with open(INPUT_FILE_A, 'r', encoding='utf-8') as f:
            data_a = json.load(f)
        with open(INPUT_FILE_B, 'r', encoding='utf-8') as f:
            data_b = json.load(f)

        print(f"📊 数据规模 -> A: {len(data_a)} 条, B: {len(data_b)} 条")

        # 3. 执行差集运算
        diff_result = get_json_list_diff(data_a, data_b, key=UNIQUE_KEY)

        # 4. 保存结果
        print(f"💾 正在写入结果至: {OUTPUT_FILE}")
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            # ensure_ascii=False 保证中文和特殊字符（如 JSON 里的 HTML）不被转义
            json.dump(diff_result, f, ensure_ascii=False, indent=4)

        # 5. 打印统计信息
        end_time = time.perf_counter()
        print("-" * 30)
        print(f"✅ 处理完成！")
        print(f"✨ 差集数量: {len(diff_result)} 条")
        print(f"⏱️ 消耗时间: {(end_time - start_time):.4f} 秒")

    except json.JSONDecodeError as e:
        print(f"❌ 错误：JSON 格式解析失败 - {e}")
    except Exception as e:
        print(f"❌ 发生意外错误: {e}")

if __name__ == "__main__":
    main()