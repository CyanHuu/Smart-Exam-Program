# Smart-Exam-Program
人工智能大赛

## 近期功能改良

- 支持读取多场次考试安排模板，并按考试日期和时间段避免教师时间冲突。
- 每个考场自动生成正式监考和 1～2 名备选监考，支持人员增删、更换及冲突提示。
- 增加教师工作量统计，可查看工号、正式监考、备选待命、总任务量及具体场次/考场。
- 教师工作量窗口支持按姓名、工号、场次或考场搜索，也可切换显示无任务教师。
- 主表和分组表导出支持监考人员自动换行，并统一列宽、行高，改善打印可读性。
- 运行日志按场次分块展示排考结果、缺口、规则满足情况和备选人数，窗口可自适应调整。

## AI 全局排考 API

新版使用 OR-Tools 对全部场次做统一约束优化，并通过 FastAPI 供 CodeWave 调用。原 PyQt 桌面端保留为离线备用。

```powershell
py -3.13 -m pip install -r requirements.txt
$env:SCHEDULER_API_KEY = "dev-key"
py -3.13 -m uvicorn api_service:app --reload
```

打开 `http://127.0.0.1:8000/docs` 调试接口。CodeWave 页面、大模型提示词和调用顺序见 [CODEWAVE_INTEGRATION.md](CODEWAVE_INTEGRATION.md)。
