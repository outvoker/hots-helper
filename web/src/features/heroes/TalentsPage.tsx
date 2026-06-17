import { useMemo, useState } from "react";
import { api } from "../../lib/api";
import { ErrorState, Loading, PageHead, useAsync } from "../../components/common";
import TalentBuildSection from "./TalentBuildSection";

type Mode = "standard" | "aram";

/**
 * Standalone talent-build entry point: pick a hero, see the win-rate
 * recommended build per tier. Separate from the hero strength board so
 * it's a first-class feature rather than buried in a detail page.
 */
export default function TalentsPage() {
  const heroes = useAsync(() => api.heroes(), []);
  const [hero, setHero] = useState("");
  const [mode, setMode] = useState<Mode>("standard");
  const [query, setQuery] = useState("");

  const build = useAsync(
    () => api.heroTalents(hero, mode),
    [hero, mode],
    Boolean(hero),
  );

  const names = useMemo(
    () =>
      (heroes.data ?? [])
        .map((h) => h.hero)
        .sort((a, b) => a.localeCompare(b, "zh")),
    [heroes.data],
  );
  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return q ? names.filter((n) => n.toLowerCase().includes(q)) : names;
  }, [names, query]);

  if (heroes.loading) return <Loading />;
  if (heroes.error) return <ErrorState message={heroes.error} />;

  return (
    <>
      <PageHead
        title="天赋推荐"
        subtitle="选择英雄，查看按胜率推荐的各层天赋加点"
      />

      <div className="filters">
        <input
          type="search"
          placeholder="搜索英雄…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="搜索英雄"
        />
        <label className="muted">
          英雄：
          <select value={hero} onChange={(e) => setHero(e.target.value)}>
            <option value="">— 请选择 —</option>
            {filtered.map((n) => (
              <option key={n} value={n}>
                {n}
              </option>
            ))}
          </select>
        </label>
      </div>

      {!hero ? (
        <p className="muted">先选择一个英雄。</p>
      ) : (
        <TalentBuildSection
          hero={hero}
          mode={mode}
          onModeChange={setMode}
          loading={build.loading}
          error={build.error}
          build={build.data}
        />
      )}
    </>
  );
}
