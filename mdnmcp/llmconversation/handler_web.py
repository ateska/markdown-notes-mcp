import json
import weakref
import asyncio
import logging

import asab.web.rest
import aiohttp.web


from .datamodel import UserMessage


L = logging.getLogger(__name__)


class LLMConversationWebHandler():
	def __init__(self, llm_conversation_router_service, web):
		self.LLMConversationRouterService = llm_conversation_router_service
		web.add_get(r"/{tenant}/llm/conversation", self.ws_conversation)

		self.Websockets = weakref.WeakSet()
		self.LLMConversationRouterService.App.PubSub.subscribe("Application.tick!", self.on_app_tick)


	async def ws_conversation(self, request):

		models = await self.LLMConversationRouterService.get_models()
		if models is None or len(models) == 0:
			return asab.web.rest.json_response(request, {"result": "ERROR", "error": "No LLM models available"})

		ws = aiohttp.web.WebSocketResponse(
			receive_timeout=60.0,
			protocols=('asab',)
		)

		conversation_id = request.query.get('conversation_id')
		if conversation_id is None:
			conversation = await self.LLMConversationRouterService.create_conversation()
		else:
			conversation = await self.LLMConversationRouterService.get_conversation(conversation_id, create=True)

		await ws.prepare(request)

		await ws.send_json({
			"type": "chat.mounted",
			"conversation_id": conversation.conversation_id,
			"models": models,
		})

		self.Websockets.add(ws)

		async def reply_to_client(data):
			"""
			Closure that is responsible for sending replay from the LLM (etc) to the client.
			Works as a monitor for the conversation.
			"""
			await ws.send_json(data)

		# Send initial full update so that the client has the current state of the conversation
		await self.LLMConversationRouterService.send_full_update(conversation, reply_to_client)

		conversation.monitors.add(reply_to_client)
		try:
			async for msg in ws:

				try:

					match (msg.type):

						case aiohttp.WSMsgType.TEXT:
							data = json.loads(msg.data)
							match data.get('type'):

								case 'user.message.created':
									user_message = UserMessage(role='user', content=data.get('content', ''), model=data.get('model', models[0]))
									await self.LLMConversationRouterService.create_exchange(conversation, user_message)

								case 'conversation.stop':
									await self.LLMConversationRouterService.stop_conversation(conversation)

								case 'conversation.restart':
									self.LLMConversationRouterService.restart_conversation(conversation, key=data.get('key'))
									await self.LLMConversationRouterService.send_full_update(conversation, reply_to_client)

								case 'update.full.requested':
									await self.LLMConversationRouterService.send_full_update(conversation, reply_to_client)

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
		
		finally:
			conversation.monitors.discard(reply_to_client)

		return ws


	async def on_app_tick(self, message_type):
		async with asyncio.TaskGroup() as tg:
			for ws in self.Websockets:
				if ws.closed:
					continue
				tg.create_task(ws.ping())
