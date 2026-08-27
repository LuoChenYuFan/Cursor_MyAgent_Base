from __future__ import annotations

import unicodedata
from typing import Any

# (规范名, 别名)。查询和缓存都归到规范名，上海 / Shanghai 算同一次。
_CITY_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("上海", ("shanghai", "shang hai", "上海", "上海市")),
    ("北京", ("beijing", "peking", "北京", "北京市", "peking city")),
    ("广州", ("guangzhou", "guang zhou", "canton", "广州", "广州市")),
    ("深圳", ("shenzhen", "shen zhen", "深圳", "深圳市")),
    ("杭州", ("hangzhou", "hang zhou", "杭州", "杭州市")),
    ("成都", ("chengdu", "cheng du", "成都", "成都市")),
    ("重庆", ("chongqing", "chong qing", "重庆", "重庆市")),
    ("天津", ("tianjin", "tian jin", "天津", "天津市")),
    ("武汉", ("wuhan", "wu han", "武汉", "武汉市")),
    ("西安", ("xi'an", "xian", "xi an", "西安", "西安市")),
    ("南京", ("nanjing", "nan jing", "南京", "南京市")),
    ("苏州", ("suzhou", "su zhou", "苏州", "苏州市")),
    ("长沙", ("changsha", "chang sha", "长沙", "长沙市")),
    ("郑州", ("zhengzhou", "zheng zhou", "郑州", "郑州市")),
    ("青岛", ("qingdao", "qing dao", "青岛", "青岛市")),
    ("大连", ("dalian", "da lian", "大连", "大连市")),
    ("宁波", ("ningbo", "ning bo", "宁波", "宁波市")),
    ("厦门", ("xiamen", "xia men", "amoy", "厦门", "厦门市")),
    ("沈阳", ("shenyang", "shen yang", "沈阳", "沈阳市")),
    ("哈尔滨", ("harbin", "haerbin", "哈尔滨", "哈尔滨市")),
    ("长春", ("changchun", "chang chun", "长春", "长春市")),
    ("济南", ("jinan", "ji nan", "济南", "济南市")),
    ("石家庄", ("shijiazhuang", "shi jia zhuang", "石家庄", "石家庄市")),
    ("太原", ("taiyuan", "tai yuan", "太原", "太原市")),
    ("合肥", ("hefei", "he fei", "合肥", "合肥市")),
    ("福州", ("fuzhou", "fu zhou", "福州", "福州市")),
    ("南昌", ("nanchang", "nan chang", "南昌", "南昌市")),
    ("南宁", ("nanning", "nan ning", "南宁", "南宁市")),
    ("昆明", ("kunming", "kun ming", "昆明", "昆明市")),
    ("贵阳", ("guiyang", "gui yang", "贵阳", "贵阳市")),
    ("海口", ("haikou", "hai kou", "海口", "海口市")),
    ("三亚", ("sanya", "san ya", "三亚", "三亚市")),
    ("兰州", ("lanzhou", "lan zhou", "兰州", "兰州市")),
    ("西宁", ("xining", "xi ning", "西宁", "西宁市")),
    ("银川", ("yinchuan", "yin chuan", "银川", "银川市")),
    ("乌鲁木齐", ("urumqi", "wulumuqi", "乌鲁木齐", "乌鲁木齐市")),
    ("拉萨", ("lhasa", "lasa", "拉萨", "拉萨市")),
    ("呼和浩特", ("hohhot", "huhehaote", "呼和浩特", "呼和浩特市")),
    ("香港", ("hong kong", "hongkong", "hk", "香港")),
    ("澳门", ("macau", "macao", "澳门")),
    ("台北", ("taipei", "tai pei", "台北", "台北市")),
    ("高雄", ("kaohsiung", "gaoxiong", "高雄", "高雄市")),
    ("无锡", ("wuxi", "wu xi", "无锡", "无锡市")),
    ("佛山", ("foshan", "fo shan", "佛山", "佛山市")),
    ("东莞", ("dongguan", "dong guan", "东莞", "东莞市")),
    ("珠海", ("zhuhai", "zhu hai", "珠海", "珠海市")),
    ("中山", ("zhongshan", "zhong shan", "中山", "中山市")),
    ("温州", ("wenzhou", "wen zhou", "温州", "温州市")),
    ("常州", ("changzhou", "chang zhou", "常州", "常州市")),
    ("烟台", ("yantai", "yan tai", "烟台", "烟台市")),
    ("桂林", ("guilin", "gui lin", "桂林", "桂林市")),
    ("丽江", ("lijiang", "li jiang", "丽江", "丽江市")),
)

_PLACE_SUFFIXES = (
    "特别行政区",
    "地区",
    "市区",
    "市",
    "省",
    "县",
    " city",
    " municipality",
    " prefecture",
)


def as_question(text: str) -> str:
    """把反问整理成一句带问号的中文。"""
    stripped = " ".join((text or "").strip().split())
    if not stripped:
        return ""
    if stripped.endswith(("？", "?")):
        return stripped
    return f"{stripped}？"


def normalize_text(value: str) -> str:
    text = unicodedata.normalize("NFKC", value or "")
    text = " ".join(text.strip().split())
    return text.casefold()


def _strip_place_suffix(text: str) -> str:
    current = text
    for suffix in _PLACE_SUFFIXES:
        if current.endswith(suffix):
            current = current[: -len(suffix)].strip()
    return current


def _build_aliases() -> dict[str, str]:
    aliases: dict[str, str] = {}
    for canonical, variants in _CITY_GROUPS:
        for variant in (*variants, canonical):
            key = normalize_text(variant)
            aliases[key] = canonical
            stripped = _strip_place_suffix(key)
            if stripped:
                aliases[stripped] = canonical
    return aliases


_CITY_ALIASES = _build_aliases()

_CITY_COUNTRIES: dict[str, str] = {name: "CN" for name, _ in _CITY_GROUPS}
_CITY_COUNTRIES.update({"香港": "HK", "澳门": "MO", "台北": "TW", "高雄": "TW"})


_WHEN_ALIASES = {
    "": "today",
    "today": "today",
    "now": "today",
    "current": "today",
    "当前": "today",
    "现在": "today",
    "今天": "today",
    "今日": "today",
    "tomorrow": "tomorrow",
    "明天": "tomorrow",
    "明日": "tomorrow",
}


_MODE_ALIASES = {
    "": "driving",
    "driving": "driving",
    "drive": "driving",
    "car": "driving",
    "驾车": "driving",
    "开车": "driving",
    "自驾": "driving",
    "walking": "walking",
    "walk": "walking",
    "步行": "walking",
    "走路": "walking",
    "徒步": "walking",
    "transit": "transit",
    "bus": "transit",
    "subway": "transit",
    "metro": "transit",
    "公交": "transit",
    "地铁": "transit",
    "公共交通": "transit",
    "换乘": "transit",
    "bicycling": "bicycling",
    "cycling": "bicycling",
    "bike": "bicycling",
    "bicycle": "bicycling",
    "骑行": "bicycling",
    "自行车": "bicycling",
    "骑车": "bicycling",
}


def canonical_mode(raw: str) -> str:
    """出行方式归一：driving / walking / transit / bicycling；无法识别则空串。"""
    text = (raw or "").strip()
    if not text:
        return "driving"
    key = normalize_text(text)
    if key in _MODE_ALIASES:
        return _MODE_ALIASES[key]
    if text in _MODE_ALIASES:
        return _MODE_ALIASES[text]
    return ""


def canonical_when(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return "today"
    key = normalize_text(text)
    if key in _WHEN_ALIASES:
        return _WHEN_ALIASES[key]
    if text in _WHEN_ALIASES:
        return _WHEN_ALIASES[text]
    return ""


def canonical_city(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    key = normalize_text(text)
    if key in _CITY_ALIASES:
        return _CITY_ALIASES[key]
    stripped = _strip_place_suffix(key)
    if stripped in _CITY_ALIASES:
        return _CITY_ALIASES[stripped]
    return text


def _city_needles() -> tuple[tuple[str, str], ...]:
    needles: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for canonical, variants in _CITY_GROUPS:
        for name in (canonical, *variants):
            compact = name.replace(" ", "").replace("'", "")
            if len(compact) < 2:
                continue
            item = (compact, canonical)
            if item in seen:
                continue
            seen.add(item)
            needles.append(item)
    needles.sort(key=lambda item: len(item[0]), reverse=True)
    return tuple(needles)


_CITY_NEEDLES = _city_needles()


def infer_city_from_place(place: str) -> str:
    """从「深圳高铁站」「北京南站」这类地名里抽出城市；抽不到则空串。"""
    text = (place or "").strip()
    if not text:
        return ""
    compact = normalize_text(text).replace(" ", "").replace("'", "")
    for needle, canonical in _CITY_NEEDLES:
        key = normalize_text(needle).replace(" ", "").replace("'", "")
        if key and key in compact:
            return canonical
    return ""


_WEATHER_MARKERS = ("天气", "气温", "下雨", "降雨", "温度", "weather", "forecast")


def last_human_content(messages) -> str:
    """从消息列表里取最近一条用户原文。"""
    for message in reversed(list(messages or [])):
        if getattr(message, "type", "") != "human":
            continue
        content = getattr(message, "content", "")
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, str):
                    parts.append(item)
                elif isinstance(item, dict) and item.get("type") == "text":
                    parts.append(str(item.get("text", "")))
            return "".join(parts).strip()
    return ""


def looks_like_weather_query(text: str) -> bool:
    compact = normalize_text(text).replace(" ", "")
    return any(marker in compact for marker in _WEATHER_MARKERS)


def infer_when_from_text(text: str) -> str:
    """从用户原句抽出 today/tomorrow；没提则空串。"""
    compact = normalize_text(text)
    if any(token in compact for token in ("明天", "明日", "tomorrow")):
        return "tomorrow"
    if any(token in compact for token in ("今天", "今日", "today")):
        return "today"
    return ""


def infer_weather_city_from_text(text: str) -> str:
    """查天气问句里抽出城市。优先取「天气」附近的地名，避免和后面的路线城市混用。"""
    raw = (text or "").strip()
    if not raw or not looks_like_weather_query(raw):
        return ""
    compact = normalize_text(raw).replace(" ", "").replace("'", "")
    marker_pos = -1
    marker_len = 0
    for marker in _WEATHER_MARKERS:
        key = normalize_text(marker).replace(" ", "").replace("'", "")
        idx = compact.find(key)
        if idx >= 0:
            marker_pos = idx
            marker_len = len(key)
            break
    if marker_pos >= 0:
        before = compact[max(0, marker_pos - 16) : marker_pos]
        after = compact[marker_pos + marker_len : marker_pos + marker_len + 16]
        found = infer_city_from_place(before) or infer_city_from_place(after)
        if found:
            return found
    return infer_city_from_place(raw)


def is_city_clarify_question(question: str) -> bool:
    text = (question or "").strip()
    if not text:
        return False
    return "哪座城市" in text or ("城市" in text and "天气" in text)


def city_hint_for_route(origin: str, destination: str, leaked_city: str = "") -> str:
    """路线规划的城市只看起终点，不沿用前面天气问句里的城市。"""
    for place in (origin, destination):
        found = infer_city_from_place(place)
        if found:
            return found
    leaked = canonical_city(leaked_city) if leaked_city else ""
    blob = f"{origin}{destination}"
    if leaked and leaked in blob:
        return leaked
    return ""


def resolve_run_city(
    skill_name: str,
    *,
    city_param: str = "",
    origin: str = "",
    destination: str = "",
    state_city: str = "",
) -> str:
    """天气城市和路线城市互不借用。

    一句话里经常先查 A 地天气、再规划 B 地路线，两段可能无关。
    weather 可用本轮 state.city；amap 只用本次调用自己的地点。
    """
    name = normalize_text(skill_name)
    explicit = canonical_city(city_param) if str(city_param or "").strip() else ""
    if name == "amap":
        origin_text = (origin or "").strip()
        dest_text = (destination or "").strip()
        if origin_text and dest_text:
            return city_hint_for_route(origin_text, dest_text, leaked_city=explicit)
        return explicit
    inherited = canonical_city(state_city) if str(state_city or "").strip() else ""
    return explicit or inherited


def city_country(raw: str) -> str:
    """已知城市对应的 ISO 国家码；未知则空字符串。"""
    canonical = canonical_city(raw)
    return _CITY_COUNTRIES.get(canonical, "")


def _latin_alias(canonical: str) -> str:
    for name, variants in _CITY_GROUPS:
        if name != canonical:
            continue
        for variant in variants:
            compact = variant.replace(" ", "").replace("'", "")
            if compact.isascii() and any(ch.isalpha() for ch in compact):
                return variant.title() if variant.islower() else variant
    return canonical


def city_geocode_query(raw: str) -> str:
    """OpenWeather 地理编码查询串：已知中国城市用英文名+国家码，避免「天津」命中日本 Amatsu。"""
    text = (raw or "").strip()
    if not text:
        return ""
    canonical = canonical_city(text) or text
    country = city_country(canonical)
    name = _latin_alias(canonical) if country else canonical
    if country:
        return f"{name},{country}"
    return name


def pick_geo_place(places: list[dict], *, expected_country: str = "") -> dict:
    """已知国家码时优先选该国结果，避免「天津」命中日本 Amatsu。"""
    if not places:
        raise ValueError("places 不能为空")
    wanted = (expected_country or "").strip().upper()
    if wanted:
        for place in places:
            if (place.get("country") or "").upper() == wanted:
                return place
    return places[0]


def normalize_args_for_cache(skill_name: str, args: dict[str, Any]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in args.items():
        field = normalize_text(str(key))
        text = str(value).strip()
        if field in {"city", "location"}:
            normalized[field] = canonical_city(text) or normalize_text(text)
        elif field in {"when", "date", "day"}:
            normalized[field] = canonical_when(text) or normalize_text(text)
        elif field in {"mode", "travel_mode"}:
            normalized[field] = canonical_mode(text) or normalize_text(text)
        elif field in {"origin", "from", "start", "destination", "dest", "end"}:
            normalized[field] = canonical_city(text) or normalize_text(text)
        else:
            normalized[field] = normalize_text(text)
    name = normalize_text(skill_name)
    if name == "weather" and "city" in normalized:
        normalized["city"] = canonical_city(normalized["city"]) or normalized["city"]
    if name == "weather" and "when" in normalized:
        normalized["when"] = canonical_when(normalized["when"]) or normalized["when"]
    if name == "amap" and "mode" in normalized:
        normalized["mode"] = canonical_mode(normalized["mode"]) or normalized["mode"]
    return normalized
