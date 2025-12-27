import uuid
import json
import pprint
import asyncio
import logging
import contextlib

import aiohttp
import asab

from .chat import Chat, ChatOutput, ChatInput, ChatContentPart, ChatReasoningPart, ChatRequest, ChatFunctionCallPart, ChatResponse, ChatToolResult
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
	"You must use the tool_ping tool when asked to ping any host or server.",
	"Always use your available tools to fulfill requests.",
	"Always use the GitHub Flavored Markdown syntax to format your responses.",
])


class LLMChatProviderV1Response(object):
	'''
	https://platform.openai.com/docs/api-reference/responses
	'''

	EVENT_PREFIX = "v1r."

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


	async def chat_request(self, chat: Chat, message: str|ChatToolResult|ChatRequest, reply, *, role: str = 'user') -> None:
		if isinstance(message, str):
			chat.messages.append(
				ChatRequest(
					role=role,
					content=message.strip()
				)
			)

		elif isinstance(message, ChatToolResult):
			chat.messages.append(message)

		elif isinstance(message, ChatRequest):
			chat.messages.append(message)

		else:
			raise ValueError("Invalid message type")


		inp = []
		for msg in chat.messages:
			if isinstance(msg, ChatRequest):
				inp.append({"role": msg.role, "content": msg.content})
			elif isinstance(msg, ChatResponse):
				for output_item in msg.output:
					if isinstance(output_item, ChatContentPart):
						inp.append({"role": output_item.role, "content": output_item.content})
			elif isinstance(msg, ChatFunctionCallPart):
				inp.append({
					"type": msg.type,
					"id": msg.id,
					"call_id": msg.call_id,
					"name": msg.name,
					"arguments": json.dumps(msg.arguments),
				})
			elif isinstance(msg, ChatToolResult):
				inp.append({
					"type": msg.type,
					"call_id": msg.call_id,
					"output": msg.output,
				})
			else:
				raise ValueError("Invalid message type")

		pprint.pprint(inp, width=2000)

		data = {
			"model": chat.model,
			"instructions": chat.instructions,
			"input": inp,
			"stream": True,  # We expect an SSE response / "text/event-stream"
			"tools": [
				{
				"type": "function",
				"name": "tool_ping",
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
		
		L.log(asab.LOG_NOTICE, "Sending request to LLM", struct_data={"chat_id": chat.chat_id})
		chat.history.append(ChatInput(data=data))
		
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
							await self._on_llm_event(chat, event, reply)
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
					await self._on_llm_event(chat, event, reply)
					event = []


	async def _on_llm_event(self, chat, event, reply):
		chat.history.append(ChatOutput(event=event))

		reply_event = {} 
		for event_type, event_data in event:
			match event_type:
				case 'data':
					reply_event['data'] = event_data
				case 'event':
					reply_event['type'] = self.EVENT_PREFIX + event_data
				case _:
					pass

		print("->>", reply_event.get('type'))

		match reply_event.get('type'):

			case 'v1r.response.created':
				chat.messages.append(ChatResponse(
					id=reply_event['data']['response']['id'],
					output=[],
					status=reply_event['data']['response']['status'],
				))

			case 'v1r.response.in_progress':
				assert isinstance(chat.messages[-1], ChatResponse)
				assert chat.messages[-1].id == reply_event['data']['response']['id']
				chat.messages[-1].status = reply_event['data']['response']['status']

			case 'v1r.response.completed':
				assert isinstance(chat.messages[-1], ChatResponse)
				assert chat.messages[-1].id == reply_event['data']['response']['id']
				chat.messages[-1].status = reply_event['data']['response']['status']

			# Output items management

			case 'v1r.response.output_item.added':
				response = chat.messages[-1]
				assert isinstance(response, ChatResponse)
				assert response.status == 'in_progress'

				output_item = None
				match reply_event['data']['item']['type']:

					case 'message':
						output_item = ChatContentPart(
							id=reply_event['data']['item']['id'],
							role=reply_event['data']['item']['role'],
							content=reply_event['data']['item']['content'] or '',
							status=reply_event['data']['item']['status'],
						)

					case 'reasoning':
						output_item = ChatReasoningPart(
							id=reply_event['data']['item']['id'],
							content=reply_event['data']['item']['content'] or '',
							status=reply_event['data']['item']['status'],
						)

					case 'function_call':
						output_item = ChatFunctionCallPart(
							id=reply_event['data']['item']['id'],
							call_id=reply_event['data']['item']['call_id'],
							name=reply_event['data']['item']['name'],
							arguments=reply_event['data']['item']['arguments'],
							status=reply_event['data']['item']['status'],
						)

					case _:
						L.warning("Unknown output item type", struct_data={"type": reply_event['data']['item']['type']})

				if output_item is not None:
					response.output.append(output_item)


			case 'v1r.response.output_item.done':
				response = chat.messages[-1]
				assert isinstance(response, ChatResponse)
				assert response.status == 'in_progress'

				output_item = response.output[reply_event['data']['output_index'] - 1]
				item_id = reply_event['data']['item'].get('id')
				if item_id is not None:
					assert output_item.id == item_id
				assert reply_event['data']['item']['type'] == output_item.type

				output_item.status = reply_event['data']['item']['status']

			# Assistant message

			case 'v1r.response.content_part.added':
				response = chat.messages[-1]
				assert isinstance(response, ChatResponse)
				assert response.status == 'in_progress'

				output_item = response.output[reply_event['data']['output_index']]
				assert isinstance(output_item, ChatContentPart | ChatReasoningPart)  # 
				assert reply_event['data']['item_id'] == output_item.id

				output_item.content = reply_event['data']['part']['text']

			case 'v1r.response.output_text.delta':
				response = chat.messages[-1]
				assert isinstance(response, ChatResponse)
				assert response.status == 'in_progress'

				output_item = response.output[reply_event['data']['output_index']]
				assert isinstance(output_item, ChatContentPart)
				assert reply_event['data']['item_id'] == output_item.id

				output_item.content += reply_event['data']['delta']

			# Reasoning

			case 'v1r.response.reasoning_part.added':
				response = chat.messages[-1]
				assert isinstance(response, ChatResponse)
				assert response.status == 'in_progress'

				output_item = response.output[reply_event['data']['output_index']]
				assert isinstance(output_item, ChatReasoningPart)
				assert reply_event['data']['item_id'] == output_item.id

				output_item.content = reply_event['data']['part']['text']

			case 'v1r.response.reasoning_text.delta':
				response = chat.messages[-1]
				assert isinstance(response, ChatResponse)
				assert response.status == 'in_progress'

				output_item = response.output[reply_event['data']['output_index']]
				assert isinstance(output_item, ChatReasoningPart)
				assert reply_event['data']['item_id'] == output_item.id

				output_item.content += reply_event['data']['delta']

			# Function call

			case 'v1r.response.function_call_arguments.done':
				response = chat.messages[-1]
				assert isinstance(response, ChatResponse)
				assert response.status == 'in_progress'

				output_item = response.output[reply_event['data']['output_index']-1]
				assert isinstance(output_item, ChatFunctionCallPart)
				assert reply_event['data']['name'] == output_item.name

				output_item.arguments = reply_event['data']['arguments']

				chat.scheduled_tasks.append(output_item)
			

			case _:
				print("Unhandled event", reply_event['type'])

		await reply(reply_event)


class LLMChatService(asab.Service):


	def __init__(self, app, service_name="LLMChatService"):
		super().__init__(app, service_name)

		self.Providers = [
			LLMChatProviderV1Response(self, "http://sp01:8888/"),
			# LLMChatProviderV1Response(self, "http://sp01:8000/"),
		]
		self.Chats = dict()


	async def create_chat(self, model):
		while True:
			chat_id = 'chat-' + uuid.uuid4().hex
			if chat_id in self.Chats:
				continue
			break

		L.log(asab.LOG_NOTICE, "Creating a new chat", struct_data={"chat_id": chat_id})

		chat = Chat(chat_id=chat_id, instructions=Instructions, model=model)

		self.Chats[chat.chat_id] = chat
		return chat


	async def get_chat(self, chat_id, create=False, model=None):
		chat = self.Chats.get(chat_id)
		if chat is None and create:
			assert model is not None
			chat = await self.create_chat(chat_id, model)
		return chat


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


	async def function_call(self, chat, reply):
		task = chat.scheduled_tasks.pop(0)
		arguments = json.loads(task.arguments)
		L.log(asab.LOG_NOTICE, "Function call", struct_data={"name": task.name, "arguments": arguments})
		
		chat.messages.append(task)

		try:
			match task.name:

				case "tool_ping":
					return await tool_ping(task.call_id, arguments, reply)

				case _:
					L.warning("Unknown function call", struct_data={"name": task.name})
					return {"error": f"Unknown function: {task.name}"}

		except Exception as e:
			L.exception("Error in function call", struct_data={"name": task.name})
