"""Reply Agent definition with Skills, MCP tools, RAG knowledge, memory, and business tools."""

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


def _build_skills():
    """Load skills from the skills directory if available."""
    skills_dir = Path(settings.skills_dir)
    if not skills_dir.exists() or not any(skills_dir.iterdir()):
        logger.info("No skills directory or empty: %s", skills_dir)
        return None

    try:
        from agno.skills import Skills, LocalSkills
        loader = LocalSkills(path=str(skills_dir), validate=False)
        skills = Skills(loaders=[loader])
        names = skills.get_skill_names()
        logger.info("Loaded %d skills: %s", len(names), ", ".join(names))
        return skills
    except Exception as e:
        logger.warning("Failed to load skills: %s", e)
        return None


def _build_mcp_tools() -> list:
    """Build MCP tool instances from config. Returns list of MCPTools objects."""
    server_configs = settings.mcp_server_list
    if not server_configs:
        return []

    try:
        from agno.tools.mcp import MCPTools
    except ImportError:
        logger.warning("MCP support not available (pip install mcp)")
        return []

    mcp_tools = []
    for cfg in server_configs:
        name = cfg.get("name", "mcp")
        transport = cfg.get("transport", "sse")
        try:
            if transport == "stdio":
                command = cfg.get("command", "")
                args = cfg.get("args", [])
                env = cfg.get("env", None)
                if not command:
                    logger.warning("MCP server '%s': stdio transport requires 'command'", name)
                    continue
                full_command = " ".join([command] + args) if args else command
                tool = MCPTools(
                    command=full_command,
                    env=env,
                    transport="stdio",
                    tool_name_prefix=name,
                )
            else:
                url = cfg.get("url", "")
                if not url:
                    logger.warning("MCP server '%s': sse/http transport requires 'url'", name)
                    continue
                tool = MCPTools(
                    url=url,
                    transport=transport,
                    tool_name_prefix=name,
                )
            mcp_tools.append(tool)
            logger.info("MCP server configured: %s (%s)", name, transport)
        except Exception as e:
            logger.warning("Failed to configure MCP server '%s': %s", name, e)

    return mcp_tools


def create_reply_agent(db=None) -> Agent:
    """Create the reply agent with knowledge, memory, skills, MCP tools, and business tools."""

    extra_body = {}
    if "k2.5" in settings.llm_model.lower() or "k2" in settings.llm_model.lower():
        extra_body["thinking"] = {"type": "disabled"}

    model = OpenAILike(
        id=settings.llm_model,
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        extra_body=extra_body or None,
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

    tools: list = [reply_toolkit]

    mcp_tools = _build_mcp_tools()
    if mcp_tools:
        tools.extend(mcp_tools)

    skills = _build_skills()

    agent = Agent(
        id="reply-agent",
        name="ReplyAgent",
        description="闲鱼/电商平台智能客服",
        model=model,
        tools=tools,
        skills=skills,
        instructions=INSTRUCTIONS,
        knowledge=knowledge,
        search_knowledge=True,
        db=db,
        enable_agentic_memory=True,
        add_history_to_context=True,
        num_history_runs=10,
        markdown=False,
    )

    return agent


def load_knowledge(agent: Agent) -> int:
    """Load documents from knowledge_docs/ into the agent's knowledge base.

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


def get_agent_capabilities() -> dict:
    """Return a summary of current agent capabilities for the admin API."""
    skills_dir = Path(settings.skills_dir)
    skill_names = []
    if skills_dir.exists():
        for d in sorted(skills_dir.iterdir()):
            if d.is_dir() and (d / "SKILL.md").exists():
                skill_names.append(d.name)

    mcp_servers = []
    for cfg in settings.mcp_server_list:
        mcp_servers.append({
            "name": cfg.get("name", "unknown"),
            "transport": cfg.get("transport", "sse"),
            "url": cfg.get("url", ""),
            "command": cfg.get("command", ""),
        })

    docs_dir = Path(settings.knowledge_docs_dir)
    kb_docs = sorted(str(f.relative_to(docs_dir)) for f in docs_dir.rglob("*.md")) if docs_dir.exists() else []

    return {
        "skills": skill_names,
        "mcp_servers": mcp_servers,
        "knowledge_docs": kb_docs,
        "tools": ["classify_intent", "evaluate_bargain", "check_escalation", "format_reply"],
        "memory_enabled": True,
        "search_knowledge": True,
    }
