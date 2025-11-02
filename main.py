# --- دالة الاستخلاص المطلقة المُطوّرة (V10.3 - قاهر النوافذ المنبثقة) ---
async def get_pdf_link_from_page(link: str):
    """
    تستخدم Playwright بخيارات تحصين متقدمة ومسار استماع للنافذة المنبثقة (popup) 
    لتجاوز التفاعلات المعقدة في المواقع.
    """
    pdf_link = None
    page_title = "book" 
    browser = None 
    
    # التحقق الأول (بدون تغيير)
    if link.lower().endswith('.pdf') or 'archive.org/download' in link.lower() or 'drive.google.com' in link.lower():
        return link, "Direct PDF"
        
    try:
        async with async_playwright() as p:
            # إعداد التحصين ومحاكاة الجهاز المحمول (بدون تغيير)
            iphone_13 = p.devices['iPhone 13']
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    '--no-sandbox', 
                    '--disable-setuid-sandbox',
                    '--disable-blink-features=AutomationControlled', 
                    f'--user-agent={iphone_13["user_agent"]}' 
                ]
            )
            context = await browser.new_context(**iphone_13) 
            page = await context.new_page()

            await page.goto(link, wait_until="domcontentloaded", timeout=40000) 
            
            # قراءة العنوان الأولي وتحديد محددات التحميل (بدون تغيير)
            html_content = await page.content()
            soup = BeautifulSoup(html_content, "html.parser")
            page_title = soup.title.string if soup.title else "book"
            download_selector_css = 'a[href*="pdf"], a.book-dl-btn, a.btn-download, button:has-text("تحميل"), a:has-text("Download"), a:has-text("ابدأ التحميل"), a:has-text("اضغط هنا للتحميل")'
            
            # --- الاستراتيجيات 2 و 1 و 4 و 3 (السابقة) يتم تنفيذها هنا ... ---
            
            # 1. الانتظار الذكي (Strategy 2)
            try:
                await page.wait_for_selector('a[href$=".pdf"], a[href*="download"], a[href*="drive.google.com"]', timeout=10000)
                html_content = await page.content()
                soup = BeautifulSoup(html_content, "html.parser")
                
                for a_tag in soup.find_all('a', href=True):
                    href = urljoin(link, a_tag['href'])
                    if href.lower().endswith('.pdf') or 'download' in href.lower() or 'drive.google.com' in href.lower():
                        pdf_link = href
                        print(f"PDF link found via Smart Wait: {pdf_link}")
                        break
            except Exception:
                pass 
                
            if not pdf_link:
                
                # 2. التزامن (gather) (Strategy 1)
                try:
                    pdf_response, _ = await asyncio.gather(
                        page.wait_for_response(
                            lambda response: response.status in [200, 206, 301, 302] and (
                                'application/pdf' in response.headers.get('content-type', '') or 
                                response.url.lower().endswith('.pdf')
                            ),
                            timeout=30000
                        ),
                        page.click(download_selector_css, timeout=25000) 
                    )
                    pdf_link = pdf_response.url
                    
                except Exception:
                    
                    # 3. التنقيب الشبكي العميق (Strategy 4)
                    print("Executing Deep Network Mining.")
                    pdf_link = await fallback_strategy_4_network_mine(page, download_selector_css, link)
            
            # --- الابتكار 4: الاستماع للنافذة المنبثقة (Strategy 5) ---
            if not pdf_link:
                print("All network/wait strategies failed. Attempting Popup Listener (Strategy 5).")
                
                try:
                    # نقوم بمحاولة النقر على زر التحميل مرة أخرى، بالتزامن مع انتظار ظهور النافذة المنبثقة
                    popup_event = await asyncio.gather(
                        page.wait_for_event('popup', timeout=15000), 
                        page.click(download_selector_css, timeout=10000) 
                    )
                    
                    popup_page = popup_event[0]
                    await popup_page.wait_for_load_state("domcontentloaded")
                    
                    # فحص رابط الصفحة المنبثقة
                    popup_url = popup_page.url.lower()
                    if popup_url.endswith('.pdf') or 'drive.google.com' in popup_url or 'dropbox.com' in popup_url:
                        pdf_link = popup_page.url
                        print(f"PDF link found via Popup Listener (Strategy 5): {pdf_link}")
                    
                    await popup_page.close()
                    
                except Exception as e:
                    print(f"Popup Listener failed: {e}")
                    
            
            # 4. فحص HTML النهائي (Strategy 3 - كمسار أخير)
            if not pdf_link:
                await asyncio.sleep(5) 
                final_html_content = await page.content()
                final_soup = BeautifulSoup(final_html_content, "html.parser")
                
                for a_tag in final_soup.find_all('a', href=True):
                    href = urljoin(link, a_tag['href'])
                    href_lower = href.lower()
                    
                    if href_lower.endswith('.pdf') or 'download' in href_lower:
                        pdf_link = href
                        print(f"General link found in HTML (Strategy 3 - Final): {pdf_link}")
                        break

            # التأكد من العنوان النهائي
            if not page_title:
                 html_content = await page.content()
                 soup = BeautifulSoup(html_content, "html.parser")
                 page_title = soup.title.string if soup.title else "book"

            return pdf_link, page_title
    
    except Exception as e:
        print(f"Critical error in get_pdf_link_from_page: {e}")
        raise e
    
    finally:
        if browser:
            await browser.close()
            print("تم ضمان إغلاق متصفح Playwright.")


# --- ملاحظة: يجب أن تبقى دالة search_duckduckgo كما هي من V10.2 ---
# (لأنها تحتوي على فلترة روابط الأقسام)
# ... (بقية دوال الكود تبقى كما هي)
