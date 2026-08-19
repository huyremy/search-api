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
async def fetch_google_cse(query: str, cx: str = CX) -> List[Dict]:
    cache_key = f"{query}_{cx}"
    
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
            page = await browser.new_page()
            await page.goto(f"https://cse.google.com/cse?cx={cx}", wait_until="networkidle")
            
            try:
                await page.wait_for_selector(".gsc-input input", timeout=10000)
                await page.fill(".gsc-input input", query)
                
                search_clicked = False
                try:
                    await page.click(".gsc-search-button-v2 button")
                    search_clicked = True
                except:
                    pass
                
                if not search_clicked:
                    await page.press(".gsc-input input", "Enter")
            except Exception as e:
                print(f"Search input error: {e}")
                await browser.close()
                return []

            await asyncio.sleep(3)
            try:
                await page.wait_for_selector(".gsc-result, .gs-result, .gsc-webResult", timeout=15000)
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
            
            await browser.close()
            
            cache[cache_key] = {
                "timestamp": time.time(),
                "results": results
            }
            return results
            
    except Exception as e:
        print(f"CRITICAL ERROR fetching results: {e}")
        return []

# --- HÀM MỚI: CHUYỂN ĐỔI SANG FORMAT GIỐNG BRAVE ---
def transform_to_brave_format(query: str, web_results: List[Dict]) -> Dict[str, Any]:
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
        "more_results_available": len(web_results) >= 20, # Giả lập (vì CSE chỉ trả 1 trang)
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
            "description": r.get("snippet", ""), # Brave dùng "description"
            "page_age": None,     # Không có từ Google
            "profile": {
                "name": r.get("display_url", "").replace("www.", "").split(".")[0] if r.get("display_url") else "",
                "url": r.get("link", ""),
                "long_name": r.get("display_url", ""),
                "img": None       # Không có favicon
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

    # Tạo cấu trúc web chính
    brave_web = {
        "type": "search",
        "results": brave_web_results,
        "family_friendly": True
    }

    # Ghép vào JSON chung
    return {
        "type": "search",
        "query": brave_query,
        "mixed": brave_mixed,
        "videos": {
            "type": "videos",
            "results": [],      # Bỏ trống vì không có video từ Google CSE
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
    cx: str = Query(CX, description="Search Engine ID")
):
    try:
        raw_results = await fetch_google_cse(q, cx)
        
        # Chuyển đổi dữ liệu sang format Brave
        brave_style_json = transform_to_brave_format(q, raw_results)
        
        return JSONResponse(brave_style_json)
        
    except Exception as e:
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
