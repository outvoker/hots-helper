"""Hero → official role map, used to scope weekly-report awards.

Without this, an award like "治疗最高" averages a player's healing across
*all* their games. A flex player who plays half DPS / half healer gets
their healing diluted by the DPS games (healing ≈ 0), so they never win
even when their healer games are excellent. Conversely a DPS player who
soaks a lot of damage on a squishy hero shouldn't contend for the tank
award. Scoping each award to the relevant roles fixes both.

Roles follow Blizzard's current taxonomy collapsed to five buckets:

    tank      钢板/前排开团
    bruiser   近战肉/混合(战士)
    assassin  远程或近战输出(刺杀者)
    healer    主奶
    support   辅助(护盾/视野/增益,如塔萨达尔、阿巴瑟)

Keys are zh-CN client names; common 繁體 / alias spellings are folded to
their zh-CN form in :data:`_ALIASES` so a KR/TW-localised replay still
resolves. Heroes we aren't certain about are intentionally absent —
:func:`hero_role` returns ``None`` for them and the caller falls back to
a numeric threshold, so an unknown hero is never silently misfiled.
"""

from __future__ import annotations

# zh-CN name -> role. Verified against Liquipedia hero infobox `role=`
# fields (Blizzard's current 6-role taxonomy, collapsed to 5 buckets).
# Only heroes whose role is unambiguous are listed; see module docstring
# for the fallback behaviour on omissions.
HERO_ROLE: dict[str, str] = {
    # --- 坦克 -----------------------------------------------------------
    "穆拉丁": "tank",
    "缝合怪": "tank",
    "精英牛头人酋长": "tank",
    "乔汉娜": "tank",
    "迪亚波罗": "tank",
    "泰瑞尔": "tank",
    "阿努巴拉克": "tank",
    "查莉娅": "tank",
    "加尔鲁什": "tank",
    "阿尔萨斯": "tank",       # 官方现行 Tank(非 Bruiser)
    "玛尔加尼斯": "tank",
    "美": "tank",            # Mei
    "茶": "tank",            # Cho(Cho'gall 坦克头)
    # --- 斗士 -----------------------------------------------------------
    "萨尔": "bruiser",
    "瓦里安": "bruiser",
    "阿塔尼斯": "bruiser",
    "霍格": "bruiser",
    "死亡之翼": "bruiser",
    "德哈卡": "bruiser",
    "陈": "bruiser",
    "布雷泽": "bruiser",
    "D.Va": "bruiser",
    "桑娅": "bruiser",
    "李奥瑞克": "bruiser",
    "拉格纳罗斯": "bruiser",
    "雷克萨": "bruiser",
    "伊瑞尔": "bruiser",
    "加兹鲁维": "bruiser",
    "英普瑞斯": "bruiser",
    "马萨伊尔": "bruiser",   # Malthael
    "祖尔": "bruiser",       # Xul(死灵法师,独立英雄)
    # --- 刺杀者(远程 / 近战输出) ------------------------------------
    "阿兹莫丹": "assassin",   # Ranged Assassin(旧 Specialist)
    "维拉": "assassin",
    "泰凯斯": "assassin",
    "李敏": "assassin",
    "凯尔萨斯": "assassin",
    "雷诺": "assassin",
    "格雷迈恩": "assassin",
    "狂鼠": "assassin",
    "弗斯塔德": "assassin",   # Falstad
    "古尔丹": "assassin",
    "吉安娜": "assassin",
    "希尔瓦娜斯": "assassin",
    "半藏": "assassin",
    "祖尔金": "assassin",     # Zul'jin = Ranged Assassin
    "克罗米": "assassin",
    "扎加拉": "assassin",
    "墨菲斯托": "assassin",
    "屠夫": "assassin",
    "露娜拉": "assassin",
    "卡西娅": "assassin",
    "阿拉纳克": "assassin",
    "奥菲娅": "assassin",
    "源氏": "assassin",
    "诺娃": "assassin",
    "克尔苏加德": "assassin",
    "泽拉图": "assassin",
    "菲尼克斯": "assassin",   # Fenix
    "伊利丹": "assassin",
    "凯瑞甘": "assassin",
    "重锤军士": "assassin",   # Sgt. Hammer
    "猎空": "assassin",
    "瓦莉拉": "assassin",
    "萨穆罗": "assassin",
    "纳兹波": "assassin",     # Ranged Assassin(旧 Specialist)
    "玛维": "assassin",       # Maiev = Melee Assassin
    "普罗比斯": "assassin",   # Probius(旧 Specialist)
    "琪拉": "assassin",       # Qhira = Melee Assassin
    "奔波尔霸": "assassin",   # Murky = Melee Assassin
    "塔萨达尔": "assassin",   # 官方现行 Ranged Assassin
    "加尔": "assassin",       # Gall(Cho'gall 法师头)
    # --- 治疗 -----------------------------------------------------------
    "光明之翼": "healer",
    "丽丽": "healer",
    "卢西奥": "healer",
    "安度因": "healer",
    "莫拉莉斯中尉": "healer",
    "乌瑟尔": "healer",
    "安娜": "healer",
    "卡拉辛姆": "healer",
    "泰兰德": "healer",
    "玛法里奥": "healer",
    "奥莉尔": "healer",
    "怀特迈恩": "healer",
    "阿莱克丝塔萨": "healer",
    "斯托科夫": "healer",
    "雷加尔": "healer",
    "迪卡德": "healer",       # Deckard Cain
    # --- 支援 -----------------------------------------------------------
    "阿巴瑟": "support",
    "麦迪文": "support",
    "失落的维京人": "support",
}

# 繁體 / 别名 -> zh-CN canonical name. Only the spellings actually seen in
# the replay DB are folded; low-frequency ones we can't pin down are left
# to the numeric fallback.
_ALIASES: dict[str, str] = {
    "維拉": "维拉",
    "縫合怪": "缝合怪",
    "克羅米": "克罗米",
    "瓦麗拉": "瓦莉拉",
    "路西歐": "卢西奥",
    "雷加": "雷加尔",
    "麥迪文": "麦迪文",
    "亞坦尼斯": "阿塔尼斯",
    "亞拉瑞克": "阿拉纳克",
    "塔薩達": "塔萨达尔",
    "斯杜科夫": "斯托科夫",
    "札莉雅": "查莉娅",
    "札迦拉": "扎加拉",
    "桑雅": "桑娅",
    "歐菲亞": "奥菲娅",
    "炸彈鼠": "狂鼠",
    "烏瑟": "乌瑟尔",
    "瑪法里恩": "玛法里奥",
    "祖爾金": "祖尔金",
    "索爾": "萨尔",
    "諾娃": "诺娃",
    "阿薩斯": "阿尔萨斯",
    "亮翼": "光明之翼",
    "凱爾薩斯": "凯尔萨斯",
    "加茲魯維": "加兹鲁维",
    "卡西雅": "卡西娅",
    "小美": "美",
    "希瓦娜斯": "希尔瓦娜斯",
    "拉格納羅斯": "拉格纳罗斯",
    "李奧瑞克": "李奥瑞克",
    "榔頭中士": "重锤军士",
    "泰科斯": "泰凯斯",
    "澤拉圖": "泽拉图",
    "爆焰": "布雷泽",
    "珍娜": "吉安娜",
    "瑪爾加尼斯": "玛尔加尼斯",
    "老陳": "陈",
    "莉莉": "丽丽",
    "葛雷邁恩": "格雷迈恩",
    "迪亞布羅": "迪亚波罗",
    "閃光": "猎空",
    "阿茲莫丹": "阿兹莫丹",
    "雅立史卓莎": "阿莱克丝塔萨",
    "雷克薩": "雷克萨",
    "泰蘭妲": "泰兰德",
    "失落的維京人": "失落的维京人",
    "蘇爾": "祖尔",       # Xul TW spelling
    # Additional 繁體 spellings observed in the replay DB (2026-06).
    "伊芮爾": "伊瑞尔",
    "凱莉根": "凯瑞甘",
    "古爾丹": "古尔丹",
    "奧莉爾": "奥莉尔",
    "普羅比斯": "普罗比斯",
    "泰瑞爾": "泰瑞尔",
    "科爾蘇加德": "克尔苏加德",
    "雷諾": "雷诺",
    "瑪翼夫": "玛维",      # Maiev TW spelling
}

# Human-readable role names for display.
ROLE_ZH: dict[str, str] = {
    "tank": "坦克",
    "bruiser": "斗士",
    "assassin": "刺杀者",
    "healer": "治疗",
    "support": "支援",
}

# Convenience role groups the awards reference.
HEAL_ROLES: frozenset[str] = frozenset({"healer", "support"})
FRONTLINE_ROLES: frozenset[str] = frozenset({"tank", "bruiser"})


def canonical_hero(hero: str) -> str:
    """Fold a 繁體/alias hero spelling to its canonical zh-CN form.

    Returns the input unchanged when it's already canonical or unknown,
    so it's safe to apply blanket-wide. Use this anywhere hero names are
    grouped or displayed so a TW/KR-localised replay's "維拉" merges with
    "维拉" instead of splitting into two rows.
    """
    if not hero:
        return hero
    return _ALIASES.get(hero, hero)


def hero_role(hero: str) -> str | None:
    """Official role for a hero's localized name, or ``None`` if unknown.

    Folds common 繁體/alias spellings to their zh-CN form first. ``None``
    is a deliberate signal — callers fall back to a numeric threshold
    rather than guessing a role.
    """
    if not hero:
        return None
    return HERO_ROLE.get(canonical_hero(hero))
