---
name: amap
description: 用高德地图规划路线和行程：驾车/步行/公交/骑行、从A到B怎么走、途经点、城市一日游与景点串联。用户问路线、导航、怎么走、通勤、行程规划、一日游、景点路线、出行方案时使用。
entrypoint: scripts/plan_trip.py
---

# 高德行程规划

## 何时使用

用户询问怎么走、路线、导航、通勤、行程规划、一日游、景点串联，或给出起点/终点时使用本 Skill。

## 如何调用

参数必须作为独立字段传入，不要放进嵌套对象：

```text
run_skill(name="amap", origin="北京南站", destination="故宫", mode="公交")
run_skill(name="amap", origin="上海虹桥站", destination="外滩", mode="驾车", waypoints="静安寺")
run_skill(name="amap", city="杭州", keywords="西湖", days="1")
```

- 从 A 到 B：必填 `origin`、`destination`。一句话里前面查了别的城市天气，也不要把那个城市传给 `city`。
- 途经点：`waypoints` 用中文逗号或英文逗号分隔。
- 城市行程/一日游：填 `city`，可选 `keywords`（景点、美食等）和 `days`（1–3）。
- `上海` 与 `上海市` 视为同一城市，不要换写法再查一次。

## 约束

- 必须使用脚本返回的路线或行程，禁止编造耗时、距离、换乘或景点。
- 起点终点都不明确、也没有城市时，先问用户从哪到哪，不要猜测，不要再 load_skill。
- 脚本报错时，把错误原因如实告诉用户，不要用相同参数重试。
- 不要把高德 Key 写进对用户的回复。
