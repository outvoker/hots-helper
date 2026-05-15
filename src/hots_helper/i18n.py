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
    "ui.popup.card.hero_line_main": {
        "zh": "{games} 场 {wr}%  K/D/A {kda}",
        "en": "{games}G {wr}%  K/D/A {kda}",
    },
    "ui.popup.card.hero_line_metrics": {
        "zh": "英伤 {hd} · 承伤 {dt} · 推塔 {strd} · 治疗 {hl} · XP {xp} · 控时 {cc}s",
        "en": "HeroDmg {hd} · Taken {dt} · Struct {strd} · Heal {hl} · XP {xp} · CC {cc}s",
    },
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
