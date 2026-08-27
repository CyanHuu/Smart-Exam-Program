import shutil
from pathlib import Path

import xlrd
from xlutils.copy import copy


ROOT = Path(r"D:\AI\人工智能项目\智能排考小程序")
SOURCE_DIR = ROOT / "测试文件"
OUTPUT_DIR = ROOT / "测试文件"

room_source = SOURCE_DIR / "1.教室编号输入.xls"
teacher_source = SOURCE_DIR / "2.老师名单.xls"
template_source = SOURCE_DIR / "3.目标写入表格.xls"

room_output = OUTPUT_DIR / "最终测试_1.教室编号输入.xls"
teacher_output = OUTPUT_DIR / "最终测试_2.老师名单.xls"
template_output = OUTPUT_DIR / "最终测试_3.目标写入表格.xls"

# 保留原教师列名和原始格式，并补充60名备用教师，保证每个考场都有1—2名备选。
teacher_book = xlrd.open_workbook(str(teacher_source), formatting_info=True)
teacher_copy = copy(teacher_book)
teacher_sheet = teacher_copy.get_sheet(0)
teacher_source_sheet = teacher_book.sheet_by_index(0)
last_data_row = max(
    row_index
    for row_index in range(1, teacher_source_sheet.nrows)
    if str(teacher_source_sheet.cell_value(row_index, 0)).strip()
)
# 测试数据使用唯一姓名，避免仅凭姓名查看时产生歧义；工号仍然保留原始编号。
for row_index in range(1, last_data_row + 1):
    if str(teacher_source_sheet.cell_value(row_index, 0)).strip():
        teacher_sheet.write(row_index, 1, f"监考教师{row_index:03d}")
for offset in range(60):
    row_index = last_data_row + 1 + offset
    teacher_sheet.write(row_index, 0, f"B{offset + 1:03d}")
    teacher_sheet.write(row_index, 1, f"备用教师{offset + 1:02d}")
    teacher_sheet.write(row_index, 2, f"1390000{offset + 100:04d}")
    teacher_sheet.write(row_index, 3, offset % 2)
    teacher_sheet.write(row_index, 4, 1 if offset % 3 == 0 else 0)
    teacher_sheet.write(row_index, 5, "备用部门")
teacher_copy.save(str(teacher_output))
shutil.copyfile(template_source, template_output)

# 保留原考场表格式，生成4人/2人混合需求。
# 前12个考场需要4人，后15个考场需要2人，共需78人；
# 原教师表有75名教师，预期缺口为3且绝不重复分配。
book = xlrd.open_workbook(str(room_source), formatting_info=True)
copy_book = copy(book)
sheet = copy_book.get_sheet(0)
source_sheet = book.sheet_by_index(0)
for row_index in range(1, source_sheet.nrows):
    sheet.write(row_index, 1, 4 if row_index <= 12 else 2)
copy_book.save(str(room_output))

print(room_output)
print(teacher_output)
print(template_output)
