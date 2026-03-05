"""Reply Agent definition with RAG knowledge, long-term memory, and business tools."""

from __future__ import annotations

import logging
from pathlib import Path

from agno.agent import Agent
from agno.knowledge.knowledge import Knowledge
from agno.models.openai.like import OpenAILike
from agno.vectordb.lancedb import LanceDb, SearchType
from agno.knowledge.embedder.openai import OpenAIEmbedder

from config import settings
from tools.reply_toolkit import ReplyToolkit

logger = logging.getLogger(__name__)

INSTRUCTIONS = [
    "你是一个专业的闲鱼/电商平台卖家客服助手。",
    "你的目标是代替卖家与买家沟通，促成交易、处理售后。",
    "",
    "## 核心原则",
    "1. 回复要自然、友好、简洁，像真人卖家一样聊天，不要有AI味",
    "2. 每次回复控制在1-3句话，不要长篇大论",
    "3. 遇到议价要有策略：先用 classify_intent 判断意图，再用 evaluate_bargain 评估出价",
    "4. 涉及商品详情先搜索知识库，确保回答准确",
    "5. 遇到敏感关键词（投诉、举报等）用 check_escalation 检查，必要时建议转人工",
    "6. 记住买家的偏好和历史对话，提供个性化服务",
    "",
    "## 回复风格",
    "- 用口语化的中文，适当用「～」「😊」等让对话更亲切",
    "- 不要用「尊敬的客户」「您好，感谢您的咨询」这类客服腔",
    "- 可以用「亲」「朋友」等称呼",
    "- 回复结尾不要用句号，用「～」或表情代替",
    "",
    "## 工作流程",
    "1. 收到买家消息后，先用 classify_intent 判断意图",
    "2. 如果是商品咨询，搜索知识库获取准确信息",
    "3. 如果是议价，用 evaluate_bargain 评估并给出策略性回复",
    "4. 用 check_escalation 检查是否需要人工介入",
    "5. 最后用 format_reply 美化回复",
    "6. 只返回最终要发送给买家的回复文本，不要返回工具调用过程",
]


def create_reply_agent(db=None) -> Agent:
    """Create the reply agent with knowledge, memory, and tools."""

    model = OpenAILike(
        id=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
    )

    embedder = OpenAIEmbedder(
        id=settings.embedding_model,
        api_key=settings.effective_embedding_api_key,
        base_url=settings.embedding_base_url,
        dimensions=settings.embedding_dimensions,
    )

    knowledge = Knowledge(
        vector_db=LanceDb(
            uri=settings.lancedb_uri,
            table_name="reply_knowledge",
            search_type=SearchType.hybrid,
            embedder=embedder,
        ),
    )

    reply_toolkit = ReplyToolkit(
        min_price_ratio=settings.min_price_ratio,
        escalate_keywords=settings.escalate_keywords_list,
    )

    agent = Agent(
        agent_id="reply-agent",
        name="ReplyAgent",
        role="闲鱼/电商平台智能客服",
        model=model,
        tools=[reply_toolkit],
        instructions=INSTRUCTIONS,
        knowledge=knowledge,
        search_knowledge=True,
        db=db,
        enable_agentic_memory=True,
        add_history_to_context=True,
        num_history_runs=10,
        markdown=False,
        show_tool_calls=False,
    )

    return agent


def load_knowledge(agent: Agent) -> int:
    """Load documents from knowledge_docs/ into the agent's knowledge base.

    Uses Knowledge.insert() with path= for directories and text_content= for files.

    Returns:
        Number of documents loaded.
    """
    docs_dir = Path(settings.knowledge_docs_dir)
    if not docs_dir.exists():
        logger.warning("Knowledge docs directory not found: %s", docs_dir)
        return 0

    if agent.knowledge is None:
        logger.warning("Agent has no knowledge base configured")
        return 0

    md_files = sorted(docs_dir.rglob("*.md"))
    if not md_files:
        logger.info("No knowledge documents found in %s", docs_dir)
        return 0

    count = 0
    for md_file in md_files:
        content = md_file.read_text(encoding="utf-8")
        if not content.strip():
            continue
        rel_path = md_file.relative_to(docs_dir)
        category = rel_path.parts[0] if len(rel_path.parts) > 1 else "general"
        try:
            agent.knowledge.insert(
                name=md_file.stem,
                text_content=content,
                metadata={"category": category, "source": str(rel_path)},
                upsert=True,
            )
            count += 1
        except Exception as e:
            logger.warning("Failed to load %s: %s", rel_path, e)

    logger.info("Knowledge base loaded: %d documents", count)
    return count
