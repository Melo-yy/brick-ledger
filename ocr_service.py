"""OCR service based on EasyOCR for Chinese text extraction from order screenshots."""
import os
import re
import numpy as np
import cv2
import easyocr

_reader = None

# Store models on persistent volume so they survive restarts
_MODEL_DIR = os.environ.get(
    "EASYOCR_MODEL_DIR",
    os.path.join(os.environ.get("DATA_DIR", os.path.expanduser("~")), ".EasyOCR"),
)


def _get_reader():
    global _reader
    if _reader is None:
        _reader = easyocr.Reader(
            ["ch_sim", "en"],
            gpu=False,
            model_storage_directory=_MODEL_DIR,
            download_enabled=True,
        )
    return _reader


def _imread_unicode(path: str) -> np.ndarray:
    with open(path, "rb") as f:
        buf = f.read()
    arr = np.frombuffer(buf, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise IOError(f"Failed to decode image: {path}")
    return img


def extract_text(image_path: str) -> list[dict]:
    """Run OCR and return text blocks sorted top-to-bottom."""
    reader = _get_reader()
    img = _imread_unicode(image_path)
    results = reader.readtext(img)
    blocks = []
    for bbox, text, confidence in results:
        y_coords = [pt[1] for pt in bbox]
        blocks.append({
            "text": text.strip(),
            "confidence": round(confidence, 3),
            "bbox": [[int(x), int(y)] for x, y in bbox],
            "y_center": sum(y_coords) / len(y_coords),
        })
    blocks.sort(key=lambda b: b["y_center"])
    return blocks


# ── Compiled patterns ──────────────────────────────────────────────────────

_PLATFORM_MAP = [
    ("淘宝", re.compile(r"淘宝|taobao|TMALL|天猫", re.I)),
    ("拼多多", re.compile(r"拼多多|pinduoduo|PDD|百亿贴|百亿补贴|拼单|多人团|先用后付", re.I)),
    ("京东", re.compile(r"京东|jd\.|JD", re.I)),
    ("得物", re.compile(r"得物|识货|毒", re.I)),
]

# "半" is a common OCR misread of "¥" — 半355 means ¥355
# (price patterns are inlined in _find_expense)

# Date
_DATE_RE = re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})[日]?")

# Size: after explicit labels (require word boundary to avoid false matches like "该规格补贴价")
_SIZE_LABEL = re.compile(r"(?:^|[^\w])"
                         r"(?:尺码|码数|规格|鞋码|尺寸)[：:\s]*"
                         r"([\w./\-+·()（）、，]+)")
# Size after semicolon or slash in product code: "xxx/主图款;42"
_SIZE_AFTER_CODE = re.compile(r"(?:/|;)[一-鿿]*;?(\d{2}\.?5?)\s*$")
# Size after comma: "98跑,43"
_SIZE_TRAILING_COMMA = re.compile(r"[,，]\s*(\d{2}\.?5?)\s*$")
# Standalone shoe size number (30-50)
_SIZE_NUM = re.compile(r"\b(3[0-9]|4[0-5]|45|46|47|48|49|50)\b")


def _find_platform(texts: list[str]) -> str | None:
    full = "\n".join(texts)
    for name, pattern in _PLATFORM_MAP:
        if pattern.search(full):
            return name
    return None


def _find_expense(texts: list[str]) -> float | None:
    """
    Extract actual payment amount.

    Strategy (in order):
    1. "实付" keyword — but skip "先用后付" (pay later) where the payment is 0.
    2. Collect all "半/¥#" prices, exclude negatives and discounts,
       then take the smallest non-discounted price.
    """

    # ── 1. "实付" — skip 先用后付 ──
    for i, t in enumerate(texts):
        if "实付" in t and "先用后付" not in t:
            # Check current line
            m = re.search(r"实付[款]?\s*[：:]?\s*[¥￥半]?\s*(\d+\.?\d*)", t)
            if m:
                try:
                    v = float(m.group(1))
                    if v > 0:
                        return v
                except ValueError:
                    pass
            # Check previous line (实付款 label often sits on its own line after the price)
            if i > 0:
                m = re.search(r"[¥￥半]\s*(\d+\.?\d*)", texts[i - 1])
                if m:
                    try:
                        return float(m.group(1))
                    except ValueError:
                        pass
            # Check next line
            if i + 1 < len(texts):
                m = re.search(r"[¥￥半]\s*(\d+\.?\d*)", texts[i + 1])
                if m:
                    try:
                        return float(m.group(1))
                    except ValueError:
                        pass

    # ── 1.5 "自动确认收货并付款" — 拼多多先用后付的实际金额就在它后面 ──
    for i, t in enumerate(texts):
        if "自动确认收货" in t or "确认收货并付款" in t:
            # Check next 2 lines for the actual payment
            for j in range(i + 1, min(i + 3, len(texts))):
                m = re.search(r"[¥￥半]\s*(\d+\.?\d*)", texts[j])
                if m:
                    try:
                        v = float(m.group(1))
                        if 1 <= v <= 99999:
                            return v
                    except ValueError:
                        pass

    # ── 2. Collect valid prices (exclude negatives and discount keywords) ──
    valid = []
    for t in texts:
        # Skip entire line if it contains discount indicators
        if re.search(r"(优惠|补贴|红包|券|减|先用后付)", t):
            continue
        for m in re.finditer(r"[¥￥半]\s*(\d+\.?\d*)", t):
            # Skip negative amounts (discounts like "-半13")
            if m.start() > 0 and t[m.start() - 1] in ("-", "~", "﹣"):
                continue
            try:
                v = float(m.group(1))
                if 1 <= v <= 99999:
                    valid.append(v)
            except ValueError:
                pass

    if valid:
        valid.sort()
        # The smallest non-discount price is typically the actual payment
        return valid[0]

    # ── 3. Last resort: bare integers ──
    for t in texts:
        for m in re.finditer(r"\b(\d{3,4}(?:\.\d{1,2})?)\b", t):
            try:
                v = float(m.group(1))
                if 10 <= v <= 99999:
                    return v
            except ValueError:
                pass

    return None


def _find_date(texts: list[str]) -> str | None:
    """Extract date in YYYY-MM-DD format."""
    full = "\n".join(texts)
    # Prefer lines with date-related keywords
    for t in texts:
        if not re.search(r"(时间|日期|下单|付款|订单)", t):
            continue
        m = _DATE_RE.search(t)
        if m:
            y, mth, d = m.groups()
            return f"{int(y):04d}-{int(mth):02d}-{int(d):02d}"
    m = _DATE_RE.search(full)
    if m:
        y, mth, d = m.groups()
        return f"{int(y):04d}-{int(mth):02d}-{int(d):02d}"
    return None


def _find_size(texts: list[str]) -> str | None:
    """Extract size from order text."""
    full = "\n".join(texts)

    # 1. Explicit size labels
    m = _SIZE_LABEL.search(full)
    if m:
        val = m.group(1).strip()
        val = re.sub(r"[,，、\s]+$", "", val)
        if val and len(val) < 20:
            return val

    # 2. Size after product code (e.g. "0H4071-004/主图款;42")
    for t in texts:
        if re.search(r"(?:/|;)", t):
            m = _SIZE_AFTER_CODE.search(t)
            if m:
                return m.group(1)

    # 3. Size after comma (e.g. "98跑,43")
    for t in texts:
        m = _SIZE_TRAILING_COMMA.search(t)
        if m:
            return m.group(1)

    # 4. Standalone shoe size number (30-50)
    for t in texts:
        # Skip lines that look like times, years, or prices
        if re.search(r":\d{2}|20\d{2}|[¥￥半]", t):
            continue
        # Skip very short or pure numeric lines (likely order numbers)
        m = _SIZE_NUM.search(t)
        if m:
            return m.group(1)

    return None


def _find_model(texts: list[str]) -> str | None:
    """
    Find the product name/model from OCR text.
    Prefers lines that contain brand+model identifiers.
    """
    # Brand/model pattern: English brand + Chinese product (e.g. "NIKE耐克飞马39")
    _BRAND_PRODUCT = re.compile(r'[A-Za-z].*[一-鿿]|[一-鿿].*[A-Za-z]')
    # SKU code pattern: alphanumeric with optional hyphens (e.g. "0H4071-004")
    _SKU_CODE = re.compile(r'[A-Za-z0-9]{4,}[-][A-Za-z0-9]+')

    candidates = []
    for t in texts:
        t = t.strip()
        if len(t) < 6:
            continue

        # Skip pure garbage lines
        if re.match(r"^[\d\s.,¥￥%#\-—/\\()（）\[\]:：+×Xx*'\"'半]+$", t):
            continue

        # Skip known non-product lines
        if re.match(
            r"^(店铺|掌柜|卖家|商家|客服|物流|快递|订单[编号]?|"
            r"交易|创建时间|付款时间|发货时间|成交时间|"
            r"收货|地址|电话|手机|备注|优惠券|红包|积分|"
            r"倒计时|已签收|自动确认|还剩|直播|"
            r"共\d+件|小计|运费|配送|合计|实付|"
            r"商品总价|店铺优惠|平台优惠|支付优惠|红包|"
            r"假一赔|极速退款|无理由|申请售后|"
            r"确认收货|延长收货|查看物流|"
            r"获得.*积分|支付宝交易号|更多型号).*$", t):
            continue

        chinese_chars = len(re.findall(r'[一-鿿]', t))
        if chinese_chars == 0 and len(t) < 10:
            continue

        score = 0
        score += min(chinese_chars * 2, 20)  # Cap Chinese char bonus at 20

        # ── Strong signals for product title ──
        has_english = bool(re.search(r'[a-zA-Z]', t))
        has_number = bool(re.search(r'[0-9]', t))

        if has_english:
            score += 15  # English = brand name indicator
        if has_number:
            score += 8   # Numbers = model/SKU indicator
        if _BRAND_PRODUCT.search(t):
            score += 20  # Mixed EN+ZH is the strongest signal for product title
        if _SKU_CODE.search(t):
            score += 5   # SKU-like pattern (useful but secondary to product name)
        # Bonus: line starts with or contains "品牌" = strong product title signal
        if '品牌' in t:
            score += 10

        # ── Penalties ──
        if re.search(r'(店|铺|馆)', t):
            score -= 20  # Store/shop names
        if re.search(r'(客服|热线|电话|回复|好评率|V[IP]?|VIP)', t):
            score -= 15
        if re.search(r'(倒计时|已签收|自动确认|还剩.*天|直播[中]?)', t):
            score -= 15
        if re.search(r'(商品下架|下架)', t):
            score -= 10
        # "|" pipe separator = description/subtitle line, not main product title
        if '|' in t:
            score -= 12

        # ── Product keyword bonus ──
        kw_count = len(re.findall(r'(鞋|衣|帽|包|裤|装|运动|跑步|休闲|男|女|童|款|色|健|身|户外|飞|马|品牌|李|宁|驭|帅|碳|板|跑)', t))
        score += kw_count * 3

        # ── Penalty for pure-description lines (no brand, no model number) ──
        if chinese_chars >= 8 and not has_english and not has_number:
            score -= 10  # Likely a subtitle/description, not the product title

        candidates.append((score, t))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    best_score, best_text = candidates[0]
    if best_score > 0:
        return _clean_model(best_text)
    return None


def _clean_model(text: str) -> str:
    """Remove platform labels/prefixes from model name."""
    # "品牌[]李宁驭帅17" → "李宁驭帅17"
    # "品牌：[李宁]" → "李宁"
    text = re.sub(r'^品牌[\[\]（()）【】\s：:]*', '', text)
    # Remove trailing/leading brackets
    text = re.sub(r'^[\[\]（()）【】\s]+', '', text)
    text = re.sub(r'[\[\]（()）【】\s]+$', '', text)
    return text.strip()


# ── Public API ─────────────────────────────────────────────────────────────

def parse_fields(blocks: list[dict]) -> dict:
    texts = [b["text"] for b in blocks]
    return {
        "expense": _find_expense(texts),
        "order_date": _find_date(texts),
        "size": _find_size(texts),
        "platform": _find_platform(texts),
        "model": _find_model(texts),
    }


def ocr_image(image_path: str) -> dict:
    blocks = extract_text(image_path)
    texts = [b["text"] for b in blocks]
    fields = parse_fields(blocks)
    return {"fields": fields, "raw_texts": texts, "blocks": blocks}
