import io
import os
import re
import time
import asyncio
from datetime import datetime
from urllib.parse import urlparse

import nest_asyncio
import streamlit as st
from PIL import Image
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

nest_asyncio.apply()

# ---------------------------
# Settings
# ---------------------------
VIEWPORT_WIDTH = 1500
VIEWPORT_HEIGHT = 900
DEVICE_SCALE_FACTOR = 2

NAV_TIMEOUT_MS = 90000
WAIT_AFTER_NAV_MS = 2500
WAIT_AFTER_SCROLL_MS = 650
MAX_SCROLL_SECONDS = 70

PAGE_SIZE = A4
MARGIN_PT = 24

INCLUDE_BLOCKED_PAGES_IN_PDF = True

BOT_CHECK_PATTERNS = [
    r"are you human",
    r"verify you are human",
    r"checking your browser",
    r"attention required",
    r"captcha",
    r"cloudflare",
    r"incapsula",
    r"perimeterx",
    r"px-captcha",
    r"datadome",
    r"bot detection",
]

# Use /tmp on Render for ephemeral files
TMP_DIR = "/tmp/promo_screens"
os.makedirs(TMP_DIR, exist_ok=True)

# ---------------------------
# Helpers
# ---------------------------
def normalize_urls(text):
    urls = []
    for line in text.splitlines():
        u = line.strip()
        if not u:
            continue
        if not re.match(r"^https?://", u, re.IGNORECASE):
            u = "https://" + u
        urls.append(u)
    return urls

def safe_filename_from_url(url):
    p = urlparse(url)
    host = p.netloc.replace("www.", "")
    path = p.path.strip("/").replace("/", "_")
    if not path:
        path = "home"
    name = re.sub(r"[^a-zA-Z0-9_\-\.]+", "_", f"{host}_{path}")
    return name[:180]

def is_reeds(url):
    try:
        return "reeds.com" in urlparse(url).netloc.lower()
    except Exception:
        return False

async def wait_for_settle(page):
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=25000)
    except Exception:
        pass
    try:
        await page.wait_for_load_state("networkidle", timeout=25000)
    except Exception:
        pass
    await page.wait_for_timeout(WAIT_AFTER_NAV_MS)

async def inject_cleanup_css(page):
    try:
        await page.add_style_tag(
            content="* { animation: none !important; transition: none !important; scroll-behavior: auto !important; }"
        )
    except Exception:
        pass

async def press_escape(page, repeats=2):
    for _ in range(repeats):
        try:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(250)
        except Exception:
            pass

async def click_common_buttons_in_frame(frame):
    texts = [
        "Accept", "Accept all", "Accept All", "I Agree", "I agree", "Agree",
        "Got it", "OK", "Ok", "Continue", "Allow all",
        "Close", "No thanks", "No Thanks", "Maybe later", "Not now", "Not Now",
        "Reject", "Reject all", "Decline",
    ]
    selectors = [
        "button",
        "a[role='button']",
        "[role='button']",
        "input[type='button']",
        "input[type='submit']",
    ]

    for sel in selectors:
        try:
            loc = frame.locator(sel)
            count = await loc.count()
        except Exception:
            continue

        if count == 0:
            continue

        limit = min(count, 40)
        for i in range(limit):
            try:
                el = loc.nth(i)
                txt = ""
                try:
                    txt = (await el.inner_text(timeout=500)).strip()
                except Exception:
                    txt = ""
                if not txt:
                    try:
                        v = await el.get_attribute("value")
                        txt = (v or "").strip()
                    except Exception:
                        txt = ""
                if any(txt.lower() == t.lower() for t in texts):
                    await el.click(timeout=1200)
                    await frame.page.wait_for_timeout(250)
            except Exception:
                pass

async def remove_overlays_dom_aggressive(page):
    # Useful for many sites, but can be dangerous on Reeds.
    try:
        await page.evaluate("""
        () => {
          const killWords = [
            "cookie", "consent", "onetrust", "trustarc", "privacy",
            "modal", "popup", "pop-up", "newsletter", "subscribe",
            "overlay", "backdrop", "interstitial", "dialog",
            "lightbox", "email-signup", "sign-up"
          ];

          function looksLikeOverlay(el) {
            const st = window.getComputedStyle(el);
            if (!st) return false;

            const idc = ((el.id || "") + " " + (el.className || "")).toLowerCase();
            const hasKillWord = killWords.some(w => idc.includes(w));

            const pos = st.position;
            const z = parseInt(st.zIndex || "0", 10);

            const r = el.getBoundingClientRect();
            const vw = window.innerWidth;
            const vh = window.innerHeight;
            const area = Math.max(0, r.width) * Math.max(0, r.height);
            const viewportArea = vw * vh;

            const bigAndOnTop = (area > viewportArea * 0.15) && (z >= 50) && (pos === "fixed" || pos === "sticky");
            const fullScreenish = (r.width > vw * 0.90) && (r.height > vh * 0.20) && (pos === "fixed" || pos === "sticky");

            return hasKillWord || bigAndOnTop || fullScreenish;
          }

          const all = Array.from(document.querySelectorAll("body *"));
          for (const el of all) {
            try { if (looksLikeOverlay(el)) el.remove(); } catch (e) {}
          }

          document.documentElement.style.overflow = "auto";
          document.body.style.overflow = "auto";
        }
        """)
    except Exception:
        pass

async def dismiss_overlays(page, aggressive=True):
    for _ in range(4):
        await press_escape(page, repeats=1)

        try:
            frames = page.frames
        except Exception:
            frames = []

        for fr in frames:
            try:
                await click_common_buttons_in_frame(fr)
            except Exception:
                pass

        if aggressive:
            await remove_overlays_dom_aggressive(page)

        await page.wait_for_timeout(300)

async def scroll_main_page(page, max_seconds=40):
    start = time.time()
    last_height = 0
    same_count = 0

    while True:
        if time.time() - start > max_seconds:
            break

        try:
            height = await page.evaluate("() => document.body.scrollHeight")
        except Exception:
            break

        if height == last_height:
            same_count += 1
        else:
            same_count = 0

        if same_count >= 6:
            break

        last_height = height

        try:
            await page.evaluate("""
                () => {
                  const step = Math.max(700, Math.floor(window.innerHeight * 0.90));
                  window.scrollBy(0, step);
                }
            """)
        except Exception:
            break

        await page.wait_for_timeout(WAIT_AFTER_SCROLL_MS)

async def scroll_common_containers(page, max_seconds=30):
    start = time.time()
    while True:
        if time.time() - start > max_seconds:
            break
        try:
            changed = await page.evaluate("""
            () => {
              const els = Array.from(document.querySelectorAll("*"));
              let didScroll = false;

              for (const el of els) {
                const st = window.getComputedStyle(el);
                if (!st) continue;
                const oy = st.overflowY;
                if (oy !== "auto" && oy !== "scroll") continue;

                const r = el.getBoundingClientRect();
                if (r.height < 300 || r.width < 300) continue;

                const maxScrollTop = el.scrollHeight - el.clientHeight;
                if (maxScrollTop > 50 && el.scrollTop < maxScrollTop) {
                  el.scrollTop = Math.min(maxScrollTop, el.scrollTop + Math.max(600, el.clientHeight * 0.9));
                  didScroll = true;
                }
              }
              return didScroll;
            }
            """)
        except Exception:
            break

        if not changed:
            break

        await page.wait_for_timeout(WAIT_AFTER_SCROLL_MS)

async def expand_scrollables_and_iframes(page):
    try:
        await page.evaluate("""
        async () => {
          const els = Array.from(document.querySelectorAll("*"));
          for (const el of els) {
            const st = window.getComputedStyle(el);
            if (!st) continue;

            const oy = st.overflowY;
            if (oy !== "auto" && oy !== "scroll") continue;

            const r = el.getBoundingClientRect();
            if (r.height < 200 || r.width < 200) continue;

            const maxScrollTop = el.scrollHeight - el.clientHeight;
            if (maxScrollTop > 50) {
              el.style.overflow = "visible";
              el.style.maxHeight = "none";
              el.style.height = el.scrollHeight + "px";
            }
          }

          const iframes = Array.from(document.querySelectorAll("iframe"));
          for (const fr of iframes) {
            try {
              const doc = fr.contentDocument;
              if (!doc || !doc.body) continue;
              const h = Math.max(doc.body.scrollHeight, doc.documentElement.scrollHeight);
              if (h && h > 200) fr.style.height = h + "px";
            } catch (e) {}
          }

          document.documentElement.style.overflow = "auto";
          document.body.style.overflow = "auto";
        }
        """)
    except Exception:
        pass

async def auto_scroll_full(page, max_seconds=70):
    await scroll_main_page(page, max_seconds=int(max_seconds * 0.6))
    await scroll_common_containers(page, max_seconds=int(max_seconds * 0.4))
    try:
        await page.evaluate("() => window.scrollTo(0, 0)")
    except Exception:
        pass
    await page.wait_for_timeout(400)

async def is_probable_bot_check(page):
    try:
        title = (await page.title()) or ""
    except Exception:
        title = ""
    try:
        body_text = await page.evaluate("() => document.body ? document.body.innerText : ''")
        if body_text is None:
            body_text = ""
    except Exception:
        body_text = ""

    hay = (title + "\n" + body_text).lower()
    for pat in BOT_CHECK_PATTERNS:
        if re.search(pat, hay, re.IGNORECASE):
            return True
    return False

async def wait_for_reeds_content(page):
    candidates = [
        "main",
        "h1",
        "[data-testid*='promo' i]",
        "[class*='promo' i]",
        "[class*='promotion' i]",
        "a[href*='promo' i]",
        "img",
    ]
    for sel in candidates:
        try:
            await page.wait_for_selector(sel, timeout=12000, state="attached")
            await page.wait_for_timeout(800)
            return True
        except Exception:
            continue
    return False

def save_a4_pdf_from_images(image_paths, pdf_path):
    page_w, page_h = PAGE_SIZE
    c = canvas.Canvas(pdf_path, pagesize=PAGE_SIZE)

    for img_path in image_paths:
        with Image.open(img_path) as im:
            img_w_px, img_h_px = im.size

        avail_w = page_w - 2 * MARGIN_PT
        avail_h = page_h - 2 * MARGIN_PT

        img_aspect = img_w_px / img_h_px
        box_aspect = avail_w / avail_h

        if img_aspect > box_aspect:
            draw_w = avail_w
            draw_h = avail_w / img_aspect
        else:
            draw_h = avail_h
            draw_w = avail_h * img_aspect

        x = (page_w - draw_w) / 2
        y = (page_h - draw_h) / 2

        c.drawImage(img_path, x, y, width=draw_w, height=draw_h, preserveAspectRatio=True, mask="auto")
        c.showPage()

    c.save()

def create_blocked_placeholder_png(url, out_path):
    img = Image.new("RGB", (1600, 900), color=(255, 255, 255))
    from PIL import ImageDraw, ImageFont

    draw = ImageDraw.Draw(img)
    title = "BLOCKED (bot check / human verification)"
    body = f"Could not capture screenshot for:\n{url}\n\nThis site appears to require a human verification step."
    draw.text((60, 80), title, fill=(0, 0, 0))
    draw.text((60, 140), body, fill=(0, 0, 0))
    img.save(out_path, format="PNG")

async def capture_screenshots(urls, status_cb=None):
    # Clean tmp dir
    for fn in os.listdir(TMP_DIR):
        try:
            os.remove(os.path.join(TMP_DIR, fn))
        except Exception:
            pass

    image_paths = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--disable-dev-shm-usage", "--no-sandbox"],
        )

        context = await browser.new_context(
            viewport={"width": VIEWPORT_WIDTH, "height": VIEWPORT_HEIGHT},
            device_scale_factor=DEVICE_SCALE_FACTOR,
            java_script_enabled=True,
            ignore_https_errors=True,
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        context.set_default_timeout(NAV_TIMEOUT_MS)
        page = await context.new_page()

        for idx, url in enumerate(urls, start=1):
            base = safe_filename_from_url(url)
            if status_cb:
                status_cb(f"[{idx}/{len(urls)}] Loading {url}")

            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=NAV_TIMEOUT_MS)
            except PlaywrightTimeoutError:
                pass
            except Exception:
                # If navigation fails, add blocked placeholder
                ph = os.path.join(TMP_DIR, f"{idx:02d}_blocked_{base}.png")
                create_blocked_placeholder_png(url, ph)
                image_paths.append(ph)
                continue

            await wait_for_settle(page)
            await inject_cleanup_css(page)

            aggressive = not is_reeds(url)
            await dismiss_overlays(page, aggressive=aggressive)

            if is_reeds(url):
                await wait_for_reeds_content(page)

            await auto_scroll_full(page, max_seconds=MAX_SCROLL_SECONDS)
            await dismiss_overlays(page, aggressive=aggressive)
            await expand_scrollables_and_iframes(page)
            await page.wait_for_timeout(1200)

            blocked = await is_probable_bot_check(page)
            if blocked:
                ph = os.path.join(TMP_DIR, f"{idx:02d}_blocked_{base}.png")
                create_blocked_placeholder_png(url, ph)
                image_paths.append(ph)
                continue

            out_path = os.path.join(TMP_DIR, f"{idx:02d}_ok_{base}.png")
            try:
                await page.screenshot(path=out_path, full_page=True)
                image_paths.append(out_path)
            except Exception:
                ph = os.path.join(TMP_DIR, f"{idx:02d}_blocked_{base}.png")
                create_blocked_placeholder_png(url, ph)
                image_paths.append(ph)

        await context.close()
        await browser.close()

    return image_paths

def build_pdf_bytes_from_images(image_paths):
    pdf_path = os.path.join(TMP_DIR, f"promo_screenshots_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.pdf")
    save_a4_pdf_from_images(image_paths, pdf_path)
    with open(pdf_path, "rb") as f:
        return f.read()

# ---------------------------
# Streamlit UI
# ---------------------------
st.set_page_config(page_title="Promo Screenshot App", layout="wide")
st.title("Promo Screenshot App")
st.caption("Paste URLs, click Generate. PDF will include placeholder pages when blocked by bot checks.")

default_urls = "\n".join(
    [
        "https://www.reeds.com/promotions.html",
        "https://www.helzberg.com/current-promotions.html",
        "https://www.brilliantearth.com/promo-codes-and-offers/",
    ]
)

urls_text = st.text_area("Paste URLs (one per line)", value=default_urls, height=160)
urls = normalize_urls(urls_text)

col1, col2 = st.columns([1, 2])
with col1:
    go = st.button("Generate PDF (Screenshots)")

with col2:
    st.write(f"URLs detected: {len(urls)}")

status = st.empty()

if go:
    if not urls:
        st.error("No URLs found. Paste at least one URL.")
    else:
        status.info("Starting capture...")
        try:
            image_paths = asyncio.run(capture_screenshots(urls, status_cb=status.info))
            status.info("Building PDF...")
            pdf_bytes = build_pdf_bytes_from_images(image_paths)
            status.success("Done.")
            st.download_button(
                label="Download PDF",
                data=pdf_bytes,
                file_name=f"promo_screenshots_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.pdf",
                mime="application/pdf",
            )
        except RuntimeError as e:
            # If asyncio.run complains about an active loop, fallback to create_task pattern
            status.error(f"Runtime error: {e}")
        except Exception as e:
            status.error(f"Error: {e}")
