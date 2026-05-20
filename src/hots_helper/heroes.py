"""Canonical hero name list (zh-CN client) used to disambiguate OCR.

The vision module needs this so that it can drop OCR blocks whose text
*is* a hero name from the player-name slots — without it, when someone
locks in a hero, the hero caption ("阿兹莫丹") sits right next to the
player-name strip in the BP UI and the OCR pipeline can't tell which
of two short labels is the player name.

Why hard-code instead of pulling from ``DB.hero_aggregate_stats``:
- The squad's DB only has heroes that have actually been picked locally.
  A new player joining via cloud-sync sees their first BP screen with
  zero history — the hero list would be incomplete on day 1.
- We also want to recognise heroes the squad has *seen banned* on KR
  ladder, which the local DB may not contain.

Both simplified (CN) and traditional (TW) spellings are listed because
the same install can swap localisations at runtime via
"区域设置". Hero names sometimes leak through with traditional glyphs
even on the CN client when a KR squad member's client localises
differently.
"""

from __future__ import annotations

# Source: every hero localised name observed in the project's replay DB
# as of 2026-05-19, plus any hero that doesn't have any local matches
# yet but is in the active rotation. Both 简体 and 繁體 spellings.
HERO_NAMES_ZH: frozenset[str] = frozenset({
    # observed in DB (sorted)
    "D.Va", "丽丽", "乌瑟尔", "乔汉娜", "亞坦尼斯", "亞拉瑞克", "亮翼",
    "伊利丹", "伊瑞尔", "光明之翼", "克尔苏加德", "克罗米", "克羅米",
    "凯尔萨斯", "凯瑞甘", "凱恩", "凱爾薩斯", "加兹鲁维", "加尔",
    "加尔鲁什", "加茲魯維", "半藏", "卡拉辛姆", "卡爾洛斯", "卡西娅",
    "卡西雅", "卢西奥", "古尔丹", "吉安娜", "塔萨达尔", "塔薩達",
    "墨菲斯托", "失落的維京人", "失落的维京人", "奔波尔霸", "奥莉尔",
    "奥菲娅", "安娜", "安度因", "小美", "屠夫", "布雷泽", "希尔瓦娜斯",
    "希瓦娜斯", "弗斯塔德", "德哈卡", "怀特迈恩", "扎加拉", "拉格納羅斯",
    "拉格纳罗斯", "斯托科夫", "斯杜科夫", "普罗比斯", "札莉雅", "札迦拉",
    "李奥瑞克", "李奧瑞克", "李敏", "查莉娅", "格雷迈恩", "桑娅", "桑雅",
    "榔頭中士", "歐菲亞", "死亡之翼", "泰兰德", "泰凯斯", "泰瑞尔",
    "泰科斯", "泰蘭妲", "泽拉图", "源氏", "澤拉圖", "炸彈鼠", "烏瑟",
    "爆焰", "狂鼠", "猎空", "玛尔加尼斯", "玛法里奥", "玛维", "珍娜",
    "琪拉", "瑪法里恩", "瑪爾加尼斯", "瓦莉拉", "瓦里安", "瓦麗拉",
    "祖尔", "祖尔金", "祖爾金", "穆拉丁", "精英牛头人酋长", "納奇班",
    "索爾", "維拉", "縫合怪", "纳兹波", "维拉", "缝合怪", "老陳",
    "英普瑞斯", "莉莉", "莫奇", "莫拉莉斯中尉", "菲尼克斯", "萨尔",
    "萨穆罗", "葛雷邁恩", "蘇爾", "諾娃", "诺娃", "路西歐", "迪亚波罗",
    "迪亞布羅", "迪卡德", "重锤军士", "閃光", "阿兹莫丹", "阿努巴拉克",
    "阿塔尼斯", "阿尔萨斯", "阿巴瑟", "阿拉纳克", "阿茲莫丹",
    "阿莱克丝塔萨", "阿薩斯", "陈", "雅立史卓莎", "雷克萨", "雷克薩",
    "雷加", "雷加尔", "雷诺", "霍格", "露娜拉", "马萨伊尔", "麥迪文",
    "麦迪文",
    # extra spellings / heroes with no local games but in active rotation
    "古加尔", "黑心", "霸天牛头人", "海潮", "塔萨达", "斯托科夫",
})


def is_hero_name(text: str) -> bool:
    """``True`` when ``text`` is recognisable as a HotS hero name.

    Whitespace and surrounding punctuation are stripped so an OCR
    fragment like ``"  阿兹莫丹 "`` still matches. We also accept:

    * **Truncations** — the detector sometimes chops off the last
      character (``"光明之翼"`` → ``"光明之"``); any 3+-char prefix
      of a known hero name matches.
    * **One-glyph OCR drift** — ``"莫拉利斯"`` (mis-read of
      ``"莫拉莉斯中尉"``) and ``"莫拉利具"`` (last glyph drift)
      both share a long substring with the canonical name. We
      reject any 3+-char CJK string that is a substring of a hero
      name — same rationale as the prefix guard.

    Pure-Latin candidates (``D.Va``, ``Yuna``) skip the substring
    check because Latin player names like ``Yuna`` partially overlap
    with localised hero names through coincidence.
    """
    if not text:
        return False
    t = text.strip().strip(" 　·.")
    if not t:
        return False
    if t in HERO_NAMES_ZH:
        return True
    if len(t) < 3 or t.isascii():
        return False
    # Prefix or substring of a known hero name → very likely a
    # mangled hero caption.
    for h in HERO_NAMES_ZH:
        if len(h) < 3 or h.isascii():
            continue
        if h.startswith(t) or h.endswith(t) or t in h:
            return True
        # Single-glyph drift on a 3+-char hero (``"莫拉利"`` ↔ ``"莫拉莉"``):
        # hamming distance ≤ 1 against any same-length window inside
        # the hero name. Tighter than the 4+-char rule below because
        # a 3-glyph CJK candidate has fewer signal bits.
        if len(t) == 3 and len(h) >= 3:
            for start in range(len(h) - 3 + 1):
                window = h[start:start + 3]
                diffs = sum(1 for a, b in zip(t, window) if a != b)
                if diffs <= 1:
                    return True
        # Multi-glyph drift on a 4+-char CJK candidate: hamming
        # distance ≤ 2 covers ``"莫拉利斯"`` (1-off) and
        # ``"莫拉利具"`` (2-off, both versus ``"莫拉莉斯"``).
        if len(t) >= 4 and len(h) >= len(t):
            for start in range(len(h) - len(t) + 1):
                window = h[start:start + len(t)]
                diffs = sum(1 for a, b in zip(t, window) if a != b)
                if diffs <= 2:
                    return True
    return False
