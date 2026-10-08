# Weather：天气查询

[技能目录](../README.md) · [运行时指令](SKILL.md) · [HTTP 工具](../../memoria/tools/http_get.py)

用于查询当前天气和简短预报，触发词包括“天气”“气温”“下雨”、`weather` 和 `forecast`。该技能通过 `http_get` 获取文本，不执行 shell，也不自带天气数据。

## 数据流程

- 首选 wttr.in，按用户指定城市请求一行或紧凑预报。
- 必要时使用 Open-Meteo；需要可信的经纬度输入，当前没有自动地理编码工具。
- 输出地点、温度与天气状况，再给简短建议；数据不可用时明确说明获取失败。

默认 `[agent.tools].http_allowed_hosts` 包含 `wttr.in`、`api.open-meteo.com` 和 `open-meteo.com`。数据源仍依赖网络、服务可用性和当前接口；“无需 API Key”不表示离线可用。

## 使用与验证

示例：“查一下上海现在的天气。”

从仓库根目录运行：

```bash
python -m pytest -q tests/test_skills.py -k 'builtin_skills_directory_present or http_host_allowlist'
```

这些测试验证目录存在与主机匹配规则，不会访问真实天气服务。
