from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from playwright.async_api import async_playwright
import asyncio
import os
import time
import random
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
CACHE_TTL = 300
CX = os.getenv("GOOGLE_CX", "83dfe6525b5214c76")

async def fetch_google_cse(query: str, cx: str = CX, offset: int = 0) -> List[Dict]:
    cache_key = f"{query}_{cx}_{offset}"
    
    if cache_key in cache:
        cached_data = cache[cache_key]
        if time.time() - cached_data["timestamp"] < CACHE_TTL:
            return cached_data["results"]
    
    try:
        async with async_playwright() as p:
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
            
            page = await browser.new_page(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            await page.set_extra_http_headers({
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Accept-Language": "en-US,en;q=0.9",
                "Sec-Ch-Ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
                "Sec-Ch-Ua-Mobile": "?0",
                "Sec-Ch-Ua-Platform": '"Windows"',
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "none",
                "Sec-Fetch-User": "?1",
                "Upgrade-Insecure-Requests": "1"
            })
            
            print(f"   - Navigating to Google CSE (cx: {cx})...")
            await page.goto(f"https://cse.google.com/cse?cx={cx}", wait_until="networkidle", timeout=30000)
            
            # 🟢 TỰ ĐỘNG TÌM INPUT BẰNG NHIỀU CÁCH (Fallback)
            search_input = None
            try:
                # Cách 1: Tìm theo ID động (Thường gặp nhất trên CSE mới)
                search_input = await page.wait_for_selector("input.gsc-i-id1, input#gsc-i-id1", timeout=5000)
            except:
                try:
                    # Cách 2: Tìm theo Class truyền thống
                    search_input = await page.wait_for_selector(".gsc-input input", timeout=5000)
                except:
                    try:
                        # Cách 3: Tìm theo Name (Luôn có)
                        search_input = await page.wait_for_selector("input[name='search']", timeout=5000)
                    except:
                        # Cách 4: Tìm bất kỳ input nào trong form tìm kiếm
                        search_input = await page.wait_for_selector("form.gsc-search-box input[type='text']", timeout=5000)
            
            if not search_input:
                print("❌ Could not find search input after trying multiple selectors!")
                await browser.close()
                return []

            # Nhập từ khóa
            await search_input.type(query, delay=random.randint(50, 150))
            await asyncio.sleep(2)
            
            # Gửi Enter
            await page.press("input.gsc-i-id1, .gsc-input input, input[name='search']", "Enter")
            
            # Trigger JS submit
            await asyncio.sleep(1)
            await page.evaluate('''
                const input = document.querySelector('input.gsc-i-id1, .gsc-input input, input[name="search"]');
                if (input) {
                    const form = input.closest('form');
                    if (form) form.submit();
                }
            ''')
                
            await asyncio.sleep(6)
            
            if offset > 0:
                try:
                    next_button = await page.query_selector('.gsc-pagination-button a, .gsc-clear-button + div a')
                    if next_button:
                        print(f"   - Clicking Next page (offset={offset})...")
                        await next_button.click()
                        await asyncio.sleep(4)
                except:
                    pass

            results = await page.evaluate('''
                () => {
                    const items = [];
                    const elements = document.querySelectorAll('.gsc-webResult, .gs-webResult');
                    
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
            
            if len(results) == 0:
                print("⚠️ DEBUG: 0 RESULTS FOUND. DUMPING HTML PAGE CONTENT BELOW:")
                html_content = await page.content()
                print(html_content[:3000]) 
                print("⚠️ DEBUG: END OF HTML DUMP")
            
            await browser.close()
            
            seen_links = set()
            unique_results = []
            for item in results:
                link = item.get('link', '')
                if link and link not in seen_links:
                    unique_results.append(item)
                    seen_links.add(link)
            
            cache[cache_key] = {
                "timestamp": time.time(),
                "results": unique_results
            }
            return unique_results
            
    except Exception as e:
        print(f"❌ CRITICAL ERROR fetching results: {e}")
        return []

def transform_to_brave_format(query: str, web_results: List[Dict], offset: int = 0) -> Dict[str, Any]:
    brave_query = {
        "original": query, "show_strict_warning": False, "is_navigational": False,
        "is_news_breaking": False, "spellcheck_off": False, "country": "us",
        "bad_results": False, "should_fallback": False, "postal_code": "", "city": "",
        "header_country": "", "more_results_available": len(web_results) >= 20, "state": ""
    }
    mixed_main = [{"type": "web", "index": idx, "all": False} for idx in range(len(web_results))]
    brave_web_results = []
    for r in web_results:
        brave_web_results.append({
            "title": r.get("title", ""), "url": r.get("link", ""),
            "is_source_local": False, "is_source_both": False,
            "description": r.get("snippet", ""), "page_age": None,
            "profile": {
                "name": r.get("display_url", "").replace("www.", "").split(".")[0] if r.get("display_url") else "",
                "url": r.get("link", ""), "long_name": r.get("display_url", ""), "img": None
            },
            "language": "en", "family_friendly": True, "type": "search_result",
            "subtype": "generic", "is_live": False,
            "meta_url": {"scheme": "https", "netloc": r.get("display_url", ""), "hostname": r.get("display_url", ""), "favicon": None, "path": ""},
            "thumbnail": None, "age": None, "deep_results": None
        })
    brave_web = {"type": "search", "results": brave_web_results, "family_friendly": True}
    return {
        "type": "search", "query": brave_query,
        "mixed": {"type": "mixed", "main": mixed_main, "top": [], "side": []},
        "videos": {"type": "videos", "results": [], "mutated_by_goggles": False},
        "web": brave_web
    }

@app.get("/")
async def root():
    return {"message": "Google CSE Search API", "endpoints": {"/search?q=query": "Tìm kiếm"}}

@app.get("/search")
async def search(q: str = Query(..., description="Từ khóa tìm kiếm"), cx: str = Query(CX, description="Search Engine ID"), offset: int = Query(0, description="Trang kết quả")):
    try:
        raw_results = await fetch_google_cse(q, cx, offset)
        return JSONResponse(transform_to_brave_format(q, raw_results, offset))
    except Exception as e:
        return JSONResponse({"error": str(e), "query": q, "results": [], "status": "error"}, status_code=500)

@app.get("/health")
async def health():
    return {"status": "ok", "cache_size": len(cache), "cx": CX}

@app.get("/clear-cache")
async def clear_cache():
    cache.clear()
    return {"status": "cache cleared"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
