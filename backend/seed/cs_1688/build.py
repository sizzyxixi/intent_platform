"""从《意图工具调用映射维护表 v1》生成 cs_1688.json（1688 智能客服空间的意图 / 槽位 / 工具绑定 / 知识库数据）。

用法：python seed/cs_1688/build.py   （需要 openpyxl；生成结果已提交，平时无需重跑）
文档里的内容原样进入 doc 字段；codes、描述、例句、槽位、工具映射是按文档整理的部分，写在下面。
"""
import json
from pathlib import Path

import openpyxl

HERE = Path(__file__).resolve().parent
wb = openpyxl.load_workbook(HERE / "意图工具调用映射维护表_v1.xlsx", data_only=True)


def rows(name):
    r = [[("" if c is None else str(c).strip()) for c in row] for row in wb[name].iter_rows(values_only=True)]
    r = [x for x in r if any(x)]
    return [dict(zip(r[0], x)) for x in r[1:]]


notes = {}
for row in wb["维护说明"].iter_rows(values_only=True):
    if row[0] and row[1]:
        notes[str(row[0]).strip()] = str(row[1]).strip()
doc = {"source": "意图工具调用映射维护表_v1.xlsx", "version": notes.get("版本", ""), "notes": notes,
       "tools": rows("工具字典"), "mapping": rows("意图工具映射"), "rules": rows("置信度与并行"),
       "ledger": rows("台账意图对照"), "changelog": rows("变更日志")}
P = "xclaw_platform_alibaba_product_get"
PRICE = "xclaw_platform_alibaba_trade_order_modifyorderprice_call"
TRACE = "xclaw_platform_alibaba_trade_getlogisticstraceinfo_sellerview_v1_call"
REFUND = "xclaw_platform_alibaba_trade_refund_sellerview_query"
tool_map = {
  "get_stock_eta": {"tools": [P], "confidence": "中", "why": "1688 开放平台「获取商品」(platform alibaba product get) 返回商品及 SKU 的可售数量；没有预计到货日 / 截单发货规则字段，这两项仍缺接口"},
  "get_price_quote": {"tools": [P], "confidence": "中", "why": "同一「获取商品」返回区间（阶梯）报价；包邮门槛、活动价不在返回里，报价时按数量对应档位"},
  "apply_change_price": {"tools": [PRICE], "confidence": "高", "why": "「修改订单价格」(trade-order-modifyorderprice) 直接改 1688 订单价；接口要求运费、改价明细以「分」为单位，需由客服按目标价换算并确认；已标记需确认"},
  "lock_or_hold_stock": {"tools": [], "confidence": "", "why": "目录中没有「占仓 / 锁库存」接口（只有修改库存 product-modifystock、批量改库存，语义不同，不能代替占仓）"},
  "ship_urge_track": {"tools": [TRACE], "confidence": "高", "why": "「获取交易订单的物流跟踪信息（卖家视角）」(trade-getlogisticstraceinfo-sellerview-v1) 按订单号返回运单与物流节点"},
  "aftersale_refund": {"tools": [REFUND], "confidence": "中", "why": "「查询退款单详情-根据订单ID（卖家视角）」(trade-refund-sellerview query) 返回退款进度/状态；同意退货、补寄、换货等操作接口存在但属写操作，本期只绑查询"},
  "get_spec_media": {"tools": [P], "confidence": "中（资质 / 发票低）", "why": "「获取商品」返回商品属性、图片；证照 / 资质、开票类型不在返回里"},
  "get_custom_moq": {"tools": [P], "confidence": "低", "why": "「获取商品」只能给起订量；「查询定制服务」(custom-query-bycate) 需要一二三级类目 ID，无法从对话槽位取得，未绑定；打样费、打样周期无接口"},
  "clarify_slot": {"tools": [], "confidence": "", "why": "Router 动作：由必填槽位追问话术 + 空间「识别设置」中的澄清实现，不是外部工具"},
  "handoff_human": {"tools": [], "confidence": "", "why": "Router 动作：由空间「识别设置」兜底动作（转人工 handoff_human）实现，不是外部工具"},
  "reply_template": {"tools": [], "confidence": "", "why": "Router 动作：无工具事实时的短模板，由 Bot 话术模块实现，不是外部工具"},
}
pm = {
  P: {"shop_id": ["槽位", "shop_id"], "product_id": ["槽位", "offer_id"], "web_site": ["常量", "1688"], "scene": ["常量", "1688"]},
  PRICE: {"shop_id": ["槽位", "shop_id"], "order_id": ["槽位", "order_id"], "carriage": ["槽位", "carriage"], "entry_discounts": ["槽位", "entry_discounts"]},
  TRACE: {"shop_id": ["槽位", "shop_id"], "order_id": ["槽位", "order_id"], "web_site": ["常量", "1688"]},
  REFUND: {"shop_id": ["槽位", "shop_id"], "order_id": ["槽位", "order_id"], "query_type": ["常量", "1"]},
}
slot_defs = [
  ("shop_id", "店铺", "文本", [], "（不向买家追问：由 Bot 按会话所属的 1688 店铺带入 ERP 店铺 ID）"),
  ("offer_id", "商品（链接/商品ID）", "文本", [], "麻烦发一下您在看的商品链接～"),
  ("sku_id", "规格（SKU）", "文本", [], "您要的是哪个规格呢？"),
  ("qty", "数量", "数字", [], "您大概需要多少件？"),
  ("order_id", "订单号", "文本", [], "麻烦发一下订单号～"),
  ("target_price", "目标价", "数字", [], "您期望的价格是多少？"),
  ("custom_type", "定制类型", "文本", [], "您想定制哪方面呢？"),
  ("custom_spec", "尺寸/参考图", "文本", [], "麻烦发一下需要的尺寸或参考图～"),
  ("logo_req", "Logo 需求", "文本", [], "您想印什么 Logo、印在哪个位置？"),
  ("logo_file", "Logo 文件", "图片", [], "方便发一下 Logo 图片吗？"),
  ("sample_qty", "样品数量", "数字", [], "样品需要几件？"),
  ("invoice_type", "发票类型", "文本", [], "您需要开哪种发票？"),
  ("cert_type", "资质类型", "文本", [], "您需要哪类资质或证书？"),
  ("question_type", "问题类型", "文本", [], "您想了解商品的哪方面信息？"),
  ("aftersale_type", "售后类型", "枚举", ["退款", "退货", "换货", "补发"], "您是想退款、退货、换货还是补发？"),
  ("carriage", "改价后运费（分）", "数字", [], "（不向买家追问：客服确认改价后填写，单位为分）"),
  ("entry_discounts", "改价明细（分）", "文本", [], "（不向买家追问：客服按目标价换算后填写，单位为分，负数为减价）"),
]
cats = [("PRE", "售前"), ("MID", "售中"), ("POST", "售后"), ("NONE", "非询盘")]
R, O = True, False
intents = [
 ("库存查询", "B.PRE.STOCK", "PRE", "买家询问某款商品或规格有没有现货、能卖多少、什么时候到货；问「有货吗」「库存多少」「没货什么时候到」算；只问价格不算（→ 询价报价），已下单问发货不算（→ 售中履约）",
  ["这款有现货吗", "黑色的还有货吗", "现在库存有多少", "没货的话什么时候能到"], [("offer_id", R), ("sku_id", O), ("qty", O), ("shop_id", O)]),
 ("信息咨询", "B.PRE.INFO", "PRE", "买家咨询商品参数、材质、毛重、实拍图等商品信息；要实拍、问规格参数算；问库存不算（→ 库存查询），问价不算（→ 询价报价），要资质证书不算（→ 资质合规）",
  ["发几张实拍图看看", "这个是什么材质的", "毛重多少", "详细参数发一下"], [("offer_id", O), ("question_type", O), ("shop_id", O)]),
 ("询价报价", "B.PRE.QUOTE", "PRE", "买家询问单价、阶梯价、包邮门槛或活动价；问「多少钱」「拿多少件什么价」算；拿别家价格比、要求降价不算（→ 比价中）",
  ["这个多少钱一件", "拿500件什么价", "多少件起包邮", "有活动价吗"], [("offer_id", R), ("qty", R), ("sku_id", O), ("shop_id", O)]),
 ("比价中", "B.PRE.COMPARE", "PRE", "买家拿其他店铺或渠道的价格作对比，要求给底价或议价；说「别家更便宜」「能不能便宜点」算；只问单价不算（→ 询价报价），已下单要求改价不算（→ 售中改价改单）",
  ["别家比你便宜", "能再便宜点吗", "给个最低价", "量大能优惠多少"], [("offer_id", R), ("qty", R), ("target_price", O), ("order_id", O), ("carriage", O), ("entry_discounts", O), ("shop_id", O)]),
 ("明确采购", "B.PRE.BUY", "PRE", "买家已明确要买，确认数量、规格后准备下单；说「我要500件」「怎么拍」「现在下单」算；还在问价、比价不算（→ 询价报价 / 比价中）",
  ["我要300件黑色的", "现在拍可以吗", "确定要了怎么下单", "今天拍今天能发吗"], [("offer_id", R), ("qty", R), ("sku_id", O), ("shop_id", O)]),
 ("交期", "B.PRE.LEADTIME", "PRE", "买家询问几天发货、截单时间或急单能否当天发（售前/售中均可能出现：没有订单按商品查发货规则，已有订单按订单查物流）；问「几天发货」「几点前付款当天发」算；问有没有货不算（→ 库存查询）",
  ["几天能发货", "今天下单今天发吗", "急用，最快什么时候发", "几点前付款当天发"], [("offer_id", O), ("order_id", O), ("shop_id", O)]),
 ("尺寸定制", "B.PRE.CUSTOM_SIZE", "PRE", "买家想按自己的尺寸或图纸定制，问能否改尺寸、起订量和打样；说「能做这个尺寸吗」「按图定做」算；印 Logo 不算（→ logo/知识产权），只要样品不算（→ 样品）",
  ["能按我的尺寸做吗", "可以做大一号的吗", "按图纸定制起订多少", "改尺寸要打样吗"], [("custom_type", R), ("custom_spec", R), ("offer_id", O), ("qty", O), ("shop_id", O)]),
 ("logo/知识产权", "B.PRE.LOGO", "PRE", "买家想在商品或包装上印 Logo、贴牌，或问印品牌图案能不能做；说「能印 logo 吗」「可以贴牌吗」算；只改尺寸不算（→ 尺寸定制）",
  ["能印我们的logo吗", "可以贴牌吗", "印logo起订多少", "包装上能印字吗"], [("logo_req", R), ("logo_file", O), ("offer_id", O), ("qty", O), ("shop_id", O)]),
 ("样品", "B.PRE.SAMPLE", "PRE", "买家想先拿样品看质量，询问样品价、运费和寄样周期；说「能寄样吗」「先拿个样」算；直接下大货不算（→ 明确采购）",
  ["能先寄个样品吗", "样品怎么收费", "先拿两件看看质量", "样品多久能寄出"], [("sku_id", R), ("sample_qty", R), ("offer_id", O), ("shop_id", O)]),
 ("发票对公", "B.PRE.INVOICE", "PRE", "买家询问能否开发票、开哪种发票、开票要什么资料或能否对公付款；说「能开专票吗」「可以对公转账吗」算；要资质证书不算（→ 资质合规）",
  ["能开专票吗", "可以对公转账吗", "开票需要提供什么", "能开普票吗"], [("invoice_type", R), ("offer_id", O), ("shop_id", O)]),
 ("资质合规", "B.PRE.CERT", "PRE", "买家索要商品资质、检测报告、证书等合规文件；说「有检测报告吗」「有什么证书」算；问开发票不算（→ 发票对公）",
  ["有检测报告吗", "有什么资质证书", "能发一下质检报告吗", "这个有认证吗"], [("cert_type", R), ("offer_id", O), ("shop_id", O)]),
 ("开场领券", "B.PRE.COUPON", "PRE", "买家开场只问优惠券、怎么领券、有没有优惠，还没说采购需求；说「有优惠券吗」「怎么领券」算；已说明数量问价不算（→ 询价报价）",
  ["有优惠券吗", "怎么领券", "新客有优惠吗", "有没有满减"], [("offer_id", O), ("qty", O), ("shop_id", O)]),
 ("售中改价改单", "B.MID.CHANGE_ORDER", "MID", "买家已拍下或已下单，要求修改订单价格、数量或运费；说「拍好了帮我改价」「数量改成 200」算；还没下单的议价不算（→ 比价中）",
  ["拍好了帮我改下价", "订单数量改成200件", "改好价我就付款", "运费能帮我改一下吗"], [("order_id", R), ("target_price", O), ("qty", O), ("offer_id", O), ("carriage", O), ("entry_discounts", O), ("shop_id", O)]),
 ("售中履约", "B.MID.FULFILL", "MID", "买家已下单，询问发货时间、催发货或查询运单号与物流轨迹；有订单问「什么时候发」「单号多少」「物流到哪了」算；没下单问几天发不算（→ 交期），要退换货不算（→ 售后处理）",
  ["我的订单什么时候发", "发货了吗，单号多少", "物流怎么一直没动", "帮我催一下发货"], [("order_id", R), ("offer_id", O), ("shop_id", O)]),
 ("售后处理", "B.POST.AFTERSALE", "POST", "买家收货后要退款、退货、换货、补发，或查询售后进度；说「少发了」「要退货」「退款到哪了」算；未发货催发不算（→ 售中履约）",
  ["少发了两件给我补一下", "质量有问题要退货", "退款什么时候到账", "发错颜色了能换吗"], [("order_id", R), ("aftersale_type", R), ("offer_id", O), ("shop_id", O)]),
 ("无买家意图/空会话", "B.NONE.EMPTY", "NONE", "会话里没有明确的采购需求，只有「在吗」「你好」、表情或只发链接不说话；先确认买家在看哪款，不调成交类工具；只要提出了具体问题就不算",
  ["在吗", "你好", "在不在", "老板"], []),
 ("其他", "B.NONE.OTHER", "NONE", "不属于以上任何意图、与采购无关或无法判断的消息；只做澄清或转人工，禁止调用改价等成交类工具；能归到具体意图的不算",
  ["你们几点下班", "帮我转给别人", "我先问问别的", "说个别的事"], []),
]
bucket_tools = {}  # bucket -> ordered xclaw tools by doc main/aux order
order = {}
out_int = []
for name, code, cat, desc, ex, slots in intents:
    row = next(r for r in doc["mapping"] if r["intent_bucket"] == name)
    ids = []
    for col in ("主工具(按序)", "辅工具"):
        for part in row[col].replace("；", ";").split(";"):
            t = part.strip().split("(")[0].strip()
            if t and t != "—":
                ids.append(t)
    binds = []
    for t in ids:
        for x in tool_map[t]["tools"]:
            if x not in binds:
                binds.append(x)
    out_int.append({"bucket": name, "code": code, "category": cat, "name": name, "description": desc, "examples": ex,
                    "slots": [{"name": s, "required": r} for s, r in slots], "doc_tools": ids,
                    "tools": [{"tool": x, "param_map": {k: {"source": v[0], "value": v[1]} for k, v in pm[x].items()}} for x in binds]})
    have = {s for s, _ in slots}
    for b in out_int[-1]["tools"]:
        for k, v in b["param_map"].items():
            assert v["source"] != "槽位" or v["value"] in have, (code, b["tool"], v)
spec = {"doc": doc, "tool_map": tool_map, "confirm_tools": [PRICE, REFUND], "slots": [dict(zip(("name", "display_name", "type", "options", "default_ask"), s)) for s in slot_defs],
        "space": {"levels": ["阶段", "意图"]}, "categories": [{"code": c, "name": n} for c, n in cats], "intents": out_int,
        "owner": "1688客服（映射维护表 v1）"}
json.dump(spec, open(HERE / 'cs_1688.json', 'w'), ensure_ascii=False, indent=1)
for i in out_int: print(i["code"], i["doc_tools"], [b["tool"][6:40] for b in i["tools"]])
