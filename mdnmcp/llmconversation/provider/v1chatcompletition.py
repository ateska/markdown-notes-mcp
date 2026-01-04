import json
import asyncio
import logging

import aiohttp

import asab

from ..datamodel import Conversation, Exchange, AssistentMessage, AssistentReasoning, FunctionCall
from .provider_abc import LLMChatProviderABC

L = logging.getLogger(__name__)


class LLMChatProviderV1ChatCompletition(LLMChatProviderABC):
	'''
	OpenAI API v1 chat completions adapter.

	https://platform.openai.com/docs/api-reference/chat/create
	'''

	def __init__(self, service, *, url, **kwargs):
		super().__init__(service, url=url)
		self.APIKey = kwargs.get('api_key', None)
		self.Semaphore = asyncio.Semaphore(2)

	def prepare_headers(self):
		headers = {
			'Content-Type': 'application/json',
		}
		if self.APIKey is not None:
			headers['Authorization'] = f"Bearer {self.APIKey}"
		return headers


	async def chat_request(self, conversation: Conversation, exchange: Exchange) -> None:
		messages = []

		# Add system message if instructions are provided
		if conversation.instructions:
			messages.append({
				"role": "system",
				"content": conversation.instructions,
			})

		for exch in conversation.exchanges:
			for item in exch.items:
				match item.__class__.__name__:

					case "UserMessage":
						messages.append({
							"role": "user",
							"content": item.content,
						})

					case "AssistentMessage":
						messages.append({
							"role": "assistant",
							"content": item.content,
						})

					case "AssistentReasoning":
						# Reasoning is not directly supported in chat completions API
						# Skip for now
						pass

					case "FunctionCall":
						# OpenAI chat completions uses tool_calls format
						messages.append({
							"role": "assistant",
							"content": None,
							"tool_calls": [{
								"id": item.call_id,
								"type": "function",
								"function": {
									"name": item.name,
									"arguments": item.arguments,
								},
							}],
						})

						messages.append({
							"role": "tool",
							"tool_call_id": item.call_id,
							"content": item.content,
						})


		model = conversation.get_model()
		assert model is not None

		data = {
			"model": model,
			"messages": messages,
			"stream": True,
			"tools": [
				{
					"type": "function",
					"function": {
						"name": "ping",
						"description": "Invoke a command-line ping tool with provided target host or service, return the textual result of the ping.",
						"parameters": {
							"type": "object",
							"properties": {
								"target": {
									"type": "string",
									"description": "The target host or service to ping"
								}
							},
							"required": ["target"]
						}
					}
				}
			]
		}

		L.log(asab.LOG_NOTICE, "Sending request to LLM", struct_data={"conversation_id": conversation.conversation_id, "model": model, "provider": self.URL})

		# State for tracking streaming content
		self._current_assistant_message = None
		self._current_tool_calls = {}  # Indexed by tool call index

		async with aiohttp.ClientSession(headers=self.prepare_headers()) as session:
			async with session.post(self.URL + "v1/chat/completions", json=data) as response:
				if response.status != 200:
					text = await response.text()
					L.error(
						"Error when sending request to LLM chat provider",
						struct_data={"status": response.status, "text": text}
					)
					return

				assert response.content_type == "text/event-stream"

				async for line in response.content:
					line = line.decode("utf-8").rstrip('\n\r')

					if line == '':
						continue

					if line.startswith('data: '):
						data_str = line[6:]
						if data_str == '[DONE]':
							# Stream finished, finalize any pending items
							await self._finalize_stream(conversation, exchange)
							break
						try:
							data = json.loads(data_str)
							await self._on_llm_chunk(conversation, exchange, data)
						except json.JSONDecodeError as e:
							L.warning("Invalid JSON in SSE response", struct_data={"line": line, "error": str(e)})


	async def _on_llm_chunk(self, conversation: Conversation, exchange: Exchange, chunk: dict) -> None:
		'''
		Process a streaming chunk from the chat completions API.

		Chunk format:
		{
			"id": "chatcmpl-...",
			"object": "chat.completion.chunk",
			"created": 1234567890,
			"model": "gpt-4",
			"choices": [{
				"index": 0,
				"delta": {
					"role": "assistant",
					"content": "Hello",
					"tool_calls": [...]
				},
				"finish_reason": null | "stop" | "tool_calls"
			}]
		}
		'''
		choices = chunk.get('choices', [])
		if not choices:
			return

		choice = choices[0]
		delta = choice.get('delta', {})
		finish_reason = choice.get('finish_reason')

		# Handle role initialization
		if 'role' in delta and delta['role'] == 'assistant':
			# Start of assistant response - may or may not have content yet
			pass

		# Handle text content delta
		if 'content' in delta and delta['content'] is not None:
			text = delta['content']
			if self._current_assistant_message is None:
				# Create new assistant message item
				self._current_assistant_message = AssistentMessage(
					role='assistant',
					content=text,
					status='in_progress',
				)
				exchange.items.append(self._current_assistant_message)
				await self.LLMChatService.send_update(conversation, {
					"type": "item.appended",
					"item": self._current_assistant_message.to_dict(),
				})
			else:
				# Append to existing message
				self._current_assistant_message.content += text
				await self.LLMChatService.send_update(conversation, {
					"type": "item.delta",
					"key": self._current_assistant_message.key,
					"delta": text,
				})

		# Handle tool calls delta
		if 'tool_calls' in delta:
			for tool_call_delta in delta['tool_calls']:
				index = tool_call_delta.get('index', 0)

				if index not in self._current_tool_calls:
					# New tool call
					tool_call_id = tool_call_delta.get('id', '')
					function_info = tool_call_delta.get('function', {})
					function_name = function_info.get('name', '')
					arguments = function_info.get('arguments', '')

					item = FunctionCall(
						call_id=tool_call_id,
						name=function_name,
						arguments=arguments,
						status='in_progress',
					)
					self._current_tool_calls[index] = item
					exchange.items.append(item)
					await self.LLMChatService.send_update(conversation, {
						"type": "item.appended",
						"item": item.to_dict(),
					})
				else:
					# Update existing tool call with more arguments
					item = self._current_tool_calls[index]
					function_info = tool_call_delta.get('function', {})
					if 'arguments' in function_info:
						item.arguments += function_info['arguments']

		# Handle finish reason
		if finish_reason is not None:
			if finish_reason == 'stop':
				# Normal completion
				if self._current_assistant_message is not None:
					self._current_assistant_message.status = 'completed'
					await self.LLMChatService.send_update(conversation, {
						"type": "item.updated",
						"item": self._current_assistant_message.to_dict(),
					})

			elif finish_reason == 'tool_calls':
				# Tool calls completion - finalize all tool calls
				for index, item in self._current_tool_calls.items():
					item.status = 'completed'
					await self.LLMChatService.send_update(conversation, {
						"type": "item.updated",
						"item": item.to_dict(),
					})
					await self.LLMChatService.create_function_call(conversation, item)


	async def _finalize_stream(self, conversation: Conversation, exchange: Exchange) -> None:
		'''
		Finalize any pending items when the stream ends.
		'''
		# Finalize assistant message if still in progress
		if self._current_assistant_message is not None and self._current_assistant_message.status == 'in_progress':
			self._current_assistant_message.status = 'completed'
			await self.LLMChatService.send_update(conversation, {
				"type": "item.updated",
				"item": self._current_assistant_message.to_dict(),
			})

		# Finalize any tool calls still in progress
		for index, item in self._current_tool_calls.items():
			if item.status == 'in_progress':
				item.status = 'completed'
				await self.LLMChatService.send_update(conversation, {
					"type": "item.updated",
					"item": item.to_dict(),
				})
				await self.LLMChatService.create_function_call(conversation, item)

		# Reset state
		self._current_assistant_message = None
		self._current_tool_calls = {}
