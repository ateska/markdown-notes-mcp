import uuid
import json
import asyncio
import logging
import contextlib

import aiohttp
import asab

from .chat import Chat, ChatRequest, ChatResponse

#

L = logging.getLogger(__name__)

#

# content: `You are a helpful assistant within the application 'Markdown notes' that is used to write Markdown notes.
# The user is a writer of Markdown note named '${notePath}'.
# You are helping the user to write the note by providing review, suggestions, corrections and other feedback.
# Use the tools to work with the note.
# There is a whole directory of notes in the application, you can use the tools to work with the notes.
# Use the GitHub Flavored Markdown syntax to format your responses.`


class LLMChatProviderV1Response(object):

	EVENT_PREFIX = "v1r."

	def __init__(self, app, url):
		self.App = app
		self.URL = url
		self.Semaphore = asyncio.Semaphore(2)

		self.Instructions = " ".join([
			"You are a helpful assistant with access to tools.",
			"You must use the tool_ping tool when asked to ping any host or server.",
			"Always use your available tools to fulfill requests.",
			"Always use the GitHub Flavored Markdown syntax to format your responses.",
		])


	async def user_message(self, chat: Chat, message: str, reply):
		data = {
			"instructions": self.Instructions.strip(),
			"input": [
				{"role": "user", "content": message.strip()}
			],
			"stream": True,  # We expect an SSE response / "text/event-stream"
		}
		
		L.log(asab.LOG_NOTICE, "Sending user message to LLM chat provider", struct_data={"chat_id": chat.Id})
		chat.History.append(ChatRequest(Message=message))
		
		async with aiohttp.ClientSession() as session:
			async with session.post(self.URL, json=data) as response:
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
							chat.History.append(ChatResponse(Event=event))
							await self.on_llm_event(chat, event, reply)
							event = []
						continue

					chat.History.append(ChatResponse(Event=line))
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
					chat.History.append(ChatResponse(Event=event))
					await self.on_llm_event(chat, event, reply)
					event = []


	async def on_llm_event(self, chat, event, reply):
		reply_event = {}
		for event_type, event_data in event:
			match event_type:
				case 'data':
					reply_event['data'] = event_data
				case 'event':
					reply_event['type'] = self.EVENT_PREFIX + event_data
				case _:
					pass
		await reply(reply_event)


class LLMChatService(asab.Service):


	def __init__(self, app, service_name="LLMChatService"):
		super().__init__(app, service_name)

		self.Providers = [
			LLMChatProviderV1Response(app, "http://sp01:8888/v1/responses"),
		]
		self.Chats = dict()


	async def create_chat(self):
		while True:
			chat_id = 'chat-' + uuid.uuid4().hex
			if chat_id in self.Chats:
				continue
			break

		L.log(asab.LOG_NOTICE, "Creating a new chat", struct_data={"chat_id": chat_id})

		chat = Chat(chat_id)
		self.Chats[chat_id] = chat
		return chat


	async def get_chat(self, chat_id, create=False):
		chat = self.Chats.get(chat_id)
		if chat is None and create:
			chat = await self.create_chat(chat_id)
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
