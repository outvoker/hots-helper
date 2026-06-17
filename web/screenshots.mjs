// One-off responsive screenshot harness. Boots nothing real: it mocks the
// /api/* responses with Playwright route interception so pages render with
// representative data, then captures key pages at phone / tablet / desktop.
//
//   node screenshots.mjs            # against `vite preview` (build output)
//
// Output: ./shots/<page>-<width>.png
import { chromium, devices } from "@playwright/test";
import { mkdir } from "node:fs/promises";

const BASE = process.env.BASE_URL ?? "http://localhost:4173";

const stats = { replays: 482, players: 37, by_mode: { "Storm League": 311, ARAM: 142, "Quick Match": 29 } };

const reference = {
  storm_league_maps: ["龙之镇", "诅咒谷", "蛛后墓", "天空殿", "黑心湾"],
  aram_maps: ["布莱克西斯"],
  heroes: ["缝合怪", "李敏", "缇兰德", "源氏", "乌瑟尔"],
  hero_roles: {},
};

const heroes = [
  ["缝合怪", 48, 31, 4.1, 2.2, 9.8, 78000, 1200],
  ["李敏", 41, 22, 3.2, 4.1, 6.0, 96000, 0],
  ["缇兰德", 37, 24, 1.8, 3.0, 14.2, 22000, 41000],
  ["源氏", 33, 15, 5.5, 5.8, 7.1, 88000, 0],
  ["乌瑟尔", 29, 19, 1.1, 2.4, 16.0, 18000, 73000],
  ["阿尔萨斯", 26, 12, 3.0, 3.9, 8.0, 71000, 0],
].map(([hero, games, wins, k, d, a, dmg, heal]) => ({
  hero, games, wins,
  winrate: wins / games,
  wilson_lb: wins / games - 0.12,
  avg_k: k, avg_d: d, avg_a: a,
  avg_hero_dmg: dmg, avg_siege_dmg: 0, avg_healing: heal,
}));

function teamSide(handleBase) {
  return [
    { hero: "缝合怪", display_name: handleBase + "·坦克" },
    { hero: "李敏", display_name: handleBase + "·法师" },
    { hero: "缇兰德", display_name: handleBase + "·辅助" },
    { hero: "源氏", display_name: handleBase + "·刺客" },
    { hero: "阿尔萨斯", display_name: handleBase + "·战士" },
  ];
}

const matches = {
  total: 311, limit: 25, offset: 0,
  matches: Array.from({ length: 8 }, (_, i) => ({
    replay_id: 1000 + i,
    match_key: "k" + i,
    map_name: reference.storm_league_maps[i % 5],
    mode: "Storm League",
    played_at: new Date(2026, 5, 16 - i, 21, 30).toISOString(),
    duration_s: 1200 + i * 60,
    winner_team: i % 2,
    bans_team0: ["李敏", "缇兰德"],
    bans_team1: ["源氏", "乌瑟尔"],
    team0: teamSide("我方"),
    team1: teamSide("敌方"),
  })),
};

const ROUTES = {
  "/api/stats": stats,
  "/api/reference": reference,
  "/api/heroes": heroes,
  "/api/matches": matches,
  "/api/weekly": {
    overview: {
      current: { days: 7, start_iso: "", end_iso: "", games: 23, wins: 14, winrate: 0.609 },
      previous: { days: 7, start_iso: "", end_iso: "", games: 19, wins: 9, winrate: 0.47 },
      games_delta: 4, winrate_delta_pp: 14,
    },
    players: [],
    awards: [],
    highlights: [],
    hero_top_picked: [], hero_top_winrate: [], hero_combos: [],
    maps: [],
    longest_win_streak: { length: 5, started_at: "", ended_at: "" },
    longest_loss_streak: { length: 2, started_at: "", ended_at: "" },
    brief: "",
  },
};

const VIEWPORTS = [
  { name: "phone", width: 390, height: 844, mobile: true },
  { name: "tablet", width: 768, height: 1024, mobile: true },
  { name: "desktop", width: 1440, height: 900, mobile: false },
];

const PAGES = [
  { name: "dashboard", path: "/" },
  { name: "heroes", path: "/heroes" },
  { name: "matches", path: "/matches" },
];

const browser = await chromium.launch();
await mkdir("shots", { recursive: true });

for (const vp of VIEWPORTS) {
  const context = await browser.newContext({
    ...(vp.mobile ? devices["iPhone 13"] : {}),
    viewport: { width: vp.width, height: vp.height },
    deviceScaleFactor: 2,
    isMobile: vp.mobile,
    hasTouch: vp.mobile,
  });
  const page = await context.newPage();

  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    const body = ROUTES[url.pathname] ?? {};
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(body) });
  });

  for (const p of PAGES) {
    await page.goto(BASE + p.path, { waitUntil: "networkidle" });
    await page.waitForTimeout(300);
    await page.screenshot({ path: `shots/${p.name}-${vp.name}.png`, fullPage: true });
    console.log(`✓ ${p.name}-${vp.name}.png`);
  }

  // Capture the open drawer state on phone only.
  if (vp.name === "phone") {
    await page.goto(BASE + "/", { waitUntil: "networkidle" });
    await page.getByRole("button", { name: "打开导航菜单" }).click();
    await page.waitForTimeout(400);
    await page.screenshot({ path: `shots/drawer-phone.png` });
    console.log("✓ drawer-phone.png");
  }

  await context.close();
}

await browser.close();
console.log("done");
