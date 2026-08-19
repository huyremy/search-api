from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from playwright.async_api import async_playwright
import asyncio
import os
import time
from typing import List, Dict

app = FastAPI(title="Google CSE Search API", version="1.0")

# CORS cho frontend gọi
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Cache đơn giản
cache: Dict[str, Dict] = {}
CACHE_TTL = 300  # 5 phút
CX = os.getenv("GOOGLE_CX", "83dfe6525b5214c76")  # Lấy từ env

async def fetch_google_cse(query: str, cx: str = CX) -> List[Dict]:
    """Dùng Playwright lấy kết quả từ Google CSE widget"""
    
    cache_key = f"{query}_{cx}"
    
    # Kiểm tra cache
    if cache_key in cache:
        cached_data = cache[cache_key]
        if time.time() - cached_data["timestamp"] < CACHE_TTL:
            return cached_data["results"]
    
    try:
        async with async_playwright() as p:
            # Launch browser với chế độ headless
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
            
            # Inject widget vào trang
            html = f'''
            <!DOCTYPE html>
            <html>
            <head>
                <meta charset="UTF-8">
                <meta name="viewport" content="width=device-width, initial-scale=1">
            </head>
            <body style="font-family: Arial, sans-serif; padding: 20px;">
                <h3>🔍 Tìm kiếm: {query}</h3>
                <div id="results" class="gcse-search"></div>
                
                <script async src="https://cse.google.com/cse.js?cx={cx}"></script>
                <script>
                    // Tự động tìm kiếm sau khi load
                    window.onload = function() {{
                        setTimeout(function() {{
                            const input = document.querySelector('.gsc-input input');
                            if (input) {{
                                input.value = "{query}";
                                const event = new Event('input', {{ bubbles: true }});
                                input.dispatchEvent(event);
                                
                                const btn = document.querySelector('.gsc-search-button button');
                                if (btn) btn.click();
                            }}
                        }}, 1500);
                    }};
                </script>
            </body>
            </html>
            '''
            
            await page.set_content(html)
            
            # Đợi kết quả load
            try:
                await page.wait_for_selector('.gsc-result', timeout=30000)
            except:
                await page.wait_for_selector('.gs-result', timeout=30000)
            
            # Đợi thêm để load hết
            await asyncio.sleep(2)
            
            # Lấy kết quả từ DOM
            results = await page.evaluate('''
                () => {
                    const items = [];
                    // Thử nhiều selector khác nhau
                    const selectors = [
                        '.gsc-result .gsc-webResult',
                        '.gsc-result .gs-webResult',
                        '.gsc-result',
                        '.gs-result'
                    ];
                    
                    let elements = [];
                    for (const sel of selectors) {
                        const els = document.querySelectorAll(sel);
                        if (els.length > 0) {
                            elements = els;
                            break;
                        }
                    }
                    
                    elements.forEach(el => {
                        const titleEl = el.querySelector('.gs-title');
                        const linkEl = el.querySelector('.gs-title a');
                        const snippetEl = el.querySelector('.gs-snippet');
                        const urlEl = el.querySelector('.gs-visibleUrl');
                        
                        if (titleEl || linkEl) {
                            items.push({
                                title: titleEl ? titleEl.innerText.trim() : (linkEl ? linkEl.innerText.trim() : ''),
                                link: linkEl ? linkEl.href : '',
                                snippet: snippetEl ? snippetEl.innerText.trim() : '',
                                display_url: urlEl ? urlEl.innerText.trim() : ''
                            });
                        }
                    });
                    
                    return items;
                }
            ''')
            
            await browser.close()
            
            # Lưu cache
            cache[cache_key] = {
                "timestamp": time.time(),
                "results": results
            }
            
            return results
            
    except Exception as e:
        print(f"Error fetching results: {e}")
        return []

@app.get("/")
async def root():
    """Trang chủ"""
    return {
        "message": "Google CSE Search API",
        "endpoints": {
            "/search?q=query": "Tìm kiếm",
            "/health": "Kiểm tra sức khỏe"
        }
    }

@app.get("/search")
async def search(
    q: str = Query(..., description="Từ khóa tìm kiếm"),
    cx: str = Query(CX, description="Search Engine ID")
):
    """API tìm kiếm qua Google CSE"""
    try:
        results = await fetch_google_cse(q, cx)
        return JSONResponse({
            "query": q,
            "total": len(results),
            "results": results,
            "status": "success"
        })
    except Exception as e:
        return JSONResponse({
            "error": str(e),
            "query": q,
            "results": [],
            "status": "error"
        }, status_code=500)

@app.get("/health")
async def health():
    """Kiểm tra trạng thái"""
    return {
        "status": "ok",
        "cache_size": len(cache),
        "cx": CX
    }

@app.get("/clear-cache")
async def clear_cache():
    """Xóa cache"""
    cache.clear()
    return {"status": "cache cleared"}

# Chạy server
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
