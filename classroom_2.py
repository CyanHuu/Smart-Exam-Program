import pandas as pd
import numpy as np
import os


def get_classroom_info(file_path):
    """从Excel文件中提取教室编号和监考人数，验证监考人数为正整数"""
    # 检查文件是否存在
    if not os.path.exists(file_path):
        raise TypeError(f"错误: 文件 {file_path} 未找到，请检查文件路径")

    # 确定文件格式并选择正确的引擎
    file_ext = os.path.splitext(file_path)[1].lower()

    # 根据文件扩展名选择合适的引擎
    if file_ext == '.xls':
        engine = 'xlrd'
    elif file_ext == '.xlsx':
        engine = 'openpyxl'
    else:
        raise TypeError(f"错误: 不支持的文件格式 {file_ext}. 请使用.xls或.xlsx文件")
    # 读取Excel文件（自动处理编码问题）
    try:
        df = pd.read_excel(file_path)
    except Exception as e:
        raise ValueError(f"读取教室信息文件失败: {str(e)}")

    # 确定列名
    classroom_col = None
    num_col = None

    # 尝试匹配列名
    for col in df.columns:
        if '教室编号' in col or 'room' in col.lower() or 'classroom' in col.lower():
            classroom_col = col
        if '监考人数' in col or 'count' in col.lower() or 'proctor' in col.lower():
            num_col = col

    if not classroom_col or not num_col:
        raise ValueError("教室信息文件缺少必要列（教室编号或监考人数）")

    # 验证监考人数
    classroom_data = []
    for _, row in df.iterrows():
        classroom_id = str(row[classroom_col]).strip()
        num_str = str(row[num_col]).strip()

        # 检查监考人数是否为空
        if not num_str or num_str == 'nan':
            raise ValueError(f"教室 {classroom_id} 的监考人数为空")

        # 验证是否为正整数
        try:
            num = int(num_str)
            if num <= 0:
                raise ValueError("监考人数必须是正整数")
            classroom_data.append((classroom_id, num))
        except ValueError:
            raise ValueError(f"教室 {classroom_id} 的监考人数 '{num_str}' 无效，必须是正整数")

    if not classroom_data:
        raise ValueError("未找到有效的教室信息")

    return classroom_data


def main():
    # 替换为你的Excel文件路径
    file_path = '/Users/jiangye/Documents/小程序相关/教室编号输入.xls'  # 请修改为你的实际文件路径

    try:
        data = get_classroom_info(file_path)
        print(data)
    except FileNotFoundError:
        print(f"错误: 文件 {file_path} 未找到，请检查文件路径")
    except ValueError as e:
        print(f"数据错误: {str(e)}")
    except Exception as e:
        print(f"处理文件时出错: {str(e)}")


if __name__ == "__main__":
    main()