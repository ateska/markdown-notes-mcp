import json
import asyncio
import logging

import aiohttp

import asab

from ..datamodel import Conversation, Exchange, AssistentMessage, AssistentReasoning, FunctionCall
from .provider_abc import LLMChatProviderABC

L = logging.getLogger(__name__)

class LLMChatProviderV1Response(LLMChatProviderABC):
	'''
	OpenAI API v1 responses adapter.

	https://platform.openai.com/docs/api-reference/responses
	'''

	def __init__(self, service, *, url, **kwargs):
		super().__init__(service, url=url)
		self.APIKey = kwargs.get('api_key', None)
		self.Semaphore = asyncio.Semaphore(2)

	def prepare_headers(self):
		headers = {}
		if self.APIKey is not None:
			headers['Authorization'] = f"Bearer {self.APIKey}"
		return headers


	async def chat_request(self, conversation: Conversation, exchange: Exchange) -> None:
		inp = []
		for exch in conversation.exchanges:
			for item in exch.items:
				match item.__class__.__name__:

					case "UserMessage" | "AssistentMessage":
						inp.append({
							"role": item.role,
							"content": item.content,
						})

					case "AssistentReasoning":
						# Reasoning items are not included in the input
						pass

					case "FunctionCall":
						inp.append({
							"type": "function_call",
							"call_id": item.call_id,
							"name": item.name,
							"arguments": item.arguments,
						})

						inp.append({
							"type": "function_call_output",
							"call_id": item.call_id,
							"output": item.content,
						})


		model = conversation.get_model()
		assert model is not None

		data = {
			"model": model,
			"instructions": conversation.instructions,
			"input": inp,
			"stream": True,  # We expect an SSE response / "text/event-stream"
			# "reasoning": {
			# 	"effort": "medium",  # TODO: Make this configurable
			# },
			# "text": {
			# 	"format":  {
			# 		"name": "steps_format",
			# 		"type": "json_schema",
			# 		"schema": StepsFormat.model_json_schema(),
			# 		"strict": True,
			# 	},
			# },
			"tools": [
				{
					"type": "function",
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
			]
		}
		
		L.log(asab.LOG_NOTICE, "Sending request to LLM", struct_data={"conversation_id": conversation.conversation_id, "model": model, "provider": self.URL})

		async with aiohttp.ClientSession(headers=self.prepare_headers()) as session:
			async with session.post(self.URL + "v1/responses", json=data) as response:
				if response.status != 200:
					text = await response.text()
					L.error(
						"Error when sending request to LLM chat provider",
						struct_data={"status": response.status, "text": text}
					)
					return

				assert response.content_type == "text/event-stream"
				event = []  # Accumulator for the event block in the SSE response

				async for line in response.content:
					if line == b'\n':
						if len(event) > 0:
							# Empty line indicates the end of the event block in the SSE response
							await self._on_llm_event(conversation, exchange, event)
							event = []
						continue

					p = line.find(b': ')
					if p == -1:
						L.warning("Invalid line in SSE response")
						return

					event_type = line[:p].decode("utf-8")					
					match event_type:
						case "data":
							data = json.loads(line[p+2:].decode("utf-8"))
							event.append(('data', data))
						case "event":
							event.append(('event', line[p+2:-1].decode("utf-8")))
						case _:
							L.warning("Unknown event type in SSE response", struct_data={"event_type": event_type})
							event.append(('???', line))

				if len(event) > 0:
					await self._on_llm_event(conversation, exchange, event)
					event = []


	async def _on_llm_event(self, conversation: Conversation, exchange: Exchange, event_items: list[tuple[str, dict | str | bytes]]) -> None:
		event = {} 
		for event_type, event_data in event_items:
			match event_type:
				case 'data':
					event['data'] = event_data
				case 'event':
					event['type'] = event_data
				case _:
					L.warning("Unknown event item type", struct_data={"event_item_type": event_type})
					pass

		match event.get('type', "???"):

			case 'response.created':
				# {'response': {
				# 	'background': False,
				# 	'conversation': None,
				# 	'created_at': 1766936980.0,
				# 	'error': None,
				# 	'id': 'resp_185a03ed22a1496f937a3c8ecec9d4d0',
				# 	'incomplete_details': None,
				# 	'instructions': 'You are a helpful assistant ...',
				# 	'max_output_tokens': None,
				# 	'max_tool_calls': None,
				# 	'metadata': None,
				# 	'model': 'nvidia/Qwen3-235B-A22B-FP4',
				# 	'object': 'response',
				# 	'output': [],
				# 	'parallel_tool_calls': False,
				# 	'previous_response_id': None,
				# 	'prompt': None,
				# 	'prompt_cache_key': None,
				# 	'prompt_cache_retention': None,
				# 	'reasoning': None,
				# 	'safety_identifier': None,
				# 	'service_tier': 'auto',
				# 	'status': 'in_progress',
				# 	'temperature': 1.0,
				# 	'text': None,
				# 	'tool_choice': 'auto',
				# 	'tools': [...],
				# 	'top_logprobs': 0,
				# 	'top_p': 1.0,
				# 	'truncation': 'disabled',
				# 	'usage': None,
				# 	'user': None
				# },
				# 'sequence_number': 0,
				# 'type': 'response.created'
				# }
				pass

			case 'response.in_progress':
				# {'response': {
				# 	'background': False,
				# 	'conversation': None,
				# 	'created_at': 1766937189.0,
				# 	'error': None,
				# 	'id': 'resp_a893f6ac308b45a5a1482d8dfb745c56',
				# 	'incomplete_details': None,
				# 	'instructions': 'You are a helpful assistant ...',
				# 	'max_output_tokens': None,
				# 	'max_tool_calls': None,
				# 	'metadata': None,
				# 	'model': 'nvidia/Qwen3-235B-A22B-FP4',
				# 	'object': 'response',
				# 	'output': [],
				# 	'parallel_tool_calls': False,
				# 	'previous_response_id': None,
				# 	'prompt': None,
				# 	'prompt_cache_key': None,
				# 	'prompt_cache_retention': None,
				# 	'reasoning': None,
				# 	'safety_identifier': None,
				# 	'service_tier': 'auto',
				# 	'status': 'in_progress',
				# 	'temperature': 1.0,
				# 	'text': None,
				# 	'tool_choice': 'auto',
				# 	'tools': [...],
				# 	'top_logprobs': 0,
				# 	'top_p': 1.0,
				# 	'truncation': 'disabled',
				# 	'usage': None,
				# 	'user': None
				# },
				# 'sequence_number': 1,
				# 'type': 'response.in_progress'
				# }
				# No-op ...
				pass

			case 'response.completed':
				# TODO: Set status to 'done' for the exchange
				pass


			case 'response.output_item.added':
				# {
				# 	'item': {
				# 		'content': None,
				# 		'encrypted_content': None,
				# 		'id': '',
				# 		'status': 'in_progress',
				# 		'summary': [],
				# 		'type': 'reasoning'
				# 	},
				# 	'output_index': 0,
				# 	'sequence_number': 3,
				# 	'type': 'response.output_item.added'
				# }

				# print("->>", event['type'], event['data']['output_index'])
				# pprint.pprint(event['data'], width=2000)

				item = None
				match event['data']['item']['type']:
					case 'reasoning':
						item = AssistentReasoning(
							content=event['data']['item']['content'] or '',
							status=event['data']['item']['status'],
						)

					case 'message':
						item = AssistentMessage(
							role=event['data']['item']['role'],
							content=event['data']['item']['content'] or '',
							status=event['data']['item']['status'],
						)

					case 'function_call':
						item = FunctionCall(
							call_id=event['data']['item']['call_id'],
							name=event['data']['item']['name'],
							arguments=event['data']['item']['arguments'] or '',
							status=event['data']['item']['status'],
						)

					case _:
						L.warning("Unknown output item type", struct_data={"type": event['data']['item']['type']})

				if item is not None:
					exchange.items.append(item)
					await self.LLMChatService.send_update(conversation, {
						"type": "item.appended",
						"item": item.to_dict(),
					})

			case 'response.output_item.done':
				# {'item': {
				# 	'content': [
				# 		{
				# 			'text': "The user wants a poem.... We'll produce a poem.\n",
				# 			'type': 'reasoning_text'
				# 		}
				# 	],
				# 	'encrypted_content': None,
				# 	'id': '',
				# 	'status': 'completed',
				# 	'summary': [],
				# 	'type': 'reasoning'
				# },
				# 'output_index': 0,
				# 'sequence_number': 61,
				# 'type': 'response.output_item.done'
				# }

				# {'item': {
				# 	'content': [{
				# 		'annotations': [],
				# 		'logprobs': None,
				# 		'text': "\nHere's a short poem for you:\n\n*Whispers of dawn on trembling seas,*  \n*Silver threads that never cease.*  \n*The world awakes in quiet grace,*  \n*And finds its pulse in night viable space.*  \n\n*Stars are lanterns in a sky of blue,*  \n*Dreams that stitch the clouds anew.*  \n*Each heartbeat echoes in the deep,*  \n*Where hope and wonder never sleep.*  \n\n*So let the rhythm of your heart be true,*  \n*And paint the world in hues anew.*carousel of thoughts that spin and sway—  \na quiet hymn where souls can stay.*",
				# 		'type': 'output_text'
				# 	}],
				# 	'id': '',
				# 	'role': 'assistant',
				# 	'status': 'completed',
				# 	'type': 'message'
				# 	},
				# 'output_index': 1,
				# 'sequence_number': 204,
				# 'type': 'response.output_item.done'
				# }

				# print("->>", event['type'], event['data']['output_index'])
				# pprint.pprint(event['data'], width=2000)

				item = exchange.get_last_item(event['data']['item']['type'])
				if item is not None:
					item.status = event['data']['item']['status']  # 'completed'
					# TODO: Update other fields based on the item type
					await self.LLMChatService.send_update(conversation, {
						"type": "item.updated",
						"item": item.to_dict(),
					})

					if isinstance(item, FunctionCall):
						await self.LLMChatService.create_function_call(conversation, item)

				else:
					L.warning("Unknown item for 'response.output_item.done'")


			case 'response.content_part.added':
				# {
				# 	'content_index': 0,
				# 	'item_id': '',
				# 	'output_index': 0,
				# 	'part': {
				# 		'text': '',
				# 		'type': 'reasoning_text'
				# 	},
				# 	'sequence_number': 4,
				# 	'type': 'response.content_part.added'
				# }
				# TODO: This is currently ignored, every Item has textual content
				pass

			case 'response.content_part.done':
				pass

			case 'response.reasoning_part.added':
				pass

			case 'response.reasoning_part.done':
				pass

			case 'response.reasoning_text.delta':
				# {
				# 	'content_index': 0,
				# 	'delta': 'Okay',
				# 	'item_id': '',
				# 	'output_index': 0,
				# 	'sequence_number': 5,
				# 	'type': 'response.reasoning_text.delta'
				# }

				item = exchange.get_last_item('reasoning')
				if item is not None:
					item.content += event['data']['delta']
					await self.LLMChatService.send_update(conversation, {
						"type": "item.delta",
						"key": item.key,
						"delta": event['data']['delta'],
					})
				elif event['data'].get('item_id', '') == '' and len(event['data'].get('delta', '').strip()) == 0:
					# Miss fired event, ignore it
					# This has been observed on TensorRT LLM with nvidia/Qwen3-235B-A22B-FP4
					return
				else:
					L.warning("Unknown item for 'response.reasoning_text.delta'")

			case 'response.reasoning_text.done':
				pass
				
			case 'response.output_text.delta':
				item = exchange.get_last_item('message')
				if item is not None:
					item.content += event['data']['delta']
					await self.LLMChatService.send_update(conversation, {
						"type": "item.delta",
						"key": item.key,
						"delta": event['data']['delta'],
					})
				elif event['data'].get('item_id', '') == '' and len(event['data'].get('delta', '').strip()) == 0:
					# Miss fired event, ignore it
					# This has been observed on TensorRT LLM with nvidia/Qwen3-235B-A22B-FP4
					return
				else:
					L.warning("Unknown item for 'response.output_text.delta'")

			case 'response.output_text.done':
				pass

			case 'response.function_call_arguments.delta':
				pass

			case 'response.function_call_arguments.done':
				# {
				# 	'arguments': '{\n  "target": "www.screenshot.com"\n}',
				# 	'item_id': 'fc_860b421d3abc08bc',
				# 	'name': 'tool_ping',
				# 	'output_index': 2,
				# 	'sequence_number': 47,
				# 	'type': 'response.function_call_arguments.done'
				# }

				item = exchange.get_last_item('function_call')
				if item is not None:
					item_name = event['data'].get('name', None)
					if item_name is not None:
						item.name = item_name
					item.arguments = event['data']['arguments']
				else:
					L.warning("Unknown item for 'response.function_call_arguments.done'")

			case _:
				L.warning("Unknown/unhandled event", struct_data={"type": event.get('type', "???")})
