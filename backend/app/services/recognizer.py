"""单句测试用的识别器：可插拔。默认实现与原型一致——用例句字二元组相似度模拟识别，
分值与 Bot 线上识别模型不同，只用于运营自查配置，不作为效果评测。"""
import re
from typing import Protocol

_PUNCT = re.compile(r"[！!？?。，,、；;：:~～\s\"'“”]")


def _norm(t: str) -> str:
    return _PUNCT.sub("", str(t or "")).lower()


def _bigrams(t: str) -> list[str]:
    s = _norm(t)
    if len(s) == 1:
        return [s]
    return [s[i:i + 2] for i in range(len(s) - 1)]


class Recognizer(Protocol):
    def score(self, text: str, examples: list[str], name: str) -> float: ...


class ExampleSimilarityRecognizer:
    name = "example-bigram"

    def score(self, text: str, examples: list[str], name: str) -> float:
        tb = _bigrams(text)
        best = 0.0
        for e in list(examples or []) + [name]:
            b = _bigrams(e)
            if not b:
                continue
            m = sum(1 for x in b if x in tb)
            v = (m / len(b)) * 0.6 + (m / max(len(tb), 1)) * 0.4
            best = max(best, v)
        return min(0.98, round(min(1.0, best * 1.1) * 100) / 100)


def slot_value(text: str, n: str) -> str | None:
    t = str(text)
    def m(p, flags=0):
        return re.search(p, t, flags)
    if n == "sku":
        x = m(r"SKU[\w-]+", re.I)
        return x.group(0) if x else ("(会话上下文商品)" if re.search(r"这款|这个|图里|链接", t) else None)
    if n == "qty":
        x = m(r"(\d+)\s*(件|个|箱|瓶|套)")
        return x.group(0) if x else None
    if n == "order_id":
        x = m(r"\d{8,}")
        return x.group(0) if x else ("(会话关联订单)" if re.search(r"订单|拍了|下单|发货|单号|退款|收到", t) else None)
    if n == "target_price":
        x = m(r"(\d+)\s*(块|元)")
        return x.group(0) if x else None
    if n == "reason":
        x = m(r"破损|漏液|质量|发错|少发|坏了")
        return x.group(0) if x else None
    if n == "platform":
        x = m(r"tiktok|shopee|amazon|亚马逊|lazada|temu|etsy|1688", re.I)
        if not x:
            return None
        v = x.group(0)
        if v == "亚马逊":
            return "Amazon"
        return (v[0].upper() + v[1:].lower()).replace("Tiktok", "TikTok")
    if n == "site":
        x = m(r"美区|美国|越南|英国|泰国|马来|\b(US|UK|VN|TH|MY)\b", re.I)
        return ({"美区": "US", "美国": "US", "越南": "VN", "英国": "UK", "泰国": "TH", "马来": "MY"}.get(x.group(0))
                or x.group(0).upper()) if x else None
    if n == "language":
        x = m(r"越南语|英文|英语|中文|泰语|西班牙语|西语|德语|日语|马来语|印尼语")
        return {"越南语": "vi", "英文": "en", "英语": "en", "中文": "zh", "泰语": "th", "西班牙语": "es", "西语": "es",
                "德语": "de", "日语": "ja", "马来语": "ms", "印尼语": "id"}[x.group(0)] if x else None
    if n == "product_image":
        x = m(r"https?://\S+")
        return x.group(0) if x else ("(会话上传的图片)" if re.search(r"这张图|这个图|图片|原图|这款", t) else None)
    if n == "ratio":
        x = m(r"\b(1:1|3:4|4:3|9:16|16:9|4:5)\b") or m(r"(竖版|横版|方图)")
        return ({"竖版": "9:16", "横版": "16:9", "方图": "1:1"}.get(x.group(0)) or x.group(0)) if x else None
    if n == "video_duration":
        x = m(r"(\d+)\s*(秒|s\b)", re.I)
        return x.group(1) + "s" if x else None
    if n == "festival":
        x = m(r"黑五|黑色星期五|圣诞|双11|双十一|双12|618|情人节|母亲节|开斋节|万圣节|春节|返校季")
        return x.group(0) if x else None
    if n == "discount":
        x = m(r"\d+\s*%\s*off|\d+\s*%|\d+(\.\d+)?\s*折|满\d+减\d+", re.I)
        return x.group(0) if x else None
    if n == "product_type":
        x = m(r"T恤|卫衣|帽子|手机壳|帆布袋|马克杯|抱枕|海报|贴纸", re.I)
        return x.group(0) if x else None
    if n == "color":
        x = m(r"(黑|白|红|蓝|绿|粉|灰|米|黄|紫)色")
        return x.group(0) if x else None
    if n in ("title", "copy_text"):
        x = m(r"[「“\"](.+?)[」”\"]")
        return x.group(1) if x else None
    if n == "style":
        x = m(r"简约|高级感|ins风|复古|可爱|科技|节日", re.I)
        return x.group(0) if x else None
    if n == "dest":
        x = m(r"(发到|到)([\u4e00-\u9fa5]{2,4}?)(运费|的|$)")
        return x.group(2) if x else None
    if n == "video_url":
        x = m(r"https?://\S+\.(mp4|mov)\b", re.I)
        return x.group(0) if x else ("(会话上传的视频)" if re.search(r"这条视频|这个视频|视频", t) else None)
    if n == "reference_image":
        return "(会话上传的参考图)" if re.search(r"参考图|参考这张|照着", t) else None
    if n in ("theme", "scene"):
        x = m(r"(主题|场景)[是为：:]?\s*([\u4e00-\u9fa5]{2,6})")
        return x.group(2) if x else None
    if n == "category":
        x = m(r"(女装|男装|家居|美妆|数码|母婴|玩具)")
        return x.group(0) if x else None
    return None


_rec = ExampleSimilarityRecognizer()


def recognizer() -> ExampleSimilarityRecognizer:
    return _rec
