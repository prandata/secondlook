export async function run({ page }) {
  const base = "http://127.0.0.1:__PORT__";
  const offHost = [];
  await page.route("**/*", async route => {
    const url = new URL(route.request().url());
    if (url.hostname !== "127.0.0.1") { offHost.push(url.href); return route.abort(); }
    return route.continue();
  });
  const out = {};
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: width === 390 ? 844 : 900 });
    await page.goto(base + "/");
    await page.fill("#caption", "BREAKING: Explosion near the Pentagon, it is under attack!! Photo shows the U.S. Pentagon is under attack. Stay safe everyone");
    await page.setInputFiles("#file", "__REPO__/samples/recorded/pentagon-mock-post.png");
    await page.locator("#inv-recorded-go").waitFor({ state: "visible", timeout: 90000 });
    await page.click("#inv-recorded-go");
    await page.locator("#factcheck").waitFor({ state: "visible" });
    await page.waitForTimeout(1500);  // the smooth scroll to the strip
    // What the judge sees after the click: the view lands on the strip (design review of PR #48, S1).
    await page.screenshot({ path: "__DIR__/pentagon-recorded-view-" + width + ".png" });
    const view = await page.evaluate(() => ({ focused: document.activeElement && document.activeElement.id,
      stripTop: Math.round(document.getElementById("factcheck").getBoundingClientRect().top),
      pick: document.getElementById("fc-pick").textContent,
      linkHeight: Math.round(document.querySelector("#fc-line a").getBoundingClientRect().height) }));
    await page.locator("#result").screenshot({ path: "__DIR__/pentagon-recorded-result-" + width + ".png" });
    await page.locator("#investigate").screenshot({ path: "__DIR__/pentagon-recorded-note-" + width + ".png" });
    out[width] = {
      overflowX: await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth),
      answer: await page.locator("#answer").textContent(),
      fcRecorded: await page.locator("#fc-recorded").textContent(),
      caption: await page.locator("#inv-recorded-caption").textContent(),
      ...view,
    };
  }
  await page.unrouteAll({ behavior: "ignoreErrors" });
  return { out, offHost };
}
