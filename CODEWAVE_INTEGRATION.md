# CodeWave 对接指南

## 1. 页面和调用顺序

1. **数据导入**：上传教室、教师、考试模板，调用 `POST /api/v1/datasets/import`。
2. **AI 规则助手**：把用户文本交给 CodeWave 大模型连接器，将返回的 JSON 显示成可编辑规则卡片。
3. **确认规则**：调用 `POST /api/v1/policies/validate`；`valid=false` 时禁用排考按钮。
4. **智能排考**：调用 `POST /api/v1/schedules/solve`，将 `comparison` 绑定到优化前后指标卡，将 `optimised` 绑定到表格/时间轴。
5. **临时请假**：选择教师后调用 `POST /api/v1/schedules/replan`，用 `changes` 展示替换前后差异。
6. **导出**：向 `POST /api/v1/schedules/export` 传入最新 `schedule_id`，下载保留原格式的 `.xls`。

CodeWave 中创建一个 REST 连接器，服务地址使用云端 HTTPS 域名，请求头统一配置 `X-API-Key`。不要在页面变量中保存密钥。后端自带 OpenAPI 说明：`https://<域名>/docs`。

## 2. 大模型系统提示词

```text
你是监考规则解析器，不是排考器。你不得生成或猜测教师安排。
只能输出一个 JSON 对象，不要输出 Markdown、注释或额外文字。
只允许以下字段：
experience_weight, gender_weight, department_weight, fairness_weight,
consecutive_penalty, stability_weight, backup_count, max_formal_count,
consecutive_gap_minutes, time_limit_seconds, random_seed,
unavailable, avoid_rooms, allow_consecutive。
unavailable 格式为 {教师工号: [场次ID]}；avoid_rooms 为 {教师工号: [考场ID]}；
allow_consecutive 为 {教师工号: true/false}。
没有被用户修改的字段使用默认值：60,25,15,100,20,100,2,null,120,20,7,{},{},{}。
只能使用上下文提供的教师工号、场次ID和考场ID。
如果同名教师无法唯一确定，输出 {"clarification_required": true, "message": "..."}，不得自行选择。
```

> 将导入接口返回的 `teachers` 和 `sessions` 作为上下文；不要向模型发送联系方式、完整 Excel 或已排人员表。

## 3. 请求示例

```json
{
  "dataset_id": "<导入接口返回值>",
  "policy": {
    "experience_weight": 60,
    "gender_weight": 25,
    "department_weight": 15,
    "fairness_weight": 100,
    "consecutive_penalty": 20,
    "stability_weight": 100,
    "backup_count": 2,
    "max_formal_count": 3,
    "consecutive_gap_minutes": 120,
    "time_limit_seconds": 20,
    "random_seed": 7,
    "unavailable": {"01395": ["01"]},
    "avoid_rooms": {},
    "allow_consecutive": {"01395": false}
  }
}
```

## 4. 部署

```powershell
docker build -t smart-exam-api .
docker run --rm -p 8000:8000 `
  -e SCHEDULER_API_KEY="替换为随机密钥" `
  -e CORS_ORIGINS="https://<CodeWave应用域名>" `
  smart-exam-api
```

健康检查为 `GET /health`。上传数据和排考结果默认 24 小时后在下一次请求时清理。
