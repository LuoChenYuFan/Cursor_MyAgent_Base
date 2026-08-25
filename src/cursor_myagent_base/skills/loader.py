from __future__ import annotations

import importlib.util
import json
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from cursor_myagent_base.config import format_contact_names
from cursor_myagent_base.skills.errors import (
    NO_ENTRYPOINT,
    SCRIPT_LOAD,
    SKILL_NOT_FOUND,
    skill_error,
)

SKILLS_ROOT = Path(__file__).resolve().parent

# 有了这个修饰符 它的每个实例只在创建时赋值，创建之后这个实例不能再改。
@dataclass(frozen=True)
class SkillSpec:
    name: str
    description: str
    body: str
    directory: Path
    entrypoint: Path


# 把 SKILL.md 拆成两块——开头的元数据，和后面的说明书正文。
# 如果正常切完，那样子大概长这样：
"""
(
    {
        "name": "weather",
        "description": "查询指定城市的当前天气、气温、湿度、风力。用户问某地天气、会不会下雨、出不出门、穿衣冷暖时使用。",
        "entrypoint": "scripts/get_weather.py",
    },
    "# 天气查询\n\n## 何时使用\n\n用户询问某地当前天气、气温、降雨、湿度、风力，或做出行/穿衣相关天气判断时使用本 Skill。\n\n## 如何调用\n\n先确认城市名，再执行（city 必须作为独立参数传入，不要放进嵌套对象）：\n\n```text\nrun_skill(name=\"weather\", city=\"北京\")\n```\n\n`city` 支持中文或英文，例如 `北京`、`上海`、`Shanghai`。禁止只传 name 而不传 city。\n\n## 约束\n\n- 必须使用脚本返回的实况，禁止编造气温、天气或风力。\n- 城市不明确时，先向用户询问要查哪个城市，不要猜测。\n- 脚本报错时，把错误原因如实告诉用户。",
)
"""
def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    stripped = text.lstrip("\ufeff")
    if not stripped.startswith("---"):
        return {}, stripped
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        return {}, stripped
    meta: dict[str, str] = {}
    for line in parts[1].splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, parts[2].lstrip("\n")

# 这个方法就是获取SKILL.md脚本中 entrypoint 中有没有要执行的脚本，如果有，则将这个脚本所在路径返回
def _discover_entrypoint(skill_dir: Path, relative: str | None) -> Path | None:
    skill_dir = skill_dir.resolve()
    if relative:
        candidate = (skill_dir / relative).resolve()
        if candidate.is_file() and skill_dir in candidate.parents:
            return candidate
        return None
    scripts_dir = skill_dir / "scripts"
    if not scripts_dir.is_dir():
        return None
    scripts = sorted(p for p in scripts_dir.glob("*.py") if p.name != "__init__.py")
    return scripts[0] if scripts else None





# 扫描包内 Skill 目录，把合格的 Skill 收成一份字典。它不执行天气查询，只负责「发现 + 校验 + 登记」。
def load_skills() -> dict[str, SkillSpec]:
    """扫描 skills/*/SKILL.md，只解析包内 Skill，不把 loader 自己当 Skill。"""
    skills: dict[str, SkillSpec] = {}
    # 调用 sorted 方法，它的返回值是个List
    # 它接收一个可迭代对象（列表、glob 的结果、字符串等），返回一个 新的排好序的列表，原来的对象不会被改掉。
    # 这个项目里：就是把找到的那些 SKILL.md 路径按默认顺序（路径字符串）排好，再拿去 for 循环。
    # 返回的值类型是 pathlib.Path，相当于这个文件的地址
    for skill_md in sorted(SKILLS_ROOT.glob("*/SKILL.md")):
        meta, body = _parse_frontmatter(skill_md.read_text(encoding="utf-8"))
        # 从这个 SKILL.md 的路径往上走一层，拿到它所在的文件夹
        skill_dir = skill_md.parent
        # 从元数据里拿 name，如果拿不到就用文件夹名代替
        name = (meta.get("name") or skill_dir.name).strip()
        # 从元数据里拿 description，如果拿不到就用空字符串代替
        description = (meta.get("description") or "").strip()
        if not description:
            continue
        # 从元数据里拿到当前Skill目录下有没有要执行的脚本，如果有，则记录这个SKILL要执行的脚本路径
        entrypoint = _discover_entrypoint(skill_dir, meta.get("entrypoint"))
        if entrypoint is None:
            continue
        skills[name] = SkillSpec(
            name=name,      # 技能名称
            description=description,
            body=body.strip(),      # 技能说明书正文
            directory=skill_dir,    # 技能目录
            entrypoint=entrypoint,  # 技能执行脚本路径
        )
    return skills # 返回一个字典，键是技能名称，值是SkillSpec对象




# 这个方法被以下三个地方调用：
# 1. cli.py 的 run_cli() —— 启动时打印给用户看
# 2. agents/intent_Agent.py 的 _intent_system() —— 塞进意图识别的系统提示，让路由器知道有哪些 Skill
# 3. agents/trip_Agent.py、agents/office_Agent.py —— 塞进对应领域专家的系统提示，让专家按本领域目录选 Skill
def format_catalog(allowed: Iterable[str] | None = None) -> str:
    # 调用 load_skills() 方法，获取所有技能的列表
    skills = load_skills()
    if allowed is not None:
        allow = {str(name).strip() for name in allowed if str(name).strip()}
        skills = {name: spec for name, spec in skills.items() if name in allow}
    # 如果技能列表为空，则返回提示信息
    if not skills:
        return "（当前没有可用 Skill）"
    return "\n".join(f"- {spec.name}: {spec.description}" for spec in skills.values())


def get_skill(name: str) -> SkillSpec | None:
    skills = load_skills()
    raw = (name or "").strip()
    if raw in skills:
        return skills[raw]
    lowered = raw.casefold()
    for key, spec in skills.items():
        if key.casefold() == lowered:
            return spec
    return None


def load_skill_markdown(name: str) -> str:
    spec = get_skill(name)
    if spec is None:
        available = ", ".join(load_skills()) or "无"
        return skill_error(
            SKILL_NOT_FOUND,
            f"找不到 Skill「{name}」。当前可用：{available}",
        )
    text = (
        f"# Skill: {spec.name}\n\n"
        f"{spec.description}\n\n"
        f"{spec.body}"
    )
    if spec.name == "email":
        text += (
            "\n\n## 当前通讯录称呼\n"
            f"{format_contact_names()}\n"
            "to 填写上述称呼即可。不要根据对话历史自行宣布某人不在通讯录；"
            "是否允许发送以脚本返回为准。"
        )
    return text


def _normalize_arguments(arguments: dict | str | None) -> dict:
    if arguments is None or arguments == "":
        return {}
    if isinstance(arguments, str):
        try:
            parsed = json.loads(arguments)
        except json.JSONDecodeError:
            return {"city": arguments}
        return parsed if isinstance(parsed, dict) else {}
    if isinstance(arguments, dict):
        return arguments
    return {}


def run_skill_script(name: str, arguments: dict | str | None = None) -> str:
    spec = get_skill(name)
    if spec is None:
        available = ", ".join(load_skills()) or "无"
        return skill_error(
            SKILL_NOT_FOUND,
            f"无法执行：找不到 Skill「{name}」。当前可用：{available}",
        )

    module_name = f"cursor_myagent_base_skill_{spec.name}_{spec.entrypoint.stem}"
    module_spec = importlib.util.spec_from_file_location(module_name, spec.entrypoint)
    if module_spec is None or module_spec.loader is None:
        return skill_error(
            SCRIPT_LOAD,
            f"无法执行：Skill「{name}」脚本加载失败（{spec.entrypoint}）。",
        )

    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    run = getattr(module, "run", None)
    if not callable(run):
        return skill_error(
            NO_ENTRYPOINT,
            f"无法执行：Skill「{name}」的脚本缺少 run(arguments) 入口。",
        )
    result = run(_normalize_arguments(arguments))
    return result if isinstance(result, str) else str(result)
