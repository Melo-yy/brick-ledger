"""OCR service: Baidu OCR API (cloud) or EasyOCR (local fallback)."""
import os
import re
import base64
import json
import time
import urllib.request
import urllib.parse

# ── Baidu OCR ─────────────────────────────────────────────────────────────

_API_KEY = os.environ.get("BAIDU_OCR_API_KEY", "")
_SECRET_KEY = os.environ.get("BAIDU_OCR_SECRET_KEY", "")
_USE_BAIDU = bool(_API_KEY and _SECRET_KEY)

_token = None
_token_expiry = 0


def _baidu_get_token() -> str:
    """Get Baidu OCR access token (cached, expires in 30 days)."""
    global _token, _token_expiry
    if _token and time.time() < _token_expiry:
        return _token
    url = "https://aip.baidubce.com/oauth/2.0/token"
    params = urllib.parse.urlencode({
        "grant_type": "client_credentials",
        "client_id": _API_KEY,
        "client_secret": _SECRET_KEY,
    })
    with urllib.request.urlopen(f"{url}?{params}", timeout=10) as resp:
        data = json.loads(resp.read())
    _token = data["access_token"]
    _token_expiry = time.time() + 86400 * 25  # refresh after 25 days
    return _token


def _baidu_ocr(image_path: str) -> list[dict]:
    """
    Call Baidu OCR API (accurate mode), return sorted text blocks.
    Each block: {"text": str, "confidence": float, "bbox": [...], "y_center": float}
    """
    # Encode image to base64
    with open(image_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    token = _baidu_get_token()
    url = f"https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic?access_token={token}"

    data = urllib.parse.urlencode({"image": img_b64}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/x-www-form-urlencoded"})

    with urllib.request.urlopen(req, timeout=30) as resp:
        result = json.loads(resp.read())

    # Collect all text into one string, then split into pseudo-lines
    all_text = "".join(
        item.get("words", "") for item in result.get("words_result", [])
    )

    # Split on common separator chars found in order screenshots
    lines = _split_ocr_text(all_text)

    blocks = [{"text": t, "confidence": 1.0, "bbox": [], "y_center": i * 100}
              for i, t in enumerate(lines) if t.strip()]
    return blocks


def _merge_line_blocks(blocks: list[dict]) -> list[dict]:
    """
    Merge blocks on the same line using anchored Y tolerance.
    Compares against the FIRST block in each line to avoid chain-merging.
    """
    if not blocks:
        return blocks
    lines = []
    current_line = [blocks[0]]
    anchor_y = blocks[0]["y_center"]
    for b in blocks[1:]:
        if abs(b["y_center"] - anchor_y) <= 12:
            current_line.append(b)
        else:
            lines.append(current_line)
            current_line = [b]
            anchor_y = b["y_center"]
    lines.append(current_line)
    merged = []
    for line in lines:
        line.sort(key=lambda b: b["bbox"][0][0] if b.get("bbox") else 0)
        text = "".join(b["text"] for b in line)
        merged.append({
            "text": text,
            "confidence": sum(b["confidence"] for b in line) / len(line),
            "bbox": line[0]["bbox"] if line[0].get("bbox") else [],
            "y_center": line[0]["y_center"],
        })
    return merged


def _split_ocr_text(text: str) -> list[str]:
    """
    Split a single OCR text blob into pseudo-lines using order screenshot patterns.
    Baidu accurate_basic often returns all text as one block; this reconstructs lines.
    """
    if not text:
        return []
    # Insert newlines before common section starts
    text = re.sub(r'(?<=[^\s])>', '>\n', text)           # ">" as section separator
    text = re.sub(r'(?<=[^\s])x1', '\nx1', text)          # "x1" quantity
    text = re.sub(r'(?<=[^\s])×1', '\n×1', text)          # "×1" quantity (fullwidth)
    text = re.sub(r'(发货时间)', r'\n\1', text)            # time labels
    text = re.sub(r'(付款时间)', r'\n\1', text)
    text = re.sub(r'(创建时间)', r'\n\1', text)
    text = re.sub(r'(下单时间)', r'\n\1', text)
    text = re.sub(r'(支付宝交易号)', r'\n\1', text)
    text = re.sub(r'(天猫积分)', r'\n\1', text)
    text = re.sub(r'(申请售后)', r'\n\1', text)
    text = re.sub(r'(商品总价)', r'\n\1', text)
    text = re.sub(r'(店铺优惠)', r'\n\1', text)
    text = re.sub(r'(平台优惠)', r'\n\1', text)
    text = re.sub(r'(支付优惠)', r'\n\1', text)
    text = re.sub(r'(实付款)', r'\n\1', text)
    text = re.sub(r'(查看物流)', r'\n\1', text)
    text = re.sub(r'(确认收货)', r'\n\1', text)
    text = re.sub(r'(延长收货)', r'\n\1', text)
    text = re.sub(r'(客服)', r'\n\1', text)
    # PDD-specific separators
    text = re.sub(r'(支付方式[：:])', r'\n\1', text)
    text = re.sub(r'(物流公司[：:])', r'\n\1', text)
    text = re.sub(r'(快递单号[：:])', r'\n\1', text)
    text = re.sub(r'(订单编号[：:])', r'\n\1', text)
    text = re.sub(r'(拼单时间)', r'\n\1', text)
    text = re.sub(r'(拼单已同步)', r'\n\1', text)
    text = re.sub(r'(商品快照[：:]?)', r'\n\1', text)
    text = re.sub(r'(已购规格)', r'\n\1', text)
    text = re.sub(r'(已购数量)', r'\n\1', text)
    text = re.sub(r'(合计补贴价)', r'\n\1', text)
    text = re.sub(r'(补贴后共优惠)', r'\n\1', text)
    # Split at "先用后付" separator line
    text = re.sub(r'(先用后付)', r'\n\1', text)
    text = re.sub(r'(退货包运费)', r'\n\1', text)
    # Split "百亿小贴" / "百亿补贴" from product text
    text = re.sub(r'(百亿小贴|百亿补贴)', r'\n\1', text)

    # Split before ¥ when preceded by non-digit (e.g. "跑￥355")
    text = re.sub(r'(?<=[^¥￥\d\s])[¥￥]', r'\n¥', text)
    # Split before ¥ when preceded by digit (e.g. "42￥1058" → "42\n￥1058")
    text = re.sub(r'(?<=\d)[¥￥]', r'\n¥', text)
    # Split at uppercase brand names (e.g. "...回复NIKE..." → "...回复\nNIKE...")
    text = re.sub(r'(?<=[一-鿿])([A-Z]{2,})', r'\n\1', text)

    lines = [l.strip() for l in text.split('\n') if l.strip()]
    return lines if lines else [text]


# ── EasyOCR (local fallback) ──────────────────────────────────────────────

_easyocr_reader = None
_MODEL_DIR = os.environ.get(
    "EASYOCR_MODEL_DIR",
    os.path.join(os.environ.get("DATA_DIR", os.path.expanduser("~")), ".EasyOCR"),
)


def _easyocr_get_reader():
    global _easyocr_reader
    if _easyocr_reader is None:
        import easyocr
        _easyocr_reader = easyocr.Reader(
            ["ch_sim", "en"], gpu=False,
            model_storage_directory=_MODEL_DIR,
            download_enabled=True,
        )
    return _easyocr_reader


def _easyocr_extract(image_path: str) -> list[dict]:
    import cv2
    import numpy as np
    reader = _easyocr_get_reader()
    # Read with Unicode path support
    with open(image_path, "rb") as f:
        buf = f.read()
    arr = np.frombuffer(buf, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)

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


# ── Extract text (auto-select backend) ────────────────────────────────────

def extract_text(image_path: str) -> list[dict]:
    """Run OCR and return text blocks sorted top-to-bottom."""
    if _USE_BAIDU:
        return _baidu_ocr(image_path)
    return _easyocr_extract(image_path)


# ── Field parsing (shared by both backends) ───────────────────────────────

_PLATFORM_MAP = [
    ("淘宝", re.compile(r"淘宝|taobao|TMALL|天猫", re.I)),
    ("拼多多", re.compile(r"拼多多|pinduoduo|PDD|百亿贴|百亿补贴|拼单|多人团|先用后付", re.I)),
    ("京东", re.compile(r"京东|jd\.|JD", re.I)),
    ("得物", re.compile(r"得物|识货|毒", re.I)),
]

_DATE_RE = re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})[日]?")

_SIZE_LABEL = re.compile(r"(?:^|[^\w])"
                         r"(?:尺码|码数|规格|鞋码|尺寸)[：:\s]*"
                         r"([\w./\-+·()（）、，]+)")
_SIZE_AFTER_CODE = re.compile(r"(?:/|;)[一-鿿]*;?(\d{2}\.?5?)\s*$")
_SIZE_TRAILING_COMMA = re.compile(r"[,，]\s*(\d{2}\.?5?)\s*$")
_SIZE_NUM = re.compile(r"\b(3[0-9]|4[0-5]|45|46|47|48|49|50)\b")


def _find_platform(texts):
    full = "\n".join(texts)
    for name, pattern in _PLATFORM_MAP:
        if pattern.search(full):
            return name
    return None


def _find_expense(texts):
    for i, t in enumerate(texts):
        if "实付" in t and "先用后付" not in t:
            m = re.search(r"实付[款]?\s*[：:]?\s*[¥￥半]?\s*(\d+\.?\d*)", t)
            if m:
                try:
                    v = float(m.group(1))
                    if v > 0:
                        return v
                except ValueError:
                    pass
            # Check NEXT line first (actual payment is after 实付款, not before)
            if i + 1 < len(texts):
                m = re.search(r"[¥￥半]?\s*(\d+\.?\d*)", texts[i + 1])
                if m:
                    try:
                        return float(m.group(1))
                    except ValueError:
                        pass
            # Then check previous line (for cases like "¥355\n实付款")
            if i > 0:
                m = re.search(r"[¥￥半]\s*(\d+\.?\d*)", texts[i - 1])
                if m:
                    try:
                        v = float(m.group(1))
                        # Skip very small values (< 10) which are likely discounts
                        if v >= 10:
                            return v
                    except ValueError:
                        pass

    for i, t in enumerate(texts):
        if "自动确认收货" in t or "确认收货并付款" in t:
            for j in range(i + 1, min(i + 3, len(texts))):
                m = re.search(r"[¥￥半]\s*(\d+\.?\d*)", texts[j])
                if m:
                    try:
                        v = float(m.group(1))
                        if 1 <= v <= 99999:
                            return v
                    except ValueError:
                        pass

    valid = []
    for t in texts:
        if re.search(r"(优惠|补贴|红包|券|减|先用后付)", t):
            continue
        for m in re.finditer(r"[¥￥半]\s*(\d+\.?\d*)", t):
            if m.start() > 0 and t[m.start() - 1] in ("-", "~", "﹣"):
                continue
            try:
                v = float(m.group(1))
                if 10 <= v <= 99999:  # ≥10 to filter out tiny discounts
                    valid.append(v)
            except ValueError:
                pass
    if valid:
        valid.sort()
        return valid[0]

    for t in texts:
        for m in re.finditer(r"\b(\d{3,4}(?:\.\d{1,2})?)\b", t):
            try:
                v = float(m.group(1))
                if 10 <= v <= 99999:
                    return v
            except ValueError:
                pass
    return None


def _find_date(texts):
    full = "\n".join(texts)
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


def _find_size(texts):
    full = "\n".join(texts)
    m = _SIZE_LABEL.search(full)
    if m:
        val = m.group(1).strip()
        val = re.sub(r"[,，、\s]+$", "", val)
        if val and len(val) < 20:
            return val
    for t in texts:
        if re.search(r"(?:/|;)", t):
            m = _SIZE_AFTER_CODE.search(t)
            if m:
                return m.group(1)
    for t in texts:
        m = _SIZE_TRAILING_COMMA.search(t)
        if m:
            return m.group(1)
    for t in texts:
        if re.search(r":\d{2}|20\d{2}|[¥￥半]", t):
            continue
        m = _SIZE_NUM.search(t)
        if m:
            return m.group(1)
    return None


def _find_model(texts):
    _BRAND_PRODUCT = re.compile(r'[A-Za-z].*[一-鿿]|[一-鿿].*[A-Za-z]')
    _SKU_CODE = re.compile(r'[A-Za-z0-9]{4,}[-][A-Za-z0-9]+')

    candidates = []
    for t in texts:
        t = t.strip()
        if len(t) < 6:
            continue
        # ABSOLUTE: skip lines starting with ¥ (price/description, never product title)
        if re.match(r'^[¥￥半]', t):
            continue
        # Skip pure garbage
        if re.match(r"^[\d\s.,¥￥%#\-—/\\()（）\[\]:：+×Xx*'\"'半]+$", t):
            continue
        if re.match(
            r"^(店铺|掌柜|卖家|商家|客服|物流|快递|订单[编号]?|"
            r"交易|创建时间|付款时间|发货时间|成交时间|"
            r"收货|地址|电话|手机|备注|优惠券|红包|积分|"
            r"倒计时|已签收|自动确认|还剩|直播|"
            r"共\d+件|小计|运费|配送|合计|实付|"
            r"商品总价|店铺优惠|平台优惠|支付优惠|红包|"
            r"假一赔|极速退款|无理由|申请售后|"
            r"确认收货|延长收货|查看物流|"
            r"获得.*积分|支付宝交易号|更多型号|"
            r"支付方式|物流公司|快递单号|"
            r"退货包运费|正品险|分享商品|联系|再次拼单|"
            r"补贴后共优惠|合计补贴价|已购规格|已购数量|"
            r"百亿小贴|百亿补贴|拼团|团价|立享低价).*$", t):
            continue

        chinese_chars = len(re.findall(r'[一-鿿]', t))
        if chinese_chars == 0 and len(t) < 10:
            continue
        score = 0
        score += min(chinese_chars * 2, 20)

        has_english = bool(re.search(r'[a-zA-Z]', t))
        has_number = bool(re.search(r'[0-9]', t))

        if has_english:
            score += 15
        if has_number:
            score += 8
        if _BRAND_PRODUCT.search(t):
            score += 20
        if _SKU_CODE.search(t):
            score += 5
        if '品牌' in t:
            score += 10

        if re.search(r'(店|铺|馆)', t):
            score -= 20
        if re.search(r'(客服|热线|电话|回复|好评率|V[IP]?|VIP)', t):
            score -= 15
        if re.search(r'(倒计时|已签收|自动确认|还剩.*天|直播[中]?)', t):
            score -= 15
        if re.search(r'(商品下架|下架)', t):
            score -= 20
        if re.search(r'(补贴价|先用后付|支付方式|快递单号|物流公司)', t):
            score -= 30
        # "品牌>" at end = section header, not product
        if re.search(r'品牌[>\|」》]', t) and chinese_chars < 4:
            score -= 30
        if '|' in t:
            score -= 12

        kw_count = len(re.findall(
            r'(鞋|衣|帽|包|裤|装|运动|跑步|休闲|男|女|童|款|色|'
            r'健|身|户外|飞|马|品牌|李|宁|驭|帅|碳|板|跑)', t))
        score += kw_count * 3

        if chinese_chars >= 8 and not has_english and not has_number:
            score -= 10

        candidates.append((score, t))

    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    best_score, best_text = candidates[0]
    if best_score > 0:
        # Clean prefixes: 百亿补贴品牌xxx → xxx, 品牌[xx] → xx
        text = re.sub(r'^(百亿补贴|百亿小贴|万人团|品牌\s*[\[\]（()）【】\s：:]*)+', '', best_text)
        return text.strip()
    return None


def parse_fields(blocks):
    texts = [b["text"] for b in blocks]
    return {
        "expense": _find_expense(texts),
        "order_date": _find_date(texts),
        "size": _find_size(texts),
        "platform": _find_platform(texts),
        "model": _find_model(texts),
    }


# ── Public API ─────────────────────────────────────────────────────────────

def ocr_image(image_path: str) -> dict:
    blocks = extract_text(image_path)
    texts = [b["text"] for b in blocks]
    fields = parse_fields(blocks)
    return {"fields": fields, "raw_texts": texts, "blocks": blocks}
