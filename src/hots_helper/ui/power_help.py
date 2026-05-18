"""Read-only dialog explaining how the combat-power score is built.

The number that lights up on every leaderboard row hides a lot of
moving parts (per-match percentile baselines, role-contribution
thresholds, two-stage rerank). Rather than describe it in cramped
tooltips we surface a "?" button next to the sort dropdown that
opens this dialog. Content is generated from the actual constants
in :mod:`hots_helper.player_rank` and
:data:`hots_helper.db.store._METRIC_CONTRIB_THRESHOLDS` so it can
never go stale relative to the live formula.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QLabel,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..i18n import t
from ..player_rank import _POWER_WEIGHTS


# Friendly labels for each metric key in _POWER_WEIGHTS.
_METRIC_LABELS_ZH: dict[str, str] = {
    "win_rate":          "胜率",
    "kda":               "KDA = (击杀+助攻)/死亡",
    "hero_damage":       "英雄伤害",
    "siege_damage":      "攻城伤害",
    "structure_damage":  "推塔伤害",
    "healing":           "治疗",
    "damage_soaked":     "硬币（吸收兵线/塔伤害）",
    "damage_taken":      "承受伤害（坦克扛伤）",
    "experience":        "经验贡献",
    "cc":                "控制时间",
    "deaths_inverse":    "死亡（越少越好）",
}

# Metrics that have a role-contribution threshold (mirrors
# db.store._METRIC_CONTRIB_THRESHOLDS, restated here so the help
# dialog can describe it without importing the SQL helpers).
_THRESHOLD_DESCRIPTIONS_ZH: dict[str, str] = {
    "healing":          "治疗 > 1000：低于此值视为不在治疗位（避免自吸误算）",
    "damage_soaked":    "硬币 > 5000：低于此值视为没真正吃线",
    "structure_damage": "推塔伤害 > 1000：低于此值视为擦伤",
    "siege_damage":     "攻城伤害 > 5000：低于此值视为没真正推线",
    "damage_taken":     "承受伤害 > 30000：低于此值视为不是前排",
    "cc":               "控制 > 1.0 秒：低于此值视为戳一下",
}


class PowerHelpDialog(QDialog):
    """Modal that explains the combat-power formula.

    Plain rich-text label, scrollable, modeled after the existing
    leaderboard footer styling. Open via the ``?`` button on either
    the player or hero ranking dialog.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(t("ui.power_help.title"))
        self.resize(640, 640)

        outer = QVBoxLayout(self)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_v = QVBoxLayout(body)

        label = QLabel(self._build_html())
        label.setTextFormat(Qt.RichText)
        label.setWordWrap(True)
        label.setOpenExternalLinks(False)
        body_v.addWidget(label)
        body_v.addStretch(1)

        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        outer.addWidget(buttons)

    def _build_html(self) -> str:
        # Sort weights descending so the most influential metrics
        # show up first — easier to skim.
        weights = sorted(
            _POWER_WEIGHTS.items(), key=lambda kv: -kv[1]
        )

        rows = []
        for key, w in weights:
            label = _METRIC_LABELS_ZH.get(key, key)
            rows.append(
                f"<tr>"
                f"<td style='padding:3px 12px 3px 0;'>{label}</td>"
                f"<td style='padding:3px 0; color:#f4c453;"
                f" text-align:right;'>{w*100:.0f}%</td>"
                f"</tr>"
            )
        weights_table = (
            "<table style='border-collapse:collapse;'>"
            + "".join(rows)
            + "</table>"
        )

        threshold_rows = "".join(
            f"<li>{desc}</li>"
            for desc in _THRESHOLD_DESCRIPTIONS_ZH.values()
        )

        return f"""
        <h3 style="color:#f4c453;">什么是“战斗力”</h3>

        <p>把多项对局数据按权重综合后，再换算成
        <b>“你超过了多少百分比的对局表现”</b> 的 0–100 分数。
        越高代表整体表现越强。</p>

        <h3 style="color:#f4c453;">两步算法</h3>
        <ol>
          <li><b>第一步：加权百分位平均。</b>
              先把每项指标在<b>整库的全部对局</b>里取百分位（你比多少局表现得更好），
              然后按下表权重做加权平均，得到一个原始分。</li>
          <li><b>第二步：再做一次百分位。</b>
              把所有人的原始分排序，再查你的原始分在其中排第几。
              这一步保证最终分数读起来就是
              <b>“我超过了 X% 的对局表现”</b>，并自然压平任一指标的极端值。</li>
        </ol>

        <h3 style="color:#f4c453;">权重表</h3>
        {weights_table}

        <h3 style="color:#f4c453;">角色贡献阈值（避免错位计分）</h3>
        <p>很多指标只有特定角色才该有数据：法师的治疗、刺客的硬币
        基本是 0；如果直接平均，会把这些 0 算进去拉低真治疗位的得分。
        所以下面这些指标设了贡献阈值——低于阈值的对局当作
        <b>“没在干这件事”</b>，不计入平均，也不会扣分（权重会
        重新分配给真正参与的指标）：</p>
        <ul>{threshold_rows}</ul>

        <h3 style="color:#f4c453;">举例</h3>
        <ul>
          <li><b>纯刺客</b>：胜率 / KDA / 英雄伤害 / 经验 / 死亡都参与；
              治疗 / 硬币 / 抗伤都低于阈值，跳过——所以纯刺客也能拿满分，
              不会因为不治疗就被压在 73 上限。</li>
          <li><b>纯坦克</b>：抗伤 / 硬币 / 控制都参与；英雄伤害可能低
              但只占 11% 权重。</li>
          <li><b>flex 玩家</b>：治疗、抗伤这种角色专项指标
              <i>只统计他真做这件事的对局</i>，不会被自己的输出局稀释。</li>
        </ul>

        <p style="color:#888; font-size:9pt;">
        权重和阈值定义在 <code>player_rank.py</code> 与
        <code>db/store.py</code>。本帮助内容由代码常量自动生成，永远和实际算法一致。
        </p>
        """
