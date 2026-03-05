"""Business tools for the Reply Agent.

Provides intent classification, bargaining logic, escalation detection,
and reply formatting as callable tools for the Agno Agent.
"""

from __future__ import annotations

import json
import logging
from typing import Optional

from agno.tools import Toolkit

logger = logging.getLogger(__name__)


class ReplyToolkit(Toolkit):
    """Toolkit for e-commerce auto-reply business logic."""

    def __init__(
        self,
        min_price_ratio: float = 0.8,
        escalate_keywords: Optional[list[str]] = None,
    ):
        super().__init__(name="reply_tools")
        self.min_price_ratio = min_price_ratio
        self.escalate_keywords = escalate_keywords or [
            "投诉", "举报", "消协", "工商", "律师", "12315",
        ]
        self.register(self.classify_intent)
        self.register(self.evaluate_bargain)
        self.register(self.check_escalation)
        self.register(self.format_reply)

    def classify_intent(self, buyer_message: str) -> str:
        """根据买家消息判断意图类别。

        Args:
            buyer_message: 买家发送的消息文本

        Returns:
            JSON string with intent and confidence, e.g.:
            {"intent": "bargain", "confidence": "high", "hint": "买家在议价"}
        """
        msg = buyer_message.lower()

        price_keywords = ["便宜", "优惠", "打折", "少点", "最低", "包邮", "减", "降", "砍"]
        if any(k in msg for k in price_keywords):
            return json.dumps({"intent": "bargain", "confidence": "high",
                               "hint": "买家在议价，参考议价话术策略回复"}, ensure_ascii=False)

        greeting_keywords = ["在吗", "你好", "hi", "hello", "亲", "在不在", "嗨"]
        if any(k in msg for k in greeting_keywords):
            return json.dumps({"intent": "greeting", "confidence": "high",
                               "hint": "买家打招呼，用友好的问候回复"}, ensure_ascii=False)

        inquiry_keywords = ["怎么样", "成色", "几新", "有没有", "能不能", "什么时候", "多久",
                            "发货", "快递", "包邮", "正品", "保修", "配件", "电池"]
        if any(k in msg for k in inquiry_keywords):
            return json.dumps({"intent": "inquiry", "confidence": "high",
                               "hint": "买家在咨询商品详情，搜索知识库回答"}, ensure_ascii=False)

        aftersales_keywords = ["退", "换", "坏了", "问题", "不对", "损坏", "碎", "裂",
                               "不能用", "故障", "质量", "假的", "不满意"]
        if any(k in msg for k in aftersales_keywords):
            return json.dumps({"intent": "after_sales", "confidence": "high",
                               "hint": "买家反馈售后问题，参考售后话术处理"}, ensure_ascii=False)

        buy_keywords = ["拍了", "下单", "付款", "买了", "要了", "我要"]
        if any(k in msg for k in buy_keywords):
            return json.dumps({"intent": "purchase", "confidence": "high",
                               "hint": "买家表示要购买，确认订单信息"}, ensure_ascii=False)

        return json.dumps({"intent": "general", "confidence": "medium",
                           "hint": "无法确定具体意图，用友好的方式回复"}, ensure_ascii=False)

    def evaluate_bargain(self, original_price: float, offered_price: float) -> str:
        """评估买家的出价是否可以接受，给出议价建议。

        Args:
            original_price: 商品原始标价（元）
            offered_price: 买家出价（元）

        Returns:
            JSON string with evaluation result and suggested counter-offer
        """
        if original_price <= 0:
            return json.dumps({"error": "原始价格无效"}, ensure_ascii=False)

        ratio = offered_price / original_price
        discount_pct = (1 - ratio) * 100

        if ratio >= 0.95:
            return json.dumps({
                "acceptable": True,
                "strategy": "accept",
                "suggestion": f"可以接受，买家只砍了{discount_pct:.0f}%，爽快成交",
                "counter_price": offered_price,
            }, ensure_ascii=False)

        if ratio >= 0.9:
            counter = round(original_price * 0.93, 0)
            return json.dumps({
                "acceptable": True,
                "strategy": "slight_counter",
                "suggestion": f"买家砍了{discount_pct:.0f}%，可以稍微还一下价到{counter}元",
                "counter_price": counter,
            }, ensure_ascii=False)

        if ratio >= self.min_price_ratio:
            counter = round(original_price * 0.88, 0)
            return json.dumps({
                "acceptable": True,
                "strategy": "counter",
                "suggestion": f"买家砍了{discount_pct:.0f}%，还价到{counter}元，强调商品价值",
                "counter_price": counter,
            }, ensure_ascii=False)

        return json.dumps({
            "acceptable": False,
            "strategy": "decline",
            "suggestion": f"买家砍了{discount_pct:.0f}%，超出底线，委婉拒绝并说明理由",
            "counter_price": round(original_price * self.min_price_ratio, 0),
        }, ensure_ascii=False)

    def check_escalation(self, buyer_message: str) -> str:
        """检查买家消息是否包含需要人工介入的敏感关键词。

        Args:
            buyer_message: 买家发送的消息文本

        Returns:
            JSON string indicating whether human escalation is needed
        """
        triggered = [kw for kw in self.escalate_keywords if kw in buyer_message]
        if triggered:
            return json.dumps({
                "needs_escalation": True,
                "triggered_keywords": triggered,
                "suggestion": "检测到敏感关键词，建议转人工处理。回复时保持冷静和专业。",
            }, ensure_ascii=False)

        return json.dumps({
            "needs_escalation": False,
            "suggestion": "正常对话，无需人工介入",
        }, ensure_ascii=False)

    def format_reply(self, reply_text: str, add_emoji: bool = True) -> str:
        """格式化回复消息，使其更自然、更像真人。

        Args:
            reply_text: 原始回复文本
            add_emoji: 是否添加表情符号使回复更亲切

        Returns:
            格式化后的回复文本
        """
        text = reply_text.strip()
        if not text:
            return "亲，在的～有什么可以帮您？"

        text = text.replace("。\n", "～\n")
        if text.endswith("。"):
            text = text[:-1] + "～"

        if add_emoji and not any(c in text for c in "～😊👍🎉✨❤️"):
            if any(k in text for k in ["感谢", "谢谢", "好的", "可以"]):
                text += " 😊"
            elif any(k in text for k in ["抱歉", "不好意思", "对不起"]):
                text += " 🙏"

        return text
