import pandas as pd
import numpy as np
import os
import sys


def analyze_teacher_list(file_path):
    """
    分析老师名单Excel文件，输出每一列的统计信息
    :param file_path: 老师名单Excel文件路径
    :return: None (直接打印统计结果)
    """
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


    # 读取老师信息
    try:
        teacher_df = pd.read_excel(file_path,dtype= str)
    except Exception as e:
        raise ValueError(f"读取老师文件失败: {str(e)}")

    # 确定列名
    id_col = None
    name_col = None
    gender_col = None
    experience_col = None
    dept_col = None

    for col in teacher_df.columns:
        if '工号' in col or 'id' in col.lower() or 'number' in col.lower():
            id_col = col
            teacher_df['id_col'] = teacher_df[col]
        if '姓名' in col or 'name' in col.lower():
            name_col = col
            teacher_df['name_col'] = teacher_df[col]
        if '性别' in col or 'gender' in col.lower():
            gender_col = col
            teacher_df['gender_col'] = teacher_df[col]
        if '经验' in col or 'experience' in col.lower() or '有经验' in col:
            experience_col = col
            teacher_df['experience_col'] = teacher_df[col]
        if '部门' in col or 'department' in col.lower() or 'dept' in col.lower():
            dept_col = col
            teacher_df['dept_col'] = teacher_df[col]

    if not all([id_col, name_col, gender_col, experience_col, dept_col]):
        missing = [col for col in [id_col, name_col, gender_col, experience_col, dept_col] if not col]
        raise ValueError(f"老师信息文件缺少必要列: {', '.join(missing)}")

    # 验证性别列（必须是"女"或"男"）
    if teacher_df['gender_col'].isin(['0', '1']).all():

        teacher_df['gender_col'] = teacher_df['gender_col'].astype(int)
    else:
        raise ValueError("性别列必须是0或1，不能包含其他值")

    # 验证经验列（必须是0或1）
    if teacher_df['experience_col'].isin(['0', '1']).all():
        # 确保经验列是数值类型
        teacher_df['experience_col'] = teacher_df['experience_col'].astype(int)
    else:
        raise ValueError("经验列必须是0或1（0表示无经验，1表示有经验）")

    # === 工号验证：确保不缺失且唯一 ===
    # 检查工号缺失
    if teacher_df['id_col'].isnull().any():
        missing_rows = teacher_df.index[teacher_df['id_col'].isnull()].tolist()
        # 限制显示前5个缺失行
        if len(missing_rows) > 5:
            missing_rows = missing_rows[:5] + ['...']
        raise ValueError(f"工号列存在缺失值（行号: {missing_rows+1}）")

    # 检查工号唯一性
    if teacher_df['id_col'].duplicated().any():
        duplicate_ids = teacher_df[teacher_df['id_col'].duplicated(keep=False)]['id_col'].unique()
        # 限制显示前5个重复工号
        if len(duplicate_ids) > 5:
            duplicate_ids = duplicate_ids[:5] + ['...']
        raise ValueError(f"工号列存在重复值: {', '.join(map(str, duplicate_ids))}")

    teacher_df['id_col'] = teacher_df['id_col'].astype(str)

    return teacher_df


def main():
    # 替换为你的老师名单Excel文件路径
    file_path = '/Users/jiangye/Documents/小程序相关/2.老师名单.xls'  # 请修改为你的实际文件路径

    # 如果命令行参数提供了文件路径，则使用命令行参数
    if len(sys.argv) > 1:
        file_path = sys.argv[1]

    data = analyze_teacher_list(file_path)
    print(data)


if __name__ == "__main__":
    main()