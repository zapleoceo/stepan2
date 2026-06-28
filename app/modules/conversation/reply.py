"""ReplyService — turn a thread's dialog into a Decision, then queue the reply.

LLM stays behind LLMPort (injected, so tests use a fake) and all DB access goes through
BranchScoped repos. No branch_id filtering by hand; no sending here — only enqueue."""
from __future__ import annotations

from sqlmodel.ext.asyncio.session import AsyncSession

from app.adapters.db.models import Branch, Outbox
from app.modules.knowledge.service import KnowledgeService
from app.ports.llm import LLMPort

from .decision import Decision, parse_decision
from .prompt import build_messages
from .repository import MessageRepo, OutboxRepo, ThreadRepo


class ReplyService:
    """Decide and enqueue the agent's reply for one branch's thread."""

    def __init__(
        self,
        session: AsyncSession,
        branch_id: int,
        llm: LLMPort,
        knowledge: KnowledgeService,
    ) -> None:
        self.session = session
        self.branch_id = branch_id
        self.llm = llm
        self.knowledge = knowledge
        self.threads = ThreadRepo(session, branch_id)
        self.messages = MessageRepo(session, branch_id)
        self.outbox = OutboxRepo(session, branch_id)

    async def decide(self, thread_id: int) -> Decision | None:
        """Run the model over the thread; None if the thread is foreign or has no dialog."""
        thread = await self.threads.by_id(thread_id)
        if thread is None:
            return None
        dialog = await self.messages.dialog(thread_id)
        if not dialog:
            return None

        context = await self.knowledge.knowledge_context(thread.product_slug)
        messages = build_messages(context, dialog, await self._lang())
        raw, _meta = await self.llm.chat(messages, require_json_schema=True)
        return parse_decision(raw)

    async def _lang(self) -> str:
        """Branch reply language; Branch is the tenant root, so read it by its own id."""
        branch = await self.session.get(Branch, self.branch_id)
        return branch.lang if branch is not None else "id"

    async def enqueue_reply(self, thread_id: int, decision: Decision) -> Outbox | None:
        """Queue the decided reply on the branch's outbox; None for a foreign thread."""
        thread = await self.threads.by_id(thread_id)
        if thread is None:
            return None
        return await self.outbox.add(
            Outbox(branch_id=self.branch_id, thread_id=thread_id, text=decision.reply)
        )
