import { pct } from "../../lib/format";
import type { TalentBuild } from "../../lib/types";
import "./talents.css";

type Mode = "standard" | "aram";

interface TalentBuildSectionProps {
  mode: Mode;
  onModeChange: (mode: Mode) => void;
  loading: boolean;
  error: string | null;
  build: TalentBuild | null;
  hero?: string;
}

const MODE_TABS: { value: Mode; label: string }[] = [
  { value: "standard", label: "风暴联赛 / 快速" },
  { value: "aram", label: "天命乱斗" },
];

/**
 * Winrate-based recommended talent build for a hero, with a mode toggle.
 * The recommended pick per tier (highest confidence-adjusted win-rate)
 * is highlighted; the other choices in that tier are listed as faint
 * alternatives so the user can judge the call.
 */
export default function TalentBuildSection({
  mode,
  onModeChange,
  loading,
  error,
  build,
  hero,
}: TalentBuildSectionProps) {
  return (
    <section className="card">
      <div className="talents-head">
        <h2 className="section-title">
          {hero ? `${hero} · 天赋加点推荐（按胜率）` : "天赋加点推荐（按胜率）"}
        </h2>
        <div className="mode-tabs" role="tablist" aria-label="模式">
          {MODE_TABS.map((m) => (
            <button
              key={m.value}
              role="tab"
              aria-selected={mode === m.value}
              className={`mode-tab${mode === m.value ? " mode-tab--on" : ""}`}
              onClick={() => onModeChange(m.value)}
            >
              {m.label}
            </button>
          ))}
        </div>
      </div>

      {loading && <p className="muted">加载中…</p>}
      {error && <p className="muted">加载失败：{error}</p>}

      {!loading && !error && build && build.tiers.length === 0 && (
        <p className="muted">该模式下还没有这个英雄的天赋数据。</p>
      )}

      {!loading && !error && build && build.tiers.length > 0 && (
        <>
          <p className="muted talents-summary">
            {modeLabel(build.mode_group)} · {build.total_games} 场 · 胜率{" "}
            {pct(build.win_rate)}
          </p>
          <div className="talent-tiers">
            {build.tiers.map((tier) => {
              const rec = tier.recommended;
              const alts = tier.choices.filter(
                (c) => c.talent !== rec?.talent,
              );
              return (
                <div key={tier.tier} className="talent-tier">
                  <div className="talent-tier__label">T{tier.tier}</div>
                  <div className="talent-tier__body">
                    {rec && (
                      <div className="talent-pick talent-pick--rec">
                        <span className="talent-pick__name">
                          {rec.talent_label}
                        </span>
                        <span className="talent-pick__stat">
                          选择率 {pct(rec.pick_rate)} · 胜率 {pct(rec.win_rate)}
                        </span>
                      </div>
                    )}
                    {alts.length > 0 && (
                      <div className="talent-alts">
                        {alts.map((c) => (
                          <span key={c.talent} className="talent-alt">
                            {c.talent_label}
                            <span className="talent-alt__wr">
                              {" "}
                              选择率 {pct(c.pick_rate)} · 胜率 {pct(c.win_rate)}
                            </span>
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </section>
  );
}

function modeLabel(group: string): string {
  return group === "aram" ? "天命乱斗" : "风暴联赛 / 快速";
}
