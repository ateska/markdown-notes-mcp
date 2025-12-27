import typing
import datetime

import pydantic


def _utc_now() -> datetime.datetime:
	"""Factory function for current UTC time."""
	return datetime.datetime.now(datetime.timezone.utc)


class ChatInput(pydantic.BaseModel):
	"""Input data sent to the LLM."""
	data: dict
	created_at: datetime.datetime = pydantic.Field(default_factory=_utc_now)


class ChatOutput(pydantic.BaseModel):
	"""Output data received from the LLM."""
	event: list[tuple[typing.Literal['data', 'event', '???'], dict | str | bytes]]
	created_at: datetime.datetime = pydantic.Field(default_factory=_utc_now)


class ChatReasoningPart(pydantic.BaseModel):
	"""Reasoning block from the LLM response."""
	id: str
	content: str
	status: str
	type: typing.Literal['reasoning'] = 'reasoning'


class ChatContentPart(pydantic.BaseModel):
	"""Content/message block from the LLM response."""
	id: str
	content: str
	status: str
	role: str
	type: typing.Literal['message'] = 'message'


class ChatFunctionCallPart(pydantic.BaseModel):
	"""Function call block from the LLM response."""
	id: str
	call_id: str
	name: str
	arguments: str
	status: str
	type: typing.Literal['function_call'] = 'function_call'


class ChatRequest(pydantic.BaseModel):
	"""User request message."""
	role: str
	content: str
	created_at: datetime.datetime = pydantic.Field(default_factory=_utc_now)


class ChatToolResult(pydantic.BaseModel):
	"""Result of a tool/function execution."""
	output: str
	call_id: str
	type: typing.Literal['function_call_output'] = 'function_call_output'
	created_at: datetime.datetime = pydantic.Field(default_factory=_utc_now)


class ChatResponse(pydantic.BaseModel):
	"""Full response from the LLM."""
	id: str  # resp_xxxxxxx
	output: list[ChatReasoningPart | ChatContentPart | ChatFunctionCallPart]
	status: str
	created_at: datetime.datetime = pydantic.Field(default_factory=_utc_now)


class Chat(pydantic.BaseModel):
	"""A complete chat session."""
	chat_id: str
	model: str
	instructions: str
	messages: list[ChatRequest | ChatResponse | ChatFunctionCallPart | ChatToolResult] = pydantic.Field(default_factory=list)
	history: list[ChatInput | ChatOutput] = pydantic.Field(default_factory=list)
	created_at: datetime.datetime = pydantic.Field(default_factory=_utc_now)
	scheduled_tasks: list[ChatFunctionCallPart] = pydantic.Field(default_factory=list)
