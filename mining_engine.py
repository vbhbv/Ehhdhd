import joblib
import pandas as pd
import numpy as np
# ... (بقية الاستيرادات: asyncio, playwright, aiohttp, re, BeautifulSoup, List, Dict, Optional) ...

# -----------------------------------------------------
#                وحدة الذكاء الاصطناعي (AI Selector)
# -----------------------------------------------------

# 🚨 الإضافة 1: دالة هندسة الميزات للاستدلال (Inference)
def feature_engineer_for_inference(record: dict) -> list:
    """تستخرج الميزات الثمانية بنفس الترتيب الذي تم التدريب عليه."""
    
    # ضمان التعامل مع القيم المفقودة
    text_content = record.get('text_content', '')
    tag_type = record.get('tag_type', '')
    css_class = record.get('css_class', '')
    href = record.get('href', '')
    css_selector = record.get('css_selector', '')
    is_near_pdf_keyword = record.get('is_near_pdf_keyword', 0)
    feat_depth = record.get('feat_depth', 0)
    feat_is_in_main_section = record.get('feat_is_in_main_section', 0)

    features = []
    
    # 1. feat_has_download_word
    features.append(1.0 if ('تحميل' in text_content or 'download' in text_content.lower()) else 0.0)
    
    # 2. feat_is_anchor
    features.append(1.0 if tag_type == 'a' else 0.0)
    
    # 3. feat_class_length
    features.append(float(len(css_class.split()) if css_class else 0.0))
    
    # 4. feat_structural_proximity
    features.append(float(is_near_pdf_keyword))
    
    # 5. feat_is_file_link
    features.append(1.0 if (href and (href.endswith('.pdf') or href.endswith('.zip') or href.endswith('.epub'))) else 0.0)
    
    # 6. feat_selector_complexity
    features.append(float(css_selector.count('.') + css_selector.count('#') if css_selector else 0.0))
    
    # 7. feat_depth_v2
    features.append(float(feat_depth))
    
    # 8. feat_is_in_main_section_v2
    features.append(float(feat_is_in_main_section))
    
    return features


# 🚨 الإضافة 2: تحميل النموذج
try:
    AI_SELECTOR_MODEL = joblib.load('selector_classifier_model.pkl')
    print("✅ وحدة MiningEngine: تم تحميل نموذج الذكاء الاصطناعي بنجاح.")
except Exception as e:
    AI_SELECTOR_MODEL = None
    print(f"❌ وحدة MiningEngine: فشل تحميل نموذج الذكاء الاصطناعي. سيتم استخدام المنطق اليدوي (إذا وجد). الخطأ: {e}")

# -----------------------------------------------------
#                   كلاس MiningEngine
# -----------------------------------------------------

class MiningEngine:
    # ... (بقية الكود) ...
    
    @staticmethod
    async def get_pdf_link_and_headers(page: Any) -> Optional[Dict[str, Any]]:
        # ⚠️ التحقق من تحميل النموذج أولاً
        if AI_SELECTOR_MODEL is None:
            # يمكن وضع منطق بديل أو إرجاع None إذا كان الذكاء الاصطناعي مطلوباً
            return None 

        html_content = await page.content()
        soup = BeautifulSoup(html_content, 'html.parser')
        
        best_selector = None
        max_probability = 0.0
        
        candidates = []

        # 1. جمع المرشحين وتوليد الميزات الهيكلية في وقت التشغيل
        for tag in soup.find_all(['a', 'button']):
            href = tag.get('href')
            if not href or href.startswith('#'):
                continue

            # حساب العمق (feat_depth)
            parent_count = 0
            current_tag = tag
            while current_tag.parent is not None and current_tag.parent.name not in ['[document]', 'html']:
                parent_count += 1
                current_tag = current_tag.parent
            
            # تحديد القسم الرئيسي (feat_is_in_main_section)
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
        print("🧠 تقييم المرشحين باستخدام نموذج الذكاء الاصطناعي...")
        
        for record in candidates:
            features = feature_engineer_for_inference(record)
            
            # التنبؤ بالاحتمالية
            # يجب استخدام np.array لتحويل القائمة إلى تنسيق مقبول للنموذج
            probability = AI_SELECTOR_MODEL.predict_proba(np.array([features]))[0][1] 
            
            if probability > max_probability:
                max_probability = probability
                best_selector = record['css_selector']
        
        CONFIDENCE_THRESHOLD = 0.70 
        
        if max_probability < CONFIDENCE_THRESHOLD:
            print(f"⚠️ تنبيه: أفضل احتمال ({max_probability:.4f}) أقل من 70%. سيتم إيقاف الاستخلاص.")
            return None
        
        print(f"✅ تم اختيار المحدد بالاحتمالية: {best_selector} ({max_probability:.4f})")
        
        # ... (بقية منطق النقر ومراقبة الشبكة) ...
        return {"selector": best_selector, "confidence": max_probability}

# ... (بقية الكلاسات والدوال) ...
