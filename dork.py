#!/usr/bin/env python3
# dork_cse_selenium_txt.py
# يقرأ dorks من ملف (محلي أو رابط)، يضيف site:domain إن طلب المستخدم،
# يفتح صفحة CSE باستخدام Selenium لاستخراج النتائج (title, link, snippet),
# ويحفظ النتائج في ملف نصي. يفصل 15s بين كل request (افتراضي).
#
# متطلبات:
#   pip install selenium webdriver-manager requests beautifulsoup4 tqdm fake-useragent
#
# ملاحظة: يمكن أن تكتشف Google أتمتة المتصفح وتعرض قيود/حظر. استعمله بمسؤولية.

import time
import argparse
import os
import random
import requests
from urllib.parse import quote_plus
from tqdm import tqdm
from bs4 import BeautifulSoup
from fake_useragent import UserAgent

# Selenium / webdriver-manager
from selenium import webdriver
from selenium.webdriver.chrome.service import Service as ChromeService
from webdriver_manager.chrome import ChromeDriverManager
from selenium.webdriver.chrome.options import Options
from selenium.common.exceptions import WebDriverException, TimeoutException

DEFAULT_CX = "f32fc71d9c0f54c66"
DEFAULT_OUT = "results.txt"
DEFAULT_DELAY = 15.0  # الافتراضي: 15 ثانية بين كل طلب
DEFAULT_TIMEOUT = 30  # ثواني لانتظار تحميل الصفحة

def read_wordlist(path):
    if path.startswith("http://") or path.startswith("https://"):
        r = requests.get(path, timeout=30)
        r.raise_for_status()
        lines = r.text.splitlines()
    else:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.read().splitlines()
    dorks = []
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith("#"):
            continue
        dorks.append(s)
    return dorks

def init_driver(headless=True):
    # user-agent عشوائي لخفض احتمالية الحظر
    ua = UserAgent()
    user_agent = ua.random
    chrome_options = Options()
    if headless:
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument(f"--user-agent={user_agent}")
    # optional: reduce detection footprint
    chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
    chrome_options.add_experimental_option('useAutomationExtension', False)

    service = ChromeService(ChromeDriverManager().install())
    try:
        driver = webdriver.Chrome(service=service, options=chrome_options)
    except WebDriverException as e:
        print("[ERR] فشل تشغيل Chrome WebDriver:", e)
        raise
    # محاولة تقليل المؤشرات على أنه بوت
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
        "source": """
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
        """
    })
    return driver

def parse_cse_html(html):
    """
    تحليل HTML لنتائج CSE. بنية الصفحة قد تتغير — هذا يحاول إيجاد النتائج الشائعة.
    يرجع قائمة عناصر: dict(title, link, snippet, display)
    """
    soup = BeautifulSoup(html, "html.parser")
    results = []

    # الطرق المحتملة لاستخراج النتائج من صفحة CSE:
    # 1) عناصر بحث مضمّنة: div.gsc-webResult
    # 2) أحياناً تستخدم class 'gsc-result' أو 'gsc-webResult gsc-result'
    candidates = soup.select("div.gsc-webResult, div.gsc-result")
    if not candidates:
        # fallback: ابحث عن عناصر شبيهة ببحث Google التقليدي
        candidates = soup.select("div.g, div.rc, div.yuRUbf")

    for c in candidates:
        title = ""
        link = ""
        snippet = ""
        display = ""

        # title & link: حاول العثور على anchor أول داخل العنصر
        a = c.find("a")
        if a and a.get("href"):
            link = a.get("href").strip()
            title = a.get_text(strip=True)

        # display link
        dsp = c.select_one(".gs-visibleUrl, .gs-bidi-start-align, .gsc-url-top")
        if dsp:
            display = dsp.get_text(strip=True)

        # snippet
        sn = c.select_one(".gsc-thumbnail-inside, .gs-bidi-start-align + .gs-snippet, .gs-snippet, .rc .s, .IsZvec")
        if sn:
            snippet = sn.get_text(" ", strip=True)
        else:
            # محاولة أخرى: أي <div> أو <span> صغير داخل العنصر
            p = c.find(["span","div"], string=True)
            if p:
                snippet = p.get_text(" ", strip=True)

        # تجاهل العناصر الفارغة
        if not (title or link or snippet):
            continue

        results.append({"title": title, "link": link, "snippet": snippet, "display": display})

    return results

def append_to_txt(out_file, query, items):
    with open(out_file, "a", encoding="utf-8") as f:
        f.write("="*80 + "\n")
        f.write(f"Query: {query}\n")
        f.write("-"*80 + "\n")
        if not items:
            f.write("[no results]\n\n")
            return
        for idx, it in enumerate(items, start=1):
            f.write(f"[{idx}] Title: {it.get('title','')}\n")
            f.write(f"    Link: {it.get('link','')}\n")
            f.write(f"    Display: {it.get('display','')}\n")
            f.write(f"    Snippet: {it.get('snippet','')}\n\n")
        f.write("\n")

def main(args):
    # تأكيد ملف الإخراج / إفراغه إن طلب overwrite
    if os.path.exists(args.output) and not args.overwrite:
        print(f"[i] ملف الإخراج '{args.output}' موجود. سيتم الإضافة إليه. استخدم --overwrite للاستبدال.")
    else:
        open(args.output, "w", encoding="utf-8").close()

    dorks = read_wordlist(args.wordlist)
    print(f"[i] قرأت {len(dorks)} dorks من: {args.wordlist}")

    driver = init_driver(headless=args.headless)
    base_cse = f"https://cse.google.com/cse?cx={args.cx}&q="

    total = 0
    try:
        for dork in tqdm(dorks, desc="dorks"):
            q = dork
            if args.site and "site:" not in dork:
                q = f"site:{args.site} {dork}"

            # شيفرة الاستعلام مشفّرة
            url_q = base_cse + quote_plus(q)
            try:
                driver.set_page_load_timeout(args.timeout)
                driver.get(url_q)
                # بعض صفحات CSE تحمل النتائج بعد تأخير؛ ننتظر قليلاً أو حتى عنصر النتائج يظهر
                time.sleep(min(3, args.timeout/4))  # انتظار أولي
                # اجعل هناك انتظار إضافي صغير حتى تُحمّل السكربتات
                # ثم احصل على مصدر الصفحة النهائي
                html = driver.page_source
            except TimeoutException:
                print(f"[WARN] Timeout عند تحميل {url_q}")
                html = driver.page_source if driver else ""

            items = parse_cse_html(html)
            append_to_txt(args.output, q, items)
            total += len(items)

            # تأخير مع jitter بسيط قبل الطلب التالي
            jitter = random.uniform(-0.2, 0.2) * args.delay
            wait = max(1.0, args.delay + jitter)
            print(f"[i] انتظر {wait:.1f}s قبل الاستعلام التالي...")
            time.sleep(wait)

    finally:
        try:
            driver.quit()
        except:
            pass

    print(f"[i] انتهى. مجموع النتائج المكتوبة في '{args.output}': {total}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Use Google CSE web UI (Selenium) to run dorks and save results to text file.")
    parser.add_argument("--wordlist", required=True, help="مسار ملف الـ dorks (محلي أو رابط)")
    parser.add_argument("--site", required=True, help="site domain to restrict searches (e.g. example.com)")
    parser.add_argument("--cx", default=DEFAULT_CX, help=f"CSE cx value (default: {DEFAULT_CX})")
    parser.add_argument("--output", default=DEFAULT_OUT, help="output text file")
    parser.add_argument("--delay", type=float, default=DEFAULT_DELAY, help="delay (seconds) between requests (default 15s)")
    parser.add_argument("--headless", action="store_true", help="run Chrome in headless mode")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, help="page load timeout in seconds")
    parser.add_argument("--overwrite", action="store_true", help="overwrite output file if exists")
    args = parser.parse_args()
    main(args)
