import uuid
import json
import weakref
import asyncio
import logging

import asab
import aiohttp.web


L = logging.getLogger(__name__)


class LLMChatWebHandler():
	def __init__(self, app, web):
		self.App = app
		web.add_get(r"/{tenant}/llmchat", self.get_llmchat)

		self.Websockets = weakref.WeakSet()
		self.App.PubSub.subscribe("Application.tick!", self.on_app_tick)


	async def on_app_tick(self, message_type):
		async with asyncio.TaskGroup() as tg:
			for ws in self.Websockets:
				if ws.closed:
					continue
				tg.create_task(ws.ping())


	async def get_llmchat(self, request):
		ws = aiohttp.web.WebSocketResponse(
			receive_timeout=60.0,
			protocols=('asab',)
		)

		chat_id = request.query.get('chat_id')
		if chat_id is not None and chat_id not in self.Chats:
			chat_id = None  # Requesting a non-existing chat id, create a new one
		
		if chat_id is None:
			chat = await self.App.LLMChatService.create_chat()
		else:
			chat = await self.App.LLMChatService.get_chat(chat_id, create=True)

		await ws.prepare(request)
 
		await ws.send_json({
			"type": "chat.new",
			"chat_id": chat.Id,
		})

		self.Websockets.add(ws)

		async def reply(data):
			"""
			Closure that is responsible for sending replay from the LLM (etc) to the client.
			"""
			await ws.send_json(data)

		async for msg in ws:

			try:

				match (msg.type):

					case aiohttp.WSMsgType.TEXT:
						data = json.loads(msg.data)
						match data.get('type'):

							case 'user_message':
								async with self.App.LLMChatService.with_provider() as provider:
									await provider.user_message(chat, data.get('content', ''), reply)

							case _:
								L.warning("Unknown message type receive", struct_data={"data": data})

					case aiohttp.WSMsgType.BINARY:
						print("aiohttp.WSMsgType.BINARY>", msg.data)

					case aiohttp.WSMsgType.CLOSE:
						print("aiohttp.WSMsgType.CLOSE!")
						await ws.close()

					case aiohttp.WSMsgType.ERROR:
						print("aiohttp.WSMsgType.ERROR!")
						await ws.close()

			except Exception as e:
				L.exception("Error in websocket message - closing websocket")
				await ws.close()
				break

		return ws
