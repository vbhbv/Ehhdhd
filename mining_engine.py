import joblib
import pandas as pd
import numpy as np
import asyncio
import re
from typing import List, Dict, Any, Optional
# تأكد من تثبيت هذه المكتبات في requirements.txt:
from bs4 import BeautifulSoup 
from playwright.async_api import async_playwright 

# -----------------------------------------------------
#                وحدة الذكاء الاصطناعي (AI Selector)
# -----------------------------------------------------

# 🚨 دالة هندسة الميزات للاستدلال (Inference) - 8 ميزات
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
    features.append(1.0 if ('تحميل' in text_content or 'download' in text_content.lower()) else 0.0) # 1
    features.append(1.0 if tag_type == 'a' else 0.0) # 2
    features.append(float(len(css_class.split()) if css_class else 0.0)) # 3
    features.append(float(is_near_pdf_keyword)) # 4
    features.append(1.0 if (href and (href.endswith('.pdf') or href.endswith('.zip') or href.endswith('.epub'))) else 0.0) # 5
    features.append(float(css_selector.count('.') + css_selector.count('#') if css_selector else 0.0)) # 6
    features.append(float(feat_depth)) # 7
    features.append(float(feat_is_in_main_section)) # 8
    
    return features


# 🚨 تحميل النموذج (AI_SELECTOR_MODEL)
try:
    AI_SELECTOR_MODEL = joblib.load('selector_classifier_model.pkl')
    print("✅ وحدة MiningEngine: تم تحميل نموذج الذكاء الاصطناعي بنجاح.")
except Exception as e:
    AI_SELECTOR_MODEL = None
    # تأكد من رفع ملف selector_classifier_model.pkl إلى المستودع!
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

            # حساب العمق (feat_depth) وتحديد القسم الرئيسي (feat_is_in_main_section)
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
            # التنبؤ بالاحتمالية للتصنيف 1 (الهدف)
            probability = AI_SELECTOR_MODEL.predict_proba(np.array([features]))[0][1] 
            
            if probability > max_probability:
                max_probability = probability
                best_selector = record['css_selector']
        
        CONFIDENCE_THRESHOLD = 0.70 
        
        # 3. القرار النهائي
        if max_probability < CONFIDENCE_THRESHOLD:
            print(f"⚠️ تنبيه: أفضل احتمال ({max_probability:.4f}) أقل من 70%.")
            return None
        
        print(f"✅ تم اختيار المحدد: {best_selector} ({max_probability:.4f})")
        
        # ... (هنا يمكنك وضع منطق النقر ومراقبة الشبكة باستخدام best_selector) ...
        return {"selector": best_selector, "confidence": max_probability}


# -----------------------------------------------------
#                   منطق التشغيل الرئيسي (Main Execution)
# -----------------------------------------------------

async def run_mining_task(url: str):
    """دالة لفتح المتصفح وتنفيذ مهمة الاستخلاص."""
    print(f"\n--- بدء مهمة الاستخلاص للرابط: {url} ---")
    
    async with async_playwright() as p:
        # يرجى اختيار المتصفح المناسب للنشر (Chromium هو الأكثر شيوعاً)
        browser = await p.chromium.launch() 
        page = await browser.new_page()
        
        # الانتقال إلى الرابط
        try:
            await page.goto(url, timeout=60000)
            print("✅ تم تحميل الصفحة.")
        except Exception as e:
            print(f"❌ فشل تحميل الصفحة: {e}")
            await browser.close()
            return

        # تنفيذ منطق الاستخلاص
        result = await MiningEngine.get_pdf_link_and_headers(page)
        
        if result:
            print("\n🌟 النتيجة النهائية:")
            print(f"المُحدِّد الأفضل: {result['selector']}")
            print(f"درجة الثقة: {result['confidence']:.4f}")
            # ... (هنا يمكن أن تضع منطق النقر الفعلي باستخدام Playwright) ...
        else:
            print("\n❌ لم يتم العثور على محدد تحميل موثوق.")

        await browser.close()
        print("--- انتهت المهمة ---")

# 🚨 تعديل هذا الجزء لبدء تشغيل البرنامج 
if __name__ == "__main__":
    # ضع هنا الرابط الذي تريد اختباره أو استخلاص البيانات منه
    TEST_URL = "https://books-library.website/" 
    try:
        asyncio.run(run_mining_task(TEST_URL))
    except KeyboardInterrupt:
        print("تم إيقاف البرنامج يدوياً.")
