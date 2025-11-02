import joblib
import pandas as pd
import numpy as np
import asyncio
import re
from typing import List, Dict, Any, Optional
# المكتبات الهامة للبيئة (تذكر إضافتها في requirements.txt)
from bs4 import BeautifulSoup 
from playwright.async_api import async_playwright 

# -----------------------------------------------------
#                وحدة الذكاء الاصطناعي (AI Selector)
# -----------------------------------------------------

# دالة هندسة الميزات للاستدلال (Inference) - 8 ميزات
def feature_engineer_for_inference(record: dict) -> list:
    """تستخرج الميزات الثمانية بنفس الترتيب الذي تم التدريب عليه."""
    
    text_content = record.get('text_content', '')
    tag_type = record.get('tag_type', '')
    css_class = record.get('css_class', '')
    href = record.get('href', '')
    css_selector = record.get('css_selector', '')
    is_near_pdf_keyword = record.get('is_near_pdf_keyword', 0)
    feat_depth = record.get('feat_depth', 0)
    feat_is_in_main_section = record.get('feat_is_in_main_section', 0)

    features = []
    
    # الـ 8 ميزات بالترتيب:
    features.append(1.0 if ('تحميل' in text_content or 'download' in text_content.lower()) else 0.0)
    features.append(1.0 if tag_type == 'a' else 0.0)
    features.append(float(len(css_class.split()) if css_class else 0.0))
    features.append(float(is_near_pdf_keyword))
    features.append(1.0 if (href and (href.endswith('.pdf') or href.endswith('.zip') or href.endswith('.epub'))) else 0.0)
    features.append(float(css_selector.count('.') + css_selector.count('#') if css_selector else 0.0))
    features.append(float(feat_depth))
    features.append(float(feat_is_in_main_section))
    
    return features


# تحميل النموذج (يتم التحميل مرة واحدة)
try:
    AI_SELECTOR_MODEL = joblib.load('selector_classifier_model.pkl')
    print("✅ وحدة MiningEngine: تم تحميل نموذج الذكاء الاصطناعي بنجاح.")
except Exception as e:
    AI_SELECTOR_MODEL = None
    print(f"❌ وحدة MiningEngine: فشل تحميل نموذج الذكاء الاصطناعي. الخطأ: {e}")

# -----------------------------------------------------
#                   كلاس MiningEngine
# -----------------------------------------------------

class MiningEngine:
    
    @staticmethod
    async def get_pdf_link_and_headers(page: Any) -> Optional[Dict[str, Any]]:
        # التحقق من تحميل النموذج
        if AI_SELECTOR_MODEL is None:
            return None 

        html_content = await page.content()
        soup = BeautifulSoup(html_content, 'html.parser')
        
        best_selector = None
        max_probability = 0.0
        candidates = []

        # 1. جمع المرشحين وتوليد الميزات الهيكلية
        for tag in soup.find_all(['a', 'button']):
            href = tag.get('href')
            if not href or href.startswith('#'):
                continue
            
            # حساب العمق وتحديد القسم الرئيسي
            parent_count = 0
            current_tag = tag
            while current_tag.parent is not None and current_tag.parent.name not in ['[document]', 'html']:
                parent_count += 1
                current_tag = current_tag.parent
            is_in_main = 1 if tag.find_parent(['main', 'article']) else 0
            
            record = {
                "text_content": tag.get_text().strip(),
                "tag_type": tag.name,
                "css_class": tag.get('class', [''])[0],
                # مُحدِّد بسيط للاستخدام في النقر
                "css_selector": f"{tag.name}[href='{href}']", 
                "href": href,
                "feat_depth": parent_count,
                "feat_is_in_main_section": is_in_main,
                "is_near_pdf_keyword": 1 if 'pdf' in tag.get_text().lower() else 0
            }
            candidates.append(record)

        if not candidates:
            return None

        # 2. تقييم المرشحين باستخدام الذكاء الاصطناعي
        for record in candidates:
            features = feature_engineer_for_inference(record)
            probability = AI_SELECTOR_MODEL.predict_proba(np.array([features]))[0][1] 
            
            if probability > max_probability:
                max_probability = probability
                best_selector = record['css_selector']
                best_href = record['href'] # حفظ الرابط لاستخدامه لاحقاً
        
        CONFIDENCE_THRESHOLD = 0.70 
        
        # 3. القرار النهائي والنقر
        if max_probability < CONFIDENCE_THRESHOLD:
            return None
        
        print(f"✅ تم اختيار المحدد: {best_selector} ({max_probability:.4f})")
        
        # 🚨 منطق النقر الفعلي (تم إكماله بمنطق قياسي لمراقبة التحميل)
        
        # تعريف متغير لمراقبة رابط الملف النهائي
        download_url = None
        
        # دالة لمراقبة الشبكة والتقاط رابط الملف
        def handle_download(download):
            nonlocal download_url
            download_url = download.url
            print(f"📥 تم التقاط رابط التحميل المباشر: {download_url}")
            
        page.on("download", handle_download)
        
        print(f"🖱️ النقر على المحدد: {best_selector}")
        await page.click(best_selector, timeout=15000)
        
        # الانتظار القصير لإتمام التحميل
        await asyncio.sleep(2) 

        return {
            "selector": best_selector, 
            "confidence": max_probability,
            "final_download_link": download_url if download_url else best_href
        }


# -----------------------------------------------------
#                   منطق التشغيل الرئيسي (للتجربة)
# -----------------------------------------------------

async def run_mining_task(url: str):
    """دالة لفتح المتصفح وتنفيذ مهمة الاستخلاص."""
    # (هذا الجزء لن يتم استخدامه مباشرة بواسطة البوت، ولكنه مفيد للتجربة)
    print(f"\n--- بدء مهمة الاستخلاص للرابط: {url} ---")
    
    async with async_playwright() as p:
        browser = await p.chromium.launch() 
        page = await browser.new_page()
        
        try:
            await page.goto(url, timeout=60000)
        except Exception:
            await browser.close()
            return

        result = await MiningEngine.get_pdf_link_and_headers(page)
        await browser.close()
        
        return result

# if __name__ == "__main__":
#     # يمكنك وضع رابط اختبار هنا
#     TEST_URL = "https://www.kotobati.com" 
#     asyncio.run(run_mining_task(TEST_URL))
