"""Tiny in-process translation table.

Two locales: ``zh`` (default) and ``en``. We deliberately don't use Qt
Linguist .qm files — the surface area is small and a single dict is easier
to keep in sync.

Usage:
    from .i18n import t, set_language
    set_language("en")
    t("ui.main.start_scan")  # -> "Start scan"

If a key is missing in the active locale we fall back to ``zh``; if missing
there too we return the key itself so the bug is obvious.
"""

from __future__ import annotations

from typing import Callable

_LOCALES = ("zh", "en")
_DEFAULT = "zh"

_translations: dict[str, dict[str, str]] = {
    # === Main window ====================================================
    "ui.app.title": {"zh": "风暴英雄助手", "en": "HotS Helper"},

    "ui.main.replay_folders": {"zh": "录像文件夹", "en": "Replay folders"},
    "ui.main.add_folder": {"zh": "添加文件夹…", "en": "Add folder…"},
    "ui.main.remove_selected": {"zh": "移除选中", "en": "Remove selected"},
    "ui.main.auto_detect": {"zh": "自动检测", "en": "Auto-detect"},
    "ui.main.no_folders_resolved": {
        "zh": "尚未解析到任何录像文件夹。点击「自动检测」或「添加文件夹…」。",
        "en": "No replay folders resolved yet. Click 'Auto-detect' or 'Add folder…'.",
    },
    "ui.main.folders_resolved": {
        "zh": "已解析 {n} 个录像文件夹：",
        "en": "{n} replay folder(s) resolved:",
    },

    "ui.main.ingest": {"zh": "录像入库", "en": "Ingest"},
    "ui.main.start_scan": {"zh": "开始扫描", "en": "Start scan"},
    "ui.main.watch": {"zh": "监听新录像", "en": "Watch for new replays"},
    "ui.main.db_summary": {
        "zh": "数据库：{replays} 局对战 · {players} 名玩家",
        "en": "DB: {replays} replays · {players} players",
    },
    "ui.main.db_error": {"zh": "数据库错误：{e}", "en": "DB error: {e}"},

    "ui.main.hotkey_section": {"zh": "对局开始前快捷键", "en": "Pre-game scout hotkey"},
    "ui.main.shortcut": {"zh": "快捷键：", "en": "Shortcut:"},
    "ui.main.apply": {"zh": "应用", "en": "Apply"},
    # Used by the HotkeyField widget for its inline edit / save / cancel
    # affordance. Display by default; enters recording mode only when
    # the user explicitly clicks 编辑.
    "ui.hotkey.edit": {"zh": "编辑", "en": "Edit"},
    "ui.hotkey.save": {"zh": "保存", "en": "Save"},
    "ui.hotkey.cancel": {"zh": "取消", "en": "Cancel"},
    "ui.hotkey.unset": {"zh": "未设置", "en": "not set"},
    "ui.main.test_popup": {"zh": "测试弹窗", "en": "Test popup"},

    # Primary feature cards on the main window.
    "ui.main.bp_card_title": {
        "zh": "BP 智能分析",
        "en": "Pre-game BP intelligence",
    },
    "ui.main.bp_card_subtitle": {
        "zh": "BP 阶段按下快捷键 → 自动截图、识别队伍、调取队伍历史数据，"
              "给出 ban/pick/天赋建议。",
        "en": "Press the hotkey during BP — the app captures the screen, "
              "OCRs both teams, mines squad history, and recommends "
              "bans, picks and talents.",
    },
    "ui.main.bp_capture_cta": {
        "zh": "立即截屏分析",
        "en": "Capture & analyse now",
    },
    "ui.main.bp_capture_tip": {
        "zh": "对当前屏幕截图并跑完整 BP 分析流程（与按下快捷键的效果完全一致）。",
        "en": "Take a real screenshot and run the full BP analysis pipeline — same as pressing the hotkey.",
    },
    "ui.main.bp_sample_cta": {
        "zh": "样例测试",
        "en": "Sample run",
    },
    "ui.main.bp_sample_tip": {
        "zh": "用内置示例 BP 截图跑一遍流程，方便在不开游戏的情况下预览效果。",
        "en": "Runs the full pipeline against a bundled sample BP screenshot — preview the popup without launching the game.",
    },
    "ui.main.sample_missing_title": {
        "zh": "样例图片缺失",
        "en": "Sample image missing",
    },
    "ui.main.sample_missing_body": {
        "zh": "本地找不到内置样例截图（src/hots_helper/ui/assets/sample_bp.jpeg）。",
        "en": "Bundled sample image is missing (src/hots_helper/ui/assets/sample_bp.jpeg).",
    },
    "ui.main.ranking_card_title": {
        "zh": "英雄强度榜",
        "en": "Hero strength rankings",
    },
    "ui.main.ranking_card_subtitle": {
        "zh": "基于本地数据库统计每个英雄在风暴联赛 / 天命乱斗的胜率与综合表现，"
              "支持按地图、保守胜率、局数排序。",
        "en": "Per-hero win-rate and performance for SL and ARAM, "
              "filterable by map and sortable by conservative win-rate.",
    },
    "ui.main.settings": {"zh": "高级设置（录像、扫描、同步）", "en": "Advanced settings (replays, scan, sync)"},

    # === Translation popups =============================================
    "ui.chat_trans.title": {
        "zh": "公屏聊天翻译",
        "en": "Chat translation",
    },
    "ui.chat_trans.subtitle": {
        "zh": "已识别公屏聊天 → 自动翻译为中文。点「复制」可粘贴到 Discord 等。",
        "en": "Detected in-game chat lines, auto-translated to Chinese. Click 复制 to copy.",
    },
    "ui.chat_trans.empty": {
        "zh": "未检测到公屏聊天文字。请确认聊天面板已展开，再按一次快捷键。",
        "en": "No chat text detected — make sure the chat panel is open and try again.",
    },
    "ui.chat_trans.copy": {"zh": "复制", "en": "Copy"},
    "ui.chat_trans.redraw": {"zh": "🎯 重新框选", "en": "🎯 Redraw region"},
    "ui.chat_trans.redraw_tip": {
        "zh": "在原始截图上手动框选聊天区域，重新识别+翻译。",
        "en": "Manually drag a rectangle over the chat panel on the original screenshot to re-OCR and re-translate.",
    },
    "ui.chat_trans.redrawing": {
        "zh": "正在按你框选的区域重新识别和翻译…",
        "en": "Re-running OCR + translate on your selected region…",
    },

    "ui.compose_trans.title": {
        "zh": "中文 → 翻译给队友",
        "en": "Translate from Chinese",
    },
    "ui.compose_trans.target": {"zh": "翻译为：", "en": "Target:"},
    "ui.compose_trans.send": {"zh": "翻译", "en": "Translate"},
    "ui.compose_trans.input_placeholder": {
        "zh": "输入要翻译的中文，回车发送（Shift+回车换行）",
        "en": "Type Chinese to translate; Enter to submit (Shift+Enter for newline)",
    },
    "ui.compose_trans.translating": {
        "zh": "翻译中…",
        "en": "Translating…",
    },

    # === Capture progress dialog ========================================
    "ui.capture.title": {
        "zh": "BP 智能分析进行中…",
        "en": "Running BP intelligence…",
    },
    "ui.capture.step_capture": {
        "zh": "正在截取全屏画面…",
        "en": "Capturing fullscreen frame…",
    },
    "ui.capture.step_ocr": {
        "zh": "调用系统 OCR 引擎识别队伍名称…",
        "en": "Running system OCR over team panels…",
    },
    "ui.capture.step_parse": {
        "zh": "解析 BP 布局并对齐到 5v5 槽位…",
        "en": "Parsing BP layout and aligning to 5v5 slots…",
    },
    "ui.capture.step_resolve": {
        "zh": "在本地数据库中匹配玩家档案…",
        "en": "Resolving players against the squad database…",
    },
    "ui.capture.step_score": {
        "zh": "运行胜率模型 · 计算保守置信下界…",
        "en": "Running win-rate model · computing Wilson lower bounds…",
    },
    "ui.capture.step_render": {
        "zh": "汇总 ban / pick / 天赋建议…",
        "en": "Aggregating ban / pick / talent recommendations…",
    },
    "ui.capture.sub_first": {
        "zh": "首次运行可能稍慢，模型会缓存到内存里。",
        "en": "First run is a bit slower; the model is being cached.",
    },
    "ui.capture.done": {
        "zh": "✓ 分析完成，正在打开侦查窗口…",
        "en": "✓ Analysis complete — opening scout window…",
    },
    "ui.capture.failed": {
        "zh": "分析失败，请查看运行日志。",
        "en": "Capture failed — see Activity log.",
    },

    # Chat-translate flow shares the same dialog but gets its own copy.
    "ui.capture.chat_title": {
        "zh": "公屏聊天翻译进行中…",
        "en": "Translating in-game chat…",
    },
    "ui.capture.chat_step_capture": {
        "zh": "正在截取全屏画面…",
        "en": "Capturing fullscreen frame…",
    },
    "ui.capture.chat_step_ocr": {
        "zh": "调用系统 OCR 引擎识别屏幕文字…",
        "en": "Running system OCR over the screen…",
    },
    "ui.capture.chat_step_filter": {
        "zh": "在聊天面板区域筛选有效消息…",
        "en": "Filtering messages out of the chat region…",
    },
    "ui.capture.chat_step_translate": {
        "zh": "通过队伍服务器中转 · 火山翻译引擎处理多语言…",
        "en": "Routing via squad server · multilingual MT in flight…",
    },
    "ui.capture.chat_step_render": {
        "zh": "汇总译文准备展示…",
        "en": "Aggregating translations…",
    },

    "ui.main.tools": {"zh": "英雄强度榜", "en": "Hero ranking"},
    "ui.main.sl_ranking": {"zh": "风暴联赛榜", "en": "Storm League ranking"},
    "ui.main.aram_ranking": {"zh": "天命乱斗榜", "en": "ARAM ranking"},
    "ui.main.sl_ranking_tip": {
        "zh": "Storm League 各英雄的强度排行（基于本地数据库）",
        "en": "Storm League hero strength ranking (from local DB)",
    },
    "ui.main.aram_ranking_tip": {
        "zh": "ARAM 各英雄的强度排行（基于本地数据库）",
        "en": "ARAM hero strength ranking (from local DB)",
    },

    "ui.main.activity": {"zh": "运行日志", "en": "Activity"},
    "ui.main.credit": {
        "zh": "Authorized by 炉石风暴外带一 SB",
        "en": "Authorized by 炉石风暴外带一 SB",
    },
    "ui.main.language": {"zh": "界面语言", "en": "Language"},

    "ui.main.sync_section": {"zh": "云同步（队伍数据共享）", "en": "Cloud sync"},
    "ui.main.sync_using_defaults": {
        "zh": "已连接队伍服务器",
        "en": "Connected to squad server",
    },
    "ui.main.sync_override_btn": {
        "zh": "高级：使用自定义 Supabase 项目",
        "en": "Advanced: use a custom Supabase project",
    },
    "ui.main.sync_url": {"zh": "Supabase URL：", "en": "Supabase URL:"},
    "ui.main.sync_key": {"zh": "Supabase 密钥：", "en": "Supabase anon key:"},
    "ui.main.sync_save": {"zh": "保存", "en": "Save"},
    "ui.main.sync_now": {"zh": "立即同步", "en": "Sync now"},
    "ui.main.sync_auto": {"zh": "自动同步", "en": "Auto sync"},
    "ui.main.sync_disabled": {"zh": "云同步未配置", "en": "Cloud sync not configured"},
    "ui.main.sync_running": {"zh": "正在同步…", "en": "Syncing…"},
    "ui.main.sync_progress": {"zh": "同步：{msg}", "en": "Sync: {msg}"},
    "ui.main.sync_done": {
        "zh": "同步完成：上传 {pushed}，下载 {pulled}",
        "en": "Sync done: pushed {pushed}, pulled {pulled}",
    },
    "ui.main.sync_errors": {
        "zh": "同步发生 {n} 个错误",
        "en": "{n} sync error(s)",
    },
    "ui.main.sync_save_warn_title": {"zh": "保存失败", "en": "Save failed"},
    "ui.main.sync_save_warn_body": {
        "zh": "请同时填写 URL 和密钥；或将两者全部留空表示禁用同步。",
        "en": "Provide both URL and key, or leave both blank to disable sync.",
    },
    "ui.main.sync_url_placeholder": {
        "zh": "https://xxxx.supabase.co",
        "en": "https://xxxx.supabase.co",
    },
    "ui.main.sync_key_placeholder": {
        "zh": "粘贴 anon (public) 密钥",
        "en": "Paste the anon (public) key",
    },

    "ui.main.no_folders_warn_title": {"zh": "没有可用的文件夹", "en": "No folders"},
    "ui.main.no_folders_warn_body": {
        "zh": "请先添加至少一个录像文件夹。",
        "en": "Add at least one folder first.",
    },
    "ui.main.invalid_hotkey_title": {"zh": "快捷键无效", "en": "Invalid"},
    "ui.main.invalid_hotkey_body": {
        "zh": "请按下一个组合键。",
        "en": "Please enter a key combination.",
    },
    "ui.main.dir_not_found": {
        "zh": "目录不存在：{path}",
        "en": "Directory not found: {path}",
    },
    "ui.main.added_folder": {"zh": "已添加文件夹：{path}", "en": "Added folder: {path}"},
    "ui.main.autodetect_added": {
        "zh": "自动检测：已加入标准 HotS 文件夹。",
        "en": "Auto-detect: added standard HotS folder(s).",
    },
    "ui.main.autodetect_none": {
        "zh": "自动检测：未发现新文件夹。",
        "en": "Auto-detect: nothing new found.",
    },
    "ui.main.scanning": {"zh": "正在扫描 {n} 个文件夹…", "en": "Scanning {n} folder(s)…"},
    "ui.main.scan_done": {
        "zh": "扫描完成：{new} 局新增、{dup} 局已入库、{err} 局错误。",
        "en": "Scan done: {new} new, {dup} already ingested, {err} errors.",
    },
    "ui.main.watcher_active": {
        "zh": "监听已开启，监控 {n} 个文件夹。",
        "en": "Watcher active on {n} folder(s).",
    },
    "ui.main.watcher_stopped": {"zh": "监听已停止。", "en": "Watcher stopped."},
    "ui.main.hotkey_set": {"zh": "快捷键已设置为：{combo}", "en": "Hotkey set to: {combo}"},
    "ui.main.hotkey_registered": {"zh": "快捷键已注册：{combo}", "en": "Hotkey registered: {combo}"},
    "ui.main.chat_translate_hotkey_registered": {
        "zh": "聊天翻译快捷键已注册：{combo}",
        "en": "Chat-translate hotkey registered: {combo}",
    },
    "ui.main.compose_translate_hotkey_registered": {
        "zh": "中文转译快捷键已注册：{combo}",
        "en": "Compose-translate hotkey registered: {combo}",
    },
    "ui.main.trans_hotkeys_section": {
        "zh": "翻译快捷键",
        "en": "Translation hotkeys",
    },
    "ui.main.launcher_section": {
        "zh": "悬浮启动器",
        "en": "Floating launcher",
    },
    "ui.main.launcher_visible": {
        "zh": "显示置顶悬浮按钮（点击展开 BP / 翻译 / 转译）",
        "en": "Show always-on-top floating chip (click to reveal BP / translate / compose)",
    },
    "ui.main.ocr_lang_section": {
        "zh": "OCR 语言模型",
        "en": "OCR language packs",
    },
    "ui.main.ocr_lang_cn": {
        "zh": "中文 + 英文（必选）",
        "en": "Chinese + English (required)",
    },
    "ui.main.ocr_lang_kr": {
        "zh": "韩文",
        "en": "Korean",
    },
    "ui.main.ocr_lang_jp": {
        "zh": "日文",
        "en": "Japanese",
    },
    "ui.main.ocr_lang_hint": {
        "zh": "每多勾一种语言，OCR 大约多花 1 秒；中英文模型已包含英文识别。",
        "en": "Each extra language adds ~1s of OCR time. The Chinese model already covers English.",
    },
    "ui.main.ocr_lang_saved": {
        "zh": "OCR 语言模型已保存：{langs}",
        "en": "OCR languages saved: {langs}",
    },

    # === Player rank leaderboards ========================================
    "ui.main.player_ranking": {
        "zh": "玩家排行榜",
        "en": "Player rankings",
    },
    "ui.main.player_ranking_tip": {
        "zh": "最坑队友 / 最强对手榜 — BP 时榜单玩家会被高亮",
        "en": "Hall of shame & hall of fame — these handles get highlighted in BP",
    },
    "ui.rank.window_title": {
        "zh": "玩家排行榜",
        "en": "Player rankings",
    },
    "ui.rank.title": {
        "zh": "玩家排行榜",
        "en": "Player rankings",
    },
    "ui.rank.board": {
        "zh": "榜单",
        "en": "Board",
    },
    "ui.rank.board_worst_teammate": {
        "zh": "🪦 最坑队友（同队胜率最低）",
        "en": "🪦 Worst teammates (lowest win rate as ally)",
    },
    "ui.rank.board_best_teammate": {
        "zh": "🤝 最强队友（同队胜率最高）",
        "en": "🤝 Best teammates (highest win rate as ally)",
    },
    "ui.rank.board_best_opponent": {
        "zh": "👑 最强对手（对面胜率最高）",
        "en": "👑 Strongest opponents (highest win rate against us)",
    },
    "ui.rank.board_worst_opponent": {
        "zh": "🎯 最弱对手（对面胜率最低）",
        "en": "🎯 Weakest opponents (lowest win rate against us)",
    },
    "ui.rank.limit_label": {
        "zh": "显示前",
        "en": "Show top",
    },
    "ui.rank.summary_single": {
        "zh": "{board} · {count} 人 · 最少 {min_games} 局起算",
        "en": "{board} · {count} player(s) · {min_games}+ games",
    },
    "ui.rank.summary_total": {
        "zh": "风暴联赛 · 共 {count} 名玩家 · 最少 {min_games} 局起算 · 点击列名切换排序",
        "en": "Storm League · {count} player(s) · {min_games}+ games · click any column header to sort",
    },
    "ui.rank.summary_hero": {
        "zh": "风暴联赛 · {hero} · {count} 名玩家 · 最少 {min_games} 局起算",
        "en": "Storm League · {hero} · {count} player(s) · {min_games}+ games",
    },
    "ui.rank.hero_filter": {"zh": "英雄", "en": "Hero"},
    "ui.rank.hero_all": {"zh": "全部英雄", "en": "All heroes"},
    "ui.rank.search_label": {"zh": "搜索", "en": "Search"},
    "ui.rank.search_placeholder": {
        "zh": "玩家名…",
        "en": "Player name…",
    },
    "ui.rank.extras_label": {
        "zh": "★ 我方常驻玩家（{count} 人，独立显示）",
        "en": "★ Squad members ({count}, pinned below)",
    },
    "ui.rank.footer": {
        "zh": "<span style='color:#888;'>"
              "排序按 Wilson 95% 置信下界（避免 1 局 100% 上榜）。"
              "BP 分析时，榜单上的玩家会在卡片上高亮提示。"
              "</span>",
        "en": "<span style='color:#888;'>"
              "Sorted by Wilson 95% lower bound on win rate (so a 1-game streak can't top the chart). "
              "Players on this board are highlighted when they show up in BP analysis."
              "</span>",
    },
    "ui.rank.footer_single": {
        "zh": "<span style='color:#888;'>"
              "数据范围：仅风暴联赛对局（天命乱斗等其他模式不计入）。"
              "战斗力 = 胜率 + KDA + 输出 + 推塔 + 治疗 + 硬币 + 经验 等多维加权后的综合百分位。"
              "BP 分析时，战斗力较低的我方槽位会标红，较高的敌方槽位会标金。"
              "</span>",
        "en": "<span style='color:#888;'>"
              "Scope: Storm League only — ARAM and other modes are excluded. "
              "Power = a percentile rank over a weighted blend of WR, KDA, damage, structure, healing, soak, XP, etc. "
              "BP analysis flags low-power ally slots in red and high-power enemy slots in gold."
              "</span>",
    },
    "ui.rank.col_rank":     {"zh": "#",       "en": "#"},
    "ui.rank.col_name":     {"zh": "玩家",    "en": "Player"},
    "ui.rank.col_games":    {"zh": "局数",    "en": "Games"},
    "ui.rank.col_wins":     {"zh": "胜",      "en": "Wins"},
    "ui.rank.col_wr":       {"zh": "胜率",    "en": "WR"},
    "ui.rank.col_wlb":      {"zh": "保守胜率", "en": "WLB"},
    "ui.rank.col_power":    {"zh": "战斗力",   "en": "Power"},
    "ui.rank.col_kda":      {"zh": "K/D/A",  "en": "K/D/A"},
    "ui.rank.col_hero_dmg": {"zh": "英雄伤害","en": "Hero dmg"},
    "ui.rank.col_struct":   {"zh": "推塔",    "en": "Struct"},
    "ui.rank.col_healing":  {"zh": "治疗",    "en": "Healing"},
    "ui.rank.col_soak":     {"zh": "硬币",    "en": "Soak"},
    "ui.rank.col_xp":       {"zh": "经验",    "en": "XP"},
    "ui.rank.sort": {"zh": "排序", "en": "Sort"},
    "ui.rank.sort_wlb": {
        "zh": "保守胜率（Wilson 下界）",
        "en": "Conservative WR (Wilson LB)",
    },
    "ui.rank.sort_power": {
        "zh": "综合战斗力",
        "en": "Combat power",
    },
    "ui.rank.sort_tip": {
        "zh": "保守胜率：按 Wilson 95% 下界排序，避免 1 局上榜。\n"
              "综合战斗力：胜率 + KDA + 伤害 + 推塔 + 治疗 + 硬币 + 经验 + 控制 加权综合。",
        "en": "Conservative WR: Wilson 95% lower bound — small samples can't top the chart.\n"
              "Combat power: weighted blend of WR + KDA + damage + structure + healing + soak + XP + CC.",
    },

    # Highlights on the BP popup player cards.
    "ui.popup.card.flag_worst": {
        "zh": "🪦 低战斗力队友：战力 {power}（{games} 局 · 胜率 {wr}%）",
        "en": "🪦 Low-power teammate: power {power} ({games}g · {wr}% WR)",
    },
    "ui.popup.card.flag_best": {
        "zh": "👑 高战斗力对手：战力 {power}（{games} 局 · 胜率 {wr}%）",
        "en": "👑 High-power opponent: power {power} ({games}g · {wr}% WR)",
    },

    # === Floating launcher ===============================================
    "ui.launcher.tooltip": {
        "zh": "点击展开 — BP 分析 / 公屏翻译 / 中文转译\n拖动可移动位置",
        "en": "Click to reveal — BP / chat translate / compose translate\nDrag to move",
    },
    "ui.launcher.bp": {"zh": "BP 分析", "en": "BP scout"},
    "ui.launcher.chat": {"zh": "公屏翻译", "en": "Chat trans"},
    "ui.launcher.compose": {"zh": "中文转译", "en": "Compose"},
    "ui.main.chat_translate_label": {
        "zh": "公屏聊天翻译：",
        "en": "Translate game chat:",
    },
    "ui.main.compose_translate_label": {
        "zh": "中文转译给队友：",
        "en": "Translate from Chinese:",
    },
    "ui.main.chat_translate_started": {
        "zh": "正在截图并翻译公屏聊天…",
        "en": "Capturing screen and translating chat…",
    },
    "ui.main.drafting": {"zh": "当前选英雄：{name}", "en": "Currently drafting: {name}"},
    "ui.main.hotkey_busy": {
        "zh": "快捷键被忽略：上次截图分析尚未完成。",
        "en": "Hotkey ignored — previous capture still running.",
    },
    "ui.main.capturing_screenshot": {"zh": "正在截图…", "en": "Capturing screenshot…"},
    "ui.main.db_path": {"zh": "数据库：{path}", "en": "DB: {path}"},
    "ui.main.screenshot_saved": {"zh": "截图已保存：{path}", "en": "Screenshot saved: {path}"},

    # === Popup ==========================================================
    "ui.popup.title": {"zh": "对局开始前侦查", "en": "Pre-game scout"},
    "ui.popup.minimize_tip": {
        "zh": "最小化为悬浮小标签（不影响全屏游戏）",
        "en": "Minimise to a floating chip (doesn't affect fullscreen game)",
    },
    "ui.popup.title_drafting": {"zh": "对局开始前侦查 — 当前选人：{name}",
                                "en": "Pre-game scout — drafting: {name}"},
    "ui.popup.map": {"zh": "地图：", "en": "Map:"},
    "ui.popup.map_placeholder": {"zh": "如：奥特兰克战道", "en": "e.g. Alterac Pass"},
    "ui.popup.analyze": {"zh": "重新分析", "en": "Analyze all"},

    "ui.popup.allies": {"zh": "我方队伍", "en": "Allies (your team)"},
    "ui.popup.enemies": {"zh": "敌方队伍", "en": "Enemies"},

    "ui.popup.footer": {
        "zh": "玩家名称由 OCR 识别 — 改名后按 Enter 或 ↻ 重新查询；▼ 展开看完整英雄列表；只统计风暴联赛数据。",
        "en": "Names come from OCR — edit any slot + press Enter or ↻ to re-query. "
              "▼ expands a slot to every hero the player has used. Storm League data only.",
    },

    "ui.popup.ban_title": {"zh": "🚫 推荐 ban", "en": "🚫 Ban suggestions"},
    "ui.popup.ban_subtitle": {"zh": "根据敌方历史", "en": "from enemy history"},
    "ui.popup.pick_title": {"zh": "✅ 推荐 pick", "en": "✅ Pick suggestions"},
    "ui.popup.pick_subtitle": {"zh": "本地图统计强势的英雄", "en": "strong on this map"},

    "ui.popup.ban_section_history": {"zh": "敌方历史强势英雄", "en": "From enemy history"},
    "ui.popup.ban_section_map": {
        "zh": "本地图强势 <span style='color:#a88; font-weight: normal;'>（且我方少用）</span>",
        "en": "Strong on this map <span style='color:#a88; font-weight: normal;'>(and squad doesn't play)</span>",
    },
    "ui.popup.ban_empty_advisory": {
        "zh": "<i style='color:#a88;'>本地数据不足，暂无对手的招牌英雄统计。</i>",
        "en": "<i style='color:#a88;'>No statistically strong signature heroes for these opponents yet — data is too thin.</i>",
    },
    "ui.popup.ban_empty_default": {
        "zh": "<i style='color:#a88;'>暂无敌方数据</i>",
        "en": "<i style='color:#a88;'>no opponent data yet</i>",
    },
    "ui.popup.ban_player_not_in_db": {
        "zh": "<span style='color:#a88;'>不在本地数据库</span>",
        "en": "<span style='color:#a88;'>not in local DB</span>",
    },
    "ui.popup.ban_player_games_seen": {
        "zh": "<span style='color:#caa;'>已记录 {n} 局风暴联赛</span>",
        "en": "<span style='color:#caa;'>{n} SL games seen</span>",
    },
    "ui.popup.we_never_play": {"zh": "我方从不使用", "en": "we never play"},
    "ui.popup.we_play_n": {"zh": "我方使用 {n} 次", "en": "we play {n}x"},

    # Per-player profile block at the top of the ban panel.
    "ui.popup.ban_section_profiles": {
        "zh": "敌方玩家速览",
        "en": "Opponents at a glance",
    },
    "ui.popup.profile_power": {
        "zh": "战斗力 {power}",
        "en": "power {power}",
    },
    "ui.popup.profile_power_rank": {
        "zh": "排名 #{rank}/{total}",
        "en": "rank #{rank}/{total}",
    },
    "ui.popup.profile_no_power": {
        "zh": "战斗力 N/A",
        "en": "power N/A",
    },
    "ui.popup.profile_with_us": {
        "zh": "和我们 {games} 局（{w}/{l} {wr}%）",
        "en": "with us {games} ({w}/{l} {wr}%)",
    },
    "ui.popup.profile_vs_us": {
        "zh": "对我们 {games} 局（{w}/{l} {wr}%）",
        "en": "against us {games} ({w}/{l} {wr}%)",
    },
    "ui.popup.profile_no_history": {
        "zh": "未与我方打过",
        "en": "no shared games",
    },
    "ui.popup.profile_tag_smurf": {
        "zh": "🔥 强力对手",
        "en": "🔥 carry threat",
    },
    "ui.popup.profile_tag_troll": {
        "zh": "💀 老坑货",
        "en": "💀 known troll",
    },
    "ui.popup.profile_tag_friend": {
        "zh": "🤝 老队友",
        "en": "🤝 frequent ally",
    },

    "ui.popup.pick_empty": {
        "zh": "<i style='color:#9a9;'>本地图暂无显著强势的英雄</i>",
        "en": "<i style='color:#9a9;'>no significantly strong picks on this map yet</i>",
    },
    "ui.popup.build_btn": {"zh": "天赋", "en": "Build"},
    "ui.popup.no_talent_data": {
        "zh": "<i style='color:#9a9;'>暂无天赋数据</i>",
        "en": "<i style='color:#9a9;'>no talent data</i>",
    },

    # Player card -------------------------------------------------------
    "ui.popup.card.player_name_placeholder": {"zh": "玩家名称", "en": "player name"},
    "ui.popup.card.requery_tip": {"zh": "重新查询此玩家", "en": "Re-query this player"},
    "ui.popup.card.region_tip": {
        "zh": "在截图上手动框选该玩家名称",
        "en": "Select the player name region on the screenshot",
    },
    "ui.popup.card.expand_tip": {"zh": "展开全部英雄", "en": "Show all heroes"},
    "ui.popup.card.found_as": {"zh": "（识别为：{name}）", "en": "(found as: {name})"},
    "ui.popup.card.no_data": {
        "zh": "<i style='color:#888'>无数据</i>",
        "en": "<i style='color:#888'>no data</i>",
    },
    "ui.popup.card.no_hero_usage": {
        "zh": "<span style='color:#888;'>风暴联赛中暂无英雄使用记录</span>",
        "en": "<span style='color:#888;'>no hero usage in Storm League yet</span>",
    },
    "ui.popup.card.heroes_used": {"zh": "曾使用英雄", "en": "Heroes used"},
    "ui.popup.card.heroes_used_on_map": {
        "zh": "本地图曾使用英雄（按胜率排序）",
        "en": "Heroes used on this map (by win-rate)",
    },
    "ui.popup.card.heroes_used_all": {
        "zh": "全部地图曾使用英雄",
        "en": "Heroes used (all maps)",
    },
    "ui.popup.card.more_heroes": {
        "zh": "<span style='color:#888;'>&nbsp;&nbsp;（还有 {n} 个英雄 — 点 ▼ 展开）</span>",
        "en": "<span style='color:#888;'>&nbsp;&nbsp;(+{n} more heroes — click ▼ to expand)</span>",
    },
    "ui.popup.card.note_not_found": {
        "zh": "本地数据库中未找到该玩家",
        "en": "not found in local database",
    },
    "ui.popup.card.summary_line": {
        "zh": "{games} 场 / 胜率 {wr}%  K/D/A {kda}",
        "en": "{games} games  {wr}% WR  K/D/A {kda}",
    },
    "ui.popup.card.career_avg": {
        "zh": "平均：英伤 {hd} · 承伤 {dt} · 治疗 {hl} · XP {xp} · 控时 {cc}s",
        "en": "avg HeroDmg {hd} · DmgTaken {dt} · Healing {hl} · XP {xp} · CC {cc}s",
    },
    "ui.popup.card.last_match": {
        "zh": "上次对局 {when} · {hero} · {result} · K/D/A {kda}",
        "en": "Last match {when} · {hero} · {result} · K/D/A {kda}",
    },
    "ui.popup.card.match_won":  {"zh": "胜",   "en": "WIN"},
    "ui.popup.card.match_lost": {"zh": "负",   "en": "LOSS"},
    "ui.popup.card.relative_minutes": {"zh": "{n} 分钟前", "en": "{n}m ago"},
    "ui.popup.card.relative_hours":   {"zh": "{n} 小时前", "en": "{n}h ago"},
    "ui.popup.card.relative_days":    {"zh": "{n} 天前",   "en": "{n}d ago"},
    "ui.popup.card.relative_weeks":   {"zh": "{n} 周前",   "en": "{n}w ago"},
    "ui.popup.card.relative_months":  {"zh": "{n} 个月前", "en": "{n}mo ago"},
    "ui.popup.card.hero_line_main": {
        "zh": "{games} 场 {wr}%  K/D/A {kda}",
        "en": "{games}G {wr}%  K/D/A {kda}",
    },
    "ui.popup.card.hero_line_metrics": {
        "zh": "英伤 {hd} · 承伤 {dt} · 推塔 {strd} · 治疗 {hl} · XP {xp} · 控时 {cc}s",
        "en": "HeroDmg {hd} · Taken {dt} · Struct {strd} · Heal {hl} · XP {xp} · CC {cc}s",
    },
    # === Copy-to-clipboard brief =======================================
    "ui.popup.copy_btn": {"zh": "📋 复制简报", "en": "📋 Copy brief"},
    "ui.popup.copy_btn_done": {"zh": "✓ 已复制", "en": "✓ Copied"},
    "ui.popup.copy_btn_tip": {
        "zh": "复制本局 BP 分析简报（ban/pick 推荐 + 十位玩家概况）",
        "en": "Copy this draft's brief (ban/pick suggestions + 10 player overviews)",
    },
    "ui.popup.brief.title": {
        "zh": "🧭 BP 分析简报",
        "en": "🧭 BP scout brief",
    },
    "ui.popup.brief.map_line": {"zh": "地图：{map}", "en": "Map: {map}"},
    "ui.popup.brief.ban_section": {
        "zh": "🚫 推荐 ban",
        "en": "🚫 Bans",
    },
    "ui.popup.brief.ban_history_header": {
        "zh": "  敌方招牌：",
        "en": "  Enemy signature:",
    },
    "ui.popup.brief.ban_map_header": {
        "zh": "  本图强势（我方少用）：",
        "en": "  Strong on map (we rarely play):",
    },
    "ui.popup.brief.ban_empty": {
        "zh": "暂无显著 ban 推荐",
        "en": "no notable ban suggestions",
    },
    "ui.popup.brief.we_never_play": {"zh": "我方从不使用", "en": "we never play"},
    "ui.popup.brief.we_play_n": {"zh": "我方使用 {n} 次", "en": "we play {n}x"},
    "ui.popup.brief.pick_section": {
        "zh": "✅ 推荐 pick",
        "en": "✅ Picks",
    },
    "ui.popup.brief.pick_empty": {
        "zh": "本图暂无显著强势的英雄",
        "en": "no significantly strong picks on this map",
    },
    "ui.popup.brief.lift_above": {
        "zh": "比平均高 {lift}%",
        "en": "{lift}% above avg",
    },
    "ui.popup.brief.lift_neutral": {"zh": "与平均持平", "en": "≈ avg"},
    "ui.popup.brief.allies_section": {
        "zh": "🤝 我方队伍",
        "en": "🤝 Allies",
    },
    "ui.popup.brief.enemies_section": {
        "zh": "⚔️ 敌方队伍",
        "en": "⚔️ Enemies",
    },
    "ui.popup.brief.no_data": {
        "zh": "无本地数据",
        "en": "no local data",
    },
    "ui.popup.brief.note_not_found": {
        "zh": "本地数据库未找到",
        "en": "not in local DB",
    },
    "ui.popup.brief.summary_line": {
        "zh": "{games} 场 / {wr} / KDA {kda} · 英伤 {hd} · 治疗 {hl} · 承伤 {dt}",
        "en": "{games}G / {wr} / KDA {kda} · HeroDmg {hd} · Heal {hl} · Taken {dt}",
    },
    "ui.popup.brief.hero_chip": {
        "zh": "{hero} {games}场 {wr}",
        "en": "{hero} {games}G {wr}",
    },
    "ui.popup.brief.last_match": {
        "zh": "上次：{when} · {hero} · {result} · KDA {kda}",
        "en": "Last: {when} · {hero} · {result} · KDA {kda}",
    },
    "ui.popup.brief.flag_worst": {
        "zh": "🪦 低战力（{power}）",
        "en": "🪦 low power ({power})",
    },
    "ui.popup.brief.flag_best": {
        "zh": "👑 高战力（{power}）",
        "en": "👑 high power ({power})",
    },
    "ui.popup.brief.squad_section": {
        "zh": "🧑‍🤝‍🧑 我方本图战绩 — {map}",
        "en": "🧑‍🤝‍🧑 Squad on {map}",
    },
    "ui.popup.brief.squad_empty": {
        "zh": "暂无我方在本图的战绩",
        "en": "no squad games on this map yet",
    },
    "ui.popup.brief.squad_total": {
        "zh": "本图共 {games} 场（胜率 {wr}）",
        "en": "{games}G on map ({wr} WR)",
    },
    "ui.popup.brief.squad_no_top": {
        "zh": "暂无达到样本量的英雄",
        "en": "no hero hits the sample threshold",
    },
    "ui.popup.brief.squad_hero_line": {
        "zh": "{hero}  {wins}/{games} ({wr}) · KDA {kda} · 英伤 {hd} · 治疗 {hl} · 承伤 {dt}",
        "en": "{hero}  {wins}/{games} ({wr}) · KDA {kda} · HeroDmg {hd} · Heal {hl} · Taken {dt}",
    },

    # === Squad picker ==================================================
    "ui.squad.dialog_title": {"zh": "选择小队成员", "en": "Choose squad"},
    "ui.squad.heading": {
        "zh": "选择你的小队成员",
        "en": "Pick your squad members",
    },
    "ui.squad.subtitle": {
        "zh": "勾选与你常组队的玩家。周报和战力榜高亮都会按这份名单计算，之后可随时重选。",
        "en": "Check the players you squad with. The weekly report and the "
              "rankings highlight both follow this roster; you can re-select "
              "anytime.",
    },
    "ui.squad.search_ph": {"zh": "搜索玩家名 / handle…", "en": "Search name / handle…"},
    "ui.squad.row": {"zh": "{name}（{games} 场）", "en": "{name} ({games} games)"},
    "ui.squad.count": {"zh": "已选 {n} 人", "en": "{n} selected"},
    "ui.squad.save": {"zh": "保存名单", "en": "Save roster"},
    "ui.squad.cancel": {"zh": "取消", "en": "Cancel"},
    "ui.squad.reselect_btn": {"zh": "⚙ 重新选择小队", "en": "⚙ Re-select squad"},
    "ui.squad.gate_hint": {
        "zh": "第一次查看周报，请先选择你的小队成员。",
        "en": "First time here — choose your squad members to continue.",
    },

    # === Weekly squad report ===========================================
    "ui.weekly.btn": {"zh": "📅 本周战报", "en": "📅 Weekly report"},
    "ui.weekly.btn_tip": {
        "zh": "查看小队最近 7 天的对局总结",
        "en": "Generate a digest of the squad's last 7 days",
    },
    "ui.weekly.dialog_title": {
        "zh": "小队周报",
        "en": "Weekly squad report",
    },
    "ui.weekly.copy_btn": {"zh": "📋 复制周报", "en": "📋 Copy report"},
    "ui.weekly.copy_btn_done": {"zh": "✓ 已复制", "en": "✓ Copied"},
    "ui.weekly.empty": {
        "zh": "<i style='color:#a88;'>最近 {days} 天小队没有风暴联赛记录。</i>",
        "en": "<i style='color:#a88;'>No Storm League games for the squad in the last {days} days.</i>",
    },
    "ui.weekly.title": {
        "zh": "🗓️ 小队周报 — 最近 {days} 天",
        "en": "🗓️ Weekly squad report — last {days} days",
    },
    "ui.weekly.window_line": {
        "zh": "时间窗口：{start} → {end}",
        "en": "Window: {start} → {end}",
    },
    "ui.weekly.section.overview": {"zh": "🧭 总览", "en": "🧭 Overview"},
    "ui.weekly.overview_line": {
        "zh": "本周 {games} 场（胜率 {wr}）；上周 {prev_games} 场（胜率 {prev_wr}）。",
        "en": "This window: {games}G ({wr} WR); previous: {prev_games}G ({prev_wr} WR).",
    },
    "ui.weekly.overview_delta": {
        "zh": "对比：场次 {games_delta:+d}，胜率 {wr_delta:+.1f} pp。",
        "en": "Δ: games {games_delta:+d}, WR {wr_delta:+.1f} pp.",
    },
    "ui.weekly.section.players": {"zh": "🎮 五人战报", "en": "🎮 Squad members"},
    "ui.weekly.player_line": {
        "zh": "{name}：{games} 场（胜率 {wr}） · KDA {kda} · 主玩 {hero} ({hero_wins}/{hero_games})",
        "en": "{name}: {games}G ({wr}) · KDA {kda} · most {hero} ({hero_wins}/{hero_games})",
    },
    "ui.weekly.player_line_no_hero": {
        "zh": "{name}：{games} 场（胜率 {wr}） · KDA {kda}",
        "en": "{name}: {games}G ({wr}) · KDA {kda}",
    },
    "ui.weekly.section.awards": {"zh": "🏆 MVP 奖项", "en": "🏆 MVP awards"},
    "ui.weekly.award.god_kda":  {"zh": "KDA 最高",      "en": "Best KDA"},
    "ui.weekly.award.dmg_king": {"zh": "英雄伤害最高",   "en": "Most hero damage"},
    "ui.weekly.award.healer":   {"zh": "治疗最高",       "en": "Most healing"},
    "ui.weekly.award.tank":     {"zh": "承受伤害最高",   "en": "Most damage taken"},
    "ui.weekly.award.siege":    {"zh": "建筑伤害最高",   "en": "Most structure damage"},
    "ui.weekly.award.xp":       {"zh": "经验贡献最高",   "en": "Most XP contribution"},
    "ui.weekly.award.cc":        {"zh": "控制时间最长",   "en": "Most crowd control time"},
    "ui.weekly.award.teamfight": {"zh": "参团击杀最多",   "en": "Most takedowns"},
    "ui.weekly.award.solo_kill": {"zh": "单杀最多",       "en": "Most solo kills"},
    "ui.weekly.award.on_fire":   {"zh": "火力全开时间最长", "en": "Longest time on fire"},
    "ui.weekly.award.tf_dmg":    {"zh": "团战伤害最高",   "en": "Most teamfight damage"},
    "ui.weekly.award.soak":      {"zh": "吸收伤害最高",   "en": "Most damage soaked"},
    "ui.weekly.award.stun":      {"zh": "眩晕时间最长",   "en": "Most stun time"},
    "ui.weekly.award.protect":   {"zh": "护盾保护最多",   "en": "Most protection given"},
    "ui.weekly.award.mercs":     {"zh": "雇佣兵营占领最多", "en": "Most merc camps"},
    "ui.weekly.award.towers":    {"zh": "侦测塔占领最多", "en": "Most watch towers"},
    "ui.weekly.award.clutch":    {"zh": "关键治疗最多",   "en": "Most clutch heals"},
    "ui.weekly.award.escapes":   {"zh": "逃脱次数最多",   "en": "Most escapes"},
    "ui.weekly.award.actor":     {"zh": "阵亡时间最长",   "en": "Most time dead"},
    "ui.weekly.award.loner":     {"zh": "被以多打少最多", "en": "Most outnumbered deaths"},
    "ui.weekly.award_line": {
        "zh": "{label}：{name} · {hero} · {value} · {games} 场",
        "en": "{label}: {name} · {hero} · {value} · {games}G",
    },
    "ui.weekly.section.highlights": {"zh": "✨ 高光对局", "en": "✨ Highlight matches"},
    "ui.weekly.highlight_line": {
        "zh": "{when} · {name} · {hero} · {map} · {result} · KDA {kda} · 英伤 {hd}",
        "en": "{when} · {name} · {hero} · {map} · {result} · KDA {kda} · HeroDmg {hd}",
    },
    "ui.weekly.match_won":  {"zh": "胜", "en": "WIN"},
    "ui.weekly.match_lost": {"zh": "负", "en": "LOSS"},
    "ui.weekly.section.heroes": {"zh": "🦸 英雄池", "en": "🦸 Hero pool"},
    "ui.weekly.heroes_top_picked": {"zh": "出场最多：", "en": "Most picked:"},
    "ui.weekly.heroes_top_wr":     {"zh": "胜率最高：", "en": "Best winrate:"},
    "ui.weekly.hero_chip": {
        "zh": "{hero} {wins}/{games} ({wr})",
        "en": "{hero} {wins}/{games} ({wr})",
    },
    "ui.weekly.section.combos": {"zh": "🤝 英雄组合胜率（历史）", "en": "🤝 Best hero combos (all-time)"},
    "ui.weekly.combo_line": {
        "zh": "{hero_a} + {hero_b}：{wins}/{games}（胜率 {wr}）",
        "en": "{hero_a} + {hero_b}: {wins}/{games} ({wr})",
    },
    "ui.weekly.section.maps": {"zh": "🗺️ 地图表现", "en": "🗺️ Map breakdown"},
    "ui.weekly.map_line": {
        "zh": "{map}：{wins}/{games}（胜率 {wr}）",
        "en": "{map}: {wins}/{games} ({wr})",
    },
    "ui.weekly.section.streaks": {"zh": "🔥 连胜连败", "en": "🔥 Streaks"},
    "ui.weekly.streak_win": {
        "zh": "最长连胜：{n} 连胜（{start} → {end}）",
        "en": "Longest win streak: {n}W ({start} → {end})",
    },
    "ui.weekly.streak_loss": {
        "zh": "最长连败：{n} 连败（{start} → {end}）",
        "en": "Longest loss streak: {n}L ({start} → {end})",
    },
    "ui.weekly.streak_none_win": {"zh": "本周暂无连胜", "en": "no win streak"},
    "ui.weekly.streak_none_loss": {"zh": "本周暂无连败", "en": "no loss streak"},

    "ui.popup.region.no_screenshot_title": {"zh": "无截图", "en": "No screenshot"},
    "ui.popup.region.no_screenshot_body": {
        "zh": "当前没有可用截图。请先按快捷键截屏。",
        "en": "No screenshot is associated with this popup. Trigger the hotkey first so the app captures one.",
    },
    "ui.popup.region.cannot_open": {"zh": "无法打开框选窗口", "en": "Cannot open region selector"},
    "ui.popup.region.no_text_title": {"zh": "未识别到文字", "en": "No text recognized"},
    "ui.popup.region.no_text_body": {
        "zh": "OCR 在该区域未识别到文字。请尝试更紧贴文字的框选，或者直接手动输入名字。",
        "en": "OCR didn't find any text in that region. Try a tighter crop or type the name manually.",
    },
    "ui.popup.region.hint": {
        "zh": "在玩家名称上拖一个紧贴的矩形。Esc 取消。",
        "en": "Drag a tight rectangle over the player name. Esc to cancel.",
    },
    "ui.popup.region.title": {"zh": "选择玩家名称区域", "en": "Select player name region"},

    # === Hero ranking dialog ============================================
    "ui.aram.window_title": {"zh": "英雄强度榜", "en": "Hero strength ranking"},
    "ui.aram.title": {"zh": "{mode} 英雄强度榜", "en": "{mode} hero strength ranking"},
    "ui.aram.title_with_map": {
        "zh": "{mode} 英雄强度榜 — {map}",
        "en": "{mode} hero strength ranking — {map}",
    },
    "ui.aram.mode": {"zh": "模式：", "en": "Mode:"},
    "ui.aram.map": {"zh": "地图：", "en": "Map:"},
    "ui.aram.map_all": {"zh": "全部地图", "en": "All maps"},
    "ui.aram.mode_aram": {"zh": "天命乱斗 (ARAM)", "en": "ARAM (天命乱斗)"},
    "ui.aram.mode_sl": {"zh": "风暴联赛 (Storm League)", "en": "Storm League (风暴联赛)"},
    "ui.aram.min_games": {"zh": "最少局数：", "en": "Minimum games:"},
    "ui.aram.sort": {"zh": "排序：", "en": "Sort by:"},
    "ui.aram.sort_wr": {"zh": "胜率", "en": "Win-rate"},
    "ui.aram.sort_wlb": {"zh": "保守胜率（推荐）", "en": "Conservative win-rate (recommended)"},
    "ui.aram.sort_power": {"zh": "综合战斗力", "en": "Combat power"},
    "ui.power_help.title": {
        "zh": "战斗力是怎么算的？",
        "en": "How is combat power computed?",
    },
    "ui.power_help.btn_tip": {
        "zh": "查看战斗力的算法、权重和阈值",
        "en": "View the combat power formula, weights, and thresholds",
    },
    "ui.power_help.btn_label": {
        "zh": "❓ 战斗力是怎么算的",
        "en": "❓ How is power computed",
    },
    "ui.aram.sort_games": {"zh": "局数", "en": "Games"},
    "ui.aram.sort_hero": {"zh": "英雄名", "en": "Hero name"},
    "ui.aram.sort_tip": {
        "zh": "「胜率」=  胜场 ÷ 局数，最直观；但样本小的英雄（5 局 5 胜 = 100%）会排到最顶。\n"
              "「保守胜率」对小样本打折扣（数学上是 95% 置信下界）；BP 时按这个排更靠谱。",
        "en": "'Win-rate' is the simple ratio (wins ÷ games); a hero with only 5/5 still jumps to 100%.\n"
              "'Conservative win-rate' discounts small samples (95% lower bound); recommended for BP.",
    },
    "ui.aram.close": {"zh": "关闭", "en": "Close"},
    "ui.aram.search_label": {"zh": "搜索英雄：", "en": "Search hero:"},
    "ui.aram.search_placeholder": {
        "zh": "输入英雄名（支持部分匹配）",
        "en": "type a hero name (partial match)",
    },
    "ui.aram.no_match": {"zh": "<span style='color:#d99;'>无匹配</span>", "en": "<span style='color:#d99;'>no match</span>"},
    "ui.aram.matches": {
        "zh": "<span style='color:#9d9;'>{n} 个匹配 — 按回车跳转到第一个</span>",
        "en": "<span style='color:#9d9;'>{n} match(es) — press Enter to jump</span>",
    },
    "ui.aram.summary": {
        "zh": "数据库样本：{games} 局 {mode}（合计 {pm} 个英雄选用记录）· 展示 {ranked} 个英雄（≥ {min_games} 局）",
        "en": "DB sample: {games} {mode} games ({pm} hero picks total) · showing {ranked} heroes (≥ {min_games} games)",
    },
    "ui.aram.col_rank": {"zh": "排名", "en": "Rank"},
    "ui.aram.col_hero": {"zh": "英雄", "en": "Hero"},
    "ui.aram.col_games": {"zh": "局数", "en": "Games"},
    "ui.aram.col_wins": {"zh": "胜场", "en": "Wins"},
    "ui.aram.col_wr": {"zh": "胜率", "en": "Win-rate"},
    "ui.aram.col_wlb": {"zh": "保守胜率", "en": "Conservative WR"},
    "ui.aram.col_kda": {"zh": "K/D/A", "en": "K/D/A"},
    "ui.aram.col_hero_dmg": {"zh": "英雄伤害", "en": "Hero dmg"},
    "ui.aram.col_dmg_taken": {"zh": "承受伤害", "en": "Dmg taken"},
    "ui.aram.col_healing": {"zh": "治疗", "en": "Healing"},
    "ui.aram.col_struct": {"zh": "推塔", "en": "Structure"},
    "ui.aram.col_xp": {"zh": "XP", "en": "XP"},
    "ui.aram.footer": {
        "zh": "<span style='color:#888;'>"
              "<b>胜率</b> = 胜场 ÷ 局数；"
              "<b>保守胜率</b> 会对小样本打折扣（5 局 5 胜的胜率是 100% 但保守胜率只有 56%；"
              "70 局 50 胜（71%）的保守胜率是 60%）。BP 时按保守胜率排序更靠谱，避免被「3 战 3 胜」之类的小样本误导。"
              "</span>",
        "en": "<span style='color:#888;'>"
              "<b>Win-rate</b> = wins ÷ games. "
              "<b>Conservative win-rate</b> discounts small samples: a hero with 5/5 has 100% WR but only 56% conservative; "
              "50/70 (71%) becomes 60% conservative. Sort by conservative WR for BP to avoid being misled by tiny samples like 3/3."
              "</span>",
    },
}

_current = _DEFAULT
_observers: list[Callable[[str], None]] = []


def t(key: str, **kwargs) -> str:
    """Translate ``key`` to the current locale; format with ``kwargs``."""
    entry = _translations.get(key)
    if entry is None:
        return key
    text = entry.get(_current) or entry.get(_DEFAULT) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except Exception:
            return text
    return text


def language() -> str:
    return _current


def set_language(code: str) -> None:
    global _current
    if code not in _LOCALES:
        return
    if code == _current:
        return
    _current = code
    for obs in list(_observers):
        try:
            obs(code)
        except Exception:
            pass


def on_change(callback: Callable[[str], None]) -> None:
    """Register a callback that fires whenever the language changes."""
    _observers.append(callback)


def available_languages() -> tuple[tuple[str, str], ...]:
    return (("zh", "中文"), ("en", "English"))
