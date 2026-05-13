"""`hots` command-line interface."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from ..db import Store, default_db_path
from ..lookup import lookup_players
from ..watcher import ingest_directory, ingest_file, watch

app = typer.Typer(add_completion=False, help="Heroes of the Storm replay ingest + lookup")
console = Console()

DEFAULT_RECORDINGS = Path(__file__).resolve().parents[3] / "recordings"


def _open_store(db_path: Path | None) -> Store:
    return Store(db_path or default_db_path())


@app.command()
def scan(
    directory: Annotated[Path, typer.Argument(help="Directory to scan")] = DEFAULT_RECORDINGS,
    db_path: Annotated[Path | None, typer.Option("--db", help="Override DB path")] = None,
) -> None:
    """One-shot ingest of all replays in DIRECTORY."""
    if not directory.exists():
        console.print(f"[red]Directory not found: {directory}[/red]")
        raise typer.Exit(1)
    with _open_store(db_path) as store:
        results = ingest_directory(store, directory)
        new = sum(1 for r in results if r.inserted)
        dup = sum(1 for r in results if r.ok and not r.inserted)
        err = sum(1 for r in results if not r.ok)
        for r in results:
            if r.error:
                console.print(f"[red]x[/red] {r.path.name}: {r.error}")
            elif r.inserted:
                console.print(f"[green]+[/green] {r.path.name}")
            elif r.reason == "match-dup":
                console.print(f"[yellow]~[/yellow] {r.path.name} (same match already in DB — different perspective)")
            else:
                console.print(f"[dim]= {r.path.name}[/dim]")
        console.print(
            f"[bold]Scan complete[/bold]: {new} new, {dup} already ingested, {err} errors. "
            f"DB now has {store.count_replays()} replays and {store.count_players()} players."
        )


@app.command()
def watch_cmd(
    directory: Annotated[Path, typer.Argument(help="Directory to watch")] = DEFAULT_RECORDINGS,
    db_path: Annotated[Path | None, typer.Option("--db", help="Override DB path")] = None,
    no_bootstrap: Annotated[bool, typer.Option("--no-bootstrap", help="Skip initial scan of existing files")] = False,
) -> None:
    """Watch DIRECTORY and ingest new replays as they appear."""
    if not directory.exists():
        console.print(f"[red]Directory not found: {directory}[/red]")
        raise typer.Exit(1)
    with _open_store(db_path) as store:
        watch(store, directory, bootstrap=not no_bootstrap, console=console)


# Typer names the command after the function; override so we get `hots watch`.
app.command(name="watch")(watch_cmd)


@app.command()
def ingest(
    file: Annotated[Path, typer.Argument(help="Replay file to ingest")],
    db_path: Annotated[Path | None, typer.Option("--db", help="Override DB path")] = None,
) -> None:
    """Ingest a single replay file."""
    with _open_store(db_path) as store:
        result = ingest_file(store, file)
        if result.error:
            console.print(f"[red]x[/red] {file.name}: {result.error}")
            raise typer.Exit(1)
        elif result.inserted:
            console.print(f"[green]+[/green] ingested {file.name} (replay_id={result.replay_id})")
        else:
            console.print(f"[dim]= already known: {file.name}[/dim]")


@app.command()
def players(
    db_path: Annotated[Path | None, typer.Option("--db", help="Override DB path")] = None,
) -> None:
    """List all players in the database."""
    with _open_store(db_path) as store:
        rows = store.conn.execute(
            "SELECT toon_handle, display_name, last_seen_at, "
            "(SELECT COUNT(*) FROM player_match WHERE toon_handle = p.toon_handle) AS games "
            "FROM players p ORDER BY games DESC, last_seen_at DESC"
        ).fetchall()
    table = Table(title=f"Players ({len(rows)})")
    table.add_column("Handle")
    table.add_column("Name")
    table.add_column("Games", justify="right")
    table.add_column("Last seen")
    for r in rows:
        table.add_row(r["toon_handle"], r["display_name"], str(r["games"]), r["last_seen_at"])
    console.print(table)


def _hero_row(h):  # type: ignore[no-untyped-def]
    return (
        h.hero,
        str(h.games),
        f"{h.winrate*100:.0f}%",
        f"{h.avg_k:.1f}/{h.avg_d:.1f}/{h.avg_a:.1f}",
        f"{h.avg_hero_dmg:,.0f}",
        f"{h.avg_siege_dmg:,.0f}",
        f"{h.avg_healing:,.0f}",
        f"{h.avg_cc:.0f}s",
    )


def _hero_table(title: str) -> Table:
    t = Table(title=title, show_edge=False)
    for col in ("Hero", "G", "WR", "K/D/A", "HeroDmg", "Siege", "Heal", "CC"):
        t.add_column(col, justify="right" if col not in ("Hero",) else "left")
    return t


@app.command()
def lookup(
    names: Annotated[list[str], typer.Argument(help="Player display names to look up")],
    map_name: Annotated[str | None, typer.Option("--map", help="Focus on a particular map")] = None,
    db_path: Annotated[Path | None, typer.Option("--db", help="Override DB path")] = None,
    top: Annotated[int, typer.Option("--top", help="How many heroes to show per player")] = 5,
    recent: Annotated[int, typer.Option("--recent", help="How many recent matches to show per player")] = 5,
) -> None:
    """Look up one or more players by display name and show ban/pick-useful stats."""
    with _open_store(db_path) as store:
        results = lookup_players(store, names, map_name=map_name, top_n_heroes=top, recent_n=recent)

    for name, summaries in results.items():
        for summary in summaries:
            header = f"[bold cyan]{name}[/bold cyan]"
            if summary.display_name != name:
                header += f" (stored as {summary.display_name})"
            if summary.toon_handle:
                header += f"  [dim]{summary.toon_handle}[/dim]"
            console.rule(header)
            if summary.note:
                console.print(f"[yellow]{summary.note}[/yellow]")
                continue

            k, d, a = summary.overall_kda
            console.print(
                f"[bold]All[/bold]: {summary.total_games} games, {summary.total_wins}W "
                f"({summary.winrate*100:.0f}% WR), KDA {k:.1f}/{d:.1f}/{a:.1f}"
            )
            if summary.recent_games:
                console.print(
                    f"[bold]Last 30d[/bold]: {summary.recent_games} games, "
                    f"{summary.recent_wins}W ({summary.recent_winrate*100:.0f}% WR)"
                )
            if map_name and summary.map_games:
                console.print(
                    f"[bold]On {map_name}[/bold]: {summary.map_games} games, "
                    f"{summary.map_wins}W ({summary.map_winrate*100:.0f}% WR)"
                )

            if summary.ban_recommendations:
                t = _hero_table("🚫 Ban candidates")
                for h in summary.ban_recommendations:
                    t.add_row(*_hero_row(h))
                console.print(t)

            if summary.signature_heroes:
                t = _hero_table("Signature heroes")
                for h in summary.signature_heroes[:top]:
                    t.add_row(*_hero_row(h))
                console.print(t)

            if map_name and summary.map_heroes:
                t = _hero_table(f"On {map_name}")
                for h in summary.map_heroes:
                    t.add_row(*_hero_row(h))
                console.print(t)

            if summary.frequent_teammates:
                t = Table(title="Frequent teammates", show_edge=False)
                for col in ("Name", "Games", "Shared WR"):
                    t.add_column(col)
                for m in summary.frequent_teammates:
                    t.add_row(
                        m.display_name,
                        str(m.games),
                        f"{m.shared_winrate*100:.0f}%",
                    )
                console.print(t)

            if summary.recent_matches:
                t = Table(title="Recent matches", show_edge=False)
                for col in ("When (UTC)", "Map", "Mode", "Hero", "Result", "K/D/A", "HeroDmg", "Heal"):
                    t.add_column(col)
                for m in summary.recent_matches:
                    t.add_row(
                        m.played_at[:19],
                        m.map_name,
                        m.mode,
                        m.hero,
                        "WIN" if m.result == 1 else "LOSS",
                        f"{m.kills}/{m.deaths}/{m.assists}",
                        f"{m.hero_damage:,}",
                        f"{m.healing:,}",
                    )
                console.print(t)


@app.command()
def heroes(
    db_path: Annotated[Path | None, typer.Option("--db", help="Override DB path")] = None,
    min_games: Annotated[int, typer.Option("--min", help="Only heroes with this many games")] = 1,
) -> None:
    """List every hero seen in the DB with winrate."""
    with _open_store(db_path) as store:
        rows = [r for r in store.all_heroes() if int(r["games"]) >= min_games]
    t = Table(title=f"Heroes ({len(rows)})")
    for col in ("Hero", "G", "W", "WR", "K/D/A", "HeroDmg", "Siege", "Heal"):
        t.add_column(col, justify="right" if col not in ("Hero",) else "left")
    for r in rows:
        games = int(r["games"])
        wins = int(r["wins"] or 0)
        t.add_row(
            r["hero"],
            str(games),
            str(wins),
            f"{(wins/games*100 if games else 0):.0f}%",
            f"{float(r['avg_k'] or 0):.1f}/{float(r['avg_d'] or 0):.1f}/{float(r['avg_a'] or 0):.1f}",
            f"{float(r['avg_hero_dmg'] or 0):,.0f}",
            f"{float(r['avg_siege_dmg'] or 0):,.0f}",
            f"{float(r['avg_healing'] or 0):,.0f}",
        )
    console.print(t)


@app.command()
def hero(
    hero_name: Annotated[str, typer.Argument(help="Hero display name (as in replay)")],
    map_name: Annotated[str | None, typer.Option("--map", help="Focus on one map for talents")] = None,
    db_path: Annotated[Path | None, typer.Option("--db", help="Override DB path")] = None,
) -> None:
    """Deep-dive a single hero: overall, per-map, and talent picks."""
    with _open_store(db_path) as store:
        overall = store.hero_overall(hero_name)
        if not overall or not int(overall["games"] or 0):
            console.print(f"[yellow]No data for hero '{hero_name}'[/yellow]")
            return

        g = int(overall["games"])
        w = int(overall["wins"] or 0)
        wr = w / g * 100 if g else 0
        console.print(
            f"[bold cyan]{hero_name}[/bold cyan] — {g} games, {w}W ({wr:.0f}% WR), "
            f"avg K/D/A {float(overall['avg_k'] or 0):.1f}/"
            f"{float(overall['avg_d'] or 0):.1f}/"
            f"{float(overall['avg_a'] or 0):.1f}"
        )

        from ..stats import two_proportion_z_test, wilson_lower_bound

        maps = store.hero_by_map(hero_name)
        # Sort by Wilson lower bound so small samples don't claim the top slot.
        maps_ranked = sorted(
            maps,
            key=lambda m: -wilson_lower_bound(int(m["wins"] or 0), int(m["games"])),
        )
        if maps_ranked:
            t = Table(title="Map winrates (ranked by Wilson lower bound)", show_edge=False)
            for col in ("Map", "G", "W", "WR", "WLB", "vs All", "p", ""):
                t.add_column(col, justify="right" if col != "Map" else "left")
            for m in maps_ranked:
                mg = int(m["games"])
                mw = int(m["wins"] or 0)
                wr = mw / mg * 100 if mg else 0
                wlb = wilson_lower_bound(mw, mg) * 100
                other_g = g - mg
                other_w = w - mw
                test = two_proportion_z_test(mw, mg, other_w, other_g)
                marker = ""
                if test.p_value < 0.05:
                    marker = "[green]✓✓[/green]" if test.lift > 0 else "[red]✗✗[/red]"
                elif test.p_value < 0.10:
                    marker = "[green]✓[/green]" if test.lift > 0 else "[red]✗[/red]"
                t.add_row(
                    m["map_name"],
                    str(mg),
                    str(mw),
                    f"{wr:.0f}%",
                    f"{wlb:.0f}%",
                    f"{test.lift*100:+.0f}pp",
                    f"{test.p_value:.2f}",
                    marker,
                )
            console.print(t)

        talents = store.hero_talents(hero_name, map_name=map_name)
        if talents:
            title = f"Talent picks" + (f" on {map_name}" if map_name else "")
            t = Table(title=title + "  (ranked by Wilson lower bound per tier)", show_edge=False)
            for col in ("Tier", "Talent", "G", "W", "Pick", "WR", "WLB"):
                t.add_column(col, justify="right" if col not in ("Talent",) else "left")
            # Compute per-tier total games for pick rate.
            total_by_tier: dict[int, int] = {}
            for r in talents:
                total_by_tier[r["tier"]] = total_by_tier.get(r["tier"], 0) + int(r["games"])
            # Re-rank within each tier by Wilson lower bound.
            by_tier: dict[int, list[dict]] = {}
            for r in talents:
                by_tier.setdefault(r["tier"], []).append(dict(r))
            for tier, lst in by_tier.items():
                lst.sort(key=lambda r: -wilson_lower_bound(int(r["wins"]), int(r["games"])))
            last_tier = None
            for tier in sorted(by_tier):
                for r in by_tier[tier]:
                    if last_tier is not None and tier != last_tier:
                        t.add_section()
                    last_tier = tier
                    g = int(r["games"])
                    w = int(r["wins"])
                    pick_rate = g / total_by_tier[tier] * 100 if total_by_tier.get(tier) else 0
                    wr = w / g * 100 if g else 0
                    wlb = wilson_lower_bound(w, g) * 100
                    t.add_row(
                        str(tier),
                        r["talent"],
                        str(g),
                        str(w),
                        f"{pick_rate:.0f}%",
                        f"{wr:.0f}%",
                        f"{wlb:.0f}%",
                    )
            console.print(t)


@app.command("bp")
def bp_cmd(
    map_name: Annotated[str, typer.Argument(help="Current draft map, e.g. 白银城")],
    opponents: Annotated[list[str] | None, typer.Option("--enemy", "-e", help="Opponent player name (repeat up to 5 times)")] = None,
    bans: Annotated[list[str] | None, typer.Option("--ban", "-b", help="Heroes already banned")] = None,
    picks: Annotated[list[str] | None, typer.Option("--pick", "-p", help="Heroes already picked")] = None,
    min_games: Annotated[int, typer.Option("--min", help="Minimum games on a hero to trust its stats")] = 5,
    db_path: Annotated[Path | None, typer.Option("--db", help="Override DB path")] = None,
) -> None:
    """Full BP advisor: ban suggestions from enemy history, pick suggestions from map stats."""
    from ..bp import recommend_bans, recommend_picks, profile_opponents

    with _open_store(db_path) as store:
        opponent_names = [o for o in (opponents or []) if o]

        # Opponent threat breakdown (context for the ban list).
        if opponent_names:
            profiles = profile_opponents(store, opponent_names, min_games=min_games)
            console.rule("[bold cyan]Enemy team scouting[/bold cyan]")
            for prof in profiles:
                header = f"[bold]{prof.display_name}[/bold]"
                if prof.total_games:
                    header += f"  [dim]{prof.total_games} games in DB[/dim]"
                console.print(header)
                if prof.note:
                    console.print(f"  [yellow]{prof.note}[/yellow]")
                    continue
                for t in prof.threats:
                    console.print(
                        f"  {t.hero:<12}  {t.games:>3}G {t.wins:>2}W  "
                        f"WR {t.raw_winrate*100:>3.0f}%  "
                        f"Lift {t.lift_pp:+.0f}pp  p={t.p_value:.2f}  "
                        f"K/D/A {t.avg_k:.1f}/{t.avg_d:.1f}/{t.avg_a:.1f}"
                    )
                console.print()

            console.rule("[bold red]🚫 Ban candidates[/bold red]")
            already = set(bans or []) | set(picks or [])
            ban_recs = recommend_bans(
                store,
                opponent_names,
                min_games=min_games,
                already_banned=already,
            )
            if not ban_recs:
                console.print("[dim]No ban candidates: opponents have no signature heroes meeting the threshold.[/dim]")
            else:
                t = Table(show_edge=False)
                for col in ("Hero", "Score", "Combined", "Threat from"):
                    t.add_column(col, justify="right" if col in ("Score",) else "left")
                for cand in ban_recs:
                    contributors = ", ".join(
                        f"{name} ({w}/{g} WLB {wlb*100:.0f}%)"
                        for name, g, w, wlb in cand.contributors
                    )
                    t.add_row(
                        cand.hero,
                        f"{cand.score:.2f}",
                        f"{cand.total_wins}/{cand.total_games}  ({cand.combined_wr*100:.0f}%)",
                        contributors,
                    )
                console.print(t)

        console.rule(f"[bold green]✅ Pick candidates on {map_name}[/bold green]")
        exclude = set(bans or []) | set(picks or [])
        pick_recs = recommend_picks(
            store,
            map_name,
            min_games=min_games,
            exclude_heroes=exclude,
        )
        if not pick_recs:
            console.print(
                f"[dim]No strong picks surfaced. "
                f"Either not enough data on this map yet, or no hero meets --min {min_games} with "
                f"winrate confidence.[/dim]"
            )
            return
        t = Table(show_edge=False)
        for col in ("Hero", "G", "W", "WR", "WLB", "vs All", "p", "Build"):
            t.add_column(col, justify="right" if col not in ("Hero", "Build") else "left")
        for cand in pick_recs:
            sig = "[green]✓[/green]" if cand.significant else ""
            build = ", ".join(f"T{tp.tier}:{tp.talent}" for tp in cand.recommended_build)
            t.add_row(
                f"{sig} {cand.hero}".strip(),
                str(cand.map_games),
                str(cand.map_wins),
                f"{cand.map_winrate*100:.0f}%",
                f"{cand.map_wilson_lb*100:.0f}%",
                f"{cand.lift_pp:+.0f}pp",
                f"{cand.p_value:.2f}",
                build,
            )
        console.print(t)


@app.command("map")
def map_cmd(
    map_name: Annotated[str, typer.Argument(help="Map name, e.g. 白银城")],
    min_games: Annotated[int, typer.Option("--min", help="Minimum games on this map")] = 3,
    db_path: Annotated[Path | None, typer.Option("--db", help="Override DB path")] = None,
    alpha: Annotated[float, typer.Option("--alpha", help="Significance threshold")] = 0.10,
) -> None:
    """Heroes that are *statistically* strong or weak on a given map.

    Winrate alone lies at small sample sizes, so we apply two filters:

    1. Drop anything below ``--min`` games on this map.
    2. Test each survivor against that hero's *global* winrate (from every
       other map combined) using a two-proportion z-test. We keep heroes whose
       on-map performance deviates significantly (default p < 0.10).

    Results are ranked by the Wilson 95% lower bound of the on-map winrate so
    that "4/4 100%" doesn't beat "14/20 70%".
    """
    from ..stats import two_proportion_z_test, wilson_lower_bound

    with _open_store(db_path) as store:
        raw = store.map_hero_winrates(map_name)
        if not raw:
            console.print(f"[yellow]No data for map '{map_name}'[/yellow]")
            return

        rows: list[dict[str, object]] = []
        for r in raw:
            hero = r["hero"]
            m_games = int(r["games"])
            m_wins = int(r["wins"] or 0)
            if m_games < min_games:
                continue

            g_games, g_wins = store.global_hero_winrate(hero)
            other_games = g_games - m_games
            other_wins = g_wins - m_wins
            test = two_proportion_z_test(m_wins, m_games, other_wins, other_games)
            rows.append(
                {
                    "hero": hero,
                    "m_games": m_games,
                    "m_wins": m_wins,
                    "m_wr": m_wins / m_games if m_games else 0.0,
                    "m_wlb": wilson_lower_bound(m_wins, m_games),
                    "global_games": g_games,
                    "global_wins": g_wins,
                    "global_wr": (g_wins / g_games) if g_games else 0.0,
                    "lift": test.lift,
                    "p": test.p_value,
                    "dir": test.direction if test.p_value < alpha else "neutral",
                }
            )

        rows.sort(key=lambda x: -float(x["m_wlb"]))  # type: ignore[arg-type]

        console.print(
            f"[bold cyan]{map_name}[/bold cyan]: "
            f"{len(raw)} heroes seen, {sum(1 for r in raw if int(r['games']) >= min_games)} "
            f"meet --min {min_games}"
        )

        strong = [r for r in rows if r["dir"] == "better"]
        weak = [r for r in rows if r["dir"] == "worse"]
        neutral = [r for r in rows if r["dir"] == "neutral"]

        def _emit(title: str, data: list[dict[str, object]], color: str) -> None:
            if not data:
                return
            t = Table(title=title, show_edge=False)
            for col in ("Hero", "Map G", "Map W", "Map WR", "WLB", "All WR", "Δ", "p"):
                t.add_column(col, justify="right" if col != "Hero" else "left")
            for r in data:
                t.add_row(
                    str(r["hero"]),
                    str(r["m_games"]),
                    str(r["m_wins"]),
                    f"{float(r['m_wr'])*100:.0f}%",
                    f"{float(r['m_wlb'])*100:.0f}%",
                    f"{float(r['global_wr'])*100:.0f}%",
                    f"{float(r['lift'])*100:+.0f}pp",
                    f"{float(r['p']):.3f}",
                )
            console.print(f"[bold {color}]{title}[/bold {color}]")
            console.print(t)

        _emit(f"✅ Significantly better on {map_name} (p < {alpha})", strong, "green")
        _emit(f"⚠️  Significantly worse on {map_name} (p < {alpha})", weak, "red")
        if neutral:
            console.print(
                f"[dim]{len(neutral)} heroes didn't show a significant deviation at p<{alpha}.[/dim]"
            )


@app.command()
def stats(
    db_path: Annotated[Path | None, typer.Option("--db", help="Override DB path")] = None,
) -> None:
    """Show database summary."""
    with _open_store(db_path) as store:
        console.print(f"Replays: [bold]{store.count_replays()}[/bold]")
        console.print(f"Players: [bold]{store.count_players()}[/bold]")
        rows = store.conn.execute(
            "SELECT mode, COUNT(*) AS n FROM replays GROUP BY mode ORDER BY n DESC"
        ).fetchall()
        if rows:
            console.print("By mode:")
            for r in rows:
                console.print(f"  {r['mode']}: {r['n']}")


if __name__ == "__main__":
    app()
