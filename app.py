from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
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

# Cache đơn giản để giảm tải cho Render
cache: Dict[str, Dict] = {}
CACHE_TTL = 300  # 5 phút
CX = os.getenv("GOOGLE_CX", "83dfe6525b5214c76")  # Lấy từ env

async def fetch_google_cse(query: str, cx: str = CX) -> List[Dict]:
    """Dùng Playwright lấy kết quả từ Google CSE widget"""
    
    cache_key = f"{query}_{cx}"
    
    # 1. Kiểm tra cache
    if cache_key in cache:
        cached_data = cache[cache_key]
        if time.time() - cached_data["timestamp"] < CACHE_TTL:
            print(f"Returning cached results for: {query}")
            return cached_data["results"]
    
    try:
        async with async_playwright() as p:
            # 2. Cấu hình Chromium chạy trên Render (Headless)
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-dev-shm-usage',  # Cần thiết cho Render
                    '--disable-gpu',
                    '--single-process'
                ]
            )
            
            page = await browser.new_page()
            
            # 3. Điều hướng trực tiếp đến trang CSE (Tối ưu hơn set_content)
            print(f"Navigating to Google CSE for query: {query}")
            await page.goto(f"https://cse.google.com/cse?cx={cx}", wait_until="networkidle")
            
            # 4. Tương tác: Nhập từ khóa và thực hiện tìm kiếm
            try:
                # Chờ ô input xuất hiện
                await page.wait_for_selector(".gsc-input input", timeout=10000)
                
                # Nhập từ khóa
                await page.fill(".gsc-input input", query)
                
                # Thử click nút tìm kiếm (Google thường dùng button dạng này)
                search_clicked = False
                try:
                    await page.click(".gsc-search-button-v2 button")
                    search_clicked = True
                except:
                    pass
                
                # Nếu không click được nút, thử gửi phím Enter
                if not search_clicked:
                    await page.press(".gsc-input input", "Enter")
                
            except Exception as e:
                print(f"Search input error: {e}")
                await browser.close()
                return []

            # 5. Đợi kết quả load
            await asyncio.sleep(3) # Thời gian đệm để Google render DOM
            try:
                # Chờ ít nhất một kết quả hiện ra
                await page.wait_for_selector(".gsc-result, .gs-result, .gsc-webResult", timeout=15000)
            except:
                pass # Không có kết quả cũng không crash server

            # 6. Trích xuất dữ liệu (Dùng Selector bền vững nhất)
            results = await page.evaluate('''
                () => {
                    const items = [];
                    // Lấy tất cả thẻ div kết quả
                    const elements = document.querySelectorAll('.gsc-webResult, .gs-webResult');
                    
                    elements.forEach(el => {
                        // Lấy đường dẫn
                        let link = '';
                        const linkEl = el.querySelector('.gs-title a');
                        if (linkEl) {
                            link = linkEl.href;
                            // Xử lý link redirect của Google
                            if (link.startsWith('/url?q=')) {
                                try {
                                    link = decodeURIComponent(link.split('/url?q=')[1].split('&')[0]);
                                } catch {
                                    link = link.split('/url?q=')[1].split('&')[0];
                                }
                            }
                        }

                        // Lấy tiêu đề
                        let title = '';
                        const titleEl = el.querySelector('.gs-title');
                        if (titleEl) {
                            title = titleEl.innerText.trim();
                        } else if (linkEl) {
                            title = linkEl.innerText.trim();
                        }

                        // Lấy đoạn mô tả
                        let snippet = '';
                        const snippetEl = el.querySelector('.gs-snippet');
                        if (snippetEl) snippet = snippetEl.innerText.trim();

                        // Lấy URL hiển thị
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
            
            # 7. Lưu cache
            cache[cache_key] = {
                "timestamp": time.time(),
                "results": results
            }
            print(f"Found {len(results)} results for: {query}")
            
            return results
            
    except Exception as e:
        print(f"CRITICAL ERROR fetching results: {e}")
        return [] # Trả về rỗng thay vì crash server nếu gặp lỗi

@app.get("/")
async def root():
    """Trang chủ"""
    return {
        "message": "Google CSE Search API",
        "endpoints": {
            "/search?q=query": "Tìm kiếm",
            "/health": "Kiểm tra sức khỏe",
            "/clear-cache": "Xóa cache"
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
    """Kiểm tra trạng thái server"""
    return {
        "status": "ok",
        "cache_size": len(cache),
        "cx": CX
    }

@app.get("/clear-cache")
async def clear_cache():
    """Xóa cache thủ công"""
    cache.clear()
    return {"status": "cache cleared"}

# Chạy server
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7860)
