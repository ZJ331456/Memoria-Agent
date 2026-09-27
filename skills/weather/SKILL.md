---
name: weather
description: 查询当前天气与简要预报（无需 API Key）。触发词：天气, weather, 气温, 下雨, forecast, 穿什么
triggers: 天气, weather, 气温, 下雨, 预报, forecast, 穿衣
metadata: {"memoria":{"emoji":"🌤️"}}
---

# Weather（Memoria 适配）

改编自 akashic `weather` skill。使用只读工具 `http_get`，不要假装调用了 shell。

## 首选：wttr.in

城市名空格改 `+`：

- 一行：`https://wttr.in/Shanghai?format=3`
- 紧凑：`https://wttr.in/Shanghai?format=%l:+%c+%t+%h+%w`
- 今日：`https://wttr.in/Shanghai?1`
- 公制：加 `?m`

示例：

```
http_get url="https://wttr.in/Beijing?format=3"
```

## 回退：Open-Meteo

先根据常识或用户给出的坐标查询：

```
https://api.open-meteo.com/v1/forecast?latitude=39.9&longitude=116.4&current_weather=true
```

## 回复要求

- 先给地点、当前气温/体感、天气现象
- 再补 1 句穿衣或出行建议
- 拉取失败时明确说数据源不可用，不要编造气温
