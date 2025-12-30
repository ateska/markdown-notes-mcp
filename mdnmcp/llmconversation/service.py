import uuid
import json
import pprint
import asyncio
import logging
import contextlib

import aiohttp
import asab

import pydantic

from .datamodel import Conversation, UserMessage, Exchange, AssistentReasoning, AssistentMessage, FunctionCall
from .tool_ping import tool_ping

#

L = logging.getLogger(__name__)

#

# content: `You are a helpful assistant within the application 'Markdown notes' that is used to write Markdown notes.
# The user is a writer of Markdown note named '${notePath}'.
# You are helping the user to write the note by providing review, suggestions, corrections and other feedback.
# Use the tools to work with the note.
# There is a whole directory of notes in the application, you can use the tools to work with the notes.
# Use the GitHub Flavored Markdown syntax to format your responses.`


Instructions = " ".join([
	"You are a helpful assistant with access to tools.",
	"You must call a function 'ping' when asked to ping any host or server.",
	"You may use available tools to fulfill requests.",
	"Always use the GitHub Flavored Markdown syntax to format your responses.",
	"Always use preformatted text for reasoning.",
])

class StepsFormat(pydantic.BaseModel):
    steps: list[str]


class LLMChatProviderV1Response(object):
	'''
	OpenAI API v1 responses adapter.

	https://platform.openai.com/docs/api-reference/responses
	'''

	def __init__(self, service, url):
		self.LLMChatService = service
		self.URL = url.rstrip('/') + '/'
		self.Semaphore = asyncio.Semaphore(2)


	async def get_models(self):
		async with aiohttp.ClientSession() as session:
			try:
				async with session.get(self.URL + "v1/models") as response:
					if response.status != 200:
						L.error("Error getting models", struct_data={"status": response.status, "text": await response.text()})
						return None
					resp = await response.json()
					return [model['id'] for model in resp['data']]
			except aiohttp.ClientError as e:
				L.error("Error communicating with LLM: {} {}".format(e.__class__.__name__, e), struct_data={"url": self.URL})
				return None

		return []


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
			"reasoning": {
				"effort": "medium",  # TODO: Make this configurable
			},
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
		
		L.log(asab.LOG_NOTICE, "Sending request to LLM", struct_data={"conversation_id": conversation.conversation_id})

		async with aiohttp.ClientSession() as session:
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
					item.name = event['data']['name']
					item.arguments = event['data']['arguments']
				else:
					L.warning("Unknown item for 'response.function_call_arguments.done'")

			case _:
				L.warning("Unknown/unhandled event", struct_data={"type": event.get('type', "???")})


class LLMConversationRouterService(asab.Service):


	def __init__(self, app, service_name="LLMConversationRouterService"):
		super().__init__(app, service_name)

		self.Providers = [
			LLMChatProviderV1Response(self, "http://sp01:8888/"),
			# LLMChatProviderV1Response(self, "http://sp01:8000/"),
		]

		self.Conversations = dict[str, Conversation]()


	async def create_conversation(self):
		while True:
			conversation_id = 'conversation-' + uuid.uuid4().hex
			if conversation_id in self.Conversations:
				continue
			break

		L.log(asab.LOG_NOTICE, "New conversation created", struct_data={"conversation_id": conversation_id})

		conversation = Conversation(conversation_id=conversation_id, instructions=Instructions)
		self.Conversations[conversation.conversation_id] = conversation
		return conversation


	async def get_conversation(self, conversation_id, create=False):
		conversation = self.Conversations.get(conversation_id)
		if conversation is None and create:
			conversation = await self.create_conversation(conversation_id)
		return conversation


	@contextlib.asynccontextmanager
	async def with_provider(self):
		provider = self.Providers[0]

		async def print_waiting():
			while True:
				await asyncio.sleep(1)
				print("Waiting")

		waiting_task = asyncio.create_task(print_waiting())
		try:
			async with provider.Semaphore:
				waiting_task.cancel()
				yield provider
		finally:
			waiting_task.cancel()


	async def create_exchange(self, conversation: Conversation, item: UserMessage) -> None:
		new_exchange = Exchange()
		conversation.exchanges.append(new_exchange)

		new_exchange.items.append(item)
		await self.send_update(conversation, {
			"type": "item.appended",
			"item": item.to_dict(),
		})

		await self.schedule_task(conversation, new_exchange, self.task_chat_request)


	async def schedule_task(self, conversation: Conversation, exchange: Exchange, task, *args, **kwargs) -> None:
		t = asyncio.create_task(
			task(conversation, exchange, *args, **kwargs),
			name=f"conversation-{conversation.conversation_id}-task"
		)

		def on_task_done(task):
			conversation.tasks.remove(task)
			asyncio.create_task(self.send_update_tasks(conversation))

		t.add_done_callback(on_task_done)

		conversation.tasks.append(t)
		await self.send_update_tasks(conversation)
		

	async def send_update_tasks(self, conversation: Conversation) -> None:
		await self.send_update(
			conversation,
			{
				"type": "tasks.updated",
				"count": len(conversation.tasks),
			}
		)


	async def task_chat_request(self, conversation: Conversation, exchange: Exchange) -> None:
		async with self.with_provider() as provider:
			await provider.chat_request(conversation, exchange)


	async def get_models(self):
		models = []

		async def collect_models(models, provider):
			try:
				pmodels = await provider.get_models()
			except Exception as e:
				L.exception("Error collecting models", struct_data={"provider": provider.__class__.__name__})
				return

			if pmodels is not None:
				models.extend(pmodels)

		async with asyncio.TaskGroup() as tg:
			for provider in self.Providers:
				tg.create_task(collect_models(models, provider))

		return models


	async def send_update(self, conversation: Conversation, event: dict):
		async with asyncio.TaskGroup() as tg:
			for monitor in conversation.monitors:
				tg.create_task(monitor(event))


	async def send_full_update(self, conversation: Conversation, monitor):
		items = []
		full_update = {
			"type": "update.full",
			"conversation_id": conversation.conversation_id,
			"created_at": conversation.created_at.isoformat(),
			"items": items,
		}

		for exchange in conversation.exchanges:
			for item in exchange.items:
				match item.__class__:
					case UserMessage:
						items.append(item.to_dict())

		try:
			await monitor(full_update)
		except Exception:
			L.exception("Error sending full update to monitors", struct_data={"conversation_id": conversation.conversation_id})


	async def create_function_call(self, conversation: Conversation, function_call: FunctionCall):
		await self.schedule_task(conversation, function_call, self.task_function_call)
		

	async def task_function_call(self, conversation: Conversation, function_call: FunctionCall) -> None:
		L.log(asab.LOG_NOTICE, "Calling function ...", struct_data={"name": function_call.name})

		function_call.status = 'executing'
		await self.send_update(conversation, {
			"type": "item.updated",
			"item": function_call.to_dict(),
		})

		try:
			match function_call.name:

				case "ping":
					async for _ in tool_ping(function_call):
						await self.send_update(conversation, {
							"type": "item.updated",
							"item": function_call.to_dict(),
						})

				case _:
					L.warning("Unknown function call", struct_data={"name": function_call.name})
					function_call.content = f"Called unknown function '{function_call.name}', no result available."
					function_call.error = True

		except Exception as e:
			L.exception("Error in function call", struct_data={"name": function_call.name})
			function_call.content = "Generic exception occurred. Try again."
			function_call.error = True

		finally:
			function_call.status = 'finished'
			await self.send_update(conversation, {
				"type": "item.updated",
				"item": function_call.to_dict(),
			})

			# Initialize a new exchange with LLM
			new_exchange = Exchange()
			conversation.exchanges.append(new_exchange)
			await self.schedule_task(conversation, new_exchange, self.task_chat_request)
