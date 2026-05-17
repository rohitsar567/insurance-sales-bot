/**
 * Tier-4 E2E — deterministic UI-presence journeys for the Insurance Advisor
 * frontend. Run via the playwright-skill runner:
 *   node /Users/rohitsar/.claude/skills/playwright-skill/run.js \
 *     audit/e2e/insurancebot_e2e.js
 *
 * The runner injects Playwright (resolved from the skill's node_modules) and
 * passes this file through verbatim because it already has `require(` + an
 * async IIFE. Every journey resolves to a boolean; everything is wrapped so a
 * single `RJSON {...}` line is ALWAYS printed to stdout — even on a hard
 * failure — so the Python check can parse it (no RJSON => WARN/infra, never a
 * silent FAIL). These are presence/no-error checks only: NO multi-turn LLM
 * chat (that is flaky/slow and not a deterministic UI signal).
 */
const { chromium } = require('playwright');

const TARGET = process.env.TARGET_URL || 'http://localhost:3000';

(async () => {
  const R = {
    loads: false,
    marketplaceRenders: false,
    logosOk: false,
    composerWorks: false,
    noConsoleError: false,
  };

  let browser;
  // Console / page errors collected across the whole session.
  const errors = [];

  try {
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({
      viewport: { width: 1440, height: 900 },
    });
    const page = await context.newPage();

    page.on('pageerror', (e) => errors.push('pageerror: ' + (e && e.message)));
    page.on('console', (msg) => {
      if (msg.type() === 'error') errors.push('console.error: ' + msg.text());
    });

    // ---- journey: loads -------------------------------------------------
    try {
      await page.goto(TARGET, { waitUntil: 'domcontentloaded', timeout: 60000 });
      await page.waitForSelector('h1', { state: 'visible', timeout: 30000 });
      R.loads = (await page.locator('h1').count()) > 0;
    } catch (e) {
      errors.push('loads: ' + (e && e.message));
    }

    // ---- journey: marketplaceRenders -----------------------------------
    // Open the Policy-Library / marketplace panel, assert >=1 policy card is
    // visible, and assert no horizontal overflow at 1440px.
    try {
      const trigger = page
        .locator(
          'button:has-text("Policy Library"), button:has-text("पॉलिसी लाइब्रेरी")'
        )
        .first();
      await trigger.click({ timeout: 20000 });
      await page.waitForSelector('.policy-card', {
        state: 'visible',
        timeout: 30000,
      });
      const cards = await page.locator('.policy-card:visible').count();
      const overflow = await page.evaluate(
        () =>
          document.documentElement.scrollWidth >
          window.innerWidth + 1
      );
      R.marketplaceRenders = cards >= 1 && !overflow;
    } catch (e) {
      errors.push('marketplaceRenders: ' + (e && e.message));
    }

    // ---- journey: logosOk ----------------------------------------------
    // At least one insurer logo <img src*="insurer-logos"> with naturalWidth>0.
    try {
      await page
        .waitForSelector('img[src*="insurer-logos"]', { timeout: 20000 })
        .catch(() => {});
      const logoLoaded = await page.evaluate(() => {
        const imgs = Array.from(
          document.querySelectorAll('img[src*="insurer-logos"]')
        );
        if (imgs.length === 0) return false;
        return imgs.some((im) => im.naturalWidth && im.naturalWidth > 0);
      });
      R.logosOk = logoLoaded;
    } catch (e) {
      errors.push('logosOk: ' + (e && e.message));
    }

    // ---- journey: composerWorks ----------------------------------------
    // The chat composer textarea is fillable and the Send button is clickable
    // once non-empty. (We do NOT actually submit a turn — no LLM dependency.)
    try {
      // The marketplace panel is a single-active <aside> that REPLACES the
      // chat view, so the composer textarea stays in the DOM but is hidden
      // while the panel is open. Toggle the Policy-Library trigger again to
      // close the panel (its onClick is togglePanel(...)), then wait for the
      // composer to become visible again.
      const ml = page
        .locator(
          'button:has-text("Policy Library"), button:has-text("पॉलिसी लाइब्रेरी")'
        )
        .first();
      if (await ml.isVisible().catch(() => false)) {
        await ml.click({ timeout: 10000 }).catch(() => {});
      }
      const ta = page.locator('textarea[aria-label="Message"]').first();
      await ta.waitFor({ state: 'visible', timeout: 20000 });
      await ta.fill('audit e2e presence check');
      const val = await ta.inputValue();
      const send = page
        .locator('button:has-text("Send")')
        .first();
      // After typing, Send must no longer be disabled (it gates on input).
      const sendEnabled = await send.isEnabled().catch(() => false);
      R.composerWorks = val === 'audit e2e presence check' && sendEnabled;
    } catch (e) {
      errors.push('composerWorks: ' + (e && e.message));
    }

    // ---- journey: noConsoleError ---------------------------------------
    // No pageerror / console.error captured during the whole session.
    R.noConsoleError = errors.every(
      (m) =>
        !m.startsWith('pageerror:') && !m.startsWith('console.error:')
    );
  } catch (fatal) {
    // Hard failure (browser launch, etc.) — journeys stay false; still emit
    // RJSON so the Python side classifies (FAIL/WARN), never a silent hang.
    errors.push('fatal: ' + (fatal && fatal.message));
  } finally {
    try {
      if (browser) await browser.close();
    } catch (_) {
      /* ignore */
    }
  }

  if (errors.length) {
    console.log('E2E_DIAG ' + JSON.stringify(errors.slice(0, 20)));
  }
  console.log('RJSON ' + JSON.stringify(R));
  // Exit 0 regardless: the boolean payload (not the exit code) is the signal.
  process.exit(0);
})();
