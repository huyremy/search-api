from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from playwright.async_api import async_playwright
import asyncio
import os
import time
from typing import List, Dict, Any

app = FastAPI(title="Google CSE Search API (Brave-like Format)")

# CORS cho frontend gọi
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cache
cache: Dict[str, Dict] = {}
CACHE_TTL = 300  # 5 phút
CX = os.getenv("GOOGLE_CX", "83dfe6525b5214c76")

# --- GIỮ NGUYÊN PHẦN PLAYWRIGHT CỦA BẠN ---
async def fetch_google_cse(query: str, cx: str = CX, offset: int = 0) -> List[Dict]:
    cache_key = f"{query}_{cx}_{offset}"
    
    if cache_key in cache:
        cached_data = cache[cache_key]
        if time.time() - cached_data["timestamp"] < CACHE_TTL:
            print(f"✅ DEBUG: Cache hit for '{query}'. Returning cached results.")
            return cached_data["results"]
        else:
            print(f"⏳ DEBUG: Cache expired for '{query}'. Re-fetching.")
    
    print(f"🚀 DEBUG: Starting Playwright fetch for '{query}'...")
    
    try:
        async with async_playwright() as p:
            print("   - Launching Chromium browser...")
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-gpu',
                    '--single-process'
                ]
            )
            
            # 🟢 User-Agent đặt ở đây mới đúng
            page = await browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            
            print(f"   - Navigating to Google CSE (cx: {cx})...")
            await page.goto(f"https://cse.google.com/cse?cx={cx}", wait_until="networkidle")
            
            try:
                print("   - Waiting for search input selector...")
                await page.wait_for_selector(".gsc-input input", timeout=10000)
                
                print(f"   - Typing query: '{query}'")
                await page.fill(".gsc-input input", query)
                
                search_clicked = False
                
                # 🟢 CÁCH 1: Thử click nút Search (Có thể class đã đổi)
                try:
                    print("   - Trying to click Search button (gsc-search-button-v2)...")
                    await page.click(".gsc-search-button-v2 button", timeout=5000)
                    search_clicked = True
                    print("   - Search button clicked successfully (v2).")
                except Exception as click_err:
                    print(f"   ⚠️ Failed v2: {click_err}")
                    pass
                
                # 🟢 CÁCH 2: Nếu cách 1 thất bại, thử nút Search với class cũ hơn
                if not search_clicked:
                    try:
                        print("   - Trying to click Search button (gsc-search-button)...")
                        await page.click(".gsc-search-button button", timeout=5000)
                        search_clicked = True
                        print("   - Search button clicked successfully (old).")
                    except Exception as click_err2:
                        print(f"   ⚠️ Failed old: {click_err2}")
                        pass
                
                # 🟢 CÁCH 3: Nếu tất cả thất bại, dùng phím Enter
                if not search_clicked:
                    print("   - Trying to press Enter key instead...")
                    await page.press(".gsc-input input", "Enter")
                    
            except Exception as e:
                print(f"❌ DEBUG LỖI NGHIÊM TRỌNG 1: Search input error! {e}")
                await browser.close()
                return []

            print("   - Waiting 3s for results to load...")
            await asyncio.sleep(3)
            
            # 🟢 XỬ LÝ PAGINATION (NẾU OFFSET > 0)
            if offset > 0:
                try:
                    # Render Google CSE thường có nút "Next" ở cuối trang
                    next_button = await page.query_selector('.gsc-clear-button + div a:last-child, .gsc-pagination-button')
                    if next_button:
                        print(f"   - Clicking Next page (offset={offset})...")
                        await next_button.click()
                        await asyncio.sleep(3)
                    else:
                        print("   ⚠️ Không tìm thấy nút Next, chỉ lấy trang hiện tại.")
                except Exception as page_err:
                    print(f"   ⚠️ Pagination error: {page_err}")

            try:
                print("   - Waiting for result selectors (gsc-result, gs-result)...")
                await page.wait_for_selector(".gsc-result, .gs-result, .gsc-webResult", timeout=15000)
                print("   ✅ Found result selector! DOM is ready.")
            except Exception as e:
                print(f"⚠️ DEBUG LỖI 2: Timeout waiting for results. DOM may be empty or Google blocked us. Error: {e}")

            print("   - Evaluating DOM to extract results...")
            results = await page.evaluate('''
                () => {
                    const items = [];
                    const elements = document.querySelectorAll('.gsc-webResult, .gs-webResult');
                    console.log(`Found ${elements.length} elements in DOM`);
                    
                    elements.forEach(el => {
                        let link = '';
                        const linkEl = el.querySelector('.gs-title a');
                        if (linkEl) {
                            link = linkEl.href;
                            if (link.startsWith('/url?q=')) {
                                try {
                                    link = decodeURIComponent(link.split('/url?q=')[1].split('&')[0]);
                                } catch {
                                    link = link.split('/url?q=')[1].split('&')[0];
                                }
                            }
                        }

                        let title = '';
                        const titleEl = el.querySelector('.gs-title');
                        if (titleEl) {
                            title = titleEl.innerText.trim();
                        } else if (linkEl) {
                            title = linkEl.innerText.trim();
                        }

                        let snippet = '';
                        const snippetEl = el.querySelector('.gs-snippet');
                        if (snippetEl) snippet = snippetEl.innerText.trim();

                        let display_url = '';
                        const urlEl = el.querySelector('.gs-visibleUrl');
                        if (urlEl) display_url = urlEl.innerText.trim();

                        if (title || link) {
                            items.push({
                                title: title,
                                link: link,
                                snippet: snippet,
                                display_url: display_url
                            });
                        }
                    });
                    return items;
                }
            ''')
            
            await browser.close()
            
            print(f"   - Raw results count before filtering: {len(results)}")
            seen_links = set()
            unique_results = []
            for item in results:
                link = item.get('link', '')
                if link and link not in seen_links:
                    unique_results.append(item)
                    seen_links.add(link)
            
            print(f"   ✅ Unique results count after filtering: {len(unique_results)}")
            
            cache[cache_key] = {
                "timestamp": time.time(),
                "results": unique_results
            }
            return unique_results
            
    except Exception as e:
        print(f"❌ CRITICAL ERROR LỚN NHẤT fetching results: {e}")
        return []

# --- HÀM MỚI: CHUYỂN ĐỔI SANG FORMAT GIỐNG BRAVE ---
def transform_to_brave_format(query: str, web_results: List[Dict], offset: int = 0) -> Dict[str, Any]:
    # Tạo cấu trúc metadata query y hệt Brave
    brave_query = {
        "original": query,
        "show_strict_warning": False,
        "is_navigational": False,
        "is_news_breaking": False,
        "spellcheck_off": False,
        "country": "us",
        "bad_results": False,
        "should_fallback": False,
        "postal_code": "",
        "city": "",
        "header_country": "",
        "more_results_available": len(web_results) >= 20,
        "state": ""
    }

    # Tạo mục mixed (đánh index)
    mixed_main = []
    for idx in range(len(web_results)):
        mixed_main.append({"type": "web", "index": idx, "all": False})

    brave_mixed = {
        "type": "mixed",
        "main": mixed_main,
        "top": [],
        "side": []
    }

    # Format lại từng kết quả web cho khớp Brave
    brave_web_results = []
    for r in web_results:
        brave_web_results.append({
            "title": r.get("title", ""),
            "url": r.get("link", ""),
            "is_source_local": False,
            "is_source_both": False,
            "description": r.get("snippet", ""),
            "page_age": None,
            "profile": {
                "name": r.get("display_url", "").replace("www.", "").split(".")[0] if r.get("display_url") else "",
                "url": r.get("link", ""),
                "long_name": r.get("display_url", ""),
                "img": None
            },
            "language": "en",
            "family_friendly": True,
            "type": "search_result",
            "subtype": "generic",
            "is_live": False,
            "meta_url": {
                "scheme": "https",
                "netloc": r.get("display_url", ""),
                "hostname": r.get("display_url", ""),
                "favicon": None,
                "path": ""
            },
            "thumbnail": None,
            "age": None,
            "deep_results": None
        })

    brave_web = {
        "type": "search",
        "results": brave_web_results,
        "family_friendly": True
    }

    return {
        "type": "search",
        "query": brave_query,
        "mixed": brave_mixed,
        "videos": {
            "type": "videos",
            "results": [],
            "mutated_by_goggles": False
        },
        "web": brave_web
    }


@app.get("/")
async def root():
    return {
        "message": "Google CSE Search API (Brave-like JSON Format)",
        "endpoints": {
            "/search?q=query": "Tìm kiếm",
        }
    }

@app.get("/search")
async def search(
    q: str = Query(..., description="Từ khóa tìm kiếm"),
    cx: str = Query(CX, description="Search Engine ID"),
    offset: int = Query(0, description="Trang kết quả (0 là trang đầu, 1 là trang tiếp theo)")
):
    try:
        print(f"🔍 RECEIVED SEARCH REQUEST: '{q}' (cx: {cx}, offset: {offset})")
        raw_results = await fetch_google_cse(q, cx, offset)
        print(f"📊 FINAL RESULTS COUNT TO SEND: {len(raw_results)}")
        
        brave_style_json = transform_to_brave_format(q, raw_results, offset)
        return JSONResponse(brave_style_json)
        
    except Exception as e:
        print(f"❌ SEARCH HANDLER EXCEPTION: {e}")
        return JSONResponse({
            "error": str(e),
            "query": q,
            "results": [],
            "status": "error"
        }, status_code=500)

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "cache_size": len(cache),
        "cx": CX
    }

@app.get("/clear-cache")
async def clear_cache():
    cache.clear()
    return {"status": "cache cleared"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
