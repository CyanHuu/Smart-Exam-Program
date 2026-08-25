import pandas as pd
import os


def extract_exam_data_from_excel(file_path,head_num):
    """
    从Excel文件读取考试表格数据，提取序号、考场号、教室编号
    :param file_path: Excel文件路径
    :head_num : 指定列民在第几行
    :return: 提取的数据列表
    """
    # 确定文件格式并选择正确的引擎
    file_ext = os.path.splitext(file_path)[1].lower()

    # 根据文件扩展名选择合适的引擎
    if file_ext == '.xls':
        engine = 'xlrd'
    elif file_ext == '.xlsx':
        engine = 'openpyxl'
    else:
        raise ValueError(f"不支持的文件格式: {file_ext}. 请使用.xls或.xlsx文件")

    # 读取Excel文件，指定引擎和列类型
    df = pd.read_excel(
        file_path,
        header=head_num,
        dtype=str,
        engine=engine
    )

    # 清理列名：移除空格和特殊字符
    df.columns = [col.strip().replace(' ', '') for col in df.columns]

    # 查找包含关键字段的列
    seq_col = next((col for col in df.columns if '序号' in col), None)
    room_col = next((col for col in df.columns if '考场号' in col), None)
    classroom_col = next((col for col in df.columns if '教室编号' in col), None)

    # 检查关键列是否存在
    if not all([seq_col, room_col, classroom_col]):
        missing_cols = []
        if not seq_col: missing_cols.append('序号')
        if not room_col: missing_cols.append('考场号')
        if not classroom_col: missing_cols.append('教室编号')
        raise ValueError(f"Excel文件缺少必要列: {', '.join(missing_cols)}")

    # 提取所需列
    result = []
    for _, row in df.iterrows():
        seq = str(row[seq_col]) if pd.notna(row[seq_col]) else ''
        room = str(row[room_col]) if pd.notna(row[room_col]) else ''  # 作为字符串处理
        classroom = str(row[classroom_col]) if pd.notna(row[classroom_col]) else ''
        result.append((seq, room, classroom))

    # 处理合并单元格：用前一个非空值填充教室编号
    # 创建临时DataFrame用于处理
    temp_df = df[[classroom_col]].copy()
    temp_df[classroom_col] = temp_df[classroom_col].fillna(method='ffill')

    # 重新提取教室编号（已处理合并单元格）
    for idx, row in df.iterrows():
        classroom = str(temp_df.at[idx, classroom_col]) if pd.notna(temp_df.at[idx, classroom_col]) else ''
        result[idx] = (result[idx][0], result[idx][1], classroom)

    # 统计非空数量
    non_empty_rooms = sum(1 for _, room, _ in result if room != '')
    non_empty_classrooms = sum(1 for _, _, cls in result if cls != '')

    # 检查重复教室编号（同一教室被多个考场号使用）
    classroom_mapping = {}
    for _, room, cls in result:
        if cls != '':  # 跳过空教室编号
            if cls not in classroom_mapping:
                classroom_mapping[cls] = []
            classroom_mapping[cls].append(room)

    # 识别重复情况：同一教室被多个考场号使用
    repeated_classrooms = {}
    for cls, rooms in classroom_mapping.items():
        if len(rooms) > 1:
            repeated_classrooms[cls] = rooms

    # 创建统计摘要
    stats = {
        '总记录数': len(result),
        '考场号非空数量': non_empty_rooms,
        '教室编号非空数量': non_empty_classrooms,
        '重复教室数量': len(repeated_classrooms),
        '重复教室详情': repeated_classrooms
    }

    return result,stats


def main():
    # 替换为你的Excel文件路径
    file_path = '/Users/jiangye/Documents/小程序相关/教室编号输入.xls'  # 请修改为你的实际文件路径

    try:
        data,stats = extract_exam_data_from_excel(file_path,head_num=2)

        print("=" * 50)
        print("提取的考试数据 (前30条用于核对):")
        print("=" * 50)
        for i, (seq, room, cls) in enumerate(data[:30], 1):
            print(f"记录 {i}: 序号={seq}, 考场号={room}, 教室编号={cls}")

        print(f"总记录数: {stats['总记录数']}")
        print(f"考场号非空数量: {stats['考场号非空数量']}")
        print(f"教室编号非空数量: {stats['教室编号非空数量']}")
        print(f"重复教室数量: {stats['重复教室数量']}")


    except FileNotFoundError:
        print(f"错误: 文件 {file_path} 未找到，请检查文件路径")
    except ValueError as e:
        print(f"数据错误: {str(e)}")
    except Exception as e:
        print(f"处理文件时出错: {str(e)}")


if __name__ == "__main__":
    main()