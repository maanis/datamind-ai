"""
Query service for handling RAG queries.
Orchestrates classification, tool execution, and response generation.
"""

from typing import Dict, Any, Optional, List, Generator

from router.classifier import classify_intent
from router.intent_types import IntentType, ClassificationResult
from tools.semantic_search import semantic_search, format_search_results
from tools.sql_tool import get_workspace_tables, generate_sql, execute_sql_query, format_sql_results
from tools.clarification_tool import generate_clarification, generate_table_selection_prompt, needs_clarification
from llm.factory import get_llm
from services.memory import (
    get_conversation_memory,
    save_conversation_memory,
    format_memory_for_llm,
    format_memory_for_classification
)


class QueryService:
    """
    Service for handling user queries.
    Orchestrates the full query pipeline.
    """
    
    MAX_MEMORY_MESSAGES = 6  # Keep last 6 messages
    MEMORY_FOR_CLASSIFICATION = 2  # Use last 2 messages for classification
    MEMORY_FOR_ANSWER = 1  # Use last 1 assistant reply for follow-ups
    
    def __init__(self, llm_provider: Optional[str] = None):
        """
        Initialize QueryService.
        
        Args:
            llm_provider: Override default LLM provider
        """
        self.llm_provider = llm_provider
    
    def handle_query(
        self,
        workspace_id: str,
        question: str,
        document_id: Optional[str] = None,
        stream: bool = False
    ) -> Dict[str, Any] | Generator[str, None, None]:
        """
        Handle a user query end-to-end.
        
        Args:
            workspace_id: Workspace ID
            question: User's question
            document_id: Optional filter by document
            stream: Whether to stream the response
            
        Returns:
            Response dict or generator if streaming
        """
        # 1. Load workspace tables metadata
        tables_metadata = get_workspace_tables(workspace_id)
        
        # 2. Load conversation memory (last 2-3 messages for context)
        memory = get_conversation_memory(workspace_id)
        memory_for_class = format_memory_for_classification(
            memory, 
            self.MEMORY_FOR_CLASSIFICATION
        )
        
        # 3. Check if clarification needed (rule-based quick check)
        needs_clarify, clarify_reason = needs_clarification(question, tables_metadata)
        
        # 4. Run classifier
        classification = classify_intent(
            question=question,
            memory_context=memory_for_class,
            tables_metadata=tables_metadata,
            llm_provider=self.llm_provider
        )
        
        # 5. Handle based on intent
        if classification.intent == IntentType.GREETING:
            response = self._handle_greeting(question)
            self._save_interaction(workspace_id, question, response, memory)
            return {"answer": response, "intent": "greeting"}
        
        if classification.intent == IntentType.CLARIFICATION or needs_clarify:
            clarification = generate_clarification(
                question=question,
                tables_metadata=tables_metadata,
                ambiguity_reason=clarify_reason or classification.reasoning,
                llm_provider=self.llm_provider
            )
            self._save_interaction(workspace_id, question, clarification, memory)
            return {"answer": clarification, "intent": "clarification", "needs_input": True}
        
        if classification.intent == IntentType.STRUCTURED:
            return self._handle_structured(
                workspace_id=workspace_id,
                question=question,
                tables_metadata=tables_metadata,
                memory=memory,
                stream=stream
            )
        
        if classification.intent == IntentType.HYBRID:
            return self._handle_hybrid(
                workspace_id=workspace_id,
                question=question,
                document_id=document_id,
                tables_metadata=tables_metadata,
                memory=memory,
                stream=stream
            )
        
        # Default: SEMANTIC
        return self._handle_semantic(
            workspace_id=workspace_id,
            question=question,
            document_id=document_id,
            memory=memory,
            stream=stream
        )
    
    def _handle_greeting(self, question: str) -> str:
        """Handle greeting/chitchat."""
        llm = get_llm(provider=self.llm_provider, max_tokens=100)
        
        prompt = f"""You are a helpful AI assistant for a data platform.
The user said: {question}

Respond warmly and briefly (1-2 sentences). 
Offer to help with their data questions."""
        
        return llm.generate(prompt)
    
    def _handle_semantic(
        self,
        workspace_id: str,
        question: str,
        document_id: Optional[str],
        memory: List[Dict[str, str]],
        stream: bool
    ) -> Dict[str, Any] | Generator[str, None, None]:
        """Handle semantic search query."""
        # Search for relevant chunks
        results = semantic_search(
            workspace_id=workspace_id,
            query=question,
            top_k=5,
            document_id=document_id
        )
        
        if not results:
            no_data_response = "I couldn't find any relevant information in your documents to answer that question."
            self._save_interaction(workspace_id, question, no_data_response, memory)
            return {"answer": no_data_response, "intent": "semantic", "sources": []}
        
        # Format context
        context = format_search_results(results, max_chars=2000)
        
        # Check if follow-up (use last assistant response)
        memory_context = ""
        if memory and self._is_follow_up(question):
            last_assistant = next(
                (m["content"] for m in reversed(memory) if m["role"] == "assistant"),
                None
            )
            if last_assistant:
                memory_context = f"\nPrevious response: {last_assistant[:300]}..."
        
        # Generate answer
        prompt = self._build_answer_prompt(question, context, memory_context)
        
        if stream:
            return self._stream_answer(workspace_id, question, prompt, memory, results)
        else:
            llm = get_llm(provider=self.llm_provider, max_tokens=1000)
            answer = llm.generate(prompt)
            self._save_interaction(workspace_id, question, answer, memory)
            return {
                "answer": answer,
                "intent": "semantic",
                "sources": [{"text": r["text"][:200], "score": r["score"]} for r in results[:3]]
            }
    
    def _handle_structured(
        self,
        workspace_id: str,
        question: str,
        tables_metadata: List[Dict[str, Any]],
        memory: List[Dict[str, str]],
        stream: bool
    ) -> Dict[str, Any] | Generator[str, None, None]:
        """Handle structured SQL query."""
        if not tables_metadata:
            # Fallback to semantic if no tables
            return self._handle_semantic(
                workspace_id=workspace_id,
                question=question,
                document_id=None,
                memory=memory,
                stream=stream
            )
        
        # Check if multiple tables and need selection
        if len(tables_metadata) > 1:
            # Check if user mentioned a specific table
            question_lower = question.lower()
            mentioned_table = None
            for table in tables_metadata:
                if table["name"].lower() in question_lower:
                    mentioned_table = table
                    break
            
            if not mentioned_table:
                selection_prompt = generate_table_selection_prompt(question, tables_metadata)
                self._save_interaction(workspace_id, question, selection_prompt, memory)
                return {
                    "answer": selection_prompt,
                    "intent": "clarification",
                    "needs_input": True,
                    "tables": [t["name"] for t in tables_metadata]
                }
        
        try:
            # Generate and execute SQL
            sql, db_path = generate_sql(
                question=question,
                tables_metadata=tables_metadata,
                llm_provider=self.llm_provider
            )
            
            results = execute_sql_query(sql, db_path)
            formatted_results = format_sql_results(results)
            
            # Generate natural language answer
            prompt = f"""Based on this SQL query result, provide a clear answer.

Question: {question}

{formatted_results}

Provide a direct, helpful answer based on the data. Be concise."""

            if stream:
                return self._stream_answer(workspace_id, question, prompt, memory, sql_results=results)
            else:
                llm = get_llm(provider=self.llm_provider, max_tokens=500)
                answer = llm.generate(prompt)
                self._save_interaction(workspace_id, question, answer, memory)
                return {
                    "answer": answer,
                    "intent": "structured",
                    "sql": sql,
                    "data": results["rows"][:20]
                }
                
        except Exception as e:
            error_msg = f"I couldn't process that query: {str(e)}"
            # Fallback to semantic search
            return self._handle_semantic(
                workspace_id=workspace_id,
                question=question,
                document_id=None,
                memory=memory,
                stream=stream
            )
    
    def _handle_hybrid(
        self,
        workspace_id: str,
        question: str,
        document_id: Optional[str],
        tables_metadata: List[Dict[str, Any]],
        memory: List[Dict[str, str]],
        stream: bool
    ) -> Dict[str, Any] | Generator[str, None, None]:
        """Handle hybrid query (both semantic and structured)."""
        # Get semantic results
        semantic_results = semantic_search(
            workspace_id=workspace_id,
            query=question,
            top_k=3,
            document_id=document_id
        )
        semantic_context = format_search_results(semantic_results, max_chars=1000)
        
        # Get structured results if tables available
        sql_context = ""
        sql_results = None
        if tables_metadata:
            try:
                sql, db_path = generate_sql(
                    question=question,
                    tables_metadata=tables_metadata,
                    llm_provider=self.llm_provider
                )
                sql_results = execute_sql_query(sql, db_path)
                sql_context = format_sql_results(sql_results, max_rows=10)
            except Exception:
                pass  # SQL failed, continue with just semantic
        
        # Combine contexts
        combined_context = f"""Document Search Results:
{semantic_context}

{f"Database Query Results:{chr(10)}{sql_context}" if sql_context else ""}"""

        prompt = f"""Answer this question using both document search and database results.

Question: {question}

{combined_context}

Provide a comprehensive answer combining insights from both sources."""

        if stream:
            return self._stream_answer(workspace_id, question, prompt, memory, semantic_results, sql_results)
        else:
            llm = get_llm(provider=self.llm_provider, max_tokens=1000)
            answer = llm.generate(prompt)
            self._save_interaction(workspace_id, question, answer, memory)
            return {
                "answer": answer,
                "intent": "hybrid",
                "sources": [{"text": r["text"][:200], "score": r["score"]} for r in semantic_results[:3]],
                "data": sql_results["rows"][:10] if sql_results else None
            }
    
    def _stream_answer(
        self,
        workspace_id: str,
        question: str,
        prompt: str,
        memory: List[Dict[str, str]],
        semantic_results: Optional[List[Dict]] = None,
        sql_results: Optional[Dict] = None
    ) -> Generator[str, None, None]:
        """Stream answer and save to memory when complete."""
        llm = get_llm(provider=self.llm_provider, max_tokens=1000)
        
        full_response = ""
        for chunk in llm.stream(prompt):
            full_response += chunk
            yield chunk
        
        # Save to memory after streaming completes
        self._save_interaction(workspace_id, question, full_response, memory)
    
    def _build_answer_prompt(
        self,
        question: str,
        context: str,
        memory_context: str = ""
    ) -> str:
        """Build the prompt for answer generation."""
        return f"""You are a helpful AI assistant for a data platform.
Answer the user's question using ONLY the provided context.

Rules:
1. Use the context as your primary source of truth
2. If the answer isn't in the context, say so politely
3. Be concise and direct
4. Don't mention embeddings, vectors, or technical details
5. Don't hallucinate or make up information
{memory_context}

Context:
{context}

Question: {question}

Answer:"""
    
    def _is_follow_up(self, question: str) -> bool:
        """Check if question is likely a follow-up."""
        follow_up_indicators = [
            "it", "this", "that", "they", "them", "those",
            "more", "else", "also", "what about", "how about",
            "and", "but", "however", "continue", "go on"
        ]
        question_lower = question.lower()
        return any(
            question_lower.startswith(ind) or f" {ind} " in question_lower 
            for ind in follow_up_indicators
        )
    
    def _save_interaction(
        self,
        workspace_id: str,
        question: str,
        answer: str,
        memory: List[Dict[str, str]]
    ):
        """Save interaction to conversation memory."""
        # Add new messages
        memory.append({"role": "user", "content": question})
        memory.append({"role": "assistant", "content": answer})
        
        # Trim to max messages
        if len(memory) > self.MAX_MEMORY_MESSAGES:
            memory = memory[-self.MAX_MEMORY_MESSAGES:]
        
        # Save to database
        save_conversation_memory(workspace_id, memory)
