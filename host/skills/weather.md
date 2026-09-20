---
name: weather
triggers: 天气,weather,下雨,气温
tool: cloud
prompt: 查询指定城市天气，只回一句话，给出温度与是否需要带伞。
---
# 天气 Skill

示例自定义 Skill。触发词命中后，板端会把请求连同本提示词一起上行云端，
由云端大模型完成理解与生成，板端只负责呈现与播报。

- 触发示例：「北京今天天气怎么样」「出门要带伞吗」
- 板端行为：`skill_match()` 命中 → 发送 `{"skill":"weather","prompt":...,"q":...}`
  → 云端返回 → 回板显示/播报
