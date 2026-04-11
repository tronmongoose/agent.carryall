"""
Authority Runtime - LangGraph Integration

This module provides integration between Authority Runtime and LangGraph's
graph-based state machine architecture.

Key Features:
- AuthorityState: Extended TypedDict for graph state with envelope tracking
- authority_node: Wrapper that creates child envelopes before tool execution
- create_authority_graph: Factory for creating authority-enabled LangGraph agents

Architecture:
    User Request → Parent Envelope → LangGraph Graph → Authority Node
    → LLM Compiler → Child Envelope → EnforcedTool → Audit Trail

Example:
    ```python
    from authority_runtime import generate_key_pair, create_authority_graph
    from authority_runtime.langgraph import AuthorityState

    # Create signing keys
    private_key, public_key = generate_key_pair()

    # Define tools
    tools = [secure_read, secure_write]

    # Create authority-enabled graph
    graph = create_authority_graph(
        agent_id="my-agent",
        provider="openai",
        root_policy_id="policy-1",
        initial_scopes=["read:user", "write:user"],
        initial_context_fields=["email", "name", "user_id"],
        tools=tools,
        private_key=private_key,
        public_key=public_key,
        model="gpt-4o-mini"
    )

    # Execute with automatic authority narrowing
    result = graph.invoke({"messages": [("user", "Find user by email")]})
    ```
"""

from typing import Annotated, TypedDict, Sequence, List, Optional, Dict, Any
from langgraph.graph import StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition
from langchain_core.messages import BaseMessage, HumanMessage

from .types import (
    AuthorityEnvelope,
    Skill,
    SkillParameters,
    Authority,
    Context,
    ExecutionConfig,
)
from .envelope import create_envelope
from .compiler import OpenAICompiler, AnthropicCompiler
from .enforce import EnforcedTool
from .storage import EnvelopeStore


class AuthorityState(TypedDict):
    """
    Extended state for LangGraph with Authority Runtime support.

    Fields:
        messages: Conversation history (LangGraph standard)
        parent_envelope: Current parent envelope (authority context)
        envelope_chain: List of all envelopes created in this workflow
        step_number: Current step in the workflow
    """
    messages: Annotated[Sequence[BaseMessage], add_messages]
    parent_envelope: Optional[AuthorityEnvelope]
    envelope_chain: List[AuthorityEnvelope]
    step_number: int


def create_authority_node(
    model: Any,
    tools: List[EnforcedTool],
    private_key: str,
    public_key: str,
    provider: str,
    agent_id: str,
    root_policy_id: str,
    use_compiler: bool = False,
    compiler_model: str = "gpt-4o-mini",
    db_path: Optional[str] = None,
):
    """
    Create a node function that integrates authority narrowing into LangGraph.

    This node:
    1. Receives current state with parent envelope
    2. (Optional) Calls LLM compiler to select minimal permissions
    3. Creates child envelope with narrowed authority
    4. Binds tools with child envelope
    5. Executes agent with narrowed context
    6. Saves envelopes to database (if db_path provided)

    Args:
        model: LangChain LLM model (ChatOpenAI, ChatAnthropic, etc.)
        tools: List of EnforcedTool instances
        private_key: Ed25519 private key for signing envelopes
        public_key: Ed25519 public key for verification
        provider: Provider name ("openai", "claude", "gemini", "custom")
        agent_id: Agent identifier
        root_policy_id: Root policy identifier
        use_compiler: If True, use LLM compiler to narrow authority
        compiler_model: Model for LLM compiler (if use_compiler=True)
        db_path: Optional path to SQLite database for persistence

    Returns:
        Node function compatible with LangGraph StateGraph
    """
    # Initialize envelope store if database path provided
    store = EnvelopeStore(db_path) if db_path else None

    # Initialize compiler if enabled
    compiler = None
    if use_compiler:
        if "openai" in compiler_model or "gpt" in compiler_model:
            compiler = OpenAICompiler(model=compiler_model)
        else:
            compiler = AnthropicCompiler(model=compiler_model)

    def authority_node(state: AuthorityState) -> Dict[str, Any]:
        """
        Execute agent with authority narrowing.

        Args:
            state: Current graph state

        Returns:
            Updated state with new messages, envelope chain, and step number
        """
        parent_envelope = state.get("parent_envelope")
        step_number = state.get("step_number", 0)
        envelope_chain = state.get("envelope_chain", [])

        # If no parent envelope, this is the first step - should have been created
        if parent_envelope is None:
            raise ValueError(
                "No parent envelope in state. Use create_authority_graph() "
                "or manually create parent envelope before invoking graph."
            )

        # Get last user message
        messages = state["messages"]
        last_user_message = None
        for msg in reversed(messages):
            if isinstance(msg, HumanMessage):
                last_user_message = msg.content
                break

        if not last_user_message:
            last_user_message = "Continue the task"

        # Create child envelope (narrowed or same as parent)
        child_envelope = parent_envelope

        if use_compiler and compiler:
            # Use LLM compiler to select minimal permissions
            from .compiler import compile_policy
            import asyncio

            # Convert EnforcedTool to Skill objects for compiler
            available_skills = [
                Skill(
                    id=tool.name,
                    name=tool.name,
                    tool=tool.description or tool.name,
                    parameters=SkillParameters(allowed=[], constraints={})
                )
                for tool in tools
            ]

            # Run compiler (sync wrapper for async function)
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

            policy_result = loop.run_until_complete(
                compile_policy(
                    parent_envelope=parent_envelope,
                    user_request=last_user_message,
                    available_skills=available_skills,
                    compiler=compiler
                )
            )

            # Create narrowed envelope based on compiler decision
            selected_skill = policy_result.selected_skill

            child_envelope = create_envelope(
                agent_id=agent_id,
                provider=provider,
                step_number=step_number + 1,
                root_policy_id=root_policy_id,
                parent_envelope_id=parent_envelope.envelope_id,
                skill=selected_skill,
                authority=Authority(
                    scopes=policy_result.required_scopes,
                    resources=parent_envelope.authority.resources,
                ),
                context=Context(
                    included=policy_result.required_context_fields,
                    excluded=parent_envelope.context.excluded,
                ),
                execution=ExecutionConfig(provider_config={}),
                private_key=private_key,
                ttl_seconds=min(parent_envelope.ttl_seconds, 300),
                decision_context=policy_result.decision_context,
            )
        else:
            # No compiler - create child with same authority as parent
            child_envelope = create_envelope(
                agent_id=agent_id,
                provider=provider,
                step_number=step_number + 1,
                root_policy_id=root_policy_id,
                parent_envelope_id=parent_envelope.envelope_id,
                skill=parent_envelope.skill,
                authority=parent_envelope.authority,
                context=parent_envelope.context,
                execution=parent_envelope.execution,
                private_key=private_key,
                ttl_seconds=parent_envelope.ttl_seconds,
            )

        # Save envelope to database
        if store:
            store.save_envelope(child_envelope)

        # Update envelope chain
        envelope_chain.append(child_envelope)

        # Bind tools to model (LangGraph pattern)
        model_with_tools = model.bind_tools(
            [tool.to_langchain_tool() for tool in tools]
        )

        # Invoke model
        response = model_with_tools.invoke(messages)

        # Return updated state
        return {
            "messages": [response],
            "parent_envelope": child_envelope,  # Child becomes new parent
            "envelope_chain": envelope_chain,
            "step_number": step_number + 1,
        }

    return authority_node


def create_authority_graph(
    agent_id: str,
    provider: str,
    root_policy_id: str,
    initial_scopes: List[str],
    initial_context_fields: List[str],
    tools: List[EnforcedTool],
    private_key: str,
    public_key: str,
    model: str = "gpt-4o-mini",
    use_compiler: bool = False,
    compiler_model: str = "gpt-4o-mini",
    db_path: Optional[str] = None,
    ttl_seconds: int = 600,
) -> StateGraph:
    """
    Create a LangGraph graph with Authority Runtime integration.

    This is the primary entry point for using Authority Runtime with LangGraph.

    Args:
        agent_id: Agent identifier
        provider: LLM provider ("openai", "claude", "gemini", "custom")
        root_policy_id: Root policy identifier
        initial_scopes: Initial permission scopes for root envelope
        initial_context_fields: Initial context fields for root envelope
        tools: List of EnforcedTool instances
        private_key: Ed25519 private key for signing
        public_key: Ed25519 public key for verification
        model: LLM model name (default: "gpt-4o-mini")
        use_compiler: If True, use LLM compiler for authority narrowing
        compiler_model: Model for LLM compiler
        db_path: Optional SQLite database path for persistence
        ttl_seconds: TTL for root envelope (default: 600 seconds)

    Returns:
        Compiled LangGraph StateGraph ready for invoke()

    Example:
        ```python
        graph = create_authority_graph(
            agent_id="my-agent",
            provider="openai",
            root_policy_id="policy-1",
            initial_scopes=["read:user", "write:user"],
            initial_context_fields=["email", "name"],
            tools=[secure_read, secure_write],
            private_key=private_key,
            public_key=public_key,
            use_compiler=True,
            db_path="./authority.db"
        )

        result = graph.invoke({
            "messages": [("user", "Find user by email")],
            "parent_envelope": None,
            "envelope_chain": [],
            "step_number": 0,
        })
        ```
    """
    # Create LLM model (lazy imports to avoid dependency issues)
    if provider == "openai" or "gpt" in model:
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model=model, temperature=0)
    elif provider == "claude" or "claude" in model:
        from langchain_anthropic import ChatAnthropic
        llm = ChatAnthropic(model=model, temperature=0)
    else:
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(model=model, temperature=0)  # Default to OpenAI

    # Create root envelope
    root_skill = Skill(
        id="root",
        name="root_authority",
        tool="Full agent authority",
        parameters=SkillParameters(allowed=[], constraints={})
    )

    root_envelope = create_envelope(
        agent_id=agent_id,
        provider=provider,
        step_number=0,
        root_policy_id=root_policy_id,
        skill=root_skill,
        authority=Authority(
            scopes=initial_scopes,
            resources=["*"],
        ),
        context=Context(
            included=initial_context_fields,
            excluded=[],
        ),
        execution=ExecutionConfig(provider_config={}),
        private_key=private_key,
        ttl_seconds=ttl_seconds,
    )

    # Save root envelope to database
    if db_path:
        store = EnvelopeStore(db_path)
        store.save_envelope(root_envelope)

    # Create authority node
    authority_agent = create_authority_node(
        model=llm,
        tools=tools,
        private_key=private_key,
        public_key=public_key,
        provider=provider,
        agent_id=agent_id,
        root_policy_id=root_policy_id,
        use_compiler=use_compiler,
        compiler_model=compiler_model,
        db_path=db_path,
    )

    # Build LangGraph graph
    workflow = StateGraph(AuthorityState)

    # Add nodes
    workflow.add_node("agent", authority_agent)
    workflow.add_node("tools", ToolNode(
        [tool.to_langchain_tool() for tool in tools]
    ))

    # Set entry point
    workflow.set_entry_point("agent")

    # Add conditional edges (standard LangGraph pattern)
    workflow.add_conditional_edges(
        "agent",
        tools_condition,
    )

    # Tools route back to agent
    workflow.add_edge("tools", "agent")

    # Compile graph with root envelope as initial state
    compiled = workflow.compile()

    # Store root envelope in compiled graph metadata
    compiled.root_envelope = root_envelope

    return compiled
