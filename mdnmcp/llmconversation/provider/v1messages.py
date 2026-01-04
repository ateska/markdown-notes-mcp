import json
import asyncio
import logging

import aiohttp

import asab

from ..datamodel import Conversation, Exchange, AssistentMessage, AssistentReasoning, FunctionCall
from .provider_abc import LLMChatProviderABC

L = logging.getLogger(__name__)


class LLMChatProviderV1Messages(LLMChatProviderABC):
	'''
	Anthropic API v1 messages adapter.

	https://platform.claude.com/docs/en/api/messages/create
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
			if self.URL.startswith('https://api.anthropic.com'):
				headers['X-Api-Key'] = self.APIKey
				headers['anthropic-version'] = '2023-06-01'
			else:
				headers['Authorization'] = f"Bearer {self.APIKey}"
		return headers


	async def chat_request(self, conversation: Conversation, exchange: Exchange) -> None:
		messages = []
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
						# Anthropic uses 'thinking' content blocks for reasoning
						# Skip for now as it requires special handling
						pass

					case "FunctionCall":
						# Anthropic uses tool_use/tool_result format
						messages.append({
							"role": "assistant",
							"content": [{
								"type": "tool_use",
								"id": item.call_id,
								"name": item.name,
								# "input": json.loads(item.arguments) if item.arguments else {},
							}],
						})

						messages.append({
							"role": "user",
							"content": [{
								"type": "tool_result",
								"tool_use_id": item.call_id,
								"content": item.content,
							}],
						})


		model = conversation.get_model()
		assert model is not None

		data = {
			"model": model,
			"system": conversation.instructions,
			"messages": messages,
			"max_tokens": 4096,
			"stream": True,
			"tools": [
				{
					"name": "ping",
					"description": "Invoke a command-line ping tool with provided target host or service, return the textual result of the ping.",
					"input_schema": {
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
			]
		}

		L.log(asab.LOG_NOTICE, "Sending request to LLM", struct_data={"conversation_id": conversation.conversation_id, "model": model, "provider": self.URL})

		async with aiohttp.ClientSession(headers=self.prepare_headers()) as session:
			async with session.post(self.URL + "v1/messages", json=data) as response:
				if response.status != 200:
					text = await response.text()
					L.error(
						"Error when sending request to LLM chat provider",
						struct_data={"status": response.status, "text": text}
					)
					return

				assert response.content_type == "text/event-stream"

				# State for tracking content blocks
				self._current_content_block = None
				self._current_content_block_index = None

				async for line in response.content:
					line = line.decode("utf-8").rstrip('\n\r')
					print(">>>", line)
					
					if line == '':
						continue

					if line.startswith('event: '):
						event_type = line[7:]
						continue

					if line.startswith('data: '):
						data_str = line[6:]
						if data_str == '[DONE]':
							break
						try:
							data = json.loads(data_str)
							await self._on_llm_event(conversation, exchange, event_type, data)
						except json.JSONDecodeError as e:
							L.warning("Invalid JSON in SSE response", struct_data={"line": line, "error": str(e)})


	async def _on_llm_event(self, conversation: Conversation, exchange: Exchange, event_type: str, data: dict) -> None:

		match event_type:

			case 'message_start':
				# {
				#   "type": "message_start",
				#   "message": {
				#     "id": "msg_...",
				#     "type": "message",
				#     "role": "assistant",
				#     "content": [],
				#     "model": "claude-3-5-sonnet-20241022",
				#     "stop_reason": null,
				#     "stop_sequence": null,
				#     "usage": {"input_tokens": 25, "output_tokens": 1}
				#   }
				# }
				pass

			case 'content_block_start':
				# {
				#   "type": "content_block_start",
				#   "index": 0,
				#   "content_block": {"type": "text", "text": ""}
				# }
				# OR for thinking:
				# {
				#   "type": "content_block_start",
				#   "index": 0,
				#   "content_block": {"type": "thinking", "thinking": ""}
				# }
				# OR for tool_use:
				# {
				#   "type": "content_block_start",
				#   "index": 0,
				#   "content_block": {"type": "tool_use", "id": "...", "name": "...", "input": ""}
				# }
				self._current_content_block_index = data.get('index')
				content_block = data.get('content_block', {})
				block_type = content_block.get('type')

				item = None
				match block_type:
					case 'text':
						item = AssistentMessage(
							role='assistant',
							content=content_block.get('text', ''),
							status='in_progress',
						)

					case 'thinking':
						item = AssistentReasoning(
							content=content_block.get('thinking', ''),
							status='in_progress',
						)

					case 'tool_use':
						item = FunctionCall(
							call_id=content_block.get('id', ''),
							name=content_block.get('name', ''),
							arguments='',
							status='in_progress',
						)

					case _:
						L.warning("Unknown content block type", struct_data={"type": block_type})

				if item is not None:
					self._current_content_block = item
					exchange.items.append(item)
					await self.LLMChatService.send_update(conversation, {
						"type": "item.appended",
						"item": item.to_dict(),
					})

			case 'content_block_delta':
				# {
				#   "type": "content_block_delta",
				#   "index": 0,
				#   "delta": {"type": "text_delta", "text": "Hello"}
				# }
				# OR for thinking:
				# {
				#   "type": "content_block_delta",
				#   "index": 0,
				#   "delta": {"type": "thinking_delta", "thinking": "..."}
				# }
				# OR for tool_use:
				# {
				#   "type": "content_block_delta",
				#   "index": 0,
				#   "delta": {"type": "input_json_delta", "partial_json": "..."}
				# }
				delta = data.get('delta', {})
				delta_type = delta.get('type')

				item = self._current_content_block
				if item is None:
					L.warning("Received delta without active content block")
					return

				match delta_type:
					case 'text_delta':
						text = delta.get('text', '')
						if isinstance(item, AssistentMessage):
							item.content += text
							await self.LLMChatService.send_update(conversation, {
								"type": "item.delta",
								"key": item.key,
								"delta": text,
							})

					case 'thinking_delta':
						thinking = delta.get('thinking', '')
						if isinstance(item, AssistentReasoning):
							item.content += thinking
							await self.LLMChatService.send_update(conversation, {
								"type": "item.delta",
								"key": item.key,
								"delta": thinking,
							})

					case 'input_json_delta':
						partial_json = delta.get('partial_json', '')
						if isinstance(item, FunctionCall):
							item.arguments += partial_json

					case _:
						L.warning("Unknown delta type", struct_data={"type": delta_type})

			case 'content_block_stop':
				# {
				#   "type": "content_block_stop",
				#   "index": 0
				# }
				item = self._current_content_block
				if item is not None:
					item.status = 'completed'
					await self.LLMChatService.send_update(conversation, {
						"type": "item.updated",
						"item": item.to_dict(),
					})

					if isinstance(item, FunctionCall):
						await self.LLMChatService.create_function_call(conversation, item)

				self._current_content_block = None
				self._current_content_block_index = None

			case 'message_delta':
				# {
				#   "type": "message_delta",
				#   "delta": {"stop_reason": "end_turn", "stop_sequence": null},
				#   "usage": {"output_tokens": 15}
				# }
				pass

			case 'message_stop':
				# {"type": "message_stop"}
				pass

			case 'ping':
				# Keepalive event
				pass

			case 'error':
				# Error event from the API
				error = data.get('error', {})
				L.error("Error from LLM API", struct_data={"error": error})

			case _:
				L.warning("Unknown/unhandled event", struct_data={"type": event_type})
